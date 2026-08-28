"""例外型別。

core 層一律以例外表達失敗，不 print、不回傳 None 當錯誤碼。
UI 層負責把這些轉成使用者看得懂的對話框。
"""


class BackpackerError(Exception):
    """本專案所有例外的基底。UI 可以只 catch 這個。"""


class ShellError(BackpackerError):
    """Windows Shell / COM 呼叫失敗。"""


class ItemNotFoundError(BackpackerError):
    """在指定的父節點下找不到目標項目。"""


class DeviceNotFoundError(BackpackerError):
    """找不到任何可攜式裝置（iPhone 沒插、或 Windows 還沒認到）。"""


class DeviceUnavailableError(BackpackerError):
    """裝置在，但內容讀不到。

    絕大多數情況是 iPhone 沒解鎖、或沒在手機上點「信任這部電腦」。
    這時 Shell 會把資料夾列舉成「空的」而不是回報錯誤，所以必須主動判斷。
    """


class DestinationError(BackpackerError):
    """目的地資料夾無法使用（不存在、不可寫、不是資料夾）。"""


class OperationCancelled(BackpackerError):
    """使用者中途取消。"""
