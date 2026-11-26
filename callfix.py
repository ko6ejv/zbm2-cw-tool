"""Callsign correction for decoded CW.

Calls get garbled in predictable ways: a dah heard as a dit, a letter gap missed or
invented (K -.- heard as E A  . .- ). So candidates are compared in Morse *element*
space, not by letters:  EA5AX -> K5AX is one flipped element.

Two sources of truth, in order:
  1. calls heard cleanly this session (the runner you're listening to, callers already logged)
  2. MASTER.SCP - Super Check Partial, ~50k contest-active calls, offline file in data/

Never invents a call: a decoded call that is valid and not in either list is left alone
(plenty of real hams - W6JL, KC7NBM - aren't contest regulars).
"""
import os
import re
from collections import Counter

from morse import code_for
from plain import is_call

CALLISH = re.compile(r'^[A-Z0-9*/]{3,12}$')


def _elements(call):
    """'K5AX' -> '-.-|.....|.-|-..-' ; unknown '*' -> '?'"""
    out = []
    for ch in call:
        if ch == '*':
            out.append('?')
        else:
            try:
                out.append(code_for(ch) or '?')
            except Exception:
                out.append('?')
    return '|'.join(out)


def morse_distance(a, b, limit=4.0):
    """Edit distance over dit/dah/letter-gap tokens. Flip dit<->dah = 1, drop/add element = 1,
    drop/add a letter gap = 0.5, '?' (unreadable) matches anything for 0.5."""
    x, y = _elements(a), _elements(b)
    n, m = len(x), len(y)
    if abs(n - m) > limit * 2:
        return 99.0
    prev = [0.0] * (m + 1)
    for j in range(1, m + 1):
        prev[j] = prev[j - 1] + (0.5 if y[j - 1] == '|' else 1.0)
    for i in range(1, n + 1):
        ci = x[i - 1]
        cur = [prev[0] + (0.5 if ci == '|' else 1.0)] + [0.0] * m
        best = cur[0]
        for j in range(1, m + 1):
            cj = y[j - 1]
            if ci == cj:
                sub = 0.0
            elif '?' in (ci, cj):
                sub = 0.5
            elif '|' in (ci, cj):
                sub = 1.0
            else:
                sub = 1.0
            v = min(prev[j - 1] + sub,
                    prev[j] + (0.5 if ci == '|' else 1.0),
                    cur[j - 1] + (0.5 if cj == '|' else 1.0))
            cur[j] = v
            if v < best:
                best = v
        if best > limit:
            return 99.0
        prev = cur
    return prev[m]


class CallFixer:
    def __init__(self, scp_paths=()):
        self.scp = set()
        self.by_digit = {}
        for p in scp_paths:
            if p and os.path.exists(p):
                self.load_scp(p)
                break
        self.heard = Counter()        # calls copied cleanly this session

    def load_scp(self, path):
        with open(path, encoding='ascii', errors='ignore') as f:
            for line in f:
                c = line.strip().upper()
                if not c or c[0] in '#!' or not is_call(c):
                    continue
                self.scp.add(c)
        self.by_digit = {}
        for c in self.scp:
            d = next((ch for ch in c if ch.isdigit()), '')
            self.by_digit.setdefault(d, []).append(c)
        self.path = path

    # ---- session memory ----
    def note(self, call, n=1):
        """Count a sighting of a decoded call. Seen twice = trusted for this session."""
        if call and is_call(call):
            self.heard[call] += n

    def trusted(self):
        return [c for c, n in self.heard.items() if n >= 2]

    # ---- correction ----
    def _candidates(self, raw):
        digits = [ch for ch in raw if ch.isdigit()]
        pool = set(self.trusted())
        if digits and self.by_digit:
            tail, head = raw[-2:], raw[:2]
            for c in self.by_digit.get(digits[0], ()):
                if abs(len(c) - len(raw)) <= 2 and (c[-2:] == tail or c[:2] == head or
                                                     c.endswith(raw[-3:]) or raw.endswith(c[-3:])):
                    pool.add(c)
        return pool

    def fix(self, raw):
        """-> (call, how). how: 'ok' | 'session' | 'scp' | '' (couldn't place it)."""
        raw = (raw or '').upper().strip()
        if not raw or not CALLISH.match(raw):
            return raw, ''
        valid = is_call(raw)
        trusted = self.trusted()
        # '*' = one unreadable letter: fill it from session calls, then SCP, if exactly one fits
        if '*' in raw:
            rx = re.compile('^' + re.escape(raw).replace(r'\*', '[A-Z0-9/]') + '$')
            for pool in (trusted, self._candidates(raw.replace('*', '')) | set(
                    c for c in self.by_digit.get(next((ch for ch in raw if ch.isdigit()), ''), ()) if rx.match(c))):
                hits = sorted(c for c in pool if rx.match(c))
                if len(hits) == 1:
                    return hits[0], 'session' if pool is trusted else 'scp'
            return raw, ''
        # 1) runner lock by majority: among this call and near-identical calls heard this session,
        #    the one heard most often wins (garbles are scattered, the real call repeats)
        near = []
        for c in trusted:
            if c != raw:
                d = morse_distance(raw, c, 3.0)
                if d <= (2.0 if len(c) >= 4 else 1.0):
                    near.append((d, c))
        if near:
            d, c = min(near, key=lambda t: (t[0], -self.heard[t[1]]))
            if self.heard[c] > self.heard.get(raw, 0) and (raw not in self.scp or self.heard[c] >= 3):
                return c, 'session'
        if raw in self.scp:
            return raw, 'ok'
        # 2) SCP. A valid-looking call is only re-split (letter-gap error: AT7RIP -> W7RIP, same dits
        #    and dahs); a broken one may move by one flipped element too. Must be unambiguous.
        lim = 0.5 if valid else 1.5
        scored = []
        for c in self._candidates(raw):
            d = morse_distance(raw, c, lim + 1)
            if d <= lim:
                scored.append((d, c))
        scored.sort()
        if scored and (len(scored) == 1 or scored[1][0] - scored[0][0] >= 0.5):
            return scored[0][1], 'scp'
        return raw, ('ok' if valid or self.heard.get(raw, 0) >= 2 else '')

    def partial(self, frag, limit=8):
        """Super-check-partial: SCP calls containing the fragment (for the contest call box)."""
        frag = frag.upper().replace('*', '')
        if len(frag) < 3:
            return []
        hits = [c for c in self.scp if frag in c]
        hits.sort(key=lambda c: (-self.heard.get(c, 0), len(c), c))
        return hits[:limit]


_default = None


def default():
    """Shared fixer, loading data/MASTER.SCP from the app folder if present."""
    global _default
    if _default is None:
        from config import HERE, RES
        _default = CallFixer([os.path.join(HERE, 'data', 'MASTER.SCP'), os.path.join(RES, 'data', 'MASTER.SCP'),
                              os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'MASTER.SCP')])
    return _default
