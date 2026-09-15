@echo off
cd /d "%~dp0"

python -m venv venv
call venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt

if not exist .env (
    copy .env.example .env >nul
    echo Created .env from .env.example - edit it if you want non-default settings.
)

echo Setup complete. Run start.bat to launch.
