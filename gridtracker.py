"""Talk to GridTracker (or any WSJT-X UDP listener: JTAlert, Log4OM, N1MM)
using the WSJT-X UDP protocol. GridTracker listens on UDP 2237 by default.
"""
import socket
import struct
import datetime as dt

MAGIC = 0xADBCCBDA
SCHEMA = 2
APP_ID = 'ZBM2-CW'


def _u32(v): return struct.pack('>I', v & 0xFFFFFFFF)
def _i32(v): return struct.pack('>i', v)
def _u64(v): return struct.pack('>Q', v)
def _i64(v): return struct.pack('>q', v)
def _f64(v): return struct.pack('>d', v)
def _b(v): return struct.pack('>?', bool(v))
def _u8(v): return struct.pack('>B', v)


def _s(v):
    if v is None:
        return _u32(0xFFFFFFFF)
    b = str(v).encode('utf-8')
    return _u32(len(b)) + b


def _qtime(t):
    return _u32(((t.hour * 60 + t.minute) * 60 + t.second) * 1000 + t.microsecond // 1000)


def _qdatetime(d):
    jd = d.date().toordinal() + 1721425      # proleptic Gregorian -> Julian day
    return _i64(jd) + _qtime(d.time()) + _u8(1)   # 1 = UTC


class GridTrackerLink:
    def __init__(self, host='127.0.0.1', port=2237, enabled=True):
        self.addr = (host, int(port))
        self.enabled = enabled
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def _send(self, mtype, body):
        if not self.enabled:
            return
        pkt = _u32(MAGIC) + _u32(SCHEMA) + _u32(mtype) + _s(APP_ID) + body
        try:
            self.sock.sendto(pkt, self.addr)
        except OSError:
            pass

    def heartbeat(self):
        self._send(0, _u32(3) + _s('ZBM2 CW Tool 3.0') + _s(''))

    def status(self, freq_hz, de_call, de_grid, dx_call='', dx_grid='',
               transmitting=False, tone=700, tx_msg=''):
        body = (_u64(int(freq_hz or 0)) + _s('CW') + _s(dx_call) + _s('') +
                _s('CW') + _b(transmitting) + _b(transmitting) + _b(True) +
                _u32(int(tone)) + _u32(int(tone)) + _s(de_call) + _s(de_grid) +
                _s(dx_grid) + _b(False) + _s('') + _b(False) + _u8(0) +
                _u32(0xFFFFFFFF) + _u32(0xFFFFFFFF) + _s('ZBM2-CW') + _s(tx_msg))
        self._send(1, body)

    def decode(self, message, snr=0, tone=700):
        """message in WSJT-X style, e.g. 'CQ W1AW FN31' or 'KO6EJV W1AW 599'."""
        now = dt.datetime.now(dt.timezone.utc)
        body = (_b(True) + _qtime(now.time()) + _i32(int(snr)) + _f64(0.0) +
                _u32(int(tone)) + _s('CW') + _s(message) + _b(False) + _b(False))
        self._send(2, body)

    def qso_logged(self, q):
        """q: dict with call, grid, freq, rst_sent, rst_rcvd, name, my_call,
        my_grid, time_on (datetime UTC), time_off, power, comment."""
        body = (_qdatetime(q['time_off']) + _s(q['call']) + _s(q.get('grid', '')) +
                _u64(int(q.get('freq', 0))) + _s('CW') + _s(q.get('rst_sent', '')) +
                _s(q.get('rst_rcvd', '')) + _s(q.get('power', '')) +
                _s(q.get('comment', '')) + _s(q.get('name', '')) +
                _qdatetime(q['time_on']) + _s(q.get('my_call', '')) +
                _s(q.get('my_call', '')) + _s(q.get('my_grid', '')) +
                _s('') + _s('') + _s(''))
        self._send(5, body)

    def logged_adif(self, adif_record):
        self._send(12, _s('<adif_ver:5>3.1.4\n<programid:7>ZBM2-CW\n<EOH>\n' + adif_record))
