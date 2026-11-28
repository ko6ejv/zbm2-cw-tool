"""Screen smoke tests: start the real app (no radio, no sound card), open every window, push every
kind of engine event through the screen, click the new buttons. Catches the wiring bugs unit tests
can't (most bugs found live in Nov 2026 were here). Needs a display: CI runs it under xvfb.

    xvfb-run -a python -m pytest -q tests/test_ui.py
"""
import json
import os
import sys
import threading
import time
import types

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

tk = pytest.importorskip('tkinter')

# no sound card in CI: a stand-in sounddevice (audio input fails cleanly, the app shows the error)
_sd = types.ModuleType('sounddevice')
_sd.default = types.SimpleNamespace(device=(None, None))
_sd.query_devices = lambda *a, **k: []
_sd.query_hostapis = lambda *a, **k: []
_sd.play = lambda *a, **k: None
_sd.stop = lambda *a, **k: None
_sd.wait = lambda *a, **k: None


class _NoStream:
    def __init__(self, *a, **k):
        raise RuntimeError('no audio device (test)')


_sd.InputStream = _sd.OutputStream = _NoStream
sys.modules['sounddevice'] = _sd


def _display_ok():
    try:
        r = tk.Tk()
        r.destroy()
        return True
    except tk.TclError:
        return False


pytestmark = pytest.mark.skipif(not _display_ok(), reason='no display (run under xvfb-run)')


@pytest.fixture(params=['auto', 'compact'])
def app(request, tmp_path, monkeypatch):
    import config
    cfgp = tmp_path / 'config.json'
    json.dump({'setup_done': True, 'call': 'KO6EJV', 'license_class': 'General', 'cat_backend': 'none',
               'update_check': False, 'rbn_enable': False, 'layout': request.param, 'gt_enable': False,
               'data_dir': str(tmp_path / 'data'), 'log_path': str(tmp_path / 'log.adi')}, open(cfgp, 'w'))
    monkeypatch.setattr(config, 'CFG_PATH', str(cfgp))
    import app as A
    errors = []
    root = tk.Tk()
    root.report_callback_exception = lambda et, ev, tb: errors.append(ev)
    old_hook = sys.excepthook
    sys.excepthook = lambda et, ev, tb: errors.append(ev)
    monkeypatch.setattr(A.messagebox, 'askyesno', lambda *a, **k: False)
    monkeypatch.setattr(A.messagebox, 'showinfo', lambda *a, **k: None)
    monkeypatch.setattr(A.messagebox, 'showerror', lambda *a, **k: None)
    a = A.App(root)
    a._errors = errors
    pump(root, 0.5)
    yield a
    sys.excepthook = old_hook
    try:
        a.eng.shutdown()
        root.destroy()
    except Exception:
        pass


def pump(root, secs):
    t = time.time() + secs
    while time.time() < t:
        root.update()
        time.sleep(0.01)


def test_starts_and_events_flow(app):
    a, root = app, app.root
    q = a.ui_q
    fr = np.linspace(100, 2000, 600)
    db = -90 + 5 * np.random.rand(600)
    a.eng.rig_ok, a.eng.freq, a.eng.mode = True, 7030000, 'CW'
    for kind, val in [
        ('rig', None), ('spec', (fr, db, -40.0, -30.0)), ('meter', (5.0, 22.0, True, 700, False)),
        ('rx', 'CQ CQ DE W1AW W1AW K '), ('segment', 'CQ CQ DE W1AW W1AW K'),
        ('rxtext', ('CQ DE W1AW', 22.0)), ('elem', ('mark', 60.0, 60.0)), ('elem', ('space', 200.0, 60.0)),
        ('bandcond', {'bands': {'80m-40m': ('Fair', 'Good'), '30m-20m': ('Good', 'Fair'),
                                '17m-15m': ('Poor', 'Poor'), '12m-10m': ('Poor', 'Poor')},
                      'sfi': '87', 'k': '1', 'noise': 'S1', 'updated': 'now'}),
        ('txstart', 'W1AW DE KO6EJV'), ('txchar', 'W'), ('txchar', '1'), ('txchar', ' '), ('txdone', None),
        ('blocked', '7.0194 MHz is outside General CW privileges.'), ('status', ('hello', False)),
        ('flash', 'ZBM2'), ('buddy', 'W1AW'),
    ]:
        q.put((kind, val))
    pump(root, 1.5)
    assert not a._errors, a._errors
    assert '7.030' in a.lbl_freq.cget('text')
    assert a.v_band.get() == '40M'


def test_bad_event_does_not_stop_the_screen(app):
    a, root = app, app.root
    a.ui_q.put(('meter', 'garbage'))                  # would raise inside the handler
    a.ui_q.put(('rig', None))
    a.eng.rig_ok, a.eng.freq = True, 14040000
    pump(root, 0.6)
    a.ui_q.put(('rig', None))
    pump(root, 0.6)
    assert '14.040' in a.lbl_freq.cget('text')        # still updating after the bad one


def test_windows_open(app):
    import app as A
    a, root = app, app.root
    A.HelpWindow(root, a)
    A.SettingsDialog(a)
    A.SetupWizard(a)
    pump(root, 0.8)
    for w in root.winfo_children():
        if isinstance(w, tk.Toplevel):
            w.destroy()
    pump(root, 0.2)
    assert not a._errors, a._errors


def test_band_tune_update_buttons(app):
    a, root = app, app.root
    calls = []
    a.eng.qsy_band = lambda b: calls.append(b) or (14026000, True)
    a._qsy_band('20M')
    a.eng.tune_antenna = lambda: (False, 'no CAT tuner')
    a._tune()
    a._update_found({'version': '99.0.0', 'page': 'x', 'setup_url': None, 'setup_name': 'x.exe', 'size': 1}, True)
    pump(root, 1.0)
    assert calls == ['20M']
    assert a.btn_update.winfo_ismapped()
    c1 = a.btn_update.cget('bg')
    pump(root, 0.7)
    assert a.btn_update.cget('bg') != c1                       # it blinks
    assert not a._errors, a._errors


def test_tx_blocked_without_cat_reading(app):
    a = app
    a.cfg['cat_backend'] = 'hamlib_launch'
    a.eng.rig_ok = False
    assert not a.eng.check_tx()[0]
    a.cfg['cat_backend'] = 'none'
    assert a.eng.check_tx()[0]


def test_threads_survive(app):
    pump(app.root, 0.5)
    names = {t.name for t in threading.enumerate()}
    assert threading.main_thread().is_alive() and len(names) > 1


def test_spots_window_and_band_counts(app):
    import app as A
    import rbn
    a, root = app, app.root
    r = rbn.RBN('KO6EJV')
    r.feed('DX de W3OA-#:    7007.90  HA8RC          CW     4 dB  22 WPM  CQ      2001Z')
    a.eng.rbn = r
    a.ui_q.put(('rbn', r.counts()))
    tuned = []
    a.eng.qsy_freq = lambda hz: tuned.append(hz) or hz
    w = A.SpotsWindow(a, band='40M')
    pump(root, 0.8)
    rows = w.tv.get_children()
    assert len(rows) == 1
    w.tv.selection_set(rows[0])
    w.tune()
    pump(root, 0.5)
    assert tuned == [7007900]
    assert any('40M  1' == v for v in a.cb_band.cget('values'))
    w.w.destroy()
    assert not a._errors, a._errors


def test_whats_new_after_update(app):
    import app as A
    a, root = app, app.root
    pump(root, 1.6)                                  # startup check runs at 1.5 s
    tops = [w for w in root.winfo_children() if isinstance(w, tk.Toplevel)]
    assert any("What's new" in w.title() for w in tops)
    assert a.cfg['seen_version'] == A.__version__
    for w in tops:
        w.destroy()
    a._whats_new_check()                             # second start: nothing
    pump(root, 0.2)
    assert not [w for w in root.winfo_children() if isinstance(w, tk.Toplevel)]
    A.WhatsNewWindow(root)                           # HELP button path
    pump(root, 0.2)
    assert not a._errors, a._errors
