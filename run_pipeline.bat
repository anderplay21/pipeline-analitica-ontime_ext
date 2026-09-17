@echo off
REM ============================================================
REM run_pipeline.bat
REM Ejecuta el pipeline analitica-ontime (SharePoint -> SQL Server)
REM usando el interprete del entorno virtual del proyecto (env-pipe).
REM Pensado para ser llamado por el Programador de tareas de Windows.
REM ============================================================

setlocal

REM La raiz del proyecto se deduce de la ubicacion del .bat (%~dp0), asi el archivo
REM sigue funcionando si la carpeta se mueve o se renombra.
set "PROYECTO=%~dp0"
if "%PROYECTO:~-1%"=="\" set "PROYECTO=%PROYECTO:~0,-1%"
set "PYTHON=%PROYECTO%\env-pipe\Scripts\python.exe"
set "SCRIPT=%PROYECTO%\src\main_pipeline.py"
set "LOG_BAT=%PROYECTO%\logs\bat_run.log"

REM Log propio del .bat (encabezado con fecha/hora de cada intento).
echo. >> "%LOG_BAT%"
echo ==== Inicio %date% %time% ==== >> "%LOG_BAT%"

cd /d "%PROYECTO%"
"%PYTHON%" "%SCRIPT%" >> "%LOG_BAT%" 2>&1
set "RC=%ERRORLEVEL%"

echo ==== Fin %date% %time% (codigo salida: %RC%) ==== >> "%LOG_BAT%"

endlocal
exit /b %RC%
