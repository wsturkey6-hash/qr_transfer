# 傳輸協定 v1

encoder 把一個檔案變成一串 QR code，decoder 掃描後還原。本文件規定兩端共用的格式與規則。

## 資料流程

1. 讀檔案的原始位元組，計算 SHA-256。
2. 用 zlib 壓縮（RFC 1950 格式，Python `zlib.compress(data, 9)`）。瀏覽器端用 `DecompressionStream('deflate')` 解壓。
3. 壓縮後的資料依序切成每片 `chunk` bytes（預設 700），最後一片可以較短。片數 `total` 至少 1、最多 65535。
4. 每片加上標頭成為一張 QR 的內容，以 byte mode 編碼。

## 標頭（12 bytes，big-endian）

| 偏移 | 長度 | 欄位 | 說明 |
|---|---|---|---|
| 0 | 2 | magic | ASCII `QT` |
| 2 | 1 | version | 固定為 `1` |
| 3 | 1 | type | `0` = meta 片，`1` = 資料片 |
| 4 | 4 | session | 每次執行 encoder 隨機產生，用來區分不同檔案 |
| 8 | 2 | index | 資料片的編號，從 0 起算；meta 片固定為 0 |
| 10 | 2 | total | 資料片總數 |

標頭不符（長度不足 12、magic 不是 `QT`、version 不是 1、type 不是 0 或 1、資料片 `index >= total`）的 QR 一律丟棄。

## meta 片

標頭之後是 UTF-8 JSON：

```json
{"chunk": 700, "name": "top.v", "sha256": "…64 個小寫十六進位字元…", "size": 51200, "zsize": 14823}
```

- `name`：檔名（不含路徑）
- `size`：原始檔案大小
- `sha256`：原始檔案的 SHA-256
- `zsize`：壓縮後大小
- `chunk`：切片大小

## 輪播順序

meta 片開頭，接著資料片 0、1、2…，每 8 張資料片之前插一張 meta 片，然後循環。所以從任何時間點開始掃都會很快拿到 meta。

## Decoder 的 session 規則

1. 鎖定第一個看到的 session。
2. 其他 session 的片一律丟棄，不進緩衝區。每個外來 session 只回報一次「看到外來 session」事件，UI 據此詢問使用者是否切換。
3. 切換是明確的操作：清空所有狀態（包含已回報過的外來 session 名單），改鎖定新的 session。
4. 檔案完成、使用者按「掃下一個檔」時，目前的 session 列入已完成名單，狀態清空、解除鎖定。之後再看到已完成 session 的片，直接忽略，不回報事件。

## 完成條件與驗證

收齊 meta 片與全部 `total` 張資料片後，依 index 順序串接、解壓，檢查：

- 串接後長度等於 `zsize`
- 解壓後長度等於 `size`
- 解壓後的 SHA-256 等於 `sha256`

全部相符才算成功。
