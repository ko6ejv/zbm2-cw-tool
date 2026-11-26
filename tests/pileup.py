import sys; sys.path[:0]=['.','tests']
import numpy as np
from synth import keying, audio
from skimmer import Skimmer
def sig(text, wpm, tone, snr, delay, seed):
    rng=np.random.default_rng(seed)
    x=audio(keying(text,wpm,jitter=0.04,rng=rng),tone=tone,snr_db=200,rng=rng)   # clean, add noise once
    return np.concatenate([np.zeros(int(48000*delay)), x]) * 10**((snr-20)/20)
stations=[('W1AW W1AW W1AW TEST', 22, 600, 14, 0.0),
          ('K6XYZ K6XYZ K6XYZ TEST', 28, 700, 10, 0.4),
          ('N7ABC N7ABC N7ABC TEST', 18, 830, 8, 0.9)]
parts=[sig(t,w,f,s,d,i) for i,(t,w,f,s,d) in enumerate(stations)]
L=max(len(p) for p in parts)+48000
mix=np.zeros(L)
for p in parts: mix[:len(p)]+=p
rng=np.random.default_rng(9)
mix+=rng.normal(0,1,L)*0.7071/10**(20/20)*np.sqrt(48000/500/2)*10**(-0/20)   # noise ref
mix=mix.astype(np.float32)
sk=Skimmer()
for i in range(0,len(mix),1024): sk.process(mix[i:i+1024])
for f,call,wpm,snr,text,cid in sk.summary(): print(f'{f:6.0f} Hz  {call:8} {wpm:4.0f} wpm  {snr:4.1f} dB  {text.strip()}')
