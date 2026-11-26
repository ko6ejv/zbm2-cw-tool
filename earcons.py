"""Morse earcons: every UI sound is a real CW character, so users learn by ear.

Played at real character speed with Farnsworth spacing, on the PC speakers
(sidetone device) at a different pitch from the radio audio.
"""
import threading
import time

EARCONS = {
    'your_turn': 'K',
    'sent': 'R',
    'logged': 'TU',
    'error': '?',
    'signed_off': '<SK>',
    'new_station': None,      # first letter of their call, passed as text
    'startup': 'ZBM2',
    'qrz': 'QRZ?',
}


class Earcons:
    def __init__(self, device=None, pitch=800, wpm=22, farnsworth=12, volume=0.25,
                 enabled=True, speak=False, on_flash=None):
        self.device, self.pitch, self.wpm, self.farns = device, pitch, wpm, farnsworth
        self.volume, self.enabled, self.speak, self.on_flash = volume, enabled, speak, on_flash
        self._last = {}
        self._lock = threading.Lock()

    def play(self, event, text=None, min_gap=3.0):
        """event: key of EARCONS or 'alert' with text=Q-code."""
        if not self.enabled or (getattr(self, 'startup_only', False) and event != 'startup'):
            return
        txt = text or EARCONS.get(event)
        if not txt:
            return
        now = time.time()
        if now - self._last.get((event, txt), 0) < min_gap:     # don't nag
            return
        self._last[(event, txt)] = now
        if self.on_flash:
            self.on_flash(txt)
        threading.Thread(target=self._play, args=(txt,), daemon=True).start()

    def _play(self, txt):
        with self._lock:
            try:
                import sounddevice as sd
                from rig import tone_waveform
                wave = tone_waveform(txt, self.wpm, 48000, self.pitch, rise_ms=4.0,
                                     amp=self.volume, farnsworth=self.farns)
                sd.play(wave, 48000, device=self.device)
                sd.wait()
                if self.speak:
                    self._say(txt)
            except Exception:
                pass

    @staticmethod
    def _say(txt):
        try:                                   # Windows SAPI voice, optional
            import win32com.client
            win32com.client.Dispatch('SAPI.SpVoice').Speak(' '.join(txt.strip('<>')))
        except Exception:
            pass
