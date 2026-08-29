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

## 階段 3 + 4 — GUI ✅ 程式碼完成，待 Windows 實測

**階段 3 與 4 合併執行。** 原計畫「先接本機資料夾、之後再接 iPhone」在
決策 D10 之後失去意義 —— 非同步是硬需求，先寫同步版再改寫是白工。
而且磁碟機與 iPhone 都在「本機」底下同一層，樹只要一個根就同時涵蓋兩者。

- [x] `ui/workers.py`：單一 worker `QThread`，`CoInitialize` / `CoUninitialize`
      掛在 `thread.started` / `thread.finished`，任務序列執行
- [x] `ui/main_window.py`：`QTreeWidget` + checkbox（資料夾層級）
- [x] 非同步延遲展開 + 「載入中…」骨架節點
- [x] 裝置狀態橫幅（含「請解鎖 iPhone 並點信任」）
- [x] 「重新整理裝置」按鈕（清快取 + 重新偵測 + 重列）
- [x] 全選 / 全不選（作用在目前節點底下，對 184 個資料夾是必需的）
- [x] 備份類型下拉（照片+影片 / 只照片 / 只影片 / 全部）
- [x] 目的地選擇、開始備份、完成後顯示失敗清單
- [x] 檔案數欄位：**400 ms debounce + 背景計算**，點選當下什麼都不算
- [x] `iphone_backpacker/app.py` 進入點、`run_gui.py` 開發用啟動腳本
- [x] **已實測**（2026-08-27）：`python run_gui.py` 可正常瀏覽、勾選、備份 iPhone
- [x] 依實測回饋修正（見下）
- [x] 第二輪實測通過：複製視窗只彈一次、取消不再有 traceback、
      多選 + 批次計算檔案數正常、停止計算正常
- [ ] **⚠ 待第三輪實測**：D15 + 裝置訊息 + 按鈕 tooltip

### 第二輪實測回饋與修正（2026-08-27）

| 回饋 | 處理 |
|---|---|
| 複製視窗只彈一次、取消無 traceback、批次計算正常 | ✅ 前一輪的修正都生效 |
| **取消時 243 個沒輪到的檔案被算成「失敗」** | 新增 `CopyReport.cancelled`（D15） |
| 按了「信任」後仍要等 1~2 分鐘，訊息卻叫人去按信任 | `status_message()` 列出三種可能，第三種明說「已經按過了，請再等」 |
| 「全不選」讓人以為會清掉所有勾選 | 兩顆按鈕都加上明確 tooltip，操作後的狀態列訊息也標明作用範圍 |
| 一次排 195 個資料夾計算，久到讓人放棄 | 超過 30 個時先問，並給出時間估計 |
| 啟動耗時 | 實測 `import 2420 ms + 我們的初始化 80 ms` —— 幾乎全是 PySide6 import |

### 這個階段的設計要點

- **啟動不同步等偵測**：視窗先 `show()`，再發 `request_detect` /
  `request_roots`。使用者看到的是「立刻開啟」而不是「卡兩秒」。
- **點選節點零成本**：`currentItemChanged` 只重設 debounce 計時器。
  用方向鍵快速滑過 184 個節點不會塞爆 worker。
- **勾選語意**：勾一個資料夾＝備份「直接放在它裡面」的檔案，**不含子資料夾**。
  右側面板有寫明。iPhone 的照片就放在葉節點，這個語意夠用。
- 開發機是 Linux，**PySide6 裝不起來，只做過語法與 signal/slot 連線檢查**。

### 首次實測回饋與修正（2026-08-27）

| 回饋 | 處理 |
|---|---|
| 展開有骨架、視窗不凍、全選 184 個正常、方向鍵不卡 | ✅ 符合預期 |
| 選 5 個資料夾跳 5 次複製視窗 | 改成**整個任務一次 `IFileOperation`**（D12 修訂） |
| 取消複製後噴 `OLE error 0x80270000` traceback | `COPYENGINE_E_USER_CANCELLED` 是正常結果（D13） |
| 想一次算多個資料夾的檔案數 | 多選 + 「計算檔案數」批次按鈕（D14） |
| 取消後 UI 卡在「備份中」很久 | `PerformOperations()` 收尾時無法中斷，改為講清楚 + 加「取消備份」按鈕 |
| 啟動要 7 秒（第二次 3 秒） | log 顯示程式初始化只有 ~1.2 秒，其餘是 Python + PySide6 import。加了計時確認，靠階段 6 的 `--onedir` 打包改善 |

---

## 階段 5 — 可靠度 ✅ 程式碼完成，待實測

- [x] ~~分批複製~~ → 改為**整個任務一次 `IFileOperation`**（D12 修訂），
      分批會讓原生進度視窗反覆彈出
- [x] `FOF_NOERRORUI` 關掉「是否略過」小視窗 + 驗證掃描產生失敗清單
- [x] **區分「使用者取消」與「檔案被靜默略過」**（D15 修訂）——
      這是使用者最初回報的問題 3 的完整解法
- [x] 失敗清單 UI（對話框列出，完整清單寫 log）
- [x] 「重試未完成的項目」按鈕（D16：就是重跑同一個任務，
      增量去重讓它只複製沒完成的）
- [x] 增量備份（檔名比對，已存在不重傳）
- [x] 批次計算的時間估計會用實測值自我修正
- [ ] **⚠ 待實測**

### 使用者最初回報的三個問題，現況

| 問題 | 狀態 |
|---|---|
| 1. iOS 改版導致資料夾結構變動 | ✅ 樹狀瀏覽 + PIDL，不再依賴任何寫死路徑 |
| 2. 零檔案時噴 `-2147418113 災難性的失敗` | ✅ 零排程 guard |
| 3. 檔案複製失敗被略過但沒有 log | ✅ `FOF_NOERRORUI` + 驗證掃描 + 與取消分開判定 |

---

## 階段 6 — 打包與散布 ✅ 程式碼完成，待實測

- [x] `iphone_backpacker.spec`：onedir、`upx=False`、`console=False`、
      `uac_admin=False`（asInvoker）、排除用不到的 Qt 模組
- [x] `build.bat` 一鍵打包
- [x] `.gitignore`（build/、dist/、`__pycache__`、測試用的 `z/`）
- [x] 首次啟動引導（`ui/dialogs.py`）+ 工具列「使用說明」按鈕
- [x] 改寫 README：平台改標 Windows 10/11、SmartScreen 與防毒誤判的說明、
      log 檔位置、打包方式
- [x] 移除舊的 `iphoneCopyOneFolder.py` / `iphoneCopyByConfig.py` / `EditThis.txt`
- [x] 修正 `.bat` 的 LF／編碼問題 —— 邏輯移到 `tools/build.py`，
      `build.bat` 只剩一行純 ASCII 轉呼叫，並加上 `.gitattributes`
- [x] **實測通過**（2026-08-28，PyInstaller 6.3.0 / Python 3.12.10）：
      打包成功，產物 113 MB，exe 可正常瀏覽、批次計算、備份、取消、重試。
      單次備份 6626 個檔案成功。
- [x] 打包後啟動：`import 1013 ms`（開發模式是 2279 ms，快了一倍以上）。
      「我們的初始化」那個數字在**首次**啟動時會很大（實測 33 秒），
      那是 Windows 第一次把 `_internal` 裡的 DLL 讀進檔案快取的成本；
      第二次啟動降到 4.4 秒。屬於冷啟動的正常現象。
- [x] `docs/BUILD.md`（自行打包）、`docs/OFFLINE-INSTALL.md`（離線安裝）
- [x] README 加上六張操作截圖與「複製速度 0 位元組」的說明

### 打包後最可能出問題的地方

1. **`EXCLUDES` 排太多** → 執行時噴 ImportError。
   第一步就是把 `iphone_backpacker.spec` 裡的 `EXCLUDES` 清空再打包一次。
2. **pywin32 的 shell 擴充沒被收進去** → 已在 `hiddenimports` 補上
   `win32com.shell.shell` 等，但實機才知道夠不夠。
3. **`console=False` 之後任何殘留的 `print()` 都會閃退**。
   目前 `iphone_backpacker/` 底下已經零 print，但改 code 時要守住這條。

---

## v1.0.1 — 災情修正 ⬜ 待實測

2026-08-29 使用者回報三個現象，處理如下。

| 回報 | 判斷 | 處理 |
|---|---|---|
| 顯示「沒有偵測到 iPhone」但樹能用 | **確認是 bug**：偵測與瀏覽走不同判斷 | 裝置偵測改多重訊號；偵測失敗時 dump 診斷資料 |
| 最新的 `202608_a` 顯示 0 個檔案 | **可疑**：可能是讀取失敗被顯示成 0 | 讀取失敗改顯示「讀取失敗」；列舉失敗改 raise |
| 好像漏掉 `__` 結尾的資料夾 | **未確認**：我們沒有名稱過濾，但有兩條靜默跳過的路徑 | 兩條都改成 raise；新增診斷工具比對 |

- [x] `shell_ns`：列舉中斷、取不到名稱 → 重試一次後 raise，不再靜默截斷
- [x] `shell_ns.attributes_of` 失敗回 `None` 而非 `0`
- [x] `shell_ns.looks_like_filesystem_path()`
- [x] `device._classify()` 多重訊號 + 失敗時 dump「本機」全部節點
- [x] UI：檔案數讀不到顯示「讀取失敗」，不寫進快取所以可重試
- [x] `tools/diagnose_device.py` 診斷工具
- [x] `tests/test_device_classify.py`（22 項測試通過，Linux 可跑）
- [x] `core/diagnostics.py`：診斷邏輯抽成共用模組，GUI 與命令列共用
- [x] GUI 工具列加「產生診斷報告」按鈕 —— **會遇到問題的人正是最不可能
      開終端機的人**，所以診斷必須是一顆按鈕，產物是存在桌面的 `.txt`
      （不是 `.log`），使用者找得到也傳得出去
- [x] 報告會針對「使用者目前選取的資料夾」多做一段逐項檢查，
      等於取代命令列的 `--folder` 參數
- [ ] **⚠ 待回報者實測**：按「產生診斷報告」，把桌面的 .txt 傳回來

### 還沒有答案的一題

**`__` 結尾的資料夾到底存不存在？** 我們的 code 沒有任何按名稱過濾的邏輯，
所以理論上不會漏。但也可能是那支手機本來就這樣命名。

診斷工具會印出完整的子資料夾清單與後綴統計，
**請回報者拿它跟 Windows 檔案總管看到的比對** ——
這是唯一能區分「手機就是這樣」與「我們漏了」的方法。

---

## 明確不做（v1 範圍外）

- 檔案層級逐張勾選 → v2，保險已埋好，見 `01-architecture.md`
- HEIC → JPG 轉檔 → 交給 iPhone 端「自動」模式
- 照片縮圖預覽
- `IFileOperationProgressSink`（pywin32 支援不完整，用分批 + 驗證掃描替代）
- 程式碼簽章憑證（一年約台幣一萬且強制硬體金鑰，對免費工具不划算）
