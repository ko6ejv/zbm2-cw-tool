"""Choose which decoder's text to print.

The soft decoder (softdec.py) is the main copy: on today's recordings it beat the fast
decoder at every signal level, on real fists, and on tight word spacing. It lags ~25 dits.
The fast decoder only prints when the soft one has nothing (its text gate closed) and the
fast one is copying a solid signal - e.g. right at the start of a strong over.
A guard keeps a hand-over from printing the same stretch twice.
"""


class Combiner:
    GUARD_S = 0.4
    SOFT_QUIET_S = 3.0

    def __init__(self):
        self.mode = 'soft'
        self.fast_until = -1.0
        self.soft_until = -1.0
        self.last_space = True
        self._soft_seen = -1e9

    def update_mode(self, sn, t_s, soft_open=True):
        # Live test 2026-11-25: the fast fallback printed noise ("E* TE E EEES") whenever the fast
        # decoder's S/N blipped up on noise. The soft decoder alone prints; fast only advises.
        self.mode = 'soft'
        return self.mode

    def _emit(self, text):
        out = []
        for ch in text:
            if ch == ' ' and self.last_space:
                continue
            out.append(ch)
            self.last_space = ch == ' '
        return ''.join(out)

    def fast(self, text, t_s):
        if self.mode != 'fast' or not text:
            return ''
        if text.strip():
            self.fast_until = t_s
        return self._emit(text)

    def soft(self, items):
        out = []
        for tok, ts in items:
            if ts <= self.fast_until + self.GUARD_S or ts < self.soft_until:
                continue
            out.append(tok)
            self.soft_until = ts
        return self._emit(''.join(out))
