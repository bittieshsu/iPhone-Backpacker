# 03 — 決策紀錄

最後更新：2026-08-26

**已定案的方向不要重新提案。要推翻請在這裡新增一筆並註明理由。**

---

## D1 — 留在 Python，不改寫成 C#  ✅ 定案

**背景**：MTP/PTP 存取在 C# 有現成的 WPD 封裝套件，Python 要自己刻。

**決策**：留在 Python。

**理由**：專案**已經**用 `IShellFolder` + `IFileOperation` 打通 MTP 存取，
最難的一關已過。為了 MTP 而重寫成 C# 的理由不存在，重寫只是把驗證過的東西再踩一次雷。

---

## D2 — 放棄 Windows 7  ✅ 定案（使用者決定）

**背景**：原 README 標示支援 Windows 7 / 10 / 11。

**衝突**：
- **Qt 6 官方只支援 Windows 10 以上**，Win7/8 完全不支援
- **Python 3.8 是最後一個支援 Windows 7 的版本**，3.9 起要 Win8.1+

所以「支援 Win7」與「Python 3.12 + PySide6」互斥。

**決策**：**放棄 Win7**，目標平台 Windows 10 / 11，技術棧 Python 3.12 + PySide6 (Qt6) + 最新 pywin32。

**理由**：Win7 已 EOL 六年；新版 iPhone 在 Win7 上跑 Apple Devices 驅動本來就不穩；
為了它把整個技術棧凍在 2020 年，代價太大（連帶防毒誤判率也較高）。

**待辦**：README 的平台標示要改（階段 6）。

---

## D3 — GUI 用 PySide6 + `QTreeWidget`  ✅ 定案

**決策**：PySide6（LGPL，商業使用較單純；PyQt 是 GPL/商業雙授權）。
用 `QTreeWidget`（item-based），不用 model/view 的 `QTreeView`。

**★ 絕對不要用 `QFileSystemModel`** —— 它只認真實檔案系統路徑，**看不到 iPhone**。這是很多人會撞的第一面牆。

**理由**：`QTreeWidget` 撐幾萬個 item 沒問題，PIDL 直接塞 `setData(0, Qt.UserRole, pidl)`。
真的遇到效能瓶頸再換自訂 `QAbstractItemModel`。先求有。

---

## D4 — v1 只做「資料夾層級勾選」  ✅ 定案（使用者決定）

**決策**：v1 不做檔案層級逐張勾選。

**擴充性評估**：**不會卡住後續發展**，前提是核心邊界設計成「吃一份檔案清單」而非「吃一個資料夾」。
這件事本來就非做不可（是修 `getFilteringSignals` zip bug 的正解），等於免費拿到擴充性。
具體三條保險見 `01-architecture.md` 的「擴充性保險」。

**v2 真正會變麻煩的**：列舉成本被搬進互動路徑（點資料夾當下就要列舉幾千檔）。
但所需的背景執行緒 + 延遲載入，v1 的樹狀延遲展開本來就要蓋。

---

## D5 — 不要比對 Shell 顯示名稱  ✅ 定案

**背景**：既有 code 有 `ChineseCharacterChecking()`（`"Desktop"` → `"桌面"`）和 `rootName="本機"`。

**問題**：英文版 Windows 是 `"This PC"`、日文版是 `"PC"`，一給別人就爆炸。
使用者把 iPhone 改名成「小明的 iPhone」也會失效。

**決策**：一律用 CSIDL / PIDL / `SHGDN_FORPARSING` 判斷。
裝置偵測改成「從 `CSIDL_DRIVES` 底下找出 `SHGDN_FORPARSING` 不符合 `X:\` 格式的節點」。

**附帶好處**：這也順便解掉「iOS 改版導致資料夾結構改變」的問題 —— 不再依賴任何寫死的路徑深度。

---

## D6 — 不碰 `IFileOperationProgressSink`  ✅ 定案

**背景**：使用者希望有「被略過的檔案」的 log。

**決策**：不實作 progress sink（pywin32 對它的支援不完整，要自己寫 COM gateway）。
改用 **`FOF_NOERRORUI` 靜默略過 + 分批執行 + 事後驗證掃描**。

**理由**：驗證掃描（比對來源選取清單 vs 目的地實際檔名/大小）同時給出失敗清單、
真實進度、以及「重試失敗項目」的基礎，成本遠低於刻 COM gateway。

---

## D7 — 打包用 PyInstaller `--onedir`  ✅ 定案

**決策**：`--onedir` 再壓成 zip 發布，**不要 `--onefile`**。

**理由**：onefile 執行時會解壓到 temp 再執行，這在啟發式偵測眼中就是標準的惡意軟體脫殼特徵，
誤判率高非常多。若誤判仍嚴重，再考慮 Nuitka（編成 C，特徵完全不同）。

**相關**：使用者原本擔心的「防火牆問題」不存在 —— 本程式不連網路，不會跳防火牆提示。
真正擋人的是 SmartScreen（未簽章必跳）與防毒誤判。
簽章憑證 OV 一年約台幣一萬且強制硬體金鑰/雲端 HSM，對免費工具不划算，
改在 README 教使用者點「其他資訊 → 仍要執行」。
