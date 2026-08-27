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

## 階段 1 — 抽出 core/ ✅ 程式碼完成，待 Windows 實測

- [x] 建立 `iphone_backpacker/core/` 骨架
      （`errors` / `logging_setup` / `filters` / `shell_ns` / `listing` / `copier`）
- [x] 零 `print` / 零 `tqdm` / 零 `assert`，改 `logging` 寫檔 + 例外
- [x] 新 core 完全不含字串路徑解析，位置一律以絕對 PIDL 交換
- [x] `copier.py`：`plan_copy` / `run_copy`，streaming pipeline，排程單位是檔案
- [x] 目的地改用 `SHCreateItemFromParsingName`（`shell_ns.item_from_path`），不走 shell 樹
- [x] `tests/test_filters.py` —— 純 Python 單元測試，11 項通過
- [x] `tools/smoke_local.py` —— Windows 上的煙霧測試腳本
- [ ] **⚠ 待使用者在 Windows 上跑 `tools/smoke_local.py` 驗證**
- [ ] 舊的 `iphoneCopyOneFolder.py` / `iphoneCopyByConfig.py` / `EditThis.txt` 移除
      → **刻意延後到 GUI 可用之後**，在那之前它們是使用者唯一能用的工具

### 這個階段有意識留下的未決點

- `IEnumIDList.Next()` 在不同 pywin32 版本的簽章可能不同，
  `shell_ns._enum_pidls()` 已寫了「先試批次、失敗退回單筆」的保險，實測後再收斂。
- 增量去重目前**只比對檔名不比對大小**。取來源檔案大小在 MTP 上要每個項目
  一次 `GetDetailsOf` 來回，成本會抵銷掉增量備份省下的時間。
  iPhone 的 `IMG_xxxx` 檔名在同一資料夾內本來就唯一，先這樣。

**驗證方式**：`python tools/smoke_local.py <來源> <目的地>`，在**本機資料夾**上跑（不需要 iPhone）。

---

## 階段 2 — 裝置偵測 ✅ 程式碼完成，待 iPhone 實測

- [x] `device.py`：`find_portable_devices()` 用 `SFGAO_FOLDER && !SFGAO_FILESYSTEM`
      判斷，語言中立、不受使用者把手機改名影響
- [x] `probe()` / `detect()` 判斷 OK / NOT_FOUND / LOCKED_OR_UNTRUSTED
- [x] `status_message()` 產生使用者看得懂的提示（「請解鎖並點信任」）
- [x] `find_photo_folders()` 遞迴掃描，用 `folder_has_media()` 早退、可取消
- [x] `tools/smoke_device.py` benchmark 腳本
- [x] `tools/smoke_device.py` 已在真機跑過兩輪（拔掉 / 插著），數字寫進
      `04-shell-com-notes.md`，並產生決策 D10 / D11
- [x] `tools/bench_enum.py` / `tools/bench_batch.py` 已實測，
      確定 `PROBE_BATCH = 1`、`DEFAULT_BATCH` 不重要
- [x] `probe()` 的 LOCKED_OR_UNTRUSTED 分支已驗過：鎖著螢幕插上去，
      26.5 ms 判斷出來並提示「請解鎖 iPhone 並點信任這部電腦」

### benchmark 已回答的問題

1. ✅ 列出「本機」有插/沒插差多少 → **沒差**（772.7 vs 768.5 ms），跟 iPhone 無關
2. ✅ 展開 `Internal Storage`（184 項）要 1.5~2.5 秒，**進不了 0.5 秒**
3. ✅ **`SHCONTF_FOLDERS` 是事後過濾，不會減少工作量** → 決策 D10
4. ✅ 成本模型 ≈ `40 ms + 8.2 ms × 項目數` → 決策 D11
5. ✅ 裝置偵測總成本約 1.9 秒 → 啟動必須非同步

6. ✅ **批次大小才是主要參數**。早退用 batch=1 比 batch=64 快 4 倍；
   完整列舉則與批次無關（~3.4 ms/項）→ `PROBE_BATCH = 1`
7. ✅ 絕對數字**跨 session 浮動可達 3 倍**，只相信同一輪內的比例

### 由使用者實測經驗產生的修正

使用者回報：早期用 `copyShellItem()` 逐檔複製「超級慢，而且 Windows
的原生進度視窗會反覆彈出」。這推翻了 `run_copy()` 原本每 200 檔一批的
設計 → 決策 D12，改為**一個資料夾一次 `IFileOperation`**。

**驗證方式**：插拔 iPhone、鎖定/解鎖、點/不點「信任」，三種狀態都要正確回報。
**效能不達標就先解決效能再往下**，這是專案存在的理由（見 D8）。

---

## 階段 2.5 — core 真機驗證 ✅ 完成

2026-08-27 在 Windows + 真機驗過：

- [x] `smoke_local.py` 通過（複製 4 個、重跑全部去重）
- [x] `smoke_device.py --copy` **真的從 iPhone 複製成功**
      （`200101__` 2 個檔案、0 失敗、372 ms）
- [x] `probe()` 三種狀態都正確
- [x] 成本模型定案：`70 ms 開場 + 3.4 ms × 實體化項目數`

---

## 階段 3 — GUI 骨架（先接本機資料夾） ⬜ 未開始

- [ ] PySide6 主視窗，`QTreeWidget` 樹狀 + checkbox（**資料夾層級**）
- [ ] 目的地選擇、全選/全不選
- [ ] `NamespaceCache`：展開過的節點收合再展開是零成本
- [ ] 右側摘要面板 —— **檔案數/大小背景算，不阻塞勾選與備份**（見 D8）
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

- [ ] streaming pipeline：`iter_files → 過濾 → 去重 → 每 200 檔排程一次`（不要先讀成大 list）
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
