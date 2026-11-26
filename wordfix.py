"""Word-level correction for decoded CW.

Noise flips dits and dahs, drops elements and moves letter / word gaps. A human copies
through that because they know the words. This does the same, conservatively: a decoded
word that isn't a known word is compared in Morse *element* space against common CW
shorthand + the most common English words, and replaced only when one candidate is clearly
the closest. Adjacent fragments ("CORN ER") are tried joined.

Never touches anything with a digit, slash or prosign (calls, reports, numbers - callfix
handles calls) and never changes a word that is already a known word.
"""
import math
import os
import re

from callfix import morse_distance, _elements

ALPHA = re.compile(r'^[A-Z]+$')
_HERE = os.path.dirname(os.path.abspath(__file__))


def _nel(word):
    """number of dits+dahs in a word"""
    return sum(1 for c in _elements(word) if c in '.-')


class WordFixer:
    MAX_JOIN = 3          # how many decoded fragments may be joined into one word

    def __init__(self, path=None):
        path = path or os.path.join(_HERE, 'data', 'words.txt')
        self.rank = {}
        self.by_n = {}
        if os.path.exists(path):
            for line in open(path, encoding='utf-8'):
                if not line.strip() or line.startswith('#'):
                    continue
                w, r = line.split()
                if w in self.rank or len(w) < 2:
                    continue
                self.rank[w] = int(r)
                self.by_n.setdefault(_nel(w), []).append(w)

    def known(self, w):
        return w in self.rank

    def _lex(self, w):
        r = self.rank[w]
        return 0.0 if r == 0 else 0.12 * math.log(1 + r)

    def best(self, raw):
        """-> (cost, word) of the best dictionary match for one fragment, or (inf, None)."""
        n = _nel(raw)
        if n < 4:
            return math.inf, None
        lim = min(3.0, 0.2 * n + 0.5)
        cands = []
        for k in range(max(2, int(n * 0.75)), int(n * 1.3) + 2):
            cands += self.by_n.get(k, ())
        scored = []
        for w in cands:
            d = morse_distance(raw, w, limit=lim)
            if d <= lim:
                scored.append((d + self._lex(w), d, w))
        if not scored:
            return math.inf, None
        scored.sort()
        c, d, w = scored[0]
        # needs a clear winner: a close runner-up means we'd be guessing
        if len(scored) > 1 and scored[1][0] - c < 0.5:
            return math.inf, None
        return c, w

    def raw_cost(self, tok):
        if tok in self.rank:
            return 0.0
        return 1.0 + 0.12 * _nel(tok)

    def fix_words(self, toks):
        """toks: list of decoded words. Returns corrected list (may be shorter: joins)."""
        n = len(toks)
        INF = math.inf
        cost = [0.0] + [INF] * n
        back = [None] * (n + 1)
        for i in range(n):
            if cost[i] == INF:
                continue
            t = toks[i]
            # keep as decoded
            c = cost[i] + (self.raw_cost(t) if ALPHA.match(t) else 0.0)
            if c < cost[i + 1]:
                cost[i + 1], back[i + 1] = c, (i, t)
            if not ALPHA.match(t):
                continue
            for j in range(i + 1, min(n, i + self.MAX_JOIN) + 1):
                span = toks[i:j]
                if not all(ALPHA.match(s) for s in span):
                    break
                if j == i + 1 and (t in self.rank or len(t) < 2):
                    continue
                joined = ''.join(span)
                # joining fragments costs a missed word gap each, more if a fragment was itself a word
                jc = sum(0.5 + (1.0 if s in self.rank else 0.0) for s in span[1:]) + \
                    (1.0 if j > i + 1 and t in self.rank else 0.0)
                if j > i + 1 and joined in self.rank:
                    c2, w = self._lex(joined) + jc, joined
                else:
                    c2, w = self.best(joined)
                    if w is None:
                        continue
                    c2 += jc
                c = cost[i] + c2
                if c < cost[j]:
                    cost[j], back[j] = c, (i, w)
        out = []
        k = n
        while k > 0:
            i, w = back[k]
            out.append(w)
            k = i
        return out[::-1]

    def fix_text(self, text):
        return ' '.join(self.fix_words(text.split()))


_default = None


def default():
    global _default
    if _default is None:
        _default = WordFixer()
    return _default
