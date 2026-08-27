"""背景執行緒。

★★ 為什麼所有 Shell 操作都必須在這裡（決策 D10）：
  實測裝置偵測總成本約 1.9 秒、展開 Internal Storage（184 個資料夾）
  首次要 2.6 秒。這些在主執行緒跑會直接凍住視窗。

★ COM 的規矩（見 docs/ai/04-shell-com-notes.md）：
  - 每個碰 Shell 的執行緒開頭要 CoInitialize()、結束要 CoUninitialize()
  - IShellFolder / IShellItem **不能跨執行緒傳遞**
  - PIDL 是純資料，**可以**跨執行緒傳遞
  所以主執行緒與 worker 之間只傳 PIDL 與 FileEntry，絕不傳 COM 介面。

★ 只用**一條** worker 執行緒，任務序列執行。
  MTP 本來就不適合並行存取，多開執行緒不會比較快，只會讓錯誤更難查。
"""

import logging
import threading

import pythoncom
from PySide6.QtCore import QObject, Signal, Slot

from ..core import copier, device, listing, shell_ns
from ..core.errors import BackpackerError

log = logging.getLogger(__name__)


class ShellWorker(QObject):
    """所有 Shell 操作的執行者。由 MainWindow 移到獨立的 QThread 裡。"""

    # 裝置偵測
    device_detected = Signal(object, object)        # DeviceStatus, Device | None

    # 樹狀展開
    roots_ready = Signal(object)                    # list[FileEntry]（「本機」底下）
    subfolders_ready = Signal(object, object)       # parent_pidl, list[FileEntry]
    subfolders_failed = Signal(object, str)         # parent_pidl, message

    # 資料夾摘要（背景算，永不阻塞選取）
    file_count_ready = Signal(object, int)          # folder_pidl, count

    # 複製
    copy_progress = Signal(object)                  # CopyProgress
    copy_finished = Signal(object)                  # CopyReport
    copy_failed = Signal(str)

    def __init__(self):
        super().__init__()
        self.cache = listing.NamespaceCache()
        self._cancel = threading.Event()

    # ---- 執行緒生命週期 ----

    @Slot()
    def start_up(self):
        pythoncom.CoInitialize()
        log.info("worker 執行緒已進入 COM apartment")

    @Slot()
    def shut_down(self):
        pythoncom.CoUninitialize()
        log.info("worker 執行緒已離開 COM apartment")

    def request_cancel(self):
        """由主執行緒呼叫。threading.Event 本身就是執行緒安全的。"""
        self._cancel.set()

    # ---- 任務 ----

    @Slot()
    def detect_device(self):
        try:
            status, dev = device.detect()
        except BackpackerError as exc:
            log.exception("裝置偵測失敗")
            status, dev = device.DeviceStatus.NOT_FOUND, None
            log.error("%s", exc)
        self.device_detected.emit(status, dev)

    @Slot()
    def load_roots(self):
        """載入「本機」底下的節點。

        磁碟機與 iPhone 都在這一層，所以樹只要一個根就同時涵蓋兩者，
        不需要為裝置另外做一棵樹。

        實測這一步要 300~800 ms（跟有沒有插 iPhone 無關，是本機列舉
        本身的成本），所以一定要在這裡而不是主執行緒。
        """
        try:
            entries = listing.list_subfolders(shell_ns.this_pc_pidl(), self.cache)
        except BackpackerError as exc:
            log.exception("載入「本機」失敗")
            self.subfolders_failed.emit(None, str(exc))
            return
        self.roots_ready.emit(entries)

    @Slot(object)
    def load_subfolders(self, parent_pidl):
        """展開一層。★ 只列資料夾，不取 details、不取縮圖。"""
        try:
            entries = listing.list_subfolders(parent_pidl, self.cache)
        except BackpackerError as exc:
            log.warning("展開失敗：%s", exc)
            self.subfolders_failed.emit(parent_pidl, str(exc))
            return
        self.subfolders_ready.emit(parent_pidl, entries)

    @Slot(object, object)
    def count_files(self, folder_pidl, categories):
        """算資料夾裡的檔案數。

        ★ 這是**慢**操作（~3.4 ms/項），只在使用者停在某個節點時才做，
          而且算不完也不影響勾選與備份（效能契約 D8）。
        """
        try:
            count = sum(1 for _ in listing.iter_files(folder_pidl, categories))
        except BackpackerError as exc:
            log.debug("計算檔案數失敗：%s", exc)
            return
        self.file_count_ready.emit(folder_pidl, count)

    @Slot(object, str, object, int)
    def start_copy(self, sources, dest_dir, categories, owner_hwnd):
        self._cancel.clear()
        try:
            plan = copier.plan_copy(sources, dest_dir)
            report = copier.run_copy(
                plan, categories,
                owner_hwnd=owner_hwnd or None,
                progress=self.copy_progress.emit,
                cancel=self._cancel.is_set,
            )
        except BackpackerError as exc:
            log.exception("備份失敗")
            self.copy_failed.emit(str(exc))
            return
        self.copy_finished.emit(report)

    @Slot()
    def clear_cache(self):
        """重新整理裝置時呼叫。MTP 偶爾回傳不完整清單，重列一次就好。"""
        self.cache.invalidate()
        log.info("命名空間快取已清空")
