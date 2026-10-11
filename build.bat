@echo off
REM Construit dist\ArmaCleaner.exe (un seul fichier, sans Python requis pour l'utiliser)
python -m pip install --upgrade pyinstaller || exit /b 1
python -m PyInstaller --onefile --windowed --clean --name ArmaCleaner --icon logo.ico --add-data "logo_win.png;." arma_cleaner.py || exit /b 1
echo.
echo OK : dist\ArmaCleaner.exe
