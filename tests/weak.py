"""Weak-signal benchmark: accuracy vs SNR (several seeds), for before/after comparisons."""
import sys, difflib
sys.path[:0] = ['.', 'tests']
import numpy as np
from synth import keying, audio
from decoder import CWDecoder
TEXT = "CQ CQ DE W1AW W1AW K TNX FER CALL UR RST 599 NAME BOB QTH NEWINGTON CT HW? <KN>"
def run(wpm, snr, seed):
    rng = np.random.default_rng(seed)
    x = audio(keying(TEXT, wpm, jitter=0.05, rng=rng), snr_db=snr, rng=rng)
    d = CWDecoder(tone=650); out = []
    for i in range(0, len(x), 1024): out += d.process(x[i:i+1024])
    out += d.flush()
    s = ' '.join(''.join(out).split())
    return difflib.SequenceMatcher(None, s, TEXT).ratio()
print('snr  ' + ' '.join(f'{w:>5}' for w in (12, 18, 25, 32)))
for snr in (9, 6, 3, 0):
    row = [np.mean([run(w, snr, s) for s in range(4)]) for w in (12, 18, 25, 32)]
    print(f'{snr:>3}dB ' + ' '.join(f'{r:5.2f}' for r in row), f' avg {np.mean(row):.3f}')
