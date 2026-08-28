"""副檔名分類。

一律用 os.path.splitext + .lower()，不要手寫大小寫變體
（舊版 code 列了 "jpg"/"JPG"/"Jpg" 卻仍然漏掉 "jPg"，而且整個漏掉 HEIC）。
"""

import os
from enum import Flag, auto

__all__ = [
    "Category", "MEDIA", "ALL",
    "IMAGE_EXTS", "VIDEO_EXTS", "SIDECAR_EXTS",
    "extension_of", "categorize", "matches", "describe",
]


class Category(Flag):
    IMAGE = auto()
    VIDEO = auto()
    SIDECAR = auto()   # iPhone 的 .aae 編輯紀錄，本身不是影像
    OTHER = auto()


# iPhone「保留原始檔」模式直出 heic/heif/mov；
# 「自動」模式由手機端即時轉檔為 jpg/mov（傳輸較慢，但相容性好）。
IMAGE_EXTS = frozenset({
    "jpg", "jpeg", "jpe", "png", "bmp", "gif",
    "tif", "tiff", "heic", "heif", "dng", "webp",
})
VIDEO_EXTS = frozenset({"mov", "mp4", "m4v", "avi", "3gp", "3g2", "mpg", "mpeg"})
SIDECAR_EXTS = frozenset({"aae"})

MEDIA = Category.IMAGE | Category.VIDEO
ALL = Category.IMAGE | Category.VIDEO | Category.SIDECAR | Category.OTHER

_LABELS = {
    Category.IMAGE: "照片",
    Category.VIDEO: "影片",
    Category.SIDECAR: "編輯紀錄",
    Category.OTHER: "其他",
}


def extension_of(file_name):
    """回傳不含點、已轉小寫的副檔名；沒有副檔名時回傳空字串。

    >>> extension_of("IMG_0001.HEIC")
    'heic'
    >>> extension_of("README")
    ''
    >>> extension_of(".hidden")
    ''
    """
    return os.path.splitext(file_name)[1].lstrip(".").lower()


def categorize(file_name):
    ext = extension_of(file_name)
    if ext in IMAGE_EXTS:
        return Category.IMAGE
    if ext in VIDEO_EXTS:
        return Category.VIDEO
    if ext in SIDECAR_EXTS:
        return Category.SIDECAR
    return Category.OTHER


def matches(file_name, categories):
    """檔名是否落在指定的分類集合裡。categories 是 Category 的 Flag 組合。"""
    return bool(categorize(file_name) & categories)


def describe(categories):
    """把 Flag 組合轉成人看得懂的字串，給 log 與 UI 用。"""
    parts = [label for cat, label in _LABELS.items() if categories & cat]
    return " + ".join(parts) if parts else "（無）"
