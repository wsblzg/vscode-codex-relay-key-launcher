@echo off
setlocal
cd /d "%~dp0"
uv run python launch_ui.py
