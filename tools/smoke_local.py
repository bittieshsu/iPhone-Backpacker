#!/usr/bin/env python
"""階段 1 的煙霧測試 —— 在本機資料夾上驗證 core，不需要 iPhone。

用法（在 Windows 上）：
    python tools/smoke_local.py <來源資料夾> <目的地資料夾>

它會依序驗證：
  1. 能不能取得「本機」節點並列出磁碟機（驗證 CSIDL_DRIVES 這條語言中立的路徑）
  2. list_subfolders / folder_has_media / iter_files 是否正常
  3. plan_copy + run_copy 能不能真的把檔案複製過去，並產生正確的報告
  4. 重跑一次是否會因為增量去重而全部跳過
"""

import argparse
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from iphone_backpacker.core import copier, listing, shell_ns          # noqa: E402
from iphone_backpacker.core.filters import ALL, MEDIA, describe       # noqa: E402
from iphone_backpacker.core.listing import FileEntry                  # noqa: E402
from iphone_backpacker.core.logging_setup import setup_logging        # noqa: E402

log = logging.getLogger("smoke")


def timed(label, fn):
    start = time.perf_counter()
    result = fn()
    elapsed = time.perf_counter() - start
    log.info("%-28s %7.1f ms", label, elapsed * 1000)
    return result, elapsed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", help="來源資料夾（本機路徑）")
    parser.add_argument("dest", help="目的地資料夾")
    parser.add_argument("--all", action="store_true",
                        help="複製所有檔案，不只照片/影片")
    args = parser.parse_args()

    setup_logging(level=logging.INFO)
    categories = ALL if args.all else MEDIA

    with shell_ns.com_apartment():
        # 1. 「本機」節點
        this_pc, _ = timed("this_pc_pidl()", shell_ns.this_pc_pidl)
        drives, _ = timed("列出「本機」底下的節點",
                          lambda: listing.list_subfolders(this_pc))
        log.info("本機底下有 %d 個節點：", len(drives))
        for entry in drives:
            log.info("    %s", entry.name)

        # 2. 來源資料夾
        source_path = Path(args.source).resolve()
        source_pidl = shell_ns.pidl_from_path(source_path)
        source = FileEntry(name=source_path.name, is_dir=True, abs_pidl=source_pidl)

        cache = listing.NamespaceCache()
        _, cold = timed("list_subfolders（首次）",
                        lambda: listing.list_subfolders(source_pidl, cache))
        _, warm = timed("list_subfolders（快取）",
                        lambda: listing.list_subfolders(source_pidl, cache))
        log.info("快取加速：%.0fx", (cold / warm) if warm else 0)

        has_media, _ = timed("folder_has_media（早退）",
                             lambda: listing.folder_has_media(source_pidl, categories))
        log.info("來源含有 %s：%s", describe(categories), has_media)

        files, _ = timed("iter_files（完整列舉）",
                         lambda: list(listing.iter_files(source_pidl, categories)))
        log.info("符合條件的檔案：%d 個", len(files))

        # 3. 複製
        plan = copier.plan_copy([source], args.dest)
        report, elapsed = timed(
            "run_copy",
            lambda: copier.run_copy(plan, categories,
                                    progress=lambda p: None),
        )
        log.info("第一次：%s", report.summary())
        if report.failed:
            for name in report.failed[:20]:
                log.warning("  失敗：%s", name)

        # 4. 增量去重
        report2, _ = timed("run_copy（重跑）",
                           lambda: copier.run_copy(plan, categories))
        log.info("第二次：%s", report2.summary())

        ok = (
            len(report.copied) == len(files)
            and not report.failed
            and len(report2.skipped_existing) == len(files)
            and not report2.copied
        )
        log.info("結果：%s", "通過" if ok else "不如預期，請看上面的數字")
        return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
