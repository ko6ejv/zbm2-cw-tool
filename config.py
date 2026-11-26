"""Settings: defaults, load/save (no GUI imports - shared by the screens and headless engine)."""
import json
import os
import sys

__version__ = '3.5.2'

HERE = os.path.dirname(os.path.abspath(sys.argv[0]))
if getattr(sys, 'frozen', False):          # installed .exe: Program Files is read-only -> per-user folder
    HERE = os.path.join(os.environ.get('APPDATA') or os.path.expanduser('~'), 'ZBM2 CW Tool')
    os.makedirs(HERE, exist_ok=True)
CFG_PATH = os.path.join(HERE, 'config.json')
RES = getattr(sys, '_MEIPASS', HERE)   # bundled images when built as .exe

DEFAULTS = {
    # station
    'call': '', 'name': '', 'qth': '', 'grid': '',
    'license_class': 'Technician', 'license_guard': True,
    # rig control (CAT)
    'cat_backend': 'hamlib_launch',        # hamlib_launch | hamlib_existing | omnirig | none
    'hamlib_dir': '', 'rig_model': 1051, 'rig_model_name': 'Yaesu FTX-1',
    'cat_port': '', 'cat_baud': 38400, 'cat_databits': 'Default', 'cat_stopbits': 'Default',
    'cat_handshake': 'Default', 'cat_dtr': 'Default', 'cat_rts': 'Default',
    'rigctld_host': '127.0.0.1', 'rigctld_port': 4532, 'omnirig_rig': 1,
    # keying + PTT
    'key_method': 'rigkeyer',              # rigkeyer | serial | audio
    'key_port': '', 'key_line': 'DTR',
    'ptt_method': 'VOX',                   # CAT | RTS | DTR | VOX(none / break-in)
    'ptt_port': '',
    # audio
    'audio_in': '', 'audio_in_ch': 'Left/Mono', 'rx_gain_db': 0, 'audio_out': '', 'audio_out_ch': 'Both',
    'tx_drive': 50, 'sidetone': False, 'sidetone_out': '',
    'ptt_lead_ms': 100, 'ptt_tail_ms': 50, 'hamlib_ptt': 'RIG',
    'setup_done': False, 'profile': '',
    'tone': 700, 'auto_tone': True, 'spec_span': 600, 'plain_english': True,
    # speed
    'turbo': False, 'turbo_wpm': 35,
    'match_wpm': True, 'wpm_offset': 0, 'manual_wpm': 18, 'min_wpm': 8, 'max_wpm': 35,
    # earcons (Morse UI sounds)
    'neural_decoder': True, 'neural_width_hz': 250,
    'update_check': True, 'rbn_enable': True, 'bandcond_enable': True,
    'earcons_on': True, 'earcons_startup_only': True, 'earcon_pitch': 800, 'earcon_vol': 0.25, 'earcon_wpm': 22, 'earcon_speak': False,
    # chat profile (rag-chew quick-fills)
    'chat_rig': '', 'chat_ant': '', 'chat_pwr': '100W', 'chat_age': '', 'chat_lic': '',
    'auto_cq_secs': 45, 'live_typing': True,
    # contest
    'contest_id': 'SST', 'contest_mode': 'run', 'c_name': '', 'c_state': 'CA', 'c_cls': '1D', 'c_sec': 'SDG',
    'c_park': '', 'c_nr': 'CA',
    # simulator
    'sim_scenario': 'answer', 'sim_wpm': 15, 'sim_snr': 15, 'sim_qsb': False, 'sim_qrn': False, 'sim_callers': 3,
    # practice
    'key_input': 'audio', 'key_input_port': 'COM9', 'key_input_line': 'CTS',
    # recorder
    'rec_seconds': 120, 'rec_autosave': False,
    # online
    'qrz_user': '', 'qrz_pass': '', 'qrz_logbook_key': '', 'hamqth_user': '', 'hamqth_pass': '',
    'lotw_user': '', 'lotw_pass': '', 'tqsl_path': '', 'tqsl_location': '',
    'auto_lookup': True, 'auto_qrz_upload': False, 'auto_lotw_upload': False,
    # gridtracker / log
    'gt_enable': True, 'gt_host': '127.0.0.1', 'gt_port': 2237,
    'log_path': os.path.join(HERE, 'zbm2cw_log.adi'),
    'data_dir': os.path.join(HERE, 'data'),
}



SECRET_KEYS = ('qrz_pass', 'qrz_logbook_key', 'hamqth_pass', 'lotw_pass')
KEYRING_MARK = '__keyring__'
SERVICE = 'ZBM2 CW Tool'


def _keyring():
    try:
        import keyring
        from keyring.backends import fail
        if isinstance(keyring.get_keyring(), fail.Keyring):
            return None
        return keyring
    except Exception:
        return None


def _install_options():
    """Choices made in the Windows installer (network features), next to the exe. Defaults only:
    anything changed later in Rig Setup wins."""
    p = os.path.join(os.path.dirname(os.path.abspath(sys.executable if getattr(sys, 'frozen', False)
                                                     else sys.argv[0])), 'install_options.json')
    try:
        with open(p) as f:
            d = json.load(f)
        return {k: bool(v) for k, v in d.items() if k in ('update_check', 'rbn_enable', 'bandcond_enable')}
    except (OSError, ValueError, AttributeError):
        return {}


def load_cfg():
    cfg = dict(DEFAULTS)
    cfg.update(_install_options())
    try:
        with open(CFG_PATH) as f:
            cfg.update(json.load(f))
    except (OSError, ValueError):
        pass
    kr = None
    for k in SECRET_KEYS:                   # passwords live in the OS credential store
        if cfg.get(k) == KEYRING_MARK:
            kr = kr or _keyring()
            try:
                cfg[k] = (kr.get_password(SERVICE, k) if kr else '') or ''
            except Exception:
                cfg[k] = ''
    old = cfg.pop('tx_mode', None)          # migrate v1 config
    if old:
        cfg['key_method'] = {'rigctld': 'rigkeyer'}.get(old, old)
    return cfg


def save_cfg(cfg):
    out = dict(cfg)
    out.pop('emergency', None)              # emergency mode is never saved: off on every start
    kr = _keyring()
    for k in SECRET_KEYS:
        v = cfg.get(k, '')
        if not kr:
            continue                        # no credential store (bare Linux box): stays in config.json
        try:
            if v:
                kr.set_password(SERVICE, k, v)
                out[k] = KEYRING_MARK
            else:
                try:
                    kr.delete_password(SERVICE, k)
                except Exception:
                    pass
        except Exception:
            pass
    tmp = CFG_PATH + '.tmp'                 # write-then-rename: a crash mid-save can't wipe your settings
    with open(tmp, 'w') as f:
        json.dump(out, f, indent=2)
    os.replace(tmp, CFG_PATH)
    if os.name != 'nt':
        try:
            os.chmod(CFG_PATH, 0o600)       # owner-only if passwords had to stay in the file
        except OSError:
            pass

