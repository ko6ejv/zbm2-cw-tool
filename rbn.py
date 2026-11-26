"""Live CW activity from the Reverse Beacon Network (telnet.reversebeacon.net:7000).

Skimmers around the world post every CW station they hear ("DX de K5TR-1-#: 10115.2 N5EEI CW 10 dB
22 WPM CQ 2001Z"). We keep the last 10 minutes and count the stations per band that skimmers near
you heard - real activity, not a forecast. Login is just your callsign (no password).
"""
import re
import socket
import threading
import time

import qso as Q

HOST, PORT = 'telnet.reversebeacon.net', 7000
WINDOW_S = 600
LINE = re.compile(r'^DX de (\S+?)(?:-\d+)?-#:\s+([\d.]+)\s+(\S+)\s+(\w+)\s+(-?\d+) dB\s+(\d+) WPM\s+(\w+)')
# North America (where most ZBM2 users are): US, Canada, Mexico call areas
_NA = re.compile(r'^(A[A-L]|[KNW][A-Z]?|V[A-GOY]|C[F-KY-Z]|X[A-I]|4[ABC]|6[D-J])\d')


def region_of(call):
    return 'NA' if _NA.match(call.upper()) else 'DX'


class RBN:
    def __init__(self, call, on_update=None):
        self.call = call.upper().strip()
        self.region = region_of(self.call) if self.call else 'NA'
        self.on_update = on_update
        self.spots = {}                       # (dx call, band) -> dict
        self.lock = threading.Lock()
        self.connected = False
        self._stop = False
        self._last_push = 0.0

    def start(self):
        if self.call:
            threading.Thread(target=self._run, daemon=True, name='rbn').start()
        return self

    def stop(self):
        self._stop = True

    # ---- network ----
    def _run(self):
        wait = 5
        while not self._stop:
            try:
                self._session()
                wait = 5
            except Exception:
                self.connected = False
                time.sleep(wait)
                wait = min(wait * 2, 300)

    def _session(self):
        s = socket.create_connection((HOST, PORT), timeout=30)
        s.settimeout(90)
        buf, logged = b'', False
        try:
            while not self._stop:
                d = s.recv(4096)
                if not d:
                    raise ConnectionError('closed')
                buf += d
                if not logged and b'call' in buf.lower():
                    s.sendall(self.call.encode() + b'\r\n')
                    logged, self.connected = True, True
                while b'\n' in buf:
                    raw, buf = buf.split(b'\n', 1)
                    self.feed(raw.decode('ascii', 'replace').strip())
                if len(buf) > 8192:
                    buf = b''
        finally:
            s.close()
            self.connected = False

    # ---- spots ----
    def feed(self, line, now=None):
        m = LINE.match(line)
        if not m:
            return False
        skimmer, khz, dx, mode, snr, wpm, kind = m.groups()
        if mode != 'CW':
            return False
        hz = int(float(khz) * 1000)
        band = Q.band_for(hz)
        if not band:
            return False
        now = now or time.time()
        near = region_of(skimmer) == self.region
        with self.lock:
            k = (dx, band)
            old = self.spots.get(k)
            if old is None or near or not old['near'] or now - old['t'] > 120:
                self.spots[k] = {'call': dx, 'band': band, 'hz': hz, 'snr': int(snr), 'wpm': int(wpm),
                                 'kind': kind, 'near': near or bool(old and old['near'] and now - old['t'] < 120),
                                 'skimmer': skimmer, 't': now}
        if self.on_update and now - self._last_push > 20:
            self._last_push = now
            self.on_update(self)
        return True

    def _fresh(self, now=None):
        now = now or time.time()
        with self.lock:
            for k in [k for k, v in self.spots.items() if now - v['t'] > WINDOW_S]:
                del self.spots[k]
            return list(self.spots.values())

    def counts(self, near_only=True, now=None):
        """{band: stations heard in the last 10 min (by skimmers in your region)}"""
        out = {}
        for s in self._fresh(now):
            if s['near'] or not near_only:
                out[s['band']] = out.get(s['band'], 0) + 1
        return out

    def on_band(self, band, now=None):
        """Spots on one band, newest/strongest first; near skimmers first."""
        now = now or time.time()
        sp = [s for s in self._fresh(now) if s['band'] == band]
        return sorted(sp, key=lambda s: (not s['near'], -s['snr']))
