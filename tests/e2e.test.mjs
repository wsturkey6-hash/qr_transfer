// 端到端測試：Python encoder 匯出 PGM → jsQR 解碼 → decoder-core 還原，比對 SHA-256。
// 執行：node --test tests/e2e.test.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { createRequire } from "node:module";
import { createHash } from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.join(here, "..");
const jsQRModule = require(path.join(root, "docs", "jsQR.js"));
const jsQR = jsQRModule.default || jsQRModule;
const { Receiver } = require(path.join(root, "docs", "decoder-core.js"));
const python = process.env.PYTHON || "python";

const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "qrt-"));
process.on("exit", () => fs.rmSync(tmp, { recursive: true, force: true }));

function run(args) {
  const r = spawnSync(python, args, { encoding: "utf-8" });
  assert.equal(r.status, 0, r.stderr);
  return r.stdout;
}

// 用 Python 產生樣本檔，和 encoder 測試用同一個產生器
run(["-c", `
import sys; sys.path.insert(0, ${JSON.stringify(here)})
import test_encoder as t
open(${JSON.stringify(path.join(tmp, "top_50k.v"))}, "wb").write(t.verilog_like(50 * 1024))
open(${JSON.stringify(path.join(tmp, "big5.v"))}, "wb").write("// 中文註解：係數路徑\\nmodule a;\\nendmodule\\n".encode("big5") * 300)
open(${JSON.stringify(path.join(tmp, "small.v"))}, "wb").write(t.verilog_like(8000, seed=5))
`]);

function readPgm(file) {
  const buf = fs.readFileSync(file);
  const m = /^P5\n(\d+) (\d+)\n255\n/.exec(buf.subarray(0, 32).toString("latin1"));
  const w = +m[1], h = +m[2];
  const gray = buf.subarray(m[0].length);
  const rgba = new Uint8ClampedArray(w * h * 4);
  for (let i = 0; i < w * h; i++) {
    rgba[i * 4] = rgba[i * 4 + 1] = rgba[i * 4 + 2] = gray[i];
    rgba[i * 4 + 3] = 255;
  }
  return { rgba, w, h };
}

// 編碼一個檔案並把每張 QR 掃回位元組；回傳 {meta, data: [...], bytes, sha}
function encodeAndScan(name) {
  const src = path.join(tmp, name);
  const dir = path.join(tmp, name + ".frames");
  run([path.join(root, "encoder", "qrsend.py"), src, "--export-frames", dir]);
  const scan = (f) => {
    const { rgba, w, h } = readPgm(path.join(dir, f));
    const code = jsQR(rgba, w, h, { inversionAttempts: "dontInvert" });
    assert.ok(code, "jsQR could not read " + f);
    return Uint8Array.from(code.binaryData);
  };
  const files = fs.readdirSync(dir).filter((f) => f.startsWith("data_")).sort();
  const bytes = fs.readFileSync(src);
  return {
    meta: scan("meta.pgm"),
    data: files.map(scan),
    bytes,
    sha: createHash("sha256").update(bytes).digest("hex"),
  };
}

function shuffle(arr, seed) {
  const a = arr.slice();
  let s = seed;
  for (let i = a.length - 1; i > 0; i--) {
    s = (s * 1103515245 + 12345) % 2147483648;
    const j = s % (i + 1);
    [a[i], a[j]] = [a[j], a[i]];
  }
  return a;
}

async function assertRestored(r, file) {
  assert.ok(r.isComplete());
  const res = await r.finish();
  assert.equal(res.error, null);
  assert.ok(res.ok);
  assert.equal(res.sha256, file.sha);
  assert.deepEqual(Buffer.from(res.data), file.bytes);
}

const A = encodeAndScan("top_50k.v");
const B = encodeAndScan("small.v");

test("50 KB file: shuffled, duplicated, missing frames filled later", async () => {
  const r = new Receiver();
  const held = new Set([2, 7, A.data.length - 1]);
  const first = shuffle([...A.data.keys()], 1).filter((i) => !held.has(i));
  for (const i of first) assert.equal(r.push(A.data[i]).type, "data");
  assert.equal(r.push(A.data[first[0]]).type, "dup");
  assert.equal(r.push(A.meta).type, "meta");
  assert.equal(r.push(A.meta).type, "dup");
  assert.ok(!r.isComplete());
  assert.deepEqual(r.missing(), [...held].sort((x, y) => x - y));
  for (const i of held) r.push(A.data[i]);
  assert.deepEqual(r.missing(), []);
  await assertRestored(r, A);
});

test("Big5 file restores byte for byte", async () => {
  const F = encodeAndScan("big5.v");
  const r = new Receiver();
  for (const d of [F.meta, ...F.data]) r.push(d);
  await assertRestored(r, F);
});

test("mixed sessions: lock first, foreign once, reset ignores completed session", async () => {
  const r = new Receiver();
  const frames = [A.meta];
  const n = Math.max(A.data.length, B.data.length);
  for (let i = 0; i < n; i++) {
    if (i < A.data.length) frames.push(A.data[i]);
    if (i < B.data.length) frames.push(B.data[i]);
    if (i === 3) frames.push(B.meta);
  }
  const events = frames.map((f) => r.push(f));
  const foreign = events.filter((e) => e.type === "foreign");
  assert.equal(foreign.length, 1);
  assert.equal(r.count, A.data.length); // B 的資料沒有進 A 的緩衝區
  await assertRestored(r, A);

  r.reset();
  for (const f of [A.meta, ...A.data]) assert.equal(r.push(f).type, "ignored");
  for (const f of [B.meta, ...B.data]) r.push(f);
  await assertRestored(r, B);
});

test("switchTo clears state and locks the new session", async () => {
  const r = new Receiver();
  for (const f of A.data.slice(0, 3)) r.push(f);
  const ev = r.push(B.data[0]);
  assert.equal(ev.type, "foreign");
  r.switchTo(ev.session);
  assert.equal(r.count, 0);
  const types = [];
  for (const f of [A.meta, B.meta, ...A.data, ...B.data]) types.push(r.push(f).type);
  assert.equal(types.filter((t) => t === "foreign").length, 1); // A 現在是外來 session，只回報一次
  await assertRestored(r, B);
});

test("garbage and wrong-version frames are invalid", () => {
  const r = new Receiver();
  assert.equal(r.push(new Uint8Array([1, 2, 3])).type, "invalid");
  const bad = Uint8Array.from(A.data[0]);
  bad[2] = 2;
  assert.equal(r.push(bad).type, "invalid");
  assert.equal(r.session, null);
});
