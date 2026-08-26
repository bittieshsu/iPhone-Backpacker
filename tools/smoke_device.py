#!/usr/bin/env python
"""階段 2 的裝置測試與效能 benchmark。

用法（在 Windows 上）：
    python tools/smoke_device.py                      # 偵測 + 量測（快，預設不做全裝置掃描）
    python tools/smoke_device.py --probe-folders 5    # 對前 5 個照片資料夾做早退量測
    python tools/smoke_device.py --scan               # 額外測 find_photo_folders（慢，有進度與上限）
    python tools/smoke_device.py --copy D:\\backup     # 額外實測複製第一個照片資料夾

★ v2 修正了 v1 的兩個缺陷：
  1. 列舉旗標 A/B 對照原本跑在葉節點（只有 2 個項目），量到的全是固定開銷，
     結論無效。現在改成自動挑「子資料夾最多」的那一層來測 —— 對 iPhone
     就是 Internal Storage。
  2. find_photo_folders 原本沒有進度也沒有上限，在 50~100 個資料夾的裝置上
     跑 20 分鐘沒有任何反應。現在改為選配、有進度、有上限。
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
        mark = ("  <= 目標 {:.0f}ms".format(target) if ms <= target
                else "  ★ 超過目標 {:.0f}ms".format(target))
    log.info("%-40s %9.1f ms%s", label[:40], ms, mark)
    return result, ms


def bench_enum_flags(abs_pidl, label):
    """A/B 對照：只列資料夾 vs 全部列 vs 只列檔案。

    ★ 這是整個效能契約最關鍵的未知數。
      如果 MTP 的 shell extension 內部仍然走訪全部項目再過濾，
      SHCONTF_FOLDERS 就省不到，我們得改用骨架畫面 + 非同步預取。

    ★ 一定要跑在「項目夠多」的節點上，否則量到的全是 ~45ms 的固定開銷，
      比例毫無意義（v1 就是栽在這裡）。
    """
    log.info("")
    log.info("--- 4. 列舉旗標 A/B 對照：[%s] ---", label)

    folders, ms_folders = timed(
        "SHCONTF_FOLDERS（只要資料夾）",
        lambda: list(shell_ns.iter_entries(abs_pidl, flags=shell_ns.FOLDERS_ONLY)),
    )
    everything, ms_all = timed(
        "FOLDERS|NONFOLDERS（全部）",
        lambda: list(shell_ns.iter_entries(abs_pidl, flags=shell_ns.EVERYTHING)),
    )
    files, ms_files = timed(
        "SHCONTF_NONFOLDERS（只要檔案）",
        lambda: list(shell_ns.iter_entries(abs_pidl, flags=shell_ns.FILES_ONLY)),
    )

    log.info("項目數：資料夾 %d / 全部 %d / 檔案 %d",
             len(folders), len(everything), len(files))

    if len(everything) < 20:
        log.warning("★ 這個節點只有 %d 個項目，量到的主要是固定開銷，"
                    "比例不具參考價值。", len(everything))
        return

    ratio = ms_folders / ms_all if ms_all else 1.0
    log.info("只列資料夾花了全部列舉的 %.0f%% 的時間", ratio * 100)

    # ★ 判定邏輯 v3。
    # v1 錯在跑於 2 個項目的葉節點；v2 錯在「檔案數 0」時直接掉進 else
    # 印出「有效」。真正有意義的訊號是：要求一個回傳 0 項的旗標，
    # 如果仍然花掉跟完整列舉相當的時間，就證明旗標只是事後過濾。
    if n_zero_flag_cost(len(files), ms_files, len(folders), ms_folders):
        log.warning("★ 要求「只列檔案」回傳 0 項，卻仍花了 %.0f ms"
                    "（列 %d 個資料夾是 %.0f ms）", ms_files, len(folders), ms_folders)
        log.warning("  → 旗標是**事後過濾**，不會減少 Shell 的實際工作量。")
        log.warning("  → 效能契約的「只列資料夾比較快」前提**不成立**，"
                    "需要備案：非同步展開 + 骨架畫面 + 跨 session 磁碟快取")
    elif len(files) < 20:
        log.warning("★ 這個節點只有 %d 個檔案，旗標無從區分，不下結論。"
                    "請改用 tools/bench_enum.py 做成本模型量測。", len(files))
    elif ratio > 0.7:
        log.warning("★ SHCONTF_FOLDERS 沒省到 —— 內部很可能仍走訪全部項目。")
    else:
        log.info("★ SHCONTF_FOLDERS 有效（省下 %.0f%%）。", (1 - ratio) * 100)


def n_zero_flag_cost(n_files, ms_files, n_folders, ms_folders):
    """「回傳 0 項的旗標卻仍花了可觀時間」—— 旗標是事後過濾的直接證據。"""
    return n_files == 0 and n_folders > 20 and ms_files > ms_folders * 0.5


def walk_levels(dev, cache, max_depth):
    """一層一層往下展開，記錄每層的節點與子資料夾數。

    回傳 [(label, abs_pidl, subfolder_count), ...]
    """
    levels = []
    node = dev.abs_pidl
    labels = [dev.name]

    for _ in range(max_depth + 1):
        label = " / ".join(labels)
        subs, _ = timed("展開 [{}]".format(label[-36:]),
                        lambda n=node: listing.list_subfolders(n, cache),
                        TARGET_MS)
        levels.append((label, node, subs))
        if not subs:
            break
        node = subs[0].abs_pidl
        labels.append(subs[0].name)

    return levels


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--copy", metavar="DEST",
                        help="額外實測：把找到的第一個照片資料夾複製到 DEST")
    parser.add_argument("--depth", type=int, default=3,
                        help="逐層展開的層數（預設 %(default)s）")
    parser.add_argument("--probe-folders", type=int, default=3, metavar="N",
                        help="對前 N 個照片資料夾做早退 vs 完整列舉的對照"
                             "（預設 %(default)s，設 0 跳過）")
    parser.add_argument("--scan", action="store_true",
                        help="額外測 find_photo_folders（慢）")
    parser.add_argument("--max-folders", type=int, default=20, metavar="N",
                        help="--scan 的走訪上限（預設 %(default)s）")
    args = parser.parse_args()

    setup_logging(level=logging.INFO)

    with shell_ns.com_apartment():
        # ---- 1. 「本機」列舉 ----
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
            log.info("★ 這一輪是「沒插 iPhone」的對照組，"
                     "請記下「列出本機（冷）」的 %.1f ms。", ms_cold)
            return 0

        dev = devices[0]
        log.info("裝置：%s", dev.name)
        status, _ = timed("probe()", lambda: device.probe(dev))
        log.info("狀態：%s", status.name)
        log.info("訊息：%s", device.status_message(status, dev.name).replace("\n", " / "))
        if status is not device.DeviceStatus.OK:
            log.warning("裝置內容讀不到，後面的量測跳過。"
                        "請解鎖手機並點「信任這部電腦」後重跑。")
            return 1

        # ---- 3. 逐層展開 ----
        log.info("")
        log.info("=== 3. 逐層展開（互動路徑，每層都要 < %.0f ms） ===", TARGET_MS)
        cache = listing.NamespaceCache()
        levels = walk_levels(dev, cache, args.depth)
        deepest_label, deepest_node, _ = levels[-1]
        timed("最深一層再展開一次（快取命中）",
              lambda: listing.list_subfolders(deepest_node, cache), TARGET_MS)

        # ---- 4. A/B：挑「子資料夾最多」的那一層來測 ----
        # ★ 這正是 v1 選錯的地方。對 iPhone 來說這一層就是 Internal Storage。
        richest = max(levels, key=lambda lv: len(lv[2]))
        log.info("")
        log.info("子資料夾最多的一層是 [%s]：%d 個",
                 richest[0], len(richest[2]))
        bench_enum_flags(richest[1], richest[0])

        # ---- 5. 早退 vs 完整列舉（要跑在真的裝了很多照片的資料夾上） ----
        candidates = richest[2][: args.probe_folders]
        if candidates:
            log.info("")
            log.info("--- 5. 早退 vs 完整列舉（前 %d 個資料夾） ---", len(candidates))
            for entry in candidates:
                _, ms_probe = timed("  early-exit [{}]".format(entry.name[:24]),
                                    lambda e=entry: listing.folder_has_media(
                                        e.abs_pidl, MEDIA))
                files, ms_full = timed("  full       [{}]".format(entry.name[:24]),
                                       lambda e=entry: list(listing.iter_files(
                                           e.abs_pidl, MEDIA)))
                saved = (1 - ms_probe / ms_full) * 100 if ms_full else 0
                log.info("  → %d 個檔案，早退省下 %.0f%%", len(files), saved)

        # ---- 6. 選配：全裝置掃描 ----
        if args.scan:
            log.info("")
            log.info("=== 6. find_photo_folders（慢，上限 %d 個資料夾） ===",
                     args.max_folders)
            start = time.perf_counter()

            def on_progress(visited, found, name):
                elapsed = time.perf_counter() - start
                log.info("  [%3d 已走訪 / %2d 命中 / %5.1fs] %s",
                         visited, found, elapsed, name[:40])

            folders, _ = timed(
                "find_photo_folders()",
                lambda: device.find_photo_folders(
                    dev, MEDIA, max_depth=2,
                    max_folders=args.max_folders, progress=on_progress,
                    cache=cache),
            )
            for entry in folders:
                log.info("    命中：%s", entry.name)
        else:
            folders = richest[2]
            log.info("")
            log.info("（略過 find_photo_folders，需要時加 --scan）")

        # ---- 7. 選配：實際複製 ----
        if args.copy and folders:
            log.info("")
            log.info("=== 7. 實際複製 [%s] ===", folders[0].name)
            plan = copier.plan_copy([folders[0]], args.copy)
            report, _ = timed("run_copy", lambda: copier.run_copy(plan, MEDIA))
            log.info("結果：%s", report.summary())
            for name in report.failed[:20]:
                log.warning("  失敗：%s", name)

        log.info("")
        log.info("★ 請把整段輸出貼回來，數字會寫進 docs/ai/04-shell-com-notes.md")
        return 0


if __name__ == "__main__":
    sys.exit(main())
