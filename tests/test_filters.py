"""filters 的單元測試 —— 純 Python，不需要 Windows 或 pywin32。

這是階段 1 唯一能在非 Windows 環境驗證的部分；其餘 core 模組要靠
tools/smoke_local.py 在 Windows 上實測。
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from iphone_backpacker.core.filters import (   # noqa: E402
    ALL, MEDIA, Category, categorize, describe, extension_of, matches,
)


class TestExtension(unittest.TestCase):
    def test_case_insensitive(self):
        # 舊版手寫 "jpg"/"JPG"/"Jpg" 仍會漏掉 "jPg"，所以改成統一轉小寫
        for name in ("a.jpg", "a.JPG", "a.Jpg", "a.jPg"):
            self.assertEqual(extension_of(name), "jpg")

    def test_no_extension(self):
        self.assertEqual(extension_of("README"), "")

    def test_dotfile_is_not_an_extension(self):
        self.assertEqual(extension_of(".hidden"), "")

    def test_multiple_dots(self):
        self.assertEqual(extension_of("IMG_0001.backup.HEIC"), "heic")


class TestCategorize(unittest.TestCase):
    def test_heic_is_image(self):
        # 這是舊版最致命的漏洞：選了「保留原始檔」模式的資料夾會被濾成 0 個檔案
        self.assertEqual(categorize("IMG_0001.HEIC"), Category.IMAGE)

    def test_common_iphone_outputs(self):
        cases = {
            "IMG_0001.JPG": Category.IMAGE,
            "IMG_0002.HEIC": Category.IMAGE,
            "IMG_0003.PNG": Category.IMAGE,
            "IMG_0004.MOV": Category.VIDEO,
            "IMG_0005.MP4": Category.VIDEO,
            "IMG_0006.AAE": Category.SIDECAR,
            "notes.txt": Category.OTHER,
        }
        for name, expected in cases.items():
            self.assertEqual(categorize(name), expected, name)


class TestMatches(unittest.TestCase):
    def test_media_excludes_sidecar(self):
        self.assertTrue(matches("IMG_0001.HEIC", MEDIA))
        self.assertTrue(matches("IMG_0004.MOV", MEDIA))
        self.assertFalse(matches("IMG_0006.AAE", MEDIA))
        self.assertFalse(matches("notes.txt", MEDIA))

    def test_all_includes_everything(self):
        for name in ("IMG_0001.HEIC", "IMG_0006.AAE", "notes.txt", "README"):
            self.assertTrue(matches(name, ALL), name)

    def test_images_only(self):
        self.assertTrue(matches("a.jpg", Category.IMAGE))
        self.assertFalse(matches("a.mov", Category.IMAGE))


class TestDescribe(unittest.TestCase):
    def test_media(self):
        self.assertEqual(describe(MEDIA), "照片 + 影片")

    def test_single(self):
        self.assertEqual(describe(Category.IMAGE), "照片")


if __name__ == "__main__":
    unittest.main(verbosity=2)
