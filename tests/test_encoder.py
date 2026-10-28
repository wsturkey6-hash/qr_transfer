"""encoder 測試：用一個照 PROTOCOL.md 寫的參考解碼器，確認各種檔案都能還原。

執行：python -m unittest discover -s tests
"""
import hashlib
import json
import os
import random
import shutil
import sys
import tempfile
import unittest
import zlib

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "encoder"))
import qrsend  # noqa: E402


def parse(frame):
    magic, ver, typ, sid, idx, total = qrsend.HEADER.unpack_from(frame)
    assert magic == b"QT" and ver == 1
    return typ, sid, idx, total, frame[qrsend.HEADER.size:]


def reassemble(meta_frame, data_frames):
    """參考解碼器：串接、解壓並依 PROTOCOL.md 驗證，回傳 (meta, data)。"""
    typ, sid, _, total, body = parse(meta_frame)
    assert typ == qrsend.TYPE_META
    meta = json.loads(body.decode("utf-8"))
    chunks = {}
    for f in data_frames:
        t, s, i, n, b = parse(f)
        assert t == qrsend.TYPE_DATA and s == sid and n == total and i < n
        chunks[i] = b
    z = b"".join(chunks[i] for i in range(total))
    data = zlib.decompress(z)
    assert len(z) == meta["zsize"]
    assert len(data) == meta["size"]
    assert hashlib.sha256(data).hexdigest() == meta["sha256"]
    return meta, data


def verilog_like(size, seed=0):
    """產生像 Verilog 原始碼的文字，壓縮率接近真實檔案。"""
    rnd = random.Random(seed)
    words = ["wire", "reg", "assign", "always", "posedge", "clk", "rst_n", "begin", "end", "if", "else",
             "data_in", "data_out", "valid", "ready", "coef", "acc", "<=", "=", "+", "&", "|", "[7:0]", "[15:0]"]
    lines = []
    n = 0
    while n < size:
        line = "    " + " ".join(rnd.choice(words) for _ in range(rnd.randint(3, 10))) + ";  // {}\n".format(rnd.randint(0, 99999))
        lines.append(line)
        n += len(line)
    return "".join(lines).encode("ascii")[:size]


class RoundTrip(unittest.TestCase):
    def check(self, data, name="test.v", chunk=700):
        session, meta_frame, data_frames = qrsend.build_frames(data, name, chunk)
        meta, out = reassemble(meta_frame, data_frames)
        self.assertEqual(out, data)
        self.assertEqual(meta["name"], name)
        self.assertEqual(meta["chunk"], chunk)
        return data_frames

    def test_empty(self):
        frames = self.check(b"")
        self.assertEqual(len(frames), 1)

    def test_big5(self):
        self.check("// 這是中文註解：係數路徑\nmodule top;\nendmodule\n".encode("big5") * 200)

    def test_50kb(self):
        frames = self.check(verilog_like(50 * 1024))
        self.assertLess(len(frames), 40)

    def test_200kb(self):
        self.check(verilog_like(200 * 1024, seed=1))

    def test_random_bytes(self):
        self.check(os.urandom(5000))

    def test_exact_chunk_boundary(self):
        chunk = 64
        rnd = random.Random(2)
        for n in range(1, 2000):
            data = bytes(rnd.randrange(256) for _ in range(n))
            if len(zlib.compress(data, 9)) % chunk == 0:
                break
        else:
            self.fail("no boundary case found")
        frames = self.check(data, chunk=chunk)
        self.assertEqual(len(frames[-1]) - qrsend.HEADER.size, chunk)

    def test_chinese_filename(self):
        self.check(b"abc", name="係數路徑_測試.v")


class Format(unittest.TestCase):
    def test_header(self):
        self.assertEqual(qrsend.HEADER.size, 12)
        session, meta_frame, data_frames = qrsend.build_frames(b"hello", "a.txt", session=b"\x01\x02\x03\x04")
        self.assertEqual(meta_frame[:12], b"QT\x01\x00\x01\x02\x03\x04\x00\x00\x00\x01")
        self.assertEqual(data_frames[0][:12], b"QT\x01\x01\x01\x02\x03\x04\x00\x00\x00\x01")

    def test_sessions_differ(self):
        a = qrsend.build_frames(b"x", "a")[0]
        b = qrsend.build_frames(b"x", "a")[0]
        self.assertEqual(len(a), 4)
        self.assertNotEqual(a, b)

    def test_playlist_order(self):
        order = qrsend.playlist_order(20)
        self.assertEqual([i for i, v in enumerate(order) if v is None], [0, 9, 18])
        self.assertEqual([v for v in order if v is not None], list(range(20)))

    def test_frames_fit_qr(self):
        _, meta_frame, data_frames = qrsend.build_frames(verilog_like(50 * 1024), "很長的中文檔名" * 5 + ".v")
        self.assertLessEqual(qrsend.make_qr(meta_frame).get_version(), 18)
        for f in data_frames:
            self.assertLessEqual(qrsend.make_qr(f).get_version(), 18)


class Cli(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.src = os.path.join(self.tmp, "top.v")
        with open(self.src, "wb") as f:
            f.write(verilog_like(20000))

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_player_page(self):
        out = os.path.join(self.tmp, "p.html")
        qrsend.main([self.src, "--out", out, "--no-open"])
        with open(out, encoding="utf-8") as f:
            page = f.read()
        start = page.index("var DATA = ") + len("var DATA = ")
        data = json.loads(page[start:page.index(";\n", start)])
        indexes = [f["index"] for f in data["frames"]]
        self.assertEqual(indexes, qrsend.playlist_order(data["total"]))
        self.assertEqual(data["name"], "top.v")

    def test_export_frames(self):
        d = os.path.join(self.tmp, "frames")
        qrsend.main([self.src, "--export-frames", d])
        names = sorted(os.listdir(d))
        self.assertEqual(names[-1], "meta.pgm")
        with open(os.path.join(d, names[0]), "rb") as f:
            self.assertTrue(f.read(3) == b"P5\n")


if __name__ == "__main__":
    unittest.main()
