"""Online services: QRZ XML + HamQTH lookups, QRZ Logbook upload, LoTW (TQSL) upload + confirmations.

All calls are blocking; the app runs them on worker threads.
"""
import datetime as dt
import os
import re
import subprocess
import tempfile
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

AGENT = 'ZBM2CWTool1.0'
TIMEOUT = 10


def _get(url, data=None):
    req = urllib.request.Request(url, data=data, headers={'User-Agent': AGENT})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.read().decode('utf-8', errors='replace')


def _strip_ns(xml_text):
    return re.sub(r'\sxmlns="[^"]+"', '', xml_text, count=1)


# ---------------- QRZ XML ----------------
class QRZ:
    URL = 'https://xmldata.qrz.com/xml/current/'

    def __init__(self, user, password):
        self.user, self.password, self.key = user, password, None
        self.cache = {}
        self.last_error = ''

    def login(self):
        q = urllib.parse.urlencode({'username': self.user, 'password': self.password, 'agent': AGENT})
        root = ET.fromstring(_strip_ns(_get(f'{self.URL}?{q}')))
        self.key = root.findtext('Session/Key')
        if not self.key:
            raise RuntimeError(root.findtext('Session/Error') or 'QRZ login failed')
        sub = root.findtext('Session/SubExp') or ''
        return sub          # subscription expiry, or 'non-subscriber'

    def lookup(self, call):
        call = call.upper()
        if call in self.cache:
            return self.cache[call]
        if not self.key:
            self.login()
        q = urllib.parse.urlencode({'s': self.key, 'callsign': call})
        root = ET.fromstring(_strip_ns(_get(f'{self.URL}?{q}')))
        err = root.findtext('Session/Error') or ''
        if 'Session Timeout' in err or 'Invalid session key' in err:
            self.key = None
            return self.lookup(call)
        c = root.find('Callsign')
        if c is None:
            self.last_error = err
            return {}
        d = parse_qrz_callsign(c)
        self.cache[call] = d
        return d


def parse_qrz_callsign(c):
    g = lambda k: (c.findtext(k) or '').strip()
    first = g('nickname') or g('fname').split(' ')[0]
    return {k: v for k, v in dict(
        call=g('call'), name=first.upper(), full_name=f"{g('fname')} {g('name')}".strip(),
        qth=' '.join(x for x in (g('addr2'), g('state')) if x).upper(), country=g('country'),
        grid=g('grid').upper(), lat=g('lat'), lon=g('lon'), image=g('image'), cls=g('class'),
        lotw=g('lotw'), source='QRZ').items() if v}


# ---------------- HamQTH (free) ----------------
class HamQTH:
    URL = 'https://www.hamqth.com/xml.php'

    def __init__(self, user, password):
        self.user, self.password, self.sid = user, password, None
        self.cache = {}

    def login(self):
        q = urllib.parse.urlencode({'u': self.user, 'p': self.password})
        root = ET.fromstring(_strip_ns(_get(f'{self.URL}?{q}')))
        self.sid = root.findtext('session/session_id')
        if not self.sid:
            raise RuntimeError(root.findtext('session/error') or 'HamQTH login failed')

    def lookup(self, call):
        call = call.upper()
        if call in self.cache:
            return self.cache[call]
        if not self.sid:
            self.login()
        q = urllib.parse.urlencode({'id': self.sid, 'callsign': call, 'prg': AGENT})
        root = ET.fromstring(_strip_ns(_get(f'{self.URL}?{q}')))
        err = root.findtext('session/error') or ''
        if 'expired' in err.lower() or 'session' in err.lower() and 'not' in err.lower():
            self.sid = None
            return self.lookup(call)
        s = root.find('search')
        if s is None:
            return {}
        d = parse_hamqth_search(s)
        self.cache[call] = d
        return d


def parse_hamqth_search(s):
    g = lambda k: (s.findtext(k) or '').strip()
    first = g('nick') or g('adr_name').split(' ')[0]
    return {k: v for k, v in dict(
        call=g('callsign').upper(), name=first.upper(), full_name=g('adr_name'),
        qth=g('qth').upper(), country=g('country'), grid=g('grid').upper(), lat=g('latitude'),
        lon=g('longitude'), image=g('picture'), lotw=g('lotw'), source='HamQTH').items() if v}


class Lookup:
    """QRZ first (if configured), HamQTH as fallback."""

    def __init__(self, cfg):
        self.qrz = QRZ(cfg['qrz_user'], cfg['qrz_pass']) if cfg.get('qrz_user') else None
        self.hq = HamQTH(cfg['hamqth_user'], cfg['hamqth_pass']) if cfg.get('hamqth_user') else None

    def enabled(self):
        return bool(self.qrz or self.hq)

    def lookup(self, call):
        errs = []
        for svc in (self.qrz, self.hq):
            if not svc:
                continue
            try:
                d = svc.lookup(call)
                if d:
                    return d
            except Exception as e:
                errs.append(f'{type(svc).__name__}: {e}')
        if errs:
            raise RuntimeError('; '.join(errs))
        return {}


# ---------------- QRZ Logbook ----------------
def qrz_logbook_upload(api_key, adif_record):
    data = urllib.parse.urlencode({'KEY': api_key, 'ACTION': 'INSERT', 'ADIF': adif_record}).encode()
    resp = _get('https://logbook.qrz.com/api', data=data)
    fields = dict(urllib.parse.parse_qsl(resp))
    if fields.get('RESULT') not in ('OK', 'REPLACE'):
        raise RuntimeError(fields.get('REASON') or resp[:120])
    return fields.get('LOGID', '')


# ---------------- LoTW ----------------
def find_tqsl(hint=''):
    for p in [hint, r'C:\Program Files (x86)\TrustedQSL\tqsl.exe', r'C:\Program Files\TrustedQSL\tqsl.exe',
              '/usr/bin/tqsl', '/usr/local/bin/tqsl', '/Applications/TrustedQSL/tqsl.app/Contents/MacOS/tqsl']:
        if p and os.path.isfile(p):
            return p
    return ''


def tqsl_command(tqsl, adif_path, location):
    # -d no date dialog, -u upload, -a compliant (skip dupes), -l station location, -x batch exit (last)
    return [tqsl, '-d', '-u', '-a', 'compliant', '-l', location, adif_path, '-x']


def lotw_upload(tqsl, location, adif_records):
    """Sign + upload with the user's TQSL certificate. -> (ok, message)"""
    if not tqsl:
        return False, 'TQSL not found - install TrustedQSL and set its path in Rig Setup > Online'
    fd, path = tempfile.mkstemp(suffix='.adi', prefix='zbm2cw_lotw_')
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        f.write('<ADIF_VER:5>3.1.4 <PROGRAMID:7>ZBM2-CW <EOH>\n' + ''.join(adif_records))
    try:
        cp = subprocess.run(tqsl_command(tqsl, path, location), capture_output=True, text=True, timeout=120,
                            creationflags=0x08000000 if os.name == 'nt' else 0)
        msg = (cp.stdout + cp.stderr).strip()[-200:]
        codes = {0: 'uploaded', 8: 'nothing new (already uploaded)', 9: 'uploaded (some were duplicates)',
                 1: 'cancelled', 2: 'rejected by LoTW', 4: 'TQSL error (certificate / station location?)',
                 10: 'command error', 11: 'no connection to LoTW'}
        ok = cp.returncode in (0, 8, 9)
        return ok, codes.get(cp.returncode, msg or f'tqsl exit {cp.returncode}')
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def lotw_confirmations(user, password, since=None):
    """-> set of (CALL, BAND) confirmed on LoTW."""
    since = since or (dt.date.today() - dt.timedelta(days=3650)).isoformat()
    q = urllib.parse.urlencode({'login': user, 'password': password, 'qso_query': 1, 'qso_qsl': 'yes',
                                'qso_qslsince': since})
    text = _get(f'https://lotw.arrl.org/lotwuser/lotwreport.adi?{q}')
    if '<EOH>' not in text.upper():
        raise RuntimeError('LoTW login failed (check username/password)')
    return parse_lotw_adif(text)


def parse_lotw_adif(text):
    out = set()
    for rec in re.split(r'<eor>', text, flags=re.I):
        c = re.search(r'<CALL:\d+>([^<\s]+)', rec, re.I)
        b = re.search(r'<BAND:\d+>([^<\s]+)', rec, re.I)
        if c:
            out.add((c.group(1).upper(), (b.group(1) if b else '').upper()))
    return out
