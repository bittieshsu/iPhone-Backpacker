# 01 — 目標架構

最後更新：2026-08-26

## 模組配置

```
iphone_backpacker/
  core/                    ← 純邏輯。零 Qt import、零 print、錯誤一律 raise
    errors.py              例外型別
    shell_ns.py            Shell 命名空間：this_pc() / 列舉 / 顯示名稱 / 屬性
    device.py              可攜式裝置偵測、裝置狀態、自動尋找照片資料夾
    listing.py             FileEntry 與資料夾列舉
    filters.py             副檔名分類
    selection.py           選取模型（見下方「擴充性保險」）
    copier.py              CopyPlan / CopyReport / 分批複製 / 驗證
    logging_setup.py       logging 設定（寫檔，不寫 stdout）
  ui/
    main_window.py         主視窗：左樹狀 + 右資訊 + 工具列
    workers.py             QThread wrapper，內含 CoInitialize/CoUninitialize
    dialogs.py             首次啟動引導、信任提示、失敗清單
  app.py                   進入點
```

**依賴方向是單向的：`ui/` → `core/`。`core/` 不准知道 Qt 的存在。**

## core 介面設計（草案）

### `listing.py`

**★ 這裡的 API 刻意分成「瀏覽路徑」與「複製路徑」兩組，理由見下方「效能契約」。**

```python
@dataclass(frozen=True)
class FileEntry:
    name: str
    is_dir: bool
    abs_pidl: bytes                  # 絕對 PIDL，純資料，可安全跨執行緒傳遞
    size: int | None = None          # MTP 上取得成本高，預設不取，允許為 None
    mtime: datetime | None = None

# --- 瀏覽路徑：互動中呼叫，必須快 ---

def list_subfolders(folder_abs_pidl) -> list[FileEntry]:
    """只列舉『子資料夾』（SHCONTF_FOLDERS），不含檔案、不取 details、不取縮圖。
    結果進 NamespaceCache。這是樹狀節點展開時唯一會呼叫的東西。"""

def folder_has_media(folder_abs_pidl, categories, *, probe_limit=200) -> bool:
    """判斷資料夾裡有沒有目標類型的檔案。
    ★ 找到第一個符合的就立刻 return True，不要數完。
    probe_limit 是保險上限，避免在超大資料夾裡白跑。"""

# --- 複製路徑：背景執行緒中呼叫，允許慢 ---

def iter_files(folder_abs_pidl, categories) -> Iterator[FileEntry]:
    """★ 回傳 generator 而非 list。
    讓 copier 邊列舉邊排程，使用者立刻看到進度，不會先卡一段無聲的列舉期。
    v1 在按下備份時才呼叫；v2 會改在使用者點開資料夾時呼叫來填檔案清單面板。
    同一支函式，只是呼叫時機不同。"""

# --- 快取 ---

class NamespaceCache:
    """以 abs_pidl 為 key 快取 list_subfolders() 的結果。
    存活範圍：一次 session。使用者按「重新整理裝置」才整個清空。
    效果：同一個節點收合再展開是零成本的。"""
```

### `device.py`

```python
class DeviceStatus(Enum):
    OK = auto()
    NOT_FOUND = auto()               # 沒偵測到可攜式裝置
    LOCKED_OR_UNTRUSTED = auto()     # 裝置在，但內容列舉為空

def find_portable_devices() -> list[FileEntry]:
    """從 CSIDL_DRIVES 底下找出『不是磁碟機』的節點。
    判斷依據是 SHGDN_FORPARSING 的回傳值不符合 'X:\\' 格式 —— 語言中立、不受改名影響。
    絕對不要比對顯示名稱是不是 'Apple iPhone'。"""

def probe(device: FileEntry) -> DeviceStatus

def find_photo_folders(device: FileEntry, *, max_depth=3) -> list[FileEntry]:
    """從裝置根往下遞迴，收集『含有影像/影片副檔名的葉節點資料夾』。
    有 DCIM 就走 DCIM，沒有就掃根層 —— Apple 再改結構也不用改 code。

    ★ 判斷「這個資料夾有沒有照片」用 folder_has_media()，找到第一個就早退，
      絕不數完。否則這個功能會變成掃描整支手機。
    ★ 一律在背景執行緒跑、可取消、過程中 UI 保持可操作。"""
```

### `filters.py`

```python
class Category(Flag):
    IMAGE = auto()     # jpg jpeg png bmp gif tif tiff heic heif dng webp
    VIDEO = auto()     # mov mp4 m4v avi 3gp mpg mpeg
    SIDECAR = auto()   # aae（iPhone 的編輯紀錄，非影像本體）
    OTHER = auto()

def categorize(file_name: str) -> Category   # 一律 os.path.splitext + .lower()
```

### `copier.py`

複製分三段，**一個來源資料夾 = 一次 `IFileOperation`**（決策 D12）：

1. **列舉 + 過濾 + 去重** —— 我們自己回報「已找到 N 個」
2. **一次排程整個資料夾** —— 原生進度視窗只出現一次
3. **驗證掃描** —— 比對目的地，產生失敗清單

不要切成小批次。每次 `PerformOperations()` 有約 600 ms 固定開銷，
而且原生進度視窗會**每批彈出一次** —— 使用者早期用逐檔複製就是栽在這裡。

```python
@dataclass
class CopyPlan:
    dest_dir: Path
    sources: list[FileEntry]            # 被勾選的「資料夾」，檔案在執行時才展開
    skipped_existing: list[FileEntry]   # 增量備份時已存在而跳過的（執行中累積）

@dataclass
class CopyReport:
    copied: list[str]
    failed: list[str]                   # 驗證掃描比對出來的
    aborted: bool                       # GetAnyOperationsAborted()

def plan_copy(sources, dest_dir: Path, *, incremental: bool = True) -> CopyPlan:
    """只做便宜的事：確認目的地可寫、列舉目的地既有檔名+大小建索引。
    不在這裡展開來源檔案清單。"""

def run_copy(plan: CopyPlan, categories, *, owner_hwnd=None,
             chunk_size=None, progress=None, cancel=None) -> CopyReport:
    """在背景執行緒跑。逐個 source 資料夾：
    列舉+過濾+去重（自己回報進度）→ 一次 IFileOperation → 驗證掃描。

    ★ chunk_size 預設 None = 不切批。切批會讓原生進度視窗反覆彈出，
      而且每次 PerformOperations() 有 ~600ms 固定開銷（決策 D12）。

    ★ 排程單位永遠是「檔案」，永遠不是「資料夾」。
      整包丟給 shell 遞迴雖然快，但失敗時拿不到是哪個檔案失敗 ——
      這正是使用者用檔案總管複製整個資料夾時遇到的「不穩定」。

    ★ 增量去重讓重跑不重傳。MTP 很慢，這個差別是分鐘 vs 小時級。"""
```

## 擴充性保險 ★

v1 只做「資料夾層級勾選」，但要能無痛長出「取消個別檔案」，靠這三條：

**1. `run_copy()` 的參數型別是 `list[FileEntry]`，永遠不接受資料夾物件。**
   （這同時也是修掉 `getFilteringSignals` 那個 zip 對不齊 bug 的正解，等於免費拿到擴充性。）

**2. `FileEntry` 從第一天就帶齊 `name / is_dir / size / mtime / abs_pidl`**，
   即使 v1 的 UI 只顯示資料夾名稱。v2 加檔案清單面板時，列舉層一行都不用改。

**3. 選取模型預留 SUBSET：**

```python
# core/selection.py
class SelectionState(Enum):
    ALL = auto()      # v1 永遠只產生這個
    SUBSET = auto()   # v2 才會用到

@dataclass
class FolderSelection:
    folder: FileEntry
    state: SelectionState = SelectionState.ALL
    excluded: frozenset[str] = frozenset()   # v1 永遠是空的

def resolve(selections: list[FolderSelection], categories) -> list[FileEntry]:
    """展開成最終要複製的檔案清單。v2 只是讓 UI 有辦法產生 SUBSET，
    resolve() 之後的所有東西（plan_copy / run_copy）完全不用改。"""
```

### v2 真正會變麻煩的地方（誠實記錄，但都是「新增」不是「改寫」）

- 右側檔案清單面板 + 三態父勾選連動 —— 中等難度，純新增，不動既有 code。
- **列舉成本被搬進互動路徑** —— 這才是痛點。v1 可以拖到按下備份才列舉；
  v2 必須在使用者點資料夾當下就列舉幾千個檔案。
  但所需的「背景執行緒 + 延遲載入」基礎建設，v1 的樹狀延遲展開本來就要蓋。

## UI 設計（v1）

- **左**：裝置資料夾樹，**延遲展開**（點開才列舉，且在背景執行緒），節點帶 checkbox。
  用 `QTreeWidget`（不是 `QTreeView`），`item.setData(0, Qt.UserRole, abs_pidl)`。
  **不要用 `QFileSystemModel`** —— 它看不到 iPhone。
- **右**：資料夾摘要（名稱、路徑、已勾選數量）。
  **★ 檔案數/大小這類需要列舉檔案才算得出來的東西，一律背景非同步計算、算完才填，
  且永遠不阻塞勾選與「開始備份」。** 點資料夾當下什麼都不算，立刻回應。
  v2 在此長出檔案清單。
- **工具列**：重新整理裝置 / 自動尋找照片資料夾 / 全選 / 全不選 / 選擇目的地 / 開始備份。
- **狀態列**：裝置狀態（未偵測到 / 請解鎖並點信任 / 就緒）。
- 複製進度直接用 **Windows 原生進度視窗**（`IFileOperation` 預設就有），
  記得 `pfo.SetOwnerWindow(hwnd)` 掛在主視窗底下，避免變成孤兒視窗。

## 效能契約 ★★

**這是本專案存在的理由 —— 使用者做這個工具就是因為檔案總管太慢。違反這節等於做白工。**

目標：**點開/勾選一個資料夾，0.5 秒內要有畫面反應。**

### 三條硬規則

1. **瀏覽路徑上絕對不碰檔案。**
   樹狀展開只呼叫 `list_subfolders()`（`SHCONTF_FOLDERS`）。
   不列舉檔案、不取縮圖、不呼叫 `GetDetailsOf`。
2. **任何「需要列舉檔案才算得出來」的資訊，一律非同步且可放棄。**
   算不出來就不顯示，絕不讓使用者等。
3. **列舉結果一律進 `NamespaceCache`**，只有「重新整理裝置」才失效。

### 為什麼檔案總管慢，而我們可以不慢

檔案總管在 MTP 上慢的主因不是列舉本身，是兩件昂貴的事：

- **縮圖** —— 為每張照片要一份 thumbnail，等於**把每張圖的資料抓下來解碼**。這是最大宗。
- **詳細資料欄位** —— 大小/日期/拍攝時間每一項都是 `GetDetailsOf`，**每個項目一次來回**。

**我們兩件都不做。** 「不需要縮圖」是使用者一開始就定下的需求，
它的價值比看起來大得多 —— 直接砍掉了檔案總管慢的主要原因。

### 尚未驗證的部分（誠實記錄）

`IShellFolder::EnumObjects` 帶 `SHCONTF_FOLDERS` 時，
**iPhone 的 MTP shell extension 是否真的只列舉資料夾、還是內部仍走訪全部項目再過濾，目前未知。**
若是後者，含數千張照片的 DCIM 子資料夾展開仍可能慢。

→ **階段 2 必須先做 benchmark 量測，不要用猜的。** 見 `02-roadmap.md`。
若量測結果不理想，備案是：對已知的照片資料夾層級改用非同步預取 + 骨架畫面（先畫出節點，內容稍後補）。
