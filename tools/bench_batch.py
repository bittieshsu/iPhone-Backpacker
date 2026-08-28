#!/usr/bin/env python
"""批次大小對 MTP 列舉成本的影響。

用法（在 Windows 上，插著解鎖的 iPhone）：
    python tools/bench_batch.py --folder 202408__

背景（2026-08-27 的發現）：
  在 421 項的資料夾上，取前 1 / 10 / 50 筆都是 ~720 ms，取前 200 筆
  跳到 2654 ms，取全部（421）是 4258 ms。階梯正好落在 64 的倍數上，
  而 64 就是 shell_ns 寫死的批次大小。

  → 推論：成本 ≈ 每個「被實體化的項目」~10 ms，而 Next(n) 不管呼叫端
    實際要幾筆都會準備 n 筆。

這支腳本要驗證兩件事：
  A. 【早退】只取第一筆時，批次越小是不是越快？
     若成立，folder_has_media() 用小批次可以省下一個數量級。
  B. 【完整列舉】批次大小對「全部取完」有沒有影響？
     若成本純粹是每項固定，應該是平的；若有每次呼叫的固定開銷，
     大批次會比較快。這決定 DEFAULT_BATCH 該設多少。
"""

import argparse
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from iphone_backpacker.core import device, listing, shell_ns    # noqa: E402
from iphone_backpacker.core.logging_setup import setup_logging  # noqa: E402

log = logging.getLogger("bench-batch")

BATCHES = [1, 4, 16, 64, 256, 1024]


def take(abs_pidl, batch, limit=None):
    count = 0
    for _ in shell_ns.iter_child_pidls(abs_pidl, flags=shell_ns.EVERYTHING,
                                       batch=batch):
        count += 1
        if limit is not None and count >= limit:
            break
    return count


def measure(label, fn):
    start = time.perf_counter()
    result = fn()
    ms = (time.perf_counter() - start) * 1000
    log.info("%-40s %9.1f ms", label[:40], ms)
    return result, ms


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--folder", metavar="NAME", required=True,
                        help="要測的資料夾名稱（挑照片多的，幾百張最理想）")
    args = parser.parse_args()

    setup_logging(level=logging.INFO)

    with shell_ns.com_apartment():
        status, dev = device.detect()
        if dev is None or status is not device.DeviceStatus.OK:
            log.error("裝置狀態：%s", status.name)
            return 1

        # 找到目標資料夾
        node = dev.abs_pidl
        target = None
        for _ in range(3):
            subs = listing.list_subfolders(node)
            match = [e for e in subs if e.name == args.folder]
            if match:
                target = match[0]
                break
            if not subs:
                break
            node = subs[0].abs_pidl
        if target is None:
            log.error("找不到資料夾「%s」。用 tools/bench_enum.py --list 看可選項目。",
                      args.folder)
            return 1

        log.info("目標資料夾：%s", target.name)
        log.info("（warm-up 中…）")
        total = take(target.abs_pidl, batch=64)
        log.info("項目數：%d", total)

        # ---- A. 早退：只取第一筆 ----
        log.info("")
        log.info("=== A. 早退（只取第 1 筆），批次大小的影響 ===")
        results_a = []
        for batch in BATCHES:
            _, ms = measure("batch={:>4}  取 1 筆".format(batch),
                            lambda b=batch: take(target.abs_pidl, b, limit=1))
            results_a.append((batch, ms))
        best_a = min(results_a, key=lambda r: r[1])
        worst_a = max(results_a, key=lambda r: r[1])
        log.info("★ 最快 batch=%d (%.0f ms)，最慢 batch=%d (%.0f ms)，差 %.1f 倍",
                 best_a[0], best_a[1], worst_a[0], worst_a[1],
                 worst_a[1] / best_a[1] if best_a[1] else 0)
        if worst_a[1] > best_a[1] * 2:
            log.info("   → 早退確實要搭配小批次。PROBE_BATCH 應設為 %d 附近。",
                     best_a[0])
        else:
            log.warning("   → 批次大小對早退影響不大，PROBE_BATCH 沒必要特別調小。")

        # ---- B. 完整列舉 ----
        log.info("")
        log.info("=== B. 完整列舉（%d 項），批次大小的影響 ===", total)
        results_b = []
        for batch in BATCHES:
            got, ms = measure("batch={:>4}  取全部".format(batch),
                              lambda b=batch: take(target.abs_pidl, b))
            results_b.append((batch, ms, got))
        best_b = min(results_b, key=lambda r: r[1])
        worst_b = max(results_b, key=lambda r: r[1])
        log.info("★ 最快 batch=%d (%.0f ms)，最慢 batch=%d (%.0f ms)，差 %.1f 倍",
                 best_b[0], best_b[1], worst_b[0], worst_b[1],
                 worst_b[1] / best_b[1] if best_b[1] else 0)
        if worst_b[1] > best_b[1] * 1.5:
            log.info("   → 批次大小對完整列舉有影響，DEFAULT_BATCH 應設為 %d。",
                     best_b[0])
            log.info("   → 展開 Internal Storage（184 項）可望從 ~1500ms 降到約 %.0f ms",
                     best_b[1] / total * 184)
        else:
            log.info("   → 成本主要是每項固定，批次大小對完整列舉影響不大。")

        log.info("")
        log.info("★ 請把輸出貼回來。")
        return 0


if __name__ == "__main__":
    sys.exit(main())
