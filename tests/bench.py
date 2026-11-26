import sys, time; sys.path[:0] = ['.', 'tests']

from synth import keying, audio
from decoder import CWDecoder
TEXT = "CQ CQ DE W1AW W1AW K TNX FER CALL UR RST 599 NAME BOB QTH NEWINGTON CT HW? <KN> " * 3
x = audio(keying(TEXT, 25, jitter=.05), snr_db=12)
secs = len(x) / 48000
d = CWDecoder(); t = time.perf_counter(); out = []
for i in range(0, len(x), 1024): out += d.process(x[i:i+1024])
el = time.perf_counter() - t
print(f'{secs:.0f}s audio in {el*1000:.0f} ms  -> {secs/el:.0f}x real time, CPU {100*el/secs:.2f}%')
print(''.join(out)[:70])
