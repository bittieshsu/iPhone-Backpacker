# 04 — Windows Shell COM / MTP 技術筆記

最後更新：2026-08-26

**寫任何 COM 相關程式碼前先讀這份。踩到新坑請補進來，這份文件的價值隨時間累積。**

---

## PIDL 與 COM 介面的執行緒規則 ★

- **`IShellFolder` / `IShellItem` 等 COM 介面不能跨執行緒直接傳遞**（COM apartment 的限制）。
- **PIDL 可以** —— 在 pywin32 裡本質是 bytes，是純資料。

所以複製作業的標準模式是：

```
主執行緒：收集使用者勾選項的「絕對 PIDL」（bytes）
   ↓ 丟給 worker thread
worker：pythoncom.CoInitialize()
        → shell.SHCreateItemFromIDList(abs_pidl) 重建 IShellItem
        → 執行複製
        → pythoncom.CoUninitialize()
```

**建議一律使用絕對 PIDL**（`shell.SHGetIDListFromObject(folder)` + 子項 PIDL 串接），
這樣 worker 端可以一步 `SHCreateItemFromIDList`，不必重走 bind 鏈。

---

## 目的地不需要走 shell 樹

目的地永遠是**真實檔案系統路徑**，直接用：

```python
shell.SHCreateItemFromParsingName(str(dest_path), None, shell.IID_IShellItem)
```

既有的 `getFolderObject_byAbsPath(dst)` 那整條路可以刪掉。這是不小的簡化。

---

## `IFileOperation` 的旗標

| 旗標 | 作用 |
|---|---|
| `FOF_NOCONFIRMATION` | 所有對話框一律回「全部是」。**撞名時等同全部覆蓋。** |
| `FOF_NOERRORUI` (`0x0400`) | **關掉錯誤/「是否略過」小視窗**，失敗項目靜默跳過 |
| `FOF_SILENT` | 關掉**進度視窗**（我們不要用它 —— 原生進度視窗品質比自己畫的高） |

- `FOF_NOCONFIRMATION` **不會**關掉進度視窗，所以 v1 直接用系統原生進度視窗就好。
- 記得 `pfo.SetOwnerWindow(hwnd)` 把它掛在 Qt 主視窗底下，否則會變成孤兒視窗。
- 複製後用 `pfo.GetAnyOperationsAborted()` 判斷是否被中途取消。

### ★ `PerformOperations()` 在零排程時會噴 `0x8000FFFF`

`-2147418113` = `0x8000FFFF` = `E_UNEXPECTED` =「災難性的失敗」。
**呼叫前一定要 guard「排程數為 0 就直接返回」。** 已於階段 0 修掉。

---

## 失敗檔案的處理（使用者回報的問題 3）

不要碰 `IFileOperationProgressSink`（pywin32 支援不完整）。用這個組合：

1. `SetOperationFlags(FOF_NOCONFIRMATION | FOF_NOERRORUI)` → 失敗項目靜默略過，不跳小視窗
2. 把批次切成每 100~200 檔一次 `PerformOperations()` → 得到**真實進度**與更細的失敗定位
3. 每批之後做**驗證掃描**：列舉來源選取清單 vs 目的地實際檔名/大小，對不上的就是失敗清單
4. UI 顯示「N 個檔案失敗」+ 可展開清單 + 「重試失敗項目」按鈕，同時寫進 log 檔

---

## 列舉順序不保證穩定 ★

**Shell 的 `IEnumIDList` 列舉順序不保證兩次一致**，MTP 上尤其。

既有 code 的 `getFilteringSignals()` 對同一個 folder 做**兩次獨立列舉**再 `zip`，
一旦順序對不齊就會**複製到錯的檔案**。這是真的 bug。

**正解**：一次列舉就把「PIDL + 檔名」綁在同一個 `FileEntry` 裡，永遠不要靠兩次列舉的順序對齊。
（`copier` 介面收 `list[FileEntry]` 也是為了這件事。）

---

## 效能：檔案總管為什麼慢 ★

在 MTP 上，慢的主因**不是列舉本身**，是這兩件事：

| 昂貴的操作 | 成本 |
|---|---|
| **縮圖（thumbnail）** | 每張照片都要把影像資料抓下來解碼。最大宗。 |
| **`GetDetailsOf` 取大小/日期** | **每個項目一次來回**。幾千個項目就是幾千次來回。 |

**本專案兩件都不做**（使用者明確表示不需要縮圖），這是我們能比檔案總管快的根本原因。

實作規則：
- 樹狀展開用 `SHCONTF_FOLDERS`，只要資料夾
- `list_children(..., want_details=False)` 是預設，`GetDetailsOf` 只在真的要用時呼叫
- 列舉結果進 session 快取，收合再展開零成本
- 「這個資料夾有沒有照片」用早退判斷，找到第一個就 return，不要數完

### 實測數據

**2026-08-27，階段 1 煙霧測試（本機資料夾，Windows + Python 3.12，插著 iPhone）**

| 操作 | 耗時 | 備註 |
|---|---|---|
| `this_pc_pidl()` | 7.1 ms | CSIDL_DRIVES，可忽略 |
| **列出「本機」底下 8 個節點** | **774.5 ms** | **★ 超過 0.5s 目標，見下方** |
| `list_subfolders()` 本機小資料夾（首次） | 6.5 ms | |
| `list_subfolders()` 同節點（快取命中） | < 0.1 ms | 快取有效 |
| `folder_has_media()` 早退 | 2.6 ms | |
| `iter_files()` 完整列舉（4 個檔案） | 2.1 ms | |
| `run_copy()` 複製 4 個檔案 | 598.6 ms | **固定成本，不是每檔成本** |
| `run_copy()` 重跑（全部命中增量去重） | 15.4 ms | 去重有效 |

驗證通過的假設：
- `IEnumIDList.Next(64)` 批次列舉可用，沒有觸發單筆 fallback。
- 「本機」底下同時列出磁碟機（`OS (C:)`）、使用者資料夾（`下載`/`圖片`/`桌面`…）
  與 `Apple iPhone`，證實 `SFGAO_FOLDER && !SFGAO_FILESYSTEM` 這個判斷有東西可分。

### 「本機」列舉的 ~770 ms —— 已釐清，**不是** MTP warm-up

**2026-08-27 實測對照組：**

| | 沒插 iPhone | 插著 iPhone |
|---|---|---|
| 列出「本機」（冷） | 772.7 ms | 768.5 ms |
| 列出「本機」（熱） | 484.8 ms | 503.7 ms |
| `find_portable_devices()` | 476.3 ms | 498.7 ms |
| `probe()` | — | 860.0 ms |

兩組幾乎一模一樣 → **這是列舉「本機」本身的固有成本，跟 iPhone 無關。**
（原本假設是 Shell 為 MTP 開 WPD session 的 warm-up，**該假設已被推翻。**）

最可能的來源：測試機上有 `SDXC (D:)` 與 `USB 磁碟機 (F:)`，
可移除式磁碟機要查媒體狀態與磁碟區標籤，這在 Shell 列舉時是同步的。

**設計結論（不變，但理由不同）**：裝置偵測總成本約
`列舉本機 ~500ms + find_portable_devices ~500ms + probe ~860ms ≈ 1.9 秒`。
**UI 啟動絕不能同步等它** —— 先畫出視窗與「正在偵測裝置…」狀態，
偵測在背景執行緒跑完再填樹。

### 另一個實測發現：`IFileOperation` 有固定啟動成本

複製 4 個檔案花了 598 ms。這幾乎全是建立 `IFileOperation` +
`PerformOperations()` 的固定開銷，不是每檔成本。

**設計含意**：chunk 不能切太小，否則固定成本會被乘上批次數。
`chunk_size=200` 的預設值是合理的，不要為了進度更新頻繁而調小。

### iPhone 實測（2026-08-27，一支有 50~100 個資料夾的 iPhone）

| 操作 | 耗時 | 判讀 |
|---|---|---|
| 展開 `Apple iPhone`（1 個子項） | 13.2 ms | 快 |
| **展開 `Internal Storage`（50~100 個子項）** | **2772.1 ms** | **★ 主要瓶頸** |
| 展開葉節點 `200101__`（2 個項目） | 98.2 ms | |
| 同一節點再展開（快取命中） | < 0.1 ms | 快取有效 |
| 單次列舉的固定開銷 | **~45 ms** | 見下 |

**★ MTP 每次列舉有 ~45ms 的固定開銷。**
證據：在只有 2 個項目的葉節點上，三種旗標分別量到 44.0 / 47.8 / 48.3 ms ——
內容幾乎不影響，全是固定成本。

這個數字的設計含意很大：**任何「對每個資料夾都做一次列舉」的演算法，
在 100 個資料夾的裝置上光固定開銷就是 4.5 秒**，而 `find_photo_folders`
初版對每個資料夾做**兩次**列舉（`folder_has_media` + `list_subfolders`）。
實測跑了 20 分鐘沒有結束，這就是原因之一。

### ★ 仍未回答：`SHCONTF_FOLDERS` 到底有沒有省到

第一版 benchmark **跑在只有 2 個項目的葉節點上**，量到的全是固定開銷，
印出來的「只省 8%」結論**無效，不要採信**。

`tools/smoke_device.py` v2 已改成自動挑「子資料夾最多」的那一層來測
（對 iPhone 就是 `Internal Storage`），並在項目數 < 20 時直接拒絕下結論。
**這個問題會決定階段 3 的 UI 要不要走骨架畫面備案，重測前不要開始寫 GUI。**

---

## MTP / iPhone 的行為特性

- **未解鎖或未點「信任這部電腦」時，資料夾會列舉成「空的」而不是報錯。**
  使用者只會看到空白視窗，以為程式壞了。
  → 必須偵測「裝置節點存在但內容為空」並明確提示「請解鎖 iPhone 並在手機上點『信任』」。
  **這是投報率最高的單一功能。**
- **列舉幾千張照片超過 30 秒是正常的。** 樹狀節點必須延遲展開 + 背景執行緒，否則視窗直接凍住。
- **MTP 偶爾回傳不完整的清單**，這是 Windows 的已知毛病，重新插拔就好 → UI 放「重新整理裝置」按鈕。
- **`GetDetailsOf` 取 size/mtime 在 MTP 上很慢**，只在真的需要時才呼叫（`want_details=True`）。
- **iOS 改版會改變 DCIM 結構**，實際觀察到三種形態（見 `00-overview.md`）。
  → 永遠不要寫死路徑深度；用樹狀瀏覽 + 「自動尋找照片資料夾」遞迴掃描。

---

## 打包相關的坑

- **PyInstaller `--windowed` 後 `sys.stdout` / `sys.stderr` 是 `None`**，
  此時 `print()` 直接拋 `AttributeError` 讓程式閃退。`tqdm` 寫 stderr，同樣中招。
  → GUI 路徑上不准有 `print` / `tqdm`，一律 `logging` 寫檔。
- **`assert` 在 `-O` 模式會被整行移除** → 不要拿 `assert` 做執行期檢查。
- manifest 用 `asInvoker`，**不要**要求管理員權限 —— 本程式不需要，多跳一個 UAC 只會更嚇人。
