"""Neural CW decoder: DeepCW model (e04, MIT) running locally with onnxruntime.

Why: hand-written timing decoders (ours, fldigi, ggmorse) fall apart a few dB above where an
operator can still copy. DeepCW is a CNN + CTC network trained on noisy / fading CW - the same
way speech recognition works - and copies cleanly at S/N where ours is at 70-80 %.

Pipeline (matches the DeepCW reference app):
  48 kHz audio -> anti-alias FIR -> 3200 Hz -> STFT (256 / hop 48, 12.5 Hz bins)
  -> keep +-width/2 around the selected tone, shifted to the model's 800 Hz centre
  -> noise-normalise -> model -> CTC posteriors stitched across overlapping 12 s windows
  (stitcher vendored in deepcw_engine.py) -> text, committed ~2 s behind real time.
"""
import json
import math
import os

import numpy as np

from deepcw_engine import PosteriorStitcher, normalize_spectrogram

_HERE = getattr(__import__('sys'), '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))  # bundled .exe
MODEL = os.path.join(_HERE, 'models', 'deepcw.onnx')
META = os.path.join(_HERE, 'models', 'deepcw.onnx.json')


def available():
    import importlib.util
    if importlib.util.find_spec('onnxruntime') is None:
        return False
    return os.path.exists(MODEL) and os.path.exists(META)


def _lowpass(fs, cutoff, taps):
    n = np.arange(taps) - (taps - 1) / 2
    h = np.sinc(2 * cutoff / fs * n) * np.blackman(taps)
    return (h / h.sum()).astype(np.float32)


class DeepCW:
    STEP_S = 1.0           # run the model once a second
    FIRST_S = 2.0
    TONE_JUMP_HZ = 25.0    # a bigger tone change = a different station: start fresh

    def __init__(self, fs=48000, width_hz=250.0, threads=2):
        import onnxruntime as ort
        self.meta = json.load(open(META))
        so = ort.SessionOptions()
        so.intra_op_num_threads = threads
        so.inter_op_num_threads = 1
        self.sess = ort.InferenceSession(MODEL, so, providers=['CPUExecutionProvider'])
        m = self.meta
        self.sr = int(m['sample_rate'])
        self.nfft, self.hop = int(m['fft_length']), int(m['hop_length'])
        self.bin_hz = self.sr / self.nfft
        self.b0 = int(math.ceil(m['spectrogram_min_freq_hz'] / self.bin_hz))
        self.nb = int(m['spectrogram_frequency_bins'])
        self.centre = 0.5 * (m['spectrogram_min_freq_hz'] + m['spectrogram_max_freq_hz'])
        self.win = np.hanning(self.nfft + 1)[:-1].astype(np.float32)
        self.fs = fs
        self.width_hz = width_hz
        self.tone = None
        self._set_rate(fs)
        self.reset()

    def _set_rate(self, fs):
        self.fs = fs
        self.dec = fs / self.sr
        if abs(self.dec - round(self.dec)) > 1e-6:
            raise ValueError('input rate must be a multiple of %d Hz' % self.sr)
        self.dec = int(round(self.dec))
        self.h = _lowpass(fs, 0.42 * self.sr, 16 * self.dec + 1)
        self._tail = np.zeros(len(self.h) - 1, dtype=np.float32)
        self._phase = 0

    def reset(self):
        self.st = PosteriorStitcher(self.meta)
        self.audio = np.zeros(0, dtype=np.float32)
        self.off = 0                     # absolute 3200 Hz sample index of audio[0]
        self.end = 0                     # absolute samples received
        self._next = int(self.FIRST_S * self.sr)
        self.transcript = ''

    def set_tone(self, f):
        if f is None:
            return
        if self.tone is None or abs(f - self.tone) > self.TONE_JUMP_HZ:
            if self.tone is not None:
                self.reset()
        self.tone = float(f)

    # ---------- audio in ----------
    def _decimate(self, x):
        buf = np.concatenate([self._tail, x.astype(np.float32)])
        self._tail = buf[-(len(self.h) - 1):]
        y = np.convolve(buf, self.h, mode='valid')          # len(x) outputs
        out = y[self._phase::self.dec]
        self._phase = (self._phase - len(y)) % self.dec
        return out.astype(np.float32)

    def process(self, x, tone=None):
        """48 kHz block in -> newly committed text."""
        if tone is not None:
            self.set_tone(tone)
        y = self._decimate(np.asarray(x))
        self.audio = np.concatenate([self.audio, y])
        self.end += len(y)
        out = ''
        while self.end >= self._next:
            out += self._step(self._next, final=False)
            self._next += int(self.STEP_S * self.sr)
        return out

    def flush(self):
        out = ''
        while True:
            start = self.st.context_start_frame
            end = min(self.end, start * self.st.hop_length + self.st.max_segment_samples)
            final = end >= self.end
            out += self._run(start, end, final)
            if final:
                return out

    # ---------- model ----------
    def _spectrogram(self, a):
        pad = self.nfft // 2
        if len(a) <= pad + 1:
            return None
        a = np.pad(a, (pad, pad), mode='reflect')
        n = 1 + (len(a) - self.nfft) // self.hop
        idx = np.arange(self.nfft)[None, :] + self.hop * np.arange(n)[:, None]
        S = np.abs(np.fft.rfft(a[idx] * self.win, n=self.nfft, axis=1)).astype(np.float32)
        if self.tone is None:
            spec = S[:, self.b0:self.b0 + self.nb]
            return normalize_spectrogram(spec, self.meta)[None, None]
        # selected station only, moved to the model's centre (as the DeepCW app's filter does)
        half = int(math.ceil(self.width_hz / 2 / self.bin_hz))
        tb = int(round(self.tone / self.bin_hz))
        cb = int(round(self.centre / self.bin_hz)) - self.b0
        spec = np.zeros((n, self.nb), dtype=np.float32)
        lo, hi = max(-half, -tb, -cb), min(half, S.shape[1] - 1 - tb, self.nb - 1 - cb)
        spec[:, cb + lo:cb + hi + 1] = S[:, tb + lo:tb + hi + 1]
        pop = spec[:, cb + lo:cb + hi + 1]
        q = float(self.meta.get('noise_level_quantile', 0.9))
        flat = np.sort(pop.reshape(-1))
        noise = max(float(flat[int(q * (len(flat) - 1))]) / math.sqrt(-2 * math.log(1 - q)),
                    float(flat[-1]) * 1e-3, 1e-5)
        return np.log1p(spec / noise).astype(np.float32)[None, None]

    def _run(self, start_frame, end_sample, final):
        s0 = start_frame * self.st.hop_length
        seg = self.audio[max(0, s0 - self.off):max(0, end_sample - self.off)]
        spec = self._spectrogram(seg)
        if spec is None:
            return ''
        logits = self.sess.run([self.meta['onnx_output_name']],
                               {self.meta['onnx_input_name']: spec})[0][0]
        self.st.add_window(start_frame, logits, final)
        committed, _ = self.st.take(final)
        # drop audio the stitcher will never look at again
        keep_from = self.st.context_start_frame * self.st.hop_length
        if keep_from - self.off > 4 * self.sr:
            cut = keep_from - self.off
            self.audio = self.audio[cut:]
            self.off += cut
        if committed is None or not committed.text:
            return ''
        text = (' ' if committed.prepend_space and self.transcript else '') + committed.text
        self.transcript = (self.transcript + text)[-2000:]
        return text

    def _step(self, end_sample, final):
        return self._run(self.st.window_start_frame(end_sample), end_sample, final)
