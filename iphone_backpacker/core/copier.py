"""複製作業。

★ 排程單位永遠是「檔案」，永遠不是「資料夾」（決策 D9）。
  整包丟給 shell 遞迴雖然快一點，但失敗時完全拿不到「是哪個檔案失敗」——
  那正是使用者用檔案總管複製整個資料夾時遇到的「不穩定」。
  逐檔排程才換得到失敗清單、重試能力與增量去重。

★★ 一整個備份任務 = 一次 IFileOperation（決策 D12，2026-08-27 修訂）。
  使用者實測過：用 copyShellItem() 逐檔各開一次操作「超級慢」，
  而且 **Windows 的原生進度視窗會反覆彈出**。切成小批次是同一個問題的
  縮小版 —— 每次 PerformOperations() 有約 600 ms 固定開銷，一個 5000 張
  的資料夾切 200 一批就是 25 次視窗 + 15 秒純浪費。

  最初改成「一個資料夾一次操作」，但實測選 5 個資料夾就跳 5 次視窗。
  IFileOperation 允許同一次操作裡每個項目有**不同的目的地**，
  所以整個任務不管幾個資料夾都只需要一次 PerformOperations()。

  流程：
    第 1 段 走過所有來源資料夾，列舉 + 過濾 + 去重
            → 這段由我們自己回報進度（「已找到 N 個」）
    第 2 段 把全部檔案排程進**單一** IFileOperation（每個檔案帶自己的目的地）
            → 原生進度視窗只出現一次
    第 3 段 逐資料夾驗證掃描 → 失敗清單

  第 2 段不需要我們畫進度：IFileOperation 的原生視窗本來就有
  逐檔進度、剩餘時間與取消鈕，品質比自己畫的高。
"""

import logging
import os
from dataclasses import dataclass, field
from enum import Enum, auto
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

# PerformOperations() 在使用者按下取消（或關掉進度視窗）時回這個 HRESULT。
# 0x80270000 = COPYENGINE_E_USER_CANCELLED。
# ★ 這是正常結果，不是錯誤 —— 不能讓它變成 traceback 嚇到使用者。
COPYENGINE_E_USER_CANCELLED = -2144927744


class CopyPhase(Enum):
    LISTING = auto()    # 正在列舉來源檔案（我們自己回報進度）
    COPYING = auto()    # 交給 IFileOperation，原生進度視窗接手
    VERIFYING = auto()  # 驗證掃描


@dataclass
class CopyProgress:
    """回報給 UI 的進度快照。"""

    phase: CopyPhase = CopyPhase.LISTING
    current_folder: str = ""
    listed: int = 0             # 列舉階段已找到、待複製的檔案數
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


def _new_operation(owner_hwnd):
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
    return pfo


def _perform(pfo):
    """執行已排程的操作。回傳 True 表示使用者取消。

    ★ 使用者按取消、或直接關掉原生進度視窗時，PerformOperations() 會拋
      COPYENGINE_E_USER_CANCELLED。那是**正常結果**，不是錯誤 ——
      要當成「已取消」處理，不能讓它變成 traceback。
    """
    try:
        pfo.PerformOperations()
    except pythoncom.com_error as exc:
        hresult = exc.args[0] if exc.args else None
        if hresult == COPYENGINE_E_USER_CANCELLED:
            log.info("使用者取消了複製（COPYENGINE_E_USER_CANCELLED）")
            return True
        raise ShellError("複製作業失敗：{}".format(exc)) from exc

    return bool(pfo.GetAnyOperationsAborted())


def run_copy(plan, categories=MEDIA, *, owner_hwnd=None,
             progress=None, cancel=None):
    """執行備份。**必須在 worker thread 且已進入 COM apartment。**

    progress: 可選的 callable，收一個 CopyProgress。
    cancel:   可選的 callable，回傳 True 表示使用者要求取消。
              只在「列舉」階段有效；一旦進入 PerformOperations()，
              取消要靠原生進度視窗上的按鈕。
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

    # ---- 第 1 段：列舉 + 過濾 + 增量去重 ----
    # 這段是我們自己回報進度的地方。MTP 列舉每項約 3.4 ms（跨 session 浮動
    # 可達 3 倍），幾百張就要好幾秒，不回報進度使用者會以為當掉。
    state.phase = CopyPhase.LISTING
    jobs = []   # [(source, dest_sub, [FileEntry, ...]), ...]

    try:
        for source in plan.sources:
            check_cancel()
            dest_sub = plan.dest_dir / source.name
            try:
                dest_sub.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                raise DestinationError(
                    "無法建立「{}」：{}".format(dest_sub, exc)) from exc

            state.current_folder = source.name
            notify()

            existing = _local_index(dest_sub)
            pending = []
            for entry in iter_files(source.abs_pidl, categories):
                check_cancel()
                # 增量去重以檔名為準，刻意不比對大小 ——
                # 取來源大小在 MTP 上要每個項目一次 GetDetailsOf 來回，
                # 成本會抵銷掉增量備份省下的時間。
                # iPhone 的 IMG_xxxx 檔名在同一資料夾內本來就唯一。
                if entry.key in existing:
                    report.skipped_existing.append(entry.name)
                    state.skipped_existing = len(report.skipped_existing)
                    continue
                pending.append(entry)
                state.listed += 1
                if state.listed % 50 == 0:
                    notify()

            log.info("「%s」：待複製 %d 個、跳過 %d 個",
                     source.name, len(pending), len(existing))
            if pending:
                jobs.append((source, dest_sub, pending))
        notify()
    except OperationCancelled:
        report.aborted = True
        log.info("備份在列舉階段被取消")
        return report

    if not jobs:
        log.info("沒有需要複製的檔案（全部已存在）")
        return report

    # ---- 第 2 段：全部排程進單一 IFileOperation ----
    # ★ 每個 CopyItem 可以帶自己的目的地，所以不管幾個資料夾都只要一次操作，
    #   原生進度視窗只彈一次（決策 D12）。
    state.phase = CopyPhase.COPYING
    state.current_folder = "全部"
    notify()

    pfo = _new_operation(owner_hwnd)
    scheduled = 0
    for source, dest_sub, pending in jobs:
        dest_item = shell_ns.item_from_path(dest_sub)
        for entry in pending:
            try:
                pfo.CopyItem(shell_ns.shell_item(entry.abs_pidl), dest_item, None)
                scheduled += 1
            except (pythoncom.com_error, ShellError) as exc:
                # 排程階段就失敗的個別項目不該拖垮整批，
                # 交給第 3 段的驗證掃描去記錄。
                log.warning("排程失敗，略過：%s\\%s（%s）",
                            source.name, entry.name, exc)

    if scheduled == 0:
        # PerformOperations() 在零排程時會回 0x8000FFFF (E_UNEXPECTED)，
        # 也就是舊版使用者遇到的 -2147418113「災難性的失敗」。
        log.warning("沒有任何項目排程成功")
        report.failed.extend(e.name for _, _, p in jobs for e in p)
        return report

    log.info("開始複製：%d 個檔案，來自 %d 個資料夾", scheduled, len(jobs))
    aborted = _perform(pfo)

    # ---- 第 3 段：逐資料夾驗證掃描 ----
    # 取代 IFileOperationProgressSink（決策 D6）。搭配 FOF_NOERRORUI，
    # 失敗的檔案不會跳「是否略過」小視窗，改由這裡比對出來，
    # 使用者才拿得到失敗清單。
    state.phase = CopyPhase.VERIFYING
    notify()
    for source, dest_sub, pending in jobs:
        landed = _local_index(dest_sub)
        for entry in pending:
            if entry.key in landed:
                report.copied.append(entry.name)
            else:
                report.failed.append("{}\\{}".format(source.name, entry.name))
    state.copied = len(report.copied)
    state.failed = len(report.failed)
    report.aborted = aborted
    notify()

    if aborted:
        log.info("備份已取消：%s", report.summary())
    else:
        log.info("備份完成：%s", report.summary())
    return report
