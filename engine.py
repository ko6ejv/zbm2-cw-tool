"""ZBM2 CW Tool - radio engine (no GUI).

Owns audio, the main decoder, the pileup skimmer, rig/CAT, transmitting, copy-quality alerts,
Morse earcons, the replay recorder, logging/uploads and callsign lookups. Talks to the outside
world only through emit(kind, payload) events, so any front end (the Tk screens, a headless
console, a future remote panel) can sit on top of the same engine.

Runs fully offline; QRZ / HamQTH / LoTW are optional extras.
"""
import os
import queue
import threading
import time

import numpy as np

import online as ON
import qso as Q
import plain as PL
import rigcontrol as RC

# common CW spots per band (most are inside the Technician/General CW segments where they exist)
BAND_CW = {'160M': 1820000, '80M': 3550000, '40M': 7040000, '30M': 10115000, '20M': 14040000,
           '17M': 18080000, '15M': 21060000, '12M': 24900000, '10M': 28060000, '6M': 50090000}
from alerts import CopyMonitor
from chat import ChatRoom
from decoder import CWDecoder
from earcons import Earcons
from endings import Ender
from gridtracker import GridTrackerLink
from practice import FistGrader
from recorder import Recorder
from rig import Transmitter, play_sidetone, close_ports
from skimmer import Skimmer


# ---------------- audio device helpers (WSJT-X / VARA style labels) ----------------
def dev_labels(kind):
    import sounddevice as sd
    apis = sd.query_hostapis()
    out = []
    for d in sd.query_devices():
        ch = d['max_input_channels'] if kind == 'input' else d['max_output_channels']
        if ch > 0:
            out.append(f"{d['name']} [{apis[d['hostapi']]['name'].replace('Windows ', '')}]")
    return out


def find_dev(label, kind):
    if not label:
        return None
    import sounddevice as sd
    apis = sd.query_hostapis()
    name = label.rsplit(' [', 1)[0]
    fallback = None
    for i, d in enumerate(sd.query_devices()):
        ch = d['max_input_channels'] if kind == 'input' else d['max_output_channels']
        if ch <= 0:
            continue
        full = f"{d['name']} [{apis[d['hostapi']]['name'].replace('Windows ', '')}]"
        if full == label:
            return i
        dn = d['name']
        # MME truncates names to 31 chars, WASAPI doesn't: match either way round
        if fallback is None and (dn == name or name in dn or dn.startswith(name[:31]) or name.startswith(dn)):
            fallback = i
    return fallback


def find_dev_like(hints, kind):
    if isinstance(hints, str):
        hints = [hints]
    labels = dev_labels(kind)
    for h in hints:
        for pref in ('[MME]', ''):
            for lbl in labels:
                if h.lower() in lbl.lower() and pref in lbl:
                    return lbl
    return ''


class Engine:
    def __init__(self, cfg, emit):
        self.cfg = cfg
        self.emit = emit                     # emit(kind, payload) - called from engine threads
        self._their_wpm = None               # read by the DSP thread from its first block
        self.deep = None
        self.audio_q = queue.Queue(maxsize=200)
        self.stream = None
        self.decoder = None
        self._rxword, self._rxprev = '', ''
        self.fs = 48000
        self.in_ch = 0
        self.freq, self.mode = 0, ''
        self.rig = RC.NoRig()
        self.rig_ok, self.rig_err = False, ''
        self.gt = GridTrackerLink(cfg['gt_host'], cfg['gt_port'], cfg['gt_enable'])
        self.qso = Q.QSO(cfg['call'])
        self.tx = Transmitter(on_start=lambda t: emit('txstart', t), on_char=lambda c: emit('txchar', c),
                              on_done=self._tx_done)
        self.tx_owner = 'qso'
        self.tx_until = 0.0
        self.data_dir = cfg['data_dir']
        os.makedirs(self.data_dir, exist_ok=True)
        self.ender = Ender(cfg['call'])
        self.monitor = CopyMonitor()
        self.rec = Recorder(cfg['rec_seconds'])
        self.earcons = Earcons(on_flash=lambda t: emit('flash', t))
        self.lookup = None
        self.lookups_done = {}
        self.lotw_conf = self._load_lotw_conf()
        self.rx_grader = FistGrader()        # grades THEIR timing (machine detection)
        self.turbo_hinted = False
        self.element_sink = None             # practice tab hook: f(kind, ms, level)
        self.skimmer = Skimmer()
        self.room = ChatRoom(cfg['call'], self.data_dir)
        self.room_log = []                   # chat transcript kept even before the CHAT tab is opened
        self.sim = None                      # simulator (audio source + fake radio) when SIM is on
        self.seg = ''
        self._threads = False

    # ================= lifecycle =================
    def start(self):
        self.apply_config()
        if not self._threads:
            self._threads = True
            threading.Thread(target=self._dsp_loop, daemon=True).start()
            threading.Thread(target=self._rig_loop, daemon=True).start()
            threading.Thread(target=self._cond_loop, daemon=True).start()
        self.start_audio()
        threading.Timer(1.2, lambda: self.earcons.play('startup')).start()

    def shutdown(self):
        try:
            self.tx.stop()
            close_ports()
            self.rig.close()
        except Exception:
            pass
        self.stop_audio()

    def status(self, text, err=False):
        self.emit('status', (text, err))

    def apply_config(self):
        c = self.cfg
        try:
            self.tx.stop()
        except Exception:
            pass
        close_ports()
        old = self.rig
        self.rig = self.sim.rig if self.sim else RC.make_rig(c)
        if old is not self.rig:
            try:
                old.close()
            except Exception:
                pass
        self.tx.configure(self.rig, dict(c, key_method='rigkeyer') if self.sim else c,
                          find_dev(c['audio_out'], 'output'))
        self.gt = GridTrackerLink(c['gt_host'], c['gt_port'], c['gt_enable'])
        my = c['call'].upper()
        self.qso.my_call = self.ender.my = self.room.my = my
        self.lookup = ON.Lookup(c)
        self.lookups_done = {}
        e = self.earcons
        e.device = find_dev(c.get('sidetone_out', ''), 'output')
        e.pitch, e.volume, e.wpm = c['earcon_pitch'], c['earcon_vol'], c['earcon_wpm']
        e.enabled, e.speak = c['earcons_on'], c['earcon_speak']
        e.startup_only = c.get('earcons_startup_only', True)

    # ================= audio =================
    def start_audio(self):
        self.stop_audio()
        if self.sim:
            self.fs = 48000
            self._new_decoder(48000)
            self.sim.start(self._feed_block)
            self.status('SIMULATOR: practice audio (nothing goes on the air)')
            return
        import sounddevice as sd
        dev = find_dev(self.cfg['audio_in'], 'input')
        try:
            maxch = sd.query_devices(dev if dev is not None else sd.default.device[0])['max_input_channels']
        except Exception:
            maxch = 1
        self.in_ch = 1 if self.cfg['audio_in_ch'] == 'Right' and maxch >= 2 else 0
        nch = 2 if (self.in_ch == 1 or maxch >= 2) else 1
        err = None
        for fs in (48000, 44100):
            try:
                self.stream = sd.InputStream(device=dev, channels=nch, samplerate=fs, blocksize=1024,
                                             dtype='float32', callback=self._audio_cb)
                self.stream.start()
                self.fs = fs
                self._new_decoder(fs)
                name = sd.query_devices(self.stream.device)['name']
                self.status(f'RX audio: {name} ({self.cfg["audio_in_ch"]}) @ {fs} Hz')
                return
            except Exception as e:
                err = e
        self.status(f'Audio input failed: {err}  - open Setup Wizard or Rig Setup -> Audio', err=True)

    def _new_decoder(self, fs):
        self._their_wpm = None          # set first: the DSP thread may read it while the model loads
        self.deep = None
        self.decoder = CWDecoder(fs=fs, tone=self.cfg['tone'], auto_tone=self.cfg['auto_tone'],
                                 on_element=self._on_element)
        self.decoder.textgate_on = getattr(self, 'textgate_on', True)
        try:
            from softdec import SoftCW
            from combine import Combiner
            from tonefind import ToneFinder
            self.soft = SoftCW(fs) if self.cfg.get('soft_decoder', True) else None
            if self.soft is not None:
                self.soft.gate_on = getattr(self, 'textgate_on', True)
            self.comb = Combiner()
            self.tonefinder = ToneFinder(fs)
            self.tonefinder.tone = float(self.cfg['tone']) if not self.cfg['auto_tone'] else None
            self.decoder.auto_tone = False            # the tone finder steers both decoders now
        except Exception:
            self.soft, self.comb, self.tonefinder = None, None, None
        # neural decoder (DeepCW) prints the text when it can run; the soft decoder then only
        # meters their speed. Falls back to the soft decoder if onnxruntime / the model is missing.
        self.deep = None
        if self.cfg.get('neural_decoder', True):
            try:
                from deepcw import DeepCW, available
                if available() and fs % 3200 == 0:
                    self.deep = DeepCW(fs, width_hz=float(self.cfg.get('neural_width_hz', 250)),
                                      threads=int(self.cfg.get('neural_threads', 0)) or None)
            except Exception:
                import sys as _sys
                _sys.excepthook(*_sys.exc_info())
                self.deep = None
        self._their_wpm = None
        self.skimmer = Skimmer(fs_in=fs)
        self.rec.set_rate(fs, self.cfg.get('rec_seconds', 120))

    def stop_audio(self):
        if self.sim:
            self.sim.stop()
        if self.stream:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception:
                pass
        self.stream = None

    def _audio_cb(self, indata, frames, t, status):
        x = indata[:, self.in_ch].copy()
        g = self.cfg.get('rx_gain_db', 0)
        if g:                                   # software boost for quiet USB audio (e.g. FTX-1 in CW)
            x *= 10 ** (g / 20)
            np.clip(x, -1.0, 1.0, out=x)
        self._feed_block(x)

    def _feed_block(self, x):
        try:
            self.audio_q.put_nowait(x)
        except queue.Full:
            pass

    def set_tone(self, f=None, auto=None, lock=False):
        """f: tone in Hz. auto: follow the signal. lock: operator picked this station - follow
        its drift (+-40 Hz) but don't wander to another one."""
        if f is not None:
            self.cfg['tone'] = int(f)
        if auto is not None:
            self.cfg['auto_tone'] = bool(auto)
        tf = getattr(self, 'tonefinder', None)
        if tf is not None:
            if lock and f is not None:
                tf.locked_to, tf.tone = float(f), float(f)
            elif f is None or not self.cfg['auto_tone']:
                tf.locked_to = None
            if not self.cfg['auto_tone'] and f is not None:
                tf.tone = float(f)
        d = self.decoder
        if d:
            d.auto_tone = False if tf is not None else self.cfg['auto_tone']
            if f is not None and abs(d.tone - f) >= 5:
                d.set_tone(f)
                d.reset_timing()

    # ================= DSP =================
    def _dsp_loop(self):
        last_meter = last_spec = last_skim = 0
        ring = np.zeros(16384, dtype=np.float32)            # ~3 Hz bins for narrow spans
        win = np.hanning(16384).astype(np.float32)
        peak = 0.0
        while True:
            x = self.audio_q.get()
            d = self.decoder
            if d is None:
                continue
            self.rec.feed(x)
            peak = max(peak, float(np.abs(x).max()))
            n = len(x)
            ring[:-n] = ring[n:]
            ring[-n:] = x
            now = time.time()
            if now - last_spec > 0.15:
                last_spec = now
                spec = np.abs(np.fft.rfft(ring * win))
                fr = np.fft.rfftfreq(len(ring), 1 / self.fs)
                band = (fr >= 100) & (fr <= 2000)
                db = 20 * np.log10(spec[band] + 1e-9)
                rms = 20 * np.log10(float(np.sqrt(np.mean(ring[-4096:] ** 2))) + 1e-9)
                self.emit('spec', (fr[band], db, rms, 20 * np.log10(peak + 1e-9)))
                peak = 0.0
                if now - getattr(self, '_last_mon', 0) > 0.5 and not self.tx.busy.is_set():
                    self._last_mon = now
                    new, cleared = self.monitor.update(d, fr[band], db)
                    self.emit('alerts', (new, cleared, self.monitor.confidence))
                    self._machine_check()
            if self.tx.busy.is_set() or now < self.tx_until:
                continue                     # don't decode ourselves
            self.monitor.feed_block(x)
            tf = getattr(self, 'tonefinder', None)
            if tf is not None:
                ft = tf.feed(x)
                if self.cfg.get('auto_tone', True) and ft and abs(ft - d.tone) > 8:
                    jump = abs(ft - d.tone) > 30
                    d.set_tone(ft)
                    if jump:
                        d.reset_timing()
            out = ''.join(d.process(x))
            if self.soft is not None and self.comb is not None:
                t_s = d.t / 1000.0
                self.comb.update_mode(d.sn, t_s, self.soft._open)
                txt = self.comb.fast(out, t_s)
                if d.locked and len(d.gaps) >= 8 and d.sn is not None and d.sn >= 3:   # sender's spacing
                    c1, c2 = d._two_means(d.gaps)
                    if c2 / c1 > 1.3:
                        self.soft.sd.set_gaps(c1, c2)
                # speed tip from the fast decoder: when it's on a solid signal, or - while the soft
                # decoder has nothing yet - as soon as it has a confident lock (gets a new station's
                # speed right within a few seconds; the soft decoder then re-decodes what it missed)
                tip = (d.sn is not None and d.sn >= 3) or (not self.soft._open and d.locked and d.confident)
                # the soft decoder runs on its own thread: a 10 s re-decode (new station / click-to-lock)
                # must never stall the spectrum and text (live 2026-11-25: 'it's frozen')
                self._soft_feed((x, d.tone, d.dit if tip else None))
            else:
                txt = out
            self._update_their_wpm(d)
            if txt:
                self._on_rx(txt)
            if self.cfg.get('skimmer', True):
                for ch, s in self.skimmer.process(x):
                    if ch.call:
                        self._buddy(ch.call, ch.snr)
                if now - last_skim > 0.5:
                    last_skim = now
                    self.emit('skim', self.skimmer.summary())
            if now - last_meter > 0.1:
                last_meter = now
                sn = d.sn
                if sn is None and self.soft is not None and self.soft._open:
                    sn = self.soft.snr_db - 12.0          # soft decoder copying a weak one
                self.emit('meter', (sn, self._their_wpm or 0, self._their_wpm is not None, d.tone, d.key))

    def _on_rx(self, text):
        self.emit('rx', text)
        # every call the main decoder completes is a vote for the call fixer (runner lock)
        try:
            import callfix
            from plain import is_call
            fx = callfix.default()
            for ch in text:
                if ch == ' ':
                    if is_call(self._rxword):
                        fx.note(self._rxword, 2 if self._rxprev in ('DE', 'CQ', 'TU', 'CWT') else 1)
                    self._rxprev, self._rxword = self._rxword, ''
                else:
                    self._rxword += ch
        except Exception:
            pass
        # chat room model (works even before the CHAT tab is opened)
        seg, sender, new = self.room.feed(text, self.decoder.snr if self.decoder else 0)
        for c in new:
            self._buddy(c, self.decoder.snr if self.decoder else 0, fresh=True)
        if seg:
            self.room.log_line(sender, sender or '???', seg)
            self.room_log.append((time.time(), sender or '???', seg))
            self.room_log = self.room_log[-200:]
            self.emit('chat_seg', (seg, sender))
        # end of their over -> earcons
        self.seg += text
        if PL.SEGMENT_END.search(self.seg):
            s, self.seg = ' '.join(self.seg.split()), ''
            words = s.split()
            mine = self.cfg['call'].upper()
            partner = self.qso.call or self.room.partner
            if 'SK' in words or '<SK>' in words:
                self.earcons.play('signed_off')
            elif mine in words or (partner and partner in words):
                self.earcons.play('your_turn')
            self.emit('segment', s)
        else:
            self.seg = self.seg[-600:]

    def _buddy(self, call, snr, fresh=False):
        new = self.room.heard(call, snr) or fresh
        if new:
            self.earcons.play('new_station', call[0], min_gap=8)
            self.emit('buddy', call)

    def _on_element(self, kind, ms, level):
        self.rx_grader.element(kind, ms, level)
        d = self.decoder
        if d is not None and not self.tx.busy.is_set() and (d.locked or (d.sn or 0) >= 6):
            self.emit('elem', (kind, ms, d.dit))           # live dit/dah strip (orange = RX)
        if self.element_sink:
            self.element_sink(kind, ms, level)

    def _machine_check(self):
        """Machine-perfect timing (elements AND letter gaps) = a computer is sending. Suggest TURBO once."""
        s = self.rx_grader.stats()
        if (not s or s['n'] < 40 or self.cfg.get('turbo') or self.turbo_hinted
                or max(s['dit_cv'], s['dah_cv']) > 0.035 or s.get('char_cv', 1) > 0.08
                or not 2.85 < s['ratio'] < 3.15):
            return
        self.turbo_hinted = True
        self.emit('turbo_hint', self.qso.call or self.room.partner or '')

    def new_contact(self):
        self.rx_grader.reset()
        self.turbo_hinted = False

    # ================= rig =================
    def _rig_loop(self):
        last_hb = 0
        while True:
            rig = self.rig
            try:
                if type(rig) is not RC.NoRig:
                    if not self.tx.busy.is_set():
                        f = rig.get_freq()
                        m, pb = rig.get_mode()
                        self.freq, self.mode = f or 0, m or ''
                        self.rig_ok, self.rig_err = True, ''
                else:
                    self.rig_ok, self.rig_err = False, 'no CAT'
            except Exception as e:
                self.rig_ok, self.rig_err = False, str(e)[:120]
                time.sleep(2)
            self.emit('rig', None)
            now = time.time()
            if now - last_hb > 15:
                self.gt.heartbeat()
                last_hb = now
            self.gt.status(self.freq, self.cfg['call'], self.cfg['grid'][:4] if self.cfg['grid'] else '',
                           self.qso.call, self.qso.grid, self.tx.busy.is_set(), self.cfg['tone'])
            time.sleep(1.0)

    # ---- band conditions (N0NBH / hamqsl.com), every 30 min when online ----
    def _cond_loop(self):
        import bandcond
        while True:
            try:
                self.emit('bandcond', bandcond.fetch())
                time.sleep(1800)
            except Exception:
                time.sleep(300)

    # ---- quick band change + antenna tuner (CAT) ----
    def qsy_band(self, band):
        """Jump to a band: back to where you were last on it, else a common CW spot. -> Hz"""
        mem = self.cfg.setdefault('band_mem', {})
        cur = Q.band_for(self.freq)
        if cur and self.freq:
            mem[cur] = int(self.freq)
        hz = int(mem.get(band) or BAND_CW[band])
        if type(self.rig) is RC.NoRig:
            raise RuntimeError('no CAT connection')
        if not self.rig.set_freq(hz):
            raise RuntimeError(f'radio refused {hz / 1e6:.3f} MHz')
        self.freq = hz
        return hz

    def tune_antenna(self):
        """Start the radio's tuner (internal or external ATU) by CAT. -> (ok, message)"""
        if self.tx.busy.is_set():
            return False, 'busy transmitting'
        ok, why = self.check_tx()
        if not ok:
            return False, why
        rig = self.rig
        if not hasattr(rig, 'cmd'):
            return False, 'no CAT tuner command (needs Hamlib CAT)'
        from radios import RADIOS
        preset = RADIOS.get(self.cfg.get('radio', ''), {}).get('tuner')
        if preset and hasattr(rig, 'raw'):                 # radio-specific raw CAT (e.g. FTX-1 AC103;)
            try:
                if 'RPRT -' not in rig.raw(preset):
                    return True, 'Tuning...'
            except Exception:
                pass
        try:
            if rig.cmd('G TUNE')[1] == 0:
                return True, 'Tuning...'
        except Exception:
            pass
        return False, 'no CAT tuner'

    def tune_carrier(self, seconds=5):
        """Key a steady carrier so an automatic tuner (LDG etc.) can tune. -> (ok, message)"""
        if self.tx.busy.is_set():
            return False, 'busy transmitting'
        ok, why = self.check_tx()
        if not ok:
            return False, why
        if self.cfg['key_method'] == 'rigkeyer':
            return False, "radio keyer can't send a carrier - use the radio's TUNE"
        self.tx.tune(seconds)
        return True, f'Carrier {seconds} s'

    def set_rig_mode(self):
        if self.cfg['key_method'] == 'audio':
            return 'DATA-U (audio keying)' if self.rig.set_data_mode() else None
        return 'CW' if self.rig.set_cw_mode() else None

    # ================= TX =================
    def _soft_feed(self, job):
        q = getattr(self, '_soft_q', None)
        if q is None:
            self._soft_q = q = queue.Queue(maxsize=400)          # ~8 s of 1024-sample blocks
            threading.Thread(target=self._soft_loop, daemon=True, name='softdec').start()
        try:
            q.put_nowait(job)
        except queue.Full:                                       # fell far behind: drop, don't block
            self._soft_drops = getattr(self, '_soft_drops', 0) + 1

    def _soft_loop(self):
        while True:
            x, tone, hint = self._soft_q.get()
            soft, comb, deep = self.soft, self.comb, getattr(self, 'deep', None)
            if deep is None and (soft is None or comb is None):
                continue
            try:
                if deep is not None:
                    txt = deep.process(x, tone=tone)
                    if soft is not None:
                        soft.process(x, tone=tone, hint_dit_ms=hint)     # speed meter only
                else:
                    txt = comb.soft(soft.process(x, tone=tone, hint_dit_ms=hint))
            except Exception:
                import sys as _sys
                _sys.excepthook(*_sys.exc_info())     # -> zbm2cw_debug.log; keep the thread alive
                continue
            if txt:
                self._on_rx(txt)

    def _update_their_wpm(self, d):
        """A steady 'their speed': only from a decoder that's actually copying text, smoothed,
        and held (not reset) through pauses - so it doesn't jump around on noise."""
        w = None
        if self.soft is not None and self.soft._open:
            w = 1200.0 / self.soft.dit_ms
        elif d.sn is not None and d.sn >= 3 and d.locked:
            w = d.wpm
        if w is None:
            return
        if self._their_wpm is None or abs(w - self._their_wpm) > 0.35 * self._their_wpm:
            self._wpm_jump = getattr(self, '_wpm_jump', 0) + 1
            if self._their_wpm is None or self._wpm_jump >= 25:    # a real speed change persists
                self._their_wpm, self._wpm_jump = w, 0
            return
        self._wpm_jump = 0
        self._their_wpm += 0.05 * (w - self._their_wpm)

    def tx_wpm(self):
        c = self.cfg
        if c.get('turbo'):
            return int(min(max(c.get('turbo_wpm', 35), 5), 60))
        if c.get('match_wpm', True) and getattr(self, '_their_wpm', None):
            w = round(self._their_wpm) + int(c.get('wpm_offset', 0))
        else:
            w = int(c.get('manual_wpm', 18))
        return int(min(max(w, c['min_wpm']), c['max_wpm']))

    def check_tx(self):
        """License guard. -> (ok, why)"""
        if self.cfg.get('emergency'):          # 97.403 / 97.405: any means, for immediate safety of life
            return True, ''
        if not self.sim and not str(self.cfg.get('call', '')).strip():
            why = 'Set your callsign first (Setup Wizard) - nothing goes on the air without it.'
            self.emit('blocked', why)
            return False, why
        if not self.cfg.get('license_guard', True):        # operator turned the band guard off
            return True, ''
        ok, why = Q.cw_allowed(self.freq if self.rig_ok else 0, self.cfg.get('license_class', 'Technician'))
        if not ok:
            self.earcons.play('error')
            self.emit('blocked', why)
        return ok, why

    def send(self, text, owner='qso'):
        text = ' '.join(text.upper().split())
        if not text or not self.check_tx()[0]:
            return False
        self.ender.note_sent(text)
        self.tx_owner = owner
        self.tx.wpm = self.tx_wpm()
        self.tx.tone = self.cfg['tone']
        if self.cfg.get('sidetone') and self.cfg['key_method'] != 'audio' and not self.sim:
            try:
                play_sidetone(text, self.tx.wpm, find_dev(self.cfg['sidetone_out'], 'output'))
            except Exception:
                pass
        self.qso.note_sent(text)
        self.tx.send(text)
        return True

    def stream_word(self, w, owner='chat'):
        self.tx_owner = owner
        self.tx.wpm = self.tx_wpm()
        self.tx.stream_word(w)

    def stop_tx(self):
        self.tx.stop()

    def _tx_done(self):
        self.tx_until = time.time() + 0.4
        self.earcons.play('sent', min_gap=1.0)
        self.emit('txdone', None)

    # ================= logging / online =================
    def log_external(self, call, grid='', rst_sent='', rst_rcvd='', name='', qth='', extra=None, time_on=None):
        import datetime as _dt
        q = Q.QSO(self.cfg['call'])
        q.call, q.grid, q.rst_rcvd, q.name, q.qth = call, grid, rst_rcvd, name, qth
        q.time_on = time_on or _dt.datetime.now(_dt.timezone.utc)
        rec, on, off = Q.adif_record(q, self.cfg, self.freq, rst_sent)
        if extra:
            rec = rec.replace('<EOR>', ''.join(f'<{k}:{len(v)}>{v} ' for k, v in extra.items()) + '<EOR>')
        Q.append_log(self.cfg['log_path'], rec)
        self.gt.qso_logged({'call': call, 'grid': grid, 'freq': self.freq, 'rst_sent': rst_sent,
                            'rst_rcvd': rst_rcvd, 'name': name, 'my_call': self.cfg['call'],
                            'my_grid': self.cfg['grid'], 'time_on': on, 'time_off': off})
        self.earcons.play('logged')
        if self.cfg.get('rec_autosave'):
            self.save_audio(call)
        self._upload_async(rec)
        self.emit('logged', call)
        return rec

    def _upload_async(self, rec):
        c = self.cfg

        def job():
            if c.get('auto_qrz_upload') and c.get('qrz_logbook_key'):
                try:
                    ON.qrz_logbook_upload(c['qrz_logbook_key'], rec)
                    self.status('QRZ Logbook: uploaded')
                except Exception as e:
                    self.status(f'QRZ Logbook upload failed: {e}', err=True)
            if c.get('auto_lotw_upload') and c.get('tqsl_location'):
                ok, msg = ON.lotw_upload(ON.find_tqsl(c.get('tqsl_path', '')), c['tqsl_location'], [rec])
                self.status(f'LoTW: {msg}', err=not ok)
        threading.Thread(target=job, daemon=True).start()

    def save_audio(self, label=''):
        import re
        try:
            path, secs = self.rec.save(os.path.join(self.data_dir, 'recordings'), re.sub(r'\W', '_', label or ''))
            self.status(f'Saved last {secs:.0f} s of audio: {path}')
            return path
        except Exception as e:
            self.status(f'Audio save failed: {e}', err=True)

    def request_lookup(self, call):
        if not call or not self.cfg.get('auto_lookup'):
            return
        if call in self.lookups_done:
            if self.lookups_done[call]:
                self.emit('lookup', (call, self.lookups_done[call]))
            return
        if not self.lookup or not self.lookup.enabled():
            return
        self.lookups_done[call] = {}

        def job():
            try:
                d = self.lookup.lookup(call)
                self.lookups_done[call] = d
                b = self.room.buddies.get(call)
                if b:
                    b.lookup = d
                self.emit('lookup', (call, d))
            except Exception as e:
                self.status(f'Lookup {call}: {e}', err=True)
        threading.Thread(target=job, daemon=True).start()

    def _load_lotw_conf(self):
        import json
        try:
            with open(os.path.join(self.data_dir, 'lotw_confirmed.json')) as f:
                return {tuple(x) for x in json.load(f)}
        except (OSError, ValueError):
            return set()

    def save_lotw_conf(self, conf):
        import json
        self.lotw_conf = set(conf)
        with open(os.path.join(self.data_dir, 'lotw_confirmed.json'), 'w') as f:
            json.dump(sorted(self.lotw_conf), f)

    def confirmed(self, call):
        return any(c == call.upper() for c, _ in self.lotw_conf)

    # ================= simulator =================
    def set_sim(self, on, **opts):
        from simulator import Simulator
        if on and not self.sim:
            self.sim = Simulator(self.cfg, **opts)
        elif not on:
            if self.sim:
                self.sim.stop()
            self.sim = None
        self.apply_config()
        self.start_audio()


# ================= headless (no screen) =================
def headless(argv):
    """python engine.py --headless   : decode to the console (field box / Pi / Jetson, no display).
       Type a line + Enter to send it (endings and ID added automatically). Ctrl+C quits."""
    import sys
    from config import load_cfg
    cfg = load_cfg()
    if '--sim' in argv:
        cfg['skimmer'] = True

    def emit(kind, val):
        if kind == 'rx':
            sys.stdout.write(val)
            sys.stdout.flush()
        elif kind == 'txchar':
            sys.stdout.write(val.lower())
            sys.stdout.flush()
        elif kind == 'segment':
            print('\n   = ' + PL.translate(val, cfg['call']))
        elif kind in ('status', 'blocked'):
            print(f"\n[{val[0] if kind == 'status' else 'NOT SENT: ' + val}]")
        elif kind == 'alerts':
            for a in val[0]:
                print(f'\n*** {a.code} *** {a.plain}')
        elif kind == 'buddy':
            print(f'\n[+ {val} on frequency]')
    eng = Engine(cfg, emit)
    if '--sim' in argv:
        eng.set_sim(True)
    eng.start()
    print(f"ZBM2 CW Tool engine - {cfg['call']} - headless. Type to send, Ctrl+C to quit.")
    try:
        for line in sys.stdin:
            full, _ = eng.ender.finish(line, eng.qso.call or eng.room.partner)
            if full:
                eng.send(full)
    except KeyboardInterrupt:
        pass
    eng.shutdown()


if __name__ == '__main__':
    import sys
    if '--headless' in sys.argv:
        headless(sys.argv)
    else:
        print('Run app.py for the screens, or: python engine.py --headless [--sim]')
