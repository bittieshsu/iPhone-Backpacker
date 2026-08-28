#!/usr/bin/env python
"""打包成可散布的資料夾。

    python tools/build.py

★ 為什麼打包邏輯放在 .py 而不是 .bat：
  開發機是 Linux，寫出來的 .bat 會是 LF 換行 + UTF-8 中文註解，
  而 cmd.exe 需要 CRLF，並且用系統的 OEM codepage（繁中是 950）讀檔。
  兩者相加會讓每行的第一個字元被吃掉、中文變亂碼：

      'equirements.txt' 不是內部或外部命令      ← 少了開頭的 r
      'cho.' 不是內部或外部命令                  ← 少了開頭的 e

  把實際邏輯放在 Python 裡就完全繞開這個問題。
  build.bat 只剩一行純 ASCII 的轉呼叫。
"""

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC = ROOT / "iphone_backpacker.spec"
DIST = ROOT / "dist" / "iPhoneBackpacker"


def main():
    if sys.platform != "win32":
        print("這個腳本只能在 Windows 上執行。")
        return 1

    for stale in (ROOT / "build", ROOT / "dist"):
        if stale.exists():
            print("清理 {}".format(stale))
            shutil.rmtree(stale)

    print("\n打包中（onedir，不用 onefile）…\n")
    result = subprocess.run(
        [sys.executable, "-m", "PyInstaller", str(SPEC), "--noconfirm"],
        cwd=ROOT,
    )
    if result.returncode != 0:
        print("\n打包失敗。")
        print("如果錯誤看起來像缺少模組（ImportError / ModuleNotFoundError），")
        print("請把 iphone_backpacker.spec 裡的 EXCLUDES 清空再試一次 ——")
        print("那份清單是為了縮小體積與加快啟動而排除的模組，排太多就會這樣。")
        return result.returncode

    exe = DIST / "iPhoneBackpacker.exe"
    print("\n完成：{}".format(exe))
    if DIST.exists():
        size = sum(f.stat().st_size for f in DIST.rglob("*") if f.is_file())
        print("資料夾大小：{:.0f} MB".format(size / 1024 / 1024))
    print("\n發布時請把整個 dist\\iPhoneBackpacker 資料夾壓成 zip。")
    print("不要只複製 exe —— 它需要同資料夾裡的其他檔案才能執行。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
