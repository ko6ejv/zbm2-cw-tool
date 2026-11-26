"""CW transmit: keying methods + PTT.

Keying (cfg 'key_method'):
  rigkeyer - text goes to the radio's internal keyer via CAT (Hamlib send_morse)
  serial   - software-timed DTR or RTS on a COM port
  audio    - shaped sine tones into the rig's USB audio (rig in DATA-U / USB)
PTT (cfg 'ptt_method'):  CAT | RTS | DTR | VOX   (break-in CW usually needs none)
"""
import threading
import time
import queue

from morse import tokenize, code_for

# ---------------- serial lines (shared per COM port) ----------------
_ports = {}
_ports_lock = threading.Lock()


class SerialLines:
    def __init__(self, name):
        import serial
        s = serial.Serial()
        s.port = name
        s.baudrate = 9600
        s.dtr = False          # set BEFORE open, or Windows asserts DTR and keys the rig
        s.rts = False
        s.open()
        self.ser = s

    def set(self, line, state):
        if line.upper() == 'DTR':
            self.ser.dtr = state
        else:
            self.ser.rts = state

    def close(self):
        try:
            self.ser.dtr = False
            self.ser.rts = False
            self.ser.close()
        except Exception:
            pass


def get_port(name):
    with _ports_lock:
        p = _ports.get(name)
        if p is None or not p.ser.is_open:
            p = _ports[name] = SerialLines(name)
        return p


def close_ports():
    with _ports_lock:
        for p in _ports.values():
            p.close()
        _ports.clear()


# ---------------- timing helpers ----------------
def _sleep_until(t_end, abort):
    while True:
        left = t_end - time.perf_counter()
        if left <= 0 or abort.is_set():
            return
        if left > 0.003:
            time.sleep(left - 0.002)


def element_timeline(text, wpm, farnsworth=None):
    dit = 1.2 / wpm
    g = 1.2 / farnsworth if farnsworth and farnsworth < wpm else dit
    seq = []
    for tok in tokenize(text):
        if tok == ' ':
            if seq and not seq[-1][0]:
                seq[-1] = (False, 7 * g)
            continue
        for i, el in enumerate(code_for(tok)):
            if i:
                seq.append((False, dit))
            seq.append((True, dit if el == '.' else 3 * dit))
        seq.append((False, 3 * g))
    return seq


def tone_waveform(text, wpm, fs=48000, tone=700.0, rise_ms=5.0, amp=0.5, farnsworth=None):
    """Click-free CW audio (raised-cosine edges)."""
    import numpy as np
    seq = element_timeline(text, wpm, farnsworth)
    env = np.concatenate([np.full(int(fs * d), 1.0 if on else 0.0) for on, d in seq] +
                         [np.zeros(int(fs * 0.05))])
    k = max(1, int(fs * rise_ms / 1000))
    w = np.hanning(2 * k)
    w /= w.sum()
    env = np.convolve(env, w, 'same')
    t = np.arange(len(env)) / fs
    return (amp * env * np.sin(2 * np.pi * tone * t)).astype(np.float32)


def play_sidetone(text, wpm, device=None, tone=600, level=0.2):
    import sounddevice as sd
    sd.play(tone_waveform(text, wpm, 48000, tone, amp=level), 48000, device=device)


PROSIGN_PLAIN = (('<SK>', 'SK'), ('<KN>', 'KN'), ('<AR>', '+'), ('<BT>', '='),
                 ('<BK>', 'BK'), ('<AS>', 'AS'), ('<CT>', 'CT'), ('<HH>', 'HH'))


def chunks(text, n=24):
    out, cur = [], ''
    for w in text.split():
        if cur and len(cur) + 1 + len(w) > n:
            out.append(cur)
            cur = w
        else:
            cur = f'{cur} {w}'.strip()
    return out + ([cur] if cur else [])


# ---------------- transmitter ----------------
_STREAM, _END = object(), object()


class Transmitter:
    """Message queue sent on a worker thread.
    send(text)          - whole message
    stream_word(word)   - type-ahead: words go out as typed; PTT held until stream_end()
    Callbacks: on_start(text), on_char(token), on_done()."""

    def __init__(self, on_start=None, on_char=None, on_done=None):
        self.q = queue.Queue()
        self.stream_q = queue.Queue()
        self.streaming = False
        self.abort = threading.Event()
        self.busy = threading.Event()
        self.on_start, self.on_char, self.on_done = on_start, on_char, on_done
        self.rig = None
        self.cfg = {}
        self.wpm = 20
        self.tone = 700
        self.audio_dev = None
        threading.Thread(target=self._run, daemon=True).start()

    def configure(self, rig, cfg, audio_dev=None):
        self.rig, self.cfg, self.audio_dev = rig, dict(cfg), audio_dev

    def send(self, text):
        self.abort.clear()
        self.q.put(text)

    def stream_word(self, word):
        self.abort.clear()
        if not self.streaming:
            self.streaming = True
            self.q.put(_STREAM)
        self.stream_q.put(word)

    def stream_end(self, tail=''):
        if tail:
            self.stream_word(tail)
        if self.streaming:
            self.stream_q.put(_END)

    def stop(self):
        self.abort.set()
        for qq in (self.q, self.stream_q):
            while not qq.empty():
                try:
                    qq.get_nowait()
                except queue.Empty:
                    break
        if self.streaming:
            self.stream_q.put(_END)
        if self.cfg.get('key_method') == 'rigkeyer' and self.rig:
            self.rig.stop_morse()
        self._safe_unkey()

    # --- PTT ---
    def _ptt(self, on):
        m = self.cfg.get('ptt_method', 'VOX')
        if m == 'CAT' and self.rig:
            self.rig.ptt(on)
        elif m in ('RTS', 'DTR'):
            get_port(self.cfg['ptt_port']).set(m, on)

    def _ptt_used(self):
        km = self.cfg.get('key_method', 'rigkeyer')
        return km == 'audio' or (km == 'serial' and self.cfg.get('ptt_method') in ('CAT', 'RTS', 'DTR'))

    def _key_on(self):
        if self._ptt_used():
            self._ptt(True)
            time.sleep(self.cfg.get('ptt_lead_ms', 100) / 1000)

    def _key_off(self):
        if self._ptt_used():
            time.sleep(self.cfg.get('ptt_tail_ms', 50) / 1000)
        self._safe_unkey()

    def _safe_unkey(self):
        try:
            if self.cfg.get('key_method') == 'serial' and self.cfg.get('key_port'):
                get_port(self.cfg['key_port']).set(self.cfg.get('key_line', 'DTR'), False)
        except Exception:
            pass
        try:
            self._ptt(False)
        except Exception:
            pass

    # --- worker ---
    def _run(self):
        while True:
            job = self.q.get()
            self.busy.set()
            try:
                if job is _STREAM:
                    self._do_stream()
                else:
                    if self.on_start:
                        self.on_start(job)
                    self._key_on()
                    self._emit(job)
                    self._key_off()
            except Exception as e:
                if self.on_char:
                    self.on_char(f' [TX ERROR: {e}] ')
            finally:
                self._safe_unkey()
                self.streaming = False if job is _STREAM else self.streaming
                self.busy.clear()
                if self.on_done:
                    self.on_done()

    def _do_stream(self):
        if self.on_start:
            self.on_start('')
        self._key_on()
        while not self.abort.is_set():
            try:
                w = self.stream_q.get(timeout=10)        # idle 10 s -> end the over
            except queue.Empty:
                break
            if w is _END:
                break
            self._emit(w + ' ')
        self._key_off()

    def _emit(self, text):
        m = self.cfg.get('key_method', 'rigkeyer')
        if m == 'rigkeyer':
            self._emit_rigkeyer(text)
        elif m == 'audio':
            self._emit_audio(text)
        else:
            self._emit_serial(text)

    def _echo(self, text):
        for tok in tokenize(text):
            if self.on_char:
                self.on_char(tok)

    def _emit_rigkeyer(self, text):
        if not self.rig or not self.rig.can_send_text:
            raise RuntimeError(f'{getattr(self.rig, "name", "No CAT")} cannot send CW text - '
                               'use Serial or Audio keying in Rig Setup')
        self.rig.set_wpm(self.wpm)
        if hasattr(self.rig, 'ensure_breakin') and self.cfg.get('ptt_method', 'VOX') == 'VOX':
            self.rig.ensure_breakin()
        plain = text.upper()
        for p, rep in PROSIGN_PLAIN:
            plain = plain.replace(p, rep)
        for ch in chunks(plain):
            if self.abort.is_set():
                return
            if not self.rig.send_text(ch):
                raise RuntimeError('rig rejected send_morse - use Serial or Audio keying')
            t_end = time.perf_counter() + sum(d for _, d in element_timeline(ch + ' ', self.wpm))
            self._echo(ch + ' ')
            _sleep_until(t_end, self.abort)

    def _stereo(self, mono):
        import numpy as np
        ch = self.cfg.get('audio_out_ch', 'Both')
        if ch == 'Left':
            return np.column_stack([mono, np.zeros_like(mono)])
        if ch == 'Right':
            return np.column_stack([np.zeros_like(mono), mono])
        return np.column_stack([mono, mono])

    def _drive(self):
        return max(0.0, min(1.0, float(self.cfg.get('tx_drive', 50)) / 100.0)) * 0.9

    def _play(self, mono):
        import sounddevice as sd
        sd.play(self._stereo(mono), 48000, device=self.audio_dev)
        t_end = time.perf_counter() + len(mono) / 48000
        while time.perf_counter() < t_end:
            if self.abort.is_set():
                sd.stop()
                break
            time.sleep(0.01)

    def _emit_audio(self, text):
        self._echo(text)
        self._play(tone_waveform(text, self.wpm, 48000, self.tone, amp=self._drive()))

    def tune(self, seconds=10):
        """Steady carrier for ALC / drive setting (audio keying)."""
        import numpy as np

        def job():
            self.busy.set()
            self.abort.clear()
            try:
                t = np.arange(int(48000 * seconds)) / 48000
                env = np.minimum(1, np.minimum(t, t[::-1]) / 0.005)
                self._ptt(True)
                time.sleep(self.cfg.get('ptt_lead_ms', 100) / 1000)
                self._play((self._drive() * env * np.sin(2 * np.pi * self.tone * t)).astype(np.float32))
            except Exception as e:
                if self.on_char:
                    self.on_char(f' [TUNE ERROR: {e}] ')
            finally:
                self._safe_unkey()
                self.busy.clear()
                if self.on_done:
                    self.on_done()
        threading.Thread(target=job, daemon=True).start()

    def _emit_serial(self, text):
        port = get_port(self.cfg['key_port'])
        line = self.cfg.get('key_line', 'DTR')
        dit = 1.2 / self.wpm
        t = time.perf_counter()
        for tok in tokenize(text):
            if self.abort.is_set():
                break
            if self.on_char:
                self.on_char(tok)
            if tok == ' ':
                t += 4 * dit
                _sleep_until(t, self.abort)
                continue
            for i, el in enumerate(code_for(tok)):
                if i:
                    t += dit
                    _sleep_until(t, self.abort)
                port.set(line, True)
                t += dit if el == '.' else 3 * dit
                _sleep_until(t, self.abort)
                port.set(line, False)
                if self.abort.is_set():
                    break
            t += 3 * dit
            _sleep_until(t, self.abort)
        port.set(line, False)
