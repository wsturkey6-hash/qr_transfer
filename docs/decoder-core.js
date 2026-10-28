// decoder 核心：解析 QR 內容、收集資料片、依 PROTOCOL.md 還原與驗證。
// 瀏覽器（window.QTDecoder）與 Node（require）共用同一份程式。
(function (root) {
  "use strict";

  var HEADER_SIZE = 12;
  var TYPE_META = 0;
  var TYPE_DATA = 1;

  function hex(bytes) {
    var s = "";
    for (var i = 0; i < bytes.length; i++) s += (bytes[i] < 16 ? "0" : "") + bytes[i].toString(16);
    return s;
  }

  // 標頭不符回傳 null
  function parseFrame(bytes) {
    if (!bytes || bytes.length < HEADER_SIZE) return null;
    if (bytes[0] !== 0x51 || bytes[1] !== 0x54 || bytes[2] !== 1) return null; // "QT", version 1
    var type = bytes[3];
    if (type !== TYPE_META && type !== TYPE_DATA) return null;
    var index = (bytes[8] << 8) | bytes[9];
    var total = (bytes[10] << 8) | bytes[11];
    if (total === 0 || (type === TYPE_DATA && index >= total)) return null;
    return {
      type: type,
      session: hex(bytes.subarray(4, 8)),
      index: index,
      total: total,
      body: bytes.slice(HEADER_SIZE)
    };
  }

  function Receiver() {
    this.completed = {}; // 已完成的 session：之後看到直接忽略
    this._clear(null);
  }

  Receiver.prototype._clear = function (session) {
    this.session = session;
    this.foreign = {};
    this.meta = null;
    this.total = null;
    this.chunks = {};
    this.count = 0;
  };

  // 餵入一張 QR 的位元組，回傳事件：
  //   {type: "invalid" | "ignored" | "foreign" | "meta" | "data" | "dup", session}
  // "foreign" 對每個外來 session 只回傳一次。
  Receiver.prototype.push = function (bytes) {
    var f = parseFrame(bytes);
    if (!f) return { type: "invalid" };
    if (this.completed[f.session]) return { type: "ignored", session: f.session };
    if (this.session === null) this.session = f.session;
    if (f.session !== this.session) {
      if (this.foreign[f.session]) return { type: "ignored", session: f.session };
      this.foreign[f.session] = true;
      return { type: "foreign", session: f.session };
    }
    if (this.total === null) this.total = f.total;
    if (f.total !== this.total) return { type: "invalid" };
    if (f.type === TYPE_META) {
      if (this.meta) return { type: "dup", session: f.session };
      try {
        this.meta = JSON.parse(new TextDecoder("utf-8").decode(f.body));
      } catch (e) {
        return { type: "invalid" };
      }
      return { type: "meta", session: f.session };
    }
    if (this.chunks[f.index]) return { type: "dup", session: f.session, index: f.index };
    this.chunks[f.index] = f.body;
    this.count++;
    return { type: "data", session: f.session, index: f.index };
  };

  Receiver.prototype.missing = function () {
    var out = [];
    if (this.total === null) return out;
    for (var i = 0; i < this.total; i++) if (!this.chunks[i]) out.push(i);
    return out;
  };

  Receiver.prototype.isComplete = function () {
    return this.meta !== null && this.total !== null && this.count === this.total;
  };

  // 改鎖定另一個 session，清空所有狀態
  Receiver.prototype.switchTo = function (session) {
    this._clear(session);
  };

  // 目前的檔案處理完畢：列入已完成名單並解除鎖定
  Receiver.prototype.reset = function () {
    if (this.session !== null) this.completed[this.session] = true;
    this._clear(null);
  };

  async function sha256Hex(bytes) {
    return hex(new Uint8Array(await crypto.subtle.digest("SHA-256", bytes)));
  }

  async function inflate(bytes) {
    var stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream("deflate"));
    return new Uint8Array(await new Response(stream).arrayBuffer());
  }

  // 收齊後還原。回傳 {ok, name, data, sha256, error}
  Receiver.prototype.finish = async function () {
    if (!this.isComplete()) return { ok: false, error: "尚未收齊" };
    var meta = this.meta;
    var len = 0, i;
    for (i = 0; i < this.total; i++) len += this.chunks[i].length;
    var z = new Uint8Array(len), off = 0;
    for (i = 0; i < this.total; i++) {
      z.set(this.chunks[i], off);
      off += this.chunks[i].length;
    }
    var result = { ok: false, name: meta.name, data: null, sha256: null, error: null };
    if (len !== meta.zsize) {
      result.error = "壓縮資料長度不符（" + len + " ≠ " + meta.zsize + "）";
      return result;
    }
    var data;
    try {
      data = await inflate(z);
    } catch (e) {
      result.error = "解壓失敗：" + e.message;
      return result;
    }
    result.data = data;
    result.sha256 = await sha256Hex(data);
    if (data.length !== meta.size) result.error = "檔案大小不符（" + data.length + " ≠ " + meta.size + "）";
    else if (result.sha256 !== meta.sha256) result.error = "SHA-256 不符";
    else result.ok = true;
    return result;
  };

  var api = { parseFrame: parseFrame, Receiver: Receiver };
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.QTDecoder = api;
})(this);
