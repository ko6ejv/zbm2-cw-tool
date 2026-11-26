"""Adaptive CW decoder: audio samples in, characters + live WPM out.

Pipeline: tone detect (windowed DFT bin, auto-tuned) -> envelope ->
adaptive noise/peak threshold with hysteresis -> mark/space timing ->
speed tracking (2-means clustering of marks + spaces) -> characters.
"""
import math

import numpy as np
from morse import DECODE
ELEMENTS = {v: k for k, v in DECODE.items()}   # char -> '.-' pattern


class CWDecoder:
    def __init__(self, fs=48000, tone=700.0, hop_ms=4.0, win_ms=12.0,
                 auto_tone=True, min_snr_db=10.0, on_element=None, adaptive=True, ride_dips=True):
        self.ride_dips = ride_dips     # log-scale key-up: hold marks through AGC/QSB dips (off-air audio)
        self.adaptive = adaptive       # narrow the detector to match the sender's speed once locked
        self.on_element = on_element   # callback(kind, ms, level) kind: 'mark' | 'space'
        self.fs = fs
        self.hop = int(fs * hop_ms / 1000)
        self.win = int(fs * win_ms / 1000)
        self.hop_ms = self.hop * 1000.0 / fs
        self.auto_tone = auto_tone
        self.min_snr = 10 ** (min_snr_db / 20)
        self.base_win = self.win
        self.window = np.hanning(self.win)
        self._buf = np.zeros(0, dtype=np.float32)
        self._fftbuf = np.zeros(0, dtype=np.float32)
        self._fft_len = 16384
        self._samples_since_fft = 0
        self._tone_candidate = None
        self.set_tone(tone)
        self.reset_timing()

    # ---------- config ----------
    def set_tone(self, f, reset=False):
        self.tone = float(f)
        self._build_ref()
        if reset or not hasattr(self, 'noise'):
            self.noise = None
            self.peak = None
            self.env = 0.0
            self._warm = int(300 / self.hop_ms)

    def _build_ref(self):
        n = np.arange(self.win)
        self._ref = self.window * np.exp(-2j * np.pi * self.tone * n / self.fs)

    def _adapt_window(self):
        """Matched filter: integrate ~0.45 dit. Narrower bandwidth = less noise (several dB at slow speeds)."""
        if not self.adaptive:
            return
        ms = min(max(0.65 * self.dit, self.base_win * 1000.0 / self.fs), 55.0)
        w = int(self.fs * ms / 1000)
        if abs(w - self.win) > 0.15 * self.win:
            self.win = w
            self.window = np.hanning(w)
            self._build_ref()

    def reset_timing(self):
        if hasattr(self, 'base_win') and self.win != self.base_win:
            self.win = self.base_win
            self.window = np.hanning(self.win)
            self._build_ref()
        self.dit = 60.0            # ms, 20 WPM starting guess
        self.locked = False
        self.key = False
        self.t = getattr(self, 't', 0.0)   # monotonic ms clock, survives re-syncs
        self.t_edge = self.t       # time of last transition (ms)
        self.marks = []            # recent mark durations (ms)
        self.spaces = []           # recent intra-char space durations
        self.gaps = []             # recent inter-char gap durations
        self.pending = []          # raw timeline held until speed lock
        self._pmark = None         # mark waiting for its trailing gap to be confirmed
        self.mark_levels = []      # peak envelope of recent marks (fading detection)
        self._mark_max = 0.0
        self.unknown = 0           # '*' count in recent output
        self.emitted = 0
        self.last_key_t = -1e9     # time (ms) of last key-down
        self._above = 0
        self.cur = []              # raw mark durations of current char
        self.char_done = True
        self.word_done = True
        self.snr = 0.0
        self.confident = False     # marks look like real keying (dits AND dahs)
        if not hasattr(self, '_open'):     # text-gate state lives across timing resets
            self._recent, self._hold = [], []
            self._noise_run = 0
            self._last_out_space = True
            self._open = False
            self._good_t = -1e9
        self.dropped = 0           # chars suppressed as noise since last confident

    def _looks_keyed(self, marks):
        if not self._marks_keyed(marks):
            return False
        # spacing check: real keying has intra-char spaces ~1 dit, tightly grouped
        sp = self.spaces[-12:]
        if len(sp) < 4:
            return True
        dit = float(np.percentile(marks[-16:], 25))
        v = np.log(np.asarray(sp) / dit)
        return abs(float(np.median(v))) < 0.55 and float(v.std()) < 0.45

    @staticmethod
    def _marks_keyed(marks):
        """Real CW: two clusters (dit/dah) ~3:1 apart, both present, each tight.
        Band noise: one smear of short random blips -> E E I T."""
        if len(marks) < 8:
            return False
        v = np.log(np.asarray(marks[-16:]))
        mid = (v.min() + v.max()) / 2
        for _ in range(8):
            a, b = v[v <= mid], v[v > mid]
            if len(a) == 0 or len(b) == 0:
                return False
            mid = (a.mean() + b.mean()) / 2
        a, b = v[v <= mid], v[v > mid]
        ratio = math.exp(b.mean() - a.mean())
        frac = len(b) / len(v)
        spread = max(a.std(), b.std())            # log-units; 0.35 ~ +/-40%
        return 2.0 < ratio < 4.8 and 0.12 <= frac <= 0.8 and spread < 0.35

    @property
    def word_thresh(self):
        # cached: gaps only change on key-down, dit only on key-up
        key = (len(self.gaps), self.gaps[-1] if self.gaps else 0, self.dit)
        if key != getattr(self, '_wt_key', None):
            t = 5.0
            if len(self.gaps) >= 4:
                cg = float(np.percentile(self.gaps, 35))   # typical char gap, dits
                t = max(5.0, min(1.7 * cg, 14.0))
            if len(self.gaps) >= 8:
                # split gaps into char-gap / word-gap clusters; tight senders put words only ~4-5 dits apart
                c1, c2 = self._two_means(self.gaps)
                if c2 / c1 > 1.3:
                    t = max(1.15 * c1, min(c1 ** (1 - 0.65) * c2 ** 0.65, 14.0))
            self._wt_key, self._wt = key, t * self.dit
        return self._wt

    SN_CAL = 19.0   # median mark level over noise reads ~19 dB for a signal at 0 dB S/N in 500 Hz

    @property
    def sn(self):
        """Signal-to-noise of the station being copied, in dB in a 500 Hz filter (what an op means by
        'S/N'). None when nothing keyed is being copied. Band noise alone would read about -4."""
        if not self.locked or len(self.mark_levels) < 6 or not self.keying_recent:
            return None
        if self.textgate_on and not self._open:
            return None
        return 20 * math.log10(float(np.median(self.mark_levels[-12:])) / max(self.noise, 1e-12)) - self.SN_CAL

    @property
    def wpm(self):
        return 1200.0 / self.dit

    # ---------- main entry ----------
    def process(self, samples):
        """Feed float samples (mono). Returns list of decoded strings
        (characters, prosigns, or ' ' for word gaps)."""
        x = np.asarray(samples, dtype=np.float32)
        out = []
        if self.auto_tone:
            self._track_tone(x)
        self._buf = np.concatenate([self._buf, x])
        n = (len(self._buf) - self.win) // self.hop + 1
        if n <= 0:
            return out
        frames = np.lib.stride_tricks.sliding_window_view(self._buf, self.win)[::self.hop][:n]
        mags = np.abs(frames @ self._ref) / self.win          # all frames at once
        self._buf = self._buf[n * self.hop:]
        for mag in mags.tolist():
            self._step(mag, out)
        return self._textgate(out)

    def _count(self, ch):
        self.emitted += 1
        self.unknown += ch == '*'
        if self.emitted > 40:                 # rolling window ~40 chars
            self.unknown *= 40 / self.emitted
            self.emitted = 40

    @property
    def unknown_ratio(self):
        return self.unknown / self.emitted if self.emitted else 0.0

    @property
    def keying_recent(self):
        return self.t - self.last_key_t < 3000

    def _classify(self, marks):
        pat = ''.join('.' if d < 2 * self.dit else '-' for d in marks)
        return DECODE.get(pat, '*')

    def _replay(self, out):
        """Speed just locked: decode everything held so far."""
        marks = []
        for is_mark, d in self.pending:
            if is_mark:
                marks.append(d)
            elif d > 2 * self.dit:
                if marks:
                    out.append(self._classify(marks))
                    marks = []
                if d > self.word_thresh and out and out[-1] != ' ':
                    out.append(' ')
        self.cur = marks           # still-open character
        self.pending = None

    # ---------- noise gate on decoded text ----------
    # Band noise decodes as a stream of 1-2 element letters (E T I A N M). Real text is ~45%
    # short letters (English) - callsigns/numbers far less. Hold output until the last dozen
    # characters look like text; let it through in one go when they do.
    SHORT_MAX = 0.62

    textgate_on = True          # off in contest mode: exchanges are all short letters (5NN, TU)

    def _textgate(self, out):
        if not self.textgate_on:
            return out
        if self._open and self.t - self.last_key_t > 20000:      # band quiet 20 s: re-arm the gate
            self._open, self._recent, self._hold = False, [], []
        if not out:
            return out
        rel = []
        for tok in out:
            if tok != ' ':
                n = len(ELEMENTS.get(tok, ''))       # '*' (unreadable) -> 0 -> counts as junk
                self._recent.append(n <= 2)
                del self._recent[:-12]
            k = len(self._recent)
            frac = sum(self._recent) / k if k else 1.0
            if self._open:
                # in a contact: short replies (TU, EE, 5NN) pass as long as real text was seen in the
                # last few seconds; once only dit-junk is arriving, close again
                last6 = self._recent[-6:]
                if tok != ' ' and len(last6) == 6 and sum(last6) <= 3:
                    self._good_t = self.t
                if self.t - self._good_t > 6000:
                    self._open, self._hold = False, []
                    self._noise_run += 1
                    continue
                rel.append(tok)
                self._noise_run = 0
                continue
            self._hold.append(tok)
            nshort = sum(self._recent)
            if (k >= 5 and nshort == 0) or (k >= 6 and nshort <= 1) or (k >= 8 and frac <= self.SHORT_MAX):
                rel += self._hold
                self._hold = []
                self._open = True
                self._good_t = self.t
                self._noise_run = 0
            else:
                del self._hold[:-12]
                if tok != ' ':
                    self._noise_run += 1
        if self._noise_run >= 24:            # long stretch of junk: unlock, relearn speed later
            self._noise_run = 0
            self._recent, self._hold = [], []
            self.reset_timing()
        while rel and rel[0] == ' ' and self._last_out_space:
            rel.pop(0)
        if rel:
            self._last_out_space = rel[-1] == ' '
        return rel

    def flush(self):
        return self._textgate(self._flush_raw())

    def _flush_raw(self):
        out = []
        if self.key:
            self._key_up(out)
        self._commit_mark(out)
        if self.pending is not None and self.pending:
            self._replay(out)
        self._emit_char(out)
        return out

    # ---------- tone tracking ----------
    def _track_tone(self, x):
        self._fftbuf = np.concatenate([self._fftbuf, x])[-self._fft_len:]
        self._samples_since_fft += len(x)
        if self._samples_since_fft < self.fs // 2 or len(self._fftbuf) < self._fft_len:
            return
        self._samples_since_fft = 0
        if not hasattr(self, '_fftwin'):
            self._fftwin = np.hanning(self._fft_len)
            self._fftfreqs = np.fft.rfftfreq(self._fft_len, 1 / self.fs)
        spec = np.abs(np.fft.rfft(self._fftbuf * self._fftwin))
        freqs = self._fftfreqs
        band = (freqs > 250) & (freqs < 1500)
        s, f = spec[band], freqs[band]
        i = int(np.argmax(s))
        if s[i] < 12 * np.median(s):         # ~20 dB above floor
            return
        # parabolic interpolation for sub-bin accuracy
        if 0 < i < len(s) - 1:
            a, b, c = s[i - 1], s[i], s[i + 1]
            p = 0.5 * (a - c) / (a - 2 * b + c + 1e-12)
            fpk = f[i] + p * (f[1] - f[0])
        else:
            fpk = f[i]
        if abs(fpk - self.tone) < 20:
            self._tone_candidate = None
            return
        # mid-contact (real text in the last 15 s): stay on this station, don't chase birdies
        if getattr(self, '_open', False) and self.t - getattr(self, '_good_t', -1e9) < 15000:
            self._tone_candidate = None
            return
        # require two consecutive agreeing looks before retuning
        if self._tone_candidate and abs(self._tone_candidate - fpk) < 20:
            self.set_tone(fpk)
            self._tone_candidate = None
        else:
            self._tone_candidate = fpk

    # ---------- envelope / keying ----------
    def _step(self, mag, out):
        self.t += self.hop_ms
        self.env = 0.5 * self.env + 0.5 * mag
        m = self.env
        if self.noise is None:
            self.noise, self.peak = m + 1e-9, m * 1.5 + 1e-9
        if self._warm > 0:                 # settle floor on plain average
            self.noise += 0.1 * (m - self.noise)
            self.peak = self.noise * 1.5
            self._warm -= 1
            self.t_edge = self.t
            return
        # noise floor: average of key-up envelope (faster down than up)
        if not self.key:
            a = 0.05 if m < self.noise else 0.02
            if m < self.noise + 0.3 * (self.peak - self.noise):
                self.noise += a * (m - self.noise)
        # peak: fast up, slow decay
        if m > self.peak:
            self.peak += (0.5 if self.key else 0.25) * (m - self.peak)
        else:
            self.peak += 0.002 * (m - self.peak)
        self.peak = max(self.peak, self.noise * 1.01)
        self.snr = 20 * math.log10(self.peak / self.noise)

        span = self.peak - self.noise
        hi = self.noise + 0.55 * span
        # key-up on a log scale: a mark rides through AGC overshoot / QSB dips instead of chopping
        lo = self.noise + 0.40 * span
        if self.ride_dips and self.locked and self._open:   # only while copying real text
            lo = min(lo, self.noise * (self.peak / self.noise) ** 0.30)
        squelched = self.peak < self.noise * self.min_snr
        if self.key and m > self._mark_max:
            self._mark_max = m

        if not self.key and m > hi and not squelched:
            self._above += 1
            if self._above >= 2:              # 2-frame debounce
                self.t -= self.hop_ms
                self._key_down(out)
                self.t += self.hop_ms
            return
        self._above = 0
        if False:
            pass
        elif self.key and (m < lo or squelched):
            self._key_up(out)
        elif not self.key:
            self._check_space(out)

    def _key_down(self, out):
        space = self.t - self.t_edge
        # noise dip inside a mark (dah chopped in two): merge back into the pending mark
        if self._pmark is not None and space < max(8.0, self.MERGE * self.dit):
            self.key = True
            self.t_edge = self._pmark[0]
            self._mark_max = max(self._mark_max, self._pmark[2])
            self._pmark = None
            return
        self._commit_mark(out)
        if not self.char_done and space < 2 * self.dit:
            self.spaces.append(space)
            self.spaces = self.spaces[-24:]
        elif 2 * self.dit < space < 12 * self.dit:
            self.gaps.append(space / self.dit)   # in dit units
            self.gaps = self.gaps[-20:]
        if self.pending is not None:
            self.pending.append((False, space))
        self._check_space(out, final=True)
        self.key = True
        self._mark_max = 0.0
        self.last_key_t = self.t
        if self.on_element:
            self.on_element('space', space, 0.0)
        self._prev_edge = self.t_edge
        self.t_edge = self.t

    MERGE = 0.3   # gaps shorter than this many dits are noise, not real spaces

    def _key_up(self, out):
        mark = self.t - self.t_edge
        self.key = False
        if mark < max(10.0, 0.25 * self.dit) and self._pmark is None:   # glitch
            self.t_edge = self._prev_edge
            if self.pending and not self.pending[-1][0]:
                self.pending.pop()
            return
        # hold the mark until the following gap proves to be real
        self._pmark = (self.t_edge, mark, self._mark_max)
        self.t_edge = self.t

    def _commit_mark(self, out):
        if self._pmark is None:
            return
        start, mark, level = self._pmark
        self._pmark = None
        self.mark_levels = (self.mark_levels + [level])[-16:]
        if self.on_element:
            self.on_element('mark', mark, level)
        self.cur.append(mark)
        self.marks.append(mark)
        self.marks = self.marks[-24:]
        if self.pending is not None:
            self.pending.append((True, mark))
            del self.pending[:-80]          # only replay the recent past, not old noise
        self.char_done = False
        self.word_done = False
        self._update_speed(out)

    def _check_space(self, out, final=False):
        space = self.t - self.t_edge
        if self._pmark is not None and space >= max(8.0, self.MERGE * self.dit):
            self._commit_mark(out)
        if not self.char_done and space > 2.0 * self.dit:
            self._emit_char(out)
        if not self.word_done and self.char_done and space > self.word_thresh:
            if self.pending is None:
                out.append(' ')
            self.word_done = True

    def _emit_char(self, out):
        if self.cur and self.pending is None:
            if self.confident or len(self.marks) < 8 or self._open:
                ch = self._classify(self.cur)
                out.append(ch)
                self._count(ch)
                self.dropped = 0
            else:
                self.dropped += 1
                if self.dropped >= 6:          # keying fell apart -> back to listening
                    self.cur = []
                    self.reset_timing()
                    return
        self.cur = []
        self.char_done = True

    # ---------- speed tracking ----------
    @staticmethod
    def _two_means(v):
        v = np.log(np.asarray(v))
        c1, c2 = v.min(), v.max()
        for _ in range(8):
            mid = (c1 + c2) / 2
            a, b = v[v <= mid], v[v > mid]
            if len(a) == 0 or len(b) == 0:
                break
            c1, c2 = a.mean(), b.mean()
        return np.exp(c1), np.exp(c2)

    def _update_speed(self, out):
        if len(self.marks) < 4:
            return
        short, long_ = self._two_means(self.marks[-16:])
        if 2.0 < long_ / short < 5.0:
            est = (short + long_ / 3) / 2          # dits and dahs both seen
            if self.locked and len(self.spaces) >= 4:
                # marks run long / spaces short with heavy weighting or slow key-up; their mean doesn't
                sp = float(np.median(self.spaces[-12:]))
                if 0.4 * est < sp < 1.6 * est:
                    est = (est + sp) / 2
        elif self.locked and len(self.spaces) >= 3:
            est = float(np.median(self.spaces[-12:]))   # intra-char ~1 dit
        else:
            return
        est = min(max(est, 1200 / 60), 1200 / 5)   # clamp 5..60 WPM
        self.confident = self._looks_keyed(self.marks)
        if not self.locked and not self.confident:
            return
        if not self.locked:
            self.dit = est
            self.locked = len(self.marks) >= 8
            if self.locked and self.pending is not None:
                self._replay(out)
        else:
            self.dit += 0.3 * (est - self.dit)
        if self.locked:
            self._adapt_window()
