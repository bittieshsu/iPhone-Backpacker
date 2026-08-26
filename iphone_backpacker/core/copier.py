"""複製作業。

★ 排程單位永遠是「檔案」，永遠不是「資料夾」（決策 D9）。
  整包丟給 shell 遞迴雖然快一點，但失敗時完全拿不到「是哪個檔案失敗」——
  那正是使用者用檔案總管複製整個資料夾時遇到的「不穩定」。
  逐檔排程才換得到失敗清單、重試能力與增量去重。

★ 這是一條 streaming pipeline：
      iter_files → 分類過濾 → 增量去重 → 每 chunk_size 檔送一次 IFileOperation → 驗證掃描
  不要先把全部檔案讀成一個大 list 再開始複製，那會產生一段沒有進度的無聲等待。
"""

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List

import pythoncom
from win32com.shell import shell, shellcon

from . import shell_ns
from .errors import DestinationError, OperationCancelled, ShellError
from .filters import MEDIA, describe
from .listing import FileEntry, iter_files

log = logging.getLogger(__name__)

# FOF_NOCONFIRMATION  所有確認對話框一律回「全部是」
# FOF_NOERRORUI       ★ 關掉「這個檔案無法複製，是否略過？」小視窗，
#                       失敗項目靜默跳過，改由事後的驗證掃描產生失敗清單。
#                       這正是使用者回報「略過後沒有任何 log」的解法。
# 刻意不加 FOF_SILENT —— 那會關掉 Windows 原生的複製進度視窗，
# 而那個視窗的品質比我們自己畫的高。
_OPERATION_FLAGS = shellcon.FOF_NOCONFIRMATION | shellcon.FOF_NOERRORUI

DEFAULT_CHUNK_SIZE = 200


@dataclass
class CopyProgress:
    """回報給 UI 的進度快照。"""

    current_folder: str = ""
    scheduled: int = 0          # 已排程的檔案數
    copied: int = 0             # 驗證確認已落地的檔案數
    skipped_existing: int = 0   # 增量備份跳過的
    failed: int = 0


@dataclass
class CopyPlan:
    """複製計畫。

    ★ 只記「被勾選的資料夾」。檔案清單要到 run_copy() 執行時才展開，
      這樣使用者按下備份之前完全不需要等待列舉（效能契約 D8）。
    """

    dest_dir: Path
    sources: List[FileEntry]

    def __post_init__(self):
        self.dest_dir = Path(self.dest_dir)


@dataclass
class CopyReport:
    copied: List[str] = field(default_factory=list)
    failed: List[str] = field(default_factory=list)
    skipped_existing: List[str] = field(default_factory=list)
    aborted: bool = False

    @property
    def ok(self):
        return not self.failed and not self.aborted

    def summary(self):
        return "複製 {} 個、跳過 {} 個（已存在）、失敗 {} 個{}".format(
            len(self.copied),
            len(self.skipped_existing),
            len(self.failed),
            "，使用者中途取消" if self.aborted else "",
        )


def plan_copy(sources, dest_dir):
    """建立計畫。只做便宜的事：檢查目的地。

    ★ 刻意不在這裡展開來源檔案清單 —— 那是 run_copy() 在背景執行緒才做的事。
    """
    dest_dir = Path(dest_dir)
    if dest_dir.exists() and not dest_dir.is_dir():
        raise DestinationError("目的地不是資料夾：{}".format(dest_dir))
    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise DestinationError("無法建立目的地「{}」：{}".format(dest_dir, exc)) from exc
    if not os.access(dest_dir, os.W_OK):
        raise DestinationError("目的地無法寫入：{}".format(dest_dir))

    sources = [s for s in sources if s.is_dir]
    log.info("計畫：%d 個來源資料夾 → %s", len(sources), dest_dir)
    return CopyPlan(dest_dir=dest_dir, sources=sources)


def _local_index(directory):
    """把本機資料夾建成 {小寫檔名: 大小} 索引。

    目的地是真實檔案系統，用 os.scandir 很便宜 ——
    跟在 MTP 上取來源大小的成本完全不是一個量級。
    """
    index: Dict[str, int] = {}
    if not directory.is_dir():
        return index
    with os.scandir(directory) as it:
        for entry in it:
            if entry.is_file():
                try:
                    index[entry.name.lower()] = entry.stat().st_size
                except OSError:
                    index[entry.name.lower()] = -1
    return index


def _copy_chunk(entries, dest_item, owner_hwnd):
    """把一批檔案送進一次 IFileOperation。回傳 (成功排程數, 是否被中途取消)。"""
    if not entries:
        # ★ PerformOperations() 在零排程時會回 0x8000FFFF (E_UNEXPECTED)，
        #   也就是使用者回報的 -2147418113「災難性的失敗」。必須擋在這裡。
        return 0, False

    try:
        pfo = pythoncom.CoCreateInstance(
            shell.CLSID_FileOperation, None,
            pythoncom.CLSCTX_ALL, shell.IID_IFileOperation,
        )
    except pythoncom.com_error as exc:
        raise ShellError("無法建立 IFileOperation：{}".format(exc)) from exc

    pfo.SetOperationFlags(_OPERATION_FLAGS)
    if owner_hwnd:
        # 把 Windows 原生進度視窗掛在主視窗底下，否則它會變成孤兒視窗。
        pfo.SetOwnerWindow(owner_hwnd)

    scheduled = 0
    for entry in entries:
        try:
            pfo.CopyItem(shell_ns.shell_item(entry.abs_pidl), dest_item, None)
            scheduled += 1
        except (pythoncom.com_error, ShellError) as exc:
            # 排程階段就失敗的個別項目不該拖垮整批，交給驗證掃描去記錄。
            log.warning("排程失敗，略過：%s（%s）", entry.name, exc)

    if scheduled == 0:
        return 0, False

    try:
        pfo.PerformOperations()
    except pythoncom.com_error as exc:
        raise ShellError("複製作業失敗：{}".format(exc)) from exc

    return scheduled, bool(pfo.GetAnyOperationsAborted())


def run_copy(plan, categories=MEDIA, *, owner_hwnd=None,
             chunk_size=DEFAULT_CHUNK_SIZE, progress=None, cancel=None):
    """執行複製。**必須在 worker thread 且已進入 COM apartment。**

    progress: 可選的 callable，收一個 CopyProgress。
    cancel:   可選的 callable，回傳 True 表示使用者要求取消。
    """
    report = CopyReport()
    state = CopyProgress()

    def notify():
        if progress is not None:
            progress(state)

    def check_cancel():
        if cancel is not None and cancel():
            raise OperationCancelled("使用者取消備份")

    log.info("開始備份：%d 個資料夾 → %s（%s）",
             len(plan.sources), plan.dest_dir, describe(categories))

    for source in plan.sources:
        check_cancel()
        dest_sub = plan.dest_dir / source.name
        try:
            dest_sub.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise DestinationError(
                "無法建立「{}」：{}".format(dest_sub, exc)
            ) from exc

        state.current_folder = source.name
        notify()

        existing = _local_index(dest_sub)
        dest_item = shell_ns.item_from_path(dest_sub)
        pending: List[FileEntry] = []

        def flush():
            """送出一批並驗證。"""
            if not pending:
                return False
            check_cancel()
            scheduled, aborted = _copy_chunk(pending, dest_item, owner_hwnd)
            state.scheduled += scheduled

            # 驗證掃描：重新讀一次目的地，比對哪些檔名真的落地了。
            # 這是取代 IFileOperationProgressSink 的作法（決策 D6）——
            # pywin32 對 progress sink 的支援不完整，而這個作法同時
            # 給出失敗清單與「重試失敗項目」的基礎。
            landed = _local_index(dest_sub)
            for item in pending:
                if item.key in landed:
                    report.copied.append(item.name)
                    existing[item.key] = landed[item.key]
                else:
                    report.failed.append(item.name)
                    log.warning("複製失敗：%s\\%s", source.name, item.name)
            state.copied = len(report.copied)
            state.failed = len(report.failed)
            pending.clear()
            notify()
            return aborted

        try:
            for entry in iter_files(source.abs_pidl, categories):
                check_cancel()
                # 增量去重：以檔名為準。
                # 刻意不比對大小 —— 取來源大小在 MTP 上要每個項目一次來回，
                # 成本高到會抵銷掉增量備份省下來的時間。
                # iPhone 的檔名（IMG_xxxx）在同一個資料夾內本來就是唯一的。
                if entry.key in existing:
                    report.skipped_existing.append(entry.name)
                    state.skipped_existing = len(report.skipped_existing)
                    continue
                pending.append(entry)
                if len(pending) >= chunk_size:
                    if flush():
                        report.aborted = True
                        log.info("使用者在原生進度視窗中取消")
                        return report
            if flush():
                report.aborted = True
                return report
        except OperationCancelled:
            report.aborted = True
            log.info("備份已取消")
            return report

    log.info("備份完成：%s", report.summary())
    return report
