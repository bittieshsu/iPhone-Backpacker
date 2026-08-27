# 00 — 專案現況

最後更新：2026-08-26

## 一句話

給 Windows 10/11 使用者、把 iPhone 相簿備份到電腦的桌面工具。目前正從 CLI 改造為 GUI。

## 為什麼這件事不簡單

iPhone 接上 Windows 走 **PTP/MTP**：檔案總管看得到，但**沒有磁碟機代號、沒有 `D:\DCIM` 這種真實路徑**。
Python 標準函式庫（`os` / `pathlib` / `shutil`）**完全看不到手機裡的檔案**。

唯一實務可行的純 Python 路徑是 **Windows Shell COM**（`pywin32` 的 `win32com.shell`），
也就是檔案總管自己在用的那套 `IShellFolder` / `IShellItem` / `IFileOperation`。
**本專案已經打通這一關**，這是最大的既有資產。

## 使用者情境

- 目標使用者：**一般民眾**，不會用 cmd、不會編輯設定檔。
- 使用流程期望：插上 iPhone → 開 `.exe` → 直覺的視窗瀏覽 → 勾選 → 備份。
- **不需要**顯示照片縮圖，但要看得到檔案類型。
- iPhone 端設定（設定 → App → 相簿 → 傳送到 Mac 或 PC）：
  - **自動** — 手機即時轉檔，出 `.jpg` / `.mov`，較慢
  - **保留原始檔** — 直出 `.heic` / `.heif` / `.mov`
  - 改設定後**必須重新插拔 USB** 才生效
- 目前不處理轉檔（HEIC → JPG 等），交給 iPhone 端設定決定。

## 目前程式碼盤點

```
iphone_backpacker/
  core/     純邏輯，零 Qt、零 print（errors / logging_setup / filters /
            shell_ns / listing / device / copier）
  ui/       PySide6（main_window / workers / dialogs）
  app.py    進入點
run_gui.py            開發用啟動腳本
iphone_backpacker.spec / build.bat    PyInstaller 打包
tools/                煙霧測試與效能 benchmark
tests/                純 Python 單元測試（不需要 Windows）
```

### 已經移除的舊版（階段 6，需要時從 git 歷史取回）

`iphoneCopyOneFolder.py` / `iphoneCopyByConfig.py` / `EditThis.txt`

隨之消失的東西，**不要再重新引入**：

- `parseAbsName` / `iter_split` / `checkFolderName` / `getFolderObject_byAbsPath` /
  `ChineseCharacterChecking` —— 整套字串路徑解析層。存在的唯一理由是
  「把 txt 裡的字串轉回 shell 物件」；GUI 的節點手上就握著 PIDL，不需要反查。
  這也是 iOS 改資料夾結構會打爆舊版的根源。
- `appendSequence` / `getNumAndSuffix`（`100APPLE ~ 105APPLE` 序號展開）——
  在日期式資料夾命名下已經失效。

## 使用者實際回報的問題

1. **iOS 改版會改變資料夾結構**，實際觀察到三種：
   - `本機\Apple iPhone\Internal Storage\DCIM\100APPLE`
   - `本機\Apple iPhone\Internal Storage\DCIM\202510_a`
   - `本機\Apple iPhone\Internal Storage\202510_a`
   → 解法見 `01-architecture.md` 的裝置偵測與「自動尋找照片資料夾」
2. **沒有檔案要複製時噴 `-2147418113 災難性的失敗`**
   → `0x8000FFFF` (E_UNEXPECTED)，`PerformOperations()` 在零排程時的行為。已於階段 0 修掉。
3. **有問題的檔案 Windows 複製失敗會跳小視窗問是否略過，略過後沒有任何 log**
   → 解法見 `04-shell-com-notes.md` 的「失敗檔案的處理」
