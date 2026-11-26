"""Contest mode: Enter-Sends-Message, exchange parsing, dupes, rate, Cabrillo.

One keypress always sends the right next message. Nothing goes out on its
own - the operator presses Enter for every transmission.
"""
import datetime as dt
import json
import os
import re

STATES = set('AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ '
             'NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC'.split())
PROVS = set('AB BC MB NB NL NS NT NU ON PE QC SK YT'.split())
SECTIONS = set(('CT EMA ME NH RI VT WMA ENY NLI NNJ NNY SNJ WNY DE EPA MDC WPA AL GA KY NC NFL PR SC SFL '
                'TN VA VI WCF AR LA MS NM NTX OK STX WTX EB LAX ORG PAC SB SCV SDG SF SJV SV AZ EWA ID MT '
                'NV OR UT WWA WY AK MI OH WV IL IN WI CO IA KS MN MO ND NE SD '
                'AB BC GH MB NB NL NS ONE ONN ONS PE QC SK TER DX').split())
CALL_RE = re.compile(r'^(?:[A-Z0-9]{1,4}/)?[A-Z0-9]{1,3}\d{1,2}[A-Z]{1,4}(?:/[A-Z0-9]{1,4})?$')
NOISE = set('TU R RR DE K KN BK <KN> <BK> <SK> SK 73 GL EE 5NN 599 CQ TEST SST CWT FD POTA ES UR NR HR '
            'TNX FB GM GA GE AGN PSE ? QRZ QRZ? = <BT> + <AR> BACK'.split())

TEMPLATES = {
    'SST': dict(
        title='K1USN Slow Speed Test (SST)', cabrillo='K1USN-SST', dupe='band',
        blurb='Friendly, slow (20 WPM or less) one-hour test. Exchange: first name + state.',
        my_fields=[('name', 'Your first name'), ('state', 'Your state/province')],
        rx_fields=[('name', 'Name'), ('spc', 'State/Prov')],
        cq='CQ SST {my}', run_exch='{call} {name} {state}', run_tu='TU {r_name} {my}',
        sp_call='{my}', sp_reply='TU {name} {state}',
        ask={'name': 'NAME?', 'spc': 'QTH?'}),
    'POTA': dict(
        title='POTA - Parks on the Air', cabrillo='', dupe='band',
        blurb='Hunt park activators (or activate). Exchange: signal report + state.',
        my_fields=[('state', 'Your state'), ('park', 'Park ref (if activating)')],
        rx_fields=[('rst', 'RST'), ('spc', 'State'), ('park', 'Their park (hunting)')],
        cq='CQ POTA DE {my} {my} K', run_exch='{call} TU 5NN {state} {state}', run_tu='TU 73 {my}',
        sp_call='{my}', sp_reply='TU 5NN {state} {state} 73',
        ask={'rst': 'RST?', 'spc': 'QTH?'}),
    'CWT': dict(
        title='CWops CWT (Wed/Thu, 1 hour)', cabrillo='CW-OPS', dupe='band',
        blurb='Fast, friendly mini-test. Exchange: name + CWops member number (non-members send state).',
        my_fields=[('name', 'Your first name'), ('nr', 'Member # or state')],
        rx_fields=[('name', 'Name'), ('nr', 'Nr/State')],
        cq='CQ CWT {my}', run_exch='{call} {name} {nr}', run_tu='TU {my} CWT',
        sp_call='{my}', sp_reply='TU {name} {nr}',
        ask={'name': 'NAME?', 'nr': 'NR?'}),
    'FD': dict(
        title='ARRL Field Day', cabrillo='ARRL-FD', dupe='band',
        blurb='The big one. Exchange: class (e.g. 1D) + ARRL section (e.g. SDG).',
        my_fields=[('cls', 'Your class (e.g. 1D)'), ('sec', 'Your section (e.g. SDG)')],
        rx_fields=[('cls', 'Class'), ('sec', 'Section')],
        cq='CQ FD {my} {my}', run_exch='{call} {cls} {sec}', run_tu='TU {my} FD',
        sp_call='{my}', sp_reply='TU {cls} {sec}',
        ask={'cls': 'CL?', 'sec': 'SEC?'}),
}


def parse_exchange(cid, text, my_call, mine=()):
    """Pull the received exchange fields out of a decoded over. mine = our own exchange words."""
    toks = text.upper().replace('?', ' ? ').split()
    my = my_call.upper()
    if my in toks:                          # exchange follows our call
        toks = toks[len(toks) - 1 - toks[::-1].index(my) + 1:]
    out = {}
    words = [t for t in toks if t not in NOISE and not (CALL_RE.match(t) and any(c.isdigit() for c in t)
                                                        and not re.fullmatch(r'\d+[A-F]', t))]
    if cid == 'FD':
        for t in words:
            if re.fullmatch(r'\d{1,2}[A-F]', t) and 'cls' not in out:
                out['cls'] = t
            elif t in SECTIONS and 'sec' not in out:
                out['sec'] = t
    elif cid == 'SST':
        # exchange is NAME STATE; callers often echo our name first ("TU DERIC BOB CT")
        spc = lambda t: t in STATES or t in PROVS or t == 'DX'
        pairs = [(words[i - 1], t) for i, t in enumerate(words)
                 if i > 0 and spc(t) and re.fullmatch(r'[A-Z]{2,12}', words[i - 1]) and not spc(words[i - 1])]
        if pairs:
            out['name'], out['spc'] = pairs[-1]
        else:
            names = [t for t in words if re.fullmatch(r'[A-Z]{2,12}', t) and not spc(t) and t not in mine]
            if names:
                out['name'] = names[-1]
            sp = [t for t in words if spc(t)]
            if sp:
                out['spc'] = sp[-1]
    elif cid == 'CWT':
        # NAME then member number (digits) or state/province/DX prefix: "TOM 1234", "BOB CT"
        nr = lambda t: re.fullmatch(r'\d{1,6}', t) or t in STATES or t in PROVS or t == 'DX'
        pairs = [(words[i - 1], t) for i, t in enumerate(words)
                 if i > 0 and nr(t) and re.fullmatch(r'[A-Z]{2,12}', words[i - 1]) and not nr(words[i - 1])
                 and words[i - 1] not in mine]
        if pairs:
            out['name'], out['nr'] = pairs[-1]
        else:
            names = [t for t in words if re.fullmatch(r'[A-Z]{2,12}', t) and not nr(t) and t not in mine]
            if names:
                out['name'] = names[-1]
            nums = [t for t in words if nr(t)]
            if nums:
                out['nr'] = nums[-1]
    elif cid == 'POTA':
        for t in toks:
            if re.fullmatch(r'[1-5][1-9N][1-9N]', t) and 'rst' not in out:
                out['rst'] = t.replace('N', '9')
            elif (t in STATES or t in PROVS) and 'spc' not in out:
                out['spc'] = t
            elif re.fullmatch(r'[A-Z]{1,2}-\d{4,5}', t):
                out['park'] = t
    return out


def find_caller(text, my_call):
    """Run mode: callsign of whoever answered our CQ (latest one sent)."""
    my = my_call.upper()
    from plain import is_call
    cands = [t for t in text.upper().split() if (is_call(t) or ('*' in t and any(c.isdigit() for c in t))) and t != my]
    return _fix(cands[-1]) if cands else ''


def _fix(call):
    try:
        import callfix
        return callfix.default().fix(call)[0]
    except Exception:
        return call


def find_cq(text):
    """S&P mode: callsign of the station calling CQ."""
    t = text.upper()
    m = [(x.start(), x.group(1)) for x in
         re.finditer(r'\bCQ\b.*?\b((?:[A-Z0-9]{1,4}/)?[A-Z0-9*]{1,3}\d{1,2}[A-Z*]{1,4})\b', t)]
    # contest runners often skip CQ between contacts: "TU K5AX CWT" / "K5AX TEST"
    m += [(x.start(), x.group(1)) for x in
          re.finditer(r'\b((?:[A-Z0-9]{1,4}/)?[A-Z0-9*]{1,3}\d{1,2}[A-Z*]{1,4})\s+(?:CWT|TEST|SST|FD)\b', t)]
    m.sort()
    return _fix(m[-1][1]) if m else ''


def band(freq):
    from qso import band_for
    return band_for(freq) or '?'


class ContestSession:
    def __init__(self, cid, my_call, my_exch, log_dir):
        self.cid, self.t = cid, TEMPLATES[cid]
        self.my = my_call.upper()
        self.my_exch = {k: v.upper() for k, v in my_exch.items()}
        self.mode = 'run'
        self.log_dir = log_dir
        os.makedirs(log_dir, exist_ok=True)
        self.path = os.path.join(log_dir, f'contest_{cid}_{dt.date.today():%Y%m%d}.jsonl')
        self.qsos = self._load()
        self.reset()

    def _load(self):
        if not os.path.exists(self.path):
            return []
        with open(self.path, encoding='utf-8') as f:
            return [json.loads(x) for x in f if x.strip()]

    def reset(self):
        self.call = ''
        self.rcvd = {}
        self.exch_sent = False
        self.call_sent = False
        self.rx = ''

    # ----- RX -----
    def feed(self, text):
        self.rx = (self.rx + text)[-400:]
        done = self.rx[:self.rx.rfind(' ') + 1]
        if not self.call:
            if self.mode == 'run':
                c = find_caller(done, self.my)
            else:
                c = find_cq(done)
            if c:
                self.call = c
                self.rx = ''
                return True
        else:
            got = parse_exchange(self.cid, done, self.my, set(self.my_exch.values()))
            changed = False
            for k, v in got.items():
                if k in dict(self.t['rx_fields']) and self.rcvd.get(k) != v:
                    self.rcvd[k] = v
                    changed = True
            return changed
        return False

    def complete(self):
        need = [k for k, _ in self.t['rx_fields'] if k != 'park']
        return all(self.rcvd.get(k) for k in need)

    def missing(self):
        return [k for k, _ in self.t['rx_fields'] if k != 'park' and not self.rcvd.get(k)]

    # ----- what Enter does -----
    def _fmt(self, tpl):
        d = dict(self.my_exch, my=self.my, call=self.call or '???')
        d.update({f'r_{k}': v for k, v in self.rcvd.items()})
        d.setdefault('r_name', '')
        return ' '.join(tpl.format(**d).split())

    def next_action(self):
        """-> (label, text, logs_after)"""
        t = self.t
        if self.mode == 'run':
            if not self.call:
                return 'CQ', self._fmt(t['cq']), False
            if self.is_dupe():
                return 'DUPE - TU anyway', f'TU {self.my}', False
            if not self.exch_sent:
                return 'SEND EXCHANGE', self._fmt(t['run_exch']), False
            if not self.complete():
                return 'ASK AGAIN', ' '.join(t['ask'][k] for k in self.missing()), False
            return 'TU + LOG', self._fmt(t['run_tu']), True
        # search & pounce
        if not self.call:
            return 'LISTEN FOR A CQ', '', False
        if not self.call_sent:
            return 'CALL THEM', self._fmt(t['sp_call']), False
        if not self.complete():
            return 'ASK AGAIN', ' '.join(t['ask'][k] for k in self.missing()), False
        return 'SEND + LOG', self._fmt(t['sp_reply']), True

    def sent(self, label):
        if label == 'SEND EXCHANGE':
            self.exch_sent = True
        elif label == 'CALL THEM':
            self.call_sent = True
        elif label.startswith('DUPE'):
            self.reset()

    # ----- log -----
    def dupe_key(self, call, freq):
        return (call.upper(), band(freq))

    def is_dupe(self, freq=None):
        if not self.call:
            return False
        f = freq if freq is not None else getattr(self, 'freq', 0)
        return any((q['call'], band(q['freq'])) == self.dupe_key(self.call, f) for q in self.qsos)

    def log(self, freq):
        now = dt.datetime.now(dt.timezone.utc)
        q = dict(call=self.call, freq=int(freq or 0), utc=now.strftime('%Y-%m-%d %H%M'),
                 sent=dict(self.my_exch), rcvd=dict(self.rcvd), cid=self.cid)
        self.qsos.append(q)
        try:
            import callfix
            callfix.default().note(self.call, 3)     # worked = trusted
        except Exception:
            pass
        with open(self.path, 'a', encoding='utf-8') as f:
            f.write(json.dumps(q) + '\n')
        self.reset()
        return q

    def rate(self, minutes=60):
        now = dt.datetime.now(dt.timezone.utc)
        n = 0
        for q in self.qsos:
            t = dt.datetime.strptime(q['utc'], '%Y-%m-%d %H%M').replace(tzinfo=dt.timezone.utc)
            if (now - t).total_seconds() <= minutes * 60:
                n += 1
        return int(n * 60 / minutes)

    # ----- exports -----
    def sent_str(self, q):
        if self.cid == 'CWT':
            return f"{q['sent'].get('name', '')} {q['sent'].get('nr', '')}"
        if self.cid == 'SST':
            return f"{q['sent'].get('name', '')} {q['sent'].get('state', '')}"
        if self.cid == 'FD':
            return f"{q['sent'].get('cls', '')} {q['sent'].get('sec', '')}"
        return f"599 {q['sent'].get('state', '')}"

    def rcvd_str(self, q):
        r = q['rcvd']
        if self.cid == 'CWT':
            return f"{r.get('name', '')} {r.get('nr', '')}"
        if self.cid == 'SST':
            return f"{r.get('name', '')} {r.get('spc', '')}"
        if self.cid == 'FD':
            return f"{r.get('cls', '')} {r.get('sec', '')}"
        return f"{r.get('rst', '599')} {r.get('spc', '')}"

    def cabrillo(self, operator_name='', power='LOW', email=''):
        lines = ['START-OF-LOG: 3.0', f"CONTEST: {self.t['cabrillo'] or self.cid}", f'CALLSIGN: {self.my}',
                 'CATEGORY-OPERATOR: SINGLE-OP', 'CATEGORY-BAND: ALL', f'CATEGORY-POWER: {power}',
                 'CATEGORY-MODE: CW', 'CATEGORY-ASSISTED: ASSISTED', f'CLAIMED-SCORE: {len(self.qsos)}',
                 f'OPERATORS: {self.my}', f'NAME: {operator_name}', 'CREATED-BY: ZBM2 CW Tool']
        if email:
            lines.append(f'EMAIL: {email}')
        if self.cid == 'FD':
            lines.append(f"LOCATION: {self.my_exch.get('sec', '')}")
        for q in self.qsos:
            khz = round(q['freq'] / 1000) if q['freq'] else 7000
            date, hhmm = q['utc'].split()
            lines.append(f"QSO: {khz:>5} CW {date} {hhmm} {self.my:<13} {self.sent_str(q):<10} "
                         f"{q['call']:<13} {self.rcvd_str(q)}")
        lines.append('END-OF-LOG:')
        return '\n'.join(lines) + '\n'

    def adif_extra(self, q):
        """Extra ADIF fields for the main log."""
        f = {'CONTEST_ID': self.t['cabrillo'] or '', 'STX_STRING': self.sent_str(q), 'SRX_STRING': self.rcvd_str(q)}
        if self.cid == 'POTA':
            f = {'SIG': 'POTA', 'SIG_INFO': q['rcvd'].get('park', ''),
                 'MY_SIG': 'POTA' if q['sent'].get('park') else '', 'MY_SIG_INFO': q['sent'].get('park', '')}
        return {k: v for k, v in f.items() if v}
