@echo off
rem Local build of the Windows app + installer (GitHub Actions does the same on every release tag).
cd /d "%~dp0"
py -3 -m pip install -r requirements.txt pyinstaller
py -3 -m PyInstaller --noconfirm --windowed --name "ZBM2 CW Tool" --icon icon.ico ^
  --add-data "logo.png;." --add-data "logo_big.png;." --add-data "logo_small.png;." --add-data "icon.png;." --add-data "icon.ico;." --add-data "fonts;fonts" --add-data "models;models" --add-data "data;data" --hidden-import app --hidden-import tab_chat --hidden-import tab_contest --hidden-import tab_practice --hidden-import tab_log --hidden-import simulator main.py
if exist "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" (
  "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" /DAppVersion=3.0.0 installer\zbm2cw.iss
  echo Installer is in installer\Output\
) else (
  echo App is in "dist\ZBM2 CW Tool\". Install Inno Setup 6 to also build the setup.exe.
)
pause
