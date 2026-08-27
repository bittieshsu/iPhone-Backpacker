# iPhone Backpacker

把 iPhone 的照片和影片備份到 Windows 電腦。不用打指令、不用改設定檔，開起來勾一勾就好。

**為什麼不直接用檔案總管？** 因為它很慢。iPhone 透過 MTP 連上 Windows 時，
檔案總管會為每張照片抓縮圖、逐項查詢大小和日期，一個資料夾就要 load 很久；
整個資料夾直接拖曳複製又容易中途失敗，而且失敗了不會告訴你是哪幾個檔案。

這個工具刻意**不抓縮圖、不查詳細資料**，只做備份該做的事。

---

## 系統需求

- **Windows 10 或 11**（不支援 Windows 7／8）
- iPhone 一台，以及能傳輸資料的 USB 線

從原始碼執行的話另外需要 Python 3.12 與 `requirements.txt` 裡的套件。

---

## 使用方式

### 1. 先確認 iPhone 的設定

iPhone 上：**設定 → App → 相簿 → 傳送到 Mac 或 PC**

| 選項 | 你會拿到 | 取捨 |
|---|---|---|
| **自動** | `.jpg` / `.mov` | 手機即時轉檔，相容性最好，但**傳輸明顯較慢** |
| **保留原始檔** | `.heic` / `.heif` / `.mov` | 傳輸快、畫質原汁原味，但 `.heic` 在某些看圖軟體打不開 |

> ⚠ **改了這個設定，一定要把 USB 線拔掉重插才會生效。**

### 2. 接上 iPhone

1. 用 USB 線接上電腦。
2. **解鎖手機**，並在手機畫面上點**「信任這部電腦」**。
3. 點了信任之後，**還要再等一到兩分鐘**（有時候更久），電腦才看得到照片資料夾。
   這段期間用檔案總管看也是空的，**這是正常現象，不是程式壞了**。

### 3. 備份

1. 執行 `iPhoneBackpacker.exe`（或從原始碼跑 `python run_gui.py`）。
2. 左邊展開 **Apple iPhone → Internal Storage**，裡面就是照片資料夾。
3. 勾選要備份的資料夾。
   點一個資料夾再按**「全選」**，可以一次勾選它底下的全部。
4. 想先知道每個資料夾有幾個檔案，框選或 Ctrl 複選多個之後按**「計算檔案數」**。
5. 按**「選擇目的地…」**指定要備份到哪裡。
6. 按**「開始備份」**。

### 好用的細節

- **已經備份過的檔案會自動跳過**，所以重複執行不會重傳，
  中途取消也可以直接再按一次「開始備份」接續。
- 備份完成後如果有檔案沒成功，會列出檔名，按**「重試未完成的項目」**通常就會成功
  （多半是 USB／MTP 傳輸的暫時性問題）。
- 資料夾清單不完整時，按**「重新整理裝置」**。這是 Windows 對 MTP 的已知毛病，
  真的還是不對就把 USB 拔掉重插。

---

## 執行時的紀錄檔

出問題時，紀錄檔在：

```
%LOCALAPPDATA%\iPhoneBackpacker\logs\backpacker.log
```

回報問題時附上這個檔案會很有幫助。

---

## 第一次執行時 Windows 會擋

因為這個程式沒有買數位簽章憑證（一年上萬元，對免費工具不划算），所以：

- **SmartScreen 會跳「Windows 已保護您的電腦」** →
  點「其他資訊」→「仍要執行」。
- **防毒軟體可能誤判** → PyInstaller 打包的程式被誤判是常見狀況。
  真的被擋掉的話，把資料夾加進白名單，或到
  [Microsoft Security Intelligence](https://www.microsoft.com/en-us/wdsi/filesubmission)
  提交誤判回報。

程式**不會連上網路**，也**不需要管理員權限**。

---

## 從原始碼執行

```bash
pip install -r requirements.txt
python run_gui.py
```

### 自己打包

```bash
pip install pyinstaller
build.bat
```

產物在 `dist\iPhoneBackpacker\`。發布時把**整個資料夾**壓成 zip ——
不能只複製 `.exe`，它需要同資料夾裡的其他檔案。

---

## 給開發者 / AI 助理

`docs/ai/` 底下有專案的架構、決策紀錄與 Windows Shell COM 的踩坑筆記。
動手改之前請先讀 `CLAUDE.md` 和 `docs/ai/01-architecture.md`。

技術上這個工具透過 Windows Shell COM（`IShellFolder` / `IShellItem` /
`IFileOperation`）存取 iPhone，也就是檔案總管自己在用的那條路 ——
因為 iPhone 走 MTP，沒有磁碟機代號也沒有真實路徑，
Python 的 `os.listdir()` / `pathlib` / `shutil` 完全看不到它。

---

## Credits

**[3chdog](https://github.com/3chdog/)**

[![GitHub](https://img.shields.io/github/followers/3chdog.svg?style=social&label=Follow%203chdog)](https://github.com/3chdog/)
