# Privacy

ZBM2 CW Tool has no telemetry, no accounts and no analytics. It talks to the internet only for the features below, and each one can be turned off: in **Rig Setup** (Extras), or with the checkboxes in the Windows installer.

| Feature | Goes to | What is sent | On by default | Turn off |
|---|---|---|---|---|
| Update check (startup) | github.com (api.github.com) | A request for the latest release (your IP address and the app version, like any web request) | Yes | Rig Setup: "Tell me when a new version is out" |
| Band conditions | hamqsl.com (N0NBH) | A request for the solar / band data (IP address) | Yes | Rig Setup: "HF band conditions" |
| Live CW activity (SPOTS) | telnet.reversebeacon.net | Your **callsign** as the login name (public information; no password) | Yes, once you enter a callsign | Rig Setup: "Live CW activity" |
| Update Hamlib / Update app | github.com | Downloads, only when you click | - | Don't click |
| Callsign lookups | qrz.com or hamqth.com | The call being looked up, with **your** login | Only if you enter a login | Leave the login empty |
| Logbook uploads | QRZ Logbook / LoTW (through your TQSL) | Your QSO records | Only if you turn it on | Off by default |
| GridTracker | 127.0.0.1 (this computer) | Status and logged QSOs | Yes (local only) | Rig Setup |

Passwords and API keys you enter are kept in your operating system's credential store (Windows Credential Manager / Secret Service), not sent anywhere except to the service they belong to.

Your settings, log and recordings stay on your computer (Windows: `%APPDATA%\ZBM2 CW Tool`; source / uConsole installs: the app folder).

The third-party services above have their own privacy policies: [GitHub](https://docs.github.com/site-policy/privacy-policies/github-general-privacy-statement), [Reverse Beacon Network](https://www.reversebeacon.net/), [hamqsl.com](https://www.hamqsl.com/), [QRZ.com](https://www.qrz.com/page/privacy.html), [HamQTH](https://www.hamqth.com/), [ARRL LoTW](https://www.arrl.org/privacy-policy).
