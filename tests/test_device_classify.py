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
    """對照 core.device._classify。兩邊必須一致。

    parsing 為 None 代表「取不到」，跟空字串不一樣。
    """
    if parsing and looks_like_filesystem_path(parsing):
        return False
    if attrs is not None:
        if attrs & SFGAO_FILESYSTEM:
            return False
        if not (attrs & SFGAO_FOLDER):
            return False
        return True
    if parsing:
        return True
    return False


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
    """判斷規則。資料來自 2026-08-29 的實測診斷報告。"""

    def test_real_measured_attributes(self):
        """實測：只有 iPhone 是 0x20000000，其餘都是 0x60000000。"""
        for name in ("下載", "圖片", "音樂", "桌面", "文件", "影片",
                     "OS (C:)", "SDXC (D:)", "USB 磁碟機 (F:)"):
            self.assertFalse(classify(None, 0x60000000), name)
        self.assertTrue(classify(None, 0x20000000), "Apple iPhone")

    def test_drives_are_not_devices(self):
        self.assertFalse(classify("C:\\", FS))
        self.assertFalse(classify("C:\\Users\\x\\Desktop", FS))
        self.assertFalse(classify("\\\\NAS\\share", FS))

    def test_device_with_parsing_name(self):
        self.assertTrue(
            classify("::{GUID}\\\\?\\usb#vid_05ac&pid_12a8", SFGAO_FOLDER))

    def test_non_folder_node_is_not_a_device(self):
        self.assertFalse(classify(None, 0))


class TestUnknownIsNotEvidence(unittest.TestCase):
    """★ 這一組測試存在的理由是一個真實的災難。

    `parsing_name()` 曾經呼叫 pywin32 裡不存在的 `SHBindToParent`，
    所以它對**每一個**節點都失敗。當時的規則是「取不到解析名稱就當作
    裝置」，結果「本機」底下的每個節點都被判成 iPhone，程式跑去抓「下載」。

    原則：**「取不到資訊」只代表我們不知道，不能當成肯定的證據。**
    """

    def test_no_parsing_name_alone_is_not_enough(self):
        # 取不到解析名稱 + 屬性顯示是檔案系統 → 不是裝置
        self.assertFalse(classify(None, FS))

    def test_nothing_known_is_not_a_device(self):
        # 兩個訊號都取不到 → 我們就是不知道，不能猜是裝置
        self.assertFalse(classify(None, None))

    def test_the_2026_08_29_regression(self):
        """重現當時的災情：所有節點的 parsing 都是 None。

        只要屬性還在，就必須能正確分辨出唯一的那台裝置。
        """
        nodes = [
            ("下載", None, 0x60000000),
            ("圖片", None, 0x60000000),
            ("桌面", None, 0x60000000),
            ("OS (C:)", None, 0x60000000),
            ("Apple iPhone", None, 0x20000000),
        ]
        detected = [n for n, parsing, attrs in nodes if classify(parsing, attrs)]
        self.assertEqual(detected, ["Apple iPhone"])


class TestRenamedDeviceIsNameAgnostic(unittest.TestCase):
    """改名不能影響判斷 —— 顯示名稱根本沒有進入 classify()。"""

    def test_verdict_depends_only_on_parsing_and_attrs(self):
        for _name in ("Apple iPhone", "阿神ㄟ@iPhone", "小明の iPhone", "PC"):
            self.assertTrue(classify(None, SFGAO_FOLDER))


if __name__ == "__main__":
    unittest.main(verbosity=2)
