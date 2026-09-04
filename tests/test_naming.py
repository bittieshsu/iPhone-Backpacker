"""目的地資料夾名稱消毒的測試。

★ 為什麼需要：使用者可以在樹狀清單裡選**任何**節點 —— 不只是手機的照片
  資料夾，也可能是「Local Disk (C:)」或第三方掛在「本機」底下的虛擬節點。
  虛擬節點的顯示名稱**不受檔案系統的命名限制**，直接拿去 mkdir() 會拋
  OSError，使用者只會看到一個看不懂的錯誤訊息。
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from iphone_backpacker.core.naming import (   # noqa: E402
    MAX_NAME_LENGTH, safe_folder_name,
)


class TestNormalNames(unittest.TestCase):
    def test_iphone_folder_names_unchanged(self):
        for name in ("202608__", "202608_a", "100APPLE", "Internal Storage"):
            self.assertEqual(safe_folder_name(name), name)

    def test_non_ascii_names_are_kept(self):
        # 使用者可以把手機改成任何名字，中文、注音、@ 都要保留
        self.assertEqual(safe_folder_name("阿神ㄟ@iPhone"), "阿神ㄟ@iPhone")


class TestInvalidCharacters(unittest.TestCase):
    def test_drive_node_with_colon(self):
        # 使用者不小心勾了「Local Disk (C:)」
        self.assertEqual(safe_folder_name("Local Disk (C:)"), "Local Disk (C_)")

    def test_all_invalid_characters(self):
        self.assertEqual(safe_folder_name('a<b>c:d"e/f\\g|h?i*j'),
                         "a_b_c_d_e_f_g_h_i_j")

    def test_control_characters(self):
        self.assertEqual(safe_folder_name("a\x00b\tc"), "a_b_c")


class TestEdgeCases(unittest.TestCase):
    def test_empty_and_whitespace(self):
        for name in ("", "   ", None, "..."):
            self.assertEqual(safe_folder_name(name), "未命名資料夾", repr(name))

    def test_trailing_dot_and_space_removed(self):
        # Windows 會默默吃掉結尾的句點與空白，之後比對就對不起來
        self.assertEqual(safe_folder_name("photos."), "photos")
        self.assertEqual(safe_folder_name("photos "), "photos")
        self.assertEqual(safe_folder_name("photos. . "), "photos")

    def test_reserved_names(self):
        for name in ("CON", "PRN", "AUX", "NUL", "COM1", "LPT9"):
            self.assertEqual(safe_folder_name(name), "_" + name)

    def test_reserved_name_with_extension(self):
        # CON.txt 在 Windows 上一樣建不起來
        self.assertEqual(safe_folder_name("CON.txt"), "_CON.txt")

    def test_reserved_name_case_insensitive(self):
        self.assertEqual(safe_folder_name("con"), "_con")

    def test_not_reserved_if_only_a_prefix(self):
        self.assertEqual(safe_folder_name("CONTACTS"), "CONTACTS")

    def test_long_name_is_truncated(self):
        result = safe_folder_name("a" * 500)
        self.assertEqual(len(result), MAX_NAME_LENGTH)


class TestAlwaysUsable(unittest.TestCase):
    """不管輸入什麼，輸出一定是能拿去 mkdir 的名字。"""

    def test_never_returns_empty(self):
        for name in ("", "   ", "...", None, "?*<>", "\x00", ". . ."):
            result = safe_folder_name(name)
            self.assertTrue(result, repr(name))
            self.assertFalse(any(ch in result for ch in '<>:"/\\|?*'), repr(name))
            self.assertEqual(result, result.strip(), repr(name))
            self.assertFalse(result.endswith("."), repr(name))


if __name__ == "__main__":
    unittest.main(verbosity=2)
