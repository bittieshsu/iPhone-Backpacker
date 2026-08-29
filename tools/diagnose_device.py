#!/usr/bin/env python
"""收集裝置診斷資料，用來回答災情回報。

    python tools/diagnose_device.py                    # 基本診斷
    python tools/diagnose_device.py --folder 202608_a  # 再細看某個資料夾

輸出會同時印在畫面上並寫進 log 檔，把整段貼回 issue 即可。

這支腳本要回答的問題：

  Q1 為什麼顯示「沒有偵測到 iPhone」，但樹狀瀏覽卻能用？
     → 印出「本機」底下每個節點的 SFGAO 屬性與解析名稱，
       以及裝置判斷走了哪一條規則。

  Q2 某個資料夾顯示 0 個檔案，是真的空的，還是我們沒讀到？
     → 分別用「只列檔案」「只列資料夾」「全部」列舉，三個數字互相對照。
       數字對不起來就是讀取有問題。

  Q3 是不是漏掉了 `__` 結尾的資料夾？
     → 印出完整的子資料夾清單並依後綴分類統計，
       你可以直接跟檔案總管看到的比對。
"""

import argparse
import logging
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from iphone_backpacker.core import device, listing, shell_ns          # noqa: E402
from iphone_backpacker.core.errors import BackpackerError             # noqa: E402
from iphone_backpacker.core.filters import ALL, MEDIA, categorize     # noqa: E402
from iphone_backpacker.core.logging_setup import setup_logging        # noqa: E402

log = logging.getLogger("diagnose")


def section(title):
    log.info("")
    log.info("=" * 60)
    log.info(title)
    log.info("=" * 60)


def dump_this_pc():
    """Q1：「本機」底下每個節點的判斷依據。"""
    section("Q1. 「本機」底下有什麼，以及裝置判斷怎麼決定的")

    this_pc = shell_ns.this_pc_pidl()
    for child_abs, name, attrs in shell_ns.iter_entries(
        this_pc, flags=shell_ns.EVERYTHING, want_attributes=True
    ):
        try:
            parsing = shell_ns.parsing_name(child_abs)
        except Exception as exc:            # noqa: BLE001
            parsing = "（取不到：{}）".format(exc)
        verdict, reason = device._classify(
            "" if parsing.startswith("（取不到") else parsing, attrs)
        log.info("  %-26s", name)
        log.info("      attrs   = %s",
                 "None（讀不到）" if attrs is None else "0x{:08X}".format(attrs))
        log.info("      parsing = %s", parsing or "（空字串）")
        log.info("      判定    = %s（%s）", "★ 裝置" if verdict else "不是裝置", reason)


def dump_folders(node_pidl, label):
    """Q3：完整的子資料夾清單 + 後綴統計。"""
    section("Q3. [{}] 底下的子資料夾".format(label))

    try:
        entries = listing.list_subfolders(node_pidl)
    except BackpackerError as exc:
        log.error("列舉失敗：%s", exc)
        return []

    log.info("共 %d 個子資料夾：", len(entries))
    for entry in entries:
        log.info("    %s", entry.name)

    suffixes = Counter()
    for entry in entries:
        name = entry.name
        suffixes[name[-2:] if len(name) >= 2 else name] += 1
    log.info("")
    log.info("後綴統計（用來檢查是不是漏了 `__` 結尾的）：")
    for suffix, count in sorted(suffixes.items()):
        log.info("    結尾「%s」：%d 個", suffix, count)

    double_underscore = [e.name for e in entries if e.name.endswith("__")]
    log.info("")
    if double_underscore:
        log.info("★ 有 %d 個 `__` 結尾的資料夾，例如：%s",
                 len(double_underscore), "、".join(double_underscore[:5]))
    else:
        log.warning("★ 一個 `__` 結尾的資料夾都沒有。")
        log.warning("  請打開 Windows 檔案總管進到同一層比對：")
        log.warning("  - 檔案總管也沒有 → 這支手機本來就是這樣命名，不是 bug")
        log.warning("  - 檔案總管有、這裡沒有 → 是我們漏了，請回報")
    return entries


def dump_folder_detail(node_pidl, name):
    """Q2：某個資料夾到底有沒有東西。"""
    section("Q2. [{}] 裡面到底有什麼".format(name))

    def count(flags, label):
        try:
            items = list(shell_ns.iter_entries(node_pidl, flags=flags))
        except BackpackerError as exc:
            log.error("  %-22s 讀取失敗：%s", label, exc)
            return None
        log.info("  %-22s %d 項", label, len(items))
        return items

    files = count(shell_ns.FILES_ONLY, "只列檔案")
    folders = count(shell_ns.FOLDERS_ONLY, "只列資料夾")
    everything = count(shell_ns.EVERYTHING, "全部")

    if files is None or folders is None or everything is None:
        log.error("★ 有列舉失敗 —— 顯示 0 個檔案很可能是讀取問題，不是真的空的。")
        return

    if len(files) + len(folders) != len(everything):
        log.warning("★ 數字對不起來：檔案 %d + 資料夾 %d ≠ 全部 %d",
                    len(files), len(folders), len(everything))
        log.warning("  這代表列舉不穩定，顯示的數量不可信。")

    if not everything:
        log.warning("★ 這個資料夾真的是空的（三種列舉都是 0 項）。")
        log.warning("  如果檔案總管進去看得到照片，請回報。")
        return

    log.info("")
    log.info("前 30 個項目與它的分類：")
    for _, item_name, _ in list(
            shell_ns.iter_entries(node_pidl, flags=shell_ns.EVERYTHING))[:30]:
        log.info("    %-32s %s", item_name, categorize(item_name).name)

    media = [n for _, n, _ in shell_ns.iter_entries(
        node_pidl, flags=shell_ns.FILES_ONLY) if categorize(n) & MEDIA]
    log.info("")
    log.info("符合「照片 + 影片」的：%d 個（程式會備份的就是這些）", len(media))
    if everything and not media:
        log.warning("★ 有檔案但沒有一個算照片或影片 —— "
                    "副檔名可能是我們沒涵蓋的，請把上面的清單回報。")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--folder", metavar="NAME",
                        help="要細看的資料夾名稱，例如 202608_a")
    args = parser.parse_args()

    log_path = setup_logging(level=logging.INFO)

    with shell_ns.com_apartment():
        dump_this_pc()

        section("裝置偵測結果")
        devices = device.find_portable_devices()
        if not devices:
            log.error("沒有偵測到任何可攜式裝置 —— 上面 Q1 的表格就是原因，請回報。")
            return 1

        dev = devices[0]
        log.info("裝置：%s", dev.name)
        status = device.probe(dev)
        log.info("狀態：%s", status.name)
        if status is not device.DeviceStatus.OK:
            log.error("讀不到裝置內容，後面的檢查跳過。")
            log.error("%s", device.status_message(status, dev.name))
            return 1

        # 往下找到子資料夾最多的那一層（對 iPhone 就是 Internal Storage）
        node, label = dev.abs_pidl, dev.name
        best = (node, label, [])
        for _ in range(3):
            subs = listing.list_subfolders(node)
            if len(subs) > len(best[2]):
                best = (node, label, subs)
            if not subs:
                break
            node, label = subs[0].abs_pidl, subs[0].name

        parent_pidl, parent_label, children = best
        entries = dump_folders(parent_pidl, parent_label)

        if args.folder:
            match = [e for e in entries if e.name == args.folder]
            if match:
                dump_folder_detail(match[0].abs_pidl, args.folder)
            else:
                log.error("找不到資料夾「%s」。上面的清單裡沒有這個名字。",
                          args.folder)
        elif entries:
            log.info("")
            log.info("想細看某個資料夾（例如顯示 0 個檔案的那個），請跑：")
            log.info("    python tools/diagnose_device.py --folder %s",
                     entries[-1].name)

        section("完成")
        log.info("請把整段輸出貼回 issue。")
        log.info("同樣的內容也在：%s", log_path)
        return 0


if __name__ == "__main__":
    sys.exit(main())
