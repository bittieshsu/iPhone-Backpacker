#!/usr/bin/env python
"""從命令列產生診斷報告。

    python tools/diagnose_device.py                    # 存到桌面
    python tools/diagnose_device.py --folder 202608_a  # 順便細看某個資料夾
    python tools/diagnose_device.py --stdout           # 只印出來，不存檔

★ 一般使用者不需要用這個。程式的工具列上有「產生診斷報告」按鈕，
  按下去會做完全一樣的事，並把 .txt 存到桌面。
  這支腳本是給開發時、或者已經在跑原始碼的人用的。

實際的檢查邏輯在 `iphone_backpacker/core/diagnostics.py`，
GUI 與這裡共用同一份。
"""

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from iphone_backpacker.core import device, diagnostics, listing, shell_ns  # noqa: E402
from iphone_backpacker.core.logging_setup import setup_logging             # noqa: E402

log = logging.getLogger("diagnose")


def _find_folder(name):
    """在裝置底下找出叫這個名字的資料夾，找不到回 None。"""
    detection = device.detect()
    root = detection.device or (detection.candidates[0]
                                if detection.candidates else None)
    if root is None:
        return None
    node = root.abs_pidl
    for _ in range(3):
        subs = listing.list_subfolders(node)
        for entry in subs:
            if entry.name == name:
                return entry
        if not subs:
            return None
        node = subs[0].abs_pidl
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--folder", metavar="NAME",
                        help="要細看的資料夾名稱，例如 202608_a")
    parser.add_argument("--stdout", action="store_true",
                        help="只印出報告，不存檔")
    args = parser.parse_args()

    setup_logging(level=logging.INFO)

    with shell_ns.com_apartment():
        focus = None
        if args.folder:
            focus = _find_folder(args.folder)
            if focus is None:
                log.warning("找不到資料夾「%s」，報告仍會產生，"
                            "只是少了逐項檢查那一段。", args.folder)

        text = diagnostics.collect_report(
            focus_folder=focus,
            progress=lambda heading: log.info("… %s", heading),
        )

    if args.stdout:
        print(text)
        return 0

    path = diagnostics.write_report(text)
    log.info("")
    log.info("報告已存到：%s", path)
    log.info("請把這個檔案整份傳給開發者。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
