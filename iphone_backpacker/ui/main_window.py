"""主視窗。

★ 絕對不要用 QFileSystemModel —— 它只認真實檔案系統路徑，看不到 iPhone。
  這裡用 QTreeWidget，每個節點把絕對 PIDL 塞在 UserRole 裡。

★ 效能契約（決策 D8 / D10）：
  - 展開一律非同步，先插「載入中…」骨架節點，資料回來再換掉
  - 點選節點時**什麼都不算**，立刻回應
  - 檔案數這種要列舉檔案才知道的資訊，背景算、算不完也不擋操作
  實測：展開 Internal Storage（184 項）首次要 2.6 秒，同步做會凍住視窗。
"""

import logging
import re
import time

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QFileDialog, QHBoxLayout, QLabel,
    QMainWindow, QMessageBox, QProgressBar, QPushButton, QSplitter,
    QTextEdit, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from ..core import device as core_device
from ..core import filters
from ..core.copier import CopyPhase

log = logging.getLogger(__name__)

PIDL_ROLE = Qt.ItemDataRole.UserRole
ENTRY_ROLE = Qt.ItemDataRole.UserRole + 1
LOADED_ROLE = Qt.ItemDataRole.UserRole + 2

COUNT_DEBOUNCE_MS = 400     # 停在同一個節點這麼久才去算檔案數

CATEGORY_CHOICES = [
    ("照片 + 影片", filters.MEDIA),
    ("只有照片", filters.Category.IMAGE),
    ("只有影片", filters.Category.VIDEO),
    ("全部檔案", filters.ALL),
]

HINT_TEXT = (
    "<b>提示：</b>iPhone 的「設定 → App → 相簿 → 傳送到 Mac 或 PC」有兩種模式。"
    "<b>自動</b> 會由手機即時轉檔成 .jpg / .mov（傳輸較慢）；"
    "<b>保留原始檔</b> 直接給 .heic / .heif / .mov。"
    "<b>改完設定要把 USB 線拔掉重插才會生效。</b>"
)


def _format_duration(seconds):
    """把秒數講成人看得懂的話。"""
    seconds = max(int(seconds), 1)
    if seconds < 60:
        return "約 {} 秒".format(seconds)
    minutes, rest = divmod(seconds, 60)
    if minutes < 60:
        return "約 {} 分 {} 秒".format(minutes, rest)
    hours, minutes = divmod(minutes, 60)
    return "約 {} 小時 {} 分".format(hours, minutes)


class MainWindow(QMainWindow):
    # 送給 worker 的請求（跨執行緒，自動走 queued connection）
    request_detect = Signal()
    request_roots = Signal()
    request_subfolders = Signal(object)
    request_count = Signal(object, object)
    request_count_batch = Signal(object, object)
    request_copy = Signal(object, str, object, int)
    request_clear_cache = Signal()

    def __init__(self):
        super().__init__()
        self.setWindowTitle("iPhone Backpacker")
        self.resize(980, 640)

        self._dest_dir = ""
        self._file_counts = {}          # pidl -> 檔案數
        self._pending_count = None
        self._copying = False
        self._cancel_copy = None        # 由 app.py 注入 worker.request_cancel
        self._cancel_count = None       # 由 app.py 注入 worker.request_cancel_count
        self._last_job = None           # (entries, dest, categories)，供「重試」用

        # 計算一個資料夾要多久。初值保守，之後用實測值自我修正 ——
        # 成本是「70ms 開場 + 3.4ms × 檔案數」，所以照片多的資料夾差很多。
        # 實測 112 個資料夾跑了 4 分 17 秒完成 57 個，約 4.5 秒/個。
        self._count_seconds_per_folder = 4.0
        self._count_started_at = None

        self._count_timer = QTimer(self)
        self._count_timer.setSingleShot(True)
        self._count_timer.setInterval(COUNT_DEBOUNCE_MS)
        self._count_timer.timeout.connect(self._request_count_for_current)

        self._build_ui()

    # ------------------------------------------------------------------
    # 介面組裝
    # ------------------------------------------------------------------

    def _build_ui(self):
        central = QWidget()
        layout = QVBoxLayout(central)

        hint = QLabel(HINT_TEXT)
        hint.setWordWrap(True)
        hint.setStyleSheet("padding:8px; background:#fff8e1; border:1px solid #ffe082;")
        layout.addWidget(hint)

        self.banner = QLabel("正在偵測裝置…")
        self.banner.setWordWrap(True)
        self.banner.setStyleSheet("padding:8px; background:#e3f2fd; border:1px solid #90caf9;")
        layout.addWidget(self.banner)

        layout.addLayout(self._build_toolbar())

        splitter = QSplitter(Qt.Orientation.Horizontal)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["資料夾", "檔案數"])
        self.tree.setColumnWidth(0, 420)
        # ★ 多選：使用者可以用滑鼠拖曳框選、或 Ctrl / Shift 複選，
        #   再按「計算檔案數」一次算一批。184 個資料夾一個一個點不切實際。
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.itemExpanded.connect(self._on_item_expanded)
        self.tree.currentItemChanged.connect(self._on_current_changed)
        self.tree.itemChanged.connect(self._on_item_changed)
        splitter.addWidget(self.tree)

        self.detail = QTextEdit()
        self.detail.setReadOnly(True)
        splitter.addWidget(self.detail)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        layout.addWidget(splitter, 1)

        self.progress = QProgressBar()
        self.progress.setVisible(False)
        layout.addWidget(self.progress)

        self.setCentralWidget(central)
        self.statusBar().showMessage("就緒")
        self._update_detail()

    def _build_toolbar(self):
        bar = QHBoxLayout()

        self.btn_refresh = QPushButton("重新整理裝置")
        self.btn_refresh.clicked.connect(self._on_refresh)
        bar.addWidget(self.btn_refresh)

        # ★ 這兩顆按鈕的作用範圍不是「整棵樹」，而是「目前選取節點的底下」。
        #   使用者回報「全不選」讓人以為會清掉所有勾選，需要明確說明。
        _scope_note = ("\n\n作用範圍：目前選取的資料夾**底下**。\n"
                       "沒有選取任何節點時，才會作用在整棵樹。")

        self.btn_check_all = QPushButton("全選")
        self.btn_check_all.setToolTip(
            "把目前選取資料夾底下、已經載入的子資料夾全部打勾。\n"
            "例：先點 Internal Storage，再按這顆，就會一次勾選它底下"
            "所有日期資料夾。" + _scope_note)
        self.btn_check_all.clicked.connect(lambda: self._set_all_checked(True))
        bar.addWidget(self.btn_check_all)

        self.btn_uncheck_all = QPushButton("全不選")
        self.btn_uncheck_all.setToolTip(
            "把目前選取資料夾底下、已經載入的子資料夾全部取消勾選。\n"
            "注意：**不會**清掉其他地方已經打的勾。" + _scope_note)
        self.btn_uncheck_all.clicked.connect(lambda: self._set_all_checked(False))
        bar.addWidget(self.btn_uncheck_all)

        self.btn_count = QPushButton("計算檔案數")
        self.btn_count.setToolTip(
            "算出選取資料夾裡的檔案數。\n"
            "可以用滑鼠拖曳框選、或按住 Ctrl / Shift 複選多個資料夾。\n"
            "沒有選取時，會計算目前節點底下所有已載入的資料夾。")
        self.btn_count.clicked.connect(self._on_count_selected)
        bar.addWidget(self.btn_count)

        self.btn_stop_count = QPushButton("停止計算")
        self.btn_stop_count.setEnabled(False)
        self.btn_stop_count.clicked.connect(self._on_stop_count)
        bar.addWidget(self.btn_stop_count)

        bar.addSpacing(16)
        bar.addWidget(QLabel("備份類型："))
        self.combo_category = QComboBox()
        for label, _ in CATEGORY_CHOICES:
            self.combo_category.addItem(label)
        self.combo_category.currentIndexChanged.connect(self._on_category_changed)
        bar.addWidget(self.combo_category)

        bar.addStretch(1)

        self.btn_dest = QPushButton("選擇目的地…")
        self.btn_dest.clicked.connect(self._on_choose_dest)
        bar.addWidget(self.btn_dest)

        self.btn_start = QPushButton("開始備份")
        self.btn_start.clicked.connect(self._on_start_copy)
        bar.addWidget(self.btn_start)

        self.btn_cancel = QPushButton("取消備份")
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.clicked.connect(self._on_cancel_copy)
        bar.addWidget(self.btn_cancel)

        return bar

    def set_cancel_hooks(self, cancel_copy, cancel_count):
        """由 app.py 注入 worker 的取消函式。

        ★ 這是唯一直接跨執行緒呼叫 worker 的地方。取消必須立刻生效，
          走 signal/slot 會排在正在執行的任務後面，等於沒有取消功能。
          被呼叫的兩個函式內部只動 threading.Event，是執行緒安全的。
        """
        self._cancel_copy = cancel_copy
        self._cancel_count = cancel_count

    # ------------------------------------------------------------------
    # 樹狀操作
    # ------------------------------------------------------------------

    @property
    def _categories(self):
        return CATEGORY_CHOICES[self.combo_category.currentIndex()][1]

    def _make_item(self, entry, parent=None):
        item = QTreeWidgetItem(parent) if parent else QTreeWidgetItem()
        item.setText(0, entry.name)
        item.setText(1, "—")
        item.setData(0, PIDL_ROLE, entry.abs_pidl)
        item.setData(0, ENTRY_ROLE, entry)
        item.setData(0, LOADED_ROLE, False)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(0, Qt.CheckState.Unchecked)
        # 骨架節點：先給一個假的子項，展開箭頭才會出現。
        # 真的展開時再換成實際內容（若沒有子資料夾就整個移除）。
        placeholder = QTreeWidgetItem(item)
        placeholder.setText(0, "載入中…")
        placeholder.setDisabled(True)
        return item

    def _is_placeholder(self, item):
        return item.childCount() == 1 and item.child(0).isDisabled()

    def _on_item_expanded(self, item):
        if item.data(0, LOADED_ROLE):
            return
        pidl = item.data(0, PIDL_ROLE)
        if pidl is None:
            return
        self.request_subfolders.emit(pidl)

    def _on_current_changed(self, current, _previous):
        self._update_detail()
        # ★ 點選當下什麼都不算，只重設 debounce 計時器。
        #   使用者用方向鍵快速滑過 184 個節點時不會塞爆 worker。
        self._count_timer.stop()
        if current is not None and current.data(0, PIDL_ROLE) is not None:
            if current.data(0, PIDL_ROLE) not in self._file_counts:
                self._count_timer.start()

    def _on_item_changed(self, item, column):
        if column == 0:
            self._update_detail()

    def _request_count_for_current(self):
        item = self.tree.currentItem()
        if item is None:
            return
        pidl = item.data(0, PIDL_ROLE)
        if pidl is None or pidl in self._file_counts:
            return
        item.setText(1, "計算中…")
        self._pending_count = pidl
        self.request_count.emit(pidl, self._categories)

    def _iter_items(self, parent=None):
        """走訪所有已載入的節點。"""
        if parent is None:
            roots = [self.tree.topLevelItem(i)
                     for i in range(self.tree.topLevelItemCount())]
        else:
            roots = [parent.child(i) for i in range(parent.childCount())]
        for item in roots:
            if item.isDisabled():
                continue
            yield item
            yield from self._iter_items(item)

    def _set_all_checked(self, checked):
        """全選 / 全不選。

        有選取節點時只作用在它底下（例如 Internal Storage 的 184 個資料夾），
        沒選取時作用在整棵樹。這是本工具最常用的動作 ——
        使用者的 iPhone 有 184 個日期資料夾，一個一個點不切實際。
        """
        current = self.tree.currentItem()
        scope = current if current is not None and current.childCount() else None
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        count = 0
        self.tree.blockSignals(True)
        for item in self._iter_items(scope):
            item.setCheckState(0, state)
            count += 1
        self.tree.blockSignals(False)
        self._update_detail()
        where = "「{}」底下".format(scope.text(0)) if scope is not None else "整棵樹"
        self.statusBar().showMessage(
            "{} {} 個資料夾（範圍：{}）".format(
                "已勾選" if checked else "已取消勾選", count, where), 6000)

    def _checked_entries(self):
        return [item.data(0, ENTRY_ROLE) for item in self._iter_items()
                if item.checkState(0) == Qt.CheckState.Checked]

    # ------------------------------------------------------------------
    # 來自 worker 的回應
    # ------------------------------------------------------------------

    def on_device_detected(self, status, dev):
        name = dev.name if dev is not None else None
        message = core_device.status_message(status, name)
        # core 用 **粗體** 標記重點（core 不能知道 UI 用什麼格式），這裡轉成 HTML。
        html = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", message).replace("\n", "<br>")
        self.banner.setText(html)
        if status is core_device.DeviceStatus.OK:
            self.banner.setStyleSheet(
                "padding:8px; background:#e8f5e9; border:1px solid #a5d6a7;")
        else:
            self.banner.setStyleSheet(
                "padding:8px; background:#fff3e0; border:1px solid #ffb74d;")
        self.statusBar().showMessage(message.split("\n")[0], 6000)

    def populate_roots(self, entries):
        """填入「本機」底下的節點（磁碟機 + iPhone 都在這裡）。"""
        self.tree.blockSignals(True)
        self.tree.clear()
        for entry in entries:
            self.tree.addTopLevelItem(self._make_item(entry))
        self.tree.blockSignals(False)
        self.statusBar().showMessage("已載入 {} 個節點".format(len(entries)), 4000)

    def on_subfolders_ready(self, parent_pidl, entries):
        item = self._find_item(parent_pidl)
        if item is None:
            return
        self.tree.blockSignals(True)
        item.takeChildren()
        for entry in entries:
            item.addChild(self._make_item(entry))
        item.setData(0, LOADED_ROLE, True)
        self.tree.blockSignals(False)
        if not entries:
            item.setExpanded(False)
        log.debug("展開完成：%s（%d 個子資料夾）", item.text(0), len(entries))

    def on_subfolders_failed(self, parent_pidl, message):
        item = self._find_item(parent_pidl)
        if item is None:
            return
        self.tree.blockSignals(True)
        item.takeChildren()
        item.setData(0, LOADED_ROLE, True)
        self.tree.blockSignals(False)
        self.statusBar().showMessage("展開失敗：{}".format(message), 8000)

    def on_file_count_ready(self, pidl, count):
        self._file_counts[pidl] = count
        item = self._find_item(pidl)
        if item is not None:
            item.setText(1, str(count))
        self._update_detail()

    def on_copy_progress(self, state):
        labels = {
            CopyPhase.LISTING: "讀取檔案清單",
            CopyPhase.COPYING: "複製中（進度請看 Windows 的複製視窗）",
            CopyPhase.VERIFYING: "驗證",
        }
        self.progress.setFormat("{}：{} — 已找到 {}、已複製 {}、跳過 {}、失敗 {}".format(
            state.current_folder, labels.get(state.phase, ""),
            state.listed, state.copied, state.skipped_existing, state.failed))

    def on_copy_finished(self, report):
        self._set_busy(False)
        self.progress.setFormat("")
        lines = [report.summary()]
        if report.aborted:
            lines.append("")
            lines.append("備份被取消了。已經複製完成的檔案會保留，"
                         "下次再備份時會自動跳過，所以直接再按一次"
                         "「開始備份」就能接續。")
        if report.cancelled:
            lines.append("")
            lines.append("「尚未複製」的 {} 個檔案不是失敗 —— "
                         "它們只是還沒輪到就被取消了。".format(len(report.cancelled)))
        if report.failed:
            lines.append("")
            # ★ 實測（2026-08-28）：兩個「複製不了」的檔案重試一次就成功了。
            #   所以這類失敗多半是 MTP 傳輸的暫時性問題，不是檔案壞掉。
            #   不要叫使用者放棄，要叫他重試。
            lines.append("這 {} 個檔案 Windows 這次沒複製成功。\n"
                         "多半是 USB／MTP 傳輸的暫時性問題，"
                         "**按「重試未完成的項目」通常就會成功**：".format(
                             len(report.failed)))
            lines.extend("  " + n for n in report.failed[:50])
            if len(report.failed) > 50:
                lines.append("  …（完整清單在 log 檔裡）")
        box = QMessageBox(self)
        if report.aborted:
            title, icon = "備份已取消", QMessageBox.Icon.Information
        elif report.ok:
            title, icon = "備份完成", QMessageBox.Icon.Information
        else:
            title, icon = "備份結束，但有些檔案失敗", QMessageBox.Icon.Warning
        box.setWindowTitle(title)
        box.setIcon(icon)
        box.setText("\n".join(lines).replace("**", ""))

        # 「重試」不需要特別的機制 —— 增量去重會自動跳過已經複製好的，
        # 所以重跑同一個任務就等於只重試沒完成的那些。
        retry_button = None
        if (report.failed or report.cancelled) and self._last_job is not None:
            retry_button = box.addButton("重試未完成的項目",
                                         QMessageBox.ButtonRole.AcceptRole)
        box.addButton("關閉", QMessageBox.ButtonRole.RejectRole)
        box.exec()

        if retry_button is not None and box.clickedButton() is retry_button:
            entries, dest_dir, categories = self._last_job
            log.info("使用者要求重試未完成的項目")
            self._start_copy_job(entries, dest_dir, categories)

    def on_copy_failed(self, message):
        self._set_busy(False)
        QMessageBox.critical(self, "備份失敗", message)

    # ------------------------------------------------------------------
    # 動作
    # ------------------------------------------------------------------

    def _on_refresh(self):
        """重新整理裝置。

        MTP 偶爾會回傳不完整的清單（Windows 已知毛病），重列一次通常就好。
        真的還是不對就請使用者重新插拔 USB。
        """
        self._file_counts.clear()
        self.request_clear_cache.emit()
        self.banner.setText("正在偵測裝置…")
        self.tree.clear()
        self.request_detect.emit()
        self.request_roots.emit()

    def _on_category_changed(self):
        # 分類改了，之前算的檔案數就不準了。
        self._file_counts.clear()
        for item in self._iter_items():
            item.setText(1, "—")
        self._update_detail()

    def _on_count_selected(self):
        """把選取的資料夾排進批次計算。

        有選取就算選取的，沒選取就算目前節點底下所有已載入的資料夾。
        """
        selected = [i for i in self.tree.selectedItems()
                    if i.data(0, PIDL_ROLE) is not None and not i.isDisabled()]
        if not selected:
            current = self.tree.currentItem()
            scope = current if current is not None and current.childCount() else None
            selected = list(self._iter_items(scope))
        targets = [i for i in selected
                   if i.data(0, PIDL_ROLE) not in self._file_counts]
        if not targets:
            self.statusBar().showMessage("選取的資料夾都已經算過了", 4000)
            return

        if len(targets) > 30:
            estimate = len(targets) * self._count_seconds_per_folder
            answer = QMessageBox.question(
                self, "要計算這麼多嗎？",
                "選取了 {} 個資料夾，估計需要 {}。\n"
                "照片多的資料夾會比較久，實際時間可能差好幾倍。\n"
                "計算期間可以隨時按「停止計算」。\n\n要開始嗎？".format(
                    len(targets), _format_duration(estimate)),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes)
            if answer != QMessageBox.StandardButton.Yes:
                return
        self._count_started_at = time.perf_counter()

        self.tree.blockSignals(True)
        for item in targets:
            item.setText(1, "排隊中…")
        self.tree.blockSignals(False)

        self.btn_stop_count.setEnabled(True)
        self.statusBar().showMessage(
            "開始計算 {} 個資料夾…".format(len(targets)), 4000)
        self.request_count_batch.emit(
            [i.data(0, PIDL_ROLE) for i in targets], self._categories)

    def _on_stop_count(self):
        if self._cancel_count is not None:
            self._cancel_count()
        self.btn_stop_count.setEnabled(False)
        self.statusBar().showMessage("已要求停止計算", 4000)
        self.tree.blockSignals(True)
        for item in self._iter_items():
            if item.text(1) in ("排隊中…", "計算中…"):
                item.setText(1, "—")
        self.tree.blockSignals(False)

    def _on_cancel_copy(self):
        if self._cancel_copy is not None:
            self._cancel_copy()
        self.btn_cancel.setEnabled(False)
        self.progress.setFormat(
            "正在取消…如果 Windows 的複製視窗還開著，請在那裡按取消")

    def on_count_batch_progress(self, done, total):
        remaining_text = ""
        if self._count_started_at is not None and done > 0:
            elapsed = time.perf_counter() - self._count_started_at
            per_folder = elapsed / done
            if done >= 3:
                # 用實測值自我修正，下次估計就會準一些。
                self._count_seconds_per_folder = per_folder
            if done < total:
                remaining_text = "，剩餘約 {}".format(
                    _format_duration(per_folder * (total - done)))

        if done >= total:
            self.btn_stop_count.setEnabled(False)
            self._count_started_at = None
            self.statusBar().showMessage("計算完成（{} 個資料夾）".format(total), 6000)
        else:
            self.statusBar().showMessage(
                "計算中… {} / {}{}".format(done, total, remaining_text))

    def _on_choose_dest(self):
        path = QFileDialog.getExistingDirectory(self, "選擇備份到哪個資料夾")
        if path:
            self._dest_dir = path
            self._update_detail()

    def _on_start_copy(self):
        if self._copying:
            return
        entries = self._checked_entries()
        if not entries:
            QMessageBox.information(self, "還沒選資料夾",
                                    "請先在左邊勾選要備份的資料夾。")
            return
        if not self._dest_dir:
            QMessageBox.information(self, "還沒選目的地",
                                    "請先按「選擇目的地…」指定要備份到哪裡。")
            return
        self._start_copy_job(entries, self._dest_dir, self._categories)

    def _start_copy_job(self, entries, dest_dir, categories):
        self._last_job = (entries, dest_dir, categories)
        self._set_busy(True)
        self.request_copy.emit(entries, dest_dir, categories, int(self.winId()))

    def _set_busy(self, busy):
        self._copying = busy
        self.progress.setVisible(busy)
        self.progress.setRange(0, 0 if busy else 1)
        for widget in (self.btn_start, self.btn_refresh, self.btn_dest,
                       self.btn_check_all, self.btn_uncheck_all,
                       self.btn_count, self.combo_category):
            widget.setEnabled(not busy)
        self.btn_cancel.setEnabled(busy)
        if busy:
            # 只有一條 worker 執行緒，備份期間其他 Shell 操作會排隊。
            # 講清楚，不然看起來像當掉。
            self.progress.setFormat("備份進行中…（期間點選資料夾不會計算檔案數）")
            self.statusBar().showMessage("備份進行中")

    # ------------------------------------------------------------------
    # 雜項
    # ------------------------------------------------------------------

    def _find_item(self, pidl):
        for item in self._iter_items():
            if item.data(0, PIDL_ROLE) == pidl:
                return item
        return None

    def _update_detail(self):
        checked = self._checked_entries()
        known = [self._file_counts.get(e.abs_pidl) for e in checked]
        counted = [n for n in known if n is not None]

        lines = ["<h3>備份設定</h3>",
                 "<p><b>目的地：</b>{}</p>".format(self._dest_dir or "（尚未選擇）"),
                 "<p><b>備份類型：</b>{}</p>".format(
                     filters.describe(self._categories)),
                 "<p><b>已勾選：</b>{} 個資料夾</p>".format(len(checked))]
        if counted:
            more = "" if len(counted) == len(checked) else \
                "（其中 {} 個尚未計算）".format(len(checked) - len(counted))
            lines.append("<p><b>已知檔案數：</b>{} 個{}</p>".format(
                sum(counted), more))

        current = self.tree.currentItem()
        if current is not None:
            pidl = current.data(0, PIDL_ROLE)
            count = self._file_counts.get(pidl)
            lines.append("<hr><h3>{}</h3>".format(current.text(0)))
            lines.append("<p>此資料夾內的檔案數：{}</p>".format(
                count if count is not None else "計算中…"))
            lines.append("<p style='color:#777'>勾選一個資料夾＝備份"
                         "「直接放在它裡面」的檔案，不含子資料夾。</p>")

        if checked:
            lines.append("<hr><p style='color:#777'>備份時會在目的地為每個"
                         "資料夾建立同名子資料夾。已經存在且同名的檔案會自動跳過，"
                         "所以重複執行不會重傳。</p>")
        self.detail.setHtml("".join(lines))
