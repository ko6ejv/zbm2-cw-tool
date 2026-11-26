"""Unit tests for the engine (no audio hardware, no radio, no GUI).  Run:  python -m pytest tests"""
import difflib
import os
import sys

import numpy as np
import pytest

sys.path[:0] = [os.path.dirname(os.path.dirname(os.path.abspath(__file__))), os.path.dirname(os.path.abspath(__file__))]

from synth import keying, audio                     # noqa: E402
from decoder import CWDecoder                        # noqa: E402
import qso as Q                                      # noqa: E402
import plain as PL                                   # noqa: E402
from endings import Ender                            # noqa: E402
from contest import ContestSession, parse_exchange   # noqa: E402
from practice import FistGrader                      # noqa: E402
from recorder import Recorder                        # noqa: E402
from qcodes import define                            # noqa: E402
from chat import ChatRoom                            # noqa: E402
import online as ON                                  # noqa: E402

TEXT = "CQ CQ DE W1AW W1AW K TNX FER CALL UR RST 599 NAME BOB QTH NEWINGTON CT HW? <KN>"


def decode(seq, snr=15, tone=700):
    x = audio(seq, tone=tone, snr_db=snr)
    d = CWDecoder(tone=650)
    out = []
    for i in range(0, len(x), 1024):
        out += d.process(x[i:i + 1024])
    out += d.flush()
    return ' '.join(''.join(out).split()), d


@pytest.mark.parametrize('wpm', [8, 13, 18, 25, 32, 40])
def test_decoder_accuracy_and_speed(wpm):
    s, d = decode(keying(TEXT, wpm, jitter=0.05), snr=12)
    assert difflib.SequenceMatcher(None, s, TEXT).ratio() > 0.97
    assert abs(d.wpm - wpm) / wpm < 0.08


def test_decoder_speed_change():
    s, d = decode(keying(TEXT, 15) + keying(TEXT, 30))
    assert abs(d.wpm - 30) < 2
    assert s.count('NEWINGTON') == 2


def test_decoder_farnsworth():
    s, _ = decode(keying(TEXT, 25, farns=12))
    assert 'NEWINGTON' in s and 'W1AW' in s


def test_qso_flow():
    cfg = dict(call='KO6EJV', name='DERIC', qth='LA MESA CA', grid='DM12LS')
    q = Q.QSO('KO6EJV')
    q.feed('CQ CQ CQ DE W1AW W1AW K ')
    assert q.state == 'cq_heard' and q.call == 'W1AW'
    q.note_sent(q.suggest(cfg, '579'))
    q.feed('KO6EJV DE W1AW GE UR RST 5NN NAME BOB QTH NEWINGTON CT GRID FN31 HW? KN ')
    assert (q.rst_rcvd, q.name, q.qth, q.grid) == ('599', 'BOB', 'NEWINGTON CT', 'FN31')
    assert 'UR RST 579' in q.suggest(cfg, '579')


def test_license_guard():
    assert Q.cw_allowed(7040000, 'Technician')[0]
    assert not Q.cw_allowed(14050000, 'Technician')[0]
    assert Q.cw_allowed(14050000, 'General')[0]
    assert Q.cw_allowed(146520000, 'Technician')[0]


def test_is_call():
    for w in ('W1AW', 'KO6EJV', '2E0ABC', 'VE3/W1AW', 'W1AW/P'):
        assert PL.is_call(w), w
    for w in ('100W', '72F', '5NN', 'FN31', 'IC7300'):
        assert not PL.is_call(w), w


def test_plain_english():
    t = PL.translate('KO6EJV DE W1AW TNX FER CALL UR RST 579 579 HW? <KN>', 'KO6EJV')
    assert 'thanks for the call' in t and 'moderately strong' in t and 'over to you only' in t
    assert t.count('579') == 1


def test_endings_and_id():
    e = Ender('KO6EJV')
    assert e.finish('CQ')[0] == 'CQ CQ CQ DE KO6EJV KO6EJV K'
    assert e.finish('CQ POTA')[1] == 'K'
    full, code = e.finish('GE BOB UR RST 579 NAME DERIC', 'W1AW')
    assert code == 'KN' and full.endswith('W1AW DE KO6EJV <KN>')
    e.note_sent(full)
    assert e.finish('AGN?', 'W1AW') == ('AGN? BK', 'BK')
    assert e.finish('TNX FER QSO 73', 'W1AW')[1] == 'SK'
    assert e.finish('KEEP GOING', 'W1AW', keep_over=True) == ('KEEP GOING', 'NONE')


def test_qcodes():
    assert define('QRM?')[0] == 'Q-CODE'
    assert define('AGN')[0] == 'ABBR'
    assert define('ZZZ') is None


def test_contest_sst_run(tmp_path):
    s = ContestSession('SST', 'KO6EJV', {'name': 'DERIC', 'state': 'CA'}, str(tmp_path))
    assert s.next_action()[0] == 'CQ'
    s.feed('W1AW W1AW ')
    assert s.next_action() == ('SEND EXCHANGE', 'W1AW DERIC CA', False)
    s.sent('SEND EXCHANGE')
    s.feed('TU DERIC BOB CT ')
    assert s.rcvd == {'name': 'BOB', 'spc': 'CT'}
    assert s.next_action() == ('TU + LOG', 'TU BOB KO6EJV', True)
    s.log(7040000)
    s.freq = 7040000
    s.feed('W1AW ')
    assert s.is_dupe(7040000)
    assert 'QSO:  7040 CW' in s.cabrillo()


def test_contest_parsers():
    assert parse_exchange('FD', 'KO6EJV 3A ORG', 'KO6EJV') == {'cls': '3A', 'sec': 'ORG'}
    assert parse_exchange('POTA', 'KO6EJV TU 5NN OR K-1234', 'KO6EJV') == {'rst': '599', 'spc': 'OR',
                                                                           'park': 'K-1234'}


def test_fist_grader_separates_good_and_bad():
    def grade(j, dah_scale=1.0):
        g = FistGrader()
        d = CWDecoder(on_element=g.element, ride_dips=False)   # fist grading: true mark lengths
        seq = [(on, ms * (dah_scale if on and ms > 100 else 1)) for on, ms in keying('PARIS PARIS PARIS', 15, jitter=j)]
        x = audio(seq, snr_db=25)
        for i in range(0, len(x), 1024):
            d.process(x[i:i + 1024])
        return g.grade()
    good, _, _ = grade(0.03)
    sloppy, _, _ = grade(0.25)
    short, tips, _ = grade(0.03, 0.73)
    assert good > sloppy and good >= 85
    assert any('short' in t for t in tips)


def test_chat_room_facts(tmp_path):
    r = ChatRoom('KO6EJV', str(tmp_path))
    for c in 'KO6EJV DE W1AW NAME HR BOB RIG HR IC7300 PWR 100W HW? KO6EJV DE W1AW KN ':
        r.feed(c)
    assert r.buddies['W1AW'].facts['rig'] == 'IC7300'
    assert '100W' not in r.buddies


def test_recorder(tmp_path):
    r = Recorder(2, 8000)
    r.feed(np.ones(20000, dtype=np.float32) * .1)
    path, secs = r.save(str(tmp_path), 'T')
    assert secs == 2.0 and os.path.exists(path)


def test_online_parsers():
    assert ON.parse_lotw_adif('<EOH><CALL:4>W1AW <BAND:3>40M <eor>') == {('W1AW', '40M')}
    assert ON.tqsl_command('tqsl', 'x.adi', 'Home')[-1] == '-x'


def test_pileup_three_stations():
    from skimmer import Skimmer

    def sig(text, wpm, tone, snr, delay, seed):
        rng = np.random.default_rng(seed)
        x = audio(keying(text, wpm, jitter=0.04, rng=rng), tone=tone, snr_db=200, rng=rng)
        return np.concatenate([np.zeros(int(48000 * delay)), x]) * 10 ** ((snr - 20) / 20)
    st = [('W1AW W1AW W1AW TEST', 22, 600, 14, 0.0), ('K6XYZ K6XYZ K6XYZ TEST', 28, 700, 10, 0.4),
          ('N7ABC N7ABC N7ABC TEST', 18, 830, 8, 0.9)]
    parts = [sig(t, w, f, s, d, i) for i, (t, w, f, s, d) in enumerate(st)]
    mix = np.zeros(max(len(p) for p in parts) + 48000)
    for p in parts:
        mix[:len(p)] += p
    mix += np.random.default_rng(9).normal(0, 1, len(mix)) * 0.0707 * np.sqrt(48)
    sk = Skimmer()
    mix = mix.astype(np.float32)
    for i in range(0, len(mix), 1024):
        sk.process(mix[i:i + 1024])
    got = {r[1]: r for r in sk.summary()}
    for call, wpm in (('W1AW', 22), ('K6XYZ', 28), ('N7ABC', 18)):
        assert call in got, got.keys()
        assert abs(got[call][2] - wpm) <= 3


def test_weak_signal_6db():
    s, _ = decode(keying(TEXT, 20, jitter=0.05), snr=6)
    assert difflib.SequenceMatcher(None, s, TEXT).ratio() > 0.97


def test_simulator_operators():
    import random
    from simulator import Simulator
    random.seed(1)
    cfg = dict(call='KO6EJV', name='DERIC', tone=700)
    s = Simulator(cfg, 'answer')
    p = s.partner
    s._respond(f'{p.call} DE KO6EJV KO6EJV K')
    assert p.stage == 'report' and 'UR RST' in p.last_said
    said = p.last_said
    s._respond('AGN?')
    assert p.last_said == said
    s._respond(f'R UR RST 579 NAME DERIC HW? {p.call} DE KO6EJV KN')
    assert p.stage == 'chat'
    s._respond(f'73 {p.call} DE KO6EJV <SK>')
    assert p.stage == 'done' and '<SK>' in p.last_said
    s = Simulator(cfg, 'pileup', callers=3)
    s._respond('CQ CQ DE KO6EJV K')
    assert len(s.stations) == 3 and all(x.stage == 'calling' for x in s.stations)
    pick = s.stations[1].call
    s._respond(f'{pick} 5NN')
    assert s.partner.call == pick and len(s.stations) == 1
    s = Simulator(cfg, 'sst')
    s._respond('CQ SST KO6EJV')
    c = s.stations[0].call
    s._respond(f'{c} DERIC CA')
    assert s.stations[0].last_said.startswith('TU DERIC')


def _guard(**kw):
    import types
    import engine
    cfg = dict(call='KO6EJV', license_guard=True, license_class='General', cat_backend='hamlib_launch',
               key_method='serial', tone=700)
    cfg.update(kw.pop('cfg', {}))
    e = types.SimpleNamespace(cfg=cfg, sim=None, rig_ok=True, freq=7030000, mode='CW', emit=lambda *a: None,
                              earcons=types.SimpleNamespace(play=lambda *a, **k: None))
    for k, v in kw.items():
        setattr(e, k, v)
    return engine.Engine.check_tx(e)[0]


def test_tx_guard_edges():
    assert _guard()
    assert not _guard(rig_ok=False)                                    # CAT down: can't check -> no TX
    assert _guard(rig_ok=False, cfg={'cat_backend': 'none'})           # no CAT at all: operator's call
    assert _guard(rig_ok=False, cfg={'license_guard': False})
    assert not _guard(cfg={'call': ''})
    assert not _guard(freq=7019800)
    # tone keying in USB transmits at dial + tone
    assert not _guard(freq=7124000, mode='PKTUSB', cfg={'key_method': 'audio'})
    assert _guard(freq=7025000, mode='USB', cfg={'key_method': 'audio'})


def test_updater_version_compare():
    import updater as U
    assert U.newer('3.4.10', '3.4.9') and not U.newer('3.4.0', '3.4.0') and not U.newer('v3.3.1', '3.4.0')


def test_bandcond_parse():
    import bandcond
    x = ('<solarflux>87</solarflux><kindex> 1</kindex><band name="80m-40m" time="day">Fair</band>'
         '<band name="80m-40m" time="night">Good</band>')
    r = bandcond.parse(x)
    assert r['bands']['80m-40m'] == ('Fair', 'Good') and r['sfi'] == '87' and r['k'] == '1'


def test_deepcw_measures_their_speed():
    deepcw = pytest.importorskip('deepcw')
    if not deepcw.available():
        pytest.skip('onnxruntime not installed')
    from rig import tone_waveform
    rng = np.random.default_rng(1)
    for wpm in (15, 25, 35):
        s = tone_waveform('CQ CQ DE W1AW W1AW K KO6EJV DE W1AW GM UR RST 579 NAME JOHN QTH CT HW? KN',
                          wpm, 48000, 700, amp=1.0)
        x = np.concatenate([np.zeros(48000), s, np.zeros(96000)])
        x = (0.1 * x + 0.1 * rng.normal(0, 1.0, len(x))).astype(np.float32)
        d = deepcw.DeepCW(48000)
        for i in range(0, len(x), 1024):
            d.process(x[i:i + 1024], tone=700)
        assert d.wpm is not None and abs(d.wpm - wpm) < 0.08 * wpm, (wpm, d.wpm)


def test_rbn_spots_and_region():
    import rbn
    assert rbn.region_of('KO6EJV') == 'NA' and rbn.region_of('VE6WZ') == 'NA' and rbn.region_of('F4GOU') == 'DX'
    r = rbn.RBN('KO6EJV')
    lines = ['DX de K5TR-1-#: 10115.20  N5EEI          CW    10 dB  22 WPM  CQ      2001Z',
             'DX de F4GOU-#:   3564.00  IZ1CQD         CW    23 dB  21 WPM  CQ      2001Z',
             'DX de W3OA-#:    7007.90  HA8RC          CW     4 dB  22 WPM  CQ      2001Z',
             'DX de KM3T-2-#: 14080.0   K1ABC          RTTY  20 dB  45 BPS  CQ      2001Z',
             'junk']
    assert [r.feed(x) for x in lines] == [True, True, True, False, False]
    assert r.counts() == {'30M': 1, '40M': 1} and r.counts(near_only=False)['80M'] == 1
    assert r.on_band('40M')[0]['call'] == 'HA8RC' and r.counts(now=1e12) == {}


def test_install_options_are_defaults(tmp_path, monkeypatch):
    import json
    import config
    (tmp_path / 'install_options.json').write_text(json.dumps({'update_check': False, 'rbn_enable': False,
                                                               'bandcond_enable': True, 'call': 'HACK'}))
    monkeypatch.setattr(sys, 'argv', [str(tmp_path / 'main.py')])
    monkeypatch.setattr(config, 'CFG_PATH', str(tmp_path / 'config.json'))
    c = config.load_cfg()
    assert c['update_check'] is False and c['rbn_enable'] is False and c['call'] == ''   # only network keys
    (tmp_path / 'config.json').write_text(json.dumps({'update_check': True}))
    assert config.load_cfg()['update_check'] is True                                     # Rig Setup wins
