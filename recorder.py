"""Rolling RX audio buffer - save the last N seconds as a WAV (replay a QSO)."""
import datetime as dt
import os
import threading
import wave

import numpy as np


class Recorder:
    def __init__(self, seconds=120, fs=48000):
        self.set_rate(fs, seconds)
        self.lock = threading.Lock()

    def set_rate(self, fs, seconds=None):
        self.fs = fs
        self.seconds = seconds or getattr(self, 'seconds', 120)
        self.buf = np.zeros(int(self.fs * self.seconds), dtype=np.int16)
        self.pos = 0
        self.filled = 0

    def feed(self, x):
        s = (np.clip(x, -1, 1) * 32767).astype(np.int16)
        with self.lock:
            n = len(s)
            if n >= len(self.buf):
                self.buf[:] = s[-len(self.buf):]
                self.pos, self.filled = 0, len(self.buf)
                return
            end = self.pos + n
            if end <= len(self.buf):
                self.buf[self.pos:end] = s
            else:
                k = len(self.buf) - self.pos
                self.buf[self.pos:] = s[:k]
                self.buf[:n - k] = s[k:]
            self.pos = end % len(self.buf)
            self.filled = min(len(self.buf), self.filled + n)

    def snapshot(self, seconds=None):
        with self.lock:
            data = np.concatenate([self.buf[self.pos:], self.buf[:self.pos]])[-self.filled:]
        if seconds:
            data = data[-int(seconds * self.fs):]
        return data

    def save(self, folder, label='', seconds=None):
        os.makedirs(folder, exist_ok=True)
        name = f'{dt.datetime.now():%Y%m%d_%H%M%S}{"_" + label if label else ""}.wav'
        path = os.path.join(folder, name)
        data = self.snapshot(seconds)
        with wave.open(path, 'wb') as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(self.fs)
            w.writeframes(data.tobytes())
        return path, len(data) / self.fs
