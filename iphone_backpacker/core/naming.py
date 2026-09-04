"""檔名／資料夾名稱的處理。

★ 這個模組刻意**不 import pywin32**，所以在非 Windows 環境也能跑測試。
  裡面全是純字串邏輯。
"""

# Windows 檔名不能出現這些字元。
# ★ 虛擬節點的顯示名稱**不受檔案系統的命名限制** —— 例如「Local Disk (C:)」
#   有冒號，第三方掛在「本機」底下的節點也可能帶 \ / * ? 等字元。
_INVALID_NAME_CHARS = '<>:"/\\|?*'

# Windows 保留字。加副檔名也不行（CON.txt 一樣建不起來）。
_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *("COM{}".format(i) for i in range(1, 10)),
    *("LPT{}".format(i) for i in range(1, 10)),
}

MAX_NAME_LENGTH = 120


def safe_folder_name(name):
    """把來源節點的名稱轉成合法的 Windows 資料夾名稱。

    ★ 為什麼需要：使用者可以在樹狀清單裡選**任何**節點，不只是手機的照片
      資料夾 —— 包括「Local Disk (C:)」這種帶冒號的，或第三方掛在「本機」
      底下的虛擬節點。這些名稱直接拿去 `mkdir()` 會拋 OSError，
      使用者只會看到一個看不懂的錯誤。

    >>> safe_folder_name("202608__")
    '202608__'
    >>> safe_folder_name("Local Disk (C:)")
    'Local Disk (C_)'
    >>> safe_folder_name("阿神ㄟ@iPhone")
    '阿神ㄟ@iPhone'
    >>> safe_folder_name("   ")
    '未命名資料夾'
    >>> safe_folder_name("CON")
    '_CON'
    """
    cleaned = "".join(
        "_" if ch in _INVALID_NAME_CHARS or ord(ch) < 32 else ch
        for ch in (name or "")
    )
    # 結尾的空白與句點在 Windows 上會被默默吃掉，
    # 之後拿名稱去比對就會對不起來。
    cleaned = cleaned.strip().rstrip(". ")
    if not cleaned:
        return "未命名資料夾"
    if cleaned.split(".")[0].upper() in _RESERVED_NAMES:
        cleaned = "_" + cleaned
    # 為完整路徑留餘裕，避免碰到 MAX_PATH。
    return cleaned[:MAX_NAME_LENGTH]
