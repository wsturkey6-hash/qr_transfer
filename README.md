# qr_transfer

把文字檔從電腦用 QR code 傳到 iPhone。電腦端的 encoder 在瀏覽器裡輪播一串 QR code，手機端的 decoder 網頁用相機掃描，還原出內容完全相同的檔案，並用 SHA-256 驗證。

## 電腦端：encoder

只需要 `encoder/qrsend.py` 這一個檔案，Python 3.6 以上，不用安裝任何套件。

```bash
python3 qrsend.py top.v
```

執行後會印出檔案資訊與 SHA-256，並用瀏覽器開啟播放頁（按 F11 全螢幕）。

| 按鍵 | 動作 |
|---|---|
| 空白鍵 | 暫停／播放 |
| ← / → | 上一張／下一張 |
| 數字＋Enter | 跳到第 N 張（補手機缺的那幾張） |
| + / - | 調整速度 |

常用參數：

- `--chunk 700`：每張 QR 放多少 bytes。掃不太到就調小，例如 400。
- `--fps 5`：每秒幾張。
- `--ecc L`：糾錯等級 L/M/Q/H，越高越耐模糊，但每張 QR 越密。
- `--no-open`：不自動開瀏覽器，自己打開印出來的 `player` 路徑。

## 手機端：decoder

用 iPhone Safari 開啟 decoder 網頁，按「開始掃描」後把鏡頭對準螢幕。畫面上的格子會顯示收到哪幾張，缺的編號會列在下方。收齊後會自動驗證 SHA-256，可以存到「檔案」、分享或複製全文。按「掃下一個檔」繼續收下一個。

網頁需要 HTTPS 才能使用相機，所以放在 GitHub Pages（`docs/` 資料夾）。

## 測試

```bash
python -m unittest discover -s tests
```

```bash
node --test tests/e2e.test.mjs
```

格式規格見 [PROTOCOL.md](PROTOCOL.md)。

## 第三方元件

- [QR Code generator library](https://www.nayuki.io/page/qr-code-generator-library)（Project Nayuki，MIT）：內嵌在 `qrsend.py`，已移除型別註記以相容 Python 3.6。
- [jsQR](https://github.com/cozmo/jsQR) 1.4.0（Apache-2.0）：`docs/jsQR.js`，授權見 `docs/jsQR.LICENSE.txt`。
