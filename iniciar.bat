@echo off
cd /d "%~dp0"
if not exist .venv (
  echo Criando ambiente virtual...
  python -m venv .venv
)
call .venv\Scripts\activate.bat
pip install -r requirements.txt -q
if not exist .env copy .env.example .env
echo.
echo Abrindo em http://127.0.0.1:5000
python app.py
