"""可攜式裝置（iPhone）偵測。

★ 絕對不要比對顯示名稱。
  使用者可能把手機改名成「小明的 iPhone」，英文版 Windows 顯示也不同。
  判斷依據是 Shell 屬性：**可攜式裝置是 SFGAO_FOLDER 但不是 SFGAO_FILESYSTEM**，
  而磁碟機與使用者資料夾兩者皆是。這個判斷語言中立、不受改名影響。

★ 未解鎖 / 未點「信任這部電腦」時，Shell 會把裝置內容列舉成「空的」
  而不是回報錯誤。使用者只會看到一片空白然後以為程式壞了。
  probe() 就是為了主動抓出這個狀況 —— 這是投報率最高的單一功能。
"""

import logging
from dataclasses import dataclass
from enum import Enum, auto
from typing import List

from win32com.shell import shellcon

from . import shell_ns
from .errors import OperationCancelled
from .filters import MEDIA
from .listing import FileEntry, folder_has_media, is_empty, list_subfolders

log = logging.getLogger(__name__)

# iPhone 的結構是 裝置 / Internal Storage / <照片資料夾>，深度 2 就夠。
# 舊版預設 3 會多掃一整層，在 50~100 個資料夾的裝置上是數倍的代價。
DEFAULT_SCAN_DEPTH = 2


class DeviceStatus(Enum):
    OK = auto()
    NOT_FOUND = auto()
    LOCKED_OR_UNTRUSTED = auto()


@dataclass(frozen=True)
class Device:
    entry: FileEntry
    parsing_name: str = ""

    @property
    def name(self):
        return self.entry.name

    @property
    def abs_pidl(self):
        return self.entry.abs_pidl


def status_message(status, device_name=None):
    """給 UI 直接顯示的訊息。core 不碰 UI，但這段文字的正確性屬於領域知識。"""
    if status is DeviceStatus.OK:
        return "已連接：{}".format(device_name or "裝置")
    if status is DeviceStatus.NOT_FOUND:
        return ("沒有偵測到 iPhone。\n"
                "請用 USB 線連接手機，稍候幾秒後按「重新整理裝置」。")
    return ("偵測到「{}」，但讀不到裡面的內容。\n"
            "請解鎖 iPhone，並在手機畫面上點「信任這部電腦」，\n"
            "然後按「重新整理裝置」。".format(device_name or "裝置"))


def find_portable_devices():
    """列出「本機」底下所有可攜式裝置。

    回傳空 list 表示沒插、或 Windows 還沒認到（不是錯誤，交給呼叫端判斷）。
    """
    this_pc = shell_ns.this_pc_pidl()
    devices: List[Device] = []

    for child_abs, name, attrs in shell_ns.iter_entries(
        this_pc, flags=shell_ns.EVERYTHING, want_attributes=True
    ):
        is_folder = bool(attrs & shellcon.SFGAO_FOLDER)
        is_filesystem = bool(attrs & shellcon.SFGAO_FILESYSTEM)
        if not is_folder or is_filesystem:
            continue        # 磁碟機與使用者資料夾都是 FILESYSTEM，排除

        try:
            parsing = shell_ns.parsing_name(child_abs)
        except Exception as exc:   # noqa: BLE001 - 診斷用，取不到不該影響偵測
            # 實測 iPhone 這裡會失敗（SHBindToParent 對 MTP 根節點不見得可用）。
            # 我們不靠 parsing name 做判斷，純粹是診斷資訊，失敗就算了。
            log.debug("取不到解析名稱（%s）：%s", name, exc)
            parsing = ""

        entry = FileEntry(name=name, is_dir=True, abs_pidl=child_abs)
        devices.append(Device(entry=entry, parsing_name=parsing))
        log.info("偵測到可攜式裝置：%s（%s）", name, parsing or "無解析名稱")

    return devices


def probe(device):
    """裝置內容讀不讀得到。

    iPhone 鎖定或未信任時，Shell 會把 Internal Storage 列成空的而不報錯，
    所以要實際往下探一層才知道。
    """
    storages = list_subfolders(device.abs_pidl)
    if not storages:
        log.warning("裝置「%s」底下沒有任何儲存區 —— 可能未解鎖或未信任", device.name)
        return DeviceStatus.LOCKED_OR_UNTRUSTED

    for storage in storages:
        if not is_empty(storage.abs_pidl):
            return DeviceStatus.OK

    log.warning("裝置「%s」的儲存區全部是空的 —— 可能未解鎖或未信任", device.name)
    return DeviceStatus.LOCKED_OR_UNTRUSTED


def detect():
    """一次完成「找裝置 + 判斷狀態」。回傳 (status, device or None)。"""
    devices = find_portable_devices()
    if not devices:
        return DeviceStatus.NOT_FOUND, None
    device = devices[0]     # v1 只處理第一台；多裝置由 UI 讓使用者選
    return probe(device), device


def find_photo_folders(root, categories=MEDIA, *,
                       max_depth=DEFAULT_SCAN_DEPTH, max_folders=None,
                       cancel=None, cache=None, progress=None):
    """從指定節點往下遞迴，收集「含有目標類型檔案的資料夾」。

    ★ 這是取代舊版 100APPLE ~ 105APPLE 序號展開的功能。
      iOS 改版曾把結構從 DCIM\\100APPLE 改成 DCIM\\202510_a 再改成直接放在
      Internal Storage 底下 —— 遞迴掃描對這三種都有效，不必跟著改 code。

    ★★ 這是**慢**操作，不能當成預設動作。
      實測（2026-08-27）：MTP 每次列舉有 ~45ms 固定開銷，而一支有 50~100 個
      資料夾的 iPhone 光是展開 Internal Storage 就要 2.7 秒。
      每個資料夾要 folder_has_media（列檔案）+ list_subfolders（列資料夾）
      兩次列舉，總量很容易爆掉 —— 初版沒有進度回報也沒有上限，實測跑了
      20 分鐘沒有任何反應。

    所以這支函式現在強制要求呼叫端提供進度與取消的能力：
      progress(visited, found, current_name)  每處理完一個資料夾呼叫一次
      cancel() -> bool                        回 True 就中止
      max_folders                             走訪上限，超過就停（測試/保險用）

    ★ 先列檔案再列資料夾是刻意的：folder_has_media() 找到第一個符合的就早退，
      對「裡面全是照片」的資料夾只需要讀到第一筆。
    """
    root_pidl = root.abs_pidl if isinstance(root, (Device, FileEntry)) else root
    found: List[FileEntry] = []
    visited = 0
    stopped_early = False

    def check_cancel():
        if cancel is not None and cancel():
            raise OperationCancelled("使用者取消掃描")

    def walk(abs_pidl, depth):
        nonlocal visited, stopped_early
        if stopped_early:
            return
        check_cancel()
        if max_folders is not None and visited >= max_folders:
            stopped_early = True
            log.warning("達到 max_folders=%d 上限，掃描提前結束", max_folders)
            return

        visited += 1
        try:
            name = shell_ns.display_name(abs_pidl)
        except Exception:   # noqa: BLE001
            name = "?"

        if folder_has_media(abs_pidl, categories):
            found.append(FileEntry(name=name, is_dir=True, abs_pidl=abs_pidl))
            log.debug("找到照片資料夾：%s", name)

        if progress is not None:
            progress(visited, len(found), name)

        if depth >= max_depth:
            return
        for sub in list_subfolders(abs_pidl, cache):
            walk(sub.abs_pidl, depth + 1)

    walk(tuple(root_pidl), 0)
    log.info("掃描%s：走訪 %d 個資料夾，找到 %d 個含有媒體檔的資料夾",
             "中止" if stopped_early else "完成", visited, len(found))
    return found
