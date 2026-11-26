import sys, numpy as np
sys.path.insert(0, '..'); sys.path.insert(0, '.')
from morse import tokenize, code_for

def keying(text, wpm, farns=None, jitter=0.0, rng=None):
    """Return list of (on:bool, ms)."""
    rng = rng or np.random.default_rng(1)
    dit = 1200 / wpm
    gap = 1200 / farns if farns else dit
    j = lambda d: d * (1 + rng.normal(0, jitter))
    seq = []
    for tok in tokenize(text):
        if tok == ' ':
            seq.append((False, j(7 * gap - 3 * gap))); continue
        for k, el in enumerate(code_for(tok)):
            if k: seq.append((False, j(dit)))
            seq.append((True, j(dit if el == '.' else 3 * dit)))
        seq.append((False, j(3 * gap)))
    return seq

def audio(seq, fs=48000, tone=700, snr_db=15, rng=None):
    rng = rng or np.random.default_rng(2)
    parts = [np.zeros(int(fs * 0.5))]
    for on, ms in seq:
        n = int(fs * ms / 1000)
        parts.append(np.full(n, 1.0 if on else 0.0))
    parts.append(np.zeros(int(fs * 0.8)))
    env = np.concatenate(parts)
    # 4 ms raised-cosine edges
    k = int(fs * 0.004); w = np.hanning(2 * k); w /= w.sum()
    env = np.convolve(env, w, 'same')
    t = np.arange(len(env)) / fs
    sig = env * np.sin(2 * np.pi * tone * t)
    noise = rng.normal(0, 1, len(sig)) * (0.7071 / 10 ** (snr_db / 20)) * np.sqrt(fs / 500 / 2)
    return (sig + noise).astype(np.float32)
