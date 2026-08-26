"""資料夾與檔案列舉。

★★ 這個模組刻意分成兩組 API，理由見 docs/ai/01-architecture.md 的「效能契約」：

  瀏覽路徑（互動中呼叫，目標 < 0.5 秒）
      list_subfolders()   只列舉資料夾，不取 details、不取縮圖，結果進快取
      folder_has_media()  找到第一個符合的檔案就早退，不要數完

  複製路徑（背景執行緒呼叫，允許慢）
      iter_files()        generator，讓 copier 邊列舉邊排程

檔案總管在 MTP 上慢的主因是「每張照片都要抓縮圖」與「每個項目一次 GetDetailsOf」。
我們兩件都不做，這是本工具能比檔案總管快的根本原因。不要把它們加回來。
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Tuple

from . import shell_ns
from .filters import MEDIA, matches

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class FileEntry:
    """命名空間裡的一個項目。

    abs_pidl 是純資料，可以安全丟給 worker thread（COM 介面不行）。

    size / mtime 對來源（iPhone）一律是 None —— 在 MTP 上取這兩個值要
    每個項目一次來回，成本高到會毀掉整個體驗。只有本機端的項目才會填。
    v2 的檔案清單面板需要這兩個欄位，所以先留著。
    """

    name: str
    is_dir: bool
    abs_pidl: Tuple[bytes, ...]
    size: Optional[int] = None
    mtime: Optional[datetime] = None

    @property
    def key(self):
        """比對用的正規化檔名。Windows 檔名不分大小寫。"""
        return self.name.lower()


class NamespaceCache:
    """快取 list_subfolders() 的結果。

    存活範圍是一次 session；使用者按「重新整理裝置」才整個清空。
    效果：同一個樹狀節點收合再展開是零成本的。
    """

    def __init__(self):
        self._folders = {}

    def get_subfolders(self, abs_pidl):
        return self._folders.get(tuple(abs_pidl))

    def put_subfolders(self, abs_pidl, entries):
        self._folders[tuple(abs_pidl)] = entries

    def invalidate(self, abs_pidl=None):
        """abs_pidl 為 None 表示全部清空（重新插拔裝置後應該這樣做）。"""
        if abs_pidl is None:
            self._folders.clear()
        else:
            self._folders.pop(tuple(abs_pidl), None)

    def __len__(self):
        return len(self._folders)


# --------------------------------------------------------------------------
# 瀏覽路徑
# --------------------------------------------------------------------------

def list_subfolders(abs_pidl, cache=None):
    """只列舉子資料夾。樹狀節點展開時唯一該呼叫的東西。

    ★ 不列舉檔案、不取 details、不取縮圖。
    """
    if cache is not None:
        cached = cache.get_subfolders(abs_pidl)
        if cached is not None:
            return cached

    entries = [
        FileEntry(name=name, is_dir=True, abs_pidl=child_abs)
        for child_abs, name, _ in shell_ns.iter_entries(
            abs_pidl, flags=shell_ns.FOLDERS_ONLY
        )
    ]
    entries.sort(key=lambda e: e.key)

    if cache is not None:
        cache.put_subfolders(abs_pidl, entries)
    return entries


def folder_has_media(abs_pidl, categories=MEDIA, probe_limit=200):
    """這個資料夾裡有沒有目標類型的檔案？

    ★ 找到第一個就立刻回 True，絕不數完 ——
      否則「自動尋找照片資料夾」會變成掃描整支手機。
    probe_limit 是保險上限，避免在超大資料夾裡白跑。
    """
    seen = 0
    for _, name, _ in shell_ns.iter_entries(abs_pidl, flags=shell_ns.FILES_ONLY):
        if matches(name, categories):
            return True
        seen += 1
        if seen >= probe_limit:
            log.debug("folder_has_media 達到 probe_limit=%d，視為沒有媒體檔", probe_limit)
            return False
    return False


def is_empty(abs_pidl):
    """節點下有沒有任何東西。

    用來偵測「iPhone 沒解鎖 / 沒點信任」—— 那種情況 Shell 會把資料夾
    列舉成空的而不是回報錯誤，不主動判斷的話使用者只會看到一片空白。
    """
    for _ in shell_ns.iter_entries(abs_pidl, flags=shell_ns.EVERYTHING):
        return False
    return True


# --------------------------------------------------------------------------
# 複製路徑
# --------------------------------------------------------------------------

def iter_files(abs_pidl, categories=MEDIA):
    """yield 資料夾內符合分類的檔案（不含子資料夾）。

    ★ 回傳 generator 而非 list：讓 copier 邊列舉邊排程，
      使用者立刻看到進度，不會先卡一段無聲的列舉期。

    v1 在按下「開始備份」之後才呼叫；
    v2 會改在使用者點開資料夾時呼叫來填檔案清單面板。同一支函式，只是時機不同。
    """
    for child_abs, name, _ in shell_ns.iter_entries(
        abs_pidl, flags=shell_ns.FILES_ONLY
    ):
        if matches(name, categories):
            yield FileEntry(name=name, is_dir=False, abs_pidl=child_abs)
