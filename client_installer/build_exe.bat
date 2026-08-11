@echo off
echo ============================================
echo   Building System Monitor Agent Client
echo ============================================
echo.

REM Install dependencies if needed
pip install psutil requests pyinstaller

echo.
echo Building .exe (this may take a minute)...
echo.

pyinstaller --onefile --noconsole --name SystemMonitorAgent agent_client.py

echo.
echo ============================================
echo   Build complete!
echo   EXE is located at: dist\SystemMonitorAgent.exe
echo ============================================
pause
