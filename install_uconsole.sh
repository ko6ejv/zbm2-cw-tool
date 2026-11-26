#!/usr/bin/env bash
# ZBM2 CW Tool installer for the ClockworkPi uConsole (CM4) and other Raspberry Pi OS 64-bit machines.
#
#   curl -fsSL https://raw.githubusercontent.com/ko6ejv/zbm2-cw-tool/main/install_uconsole.sh | bash
#
# Installs system packages, the app (in ~/zbm2-cw-tool), its Python environment (incl. the
# neural CW decoder), Hamlib (system copy; newest on request), serial-port
# permissions and a desktop launcher. Safe to run again to update.
set -euo pipefail

REPO="${ZBM2_REPO:-https://github.com/ko6ejv/zbm2-cw-tool}"
DEST="${ZBM2_DIR:-$HOME/zbm2-cw-tool}"
HAMLIB_VER="${HAMLIB_VER:-4.7.2}"
say() { printf '\n\033[1;33m>> %s\033[0m\n' "$*"; }

arch="$(uname -m)"
case "$arch" in
  aarch64|x86_64) ;;
  *) say "Note: $arch is a 32-bit OS. The app runs, but the neural decoder needs 64-bit Raspberry Pi OS." ;;
esac

say "1/6  System packages"
sudo apt-get update -qq
sudo apt-get install -y -qq git python3 python3-venv python3-tk python3-dev libportaudio2 \
  libhamlib-utils curl ca-certificates

say "2/6  Getting the app"
if [ -d "$DEST/.git" ]; then
  git -C "$DEST" pull --ff-only
else
  git clone --depth 1 "$REPO" "$DEST"
fi
cd "$DEST"

say "3/6  Python environment (this takes a few minutes the first time)"
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
if ! .venv/bin/pip install -q -r requirements.txt; then
  say "onnxruntime didn't install - continuing without the neural decoder"
  grep -v '^onnxruntime' requirements.txt > /tmp/zbm2-req.txt
  .venv/bin/pip install -q -r /tmp/zbm2-req.txt
fi

say "4/6  Hamlib (rig control)"
# System Hamlib covers the FT-857D, FT-891, IC-7300 and most radios. ZBM2_HAMLIB=latest (or a radio the
# system Hamlib doesn't list, like the FTX-1) gets the newest Hamlib, built into the app's own folder.
# Later updates: Rig Setup > Update Hamlib, or:  ~/zbm2-cw-tool/.venv/bin/python hamlibupd.py
echo "System Hamlib: $(rigctld --version 2>/dev/null | head -1 || echo 'not found')"
if [ "${ZBM2_HAMLIB:-system}" = "latest" ]; then
  sudo apt-get install -y -qq build-essential pkg-config libusb-1.0-0-dev
  .venv/bin/python hamlibupd.py
else
  echo "Need the newest Hamlib (e.g. Yaesu FTX-1)? Rig Setup > Update Hamlib inside the app."
fi

say "5/6  Serial port + audio permissions"
sudo usermod -aG dialout,audio "$USER"

say "6/6  Desktop launcher"
chmod +x start.sh
mkdir -p "$HOME/.local/share/applications"
cat > "$HOME/.local/share/applications/zbm2-cw-tool.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=ZBM2 CW Tool
Comment=CW for people who can't copy CW yet
Exec=$DEST/start.sh
Path=$DEST
Icon=$DEST/docs/logo.svg
Terminal=false
Categories=HamRadio;Utility;
EOF
if [ -d "$HOME/Desktop" ]; then
  cp "$HOME/.local/share/applications/zbm2-cw-tool.desktop" "$HOME/Desktop/"
  chmod +x "$HOME/Desktop/zbm2-cw-tool.desktop"
fi

say "Checking the decoder"
.venv/bin/python - <<'PY'
import deepcw
print('Neural decoder:', 'ready' if deepcw.available() else 'not available (built-in decoder will be used)')
PY

say "Done. Log out and back in once (for serial-port access), then start ZBM2 CW Tool from the menu."
echo "Plug the radio's USB cable in first; the Setup Wizard opens on the first run."
