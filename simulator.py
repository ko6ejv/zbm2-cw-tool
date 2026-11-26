"""SIMULATOR - practice real-feeling CW contacts without transmitting.

Replaces the radio: a fake rig takes whatever you "send", and simulated operators
answer through your speakers (and into the decoder) with realistic noise, fading,
static and pileups. Every tab works in SIM mode, so CHAT and CONTEST can be practiced too.
"""
import random
import re
import threading
import time

import numpy as np

from morse import tokenize, code_for

FS = 48000
BLOCK = 1024

ROSTER = [('W1AW', 'BOB', 'CT', 'NEWINGTON CT', 'FN31', '3A', 'CT'), ('K6XYZ', 'JIM', 'CA', 'SAN JOSE CA', 'CM97', '2A', 'SCV'),
          ('N7ABC', 'SUE', 'OR', 'BEND OR', 'CN94', '1D', 'OR'), ('W5TX', 'RAY', 'TX', 'AUSTIN TX', 'EM10', '1E', 'STX'),
          ('KD9QRP', 'ANN', 'IL', 'PEORIA IL', 'EN50', '1B', 'IL'), ('VE3OTT', 'PAT', 'ON', 'OTTAWA ON', 'FN25', '2A', 'ONE'),
          ('AC0KS', 'TOM', 'KS', 'WICHITA KS', 'EM17', '1D', 'KS'), ('W4FL', 'DAN', 'FL', 'TAMPA FL', 'EL87', '4A', 'WCF'),
          ('KB2NY', 'LIZ', 'NY', 'BUFFALO NY', 'FN02', '1D', 'WNY'), ('N0MN', 'GUS', 'MN', 'DULUTH MN', 'EN46', '2E', 'MN')]
RIGS = ['IC7300', 'K3', 'FT991A', 'TS590', 'KX2', 'FT710']
ANTS = ['DIPOLE', 'EFHW', 'VERTICAL', 'YAGI', 'LOOP']
WX = ['SUNNY 72F', 'CLOUDY 55F', 'RAIN 48F', 'CLEAR 30F', 'WINDY 65F']


def element_env(text, wpm, jitter=0.05, farns=None, rng=random):
    """0/1 keying envelope (sample-accurate) for text, with human-ish timing jitter."""
    dit = 1.2 / wpm
    gap = 1.2 / farns if farns and farns < wpm else dit
    j = lambda d: max(0.3 * d, d * (1 + rng.gauss(0, jitter)))
    parts = []
    for tok in tokenize(text):
        if tok == ' ':
            parts.append(np.zeros(int(FS * j(4 * gap))))
            continue
        for i, el in enumerate(code_for(tok)):
            if i:
                parts.append(np.zeros(int(FS * j(dit))))
            parts.append(np.ones(int(FS * j(dit if el == '.' else 3 * dit))))
        parts.append(np.zeros(int(FS * j(3 * gap))))
    env = np.concatenate(parts) if parts else np.zeros(1)
    k = int(FS * 0.004)                                  # 4 ms raised-cosine edges
    w = np.hanning(2 * k)
    return np.convolve(env, w / w.sum(), 'same')


class SimStation:
    def __init__(self, info, tone, wpm, snr_db):
        self.call, self.name, self.state, self.qth, self.grid, self.cls, self.sec = info
        self.tone, self.wpm, self.snr_db = tone, wpm, snr_db
        self.rig, self.ant, self.wx = random.choice(RIGS), random.choice(ANTS), random.choice(WX)
        self.queue = []          # pending envelopes
        self.pos = 0
        self.phase = random.random() * 6.28
        self.stage = 'idle'
        self.last_said = ''
        self.next_cq = time.time() + random.uniform(2, 5)
        self.calls_left = 0
        self.next_call = 0.0

    def say(self, text, delay=0.0):
        self.last_said = text
        env = element_env(text, self.wpm)
        if delay > 0:
            env = np.concatenate([np.zeros(int(FS * delay)), env])
        self.queue.append(env)

    @property
    def busy(self):
        return bool(self.queue)

    def render(self, n, t0, qsb):
        out = np.zeros(n, dtype=np.float32)
        filled = 0
        while filled < n and self.queue:
            env = self.queue[0]
            take = min(n - filled, len(env) - self.pos)
            out[filled:filled + take] = env[self.pos:self.pos + take]
            filled += take
            self.pos += take
            if self.pos >= len(env):
                self.queue.pop(0)
                self.pos = 0
        if not out.any():
            return out
        t = (t0 + np.arange(n)) / FS
        amp = 10 ** ((self.snr_db - 20) / 20)
        if qsb:
            amp *= 0.55 + 0.45 * np.sin(2 * np.pi * 0.12 * t + self.phase)
        return (out * amp * np.sin(2 * np.pi * self.tone * t)).astype(np.float32)


class SimRig:
    """Fake radio: whatever the app 'transmits' is handed to the simulated operators."""
    name = 'SIMULATOR'
    can_send_text = True

    def __init__(self, sim):
        self.sim = sim

    def connect(self): pass
    def close(self): pass
    def get_freq(self): return 7040000
    def get_mode(self): return ('CW', '500')
    def set_cw_mode(self): return True
    def set_data_mode(self): return True
    def set_freq(self, hz): return True
    def set_wpm(self, wpm): self.sim.user_wpm = wpm; return True
    def stop_morse(self): return True
    def ptt(self, on): return True

    def send_text(self, text):
        self.sim.heard(text)
        return True


class Simulator:
    SCENARIOS = {
        'answer': 'A station calls CQ - you answer it',
        'run': 'You call CQ - stations answer you',
        'pileup': 'You call CQ - a pileup answers',
        'sst': 'Contest: K1USN SST (you call CQ SST)',
        'fd': 'Contest: Field Day (you call CQ FD)',
        'pota': 'POTA: hunt a park activator',
    }

    def __init__(self, cfg, scenario='answer', wpm=15, snr_db=15, qsb=False, qrn=False, callers=3):
        self.cfg = cfg
        self.my = cfg['call'].upper()
        self.my_name = (cfg.get('name') or 'OP').split()[0].upper()
        self.scenario, self.wpm, self.snr_db = scenario, wpm, snr_db
        self.qsb, self.qrn, self.callers = qsb, qrn, callers
        self.rig = SimRig(self)
        self.user_wpm = wpm
        self.heard_buf, self.last_heard = '', 0.0
        self.stations = []
        self.partner = None
        self.run = False
        self.t = 0
        self.lock = threading.Lock()
        self._spawn_scenario()

    # ---------- roster ----------
    def _new_station(self, tone=None, snr=None, wpm=None):
        used = {s.call for s in self.stations}
        info = random.choice([r for r in ROSTER if r[0] not in used] or ROSTER)
        tone = tone or random.choice([560, 600, 640, 700, 740, 780, 820])
        st = SimStation(info, tone, wpm or max(5, self.wpm + random.choice([-2, -1, 0, 1, 2])),
                        snr if snr is not None else self.snr_db + random.uniform(-3, 3))
        self.stations.append(st)
        return st

    def _spawn_scenario(self):
        self.stations = []
        if self.scenario in ('answer', 'pota'):
            st = self._new_station(tone=self.cfg.get('tone', 700))
            st.stage = 'cq'
            self.partner = st

    # ---------- audio ----------
    def start(self, sink):
        self.sink = sink
        self.run = True
        threading.Thread(target=self._loop, daemon=True).start()

    def stop(self):
        self.run = False

    def _loop(self):
        out = None
        try:
            import sounddevice as sd
            from engine import find_dev
            out = sd.OutputStream(samplerate=FS, channels=1, blocksize=BLOCK, dtype='float32',
                                  device=find_dev(self.cfg.get('sidetone_out', ''), 'output'))
            out.start()
        except Exception:
            out = None
        nxt = time.perf_counter()
        rng = np.random.default_rng()
        noise_amp = 0.7071 * np.sqrt(FS / 500 / 2) * 10 ** (-20 / 20)
        while self.run:
            with self.lock:
                self._behave()
                blk = rng.normal(0, noise_amp, BLOCK).astype(np.float32)
                for st in self.stations:
                    blk += st.render(BLOCK, self.t, self.qsb)
            if self.qrn and rng.random() < 0.04:                    # static crashes
                i = rng.integers(0, BLOCK - 200)
                blk[i:i + 200] += rng.normal(0, noise_amp * 25, 200).astype(np.float32)
            self.t += BLOCK
            blk *= 0.08                                             # comfortable level
            self.sink(blk)
            if out is not None:
                try:
                    out.write(blk.reshape(-1, 1))                   # paces us in real time
                    continue
                except Exception:
                    out = None
            nxt += BLOCK / FS
            d = nxt - time.perf_counter()
            if d > 0:
                time.sleep(d)
            else:
                nxt = time.perf_counter()
        if out is not None:
            try:
                out.stop()
                out.close()
            except Exception:
                pass

    # ---------- what the user sent ----------
    def heard(self, text):
        """Text reaches us the moment the keyer starts; they only 'hear the end' after its airtime."""
        from rig import element_timeline
        air = sum(d for _, d in element_timeline(text + ' ', max(5, self.user_wpm)))
        with self.lock:
            self.heard_buf += ' ' + text.upper()
            self.last_heard = max(self.last_heard, time.time()) + air

    def _behave(self):
        now = time.time()
        # stations calling CQ on their own
        for st in self.stations:
            if st.stage == 'cq' and not st.busy and now > st.next_cq and now - self.last_heard > 4:
                if self.scenario == 'pota':
                    st.say(f'CQ POTA DE {st.call} {st.call} K')
                else:
                    st.say(f'CQ CQ CQ DE {st.call} {st.call} K')
                st.next_cq = now + random.uniform(9, 14)
        # pileup callers keep calling until you pick one (a few tries, then they give up)
        for st in self.stations:
            if (st.stage == 'calling' and not st.busy and st.calls_left > 0 and now > st.next_call
                    and now - self.last_heard > 2.5):
                st.calls_left -= 1
                st.next_call = now + random.uniform(4.5, 7.0)
                st.say(st.call if self.scenario == 'sst' else f'{st.call} {st.call}', random.uniform(0.1, 0.8))
        # respond ~1.2 s after the user stops sending
        if not self.heard_buf or now - self.last_heard < 1.2:
            return
        msg = ' '.join(self.heard_buf.split())
        self.heard_buf = ''
        self._respond(msg)

    def _respond(self, msg):
        my, words = self.my, msg.split()
        d = random.uniform(0.3, 0.9)
        # repeat / slow down requests go to the current partner
        p = self.partner
        if p and re.search(r'\b(QRS)\b', msg):
            p.wpm = max(5, p.wpm - 4)
            p.say(p.last_said, d)
            return
        if p and re.search(r'(^|\s)(AGN\??|\?|RPT)(\s|$)', msg) and not re.search(r'\bCQ\b', msg):
            p.say(p.last_said, d)
            return
        if words and words[0] == 'CQ':
            self._cq_answered(msg)
            return
        # QRL? -> silence means clear (nobody answers)
        if 'QRL' in words:
            return
        if not p:
            return
        sc = self.scenario
        if sc in ('sst', 'fd', 'pileup', 'run') and p.stage == 'calling':
            picked = self.pick(words) or next((s for s in self.stations if s.stage == 'calling' and any(
                s.call.startswith(w.rstrip('?')) for w in words if len(w) >= 3)), None)
            if picked:
                p = self.partner = picked
                # you picked them: they send their exchange
                for o in self.stations:
                    if o is not p:
                        o.queue.clear()
                self.stations = [p]
                p.stage = 'exch'
                if sc == 'sst':
                    p.say(f'TU {self.my_name} {p.name} {p.state}', d)
                elif sc == 'fd':
                    p.say(f'TU {p.cls} {p.sec}', d)
                else:
                    p.say(f'{my} DE {p.call} GE TNX FER CALL UR RST 5{random.choice("6789")}9 5NN NAME {p.name} '
                          f'{p.name} QTH {p.qth} HW? {my} DE {p.call} KN', d)
            return
        if p.stage == 'exch' and sc in ('sst', 'fd'):
            if words and words[0] == 'TU':
                self._next_caller(sc)
            return
        if p.stage == 'cq' and p.call in words and my in words:
            # you answered their CQ
            p.stage = 'report'
            if sc == 'pota':
                p.say(f'{my} TU 5NN {p.state} {p.state}', d)
            else:
                p.say(f'{my} DE {p.call} GE TNX FER CALL UR RST 5{random.choice("789")}9 5{random.choice("789")}9 '
                      f'NAME {p.name} {p.name} QTH {p.qth} {p.qth} HW? {my} DE {p.call} KN', d)
            return
        if p.stage in ('report', 'exch'):
            if sc == 'pota':
                p.say(f'TU 73 {p.call} K-1234', d)
                self._wrap(p)
                return
            if re.search(r'\b(73|<SK>|SK)\b', msg):
                p.say(f'R TU {self.my_name} 73 GL {my} DE {p.call} <SK> EE', d)
                self._wrap(p)
                return
            # they got your report: a little rag-chew, then 73
            p.stage = 'chat'
            p.say(f'R FB {self.my_name} TNX FER RPT RIG HR {p.rig} ANT {p.ant} WX {p.wx} '
                  f'HW? {my} DE {p.call} KN', d)
            return
        if p.stage == 'chat':
            p.say(f'R FB {self.my_name} TNX FER NICE QSO 73 ES GL {my} DE {p.call} <SK> EE', d)
            self._wrap(p)

    def _cq_answered(self, msg):
        sc = self.scenario
        if sc in ('answer', 'pota'):
            return                                   # in these you answer them
        n = {'run': 1, 'pileup': max(2, self.callers)}.get(sc, random.choice([1, 1, 2]))
        self.stations = [s for s in self.stations if s.busy]
        tones = random.sample([520, 590, 660, 730, 800, 870], k=min(n, 6))
        for i in range(n):
            st = self._new_station(tone=tones[i], snr=self.snr_db + random.uniform(-6, 4))
            st.stage = 'calling'
            st.calls_left, st.next_call = 3, time.time() + random.uniform(5, 7)
            st.say(f'{st.call} {st.call}' if sc != 'sst' else f'{st.call}', random.uniform(0.4, 1.4))
        self.partner = self.stations[-1] if n == 1 else None
        if n > 1:
            self.partner = self.stations[0]           # whoever you pick becomes partner (matched in _respond)
            for st in self.stations:
                st.stage = 'calling'
        self._pile = list(self.stations)

    def pick(self, words):
        for st in self.stations:
            if st.call in words:
                self.partner = st
                return st
        return None

    def _next_caller(self, sc):
        st = self._new_station(snr=self.snr_db + random.uniform(-4, 4))
        st.stage = 'calling'
        st.calls_left, st.next_call = 3, time.time() + 6
        st.say(f'{st.call}', random.uniform(1.0, 2.0))
        self.stations = [st]
        self.partner = st

    def _wrap(self, p):
        p.stage = 'done'

        def later():
            time.sleep(12)
            with self.lock:
                if self.scenario in ('answer', 'pota'):
                    self.stations = [s for s in self.stations if s.busy]
                    st = self._new_station(tone=self.cfg.get('tone', 700))
                    st.stage = 'cq'
                    self.partner = st
        threading.Thread(target=later, daemon=True).start()
