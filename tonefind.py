"""Find the CW signal's audio pitch - and ignore steady carriers (birdies, tune-ups).

Every ~0.5 s, over the last ~10 s of 2048-pt FFT frames (23 Hz bins, 20 ms hop):

* Strong station present (a bin 3x+ the band's floor that drops out between elements):
  pick the loudest. Its AGC action makes every other bin swing in step with its keying,
  so an on/off test would be fooled - loudness is the honest signal here.
* Otherwise (weak signals): compare each bin with its OWN quiet moments. CW has gaps where
  the bin falls back to noise; filter-shaped noise and carriers don't change that ratio,
  so they score ~0. Needs a score clearly above what real band noise reaches.
* The 'birdies' seen on this station measure like loud noise patches, not steady carriers,
  so there's no birdie test; they score ~0 on the weak-signal test, and a click on the
  spectrum locks onto the station the operator wants.

The current tone follows slow drift (+-30 Hz) and only moves to another station if that
one is clearly stronger (and seen twice in a row). An operator click locks it to +-40 Hz.
"""
import numpy as np


class ToneFinder:
    MIN_SCORE = 1.0      # weak path: real band noise reaches ~0.9 (95th pct); ~-3 dB S/N CW clears it
    STRONG = 3.0         # x the band floor

    def __init__(self, fs=48000, lo=300.0, hi=1200.0, nfft=2048, hop_s=0.02, hist_s=10.0):
        self.fs, self.nfft = fs, nfft
        self.hop = int(fs * hop_s)
        self.win = np.hanning(nfft).astype(np.float32)
        f = np.fft.rfftfreq(nfft, 1 / fs)
        self.band = (f >= lo) & (f <= hi)
        self.freqs = f[self.band]
        self.maxf = int(hist_s / hop_s)
        self.P = []
        self._buf = np.zeros(0, dtype=np.float32)
        self._since = 0
        self.tone = None
        self.birdies = []
        self.cand = None
        self.score = 0.0
        self.locked_to = None

    def feed(self, x):
        buf = np.concatenate([self._buf, np.asarray(x, dtype=np.float32)])
        while len(buf) >= self.nfft:
            self.P.append(np.abs(np.fft.rfft(buf[:self.nfft] * self.win))[self.band] ** 2)
            buf = buf[self.hop:]
            self._since += 1
        self._buf = buf
        if len(self.P) > self.maxf:
            del self.P[:-self.maxf]
        if self._since >= 25 and len(self.P) >= 100:      # every ~0.5 s
            self._since = 0
            self._update()
        return self.tone

    def _update(self):
        P = np.asarray(self.P)                             # frames x bins
        Ps = (P[:, :-2] + P[:, 1:-1] + P[:, 2:]) / 3.0     # 3-bin smoothing (signal between bins)
        fr = self.freqs[1:-1]
        mean = Ps.mean(axis=0)
        floor = float(np.median(mean))
        level = mean / floor
        inband = level > 0.25                              # inside the radio's filter
        # stay off the filter's skirts: the steep roll-off fools the on/off test (live: locked to 420 Hz
        # on a 450-950 Hz CW filter). Erode the passband by 2 bins (~47 Hz) each side.
        core = inband.copy()
        for k in (1, 2):
            core[k:] &= inband[:-k]
            core[:-k] &= inband[k:]
        inband = core
        carrier = np.zeros_like(level, dtype=bool)        # (measured birdies on this rig behave like
        # loud noise patches, not steady carriers - no reliable test; the operator's click overrides)
        self.birdies = [float(fr[k]) for k in np.flatnonzero(carrier)
                        if (k == 0 or mean[k] >= mean[k - 1]) and (k == len(mean) - 1 or mean[k] >= mean[k + 1])]
        strong = inband & ~carrier & (level >= self.STRONG)
        if strong.any():
            score = np.where(strong, level, 0.0)
            min_score = self.STRONG
        else:
            p20 = np.percentile(Ps, 20, axis=0)
            ratio = mean / np.maximum(p20, 1e-20)
            base = float(np.median(ratio[inband])) if inband.sum() > 5 else 4.48
            score = np.where(inband & ~carrier, np.clip(ratio / base - 1.0, 0, None), 0.0)
            min_score = self.MIN_SCORE
        if self.locked_to:
            score = np.where(np.abs(fr - self.locked_to) <= 40, score, 0.0)
        i = int(np.argmax(score))
        if score[i] <= min_score:                          # nothing clearly keyed: keep what we have
            self.cand = None
            return
        f = fr[i]
        if 0 < i < len(score) - 1:                         # parabolic peak refine
            a, b, c = mean[i - 1], mean[i], mean[i + 1]
            den = a - 2 * b + c
            if den < 0:
                f += 0.5 * (a - c) / den * (fr[1] - fr[0])
        if self.tone is not None:
            if abs(f - self.tone) <= 30:
                self.tone += 0.5 * (f - self.tone)         # follow drift
                self.score = float(score[i])
                return
            j = int(np.argmin(np.abs(fr - self.tone)))
            here = float(score[max(0, j - 1):j + 2].max())
            if here > 1.5 * min_score and score[i] < 2.0 * here:  # current one solid, rival not clearly stronger
                return
            if here <= 1.5 * min_score and score[i] < 1.5 * here:  # current one weak: rival needs a margin
                return
        if self.tone is None and score[i] < 1.5 * min_score:      # first pick: not on a noise blip
            return
        if self.tone is None or (self.cand is not None and abs(self.cand - f) <= 30):
            self.tone, self.cand, self.score = float(f), None, float(score[i])
        else:
            self.cand = float(f)
