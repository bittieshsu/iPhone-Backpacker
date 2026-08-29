"""進入點。

負責把 MainWindow 與 ShellWorker 接起來，並管理 worker 執行緒的生命週期。

★ 啟動時**不同步等裝置偵測**（實測總成本約 1.9 秒）。
  視窗先出來，偵測與「本機」列舉都在 worker 執行緒跑，回來再填（決策 D10）。
"""

import logging
import sys
import time

_T0 = time.perf_counter()

from PySide6.QtCore import QThread
from PySide6.QtWidgets import QApplication

from .core.logging_setup import setup_logging
from .ui import dialogs
from .ui.main_window import MainWindow
from .ui.workers import ShellWorker

log = logging.getLogger(__name__)


def _connect(window, worker):
    # 主執行緒 → worker（跨執行緒，Qt 自動用 queued connection）
    window.request_detect.connect(worker.detect_device)
    window.request_roots.connect(worker.load_roots)
    window.request_subfolders.connect(worker.load_subfolders)
    window.request_count.connect(worker.count_files)
    window.request_count_batch.connect(worker.count_files_batch)
    window.request_copy.connect(worker.start_copy)
    window.request_clear_cache.connect(worker.clear_cache)
    window.request_report.connect(worker.build_report)

    # worker → 主執行緒
    worker.device_detected.connect(window.on_device_detected)
    worker.roots_ready.connect(window.populate_roots)
    worker.subfolders_ready.connect(window.on_subfolders_ready)
    worker.subfolders_failed.connect(window.on_subfolders_failed)
    worker.file_count_ready.connect(window.on_file_count_ready)
    worker.file_count_failed.connect(window.on_file_count_failed)
    worker.count_batch_progress.connect(window.on_count_batch_progress)
    worker.copy_progress.connect(window.on_copy_progress)
    worker.copy_finished.connect(window.on_copy_finished)
    worker.copy_failed.connect(window.on_copy_failed)
    worker.report_progress.connect(window.on_report_progress)
    worker.report_ready.connect(window.on_report_ready)
    worker.report_failed.connect(window.on_report_failed)


def main():
    log_path = setup_logging()

    app = QApplication(sys.argv)
    app.setApplicationName("iPhone Backpacker")

    thread = QThread()
    worker = ShellWorker()
    worker.moveToThread(thread)
    # CoInitialize 必須在 worker 自己的執行緒裡呼叫，所以掛在 started 上。
    thread.started.connect(worker.start_up)
    thread.finished.connect(worker.shut_down)

    window = MainWindow()
    _connect(window, worker)
    # 取消必須立刻生效，不能走 signal/slot（會排在正在執行的任務後面）。
    window.set_cancel_hooks(worker.request_cancel, worker.request_cancel_count)

    def shutdown():
        worker.request_cancel()
        thread.quit()
        thread.wait(5000)

    app.aboutToQuit.connect(shutdown)

    thread.start()
    window.show()

    # 首次啟動的引導。放在 show() 之後 —— 主視窗要先出現在後面，
    # 使用者關掉說明就能直接操作。
    dialogs.maybe_show_guide(window)

    # 視窗已經顯示之後才發請求 —— 使用者看到的是「立刻開啟」而不是「卡兩秒」。
    window.request_detect.emit()
    window.request_roots.emit()

    import_ms = getattr(sys.modules["__main__"], "_IMPORT_MS", None)
    if import_ms is not None:
        # 實測（2026-08-27）：import 約 2100~2400 ms，之後的初始化只有約 80 ms。
        # 也就是使用者感受到的啟動時間**幾乎全部**是 Python 直譯器啟動 +
        # PySide6 import，發生在我們任何程式碼之前，開發模式下無法改善。
        # PyInstaller --onedir 打包後會明顯變快（階段 6）。
        total_ms = (time.perf_counter() - _T0) * 1000
        log.info("啟動耗時：import %.0f ms + 我們的初始化 %.0f ms（合計 %.0f ms）",
                 import_ms, max(total_ms - import_ms, 0.0), total_ms)
    log.info("啟動完成，log 檔：%s", log_path)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
