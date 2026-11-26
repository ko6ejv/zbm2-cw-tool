"""Chat mode model ("ZBM2 Online"): room = frequency, buddy list = stations heard,
profile facts pulled out of the rag-chew, per-callsign chat logs."""
import datetime as dt
import os
import re
import time
from dataclasses import dataclass, field

from plain import is_call, SEGMENT_END

FACTS = [  # (field, regex) - value is group 1
    ('name', r'\b(?:NAME|OP)\s+(?:HR\s+|IS\s+)*([A-Z]{2,12})\b'),
    ('qth', r'\bQTH\s+(?:HR\s+|IS\s+)*([A-Z][A-Z ]{1,24}?)(?=\s+(?:ES|RIG|ANT|PWR|WX|NAME|HW|BK|KN|K|=|<)\b|\s*$)'),
    ('rig', r'\bRIG\s+(?:HR\s+|IS\s+)*([A-Z0-9-]{2,12})'),
    ('ant', r'\bANT\s+(?:HR\s+|IS\s+)*([A-Z0-9 -]{2,20}?)(?=\s+(?:ES|RIG|PWR|WX|HW|BK|KN|K|=|<)\b|\s*$)'),
    ('pwr', r'\bPWR\s+(?:HR\s+|IS\s+)*(\d{1,4}\s?W?)'),
    ('wx', r'\bWX\s+(?:HR\s+|IS\s+)*([A-Z0-9 ]{2,24}?)(?=\s+(?:ES|RIG|ANT|PWR|HW|BK|KN|K|=|<)\b|\s*$)'),
    ('age', r'\bAGE\s+(?:HR\s+|IS\s+)*(\d{1,3})\b'),
    ('grid', r'\b(?:GRID|LOC)\s+([A-R]{2}\d{2}(?:[A-X]{2})?)\b'),
]
NOT_NAME = {'IS', 'HR', 'ES', 'OP', 'K', 'KN', 'BK', 'FB', 'UR'}


@dataclass
class Buddy:
    call: str
    first: float = field(default_factory=time.time)
    last: float = field(default_factory=time.time)
    snr: float = 0.0
    facts: dict = field(default_factory=dict)
    worked: bool = False
    confirmed: bool = False
    lookup: dict = field(default_factory=dict)     # QRZ / HamQTH data

    def age(self):
        s = int(time.time() - self.last)
        return f'{s}s' if s < 60 else f'{s // 60}m'


class ChatRoom:
    def __init__(self, my_call, log_dir):
        self.my = my_call.upper()
        self.buddies = {}
        self.partner = ''
        self.seg = ''
        self.log_dir = os.path.join(log_dir, 'chat')
        os.makedirs(self.log_dir, exist_ok=True)

    def heard(self, call, snr=0.0):
        """-> True if this is a new buddy."""
        if call == self.my:
            return False
        b = self.buddies.get(call)
        new = b is None
        if new:
            b = self.buddies[call] = Buddy(call)
        b.last, b.snr = time.time(), snr or b.snr
        return new

    def feed(self, chunk, snr=0.0):
        """Decoded text in. -> (finished_segment or None, sender, new_buddies)"""
        self.seg += chunk
        new = []
        done = self.seg[:self.seg.rfind(' ') + 1]
        for w in done.split():
            if is_call(w) and w != self.my:
                if self.heard(w, snr):
                    new.append(w)
        if SEGMENT_END.search(self.seg):
            seg, self.seg = ' '.join(self.seg.split()), ''
            sender = self.sender_of(seg)
            if sender:
                self.learn(sender, seg)
                if not self.partner and self.my in seg.split():
                    self.partner = sender
            return seg, sender, new
        return None, '', new

    def sender_of(self, seg):
        m = re.findall(r'\bDE\s+((?:[A-Z0-9]{1,4}/)?[A-Z0-9]{1,3}\d{1,2}[A-Z]{1,4}(?:/[A-Z0-9]{1,4})?)', seg)
        m = [c for c in m if c != self.my]
        if m:
            return m[-1]
        return self.partner or ''

    def learn(self, call, seg):
        b = self.buddies.get(call) or self.buddies.setdefault(call, Buddy(call))
        t = ' ' + seg + ' '
        for k, rx in FACTS:
            hits = re.findall(rx, t)
            hits = [h.strip() for h in hits if h.strip() and not (k == 'name' and h in NOT_NAME)]
            if hits:
                b.facts[k] = hits[-1]
        return b

    def live_text(self):
        return self.seg

    def log_line(self, call, who, text):
        if not call:
            call = 'ROOM'
        path = os.path.join(self.log_dir, re.sub(r'[^A-Z0-9]', '_', call) + '.txt')
        with open(path, 'a', encoding='utf-8') as f:
            f.write(f'[{dt.datetime.now():%Y-%m-%d %H:%M}] {who}: {text}\n')

    def history(self, call, n=40):
        path = os.path.join(self.log_dir, re.sub(r'[^A-Z0-9]', '_', call) + '.txt')
        if not os.path.exists(path):
            return []
        with open(path, encoding='utf-8') as f:
            return f.read().splitlines()[-n:]


# popular CW spots by band for Technicians (all inside Tech CW segments)
TECH_SPOTS = [
    ('40m  7.114', 7114000, 'slower CW is common at the top of the Tech 40m segment'),
    ('80m  3.560', 3560000, 'evening / night, regional'),
    ('15m  21.114', 21114000, 'daytime, long distance when the band is open'),
    ('10m  28.050', 28050000, 'daytime, best near solar max'),
]


def quick_fills(cfg):
    """Rag-chew phrases built from the user's profile."""
    out = [('NAME', f"NAME HR {cfg.get('name', '').split()[0] if cfg.get('name') else ''}"),
           ('QTH', f"QTH HR {cfg.get('qth', '')}"),
           ('RIG', f"RIG HR {cfg.get('chat_rig', '') or '???'}"),
           ('ANT', f"ANT HR {cfg.get('chat_ant', '') or '???'}"),
           ('PWR', f"PWR HR {cfg.get('chat_pwr', '') or '100W'}"),
           ('WX', 'WX HR '),
           ('AGE', f"AGE HR {cfg.get('chat_age', '')}"),
           ('LIC', f"LIC {cfg.get('chat_lic', '') or '?'} YRS"),
           ('HW CPY?', 'HW CPY?'),
           ('BTU', 'BTU'),
           ('FB', 'FB OM')]
    return out
