"""裝置判斷邏輯的單元測試 —— 純 Python，不需要 Windows 或 pywin32。

案例全部來自真實的災情回報：

  民眾A（2026-08-29）：iPhone 改名成「阿神ㄟ@iPhone」後偵測不到。
      根因是 parsing_name 呼叫了 pywin32 裡不存在的 SHBindToParent，
      每個節點的解析名稱都取不到，而當時把「取不到」當成了裝置的證據。

  民眾B（2026-08-30）：程式抓到「CopyTrans Studio」而不是 iPhone。
      根因是第三方掛進「本機」的 namespace extension 與可攜式裝置
      **同屬虛擬資料夾**，SFGAO_FOLDER/FILESYSTEM 在原理上分不出來。
"""

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# core.device / core.shell_ns 需要 pywin32，在 Linux 上匯入不了。
# 判斷邏輯本身是純資料運算，所以在這裡重現一份對照實作 ——
# 改動 shell_ns 或 device._classify 時，這裡也要跟著改，兩邊必須一致。
SFGAO_FOLDER = 0x20000000
SFGAO_FILESYSTEM = 0x40000000

_DRIVE_PATH = re.compile(r"^[A-Za-z]:([\\/]|$)")

_WPD_DEVICE_INTERFACE = "{6ac27878-a6fa-4155-ba85-f98f491d4f33}"
_WPD_NAMESPACE = "{35786d3c-b075-49b9-88dd-029876e11c01}"
_DEVICE_INTERFACE_PREFIX = "\\\\?\\"

EXCLUDED, LIKELY, CONFIRMED = "EXCLUDED", "LIKELY", "CONFIRMED"


def looks_like_filesystem_path(name):
    if not name:
        return False
    if _DRIVE_PATH.match(name):
        return True
    if name.startswith("\\\\"):
        return True
    return False


def looks_like_portable_device(parsing):
    if not parsing:
        return False
    lowered = parsing.lower()
    return (_DEVICE_INTERFACE_PREFIX in parsing
            or _WPD_DEVICE_INTERFACE in lowered
            or _WPD_NAMESPACE in lowered)


def classify(parsing, attrs):
    """對照 core.device._classify。兩邊必須一致。

    parsing 為 None 代表「取不到」，跟空字串不一樣。
    """
    if parsing and looks_like_filesystem_path(parsing):
        return EXCLUDED
    if attrs is not None:
        if attrs & SFGAO_FILESYSTEM:
            return EXCLUDED
        if not (attrs & SFGAO_FOLDER):
            return EXCLUDED
    if looks_like_portable_device(parsing):
        return CONFIRMED
    if attrs is not None:
        return LIKELY
    if parsing:
        return LIKELY
    return EXCLUDED


FS = SFGAO_FOLDER | SFGAO_FILESYSTEM
VIRTUAL = SFGAO_FOLDER

# 民眾A 的 iPhone 解析名稱（實測原文）
IPHONE_PARSING = (
    r"::{20D04FE0-3AEA-1069-A2D8-08002B30309D}\\\?\usb#vid_05ac&pid_12a8"
    r"#0000802000117ce43684002e#{6ac27878-a6fa-4155-ba85-f98f491d4f33}"
)


class TestFilesystemPath(unittest.TestCase):
    def test_drives(self):
        for name in ("C:\\", "D:", "G:\\", "F:\\package", "c:/temp"):
            self.assertTrue(looks_like_filesystem_path(name), name)

    def test_user_folders(self):
        self.assertTrue(
            looks_like_filesystem_path("C:\\Users\\c.Phar\\Pictures"))

    def test_unc(self):
        self.assertTrue(looks_like_filesystem_path("\\\\NAS\\share"))

    def test_virtual_nodes_are_not_filesystem_paths(self):
        for name in (IPHONE_PARSING, "::", "", None):
            self.assertFalse(looks_like_filesystem_path(name), repr(name))


class TestPortableDeviceEvidence(unittest.TestCase):
    """WPD 的正面證據。這是唯一能把可攜式裝置跟第三方掛載分開的訊號。"""

    def test_real_iphone_parsing_name(self):
        self.assertTrue(looks_like_portable_device(IPHONE_PARSING))

    def test_other_vendor_phone(self):
        # 另一份獨立來源的實例，不同廠牌手機
        self.assertTrue(looks_like_portable_device(
            r"::{35786D3C-B075-49B9-88DD-029876E11C01}\\\?\usb#vid_0e79"
            r"&pid_52c2#908165c02an9577#{6ac27878-a6fa-4155-ba85-f98f491d4f33}"))

    def test_non_usb_wpd_still_detected(self):
        # MTP over IP / Bluetooth 沒有 usb#，所以我們測的是 \\?\ 與 WPD GUID，
        # 不是測 usb#。
        self.assertTrue(looks_like_portable_device(
            r"::{20D04FE0-3AEA-1069-A2D8-08002B30309D}"
            r"\{6AC27878-A6FA-4155-BA85-F98F491D4F33}"))

    def test_third_party_namespace_extension(self):
        self.assertFalse(looks_like_portable_device(
            r"::{20D04FE0-3AEA-1069-A2D8-08002B30309D}"
            r"\::{11111111-2222-3333-4444-555555555555}"))

    def test_unreadable_parsing_name_is_not_evidence(self):
        for value in (None, "", "::"):
            self.assertFalse(looks_like_portable_device(value), repr(value))


class TestClassifyBasics(unittest.TestCase):
    def test_drives_and_user_folders(self):
        self.assertEqual(classify("C:\\", FS), EXCLUDED)
        self.assertEqual(classify("C:\\Users\\x\\Desktop", FS), EXCLUDED)
        self.assertEqual(classify("G:\\", FS), EXCLUDED)          # Google Drive
        self.assertEqual(classify("\\\\NAS\\share", FS), EXCLUDED)

    def test_non_folder_node(self):
        self.assertEqual(classify(None, 0), EXCLUDED)

    def test_confirmed_iphone(self):
        self.assertEqual(classify(IPHONE_PARSING, VIRTUAL), CONFIRMED)


class TestUnknownIsNotEvidence(unittest.TestCase):
    """★ 民眾A 的災難：把「取不到資訊」當成正面證據。

    parsing_name() 曾經呼叫 pywin32 裡不存在的 SHBindToParent，所以它對
    **每一個**節點都失敗。當時的規則是「取不到解析名稱就當作裝置」，
    結果「本機」底下每個節點都被判成 iPhone，程式跑去抓「下載」。
    """

    def test_no_parsing_name_alone_is_not_enough(self):
        self.assertEqual(classify(None, FS), EXCLUDED)

    def test_nothing_known_is_not_a_device(self):
        self.assertEqual(classify(None, None), EXCLUDED)

    def test_民眾A_regression(self):
        """重現當時的資料：所有節點的 parsing 都是 None。

        只要屬性還在，就必須能正確挑出唯一的那台裝置。
        解析名稱讀不到，所以只能到 LIKELY —— 但仍然是唯一的候選。
        """
        nodes = [
            ("下載", None, FS),
            ("圖片", None, FS),
            ("桌面", None, FS),
            ("OS (C:)", None, FS),
            ("Apple iPhone", None, VIRTUAL),
        ]
        verdicts = {name: classify(p, a) for name, p, a in nodes}
        self.assertEqual(
            [n for n, v in verdicts.items() if v != EXCLUDED],
            ["Apple iPhone"])
        self.assertEqual(verdicts["Apple iPhone"], LIKELY)


class TestThirdPartyNamespaceExtension(unittest.TestCase):
    """★ 民眾B 的災難：第三方掛進「本機」的 namespace extension。

    CopyTrans Studio 與 Apple iPhone 的 attrs 完全相同（0x20000000），
    因為兩者**同屬虛擬資料夾**。SFGAO 在原理上分不出來，
    必須靠解析名稱裡的 WPD 正面證據。
    """

    def test_same_attrs_cannot_be_told_apart_by_attrs_alone(self):
        self.assertEqual(
            classify(None, VIRTUAL),      # CopyTrans，解析名稱讀不到
            classify(None, VIRTUAL),      # iPhone，解析名稱讀不到
        )

    def test_parsing_name_separates_them(self):
        copytrans = r"::{20D04FE0-3AEA-1069-A2D8-08002B30309D}\::{CopyTransCLSID}"
        self.assertEqual(classify(copytrans, VIRTUAL), LIKELY)
        self.assertEqual(classify(IPHONE_PARSING, VIRTUAL), CONFIRMED)

    def test_民眾B_full_node_list(self):
        """民眾B 報告裡的 11 個節點，逐一驗證。"""
        nodes = [
            ("Downloads", r"C:\Users\c.Phar\Downloads", FS, EXCLUDED),
            ("3D Objects", r"C:\Users\c.Phar\3D Objects", FS, EXCLUDED),
            ("Pictures", r"C:\Users\c.Phar\Pictures", FS, EXCLUDED),
            ("Music", r"C:\Users\c.Phar\Music", FS, EXCLUDED),
            ("CopyTrans Studio",
             r"::{20D04FE0-3AEA-1069-A2D8-08002B30309D}\::{CopyTransCLSID}",
             VIRTUAL, LIKELY),
            ("Desktop", r"C:\Users\c.Phar\Desktop", FS, EXCLUDED),
            ("Documents", r"C:\Users\c.Phar\Documents", FS, EXCLUDED),
            ("Videos", r"C:\Users\c.Phar\Videos", FS, EXCLUDED),
            ("Apple iPhone", IPHONE_PARSING, VIRTUAL, CONFIRMED),
            ("Local Disk (C:)", r"C:\ ", FS, EXCLUDED),
            ("Google Drive (G:)", r"G:\ ", FS, EXCLUDED),
        ]
        for name, parsing, attrs, expected in nodes:
            self.assertEqual(classify(parsing, attrs), expected, name)

        confirmed = [n for n, p, a, _ in nodes if classify(p, a) == CONFIRMED]
        self.assertEqual(confirmed, ["Apple iPhone"],
                         "只有 iPhone 應該被確認為可攜式裝置")


class TestNameAgnostic(unittest.TestCase):
    """改名不能影響判斷 —— 顯示名稱根本沒有進入 classify()。"""

    def test_verdict_depends_only_on_parsing_and_attrs(self):
        for _name in ("Apple iPhone", "阿神ㄟ@iPhone", "小明の iPhone", "PC"):
            self.assertEqual(classify(IPHONE_PARSING, VIRTUAL), CONFIRMED)


if __name__ == "__main__":
    unittest.main(verbosity=2)
