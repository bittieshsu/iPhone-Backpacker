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


class MainWindow(QMainWindow):
    # 送給 worker 的請求（跨執行緒，自動走 queued connection）
    request_detect = Signal()
    request_roots = Signal()
    request_subfolders = Signal(object)
    request_count = Signal(object, object)
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
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
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

        self.btn_check_all = QPushButton("全選")
        self.btn_check_all.setToolTip("勾選目前節點底下所有已載入的資料夾")
        self.btn_check_all.clicked.connect(lambda: self._set_all_checked(True))
        bar.addWidget(self.btn_check_all)

        self.btn_uncheck_all = QPushButton("全不選")
        self.btn_uncheck_all.clicked.connect(lambda: self._set_all_checked(False))
        bar.addWidget(self.btn_uncheck_all)

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

        return bar

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
        self.statusBar().showMessage(
            "{} {} 個資料夾".format("已勾選" if checked else "已取消勾選", count), 4000)

    def _checked_entries(self):
        return [item.data(0, ENTRY_ROLE) for item in self._iter_items()
                if item.checkState(0) == Qt.CheckState.Checked]

    # ------------------------------------------------------------------
    # 來自 worker 的回應
    # ------------------------------------------------------------------

    def on_device_detected(self, status, dev):
        name = dev.name if dev is not None else None
        message = core_device.status_message(status, name)
        self.banner.setText(message.replace("\n", "<br>"))
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
        lines = [report.summary()]
        if report.failed:
            lines.append("")
            lines.append("失敗的檔案（共 {} 個）：".format(len(report.failed)))
            lines.extend("  " + n for n in report.failed[:50])
            if len(report.failed) > 50:
                lines.append("  …（其餘請看 log 檔）")
        box = QMessageBox(self)
        box.setWindowTitle("備份完成" if report.ok else "備份結束，但有問題")
        box.setIcon(QMessageBox.Icon.Information if report.ok
                    else QMessageBox.Icon.Warning)
        box.setText("\n".join(lines))
        box.exec()

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
        self._set_busy(True)
        self.request_copy.emit(entries, self._dest_dir, self._categories,
                               int(self.winId()))

    def _set_busy(self, busy):
        self._copying = busy
        self.progress.setVisible(busy)
        self.progress.setRange(0, 0 if busy else 1)
        for widget in (self.btn_start, self.btn_refresh, self.btn_dest,
                       self.btn_check_all, self.btn_uncheck_all,
                       self.combo_category):
            widget.setEnabled(not busy)

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
