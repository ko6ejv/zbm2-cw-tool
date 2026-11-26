import sys; sys.path[:0] = ['.', 'tests']

from synth import keying, audio
from decoder import CWDecoder

TEXT = "CQ CQ DE W1AW W1AW K TNX FER CALL UR RST 599 NAME BOB QTH NEWINGTON CT HW? <KN>"
def run(text, wpm, snr=15, jit=0.05, tone=700, farns=None, seq=None):
    seq = seq or keying(text, wpm, farns, jit)
    x = audio(seq, tone=tone, snr_db=snr)
    d = CWDecoder(tone=650)
    out = []
    for i in range(0, len(x), 1024):
        out += d.process(x[i:i+1024])
    out += d.flush()
    s = ''.join(out).strip()
    s = ' '.join(s.split())
    return s, d.wpm, d.tone

def score(a, b):
    import difflib; return difflib.SequenceMatcher(None, a, b).ratio()

for wpm in (8, 13, 18, 25, 32, 40):
    for snr in (20, 10, 6):
        s, w, t = run(TEXT, wpm, snr)
        print(f"{wpm:2d}wpm snr{snr:2d}dB -> est {w:5.1f} tone {t:5.0f} acc {score(s, TEXT):.2f}  {s[:60]}")
s, w, t = run(TEXT, 25, 15, farns=12); print("farns 25/12", round(w,1), round(score(s,TEXT),2), s[:60])
seq = keying(TEXT, 15) + keying(TEXT, 30)
s, w, t = run(None, 0, seq=seq); print("15->30 change est", round(w,1), round(score(s, TEXT+' '+TEXT),2)); print(s)
