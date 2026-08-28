"""logging 設定。

★ 為什麼不能用 print：PyInstaller 以 --windowed 打包後 sys.stdout / sys.stderr
  會是 None，此時 print() 直接拋 AttributeError 讓程式閃退。tqdm 寫 stderr，同樣中招。
  所以 GUI 路徑上一律走 logging，而且預設只寫檔。
"""

import logging
import os
import sys
import tempfile
from logging.handlers import RotatingFileHandler
from pathlib import Path

_FORMAT = "%(asctime)s %(levelname)-7s [%(name)s] %(message)s"
_configured = False


def default_log_dir():
    """Windows 用 %LOCALAPPDATA%，其他平台或取不到時退回暫存目錄。"""
    base = os.environ.get("LOCALAPPDATA")
    if base:
        return Path(base) / "iPhoneBackpacker" / "logs"
    return Path(tempfile.gettempdir()) / "iPhoneBackpacker" / "logs"


def setup_logging(log_dir=None, level=logging.INFO, console=None):
    """設定 root logger，回傳 log 檔路徑。重複呼叫是安全的。

    console=None 表示「有 stderr 才輸出到 console」——
    這樣 CLI 下看得到訊息，--windowed 打包後自動關閉，不會閃退。
    """
    global _configured
    log_dir = Path(log_dir) if log_dir else default_log_dir()
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "backpacker.log"

    if _configured:
        return log_path

    root = logging.getLogger()
    root.setLevel(level)

    file_handler = RotatingFileHandler(
        log_path, maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(logging.Formatter(_FORMAT))
    root.addHandler(file_handler)

    if console is None:
        console = sys.stderr is not None
    if console:
        stream_handler = logging.StreamHandler(sys.stderr)
        stream_handler.setFormatter(logging.Formatter(_FORMAT))
        root.addHandler(stream_handler)

    _configured = True
    logging.getLogger(__name__).info("Log file: %s", log_path)
    return log_path
