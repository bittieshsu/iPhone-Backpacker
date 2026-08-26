#!/usr/bin/env python
"""列舉成本模型量測 —— 回答「MTP 的 enumerator 是不是串流」。

用法（在 Windows 上，插著解鎖的 iPhone）：
    python tools/bench_enum.py                     # 自動挑資料夾最多的那層
    python tools/bench_enum.py --folder 201501__   # 指定某個資料夾（挑張數多的）
    python tools/bench_enum.py --list              # 只列出可選的資料夾名稱

要回答的問題：
  Q1  取顯示名稱（GetDisplayNameOf）佔多少成本？
  Q2  ★ enumerator 是串流回傳，還是第一次 Next() 就把整份清單備妥？
      這決定 folder_has_media() 的早退到底有沒有意義。
  Q3  單次列舉的固定成本與每項成本各是多少？

★ 量測方法上的注意：每一項都先做一次 warm-up 再計時。
  前一版 benchmark 沒做，導致「先跑的比較慢」被誤讀成「早退比較慢」。
"""

import argparse
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from iphone_backpacker.core import device, listing, shell_ns    # noqa: E402
from iphone_backpacker.core.logging_setup import setup_logging  # noqa: E402

log = logging.getLogger("bench-enum")


def consume(abs_pidl, flags, limit=None, with_names=True):
    """列舉並在取到 limit 筆後停止，回傳實際取到的筆數。"""
    source = (shell_ns.iter_entries(abs_pidl, flags=flags) if with_names
              else shell_ns.iter_child_pidls(abs_pidl, flags=flags))
    count = 0
    for _ in source:
        count += 1
        if limit is not None and count >= limit:
            break
    return count


def measure(label, fn, warmup=True):
    """先 warm-up 再計時，避免冷熱差異被誤讀成真實差異。"""
    if warmup:
        fn()
    start = time.perf_counter()
    result = fn()
    ms = (time.perf_counter() - start) * 1000
    log.info("%-44s %9.1f ms", label[:44], ms)
    return result, ms


def bench_scaling(abs_pidl, name, total_hint=None):
    """★ 核心量測：取 N 筆各要多久。

    如果「取 1 筆」跟「取全部」差不多 → enumerator 不是串流，
    第一次 Next() 就把整份清單備妥了，早退**沒有意義**。
    如果耗時隨 N 線性成長 → 是串流，早退**有意義**。
    """
    log.info("")
    log.info("=== Q2 串流測試：[%s] ===", name)

    steps = [1, 10, 50, 200, 800]
    points = []
    for n in steps:
        got, ms = measure("取前 {:>4} 筆".format(n),
                          lambda k=n: consume(abs_pidl, shell_ns.EVERYTHING, limit=k))
        points.append((got, ms))
        if got < n:
            log.info("   （這個資料夾只有 %d 項，後面的級距略過）", got)
            break

    total, ms_total = measure("取全部",
                              lambda: consume(abs_pidl, shell_ns.EVERYTHING))
    points.append((total, ms_total))

    log.info("")
    first = points[0]
    if ms_total > 0 and first[1] / ms_total > 0.7 and total > first[0] * 5:
        log.warning("★ 結論：取 1 筆花了取全部的 %.0f%% 時間 → **不是串流**。",
                    first[1] / ms_total * 100)
        log.warning("   folder_has_media() 的早退沒有意義，應該改成"
                    "「列舉一次就把名單留著」而不是「早退」。")
    elif total > first[0] * 5:
        log.info("★ 結論：耗時隨筆數成長 → **是串流**，早退有意義。")
        # 用兩個相距最遠的點估固定成本與每項成本
        (n1, t1), (n2, t2) = points[0], points[-1]
        if n2 > n1:
            per_item = (t2 - t1) / (n2 - n1)
            fixed = t1 - per_item * n1
            log.info("   成本模型估計：固定 %.0f ms + 每項 %.2f ms", fixed, per_item)
            log.info("   → 一個 5000 張照片的資料夾約需 %.1f 秒",
                     (fixed + per_item * 5000) / 1000)
    else:
        log.warning("★ 這個資料夾只有 %d 項，測不出串流與否。"
                    "請用 --folder 指定一個照片多的資料夾。", total)
    return total


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--folder", metavar="NAME",
                        help="指定要測的資料夾名稱（建議挑照片多的）")
    parser.add_argument("--list", action="store_true",
                        help="只列出可選的資料夾名稱就結束")
    args = parser.parse_args()

    setup_logging(level=logging.INFO)

    with shell_ns.com_apartment():
        status, dev = device.detect()
        if dev is None or status is not device.DeviceStatus.OK:
            log.error("裝置狀態：%s", status.name)
            log.error("%s", device.status_message(status, dev.name if dev else None))
            return 1
        log.info("裝置：%s", dev.name)

        # 往下找到「子資料夾最多」的那一層
        node = dev.abs_pidl
        best = (node, dev.name, [])
        for _ in range(3):
            subs = listing.list_subfolders(node)
            if len(subs) > len(best[2]):
                best = (node, "…", subs)
            if not subs:
                break
            node = subs[0].abs_pidl
        parent_pidl, _, children = best
        log.info("資料夾最多的一層有 %d 個子資料夾", len(children))

        if args.list:
            for entry in children:
                log.info("    %s", entry.name)
            return 0

        # ---- Q1：取名字佔多少成本 ----
        log.info("")
        log.info("=== Q1：GetDisplayNameOf 的佔比（在 %d 項的節點上） ===",
                 len(children))
        _, ms_raw = measure("只取 PIDL（不取名字）",
                            lambda: consume(parent_pidl, shell_ns.EVERYTHING,
                                            with_names=False))
        _, ms_named = measure("取 PIDL + 顯示名稱",
                              lambda: consume(parent_pidl, shell_ns.EVERYTHING,
                                              with_names=True))
        if ms_named > 0:
            log.info("★ 取名字佔總成本的 %.0f%%", (1 - ms_raw / ms_named) * 100)

        # ---- Q3：旗標到底有沒有減少工作量 ----
        log.info("")
        log.info("=== Q3：旗標是不是事後過濾 ===")
        n_folders, ms_f = measure("SHCONTF_FOLDERS",
                                  lambda: consume(parent_pidl, shell_ns.FOLDERS_ONLY,
                                                  with_names=False))
        n_files, ms_n = measure("SHCONTF_NONFOLDERS",
                                lambda: consume(parent_pidl, shell_ns.FILES_ONLY,
                                                with_names=False))
        log.info("資料夾 %d 個 / 檔案 %d 個", n_folders, n_files)
        if n_files == 0 and ms_n > ms_f * 0.5:
            log.warning("★ 要求「只列檔案」回傳 0 項卻仍花了 %.0f ms"
                        "（跟列 %d 個資料夾的 %.0f ms 相當）", ms_n, n_folders, ms_f)
            log.warning("   → 旗標是**事後過濾**，不會減少 Shell 的實際工作量。")

        # ---- Q2：串流測試 ----
        target_pidl, target_name = parent_pidl, "（資料夾最多的那層）"
        if args.folder:
            match = [e for e in children if e.name == args.folder]
            if not match:
                log.error("找不到資料夾「%s」，用 --list 看可選項目。", args.folder)
                return 1
            target_pidl, target_name = match[0].abs_pidl, match[0].name
        else:
            log.info("")
            log.info("（未指定 --folder，用資料夾最多的那層做串流測試。"
                     "若想測「照片很多的資料夾」，請用 --list 挑一個再用 --folder 指定）")

        bench_scaling(target_pidl, target_name)

        log.info("")
        log.info("★ 請把輸出貼回來，數字會寫進 docs/ai/04-shell-com-notes.md")
        return 0


if __name__ == "__main__":
    sys.exit(main())
