"""Live dits and dahs drawn on the spectrum, with letters above (orange RX, red TX)."""
from morse import code_for
from ui_theme import UI


class MorseStrip:
    """Live dits and dahs drawn right on the spectrum (top right, no box), with each letter above its
    elements: orange = receiving, red = we're sending. RX comes from the decoder element by element;
    TX is played out at our sending speed."""
    RXC, TXC = '#ff8c00', '#ff3c00'

    def __init__(self, root, spec, compact):
        self.root, self.c = root, spec
        self.SW = 200 if compact else 300                  # strip width (px), right edge of the spectrum
        self.u = 4 if compact else 5                       # pixels per dit
        self.ly, self.dy = (24, 35) if compact else (30, 44)   # letter / element rows (below the Hz scale)
        self.fs = -12 if compact else -15
        self.els, self.lets = [], []                       # (x, w, dah, col) / (xc, ch, col)
        self.x, self.pend, self.pend_x, self.pend_col = 0, '', 0, self.RXC
        self.last = 0.0
        self.q, self.qjob = [], None
        self._idle()

    def place(self):                                       # kept for the caller; nothing to place
        pass

    # --- model ---
    def _now(self):
        import time as _t
        return _t.time()

    def _mark(self, dah, col):
        if not self.pend:
            self.pend_x, self.pend_col = self.x, col
        w = (3 if dah else 1) * self.u
        self.els.append((self.x, w, dah, col))
        self.x += w + self.u
        self.pend += '-' if dah else '.'
        self.last = self._now()
        self.render()

    def _letter(self, ch=None):
        """Close the letter being built: label it (decoded from its pattern unless given)."""
        if self.pend:
            from morse import DECODE
            ch = ch or DECODE.get(self.pend, '?')
            xc = (self.pend_x + self.x - self.u) / 2
            self.lets.append((xc, ch.strip('<>'), self.pend_col))
            self.pend = ''
            self.render()

    def _gap(self, units):
        self._letter()
        self.x += units * self.u

    def render(self):
        c = self.c
        c.delete('ms')
        W = max(c.winfo_width(), 100)
        x0 = W - self.SW - 4
        off = max(0, self.x - self.SW)
        keep = off - 4 * self.u                            # forget what has scrolled away
        self.els = [e for e in self.els if e[0] + e[1] > keep]
        self.lets = [t for t in self.lets if t[0] > keep]
        h = self.u
        for x, w, dah, col in self.els:
            X = x0 + x - off
            if X < x0:
                continue
            if dah:
                c.create_rectangle(X, self.dy - h / 2, X + w, self.dy + h / 2, fill=col, outline='', tags='ms')
            else:
                c.create_oval(X, self.dy - h / 2, X + w, self.dy + h / 2, fill=col, outline='', tags='ms')
        for xc, ch, col in self.lets:
            X = x0 + xc - off
            if X >= x0:
                c.create_text(X, self.ly, text=ch, fill=col, font=(UI, self.fs, 'bold'), tags='ms')
        c.tag_raise('ms')

    def _idle(self):
        t = self._now() - self.last
        if self.pend and t > 1.5 and not self.q:          # last letter of an over: label it
            self._letter()
        if (self.els or self.lets) and t > 8 and not self.q:
            self.els, self.lets, self.x = [], [], 0
            self.render()
        self.root.after(500, self._idle)

    # --- RX: one call per keyed element / space from the decoder ---
    def rx(self, kind, ms, dit):
        dit = dit or 60.0
        if kind == 'mark':
            if self.pend and self.pend_col != self.RXC:
                self._letter()
            self._mark(ms > 2.0 * dit, self.RXC)
        elif ms > 5.0 * dit:
            self._gap(4)
        elif ms > 2.0 * dit:
            self._gap(2)

    # --- TX: tokens as the transmitter sends them, played at our speed ---
    def tx(self, tok, wpm, col=None):
        dit = 1.2 / max(5, wpm or 20)
        col = col or self.TXC
        if tok == ' ':
            self.q.append(('gap', 4 * dit, '', col))
        else:
            code = code_for(tok) or ''
            for el in code:
                self.q.append(('dah' if el == '-' else 'dit', (3 if el == '-' else 1) * dit, '', col))
                self.q.append(('egap', dit, '', col))
            if code:
                self.q.append(('let', 2 * dit, tok, col))
        if self.qjob is None:
            self._run()

    # --- RX from the neural decoder: its letters, drawn as dits/dahs at their speed ---
    def rx_text(self, text, wpm):
        if len(self.q) > 120:                              # never fall far behind the copy
            self.q = [e for e in self.q if e[3] != self.RXC]
        for ch in text:
            self.tx(' ' if ch == ' ' else ch, wpm, self.RXC)

    def _run(self):
        if not self.q:
            self.qjob = None
            return
        kind, dur, tok, col = self.q.pop(0)
        if kind in ('dit', 'dah'):
            if self.pend and self.pend_col != col:
                self._letter()
            self._mark(kind == 'dah', col)
        elif kind == 'let':
            self._letter(tok)
            self.x += 2 * self.u
        elif kind == 'gap':
            self._gap(3)
        self.qjob = self.root.after(max(1, int(dur * 1000)), self._run)

    def tx_done(self):
        if self.qjob is not None and len(self.q) < 6:      # let the last letter finish drawing
            return
        self.q.clear()
