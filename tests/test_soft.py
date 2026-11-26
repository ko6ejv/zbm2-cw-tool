"""Soft decoder, tone finder, combiner."""
import difflib
import sys, os
sys.path[:0] = [os.path.dirname(os.path.dirname(os.path.abspath(__file__))), os.path.dirname(os.path.abspath(__file__))]
import numpy as np
from synth import keying, audio
from softdec import SoftCW
from tonefind import ToneFinder
from combine import Combiner

TXT = 'CQ CQ DE W6JL W6JL K KO6EJV DE W6JL TNX FER CALL UR RST 449 449 NAME JOHN QTH SEATTLE WA HW?'


def _mix(snr_db, tone=700, wpm=20, seed=1):
    rng = np.random.default_rng(seed)
    sig = audio(keying(TXT, wpm, jitter=0.05, rng=rng), tone=tone, snr_db=200, rng=rng)
    sig = sig / np.sqrt(np.mean(sig ** 2))
    # noise in a 500 Hz band around the tone (like the radio's CW filter), unit power in band
    n = rng.normal(0, 1, len(sig) + 48000 * 4)
    N = np.fft.rfft(n); f = np.fft.rfftfreq(len(n), 1 / 48000)
    N[(f < tone - 250) | (f > tone + 250)] = 0
    n = np.fft.irfft(N, len(n)); n /= np.sqrt(np.mean(n ** 2))
    x = n.copy(); x[48000 * 2:48000 * 2 + len(sig)] += sig * 10 ** (snr_db / 20)
    return x.astype(np.float32) * 0.01


def _soft(x, tone=700):
    sc = SoftCW(48000); out = ''
    for i in range(0, len(x), 2048):
        out += sc.text(x[i:i + 2048], tone=tone)
    return out + ''.join(t for t, _ in sc.flush()), sc


def test_soft_copies_weak_signal():
    out, sc = _soft(_mix(-3))
    assert difflib.SequenceMatcher(None, ' '.join(out.split()), TXT).ratio() > 0.75, out
    assert 17 < 1200 / sc.dit_ms < 23            # found the speed by itself


def test_soft_quiet_on_noise():
    rng = np.random.default_rng(5)
    out, _ = _soft((rng.normal(0, 0.01, 48000 * 40)).astype(np.float32))
    assert len(out.strip()) < 8, out


def test_tonefinder_finds_signal():
    x = _mix(3, tone=880)
    tf = ToneFinder(48000)
    for i in range(0, len(x), 1024):
        tf.feed(x[i:i + 1024])
    assert tf.tone is not None and abs(tf.tone - 880) < 15


def test_combiner_no_double_print():
    c = Combiner()
    c.update_mode(None, 0.0, soft_open=True)
    a = c.soft([('C', 1.0), ('Q', 1.3), (' ', 1.6)])
    b = c.soft([('Q', 1.3), ('D', 2.0)])          # older stamp repeated: dropped
    assert a == 'CQ ' and b == 'D'
