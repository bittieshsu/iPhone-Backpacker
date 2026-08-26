"""核心邏輯層。

規則（見 CLAUDE.md）：
- 這個套件底下**零 Qt import、零 print**。
- 錯誤一律 raise（errors.py 的型別），不要靠回傳值或印訊息表達失敗。
- 瀏覽路徑不碰檔案，見 docs/ai/01-architecture.md 的「效能契約」。
"""
