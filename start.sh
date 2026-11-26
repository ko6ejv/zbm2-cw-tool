#!/usr/bin/env bash
# ZBM2 CW Tool launcher (Linux / macOS)
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  echo "First run: setting up Python environment..."
  python3 -m venv .venv || { echo "Install python3-venv and python3-tk first (sudo apt install python3-venv python3-tk libportaudio2)"; exit 1; }
  .venv/bin/pip install -q -r requirements.txt
fi
exec .venv/bin/python main.py "$@"
