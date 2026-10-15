@echo off
cd /d "%~dp0app"
uv run --python 3.12 python -m zara.enroll
