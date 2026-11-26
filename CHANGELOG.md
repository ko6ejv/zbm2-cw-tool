## ZBM2 CW Tool 3.4.2

- Live dit/dah strip drawn on the spectrum (transparent), letters spelled out above.

## ZBM2 CW Tool 3.4.1

- Startup update check (GitHub) with an orange NEW VERSION button; one-click update (Windows installer runs silently and reopens the app; git installs pull + restart).
- HELP > Check for updates; Rig Setup toggle.
- Stable download link: releases/latest/download/ZBM2-CW-Tool-Setup.exe

## ZBM2 CW Tool 3.4.0

- BAND list (CAT band change with per-band memory) and TUNE button (CAT tuner; carrier fallback for autotuners).
- HF band conditions as day/night dots (N0NBH), refreshed every 30 min.
- Live dit/dah strip over the spectrum: orange RX, red TX.
- Station line shows the real keying and PTT.

## ZBM2 CW Tool 3.3.1 - uConsole CM5

- uConsole CM5 support: installer reports board/OS, checks the CM5 overlay, warns on a CM5 without Wi-Fi.
- CI runs the installer and tests on ARM64 Debian Bookworm and Trixie.
- Neural decoder threads scale with the CPU; `neural_threads` override.

## ZBM2 CW Tool 3.3.0 - uConsole edition

**CW for people who can't copy CW yet. Putting competent operators on the net.**

### New
- **Neural CW decoder (DeepCW).** Copies 97% at -6 dB and 90% at -9 dB S/N on real band noise (the old decoder: 66% / 46%). Locks to the station you pick; silent on an empty frequency.
- **ClockworkPi uConsole (CM4) / Raspberry Pi.** One-line installer, automatic compact layout for 1280x720, Ctrl+1...8 instead of F-keys. Tested on ARM64 on every change.
- **Pick your radio** in the Setup Wizard: Yaesu FTX-1, FT-891, FT-857D, FT-817/818, FT-991A, Elecraft KX3, Xiegu G90, X6100, X6200, G7250*, Icom IC-7300, IC-705 - model, speed, CW keying and sound card filled in, plus a hookup note.
- **Update Hamlib** from inside the app (Rig Setup, or the wizard): newest official release, SHA-256 checked, installed in the app's own folder. No admin rights.
- **License guard** checkbox on the main screen (red GUARD OFF marker when off).
- **Emergency mode** (FCC 97.403 / 97.405): one red button lifts the callsign and license-guard blocks so anyone can transmit. Never stays on after a restart; the wizard offers it to people without a callsign.
- **HELP** button with a one-page quick start; CLEAR wipes everything; sounds off except the ZBM2 startup.
- New smooth skull logo everywhere.

### Fixed
- Startup crash while the decoder model loads; bottom controls cut off on short screens; tight keyer/bug letter spacing; speed lock-in on a new station.

\* The G7250 isn't in Hamlib yet: it starts as the X6200 (CI-V) - use Update Hamlib once it's added.

**Install:** Windows - download the Setup .exe below. uConsole / Raspberry Pi OS 64-bit:
```
curl -fsSL https://raw.githubusercontent.com/ko6ejv/zbm2-cw-tool/main/install_uconsole.sh | bash
```
