"""Neural decoder speed + accuracy check: 60 s of 20 WPM CW in noise -> real-time factor.

    python tests/bench_deepcw.py        (fails if slower than real time or copy is wrong)
Runs in CI on x86 and ARM64 (the uConsole CM4 / Raspberry Pi family).
"""
import os
import sys
import time

import numpy as np

sys.path[:0] = [os.path.dirname(os.path.dirname(os.path.abspath(__file__))), os.path.dirname(__file__)]
from synth import keying, audio      # noqa: E402
import deepcw                        # noqa: E402

TXT = 'CQ CQ DE KO6EJV KO6EJV K ' * 3 + 'UR RST 579 579 NAME DERIC QTH LA MESA CA HW? K '
if not deepcw.available():
    print('onnxruntime / model not available - skipped')
    sys.exit(0)
fs = 48000
x = audio(keying(TXT, 20, jitter=0.03), tone=700, snr_db=6).astype(np.float32)
d = deepcw.DeepCW(fs)
t0 = time.time()
out = ''
for i in range(0, len(x), 2048):
    out += d.process(x[i:i + 2048], tone=700)
out += d.flush()
secs = len(x) / fs
rtf = (time.time() - t0) / secs
print(f'{secs:.0f} s of audio decoded in {secs * rtf:.1f} s  (real-time factor {rtf:.3f})')
print('copy:', out)
ok_words = sum(w in out.split() for w in ('KO6EJV', 'DERIC', 'MESA', '579'))
assert rtf < 1.0, 'slower than real time'
assert ok_words >= 3, 'copy too poor'
print('OK')
