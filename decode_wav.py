"""Decode a WAV file offline:  python decode_wav.py recording.wav"""
import sys
import wave
import numpy as np
from decoder import CWDecoder

with wave.open(sys.argv[1]) as w:
    fs, ch, sw = w.getframerate(), w.getnchannels(), w.getsampwidth()
    raw = w.readframes(w.getnframes())
x = np.frombuffer(raw, {1: np.uint8, 2: np.int16, 4: np.int32}[sw]).astype(np.float32)
x = x.reshape(-1, ch)[:, 0]
d = CWDecoder(fs=fs)
out = []
for i in range(0, len(x), 1024):
    out += d.process(x[i:i + 1024])
print(''.join(out + d.flush()))
print(f'\n~{d.wpm:.0f} WPM, pitch {d.tone:.0f} Hz')
