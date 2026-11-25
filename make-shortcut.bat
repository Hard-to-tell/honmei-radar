@echo off
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$d=[Environment]::GetFolderPath('Desktop'); $s=(New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $d 'Manga Shelf.lnk')); $s.TargetPath=(Join-Path '%~dp0' 'start-shelf.bat'); $s.WorkingDirectory='%~dp0'; $s.Save()"
echo Desktop shortcut "Manga Shelf" created.
pause
