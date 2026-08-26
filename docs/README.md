# docs/

| 路徑 | 給誰看 | 內容 |
|---|---|---|
| `docs/ai/` | **AI 助理 / 接手的開發者** | 專案現況、架構決策、踩坑筆記。每次改動後應保持同步。 |
| `README.md`（repo 根目錄） | 一般使用者 | 安裝與使用說明 |
| `CLAUDE.md`（repo 根目錄） | AI 助理 | 進入專案的第一份指引，指向 `docs/ai/` |

## `docs/ai/` 檔案一覽

- `00-overview.md` — 專案現況、使用者情境、目前程式碼盤點
- `01-architecture.md` — 目標架構、core 介面設計、擴充性保險
- `02-roadmap.md` — 分階段執行計畫與進度
- `03-decisions.md` — 決策紀錄（ADR），含已否決的方向
- `04-shell-com-notes.md` — Windows Shell COM / MTP 技術筆記

## 維護原則

- 決策改變時**改 `03-decisions.md`，不要只在對話裡講**。
- 階段完成時**更新 `02-roadmap.md` 的勾選狀態**。
- 踩到新的 COM/MTP 坑時**補進 `04-shell-com-notes.md`**，這份文件的價值隨時間累積。
