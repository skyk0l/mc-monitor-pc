@echo off
cd /d "%~dp0"
py -3.11 -m pip install -r requirements.txt pyinstaller
py -3.11 -m PyInstaller --noconfirm --clean --onefile --windowed ^
  --name "MC Monitor" --icon "assets\icon.ico" ^
  --add-data "assets;assets" --add-data "version.json;." mcmonitor\__main__.py
echo.
echo Готово: dist\MC Monitor.exe
pause
