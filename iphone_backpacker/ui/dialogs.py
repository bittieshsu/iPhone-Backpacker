"""對話框。

目前只有首次啟動的引導。
"""

import logging

from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QLabel, QVBoxLayout,
)

log = logging.getLogger(__name__)

_ORG = "iPhoneBackpacker"
_APP = "iPhoneBackpacker"
_KEY_SEEN_GUIDE = "ui/seen_first_run_guide"

GUIDE_HTML = """
<h2>開始之前，先確認 iPhone 的一個設定</h2>

<p>iPhone 上：<b>設定 → App → 照片 → 傳送到 Mac 或 PC</b>，有兩個選項：</p>

<table cellpadding="6" style="border-collapse:collapse">
  <tr>
    <td style="background:#e8f5e9"><b>自動</b></td>
    <td>手機會即時轉檔，電腦收到的是 <b>.jpg</b> 和 <b>.mov</b>。<br>
        相容性最好，什麼軟體都打得開，但<b>傳輸明顯比較慢</b>，
        <b>備份影片時特別明顯</b>。</td>
  </tr>
  <tr>
    <td style="background:#e3f2fd"><b>保留原始檔</b></td>
    <td>直接給原檔，也就是 <b>.heic</b> / <b>.heif</b> / <b>.mov</b>。<br>
        傳輸快、畫質原汁原味，但 .heic 在舊版 Windows 或某些看圖軟體打不開。</td>
  </tr>
</table>

<p style="background:#fff8e1; padding:8px; border:1px solid #ffe082">
<b>⚠ 改了這個設定之後，一定要把 USB 線拔掉重插才會生效。</b><br>
不重插的話，你改的設定不會反映在電腦看到的檔案上。
</p>

<p style="background:#e3f2fd; padding:8px; border:1px solid #90caf9">
<b>💡 要備份影片，建議選「保留原始檔」。</b><br>
用「自動」時，手機要先把整支影片轉檔完才有資料可以傳，
這段期間 Windows 的複製視窗會顯示<b>速度 0 位元組</b>，看起來像卡住。
那不是當掉，但一支大影片可能要等好幾分鐘。<br>
真的需要 .jpg 的話，在電腦上轉檔比讓手機轉快得多。
</p>

<h3>接上 iPhone 之後</h3>
<ol>
  <li>解鎖手機，並在手機畫面上點<b>「信任這部電腦」</b>。</li>
  <li>點了信任之後，<b>還要再等一到兩分鐘</b>（有時候更久），
      電腦才看得到照片資料夾。這段期間用檔案總管看也是空的，
      是正常現象。</li>
  <li>等不到的話，按工具列的<b>「重新整理裝置」</b>再試一次。</li>
</ol>

<h3>操作方式</h3>
<ul>
  <li>左邊展開 <b>Apple iPhone → Internal Storage</b>，裡面就是照片資料夾。</li>
  <li>勾選要備份的資料夾。點一個資料夾再按<b>「全選」</b>，
      可以一次勾選它底下全部。</li>
  <li>想知道每個資料夾有幾張照片，框選或 Ctrl 複選多個之後按<b>「計算檔案數」</b>。</li>
  <li>選好目的地，按<b>「開始備份」</b>。</li>
  <li><b>已經備份過的檔案會自動跳過</b>，所以重複執行不會重傳，
      中途取消也可以直接再按一次接續。</li>
</ul>
"""


class FirstRunGuide(QDialog):
    """首次啟動的引導。使用者勾「不要再顯示」之後就不再出現。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("使用說明")
        self.resize(620, 640)

        layout = QVBoxLayout(self)

        label = QLabel(GUIDE_HTML)
        label.setWordWrap(True)
        label.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(label)

        self.dont_show = QCheckBox("我知道了，下次不要再顯示")
        layout.addWidget(self.dont_show)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)


def should_show_guide():
    settings = QSettings(_ORG, _APP)
    return not settings.value(_KEY_SEEN_GUIDE, False, type=bool)


def maybe_show_guide(parent=None):
    """首次啟動時顯示引導。回傳是否真的顯示了。"""
    if not should_show_guide():
        return False
    dialog = FirstRunGuide(parent)
    dialog.exec()
    if dialog.dont_show.isChecked():
        QSettings(_ORG, _APP).setValue(_KEY_SEEN_GUIDE, True)
        log.info("使用者選擇不再顯示首次啟動引導")
    return True


def show_guide(parent=None):
    """從「使用說明」按鈕叫出來，不受「不再顯示」影響。"""
    FirstRunGuide(parent).exec()
