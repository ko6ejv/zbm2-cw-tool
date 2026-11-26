"""Raw mode: key it yourself, get graded.

Timing comes from the decoder's element callbacks (your sidetone through the
radio's audio or a mic), or straight from a key wired to a COM port's CTS/DSR
input (most accurate). The grader compares your timing with standard Morse:
dah = 3 dits, gap inside a letter = 1 dit, between letters = 3, between words = 7.
"""
import difflib
import threading
import time

import numpy as np

DRILLS = [
    'CQ CQ CQ DE {my} {my} K',
    '{my}',
    'TNX FER CALL UR RST 599 599',
    'NAME HR {name} {name}',
    'QTH {qth} {qth}',
    '73 TU <SK> EE',
    'PARIS PARIS PARIS',
    'THE QUICK BROWN FOX JUMPS OVER THE LAZY DOG',
    'VVV VVV VVV',
    '5NN 5NN TU',
]


class FistGrader:
    def __init__(self):
        self.reset()

    def reset(self):
        self.marks, self.spaces = [], []
        self.text = ''

    def element(self, kind, ms, level=0.0):
        if kind == 'mark':
            self.marks = (self.marks + [ms])[-200:]
        elif ms < 3000:
            self.spaces = (self.spaces + [ms])[-200:]

    def add_text(self, s):
        self.text = (self.text + s)[-500:]

    def stats(self):
        if len(self.marks) < 6:
            return None
        m = np.log(np.array(self.marks))
        c1, c2 = m.min(), m.max()
        for _ in range(10):
            mid = (c1 + c2) / 2
            a, b = m[m <= mid], m[m > mid]
            if len(a) and len(b):
                c1, c2 = a.mean(), b.mean()
        dits = np.array([x for x in self.marks if np.log(x) <= (c1 + c2) / 2])
        dahs = np.array([x for x in self.marks if np.log(x) > (c1 + c2) / 2])
        if not len(dits) or not len(dahs):
            return None
        dit = float(np.median(dits))
        sp = np.array(self.spaces) if self.spaces else np.array([dit])
        intra = sp[sp < 2 * dit]
        char = sp[(sp >= 2 * dit) & (sp < 5 * dit)]
        word = sp[(sp >= 5 * dit) & (sp < 12 * dit)]
        f = lambda a: float(np.median(a)) / dit if len(a) else None
        cv = lambda a: float(np.std(a) / np.mean(a)) if len(a) > 2 else 0.0
        return dict(char_cv=cv(char) if len(char) > 3 else 1.0, wpm=1200 / dit, dit_ms=dit, dah_ms=float(np.median(dahs)), ratio=float(np.median(dahs)) / dit,
                    intra=f(intra), char=f(char), word=f(word), dit_cv=cv(dits), dah_cv=cv(dahs),
                    n=len(self.marks))

    def grade(self):
        s = self.stats()
        if not s:
            return None, [], s
        score = 100.0
        tips = []
        r = s['ratio']
        score -= min(30, abs(r - 3.0) * 15)
        if r < 2.6:
            tips.append(f'Dahs are short ({r:.1f}x a dit). Hold them longer - aim for 3x.')
        elif r > 3.6:
            tips.append(f'Dahs are long ({r:.1f}x a dit). Shorten them toward 3x.')
        if s['intra']:
            score -= min(20, abs(s['intra'] - 1.0) * 20)
            if s['intra'] > 1.4:
                tips.append('Gaps INSIDE letters are too long - letters break apart (I becomes E E).')
            elif s['intra'] < 0.7:
                tips.append('Elements run together inside letters - let each dit/dah breathe.')
        if s['char']:
            score -= min(15, abs(s['char'] - 3.0) * 6)
            if s['char'] < 2.4:
                tips.append('Letters run together - leave a 3-dit gap between letters.')
            elif s['char'] > 4.5:
                tips.append('Long gaps between letters - fine while learning (Farnsworth), tighten as you improve.')
        if s['word'] and s['word'] < 5:
            tips.append('Words run together - leave a longer pause (7 dits) between words.')
        wob = max(s['dit_cv'], s['dah_cv'])
        score -= min(25, wob * 100)
        if wob > 0.2:
            tips.append('Timing wobbles - slow down a little and keep a steady rhythm.')
        if not tips:
            tips.append('Clean fist! Try 2-3 WPM faster.')
        return max(0, round(score)), tips, s

    @staticmethod
    def accuracy(sent, target):
        a = ' '.join(sent.upper().split())
        b = ' '.join(target.upper().split())
        return round(100 * difflib.SequenceMatcher(None, a, b).ratio())


class SerialKeyInput:
    """Straight key / keyer output wired to a COM port input (CTS or DSR; key closes to RTS/DTR).
    Polls every ~2 ms and feeds the decoder as a clean on/off envelope."""

    def __init__(self, port, line='CTS', on_level=None):
        self.port, self.line, self.on_level = port, line.upper(), on_level
        self.run = False

    def start(self):
        import serial
        s = serial.Serial()
        s.port = self.port
        s.dtr, s.rts = True, True          # supply for the key contact
        s.open()
        self.ser = s
        self.run = True
        threading.Thread(target=self._loop, daemon=True).start()

    def stop(self):
        self.run = False
        try:
            self.ser.close()
        except Exception:
            pass

    def _loop(self):
        hop = 0.004
        t = time.perf_counter()
        while self.run:
            down = self.ser.cts if self.line == 'CTS' else self.ser.dsr
            if self.on_level:
                self.on_level(1.0 if down else 0.01)
            t += hop
            d = t - time.perf_counter()
            if d > 0:
                time.sleep(d)
            else:
                t = time.perf_counter()
