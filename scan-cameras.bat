@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Обследование камер

echo.
echo   Ищу камеры в вашей сети. Это займёт около полутора минут.
echo   Ничего нажимать не нужно, просто подождите.
echo.

set "REPORT=otchet-%DATE:/=-%.html"

rem Собранный .exe работает без Python. Если его нет — запускаем исходник.
if exist "CameraProbe.exe" (
    CameraProbe.exe --scan auto --report-html "%REPORT%"
) else (
    python probe.py --scan auto --report-html "%REPORT%"
)

echo.
if exist "%REPORT%" (
    echo   Готово. Открываю отчёт…
    start "" "%REPORT%"
) else (
    echo   Отчёт не создан. Проверьте, что компьютер подключён к той же
    echo   сети, что и камеры, и попробуйте снова.
)

echo.
echo   Можно закрыть это окно.
pause >nul
