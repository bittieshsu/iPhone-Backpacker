#!/usr/bin/env python
"""開發用的啟動腳本：python run_gui.py

★ 這裡刻意在 import 前後計時。使用者回報首次啟動要 7 秒、第二次 3 秒，
  而 log 顯示程式本身的初始化只有約 1.2 秒 —— 差額全在 Python 直譯器
  啟動與 PySide6 的 import，那發生在任何我們能控制的程式碼之前。
  打包成 --onedir 之後會明顯變快，這個計時就是用來確認的。
"""

import sys
import time

_start = time.perf_counter()

from iphone_backpacker.app import main   # noqa: E402

_IMPORT_MS = (time.perf_counter() - _start) * 1000

if __name__ == "__main__":
    sys.exit(main())
