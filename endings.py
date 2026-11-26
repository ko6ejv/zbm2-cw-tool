"""Automatic over endings (K / KN / BK / SK / CL) and station ID.

Beginners type what they want to say; this decides how to hand the
conversation back and adds callsigns when the rules need them
(97.119: ID at least every 10 minutes and at the end of a contact).
"""
import re
import time

ID_INTERVAL = 9 * 60          # ID a minute early to be safe
GOODBYE = re.compile(r'\b(73|TU|GN|CUL|GB|BCNU|GL|SK)\b')
QRT = re.compile(r'\bQRT\b')
ENDINGS = re.compile(r'(\s+(K|KN|<KN>|BK|<BK>|SK|<SK>|AR|<AR>|CL|EE))+\s*$')

EXPLAIN = {
    'K': 'then hands it to anyone listening (K)',
    'KN': 'then hands it back to {call} only (KN)',
    'BK': 'then flips it straight back to {call} (BK)',
    'SK': 'then ends the contact (SK) with a friendly dit-dit',
    'CL': 'then says you are going off the air (CL)',
    'NONE': 'and keeps your turn going (no ending)',
}


class Ender:
    def __init__(self, my_call):
        self.my = my_call.upper()
        self.last_id = 0.0

    def note_sent(self, text):
        if self.my and self.my in text.upper().split():
            self.last_id = time.time()

    def id_due(self):
        return time.time() - self.last_id > ID_INTERVAL

    def finish(self, text, their_call='', keep_over=False):
        """-> (full_text, ending_code). keep_over=True (Shift+Enter): no ending."""
        t = ' '.join(text.upper().split())
        if not t:
            return '', 'NONE'
        if keep_over:
            return t, 'NONE'
        t = ENDINGS.sub('', t)                       # we decide the ending
        my, them = self.my, (their_call or '').upper()
        words = t.split()
        if words and words[0] == 'CQ':
            if my not in words:
                t = f'CQ CQ CQ DE {my} {my}' if t == 'CQ' else f'{t} {t} DE {my} {my}'
            return f'{t} K', 'K'
        if QRT.search(t):
            return f'{t} {them + " " if them else ""}DE {my} CL'.replace('  ', ' '), 'CL'
        if GOODBYE.search(t):
            tail = f'{them} DE {my}' if them else f'DE {my}'
            return f'{t} {tail} <SK> EE', 'SK'
        if not them:
            return f'{t} DE {my} K', 'K'
        short = len(words) <= 4
        if short and not self.id_due():
            return f'{t} BK', 'BK'
        if short:
            return f'{t} {them} DE {my} BK', 'BK'
        return f'{t} {them} DE {my} <KN>', 'KN'

    @staticmethod
    def explain(code, their_call=''):
        return EXPLAIN.get(code, '').format(call=their_call or 'them')
