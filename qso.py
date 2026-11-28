"""QSO assistant: reads the decoded stream, pulls out call / RST / name /
QTH / grid, tracks where the QSO is, and proposes the next reply."""
import math
import os
import re
import datetime as dt

CALL_RE = r'(?:[A-Z0-9]{1,4}/)?[A-Z0-9]{1,3}\d{1,2}[A-Z]{1,4}(?:/[A-Z0-9]{1,4})?'
GRID_RE = re.compile(r'\b([A-R]{2}\d{2}(?:[A-X]{2})?)\b')
NOT_CALLS = {'5NN', '599', 'R5NN'}
NOT_NAMES = {'IS', 'HR', 'HERE', 'ES', 'OP', 'AND', 'K', 'KN', 'BK', 'UR', 'FB'}


# ---------------- grid math ----------------
def grid_to_latlon(g):
    g = g.upper()
    lon = (ord(g[0]) - 65) * 20 - 180 + int(g[2]) * 2
    lat = (ord(g[1]) - 65) * 10 - 90 + int(g[3])
    if len(g) >= 6:
        lon += (ord(g[4]) - 65) * (5 / 60) + 2.5 / 60
        lat += (ord(g[5]) - 65) * (2.5 / 60) + 1.25 / 60
    else:
        lon += 1
        lat += 0.5
    return lat, lon


def dist_bearing(g1, g2):
    la1, lo1 = map(math.radians, grid_to_latlon(g1))
    la2, lo2 = map(math.radians, grid_to_latlon(g2))
    d = 2 * 6371 * math.asin(math.sqrt(
        math.sin((la2 - la1) / 2) ** 2 +
        math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2))
    y = math.sin(lo2 - lo1) * math.cos(la2)
    x = math.cos(la1) * math.sin(la2) - math.sin(la1) * math.cos(la2) * math.cos(lo2 - lo1)
    return d, (math.degrees(math.atan2(y, x)) + 360) % 360


def band_for(freq):
    mhz = (freq or 0) / 1e6
    for lo, hi, b in [(1.8, 2, '160M'), (3.5, 4, '80M'), (5.3, 5.41, '60M'),
                      (7, 7.3, '40M'), (10.1, 10.15, '30M'), (14, 14.35, '20M'),
                      (18.068, 18.168, '17M'), (21, 21.45, '15M'),
                      (24.89, 24.99, '12M'), (28, 29.7, '10M'), (50, 54, '6M'),
                      (144, 148, '2M'), (420, 450, '70CM')]:
        if lo <= mhz <= hi:
            return b
    return ''


# CW-legal segments (MHz) by US license class, Part 97.301/97.305
_EXTRA = [(1.8, 2.0), (3.5, 4.0), (7.0, 7.3), (10.1, 10.15), (14.0, 14.35),
          (18.068, 18.168), (21.0, 21.45), (24.89, 24.99), (28.0, 29.7)]
_GENERAL = [(1.8, 2.0), (3.525, 3.6), (3.8, 4.0), (7.025, 7.125), (7.175, 7.3),
            (10.1, 10.15), (14.025, 14.15), (14.225, 14.35), (18.068, 18.168), (21.025, 21.2),
            (21.275, 21.45), (24.89, 24.99), (28.0, 29.7)]
_TECH = [(3.525, 3.6), (7.025, 7.125), (21.025, 21.2), (28.0, 28.5)]
_VHF_UP = [(50, 54), (144, 148), (219, 225), (420, 450), (902, 928), (1240, 1300)]
LICENSE_CLASSES = ['Technician', 'General', 'Amateur Extra']
CH60_CW = (5332000, 5348000, 5358500, 5373000, 5405000)   # 97.303(h): CW on the channel centre


BAND_EDGES = {'160M': (1.8, 2.0), '80M': (3.5, 4.0), '60M': (5.3305, 5.4065), '40M': (7.0, 7.3),
              '30M': (10.1, 10.15), '20M': (14.0, 14.35), '17M': (18.068, 18.168), '15M': (21.0, 21.45),
              '12M': (24.89, 24.99), '10M': (28.0, 29.7), '6M': (50.0, 54.0)}


def band_start(band, lic):
    """Bottom of your CW privileges on a band (1 kHz inside the edge). -> (Hz, can_transmit)"""
    if band == '60M':
        ok = lic in ('General', 'Amateur Extra')
        return CH60_CW[0], ok
    lo_b, hi_b = BAND_EDGES[band]
    segs = {'Technician': _TECH, 'General': _GENERAL}.get(lic, _EXTRA) + _VHF_UP
    inside = sorted(lo for lo, hi in segs if lo_b <= lo < hi_b)
    if inside:
        return int(round(inside[0] * 1e6)) + 1000, True
    return int(round(lo_b * 1e6)) + 1000, False            # no TX privileges here: listen at the bottom


def cw_allowed(freq_hz, lic):
    """(ok, message). Unknown frequency -> allowed (no CAT)."""
    if not freq_hz:
        return True, ''
    mhz = freq_hz / 1e6
    if 5.3 < mhz < 5.41:                            # 60 m: channels; CW must sit on the channel centre
        if lic in ('General', 'Amateur Extra') and any(abs(freq_hz - c) <= 100 for c in CH60_CW):
            return True, ''
        return False, (f'{mhz:.4f} MHz: on 60 m, CW goes exactly on a channel centre '
                       '(5.3320, 5.3480, 5.3585, 5.3730, 5.4050 MHz), General and up.')
    segs = {'Technician': _TECH, 'General': _GENERAL}.get(lic, _EXTRA) + _VHF_UP
    for lo, hi in segs:
        if lo + 0.0005 <= mhz <= hi - 0.0005:       # keep ~500 Hz off the edge
            return True, ''
    tech = ', '.join(f'{lo:.3f}-{hi:.3f}' for lo, hi in _TECH)
    hint = f' Technician CW on HF: {tech} MHz.' if lic == 'Technician' else ''
    return False, f'{mhz:.4f} MHz is outside {lic} CW privileges.{hint}'


def rst_from_snr(snr_db):
    s = 9 if snr_db > 30 else 8 if snr_db > 25 else 7 if snr_db > 20 else \
        6 if snr_db > 16 else 5 if snr_db > 12 else 4 if snr_db > 9 else 3
    return f'5{s}9'


# ---------------- QSO state ----------------
class QSO:
    def __init__(self, my_call):
        self.my_call = my_call.upper()
        self.expert = False          # operator keys with own paddle: we never see what was sent
        self.reset()

    def reset(self):
        self.call = ''
        self.rst_rcvd = ''
        self.name = ''
        self.qth = ''
        self.grid = ''
        self.state = 'idle'          # idle | cq_heard | called_me | in_qso | ending
        self.sent_report = False
        self.sent_73 = False
        self.they_ended = False
        self.want_agn = False
        self.time_on = None
        self.text = ''               # decoded text since this QSO began
        self.last_tx = ''

    # feed decoded text; returns set of field names that changed
    def feed(self, chunk):
        self.text = (self.text + chunk)[-1500:]
        done = self.text[:max(self.text.rfind(' '), 0)]      # only completed words
        t = ' ' + ' '.join(done.split()) + ' '
        # tight word spacing glues DE onto the call ("DEW6JL"): pry it apart before parsing
        t = re.sub(rf'\bDE(?={CALL_RE}\b)', 'DE ', t)
        changed = set()
        my = re.escape(self.my_call)

        def setf(k, v):
            if v and getattr(self, k) != v:
                setattr(self, k, v)
                changed.add(k)

        # who is calling whom
        m = list(re.finditer(rf'\b({my})\s+(?:DE\s+)?({CALL_RE})\b', t))
        m = [x for x in m if x.group(2) != self.my_call and x.group(2) not in NOT_CALLS]
        if m:
            c = m[-1].group(2)
            if c != self.call and self.call and self.state in ('in_qso', 'called_me'):
                pass                     # keep current partner; ignore tail-enders
            else:
                if c != self.call:
                    self._new_partner(c)
                    changed.add('call')
                if self.state in ('idle', 'cq_heard'):
                    self.state = 'called_me'
                    changed.add('state')
        if self.state in ('idle', 'cq_heard'):
            cq = list(re.finditer(rf'\bCQ\b(?:\s+(?:CQ|DX|POTA|SOTA|TEST|NA))*.*?\bDE\s+({CALL_RE})\b', t))
            if cq:
                c = cq[-1].group(1)
                if c != self.my_call and c != self.call:
                    self._new_partner(c)
                    self.state = 'cq_heard'
                    changed |= {'call', 'state'}

        if not self.call:
            return changed
        r = re.findall(r'\b(?:RST|UR)\s+(?:IS\s+)?([1-5][1-9N][1-9N])\b', t) or \
            re.findall(r'\b(5NN|5[1-9]9|5[1-9]N)\b', t)
        if r:
            setf('rst_rcvd', r[-1].replace('N', '9'))
        n = re.findall(r'\b(?:NAME|OP)\s+(?:IS\s+|HR\s+)*([A-Z]{2,12})\b', t)
        n = [x for x in n if x not in NOT_NAMES]
        if n:
            setf('name', n[-1])
        q = re.findall(r'\bQTH\s+(?:IS\s+|HR\s+)*([A-Z][A-Z ]{1,20}?)(?=\s+(?:NR|GRID|LOC|RIG|ANT|WX|HW|BK|KN|K|PWR|=|<)\b|\s*$)', t)
        if q:
            setf('qth', q[-1].strip())
        g = [x for x in GRID_RE.findall(t) if not re.fullmatch(CALL_RE, x)]
        if g:
            setf('grid', g[-1])

        if self.expert and self.rst_rcvd:
            self.sent_report = True      # reports go both ways; assume the operator sent theirs
        if self.state in ('cq_heard', 'called_me') and (self.rst_rcvd or self.sent_report):
            self.state = 'in_qso'
            changed.add('state')
        if re.search(r'\b(73|SK|<SK>|TU|GL|CUL)\b', t[-80:]) and self.sent_report:
            if not self.they_ended:
                self.they_ended = True
                changed.add('state')
        # they want a repeat: AGN / RPT / a lone "?" near the end (not "HW?" or "QTH?")
        self.want_agn = bool(re.search(r'(?:^|\s)(AGN\??|RPT|PSE RPT|\?)(?:\s+\S+){0,2}\s*$', t[-25:])) \
            and bool(self.last_tx)
        return changed

    def _new_partner(self, c):
        try:
            import callfix
            fx = callfix.default()
            fx.note(c)
            c = fx.fix(c)[0]
        except Exception:
            pass
        keep_text = self.text
        self.reset()
        self.text = keep_text
        self.call = c
        self.time_on = dt.datetime.now(dt.timezone.utc)

    def note_sent(self, text):
        self.last_tx = text
        u = text.upper()
        if 'RST' in u or ' UR ' in f' {u} ':
            self.sent_report = True
            if self.state in ('cq_heard', 'called_me'):
                self.state = 'in_qso'
        if '73' in u or '<SK>' in u or ' TU ' in f' {u} ':
            self.sent_73 = True
        self.text = ''   # start fresh parse window after our transmission

    # ---------------- replies ----------------
    def suggest(self, cfg, rst_out):
        my, c = self.my_call, self.call
        if self.want_agn:
            return self.last_tx
        if self.state == 'idle' or not c:
            return macro('cq', cfg, self, rst_out)
        if self.state == 'cq_heard':
            return f'{c} DE {my} {my} K'
        if not self.sent_report:
            return macro('report', cfg, self, rst_out)
        if not self.sent_73:
            return macro('73', cfg, self, rst_out)
        return f'{c} DE {my} <SK> EE'


def _greeting():
    h = dt.datetime.now().hour
    return 'GM' if h < 12 else 'GA' if h < 18 else 'GE'


def macro(kind, cfg, q, rst_out):
    my = cfg.get('call', '').upper()
    c = q.call or '???'
    name = cfg.get('name', '').upper()
    qth = cfg.get('qth', '').upper()
    grid = cfg.get('grid', '').upper()
    nm = f' {q.name}' if q.name else ''
    r_ack = 'R ' if q.rst_rcvd else ''
    if kind == 'cq':
        return f'CQ CQ CQ DE {my} {my} {my} K'
    if kind == 'answer':
        return f'{c} DE {my} {my} K'
    if kind == 'report':
        thanks = 'TNX FER CALL' if q.state == 'called_me' else 'TNX FER RPRT' if q.rst_rcvd else 'TNX'
        loc = f' QTH {qth}' if qth else ''
        gr = f' GRID {grid}' if grid else ''
        return (f'{c} DE {my} {r_ack}{_greeting()}{nm} {thanks} UR RST {rst_out} {rst_out}'
                f' NAME {name} {name}{loc}{gr} HW? {c} DE {my} <KN>')
    if kind == '73':
        return f'{c} DE {my} R TNX FER QSO{nm} 73 ES GL {c} DE {my} TU <SK>'
    if kind == 'agn':
        return 'AGN? AGN?'
    if kind == 'qrs':
        return 'QRS PSE QRS'
    if kind == 'call':
        return f'{my} {my}'
    return ''


# ---------------- logging ----------------
def adif_record(q, cfg, freq, rst_sent):
    now = dt.datetime.now(dt.timezone.utc)
    on = q.time_on or now
    f = {
        'CALL': q.call, 'QSO_DATE': on.strftime('%Y%m%d'), 'TIME_ON': on.strftime('%H%M%S'),
        'QSO_DATE_OFF': now.strftime('%Y%m%d'), 'TIME_OFF': now.strftime('%H%M%S'),
        'MODE': 'CW', 'FREQ': f'{(freq or 0) / 1e6:.6f}', 'BAND': band_for(freq),
        'RST_SENT': rst_sent, 'RST_RCVD': q.rst_rcvd, 'NAME': q.name, 'QTH': q.qth,
        'GRIDSQUARE': q.grid, 'STATION_CALLSIGN': cfg.get('call', '').upper(),
        'MY_GRIDSQUARE': cfg.get('grid', '').upper(),
    }
    rec = ''.join(f'<{k}:{len(v)}>{v} ' for k, v in f.items() if v)
    return rec + '<EOR>\n', on, now


def append_log(path, record):
    new = not os.path.exists(path)
    with open(path, 'a', encoding='utf-8') as fh:
        if new:
            fh.write('ZBM2 CW Tool log\n<ADIF_VER:5>3.1.4 <PROGRAMID:7>ZBM2-CW <EOH>\n')
        fh.write(record)


def worked_before(path, call):
    """Return list of (date, band) previously logged for call."""
    if not call or not os.path.exists(path):
        return []
    out = []
    for rec in open(path, encoding='utf-8').read().upper().split('<EOR>'):
        m = re.search(r'<CALL:\d+>(\S+)', rec)
        if m and m.group(1) == call.upper():
            d = re.search(r'<QSO_DATE:\d+>(\d+)', rec)
            b = re.search(r'<BAND:\d+>(\S+)', rec)
            out.append((d.group(1) if d else '', b.group(1) if b else ''))
    return out
