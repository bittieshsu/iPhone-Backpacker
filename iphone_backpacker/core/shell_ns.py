"""Windows Shell 命名空間的薄封裝。

這是全專案唯一直接碰 pywin32 的地方，其他模組只透過這裡的函式操作。

★ 關鍵概念 —— PIDL 與 COM 介面的差別（見 docs/ai/04-shell-com-notes.md）：
  - IShellFolder / IShellItem 這類 COM 介面 **不能跨執行緒傳遞**。
  - PIDL 是純資料（bytes 的序列），**可以**安全跨執行緒傳遞。
  所以整個 core 的公開介面一律用「絕對 PIDL」交換位置，
  需要 COM 物件時在使用端當場 bind 出來。

★ 一律不要比對顯示名稱字串（"本機" / "This PC" / "Apple iPhone"）。
  英文/日文版 Windows 會全滅，使用者把 iPhone 改名也會失效。
"""

import contextlib
import logging
import re

import pythoncom
from win32com.shell import shell, shellcon

from .errors import ItemNotFoundError, ShellError

log = logging.getLogger(__name__)

# 列舉旗標。瀏覽路徑只用 FOLDERS_ONLY —— 見「效能契約」。
FOLDERS_ONLY = shellcon.SHCONTF_FOLDERS
FILES_ONLY = shellcon.SHCONTF_NONFOLDERS
EVERYTHING = shellcon.SHCONTF_FOLDERS | shellcon.SHCONTF_NONFOLDERS

# ★ 批次大小是 MTP 上最重要的效能參數（2026-08-27 實測發現）。
#
# 實測顯示成本 ≈ 每個「被實體化的項目」~10ms，而 Next(n) 不管呼叫端
# 實際要幾筆，都會把 n 筆準備好。所以：
#   - 需要「全部」時：批次大小影響不大，反正每一筆都要付錢。
#   - 只需要「前幾筆」時（早退）：批次大小就是全部的成本。
#     用 64 去問「這資料夾有沒有照片」等於付 64 筆的錢拿 1 筆的答案。
#
# 證據：在 421 項的資料夾上，取 1 筆 / 10 筆 / 50 筆都是 ~720ms（一次
# Next(64)），取 200 筆跳到 2654ms（四次），取 421 筆是 4258ms（七次）。
# 實測（2026-08-27，三個 293~479 項的資料夾）：
#   完整列舉：batch 1~1024 差不到 3%（都是 ~3.4 ms/項）→ 設多少都一樣
#   只取第 1 筆：batch=1 約 67 ms，batch=64 約 290 ms，batch=1024 約 1450 ms
# 所以 DEFAULT_BATCH 不重要，PROBE_BATCH 很重要，而且 1 就是最佳解。
DEFAULT_BATCH = 64
PROBE_BATCH = 1     # 早退式探測用：只想知道「有沒有」，不想付整批的錢

_ENUM_BATCH = DEFAULT_BATCH   # 保留舊名稱


@contextlib.contextmanager
def com_apartment():
    """worker thread 用的 COM apartment。

    每一個會碰 Shell 的執行緒都必須包在這裡面，主執行緒也不例外。
    """
    pythoncom.CoInitialize()
    try:
        yield
    finally:
        pythoncom.CoUninitialize()


def as_pidl(pidl):
    """把 PIDL 正規化成 tuple，讓它可 hash（才能當快取的 key）也可比較。"""
    return tuple(pidl)


def combine(parent_abs_pidl, child_rel_pidl):
    """父節點的絕對 PIDL + 子項的相對 PIDL = 子項的絕對 PIDL。

    pywin32 把 PIDL 表示成 item-id 的序列，所以串接就是直接相加。
    """
    return tuple(parent_abs_pidl) + tuple(child_rel_pidl)


def desktop_folder():
    """Shell 命名空間的根。"""
    return shell.SHGetDesktopFolder()


def this_pc_pidl():
    """「本機 / This PC」的絕對 PIDL。

    用 CSIDL_DRIVES 取得，語言中立 —— 這是取代舊版 rootName="本機" 的正解。
    """
    try:
        pidl = shell.SHGetSpecialFolderLocation(0, shellcon.CSIDL_DRIVES)
    except pythoncom.com_error as exc:
        raise ShellError("無法取得「本機」節點：{}".format(exc)) from exc
    return as_pidl(pidl)


def bind_folder(abs_pidl):
    """由絕對 PIDL 取得 IShellFolder。呼叫端必須已經在 COM apartment 裡。"""
    abs_pidl = tuple(abs_pidl)
    if not abs_pidl:
        return desktop_folder()
    try:
        return desktop_folder().BindToObject(
            list(abs_pidl), None, shell.IID_IShellFolder
        )
    except pythoncom.com_error as exc:
        raise ShellError("無法繫結到資料夾：{}".format(exc)) from exc


def shell_item(abs_pidl):
    """由絕對 PIDL 取得 IShellItem，給 IFileOperation 用。"""
    try:
        return shell.SHCreateItemFromIDList(list(abs_pidl))
    except pythoncom.com_error as exc:
        raise ShellError("無法建立 ShellItem：{}".format(exc)) from exc


def item_from_path(path):
    """由真實檔案系統路徑取得 IShellItem。

    目的地永遠是本機路徑，用這個就好，不需要走 Shell 樹一層層 bind ——
    這取代了舊版整個 getFolderObject_byAbsPath() 的目的地分支。
    """
    try:
        return shell.SHCreateItemFromParsingName(
            str(path), None, shell.IID_IShellItem
        )
    except pythoncom.com_error as exc:
        raise ShellError("無法開啟目的地「{}」：{}".format(path, exc)) from exc


def pidl_from_path(path):
    """由真實檔案系統路徑取得絕對 PIDL。

    主要給測試與「目的地」使用。瀏覽 iPhone 時用不到 ——
    MTP 節點沒有真實路徑，那正是舊版字串路徑解析層失敗的根源。
    """
    text = str(path)
    try:
        result = shell.SHParseDisplayName(text, None, 0)
    except (AttributeError, TypeError):
        result = shell.SHILCreateFromPath(text, 0)
    except pythoncom.com_error as exc:
        raise ShellError("無法解析路徑「{}」：{}".format(path, exc)) from exc
    # 依 pywin32 版本，可能回 pidl 本身或 (pidl, attributes)
    pidl = result[0] if isinstance(result, tuple) else result
    if pidl is None:
        raise ItemNotFoundError("找不到路徑：{}".format(path))
    return as_pidl(pidl)


def _enum_pidls(folder, flags, batch=DEFAULT_BATCH):
    """列舉子項的相對 PIDL。

    batch 直接決定 Next() 一次要求幾筆 —— 在 MTP 上這是主要的成本來源，
    見上方 DEFAULT_BATCH / PROBE_BATCH 的說明。

    pywin32 各版本 IEnumIDList.Next() 的簽章不完全一致，
    所以先試批次、失敗再退回單筆。
    """
    try:
        enumerator = folder.EnumObjects(0, flags)
    except pythoncom.com_error as exc:
        raise ShellError("列舉失敗：{}".format(exc)) from exc
    if enumerator is None:          # 空資料夾在某些 shell extension 上會回 None
        return

    seen = 0
    while True:
        try:
            chunk = enumerator.Next(batch)
        except TypeError:
            batch = None
            chunk = enumerator.Next()
        except pythoncom.com_error as exc:
            # ★★ 這裡以前是 log.warning 之後直接 return，也就是**靜默截斷清單**。
            #   對備份工具來說那是最糟的失敗方式：iter_files() 被截斷後，
            #   copier 會判定「待複製 0 個」而**靜默跳過整個資料夾不備份**，
            #   使用者卻以為備份完成了。
            #   MTP 偶發性失敗是真的存在，所以先重試一次，仍然失敗就 raise，
            #   讓使用者看到錯誤並按「重新整理裝置」，而不是拿到不完整的資料。
            log.warning("列舉中斷（已取得 %d 項）：%s —— 重試一次", seen, exc)
            try:
                chunk = enumerator.Next(batch)
            except pythoncom.com_error as exc2:
                raise ShellError(
                    "列舉中斷，清單不完整（已取得 {} 項）：{}。"
                    "請按「重新整理裝置」，或把 USB 線拔掉重插。".format(seen, exc2)
                ) from exc2
        if not chunk:
            return
        if not isinstance(chunk, (list, tuple)):
            chunk = [chunk]
        for rel_pidl in chunk:
            seen += 1
            yield rel_pidl


def iter_child_pidls(abs_pidl, flags=EVERYTHING, batch=DEFAULT_BATCH):
    """只列舉子項的絕對 PIDL，不取顯示名稱。

    給效能量測與「只需要數量/位置、不需要名字」的場合用。
    GetDisplayNameOf 在 MTP 上是每個項目一次來回，佔比可能不小，
    所以把「有沒有取名字」拆成兩支函式才量得出來。
    """
    folder = bind_folder(abs_pidl)
    for rel_pidl in _enum_pidls(folder, flags, batch):
        yield combine(abs_pidl, rel_pidl)


def iter_entries(abs_pidl, flags=EVERYTHING, want_attributes=False,
                 batch=DEFAULT_BATCH):
    """列舉一層，yield (child_abs_pidl, name, attributes)。

    want_attributes=False 時 attributes 為 None。
    取屬性要多一次 COM 呼叫，非必要不取（MTP 上每一次來回都是成本）。
    """
    folder = bind_folder(abs_pidl)
    for rel_pidl in _enum_pidls(folder, flags, batch):
        try:
            name = folder.GetDisplayNameOf(rel_pidl, shellcon.SHGDN_NORMAL)
        except pythoncom.com_error:
            # ★ 這裡以前是 log.warning + continue，等於**靜默漏掉一個項目**。
            #   漏掉一個資料夾 → 使用者看不到也就備份不到；
            #   漏掉一個檔案 → 那個檔案不會被複製。兩種都不能默默發生。
            #   先重試一次（MTP 偶發），仍然失敗就 raise。
            try:
                name = folder.GetDisplayNameOf(rel_pidl, shellcon.SHGDN_NORMAL)
            except pythoncom.com_error as exc2:
                raise ShellError(
                    "有項目讀不到名稱，清單不完整：{}。"
                    "請按「重新整理裝置」，或把 USB 線拔掉重插。".format(exc2)
                ) from exc2
        attributes = None
        if want_attributes:
            attributes = attributes_of(folder, rel_pidl)
        yield combine(abs_pidl, rel_pidl), name, attributes


def attributes_of(folder, rel_pidl, mask=None):
    """取得項目的 SFGAO_* 屬性。

    我們主要用兩個旗標來判斷「這是不是可攜式裝置」：
      SFGAO_FOLDER      是資料夾類的節點
      SFGAO_FILESYSTEM  對應到真實檔案系統
    iPhone 這種 MTP 裝置是 FOLDER 但 **不是** FILESYSTEM，而磁碟機兩者皆是。
    這個判斷語言中立，也不受使用者把手機改名影響。
    """
    if mask is None:
        mask = shellcon.SFGAO_FOLDER | shellcon.SFGAO_FILESYSTEM
    try:
        return folder.GetAttributesOf([rel_pidl], mask)
    except pythoncom.com_error as exc:
        # ★ 回 None（未知）而不是 0（全部旗標都沒有）。
        #   回 0 會讓呼叫端誤判成「不是資料夾、不是檔案系統」，
        #   裝置偵測就會把這個節點整個排除掉。
        log.warning("取得屬性失敗，視為未知：%s", exc)
        return None


_DRIVE_PATH = re.compile(r"^[A-Za-z]:([\\/]|$)")


def looks_like_filesystem_path(name):
    """解析名稱看起來是不是真實檔案系統路徑。

    磁碟機與使用者資料夾的 SHGDN_FORPARSING 會回 `C:\\` 或
    `C:\\Users\\某人\\Desktop` 這種路徑；
    MTP 裝置則是 `::{GUID}\\\\?\\usb#vid_05ac...` 那種東西。

    這個判斷不看顯示名稱，所以使用者把 iPhone 改成什麼名字都不受影響。
    """
    if not name:
        return False
    if _DRIVE_PATH.match(name):
        return True
    if name.startswith("\\\\"):       # UNC 網路路徑
        return True
    return False


def find_child(abs_pidl, name, flags=EVERYTHING):
    """在指定節點下找出叫某個名字的子項，回傳絕對 PIDL。

    ★ 只用在「已知結構的內部探索」（例如往下找 DCIM），
      不要拿來做使用者可見的路徑解析 —— 那正是舊版被 iOS 改版打爆的地方。
    """
    for child_abs, child_name, _ in iter_entries(abs_pidl, flags):
        if child_name == name:
            return child_abs
    raise ItemNotFoundError("在指定節點下找不到「{}」".format(name))


def display_name(abs_pidl):
    """取得單一節點的顯示名稱。"""
    try:
        parent_folder, rel_pidl = shell.SHBindToParent(
            list(abs_pidl), shell.IID_IShellFolder, None
        )
        return parent_folder.GetDisplayNameOf(rel_pidl, shellcon.SHGDN_NORMAL)
    except pythoncom.com_error as exc:
        raise ShellError("無法取得顯示名稱：{}".format(exc)) from exc


def parsing_name(abs_pidl):
    """取得解析用名稱（SHGDN_FORPARSING）。

    磁碟機會回 "C:\\" 這種真實路徑，MTP 裝置會回 "::{GUID}\\\\?\\usb#..." 這種東西。
    可以當作 SFGAO_FILESYSTEM 之外的第二道判斷依據。
    """
    try:
        parent_folder, rel_pidl = shell.SHBindToParent(
            list(abs_pidl), shell.IID_IShellFolder, None
        )
        return parent_folder.GetDisplayNameOf(rel_pidl, shellcon.SHGDN_FORPARSING)
    except pythoncom.com_error as exc:
        raise ShellError("無法取得解析名稱：{}".format(exc)) from exc
