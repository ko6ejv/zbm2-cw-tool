"""Plain-English translation of CW shorthand (Q-codes, abbreviations, prosigns)."""
import re

CALL_RE = re.compile(r'^(?:[A-Z0-9]{1,4}/)?[A-Z0-9]{1,3}\d{1,2}[A-Z]{1,4}(?:/[A-Z0-9]{1,4})?$')
def is_call(w):
    """Real callsign? Needs a letter before the digit and a letter after it (rejects 100W, 72F, 5NN, FN31)."""
    w = w.upper()
    return bool(CALL_RE.match(w)) and bool(re.search(r'[A-Z][A-Z0-9]*\d+[A-Z]', w.split('/')[-1] if
                                                    len(w.split('/')[-1]) > 3 else w))


GRID_RE = re.compile(r'^[A-R]{2}\d{2}(?:[A-X]{2})?$')

# multi-word phrases first (matched greedily, longest first)
PHRASES = {
    'TNX FER CALL': 'thanks for the call', 'TNX FER QSO': 'thanks for the contact',
    'TNX FER RPRT': 'thanks for the report', 'TNX FER FB QSO': 'thanks for the great contact',
    'TU FER QSO': 'thank you for the contact', 'TU FER CALL': 'thank you for the call',
    'UR RST': 'your signal report is', 'UR SIG': 'your signal is', 'RST IS': 'signal report is',
    'HW CPY': 'how do you copy me?', 'HW CPY?': 'how do you copy me?', 'NAME HR': 'my name here is',
    'NAME IS': 'my name is', 'OP HR': 'operator here is', 'QTH HR': 'my location here is',
    'QTH IS': 'my location is', 'RIG HR': 'my radio here is', 'RIG IS': 'my radio is',
    'ANT IS': 'my antenna is', 'PWR HR': 'my power here is', 'WX HR': 'weather here is',
    '73 ES GL': 'best regards and good luck', 'ES GL': 'and good luck', 'CUL': 'see you later',
    'GUD DX': 'good luck working distant stations', 'PSE QRS': 'please send slower',
    'QRS PSE': 'please send slower', 'AGN PSE': 'again, please', 'PSE AGN': 'please repeat',
    'QSL VIA': 'send QSL card via', 'BURO': 'through the QSL bureau', 'LOTW': 'Logbook of the World',
    'CQ DX': 'calling any distant (foreign) station', 'CQ POTA': 'calling anyone (Parks on the Air)',
    'CQ SOTA': 'calling anyone (Summits on the Air)', 'CQ TEST': 'calling anyone for the contest',
    'FB OM': 'great, friend', 'R R': 'got it',
}

WORDS = {
    'CQ': 'calling anyone', 'DE': 'from', 'K': '(over to anyone)', 'KN': '(over to you only)',
    '<KN>': '(over to you only)', 'BK': '(back to you)', '<BK>': '(back to you)',
    'SK': '(end of contact)', '<SK>': '(end of contact)', 'AR': '(end of message)', '<AR>': '(end of message)',
    '+': '(end of message)', '<BT>': '-', '=': '-', '<AS>': '(wait)', 'AS': '(wait)', 'CL': '(closing station)',
    'R': 'received', 'RR': 'received', 'TNX': 'thanks', 'TKS': 'thanks', 'TU': 'thank you',
    'FER': 'for', 'FB': 'great', 'UR': 'your', 'URS': 'yours', 'HR': 'here', 'HW': 'how',
    'HW?': 'how do you copy?', 'ES': 'and', 'GM': 'good morning', 'GA': 'good afternoon',
    'GE': 'good evening', 'GN': 'good night', 'GL': 'good luck', 'GD': 'good day', 'OM': 'friend',
    'YL': 'young lady', 'XYL': 'wife', 'OP': 'operator', 'NAME': 'name', 'QTH': 'location',
    'RIG': 'radio', 'ANT': 'antenna', 'PWR': 'power', 'W': 'watts', 'WX': 'weather',
    'TEMP': 'temperature', 'AGN': 'again', 'AGN?': 'say again?', 'PSE': 'please', 'PLS': 'please',
    'RPT': 'repeat', 'SRI': 'sorry', 'NR': 'number', 'ABT': 'about', 'BCNU': 'be seeing you',
    'CUL': 'see you later', 'CPY': 'copy', 'SIG': 'signal', 'SIGS': 'signals', 'RST': 'signal report',
    '5NN': '599 (perfect signal)', 'FT': 'feet', 'HI': '(laughter)', 'HIHI': '(laughing)',
    'OK': 'okay', 'NW': 'now', 'WID': 'with', 'WL': 'will', 'VY': 'very', 'GUD': 'good',
    'BEAM': 'beam antenna', 'DIPOLE': 'dipole antenna', 'VERT': 'vertical antenna', 'EFHW': 'end-fed wire antenna',
    'QRP': 'low power', 'QRO': 'high power', 'DX': 'distant station', 'POTA': 'Parks on the Air',
    'SOTA': 'Summits on the Air', 'PARK': 'park', 'TEST': 'contest', 'YRS': 'years', 'AGE': 'age',
    'LIC': 'licensed', 'RETD': 'retired', 'WKD': 'worked', 'WRK': 'work', 'FM': 'from',
    '73': 'best regards', '88': 'love and kisses', '599': '599 (perfect signal)', 'EE': '(dit dit - bye)',
    'E': '', 'T': '', 'ENUF': 'enough', 'MNI': 'many', 'OB': 'old buddy', 'OT': 'old timer',
    'PSED': 'pleased', 'GLD': 'glad', 'CONDX': 'band conditions', 'QSB': 'fading', 'QRM': 'interference',
    'QRN': 'static', 'QSY': 'change frequency', 'QSL': 'confirmed', 'QSL?': 'do you confirm?',
    'QRL?': 'is this frequency in use?', 'QRL': 'this frequency is busy', 'QRZ?': 'who is calling me?',
    'QRZ': 'who is calling me?', 'QRS': 'send slower', 'QRQ': 'send faster', 'QRT': 'stopping now',
    'QRX': 'wait', 'QTH?': 'where are you?', 'QSO': 'contact', 'QRV': 'ready', 'QRU': 'nothing more for you',
    '?': '?', 'MY': 'my', 'IS': 'is', 'AND': 'and', 'IN': 'in', 'ON': 'on', 'THE': 'the', 'TO': 'to',
    'GRID': '', 'LOC': '', 'OUT': 'out', 'U': 'you', 'C': 'yes', 'N': 'no', 'NIL': 'nothing', 'B4': 'before',
}

R_DESC = {1: 'unreadable', 2: 'barely readable', 3: 'readable with difficulty', 4: 'readable', 5: 'perfectly readable'}
S_DESC = {1: 'faint', 2: 'very weak', 3: 'weak', 4: 'fair', 5: 'fairly good', 6: 'good',
          7: 'moderately strong', 8: 'strong', 9: 'very strong'}


def explain_rst(r):
    r = r.replace('N', '9')
    if not re.fullmatch(r'[1-5][1-9][1-9]?', r):
        return r
    s = f'{r} ({R_DESC[int(r[0])]}, {S_DESC[int(r[1])]}'
    if len(r) == 3:
        s += ', clean tone' if r[2] in '89' else ', rough tone'
    return s + ')'


def _fixcall(w):
    try:
        import callfix
        c, how = callfix.default().fix(w)
        return c if how in ('session', 'scp') else w
    except Exception:
        return w


def translate(text, my_call=''):
    """Return plain-English rendering of a decoded CW segment."""
    raw = text.upper().split()
    # segment that is only dit-junk (E/I/T/EE/ETE...) is noise, not a message
    if raw and all(re.fullmatch(r'[EIT*]+', w) for w in raw):
        return ''
    words = [w for j, w in enumerate(raw) if j == 0 or w != raw[j - 1] or w in ('R',)]  # ops send things twice
    out, i = [], 0
    my_call = my_call.upper()
    prev = ''
    while i < len(words):
        w = words[i]
        # longest phrase match (up to 4 words)
        for n in (4, 3, 2):
            ph = ' '.join(words[i:i + n])
            if len(words) - i >= n and ph in PHRASES:
                out.append(PHRASES[ph])
                prev = ph.split()[-1]
                i += n
                break
        else:
            if w == my_call and my_call:
                out.append('YOU')
            elif prev in ('RST', 'UR', 'IS') and re.fullmatch(r'[1-5][1-9N][1-9N]?', w):
                out.append(explain_rst(w))
            elif w in WORDS:
                if WORDS[w]:
                    out.append(WORDS[w])
            elif re.fullmatch(r'\d+W', w):
                out.append(f'{w[:-1]} watts')
            elif re.fullmatch(r'\d+F', w):
                out.append(f'{w[:-1]} degrees F')
            elif re.fullmatch(r'\d+C', w):
                out.append(f'{w[:-1]} degrees C')
            elif re.fullmatch(r'\d+', w):
                out.append(w)
            elif GRID_RE.match(w) and (prev in ('GRID', 'LOC', 'QTH') or len(w) == 4):
                out.append(f'grid {w}')
            elif is_call(w) or ('*' in w and any(ch.isdigit() for ch in w)):
                out.append(f'[{_fixcall(w)}]')
            elif GRID_RE.match(w):
                out.append(f'grid {w}')
            else:
                out.append(w.capitalize() if len(w) > 2 else w)
            prev = w
            i += 1
    s = ' '.join(out)
    s = re.sub(r'\b(\w[\w ]*?)( \1\b)+', r'\1', s)          # collapse repeats: "CQ CQ CQ"
    s = re.sub(r'\[(\w+)\]( \[\1\])+', r'[\1]', s)            # "[W1AW] [W1AW]"
    s = re.sub(r'\s+', ' ', s).strip()
    return s[:1].upper() + s[1:] if s else ''


SEGMENT_END = re.compile(r'(?:^|\s)(K|KN|<KN>|BK|<BK>|SK|<SK>|AR|<AR>|\+|EE)\s+$')   # whole word only
