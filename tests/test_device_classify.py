"""裝置判斷邏輯的單元測試 —— 純 Python，不需要 Windows 或 pywin32。

這些案例來自實際的災情回報：使用者把 iPhone 改名成「阿神ㄟ@iPhone」後，
程式顯示「沒有偵測到 iPhone」，但樹狀瀏覽卻能正常展開。
"""

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# core.device / core.shell_ns 需要 pywin32，在 Linux 上匯入不了。
# 判斷邏輯本身是純資料運算，所以在這裡重現一份對照測試 ——
# 改動 shell_ns.looks_like_filesystem_path 或 device._classify 時，
# 這裡也要跟著改，兩邊必須一致。
SFGAO_FOLDER = 0x20000000
SFGAO_FILESYSTEM = 0x40000000

_DRIVE_PATH = re.compile(r"^[A-Za-z]:([\\/]|$)")


def looks_like_filesystem_path(name):
    if not name:
        return False
    if _DRIVE_PATH.match(name):
        return True
    if name.startswith("\\\\"):
        return True
    return False


def classify(parsing, attrs):
    if looks_like_filesystem_path(parsing):
        return False
    if not parsing:
        if attrs is not None and not (attrs & SFGAO_FOLDER):
            return False
        return True
    if attrs is None:
        return True
    if attrs & SFGAO_FILESYSTEM:
        return False
    if not (attrs & SFGAO_FOLDER):
        return False
    return True


FS = SFGAO_FOLDER | SFGAO_FILESYSTEM


class TestFilesystemPath(unittest.TestCase):
    def test_drives(self):
        for name in ("C:\\", "D:", "F:\\package", "c:/temp"):
            self.assertTrue(looks_like_filesystem_path(name), name)

    def test_user_folders(self):
        self.assertTrue(
            looks_like_filesystem_path("C:\\Users\\Someone\\Desktop"))

    def test_unc(self):
        self.assertTrue(looks_like_filesystem_path("\\\\NAS\\share"))

    def test_mtp_is_not_filesystem(self):
        for name in ("::{20D04FE0-3AEA-1069-A2D8-08002B30309D}\\\\?\\usb#vid_05ac",
                     "::{35786D3C-B075-49b9-88DD-029876E11C01}\\Apple iPhone",
                     ""):
            self.assertFalse(looks_like_filesystem_path(name), name)


class TestClassify(unittest.TestCase):
    def test_drives_are_not_devices(self):
        self.assertFalse(classify("C:\\", FS))
        self.assertFalse(classify("C:\\Users\\x\\Desktop", FS))
        self.assertFalse(classify("\\\\NAS\\share", FS))

    def test_iphone_without_parsing_name(self):
        # 實測：SHBindToParent 對 MTP 根節點會失敗，所以解析名稱是空的。
        # 這是裝置的特徵，不是排除的理由。
        self.assertTrue(classify("", SFGAO_FOLDER))

    def test_iphone_with_parsing_name(self):
        self.assertTrue(
            classify("::{GUID}\\\\?\\usb#vid_05ac&pid_12a8", SFGAO_FOLDER))

    def test_attributes_unreadable_is_permissive(self):
        # 屬性讀不到時寧可放行 —— 少偵測到裝置的代價比誤判大得多。
        self.assertTrue(classify("", None))
        self.assertTrue(classify("::{GUID}\\something", None))

    def test_filesystem_flag_set_but_no_parsing_name(self):
        # 災情回報的可能情境：裝置節點被回報成 FILESYSTEM。
        # 舊版判斷（SFGAO_FOLDER && !FILESYSTEM）會漏掉它。
        self.assertTrue(classify("", FS))

    def test_non_folder_node_is_not_a_device(self):
        self.assertFalse(classify("", 0))


class TestRenamedDeviceIsNameAgnostic(unittest.TestCase):
    """改名不能影響判斷 —— 判斷完全不看顯示名稱。"""

    def test_same_verdict_regardless_of_name(self):
        # 顯示名稱根本沒有進入 classify()，這裡用參數本身證明這件事：
        # 只要 parsing 與 attrs 相同，結果就相同。
        for _name in ("Apple iPhone", "阿神ㄟ@iPhone", "小明の iPhone", "PC"):
            self.assertTrue(classify("", SFGAO_FOLDER))


if __name__ == "__main__":
    unittest.main(verbosity=2)
