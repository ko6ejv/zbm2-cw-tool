"""Rig control backends (CAT) + Hamlib discovery.

  HamlibRig  - talks to rigctld. Can launch rigctld itself (like WSJT-X)
               with the radio model / COM port / baud you pick, or attach
               to one you already run.
  OmniRigRig - OmniRig COM server (Windows), Rig 1 or Rig 2.
  NoRig      - no CAT.
All expose: connect(), close(), get_freq(), get_mode(), set_cw_mode(),
set_wpm(), send_text(), stop_morse(), ptt(on), can_send_text, name
"""
import glob
import os
import re
import shutil
import socket
import subprocess
import threading
import time

# ---------------- Hamlib discovery ----------------
FALLBACK_MODELS = [(1, 'Hamlib', 'Dummy'), (2, 'Hamlib', 'NET rigctl'),
                   (1035, 'Yaesu', 'FT-991'), (1022, 'Yaesu', 'FT-857'),
                   (3073, 'Icom', 'IC-7300')]


def find_hamlib_bin(hint=''):
    """Folder containing rigctld(.exe), or ''."""
    exe = 'rigctld.exe' if os.name == 'nt' else 'rigctld'
    if hint and os.path.isfile(os.path.join(hint, exe)):
        return hint
    try:                                   # newest copy installed by Rig Setup > Update Hamlib
        import hamlibupd
        mine = hamlibupd.managed_bins()
        if mine:
            return mine[0]
    except Exception:
        pass
    w = shutil.which('rigctld')
    if w:
        return os.path.dirname(w)
    pats = [r'C:\Program Files\hamlib*\bin', r'C:\Program Files (x86)\hamlib*\bin',
            r'C:\hamlib*\bin', os.path.expanduser(r'~\hamlib*\bin'),
            r'C:\Program Files\WSJT-X\bin', r'C:\WSJT\wsjtx\bin']
    for p in pats:
        for d in sorted(glob.glob(p), reverse=True):
            if os.path.isfile(os.path.join(d, exe)):
                return d
    return ''


def list_hamlib_models(bindir):
    """[(model_no, mfg, model)] from `rigctl -l`, or a short fallback list."""
    exe = os.path.join(bindir, 'rigctl.exe' if os.name == 'nt' else 'rigctl') if bindir else ''
    if exe and os.path.isfile(exe):
        try:
            out = subprocess.run([exe, '-l'], capture_output=True, text=True, timeout=10,
                                 creationflags=_NOWIN).stdout
            rows = []
            for line in out.splitlines():
                m = re.match(r'^\s*(\d+)\s+(.+?)\s{2,}(.+?)\s{2,}', line)
                if m:
                    rows.append((int(m.group(1)), m.group(2).strip(), m.group(3).strip()))
            if rows:
                return rows
        except Exception:
            pass
    return list(FALLBACK_MODELS)


def list_com_ports():
    try:
        from serial.tools import list_ports
        return [(p.device, p.description) for p in sorted(list_ports.comports(),
                key=lambda p: int(re.sub(r'\D', '', p.device) or 0))]
    except Exception:
        return []


_NOWIN = 0x08000000 if os.name == 'nt' else 0     # CREATE_NO_WINDOW


# ---------------- base ----------------
class NoRig:
    name = 'No CAT'
    can_send_text = False

    def connect(self): pass
    def close(self): pass
    def get_freq(self): return None
    def get_mode(self): return (None, None)
    def set_cw_mode(self): return False
    def set_data_mode(self): return False
    def set_freq(self, hz): return False
    def set_wpm(self, wpm): return False
    def send_text(self, text): return False
    def stop_morse(self): return False
    def ptt(self, on): return False


# ---------------- Hamlib / rigctld ----------------
class HamlibRig(NoRig):
    can_send_text = True

    def __init__(self, host='127.0.0.1', port=4532, launch=False, bindir='', model=1,
                 com='COM7', baud=38400, data_bits='Default', stop_bits='Default',
                 handshake='Default', dtr_state='Default', rts_state='Default',
                 ptt_type='RIG', ptt_port='', timeout=2.0):
        self.addr = (host, int(port))
        self.launch = launch
        self.bindir, self.model, self.com, self.baud = bindir, int(model), com, int(baud)
        self.data_bits, self.stop_bits, self.handshake = data_bits, stop_bits, handshake
        self.dtr_state, self.rts_state = dtr_state, rts_state
        self.ptt_type, self.ptt_port = ptt_type, ptt_port
        self.timeout = timeout
        self.sock = None
        self.proc = None
        self.lock = threading.Lock()
        self.name = f'Hamlib {"(launched)" if launch is True else "rigctld"} {host}:{port}'
        self.last_error = ''

    def command_line(self):
        exe = os.path.join(self.bindir, 'rigctld.exe' if os.name == 'nt' else 'rigctld')
        cmd = [exe, '-m', str(self.model), '-r', self.com, '-s', str(self.baud),
               '-t', str(self.addr[1])]
        if self.ptt_type in ('RTS', 'DTR'):         # Hamlib toggles the line on "T 1"
            cmd += ['-P', self.ptt_type, '-p', self.ptt_port or self.com]
        conf = []
        if self.data_bits != 'Default':
            conf.append(f'data_bits={self.data_bits}')
        if self.stop_bits != 'Default':
            conf.append(f'stop_bits={self.stop_bits}')
        if self.handshake != 'Default':
            conf.append(f'serial_handshake={self.handshake}')
        if self.dtr_state != 'Default':
            conf.append(f'dtr_state={self.dtr_state.upper()}')
        if self.rts_state != 'Default':
            conf.append(f'rts_state={self.rts_state.upper()}')
        if conf:
            cmd.append('--set-conf=' + ','.join(conf))
        return cmd

    def _listening(self):
        try:
            socket.create_connection(self.addr, timeout=0.3).close()
            return True
        except OSError:
            return False

    def start_daemon(self):
        if not self.launch or (self.proc and self.proc.poll() is None):
            return
        if self._listening():                     # a rigctld is already up (ours or WSJT-X's): share it
            return
        if self.launch == 'auto' and (self.addr[0] not in ('127.0.0.1', 'localhost') or self.model <= 2
                                      or not self.bindir):
            return                                # remote / not configured: just try to connect
        cmd = self.command_line()
        if not os.path.isfile(cmd[0]):
            raise FileNotFoundError('rigctld not found - set the Hamlib folder in Rig Setup')
        self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     creationflags=_NOWIN)
        for _ in range(40):                       # wait up to 4 s for the port
            if self.proc.poll() is not None:
                msg = self.proc.stdout.read().decode(errors='replace').strip()[-300:]
                raise RuntimeError(f'rigctld exited: {msg or "check COM port / model / baud"}')
            try:
                socket.create_connection(self.addr, timeout=0.2).close()
                return
            except OSError:
                time.sleep(0.1)
        raise TimeoutError('rigctld did not start listening')

    def connect(self):
        self._drop()
        self.start_daemon()
        self.sock = socket.create_connection(self.addr, timeout=self.timeout)
        self._f = self.sock.makefile('rwb', buffering=0)

    def _drop(self):
        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass
        self.sock = None

    def close(self):
        self._drop()
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(2)
            except Exception:
                self.proc.kill()
        self.proc = None

    def cmd(self, line):
        with self.lock:
            if not self.sock:
                self.connect()
            try:
                self._f.write(('+' + line + '\n').encode())
                fields, rprt = {}, None
                while True:
                    raw = self._f.readline()
                    if not raw:
                        raise ConnectionError('rigctld closed connection')
                    s = raw.decode(errors='replace').strip()
                    if s.startswith('RPRT'):
                        rprt = int(s.split()[1])
                        break
                    if ':' in s:
                        k, v = s.split(':', 1)
                        fields[k.strip()] = v.strip()
                    elif s:
                        fields.setdefault('_', []).append(s)   # bare value lines (get_func / get_level)
                return fields, rprt
            except (OSError, ConnectionError):
                self._drop()
                raise

    def get_freq(self):
        f, r = self.cmd('f')
        return int(float(f.get('Frequency', 0))) if r == 0 else None

    def get_mode(self):
        f, r = self.cmd('m')
        return (f.get('Mode'), f.get('Passband')) if r == 0 else (None, None)

    def set_cw_mode(self, passband=500):
        return self.cmd(f'M CW {passband}')[1] == 0

    def set_freq(self, hz):
        return self.cmd(f'F {int(hz)}')[1] == 0

    def set_data_mode(self):
        return self.cmd('M PKTUSB 0')[1] == 0

    def set_wpm(self, wpm):
        return self.cmd(f'L KEYSPD {int(round(wpm))}')[1] == 0

    def send_text(self, text):
        return self.cmd('b ' + text.upper())[1] == 0

    def ensure_breakin(self):
        """Radio keyer only transmits CAT text with break-in on (Yaesu KY etc.). Turn on semi BK-IN."""
        try:
            for f in ('SBKIN', 'FBKIN'):
                if self.cmd('u ' + f)[0].get('_', ['0'])[0] == '1':
                    return True
            return self.cmd('U SBKIN 1')[1] == 0
        except Exception:
            return False

    def stop_morse(self):
        try:
            return self.cmd('\\stop_morse')[1] == 0
        except Exception:
            return False

    def ptt(self, on):
        return self.cmd(f'T {1 if on else 0}')[1] == 0


# ---------------- OmniRig ----------------
PM_RX, PM_TX = 0x00200000, 0x00400000
PM_CW_U, PM_CW_L = 0x00800000, 0x01000000
PM_SSB_U, PM_SSB_L = 0x02000000, 0x04000000
PM_DIG_U, PM_DIG_L = 0x08000000, 0x10000000
PM_AM, PM_FM = 0x20000000, 0x40000000
OMNI_MODES = {PM_CW_U: 'CW', PM_CW_L: 'CWR', PM_SSB_U: 'USB', PM_SSB_L: 'LSB',
              PM_DIG_U: 'DATA-U', PM_DIG_L: 'DATA-L', PM_AM: 'AM', PM_FM: 'FM'}
ST_ONLINE = 4


class OmniRigRig(NoRig):
    """Uses OmniRig (install separately and configure Rig 1/2 inside OmniRig).
    No CW text sending - pair with Serial or Audio keying."""

    def __init__(self, rig_no=1):
        self.rig_no = int(rig_no)
        self.name = f'OmniRig Rig {self.rig_no}'
        self.omni = None
        self.rig = None
        self._tls = threading.local()

    def _r(self):
        # COM objects are per-thread; create one per calling thread
        rig = getattr(self._tls, 'rig', None)
        if rig is None:
            import pythoncom
            import win32com.client
            pythoncom.CoInitialize()
            omni = win32com.client.Dispatch('OmniRig.OmniRigX')
            rig = omni.Rig1 if self.rig_no == 1 else omni.Rig2
            self._tls.omni, self._tls.rig = omni, rig
        return rig

    def connect(self):
        r = self._r()
        if r.Status != ST_ONLINE:
            raise ConnectionError(f'OmniRig: {r.StatusStr} ({r.RigType})')

    def get_freq(self):
        r = self._r()
        if r.Status != ST_ONLINE:
            raise ConnectionError(r.StatusStr)
        return int(r.GetRxFrequency()) or int(r.Freq)

    def get_mode(self):
        return (OMNI_MODES.get(self._r().Mode, '?'), None)

    def set_cw_mode(self):
        self._r().Mode = PM_CW_U
        return True

    def set_freq(self, hz):
        self._r().Freq = int(hz)
        return True

    def set_data_mode(self):
        self._r().Mode = PM_DIG_U
        return True

    def ptt(self, on):
        self._r().Tx = PM_TX if on else PM_RX
        return True


def make_rig(cfg):
    b = cfg['cat_backend']
    if b == 'hamlib_launch':
        return HamlibRig('127.0.0.1', cfg['rigctld_port'], launch=True,
                         bindir=find_hamlib_bin(cfg['hamlib_dir']), model=cfg['rig_model'],
                         com=cfg['cat_port'], baud=cfg['cat_baud'], data_bits=cfg['cat_databits'],
                         stop_bits=cfg['cat_stopbits'], handshake=cfg['cat_handshake'],
                         dtr_state=cfg['cat_dtr'], rts_state=cfg['cat_rts'],
                         ptt_type=cfg.get('hamlib_ptt', 'RIG'), ptt_port=cfg.get('ptt_port', ''))
    if b == 'hamlib_existing':
        # use the running rigctld; if nothing answers, start one with the radio settings
        return HamlibRig(cfg['rigctld_host'], cfg['rigctld_port'], launch='auto',
                         bindir=find_hamlib_bin(cfg.get('hamlib_dir', '')), model=cfg['rig_model'],
                         com=cfg['cat_port'], baud=cfg['cat_baud'], data_bits=cfg['cat_databits'],
                         stop_bits=cfg['cat_stopbits'], handshake=cfg['cat_handshake'],
                         dtr_state=cfg['cat_dtr'], rts_state=cfg['cat_rts'],
                         ptt_type=cfg.get('hamlib_ptt', 'RIG'), ptt_port=cfg.get('ptt_port', ''))
    if b == 'omnirig':
        return OmniRigRig(cfg['omnirig_rig'])
    return NoRig()


def find_digirig():
    """(com_port, audio_label_hint) for a plugged-in DigiRig, or (None, None).
    DigiRig = CP2102 USB-serial + CM108 USB audio ("USB PnP Sound Device")."""
    com = None
    for dev, desc in list_com_ports():
        if 'CP210' in desc.upper() or 'SILICON LABS' in desc.upper():
            com = dev
            break
    return com, 'USB PnP Sound Device'
