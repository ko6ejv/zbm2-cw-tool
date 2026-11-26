"""Soft-decision CW decoder (weak-signal path).

The fast decoder (decoder.py) makes a hard on/off call every 4 ms. This one doesn't decide
anything frame by frame. It:

  1. turns each envelope frame into a log-likelihood ratio  "tone present vs absent"
     using a Rayleigh (noise) / Rice (noise + carrier) model with levels learned from the
     last ~8 s of audio (EM on a 2-component mixture),
  2. runs a semi-Markov Viterbi over that: segments alternate mark/space, every segment
     type (dit, dah, element gap, letter gap, word gap, long silence) has a duration prior
     in units of the sender's dit, so a 2-frame noise spike can't become a dit and a fade
     inside a dah can't split it - the whole element is weighed at once, the way an ear
     integrates,
  3. reads the best path out with a fixed lag (~1.5 s), so text is final when shown.

Speed and tone come from the caller (the fast decoder's lock, or the speed search).
"""
import math

import numpy as np

from morse import DECODE

_ELEMENTS = {v: k for k, v in DECODE.items()}

# segment types
DIT, DAH, IGAP, CGAP, WGAP, IDLE = range(6)


def _log_i0(x):
    """log of modified Bessel I0, vectorised, no scipy."""
    x = np.asarray(x, dtype=np.float64)
    small = x < 3.0
    out = np.empty_like(x)
    xs = x[small]
    t = (xs / 3.75) ** 2
    out[small] = np.log(1 + t * (3.5156229 + t * (3.0899424 + t * (1.2067492 + t * (0.2659732 + t * (
        0.0360768 + t * 0.0045813))))))
    xl = x[~small]
    tl = 3.75 / xl
    out[~small] = xl - 0.5 * np.log(xl) + np.log(0.39894228 + tl * (0.01328592 + tl * (0.00225319 + tl * (
        -0.00157565 + tl * (0.00916281 + tl * (-0.02057706 + tl * (0.02635537 + tl * (-0.01647633 + tl * 0.00392377))))))))
    return out


class Levels:
    """Noise sigma and mark amplitude from a Rayleigh + Rice mixture (EM) over recent frames."""

    def __init__(self):
        self.sigma, self.A, self.w = 1.0, 3.0, 0.3
        self.ok = False

    def fit(self, m):
        m = np.asarray(m, dtype=np.float64)
        m = m[m > 0]
        if len(m) < 200:
            return
        s = np.percentile(m, 25) / 0.7585            # Rayleigh 25th percentile
        A = max(np.percentile(m, 92), 1.5 * s)
        w = 0.35
        for _ in range(12):
            lr = np.log(m / s ** 2) - m * m / (2 * s * s)                  # Rayleigh
            lc = lr - A * A / (2 * s * s) + _log_i0(m * A / (s * s))        # Rice
            a, b = np.log(1 - w) + lr, np.log(w) + lc
            mx = np.maximum(a, b)
            r = np.exp(b - mx) / (np.exp(a - mx) + np.exp(b - mx))         # P(mark | m)
            w = float(np.clip(r.mean(), 0.05, 0.7))
            n0 = (1 - r).sum()
            if n0 > 10:
                s = math.sqrt(float(((1 - r) * m * m).sum() / (2 * n0)))
            if r.sum() > 5:
                # E[m] under Rice ~ sqrt(A^2 + sigma^2) for A >> sigma; good enough as an update
                em = float((r * m).sum() / r.sum())
                A = math.sqrt(max(em * em - s * s, (0.5 * s) ** 2))
        self.sigma, self.A, self.w = s, A, w
        self.ok = True

    @property
    def snr_db(self):
        """Mark amplitude over noise, dB (detector bandwidth)."""
        return 20 * math.log10(self.A / self.sigma) if self.ok else -99.0

    FADE = ((1.0, 0.6), (0.5, 0.25), (0.25, 0.15))   # (fraction of A, weight): marks dip with AGC/QSB

    def llr(self, m):
        """log P(m | tone) - log P(m | noise only); 'tone' allows the mark to sag to 1/2 or 1/4 level."""
        m = np.asarray(m, dtype=np.float64)
        s2 = self.sigma ** 2
        terms = []
        for frac, w in self.FADE:
            a = self.A * frac
            if frac < 1.0 and a < 2.5 * self.sigma:      # only strong signals get the fade allowance;
                continue                                  # on weak ones a sagging mark looks like noise
            terms.append(math.log(w) - a * a / (2 * s2) + _log_i0(m * a / s2))
        t = np.vstack(terms)
        mx = t.max(axis=0)
        return mx + np.log(np.exp(t - mx).sum(axis=0))


class SoftDecoder:
    """Feed envelope magnitudes (one per hop) with the current dit length; get text."""

    LAG_DITS = 25          # commit text this far behind the newest frame
    HIST_S = 8.0

    def __init__(self, hop_ms=4.0):
        self.hop_ms = hop_ms
        self.levels = Levels()
        self.reset()

    def reset(self):
        t0 = getattr(self, 't', 0)       # the clock keeps running across resets (timestamps stay monotonic)
        self.t = t0                      # frames seen (absolute)
        self.mags = []                   # recent magnitudes for level fitting
        self.base = t0                   # absolute frame index of array slot 0
        n = 4096
        self.C = np.zeros(n)             # cumulative LLR
        self.aM = np.full(n, -1e18)      # best score, path ends with a MARK at this frame
        self.aS = np.full(n, -1e18)      # ... ends with a short SPACE
        self.aL = np.full(n, -1e18)      # ... in a long silence
        self.aL[0] = 0.0
        self.bp = np.zeros((3, n, 3), dtype=np.int64)   # [state M/S/L][slot] -> (start abs, type, from-state)
        self.bp[:, 0, :] = (-1, -1, -1)
        self.committed = t0
        self._last_committed = []
        self.tok_ev = {}
        self.cur = ''
        self.dit_frames = 15.0
        self._fit_ctr = 0

    def _grow(self, need):
        n = len(self.C)
        if need < n:
            return
        m = max(2 * n, need + 1)
        for name, fill in (('C', 0.0), ('aM', -1e18), ('aS', -1e18), ('aL', -1e18)):
            a = getattr(self, name)
            b = np.full(m, fill)
            b[:n] = a
            setattr(self, name, b)
        bp = np.zeros((3, m, 3), dtype=np.int64)
        bp[:, :n] = self.bp
        self.bp = bp

    # ---------- duration priors ----------
    def _priors(self, D):
        def gauss(mean, sd, lo, hi):
            d = np.arange(max(1, int(lo * D)), max(2, int(math.ceil(hi * D))) + 1)
            lp = -0.5 * ((np.log(d / (mean * D))) / sd) ** 2
            return d, lp
        return {
            DIT: gauss(1.0, 0.30, 0.45, 2.0),
            DAH: gauss(3.0, 0.28, 1.8, 5.5),
            # element gap / letter gap split at the geometric middle (no overlap: a 2-dit gap is a
            # letter gap, which is what tight keyer and bug senders actually send)
            IGAP: gauss(1.0, 0.35, 0.4, math.sqrt(3.0)),
            CGAP: gauss(3.0, 0.30, math.sqrt(3.0), 5.5),
            WGAP: gauss(7.0, 0.30, 4.5, 12.0),
        }

    def set_speed(self, dit_ms):
        D = max(3.0, dit_ms / self.hop_ms)
        if not hasattr(self, '_pri') or abs(D - self.dit_frames) > 0.08 * self.dit_frames:
            self.dit_frames = D
            self._pri = self._priors(D)
            tp = {DIT: math.log(0.55), DAH: math.log(0.45),
                  IGAP: math.log(0.62), CGAP: math.log(0.30), WGAP: math.log(0.08)}
            # proper log-pmfs over duration (so paths at different speeds score comparably)
            norm = {k: (d, lp - (lp.max() + math.log(np.exp(lp - lp.max()).sum())))
                    for k, (d, lp) in self._pri.items()}
            self._pri = {k: (d, lp + tp[k]) for k, (d, lp) in norm.items()}
            self._idle_step = -0.02
            self._dmax = int(max(v[0][-1] for v in self._pri.values())) + 2
            if getattr(self, '_gaps', None):
                self._gapkey = None
                self.set_gaps(*self._gaps)

    # ---------- streaming ----------
    def feed(self, mags, dit_ms, scale=1.0):
        """mags: detector magnitudes for consecutive hops. Returns newly committed text."""
        self.set_speed(dit_ms)
        mags = np.asarray(mags, dtype=np.float64)
        self.mags.extend(mags.tolist())
        keep = int(self.HIST_S * 1000 / self.hop_ms)
        if len(self.mags) > keep:
            del self.mags[:-keep]
        self._fit_ctr += len(mags)
        if not self.levels.ok or self._fit_ctr > 250 / self.hop_ms:
            self._fit_ctr = 0
            self.levels.fit(self.mags)
        if not self.levels.ok:
            return []
        llr = self.levels.llr(mags) * scale
        self._grow(self.t - self.base + len(llr) + 2)
        for v in llr:
            self._step(float(v))
        if self.t - self.base > 3000:
            self._trim()
        return self._commit_timed()

    def _step(self, v):
        self.t += 1
        i = self.t - self.base
        C = self.C
        C[i] = C[i - 1] + v
        bm_sc, bm = -1e18, None
        for typ in (DIT, DAH):
            ds, lp = self._pri[typ]
            st = i - ds
            st = st[st >= 0]
            if not len(st):
                continue
            prevS, prevL = self.aS[st], self.aL[st]
            prev = np.maximum(prevS, prevL)
            sc = prev + (C[i] - C[st]) + lp[:len(st)]
            k = int(np.argmax(sc))
            if sc[k] > bm_sc:
                bm_sc, bm = sc[k], (st[k] + self.base, typ, 1 if prevS[k] >= prevL[k] else 2)
        bs_sc, bs = -1e18, None
        for typ in (IGAP, CGAP, WGAP):
            ds, lp = self._pri[typ]
            st = i - ds
            st = st[st >= 0]
            if not len(st):
                continue
            sc = self.aM[st] - (C[i] - C[st]) + lp[:len(st)]
            k = int(np.argmax(sc))
            if sc[k] > bs_sc:
                bs_sc, bs = sc[k], (st[k] + self.base, typ, 0)
        cont = self.aL[i - 1] - v + self._idle_step
        if bs is not None and bs[1] == WGAP and bs_sc > cont:
            bl_sc, bl = bs_sc, bs
        else:
            bl_sc, bl = cont, (self.t - 1, IDLE, 2)
        self.aM[i], self.aS[i], self.aL[i] = bm_sc, bs_sc, bl_sc
        self.bp[0, i] = bm if bm else (-1, -1, -1)
        self.bp[1, i] = bs if bs else (-1, -1, -1)
        self.bp[2, i] = bl

    def _trim(self):
        cut = min(self.committed, self.t - self._dmax - 5) - self.base - 1
        if cut > 1000:
            n = self.t - self.base + 1
            for name in ('C', 'aM', 'aS', 'aL'):
                a = getattr(self, name)
                a[:n - cut] = a[cut:n]
            self.bp[:, :n - cut] = self.bp[:, cut:n]
            self.base += cut
            mx = max(self.aM[self.t - self.base], self.aS[self.t - self.base], self.aL[self.t - self.base])
            for name in ('aM', 'aS', 'aL'):
                a = getattr(self, name)
                a[:n - cut] -= mx
            self.C[:n - cut] -= self.C[0]

    def _path(self):
        i = self.t - self.base
        st = int(np.argmax([self.aM[i], self.aS[i], self.aL[i]]))
        t = self.t
        segs = []
        while t > self.committed:
            s, typ, frm = (int(v) for v in self.bp[st, t - self.base])
            if typ < 0 or s < self.base:
                break
            segs.append((s, t, typ))
            t, st = s, frm
        segs.reverse()
        return segs

    def _commit(self, final=False):
        return ''.join(tok for tok, _ in self._commit_timed(final))

    def _commit_timed(self, final=False):
        """-> [(token, start_frame)] newly final."""
        lag = 0 if final else int(self.LAG_DITS * self.dit_frames)
        horizon = self.t - lag
        if horizon <= self.committed:
            return []
        out = []
        self._last_committed = []
        for s, e, typ in self._path():
            if e > horizon:
                break
            if e <= self.committed:
                continue
            self._last_committed.append((s, e, typ))
            if typ in (DIT, DAH):
                if not self.cur:
                    self._cur_start = s
                    self._cur_ev = [0.0, 0]
                self.cur += '.' if typ == DIT else '-'
                i0, i1 = s - self.base, e - self.base
                if 0 <= i0 < i1 < len(self.C):
                    self._cur_ev[0] += float(self.C[i1] - self.C[i0])
                    self._cur_ev[1] += i1 - i0
            elif typ != IGAP:
                if self.cur:
                    out.append((DECODE.get(self.cur, '*'), self._cur_start))
                    ev = getattr(self, '_cur_ev', [0.0, 0])
                    self.tok_ev[self._cur_start] = ev[0] / max(ev[1], 1)   # mean LLR/frame of its marks
                    self.cur = ''
                if typ in (WGAP, IDLE) and out and out[-1][0] != ' ':
                    out.append((' ', s))
            self.committed = e
        return out

    def flush(self):
        out = self._commit_timed(final=True)
        if self.cur:
            out.append((DECODE.get(self.cur, '*'), getattr(self, '_cur_start', self.t)))
            self.cur = ''
        return out

    def set_gaps(self, cgap, wgap):
        """Sender's own letter / word gap (in dits), learned by the fast decoder."""
        cgap = min(max(cgap, 2.0), 5.0)
        wgap = min(max(wgap, cgap * 1.25), 12.0)
        self._gaps = (cgap, wgap)
        key = (round(cgap, 1), round(wgap, 1), round(self.dit_frames, 1))
        if key != getattr(self, '_gapkey', None) and hasattr(self, '_pri'):
            self._gapkey = key
            D = self.dit_frames
            def gauss(mean, sd, lo, hi):
                d = np.arange(max(1, int(lo * D)), max(2, int(math.ceil(hi * D))) + 1)
                lp = -0.5 * ((np.log(d / (mean * D))) / sd) ** 2
                return d, lp - (lp.max() + math.log(np.exp(lp - lp.max()).sum()))
            mid = math.sqrt(cgap * wgap)
            # tight senders: split the letter/word gap at the geometric middle of their own clusters
            ic = math.sqrt(cgap)            # element-gap / letter-gap split (element gap = 1 dit)
            self._pri[IGAP] = (lambda d, lp: (d, lp + math.log(0.62)))(*gauss(1.0, 0.35, 0.4, ic))
            self._pri[CGAP] = (lambda d, lp: (d, lp + math.log(0.30)))(*gauss(cgap, 0.20, ic, mid))
            self._pri[WGAP] = (lambda d, lp: (d, lp + math.log(0.12)))(*gauss(wgap, 0.25, mid, 12.0))
            self._dmax = int(max(v[0][-1] for v in self._pri.values())) + 2


class SoftCW:
    """Audio in, text out: detector + levels + speed finding around a SoftDecoder.

    tone comes from the fast decoder (or the operator); a speed hint (the fast decoder's lock)
    is used when offered; otherwise a background search tries a grid of speeds on the last 10 s.
    """
    GRID_WPM = (12, 15, 19, 24, 30, 38, 46)
    HOP_MS = 4.0

    def __init__(self, fs=48000):
        self.fs = fs
        self.hop = int(fs * self.HOP_MS / 1000)
        self.sd = SoftDecoder(self.HOP_MS)
        self.tone = 700.0
        self.dit_ms = 60.0                      # 20 WPM until we know better
        self.speed_src = 'default'
        self._buf = np.zeros(0, dtype=np.float32)
        self._win_ms = None
        # search detector: fixed 20 ms window, 8 ms hop, last 10 s
        self._sbuf = np.zeros(0, dtype=np.float32)
        self._smags = []
        self._since_search = 0.0
        self._durs = []                         # (type, frames) of recently committed marks
        self.last_search = None
        self._emitted_until = -1.0              # audio time (s) of the last token handed out
        self.gate_on = True
        self._norm = []                         # recent per-letter signal strength (fade guard)
        self._recent, self._hold = [], []
        self._open, self._good_t, self._clock = False, -1e9, 0.0

    # ---------- detectors ----------
    def _ref(self, win):
        t = np.arange(win) / self.fs
        return (np.exp(-2j * np.pi * self.tone * t) * np.hanning(win)).astype(np.complex64)

    # side bins for the band-noise reference: a static crash, splatter or the AGC moves these
    # together with the tone bin; keying moves only the tone bin
    SIDE_HZ = (-420, -340, -260, -180, 180, 260, 340, 420)
    NORM = True
    STRONG_DB = 17.0          # detector strength (vs noise) that only a real station reaches
    NORM_KNEE = 2.0

    def _refmat(self, win):
        t = np.arange(win) / self.fs
        h = np.hanning(win)
        cols = [np.exp(-2j * np.pi * self.tone * t) * h]
        cols += [np.exp(-2j * np.pi * max(60.0, self.tone + o) * t) * h for o in self.SIDE_HZ]
        return np.stack(cols, axis=1).astype(np.complex64)

    def _mags(self, x, buf_name, win, hop):
        buf = np.concatenate([getattr(self, buf_name), x])
        n = (len(buf) - win) // hop + 1
        if n <= 0:
            setattr(self, buf_name, buf)
            return np.zeros(0)
        fr = np.lib.stride_tricks.sliding_window_view(buf, win)[::hop][:n]
        key = (buf_name, win, round(self.tone))
        if getattr(self, '_refkey_' + buf_name, None) != key:
            setattr(self, '_ref_' + buf_name, self._refmat(win))
            setattr(self, '_refkey_' + buf_name, key)
        M = np.abs(fr @ getattr(self, '_ref_' + buf_name)) / win
        m = M[:, 0]
        setattr(self, buf_name, buf[n * hop:])
        if self.NORM:
            m = self._normalize(m, np.median(M[:, 1:], axis=1), buf_name, hop)
        return m

    def _normalize(self, m, ref, buf_name, hop):
        """Divide out band-noise level changes (crashes, splatter, AGC pumping) measured in the
        side bins: fast-smoothed band level vs its slow quiet-floor."""
        st = getattr(self, '_nst_' + buf_name, None)
        if st is None:
            r0 = float(np.median(ref)) if len(ref) else 1e-9
            st = [r0, r0]
        fast_a = 1 - math.exp(-hop / self.fs / 0.040)      # ~40 ms band-level smoother
        up_a = 1 - math.exp(-hop / self.fs / 30.0)         # normal band level: rises slowly
        dn_a = 1 - math.exp(-hop / self.fs / 3.0)          # falls faster
        K = self.NORM_KNEE
        out = np.empty_like(m)
        f, fl = st
        for i in range(len(m)):
            f += fast_a * (ref[i] - f)
            fl += (up_a if f > fl else dn_a) * (f - fl)
            r = f / max(fl, 1e-12)
            out[i] = m[i] * (K / r) if r > K else m[i]   # only big broadband rises are divided out
        setattr(self, '_nst_' + buf_name, [f, fl])
        return out

    def set_tone(self, f):
        if abs(f - self.tone) > 3:
            jump = abs(f - self.tone) > 25
            self.tone = float(f)
            if jump:                       # different station: start fresh
                self.sd.reset()
                self._recent, self._hold, self._open = [], [], False
                self._smags, self._sbuf = [], np.zeros(0, dtype=np.float32)
                self.speed_src = 'default'
                self.dit_ms = 60.0             # 20 WPM until the new station's speed is known
                self._durs = []
                self._since_search = 0.0

    # ---------- speed ----------
    def _search(self):
        # while hunting, look at the last 5 s only: old junk must not steer the speed of a new station
        span_ms = 10000 if self._open else 5000
        m = np.asarray(self._smags[-int(span_ms / 8):], dtype=np.float64)
        if len(m) < 500:
            return None
        best = None
        for wpm in self.GRID_WPM:
            sd = SoftDecoder(hop_ms=8.0)
            sd.LAG_DITS = 10 ** 6                    # score only, no text
            dit = 1200.0 / wpm
            sd.levels.fit(m)
            if not sd.levels.ok:
                return None
            sd.set_speed(dit)
            sd._grow(len(m) + 2)
            llr = sd.levels.llr(m) * (8.0 / 20.0)
            for v in llr:
                sd._step(float(v))
            i = sd.t - sd.base
            sc = max(sd.aM[i], sd.aS[i], sd.aL[i]) / sd.t
            if best is None or sc > best[0]:
                best = (sc, wpm)
        self.last_search = best
        return best

    def _refine(self):
        """Nudge speed toward what the decoder is actually segmenting."""
        if len(self._durs) < 12:
            return
        units = [d if typ == DIT else d / 3.0 for typ, d in self._durs if typ != IGAP][-40:]
        if len(units) < 12:
            return
        est = float(np.median(units)) * self.HOP_MS
        if 0.6 * self.dit_ms < est < 1.6 * self.dit_ms:
            self.dit_ms += 0.3 * (est - self.dit_ms)

    # ---------- main ----------
    def process(self, x, tone=None, hint_dit_ms=None):
        if tone:
            self.set_tone(tone)
        x = np.asarray(x, dtype=np.float32)
        # background speed search material (cheap fixed detector)
        sm = self._mags(x, '_sbuf', int(self.fs * 0.020), int(self.fs * 0.008))
        self._smags.extend(sm.tolist())
        if len(self._smags) > 2000:
            del self._smags[:-1500]
        self._since_search += len(x) / self.fs
        # Fast-decoder speed tips are only used to acquire a station. Once copying, the soft decoder
        # trusts its own measured marks (tips jump around at the ends of overs). A tip must hold
        # steady for ~1 s before it's taken.
        if hint_dit_ms and not self._open:
            h = float(hint_dit_ms)
            p = getattr(self, '_tip', None)
            if p and abs(h - p[0]) <= 0.15 * p[0]:
                p[1] += len(x) / self.fs
                p[0] += 0.3 * (h - p[0])
            else:
                p = self._tip = [h, 0.0]
            if p[1] >= 1.0:
                if self.speed_src != 'fast' or abs(p[0] - self.dit_ms) > 0.2 * self.dit_ms:
                    self.dit_ms = p[0]
                self.speed_src = 'fast'
        elif self._since_search > (4.0 if self.speed_src == 'default' or not self._open else 15.0):
            self._since_search = 0.0
            r = self._search()
            if r:
                wpm = r[1]
                new = 1200.0 / wpm
                big = abs(new - self.dit_ms) > 0.3 * self.dit_ms
                # Same station after a pause: a search over mostly band noise likes the fastest speed
                # (live 2026-11-26: a 25 WPM op read as 46). A big jump right after good copy must be
                # found by two searches in a row before it's believed.
                recent = self.speed_src != 'default' and self._clock - self._good_t < 30.0
                if big and recent:
                    prev = getattr(self, '_jump_cand', None)
                    self._jump_cand = wpm
                    if prev != wpm:
                        r = None
                if r:
                    self._jump_cand = None
                    if self.speed_src != 'search' or big:
                        self.dit_ms = new
                    self.speed_src = 'search'
        # main detector: window ~0.65 dit
        win_ms = min(max(0.65 * self.dit_ms, 12.0), 55.0)
        win = int(self.fs * win_ms / 1000)
        m = self._mags(x, '_buf', win, self.hop)
        self._clock += len(x) / self.fs
        if not len(m):
            return []
        before = self.sd.committed
        toks = self.sd.feed(m, self.dit_ms, scale=self.HOP_MS / win_ms)
        if self.sd.committed > before:
            self._collect(before)
            self._refine()
        return self._gate([(tok, fr * self.HOP_MS / 1000.0) for tok, fr in toks])

    def text(self, x, **kw):
        return ''.join(t for t, _ in self.process(x, **kw))

    # same idea as the fast decoder's text gate: noise decodes as a stream of 1-2 element letters
    def _fresh(self, items):
        """Never hand out a token at or before one already handed out (re-decodes repeat audio)."""
        out = [it for it in items if it[1] > self._emitted_until]
        if out:
            self._emitted_until = out[-1][1]
            self._emit_clock = self._clock
        return out

    def _local_db(self, ts):
        """Signal strength (90th pct of detector output vs noise sigma, dB) within +-0.6 s of ts."""
        sd = self.sd
        fr = int(round(ts * 1000 / self.HOP_MS))
        k = len(sd.mags) - (sd.t - fr)
        w = int(600 / self.HOP_MS)
        a, b = max(0, k - w), min(len(sd.mags), k + w)
        if b - a < 20 or not sd.levels.ok:
            return None
        return 20 * math.log10(max(float(np.percentile(sd.mags[a:b], 90)), 1e-12) / sd.levels.sigma)

    def _fade_ok(self, item):
        """Drop letters made out of a fade: local strength well below this station's norm and
        near the noise (plain noise reads ~6.6 dB here). Relative, so weak-but-steady stations pass."""
        if item[0] == ' ':
            return True
        s = self._local_db(item[1])
        if s is None:
            return True
        norm = float(np.median(self._norm)) if len(self._norm) >= 8 else None
        ok = norm is None or not (s < norm - 4.0 and s < 10.0)
        if ok:
            self._norm.append(s)
            del self._norm[:-30]
        return ok

    def _gate(self, txt):
        txt = [it for it in txt if self._fade_ok(it)]
        if not self.gate_on:
            return self._fresh(list(txt))
        if self._open and self._clock - self._good_t > 8.0:
            self._open, self._recent, self._hold = False, [], []
        rel = []
        for item in txt:
            tok = item[0]
            if tok != ' ':
                n = len(_ELEMENTS.get(tok, ''))
                self._recent.append(n <= 2)
                del self._recent[:-12]
            k = len(self._recent)
            if self._open:
                last6 = self._recent[-6:]
                if tok != ' ' and len(last6) == 6 and sum(last6) <= 3:
                    self._good_t = self._clock
                rel.append(item)
                continue
            self._hold.append(item)
            nshort = sum(self._recent)
            # a clearly strong station opens the gate even when it's sending short letters
            if tok != ' ':
                s = self._local_db(item[1])
                self._strong = (getattr(self, '_strong', 0) + 1) if (s is not None and s >= self.STRONG_DB) else 0
            if (k >= 5 and nshort == 0) or (k >= 6 and nshort <= 1) or (k >= 8 and nshort / k <= 0.55) \
                    or getattr(self, '_strong', 0) >= 4:
                rel += self._hold
                self._hold, self._open, self._good_t = [], True, self._clock
            else:
                del self._hold[:-12]
        return self._fresh(rel)

    def _collect(self, since):
        # durations of marks that were just committed (walk the stored path segment list)
        for s, e, typ in self.sd._last_committed:
            if typ in (DIT, DAH):
                self._durs.append((typ, e - s))
        del self._durs[:-120]

    def flush(self):
        return self._gate([(tok, fr * self.HOP_MS / 1000.0) for tok, fr in self.sd.flush()])

    @property
    def snr_db(self):
        return self.sd.levels.snr_db
