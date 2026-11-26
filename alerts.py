"""Copy-quality monitor: works out WHY copy is bad and what to ask for.

Each alert = Q-code (or abbreviation) + formal definition + plain English
+ the reply to pre-load into NEXT. The UI turns it into a DOS banner and a
Morse earcon.
"""
import math
import time
from dataclasses import dataclass, field

import numpy as np

from qcodes import define


@dataclass
class Alert:
    code: str
    plain: str
    action: str
    reply: str
    severity: str = 'warn'          # 'warn' (amber) | 'block' (red) | 'info'
    label: str = ''
    definition: str = ''
    t: float = field(default_factory=time.time)

    def __post_init__(self):
        d = define(self.code)
        if d and not self.definition:
            self.label, self.definition = d


SPECS = {
    'QRM': ('Another station is on top of them.', 'ask for a repeat', 'SRI QRM PSE RPT'),
    'QRN': ('Static crashes are covering their signal.', 'ask for a repeat', 'QRN HR PSE RPT'),
    'QSB': ('Their signal is fading in and out.', 'ask for a repeat', 'QSB PSE RPT'),
    'QRO': ('Their signal is very weak.', 'ask for more power', 'UR SIG WEAK PSE QRO'),
    'QRS': ("They're sending faster than we can copy.", 'ask them to slow down', 'PSE QRS'),
    'AGN': ('Copy is shaky - we missed some of that.', 'ask them to say it again', 'PSE AGN'),
}


def make(code, **over):
    plain, action, reply = SPECS[code]
    kw = dict(code=code, plain=plain, action=action, reply=reply)
    kw.update(over)
    return Alert(**kw)


class CopyMonitor:
    HOLD = 2.0       # condition must persist this long to alert
    CLEAR = 4.0      # ...and be gone this long to clear
    COOLDOWN = 25.0  # don't repeat the same alert sooner than this

    def __init__(self):
        self.since = {}       # code -> first time condition seen
        self.gone = {}        # code -> first time condition absent
        self.active = {}      # code -> Alert
        self.last_fired = {}
        self.crests = []      # (t, crest) of recent audio blocks
        self.confidence = 'idle'

    def feed_block(self, x):
        """Raw audio block -> impulsive-noise tracking (QRN)."""
        rms = float(np.sqrt(np.mean(x * x))) + 1e-12
        crest = float(np.max(np.abs(x))) / rms
        now = time.time()
        self.crests.append((now, crest))
        while self.crests and now - self.crests[0][0] > 3.0:
            self.crests.pop(0)

    def conditions(self, dec, fr=None, db=None):
        c = set()
        if not dec or not dec.keying_recent or not getattr(dec, 'confident', True):
            return c
        sn = getattr(dec, 'sn', None)
        if sn is not None and sn < -4:
            c.add('QRO')
        if len(dec.mark_levels) >= 8:
            lv = [v for v in dec.mark_levels[-10:] if v > 0]
            if lv and 20 * math.log10(max(lv) / max(min(lv), 1e-9)) > 10:
                c.add('QSB')
        if fr is not None and db is not None and len(fr):
            t = dec.tone
            main = (fr > t - 30) & (fr < t + 30)
            side = (np.abs(fr - t) > 60) & (np.abs(fr - t) < 300)
            if main.any() and side.any():
                floor = float(np.median(db))
                mp, sp = float(db[main].max()), float(db[side].max())
                if sp > mp - 8 and sp > floor + 15:
                    c.add('QRM')
        spikes = sum(1 for _, k in self.crests if k > 7.0)
        if spikes >= 6:
            c.add('QRN')
        if dec.emitted >= 15 and dec.unknown_ratio > 0.15 and dec.wpm > 22:
            c.add('QRS')
        elif dec.emitted >= 15 and dec.unknown_ratio > 0.2 and not c:
            c.add('AGN')
        return c

    def update(self, dec, fr=None, db=None):
        """-> (new_alerts, cleared_codes)"""
        now = time.time()
        cond = self.conditions(dec, fr, db)
        new, cleared = [], []
        for code in cond:
            self.gone.pop(code, None)
            self.since.setdefault(code, now)
            if (code not in self.active and now - self.since[code] >= self.HOLD
                    and now - self.last_fired.get(code, 0) > self.COOLDOWN):
                a = make(code)
                self.active[code] = a
                self.last_fired[code] = now
                new.append(a)
        for code in list(self.since):
            if code not in cond:
                self.since.pop(code, None)
        for code in list(self.active):
            if code not in cond:
                self.gone.setdefault(code, now)
                if now - self.gone[code] >= self.CLEAR:
                    self.active.pop(code)
                    self.gone.pop(code, None)
                    cleared.append(code)
        # confidence meter
        if not dec or not dec.keying_recent or not getattr(dec, 'confident', True):
            self.confidence = 'idle'
        elif (getattr(dec, 'sn', None) is not None and dec.sn < -5) or dec.unknown_ratio > 0.25:
            self.confidence = 'lost'
        elif (getattr(dec, 'sn', None) is not None and dec.sn < 0) or dec.unknown_ratio > 0.06 or cond:
            self.confidence = 'shaky'
        else:
            self.confidence = 'good'
        return new, cleared


def missing_fields_alert(q):
    """After their over: ask only for what we still don't have."""
    want = []
    if q.state in ('in_qso',) and q.rst_rcvd:
        if not q.name:
            want.append('NAME?')
        if not q.qth:
            want.append('QTH?')
    if not want:
        return None
    what = ' and '.join(w.rstrip('?').replace('QTH', 'location').lower() for w in want)
    return Alert(code='AGN', plain=f'We missed their {what}.', action=f'ask for just their {what}',
                 reply='PSE AGN ' + ' '.join(want))


def license_block_alert(why):
    return Alert(code='QSY', plain=why, action='change frequency (QSY) before transmitting',
                 reply='', severity='block')
