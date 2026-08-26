#!/usr/bin/env python
"""階段 2 的裝置測試與效能 benchmark。

用法（在 Windows 上）：
    python tools/smoke_device.py                  # 偵測 + 量測
    python tools/smoke_device.py --copy D:\\backup # 額外實測複製第一個照片資料夾

★ 請跑兩次：一次「拔掉 iPhone」、一次「插著 iPhone」。
  階段 1 量到「列出本機」要 774ms，需要這組對照才知道那是不是
  Shell 為 MTP 裝置開 WPD session 的 warm-up 成本。

本腳本要回答的問題（見 docs/ai/02-roadmap.md 階段 2）：
  1. 列出「本機」有插/沒插 iPhone 差多少？
  2. 展開 iPhone 的資料夾一層要多久？能不能進 0.5 秒？
  3. SHCONTF_FOLDERS 在 MTP 上有沒有真的省到？（A/B 對照）
  4. folder_has_media() 的早退相對於完整列舉省了多少？
  5. 第一次觸碰裝置的 warm-up 成本是多少？
"""

import argparse
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from iphone_backpacker.core import copier, device, listing, shell_ns   # noqa: E402
from iphone_backpacker.core.filters import MEDIA, describe             # noqa: E402
from iphone_backpacker.core.logging_setup import setup_logging         # noqa: E402

log = logging.getLogger("smoke-device")

TARGET_MS = 500.0   # 效能契約：互動操作 0.5 秒內要有反應


def timed(label, fn, target=None):
    start = time.perf_counter()
    result = fn()
    ms = (time.perf_counter() - start) * 1000
    mark = ""
    if target is not None:
        mark = "  <= 目標 {:.0f}ms" .format(target) if ms <= target \
            else "  ★ 超過目標 {:.0f}ms".format(target)
    log.info("%-38s %8.1f ms%s", label, ms, mark)
    return result, ms


def bench_enum_flags(abs_pidl, name):
    """A/B 對照：只列資料夾 vs 全部列。

    這是整個效能契約最關鍵的未知數 ——
    如果 MTP 的 shell extension 內部仍然走訪全部項目再過濾，
    那 SHCONTF_FOLDERS 就省不到，我們得改用骨架畫面 + 非同步預取。
    """
    log.info("")
    log.info("--- 列舉旗標 A/B 對照：%s ---", name)

    folders, ms_folders = timed(
        "SHCONTF_FOLDERS（只要資料夾）",
        lambda: list(shell_ns.iter_entries(abs_pidl, flags=shell_ns.FOLDERS_ONLY)),
    )
    everything, ms_all = timed(
        "FOLDERS|NONFOLDERS（全部）",
        lambda: list(shell_ns.iter_entries(abs_pidl, flags=shell_ns.EVERYTHING)),
    )
    files_only, ms_files = timed(
        "SHCONTF_NONFOLDERS（只要檔案）",
        lambda: list(shell_ns.iter_entries(abs_pidl, flags=shell_ns.FILES_ONLY)),
    )

    log.info("資料夾 %d 個 / 全部 %d 項 / 檔案 %d 個",
             len(folders), len(everything), len(files_only))
    if ms_all > 0:
        ratio = ms_folders / ms_all
        log.info("結論：只列資料夾花了全部列舉的 %.0f%% 的時間", ratio * 100)
        if ratio > 0.7 and len(everything) > len(folders) * 3:
            log.warning("★ SHCONTF_FOLDERS 幾乎沒省到 —— "
                        "MTP shell extension 很可能內部仍走訪全部項目。")
            log.warning("  → 需要啟用備案：骨架畫面 + 非同步預取（見效能契約）")
        else:
            log.info("★ SHCONTF_FOLDERS 有效，效能契約的前提成立。")
    return files_only


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--copy", metavar="DEST",
                        help="額外實測：把找到的第一個照片資料夾複製到 DEST")
    parser.add_argument("--depth", type=int, default=device.DEFAULT_SCAN_DEPTH,
                        help="自動尋找照片資料夾的遞迴深度（預設 %(default)s）")
    args = parser.parse_args()

    setup_logging(level=logging.INFO)

    with shell_ns.com_apartment():
        # ---- 1. 「本機」列舉（有插/沒插 iPhone 的對照組） ----
        log.info("=== 1. 「本機」列舉 ===")
        this_pc, _ = timed("this_pc_pidl()", shell_ns.this_pc_pidl)
        nodes, ms_cold = timed("列出「本機」（首次 / 冷）",
                               lambda: listing.list_subfolders(this_pc), TARGET_MS)
        timed("列出「本機」（再一次 / 熱）",
              lambda: listing.list_subfolders(this_pc), TARGET_MS)
        for entry in nodes:
            log.info("    %s", entry.name)

        # ---- 2. 裝置偵測 ----
        log.info("")
        log.info("=== 2. 裝置偵測 ===")
        devices, _ = timed("find_portable_devices()", device.find_portable_devices)
        if not devices:
            log.info("沒有偵測到可攜式裝置。")
            log.info("★ 這一輪就是「沒插 iPhone」的對照組，"
                     "請記下上面「列出本機（冷）」的 %.1f ms。", ms_cold)
            log.info("   接著插上 iPhone 再跑一次，比較兩個數字。")
            return 0

        dev = devices[0]
        log.info("裝置：%s", dev.name)
        log.info("解析名稱：%s", dev.parsing_name)
        status, _ = timed("probe()", lambda: device.probe(dev))
        log.info("狀態：%s", status.name)
        log.info("訊息：%s", device.status_message(status, dev.name).replace("\n", " / "))
        if status is not device.DeviceStatus.OK:
            log.warning("裝置內容讀不到，後面的量測跳過。"
                        "請解鎖手機並點「信任這部電腦」後重跑。")
            return 1

        # ---- 3. 逐層展開 ----
        log.info("")
        log.info("=== 3. 逐層展開（互動路徑，每一層都要 < %.0f ms） ===", TARGET_MS)
        cache = listing.NamespaceCache()
        node = dev.abs_pidl
        path_labels = [dev.name]
        deepest_with_files = None

        for depth in range(args.depth + 1):
            label = " / ".join(path_labels)
            subs, _ = timed("展開 [{}]".format(label[-34:]),
                            lambda n=node: listing.list_subfolders(n, cache),
                            TARGET_MS)
            if not subs:
                deepest_with_files = node
                break
            node = subs[0].abs_pidl
            path_labels.append(subs[0].name)
        else:
            deepest_with_files = node

        timed("同一層再展開一次（快取命中）",
              lambda: listing.list_subfolders(node, cache), TARGET_MS)

        # ---- 4. 列舉旗標 A/B ----
        if deepest_with_files:
            bench_enum_flags(deepest_with_files, " / ".join(path_labels[-2:]))

            log.info("")
            log.info("--- 早退 vs 完整列舉 ---")
            timed("folder_has_media()（早退）",
                  lambda: listing.folder_has_media(deepest_with_files, MEDIA))
            files, _ = timed("iter_files()（完整列舉）",
                             lambda: list(listing.iter_files(deepest_with_files, MEDIA)))
            log.info("該資料夾符合 %s 的檔案：%d 個", describe(MEDIA), len(files))

        # ---- 5. 自動尋找照片資料夾 ----
        log.info("")
        log.info("=== 5. 自動尋找照片資料夾（背景任務，允許慢） ===")
        folders, _ = timed(
            "find_photo_folders(depth={})".format(args.depth),
            lambda: device.find_photo_folders(dev, MEDIA, max_depth=args.depth,
                                              cache=cache),
        )
        for entry in folders:
            log.info("    %s", entry.name)

        # ---- 6. 選配：實際複製 ----
        if args.copy and folders:
            log.info("")
            log.info("=== 6. 實際複製第一個資料夾 ===")
            plan = copier.plan_copy([folders[0]], args.copy)
            report, _ = timed("run_copy",
                              lambda: copier.run_copy(plan, MEDIA))
            log.info("結果：%s", report.summary())
            for name in report.failed[:20]:
                log.warning("  失敗：%s", name)

        log.info("")
        log.info("★ 請把整段輸出貼回來，這些數字會寫進 docs/ai/04-shell-com-notes.md")
        return 0


if __name__ == "__main__":
    sys.exit(main())
