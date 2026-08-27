@echo off
REM 在 Windows 上打包成可散布的資料夾。
REM
REM 產物：dist\iPhoneBackpacker\  ← 整個資料夾壓成 zip 發布
REM
REM 前置作業：
REM   pip install -r requirements.txt
REM   pip install pyinstaller

echo === 清理舊的產物 ===
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

echo.
echo === 打包中（onedir，不用 onefile）===
pyinstaller iphone_backpacker.spec --noconfirm
if errorlevel 1 (
    echo.
    echo 打包失敗。若錯誤看起來像缺少模組，
    echo 請把 iphone_backpacker.spec 裡的 EXCLUDES 清空再試一次。
    exit /b 1
)

echo.
echo === 完成 ===
echo 產物：dist\iPhoneBackpacker\iPhoneBackpacker.exe
echo.
echo 發布時請把整個 dist\iPhoneBackpacker 資料夾壓成 zip。
echo 不要只複製 exe，它需要同資料夾裡的其他檔案才能執行。
