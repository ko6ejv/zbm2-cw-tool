"""Pileup / multi-signal decoding: find every CW signal in the passband and decode each one at once.

Audio is decimated to 8 kHz, signals are found on a 1 s FFT, and each one gets its own
narrow detector (Hann window matched to the sender's speed) feeding its own CWDecoder state machine.
All channels are computed in one matrix multiply per audio block, so 8 signals cost about the same as 1.
"""
import re
import time

import numpy as np

from decoder import CWDecoder
from plain import is_call

FS = 8000
HOP = 32            # 4 ms
WIN = 256           # 32 ms (~62 Hz first null) - separates stations ~60 Hz apart (good to ~30 WPM)


def _dit_junk(call):
    """Noise makes calls like D6EEE / K7IEE: a suffix made only of dit letters."""
    m = re.search(r'\d([A-Z]+)$', call.split('/')[0])
    return bool(m) and len(m.group(1)) >= 3 and set(m.group(1)) <= set('EIS')


class Channel:
    def __init__(self, f, cid):
        self.f, self.id = f, cid
        self.dec = CWDecoder(fs=FS, tone=f, hop_ms=4.0, win_ms=32.0, auto_tone=False, adaptive=False, ride_dips=False,
                             min_snr_db=13.0)
        self.dec._warm = 25
        self.text = ''
        self._word = ''
        self.call = ''
        self.last_signal = time.time()
        self.born = time.time()

    def feed_mag(self, mag, out):
        self.dec._step(mag, out)

    def add(self, s):
        self.text = (self.text + s)[-160:]
        # count every completed call-shaped word once (majority vote for the call fixer)
        try:
            import callfix
            fx = callfix.default()
            for ch in s:
                if ch == ' ':
                    if self._word and is_call(self._word) and not _dit_junk(self._word):
                        fx.note(self._word)
                    self._word = ''
                else:
                    self._word += ch
        except Exception:
            pass
        words = self.text.split()[:-1] if not self.text.endswith(' ') else self.text.split()
        # a call has to be heard twice, or right after DE / CQ - one garbled copy (EE7US) isn't enough
        score = {}
        for i, w in enumerate(words):
            if is_call(w) and not _dit_junk(w):
                score[w] = score.get(w, 0) + 1 + (2 if i and words[i - 1] in ('DE', 'CQ', 'TU') else 0)
        best = max(score.items(), key=lambda kv: kv[1], default=(None, 0))
        if best[1] >= 2 or (len(score) == 1 and self.dec.locked):
            call = best[0]
            try:
                import callfix
                fx = callfix.default()
                call = fx.fix(call)[0]
            except Exception:
                pass
            self.call = call

    @property
    def snr(self):
        return self.dec.snr

    @property
    def wpm(self):
        return self.dec.wpm if self.dec.locked else 0


class Skimmer:
    def __init__(self, fs_in=48000, lo=250, hi=1500, max_ch=8, min_sep=55, snr_db=10):
        self.fs_in = fs_in
        self.dec_n = max(1, int(round(fs_in / FS)))
        self.lo, self.hi, self.max_ch, self.min_sep = lo, hi, max_ch, min_sep
        self.snr_lin = 10 ** (snr_db / 20)
        self.buf = np.zeros(0, dtype=np.float32)
        self.ring = np.zeros(FS, dtype=np.float32)        # last 1 s @ 8 kHz for signal search
        self.since = 0
        self.channels = []
        self._next_id = 1
        self._win = np.hanning(WIN).astype(np.float32)
        self._ref = None
        self._fftwin = np.hanning(FS).astype(np.float32)
        self._freqs = np.fft.rfftfreq(FS, 1 / FS)

    # ---------- public ----------
    def process(self, x):
        """Raw audio block in. -> list of (channel, text) decoded this block."""
        y = self._decimate(x)
        if not len(y):
            return []
        self.ring = np.concatenate([self.ring, y])[-FS:]
        self.since += len(y)
        if self.since >= FS // 2:
            self.since = 0
            new = self._find_signals()
            # catch up: decode the last second of audio for newly found signals
            early = self._run(self.ring[:max(0, len(self.ring) - len(y) - len(self.buf))], new) if new else []
        else:
            early = []
        if not self.channels:
            return early
        self.buf = np.concatenate([self.buf, y])
        n = (len(self.buf) - WIN) // HOP + 1
        if n <= 0:
            return early
        frames = np.lib.stride_tricks.sliding_window_view(self.buf, WIN)[::HOP][:n]
        self.buf = self.buf[n * HOP:]
        out = early + self._feed(frames, self.channels, self._ref)
        self._expire()
        return out

    def _run(self, audio8k, chans):
        if len(audio8k) < WIN:
            return []
        n = (len(audio8k) - WIN) // HOP + 1
        frames = np.lib.stride_tricks.sliding_window_view(audio8k, WIN)[::HOP][:n]
        return self._feed(frames, chans, self._make_ref([c.f for c in chans]))

    def _feed(self, frames, chans, ref):
        mags = np.abs(frames @ ref) / WIN                       # (frames, channels)
        out = []
        for k, ch in enumerate(chans):
            o = []
            for m in mags[:, k].tolist():
                ch.feed_mag(m, o)
            if o:
                s = ''.join(o)
                ch.add(s)
                out.append((ch, s))
            if ch.dec.key or ch.dec.keying_recent:
                ch.last_signal = time.time()
        return out

    def summary(self):
        """[(freq, call, wpm, snr, text, id)] strongest first."""
        rows = [(c.f, c.call, c.wpm, c.snr, c.text, c.id) for c in self.channels if c.text.strip() or c.dec.locked]
        rows.sort(key=lambda r: -r[3])
        out, seen = [], []
        for r in rows:
            call = r[1]
            if call:
                if r[2] <= 0:                      # channel not copying (no speed lock): don't show a call
                    continue
                # same station heard on a neighbouring channel, or a garbled copy (EE7US / EN7US of N7US)
                if any(call == c or (len(call) >= 4 and (call.endswith(c) or c.endswith(call))) or
                       (call[-3:] == c[-3:] and abs(len(call) - len(c)) <= 1) for c in seen):
                    continue
                seen.append(call)
            out.append(r)
        return out

    # ---------- internals ----------
    def _decimate(self, x):
        x = np.asarray(x, dtype=np.float32)
        n = self.dec_n
        if n == 1:
            return x
        if not hasattr(self, '_dbuf'):
            self._dbuf = np.zeros(0, dtype=np.float32)
        x = np.concatenate([self._dbuf, x])
        k = len(x) // n
        self._dbuf = x[k * n:]
        return x[:k * n].reshape(k, n).mean(axis=1)            # boxcar + decimate (rig audio is band-limited)

    def _find_signals(self):
        spec = np.abs(np.fft.rfft(self.ring * self._fftwin))
        band = (self._freqs >= self.lo) & (self._freqs <= self.hi)
        f, s = self._freqs[band], spec[band]
        floor = np.median(s) + 1e-12
        peaks = []
        for i in np.argsort(s)[::-1]:
            if s[i] < floor * self.snr_lin * 3 or len(peaks) >= self.max_ch:
                break
            if 0 < i < len(s) - 1 and s[i] >= s[i - 1] and s[i] >= s[i + 1]:
                fi = f[i]
                if all(abs(fi - p) >= self.min_sep for p in peaks):
                    peaks.append(float(fi))
        changed, new = False, []
        for p in peaks:
            ch = min(self.channels, key=lambda c: abs(c.f - p), default=None)
            if ch and abs(ch.f - p) < 25:
                ch.last_signal = time.time()
                continue
            if len(self.channels) < self.max_ch:
                c = Channel(round(p / 5) * 5, self._next_id)
                self.channels.append(c)
                new.append(c)
                self._next_id += 1
                changed = True
        if changed:
            self._rebuild()
        return new

    def _expire(self, idle=45):
        now = time.time()
        keep = [c for c in self.channels if now - c.last_signal < idle]
        if len(keep) != len(self.channels):
            self.channels = keep
            self._rebuild()

    def _rebuild(self):
        self.channels.sort(key=lambda c: c.f)
        if not self.channels:
            self._ref = None
            return
        self._ref = self._make_ref([c.f for c in self.channels])

    def _make_ref(self, freqs):
        n = np.arange(WIN)
        fr = np.array(freqs)
        return (self._win[:, None] * np.exp(-2j * np.pi * n[:, None] * fr[None, :] / FS)).astype(np.complex64)
