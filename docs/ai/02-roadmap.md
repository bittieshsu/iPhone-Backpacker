# 02 — 執行計畫與進度

最後更新：2026-08-26

**規則：完成一個階段就回來勾選，並註明實際做了什麼 / 與計畫的差異。**

---

## 階段 0 — 立即止血 ✅ 已完成

在既有 CLI 上做最小修補，不動架構。

- [x] `copyShellItem_batch` 加上「零檔案就直接返回」的 guard
      → 修掉 `-2147418113 災難性的失敗`（`0x8000FFFF` E_UNEXPECTED）
- [x] `FILTER` 改為分類式、大小寫不敏感，補上 `heic/heif/gif/tiff/dng/webp`
      → 修掉「選了 HEIC 資料夾卻濾成 0 個檔案」
- [x] `getFolderObject` 找不到目標時 `raise FileNotFoundError`
      → 原本只 print 然後拿 `None` 去 `BindToObject` 直接 crash

**驗證方式**：使用者在 Windows 上跑既有 CLI，確認上述兩種情境不再爆炸。

---

## 階段 1 — 抽出 core/ ⬜ 未開始

- [ ] 建立 `core/` 骨架，實作 `errors.py` / `shell_ns.py` / `filters.py` / `listing.py`
- [ ] 移除 `print` / `tqdm` / `assert`，改 `logging` + 例外
- [ ] 刪除字串路徑解析層（`parseAbsName` / `iter_split` / `checkFolderName` /
      `getFolderObject_byAbsPath` / `ChineseCharacterChecking`）
- [ ] `copier.py`：`plan_copy` / `run_copy`，介面收 `list[FileEntry]`
- [ ] 目的地改用 `SHCreateItemFromParsingName`，不走 shell 樹（省掉一整條路徑）

**驗證方式**：用一支小測試腳本在**本機資料夾**上跑通（不需要 iPhone）。

---

## 階段 2 — 裝置偵測 ⬜ 未開始

- [ ] `device.py`：`find_portable_devices()` 用 `CSIDL_DRIVES` + `SHGDN_FORPARSING`，語言中立
- [ ] `probe()` 判斷 OK / NOT_FOUND / LOCKED_OR_UNTRUSTED
- [ ] `find_photo_folders()` 遞迴掃描（限深度 3）

**驗證方式**：插拔 iPhone、鎖定/解鎖、點/不點「信任」，三種狀態都要正確回報。

---

## 階段 3 — GUI 骨架（先接本機資料夾） ⬜ 未開始

- [ ] PySide6 主視窗，`QTreeWidget` 樹狀 + checkbox（**資料夾層級**）
- [ ] 目的地選擇、全選/全不選
- [ ] **先只接本機資料夾**，把互動流程跑順再接 iPhone

**驗證方式**：介面流程確認，使用者實際點過一輪。

---

## 階段 4 — 接上 iPhone + 背景執行緒 ⬜ 未開始

- [ ] `workers.py`：QThread wrapper，`CoInitialize` / `CoUninitialize`
- [ ] 樹狀延遲展開，列舉在背景執行緒（MTP 幾千張跑 30 秒以上是正常的）
- [ ] 裝置狀態顯示 + 「請解鎖 iPhone 並點『信任』」提示 ← **投報率最高的單一功能**
- [ ] 「重新整理裝置」按鈕（MTP 偶爾回傳不完整清單，是 Windows 已知毛病）

---

## 階段 5 — 可靠度 ⬜ 未開始

- [ ] 分批複製（chunk 200）+ 真實進度
- [ ] `FOF_NOERRORUI` 關掉「是否略過」小視窗 + 驗證掃描產生失敗清單
- [ ] 失敗清單 UI + 「重試失敗項目」按鈕
- [ ] 增量備份（檔名 + 大小比對，已存在不重傳）

**對應使用者回報的問題 2 與 3。**

---

## 階段 6 — 打包與散布 ⬜ 未開始

- [ ] PyInstaller **`--onedir`**（不要 `--onefile`）再壓 zip
- [ ] manifest 設 `asInvoker`（**不要**要求管理員權限）
- [ ] 首次啟動引導：說明「自動 vs 保留原始檔」，提醒改設定要重新插拔
- [ ] 改寫 README：目標平台改標 Windows 10/11，加上 SmartScreen「其他資訊 → 仍要執行」說明

---

## 明確不做（v1 範圍外）

- 檔案層級逐張勾選 → v2，保險已埋好，見 `01-architecture.md`
- HEIC → JPG 轉檔 → 交給 iPhone 端「自動」模式
- 照片縮圖預覽
- `IFileOperationProgressSink`（pywin32 支援不完整，用分批 + 驗證掃描替代）
- 程式碼簽章憑證（一年約台幣一萬且強制硬體金鑰，對免費工具不划算）
