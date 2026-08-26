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

```python
@dataclass(frozen=True)
class FileEntry:
    name: str
    is_dir: bool
    abs_pidl: bytes                  # 絕對 PIDL，純資料，可安全跨執行緒傳遞
    size: int | None = None          # MTP 上取得成本高，允許為 None
    mtime: datetime | None = None

def list_children(folder_abs_pidl, *, want_details=False) -> list[FileEntry]:
    """列舉一層。want_details=True 才呼叫 GetDetailsOf 取 size/mtime（慢）。"""

def expand_folder(folder_abs_pidl, categories) -> list[FileEntry]:
    """回傳資料夾內符合分類的『檔案』（不含子資料夾）。
    v1 在按下備份時呼叫；v2 會改在使用者點開資料夾時呼叫來填檔案清單面板。
    同一支函式，只是呼叫時機不同。"""
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
    有 DCIM 就走 DCIM，沒有就掃根層 —— Apple 再改結構也不用改 code。"""
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

```python
@dataclass
class CopyPlan:
    dest_dir: Path
    items: list[FileEntry]              # 要複製的
    skipped_existing: list[FileEntry]   # 增量備份時已存在而跳過的

@dataclass
class CopyReport:
    copied: list[str]
    failed: list[str]                   # 驗證掃描比對出來的
    aborted: bool                       # GetAnyOperationsAborted()

def plan_copy(items: list[FileEntry], dest_dir: Path, *,
              incremental: bool = True) -> CopyPlan:
    """增量備份：列舉目的地，用『檔名 + 大小』比對，已存在的不排程。
    MTP 很慢，重跑不重傳的差別是分鐘 vs 小時級。"""

def run_copy(plan: CopyPlan, *, owner_hwnd=None,
             chunk_size=200, progress=None) -> CopyReport:
    """分批執行 IFileOperation，每批之後做驗證掃描。
    ★ 只接受 list[FileEntry]，永遠不接受資料夾物件。"""
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
- **右**：目前選取資料夾的摘要（檔案數、各分類數量、預估大小）。v2 在此長出檔案清單。
- **工具列**：重新整理裝置 / 自動尋找照片資料夾 / 全選 / 全不選 / 選擇目的地 / 開始備份。
- **狀態列**：裝置狀態（未偵測到 / 請解鎖並點信任 / 就緒）。
- 複製進度直接用 **Windows 原生進度視窗**（`IFileOperation` 預設就有），
  記得 `pfo.SetOwnerWindow(hwnd)` 掛在主視窗底下，避免變成孤兒視窗。
