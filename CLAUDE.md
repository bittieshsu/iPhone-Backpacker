# CLAUDE.md — iPhone Backpacker

## 這個專案是什麼

給 **Windows 使用者** 把 **iPhone 照片/影片備份到電腦** 的工具。

iPhone 接上 Windows 走 **PTP/MTP**，在檔案總管看得到但**沒有磁碟機代號、沒有真實路徑**，
所以 `os.listdir()` / `pathlib` / `shutil` **完全看不到手機裡的檔案**。
本專案透過 Windows Shell COM（`IShellFolder` / `IShellItem` / `IFileOperation`）存取，
這是檔案總管本身用的那條路。**這一關已經解掉了，不要因為任何理由重寫成別的方案。**

目前狀態：CLI 版可用（靠 `EditThis.txt` 設定路徑），正在改造成 PySide6 GUI。

## 開始工作前必讀

`docs/ai/` 是專門維護給 AI 讀的專案狀態文件。**動手前先讀：**

- `docs/ai/00-overview.md` — 專案現況與使用者情境
- `docs/ai/01-architecture.md` — 目標架構與 core 介面設計（**改 code 前必讀**）
- `docs/ai/02-roadmap.md` — 分階段進度，目前做到哪
- `docs/ai/03-decisions.md` — 已定案的決策與理由（不要重新提案已否決的方向）
- `docs/ai/04-shell-com-notes.md` — Shell COM / MTP 踩坑筆記（**寫 COM 相關 code 前必讀**）

**做完一個階段要回頭更新 `02-roadmap.md` 的進度，有新決策要補進 `03-decisions.md`。**

## 硬性限制

- **目標平台：Windows 10 / 11。已明確放棄 Windows 7。**
- Python 3.12 / PySide6 (Qt6) / pywin32 最新版。
- **開發機是 Linux，`pywin32` 裝不起來，任何 Shell COM 程式碼在此無法執行或測試。**
  所有實機驗證都必須由使用者在 Windows 上進行。不要宣稱「已測試通過」。

## 寫 code 的規矩

- **絕對不要用 `QFileSystemModel`。** 它只認真實檔案系統路徑，看不到 iPhone。用 `QTreeWidget`。
- **絕對不要比對 Shell 的顯示名稱字串**（`"本機"`、`"Apple iPhone"`、`"Desktop"`…）。
  英文/日文版 Windows 會全滅，使用者把 iPhone 改名也會失效。
  一律用 CSIDL / PIDL / `SHGDN_FORPARSING` 判斷。
- **GUI 相關程式碼裡不准有 `print()` / `tqdm`。** PyInstaller `--windowed` 後 `sys.stdout is None`，
  `print()` 會直接 `AttributeError` 閃退。用 `logging` 寫檔。
- **不准用 `assert` 做執行期檢查**（`-O` 模式整行會被移除）。改用例外。
- `core/` 底下**零 Qt import、零 print**。UI 只能單向依賴 core。
- COM 介面（`IShellFolder` / `IShellItem`）**不能跨執行緒傳遞**；PIDL 是純資料，可以。
  worker thread 開頭 `pythoncom.CoInitialize()`、結尾 `CoUninitialize()`。

## 使用者情境備註

使用者在 iPhone 的「設定 → App → 相簿 → 傳送到 Mac 或 PC」有兩種模式，會影響拿到的檔案格式：

- **自動**：手機端即時轉檔，照片出 `.jpg`、影片出 `.mov`。傳輸明顯較慢。
- **保留原始檔**：直出 `.heic` / `.heif` / `.mov`。

**中途改設定要重新插拔 USB 才生效。** 這點必須寫進 UI 提示。
