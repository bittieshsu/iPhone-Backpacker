"""產生診斷報告。

★ 為什麼要有這個：會遇到問題的人，正是最不可能開終端機跑 Python 的人。
  報告必須能從 GUI 按一個按鈕產生，存成一個他們找得到、看得懂、
  可以直接傳給開發者的 .txt 檔。

★ 這個模組沒有 Qt，GUI 與命令列工具共用同一份邏輯。

★ **每一段都獨立包在 try/except 裡。** 報告的目的就是在出問題的時候用，
  所以任何一段失敗都不能讓整份報告產不出來 —— 失敗本身就是有價值的資訊，
  要寫進報告而不是讓它中斷。
"""

import logging
import platform
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

from win32com.shell import shell, shellcon

from . import device, listing, shell_ns
from .filters import MEDIA, categorize
from .logging_setup import default_log_dir

log = logging.getLogger(__name__)

LOG_TAIL_LINES = 300


def default_report_dir():
    """報告要存在使用者一定找得到的地方 —— 桌面。

    用 CSIDL_DESKTOPDIRECTORY 而不是 `Path.home() / "Desktop"`，
    因為桌面可能被 OneDrive 或群組原則重新導向到別的位置。
    """
    try:
        return Path(shell.SHGetFolderPath(0, shellcon.CSIDL_DESKTOPDIRECTORY, 0, 0))
    except Exception:   # noqa: BLE001 - 取不到就退回家目錄，不能因此產不出報告
        return Path.home()


def default_report_path():
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return default_report_dir() / "iPhoneBackpacker-診斷報告-{}.txt".format(stamp)


class _Report:
    """收集報告內容。每一段都不會因為例外而中斷整份報告。"""

    def __init__(self, progress=None):
        self.lines = []
        self._progress = progress

    def say(self, text=""):
        self.lines.append(text)

    def title(self, text):
        self.say()
        self.say("=" * 64)
        self.say(text)
        self.say("=" * 64)
        if self._progress is not None:
            self._progress(text)

    def section(self, heading, func):
        """跑一段檢查。失敗就把錯誤寫進報告，繼續下一段。"""
        self.title(heading)
        try:
            func()
        except Exception as exc:   # noqa: BLE001 - 報告本來就是給出問題時用的
            self.say("!! 這一段檢查失敗：{}".format(exc))
            self.say("!! （這件事本身就是線索，請連同這份報告一起回報）")
            log.exception("診斷段落失敗：%s", heading)

    def text(self):
        return "\n".join(self.lines) + "\n"


def _describe_environment(report):
    from .. import __version__

    report.say("產生時間：{}".format(datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    report.say("程式版本：{}".format(__version__))
    report.say("執行方式：{}".format(
        "打包後的 exe" if getattr(sys, "frozen", False) else "從原始碼執行"))
    report.say("作業系統：{}".format(platform.platform()))
    report.say("Python  ：{}".format(sys.version.replace("\n", " ")))


def _describe_this_pc(report):
    """「本機」底下有什麼，以及裝置判斷怎麼決定的。"""
    report.say("這一段用來回答：為什麼顯示「沒有偵測到 iPhone」。")
    report.say()

    this_pc = shell_ns.this_pc_pidl()
    for child_abs, name, attrs in shell_ns.iter_entries(
        this_pc, flags=shell_ns.EVERYTHING, want_attributes=True
    ):
        try:
            parsing = shell_ns.parsing_name(child_abs)
            parsing_failed = False
        except Exception as exc:   # noqa: BLE001
            parsing, parsing_failed = "（取不到：{}）".format(exc), True

        verdict, reason = device._classify("" if parsing_failed else parsing, attrs)
        report.say("  {}".format(name))
        report.say("      attrs   = {}".format(
            "None（讀不到）" if attrs is None else "0x{:08X}".format(attrs)))
        report.say("      parsing = {}".format(parsing or "（空字串）"))
        report.say("      判定    = {}（{}）".format(
            "★ 是裝置" if verdict else "不是裝置", reason))


def _describe_device(report):
    devices = device.find_portable_devices()
    if not devices:
        report.say("!! 沒有偵測到任何可攜式裝置。")
        report.say("!! 上面那張表就是原因 —— 請連同這份報告回報。")
        return None

    dev = devices[0]
    report.say("裝置名稱：{}".format(dev.name))
    report.say("解析名稱：{}".format(dev.parsing_name or "（取不到，這對 MTP 裝置是正常的）"))
    if len(devices) > 1:
        report.say("（另外還偵測到 {} 個裝置，程式目前只處理第一個）".format(
            len(devices) - 1))

    status = device.probe(dev)
    report.say("狀態：{}".format(status.name))
    report.say()
    for line in device.status_message(status, dev.name).splitlines():
        report.say("  " + line.replace("**", ""))
    return dev if status is device.DeviceStatus.OK else None


def _find_richest_level(dev):
    """往下找到子資料夾最多的那一層。對 iPhone 就是 Internal Storage。"""
    node, label = dev.abs_pidl, dev.name
    best = (node, label, [])
    for _ in range(3):
        subs = listing.list_subfolders(node)
        if len(subs) > len(best[2]):
            best = (node, label, subs)
        if not subs:
            break
        node, label = subs[0].abs_pidl, subs[0].name
    return best


def _describe_folders(report, entries, label):
    report.say("[{}] 底下共 {} 個子資料夾：".format(label, len(entries)))
    report.say()
    for entry in entries:
        report.say("    {}".format(entry.name))

    suffixes = Counter(e.name[-2:] if len(e.name) >= 2 else e.name for e in entries)
    report.say()
    report.say("後綴統計：")
    for suffix, count in sorted(suffixes.items()):
        report.say("    結尾「{}」：{} 個".format(suffix, count))

    double = [e.name for e in entries if e.name.endswith("__")]
    report.say()
    if double:
        report.say("有 {} 個「__」結尾的資料夾，例如：{}".format(
            len(double), "、".join(double[:5])))
    else:
        report.say("!! 一個「__」結尾的資料夾都沒有。")
        report.say("!! 請打開 Windows 檔案總管進到同一層比對：")
        report.say("!!   - 檔案總管也沒有 → 這支手機本來就是這樣命名，不是程式的問題")
        report.say("!!   - 檔案總管有、這裡沒有 → 是程式漏了，請務必回報")


def _describe_one_folder(report, folder):
    """某個資料夾到底有沒有東西。用三種旗標交叉比對。"""
    report.say("這一段用來回答：某個資料夾顯示 0 個檔案，是真的空的還是沒讀到。")
    report.say()

    counts = {}
    for flags, label in ((shell_ns.FILES_ONLY, "只列檔案"),
                         (shell_ns.FOLDERS_ONLY, "只列資料夾"),
                         (shell_ns.EVERYTHING, "全部")):
        try:
            items = list(shell_ns.iter_entries(folder.abs_pidl, flags=flags))
            counts[label] = len(items)
            report.say("  {:<12} {} 項".format(label, len(items)))
        except Exception as exc:   # noqa: BLE001
            counts[label] = None
            report.say("  {:<12} 讀取失敗：{}".format(label, exc))

    report.say()
    if None in counts.values():
        report.say("!! 有列舉失敗 —— 顯示 0 個檔案很可能是讀取問題，不是真的空的。")
        return

    if counts["只列檔案"] + counts["只列資料夾"] != counts["全部"]:
        report.say("!! 數字對不起來：{} + {} != {}".format(
            counts["只列檔案"], counts["只列資料夾"], counts["全部"]))
        report.say("!! 這代表列舉不穩定，顯示的數量不可信。")

    if counts["全部"] == 0:
        report.say("!! 這個資料夾三種列舉都是 0 項，看起來是真的空的。")
        report.say("!! 如果用檔案總管進去看得到照片，請務必回報。")
        return

    names = [n for _, n, _ in shell_ns.iter_entries(
        folder.abs_pidl, flags=shell_ns.EVERYTHING)]
    report.say()
    report.say("前 30 個項目與分類：")
    for name in names[:30]:
        report.say("    {:<34} {}".format(name, categorize(name).name))
    if len(names) > 30:
        report.say("    …（其餘 {} 項省略）".format(len(names) - 30))

    media = [n for n in names if categorize(n) & MEDIA]
    report.say()
    report.say("符合「照片 + 影片」的：{} 個（程式會備份的就是這些）".format(len(media)))
    if names and not media:
        report.say("!! 有檔案但沒有一個算照片或影片 —— "
                   "副檔名可能是程式沒涵蓋的，請回報上面的清單。")


def _describe_log_tail(report):
    log_path = default_log_dir() / "backpacker.log"
    report.say("紀錄檔：{}".format(log_path))
    report.say()
    if not log_path.exists():
        report.say("（紀錄檔還不存在）")
        return
    with log_path.open(encoding="utf-8", errors="replace") as handle:
        tail = handle.readlines()[-LOG_TAIL_LINES:]
    report.say("最後 {} 行：".format(len(tail)))
    report.say()
    for line in tail:
        report.say("  " + line.rstrip())


def collect_report(focus_folder=None, progress=None):
    """跑完整套診斷，回傳報告全文。

    focus_folder: 可選的 FileEntry，會針對它做逐項檢查。
                  GUI 會把使用者目前選取的資料夾傳進來 ——
                  這樣「看某個可疑資料夾」不需要命令列參數。
    progress:     可選的 callable，收一段文字，用來更新 UI 狀態。
    """
    started = time.perf_counter()
    report = _Report(progress)

    report.title("iPhone Backpacker 診斷報告")
    report.section("執行環境", lambda: _describe_environment(report))
    report.section("一、「本機」底下有什麼", lambda: _describe_this_pc(report))

    dev_holder = {}
    report.section("二、裝置偵測",
                   lambda: dev_holder.update(dev=_describe_device(report)))
    dev = dev_holder.get("dev")

    if dev is not None:
        level = {}
        report.section(
            "三、照片資料夾清單",
            lambda: (level.update(zip(("pidl", "label", "entries"),
                                      _find_richest_level(dev))),
                     _describe_folders(report, level["entries"], level["label"])))

        if focus_folder is not None:
            report.section("四、[{}] 逐項檢查".format(focus_folder.name),
                           lambda: _describe_one_folder(report, focus_folder))
        else:
            report.title("四、逐項檢查")
            report.say("（沒有指定資料夾。若某個資料夾的檔案數看起來不對，")
            report.say("　請在程式裡點選那個資料夾，再按一次「產生診斷報告」。）")

    report.section("五、紀錄檔內容", lambda: _describe_log_tail(report))

    report.title("報告結束")
    report.say("耗時 {:.1f} 秒。".format(time.perf_counter() - started))
    report.say("請把這個檔案整份傳給開發者。")
    return report.text()


def write_report(text, path=None):
    """把報告寫成 .txt。刻意不用 .log —— 使用者要找得到、打得開、傳得出去。"""
    path = Path(path) if path else default_report_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # Windows 記事本對沒有 BOM 的 UTF-8 中文會顯示成亂碼，所以用 utf-8-sig。
    path.write_text(text, encoding="utf-8-sig")
    log.info("診斷報告已寫入：%s", path)
    return path
