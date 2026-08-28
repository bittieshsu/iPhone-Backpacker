# docs/

| 路徑 | 給誰看 | 內容 |
|---|---|---|
| [BUILD.md](BUILD.md) | 想自己打包成 exe 的人 | 打包步驟、打包設定的理由、失敗時怎麼查 |
| [OFFLINE-INSTALL.md](OFFLINE-INSTALL.md) | 沒有網路的環境 | 要先下載哪些 `.whl`，以及怎麼離線安裝 |
| `img/` | README 用的螢幕截圖 | |
| [`ai/`](ai/) | **AI 助理 / 接手的開發者** | 專案現況、架構決策、踩坑筆記 |

一般使用者請看 repo 根目錄的 [README.md](../README.md)。

## `docs/ai/` 檔案一覽

- `00-overview.md` — 專案現況、使用者情境、程式碼盤點
- `01-architecture.md` — 目標架構、core 介面設計、擴充性保險
- `02-roadmap.md` — 分階段執行計畫與進度
- `03-decisions.md` — 決策紀錄（ADR），含已否決的方向
- `04-shell-com-notes.md` — Windows Shell COM / MTP 技術筆記與實測數據

## 維護原則

- 決策改變時**改 `03-decisions.md`，不要只在對話裡講**。
- 階段完成時**更新 `02-roadmap.md` 的勾選狀態**。
- 踩到新的 COM/MTP 坑時**補進 `04-shell-com-notes.md`**，這份文件的價值隨時間累積。
- 效能量測**一律先 warm-up 再計時**，並且只相信同一輪執行內的比例
  （絕對數字跨 session 浮動可達 3 倍）。
