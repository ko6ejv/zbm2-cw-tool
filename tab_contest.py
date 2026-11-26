"""CONTEST tab - Enter Sends Message, live exchange parsing, dupes, rate, Cabrillo."""
import os
import tkinter as tk
from tkinter import ttk, filedialog

import plain as PL
from contest import ContestSession, TEMPLATES, band
from ui_theme import BG, PANEL, FG, DIM, ACC, RX, TX, UI, Tip

MY_KEYS = {'name': 'c_name', 'state': 'c_state', 'cls': 'c_cls', 'sec': 'c_sec', 'park': 'c_park', 'nr': 'c_nr'}


class ContestTab:
    def __init__(self, app, parent):
        self.app, self.cfg = app, app.cfg
        self.parent = parent
        self.session = None
        self.typed = set()            # fields the operator typed in (don't overwrite)

        top = tk.Frame(parent, bg=PANEL)
        top.pack(fill='x', pady=(6, 0))
        tk.Label(top, text=' CONTEST ', bg=ACC, fg=BG, font=(UI, -16)).pack(side='left', padx=(0, 6))
        self.v_cid = tk.StringVar(value=TEMPLATES[self.cfg.get('contest_id', 'SST')]['title'])
        cb = ttk.Combobox(top, textvariable=self.v_cid, values=[t['title'] for t in TEMPLATES.values()],
                          state='readonly', width=34)
        cb.pack(side='left', pady=4)
        cb.bind('<<ComboboxSelected>>', lambda e: self.new_session())
        self.v_mode = tk.StringVar(value=self.cfg.get('contest_mode', 'run'))
        for val, lbl, tip in (('run', 'RUN (I call CQ)', 'You call CQ and others answer you.'),
                              ('sp', 'S&P (I answer CQs)', 'Search & Pounce: tune around, answer people calling CQ.')):
            rb = tk.Radiobutton(top, text=lbl, variable=self.v_mode, value=val, bg=PANEL, fg=FG,
                                selectcolor=BG, activebackground=PANEL, font=(UI, -16),
                                command=self.mode_changed)
            rb.pack(side='left', padx=6)
            Tip(rb, tip)
        self.lbl_blurb = tk.Label(parent, text='', bg=BG, fg=DIM, font=(UI, -16), anchor='w')
        self.lbl_blurb.pack(fill='x')

        self.myf = tk.Frame(parent, bg=BG)
        self.myf.pack(fill='x', pady=(2, 4))
        # entry row
        self.ef = tk.Frame(parent, bg=PANEL)
        self.ef.pack(fill='x')
        # ESM line
        esm = tk.Frame(parent, bg=PANEL)
        esm.pack(fill='x', pady=(6, 0))
        tk.Label(esm, text='ENTER>', bg=PANEL, fg=ACC, font=(UI, -16)).pack(side='left', padx=6)
        self.lbl_act = tk.Label(esm, text='', bg=ACC, fg=BG, font=(UI, -16), padx=6)
        self.lbl_act.pack(side='left')
        self.lbl_send = tk.Label(esm, text='', bg=PANEL, fg=FG, font=(UI, -24), anchor='w')
        self.lbl_send.pack(side='left', fill='x', expand=True, padx=6)
        ttk.Button(esm, text='SEND  Enter/F1', style='Send.TButton', command=self.esm).pack(side='left', padx=4, pady=4)
        ttk.Button(esm, text='WIPE', command=self.wipe).pack(side='left', padx=(0, 6))
        self.lbl_means = tk.Label(parent, text='', bg=BG, fg=DIM, font=(UI, -16), anchor='w', justify='left',
                                  wraplength=1100)
        self.lbl_means.pack(fill='x')

        mid = tk.Frame(parent, bg=BG)
        mid.pack(fill='both', expand=True, pady=(6, 0))
        right = tk.Frame(mid, bg=BG)
        right.pack(side='right', fill='y', padx=(8, 0))
        self.lbl_stats = tk.Label(right, text='', bg='#000000', fg=FG, font=(UI, -16), justify='left',
                                  anchor='nw', padx=8, pady=6, highlightthickness=1, highlightbackground=DIM,
                                  width=26)
        self.lbl_stats.pack(fill='x')
        ttk.Button(right, text='EXPORT CABRILLO', command=self.export).pack(fill='x', pady=(6, 2))
        ttk.Button(right, text='LOG MANUALLY', command=self.log_manual).pack(fill='x', pady=2)
        Tip(right, 'Cabrillo is the file contests want you to submit after the event.')
        left = tk.Frame(mid, bg=BG)
        left.pack(side='left', fill='both', expand=True)
        self.rx = tk.Text(left, bg='#000000', fg=RX, font=(UI, -16), height=4, wrap='word', relief='flat',
                          padx=6, highlightthickness=1, highlightbackground=DIM)
        self.rx.pack(fill='x')
        self.rx.bind('<Key>', lambda e: 'break')
        st = ttk.Style()
        st.configure('Log.Treeview', background='#000000', fieldbackground='#000000', foreground=FG,
                     font=(UI, -16), rowheight=20)
        st.configure('Log.Treeview.Heading', background=PANEL, foreground=DIM, font=(UI, -16))
        self.log = ttk.Treeview(left, columns=('utc', 'band', 'call', 'sent', 'rcvd'), show='headings',
                                style='Log.Treeview', height=8)
        for c, w in (('utc', 150), ('band', 60), ('call', 120), ('sent', 160), ('rcvd', 200)):
            self.log.heading(c, text=c.upper())
            self.log.column(c, width=w)
        self.log.pack(fill='both', expand=True, pady=(6, 0))
        self.new_session()
        app.root.after(5000, self._tick)

    # ---------- session ----------
    def _cid(self):
        title = self.v_cid.get()
        return next(k for k, t in TEMPLATES.items() if t['title'] == title)

    def my_exch(self):
        t = TEMPLATES[self._cid()]
        out = {}
        for k, _ in t['my_fields']:
            v = self.my_vars[k].get().strip().upper() if hasattr(self, 'my_vars') and k in self.my_vars else ''
            if not v:
                v = self.cfg.get(MY_KEYS[k], '') or (self.cfg.get('name', '').split()[0] if k == 'name' else '')
            out[k] = v.upper()
        return out

    def new_session(self):
        cid = self._cid()
        t = TEMPLATES[cid]
        self.cfg['contest_id'] = cid
        self.lbl_blurb.config(text=' ' + t['blurb'])
        for w in self.myf.winfo_children():
            w.destroy()
        tk.Label(self.myf, text='MY EXCHANGE:', bg=BG, fg=DIM, font=(UI, -16)).pack(side='left')
        prev = getattr(self, 'my_vars', {})
        self.my_vars = {}
        for k, lbl in t['my_fields']:
            tk.Label(self.myf, text=lbl, bg=BG, fg=FG, font=(UI, -16)).pack(side='left', padx=(10, 4))
            default = (prev.get(k).get() if k in prev else '') or self.cfg.get(MY_KEYS[k], '') or \
                (self.cfg.get('name', '').split()[0] if k == 'name' and self.cfg.get('name') else '')
            v = tk.StringVar(value=default)
            e = tk.Entry(self.myf, textvariable=v, bg=PANEL, fg=FG, insertbackground=FG, font=(UI, -16),
                         relief='flat', width=10)
            e.pack(side='left')
            e.bind('<KeyRelease>', lambda ev: self._my_changed())
            self.my_vars[k] = v
        for w in self.ef.winfo_children():
            w.destroy()
        self.vars = {}
        fields = [('call', 'CALL')] + t['rx_fields']
        for k, lbl in fields:
            col = tk.Frame(self.ef, bg=PANEL)
            col.pack(side='left', padx=6, pady=4)
            tk.Label(col, text=lbl, bg=PANEL, fg=DIM, font=(UI, -16)).pack(anchor='w')
            v = tk.StringVar()
            e = tk.Entry(col, textvariable=v, bg='#000000', fg=FG, insertbackground=FG,
                         font=(UI, -32 if k == 'call' else -24), relief='flat', width=12 if k == 'call' else 8)
            e.pack()
            e.bind('<Return>', lambda ev: self.esm())
            e.bind('<KeyRelease>', lambda ev, kk=k: self._edited(kk, ev))
            self.vars[k] = v
            if k == 'call':
                self.call_entry = e
        self.lbl_dupe = tk.Label(self.ef, text='', bg=PANEL, fg=TX, font=(UI, -24))
        self.lbl_dupe.pack(side='left', padx=10)
        self.typed = set()
        self.session = ContestSession(cid, self.cfg['call'], self.my_exch(),
                                      os.path.join(self.app.data_dir, 'contests'))
        self.session.mode = self.v_mode.get()
        self._fill_log()
        self.refresh()

    def _my_changed(self):
        self.session.my_exch = self.my_exch()
        for k, v in self.session.my_exch.items():
            self.cfg[MY_KEYS[k]] = v
        self.refresh()

    def mode_changed(self):
        self.cfg['contest_mode'] = self.v_mode.get()
        if self.session:
            self.session.mode = self.v_mode.get()
            self.session.reset()
        self.refresh()

    def _edited(self, k, ev=None):
        if ev is not None and ev.keysym in ('Return', 'Tab', 'Shift_L', 'Shift_R', 'F1'):
            return
        self.typed.add(k)
        v = self.vars[k].get().strip().upper()
        if k == 'call':
            self.session.call = v
        elif v:
            self.session.rcvd[k] = v
        else:
            self.session.rcvd.pop(k, None)
        self.refresh(sync=False)

    # ---------- app hooks ----------
    def on_show(self):
        self.call_entry.focus_set()

    def on_config(self):
        if self.session and self.session.my != self.cfg['call'].upper():
            self.new_session()

    def f1(self):
        self.esm()

    def preload(self, reply):
        pass

    def on_rx(self, chunk, snr):
        self.rx.insert('end', chunk)
        if int(self.rx.index('end-1c').split('.')[0]) > 200:
            self.rx.delete('1.0', '100.0')
        self.rx.see('end')
        if self.session and self.session.feed(chunk):
            self.refresh()

    # ---------- display ----------
    def refresh(self, sync=True):
        s = self.session
        s.freq = self.app.freq
        if sync:
            if 'call' not in self.typed:
                self.vars['call'].set(s.call)
            for k, _ in s.t['rx_fields']:
                if k not in self.typed:
                    self.vars[k].set(s.rcvd.get(k, ''))
        label, text, logs = s.next_action()
        self.lbl_act.config(text=f' {label} ', bg=TX if 'DUPE' in label else ACC)
        self.lbl_send.config(text=text or '(listening...)')
        self.lbl_means.config(text=('MEANS> ' + PL.translate(text, self.cfg['call'])) if text else
                              'Tune across the band and watch the text - when someone calls CQ their call '
                              'appears here. Then press Enter.')
        self.lbl_dupe.config(text='DUPE!' if s.is_dupe(self.app.freq) else '')
        self._stats()

    def _stats(self):
        s = self.session
        n = len(s.qsos)
        bands = {}
        for q in s.qsos:
            bands[band(q['freq'])] = bands.get(band(q['freq']), 0) + 1
        bl = '  '.join(f'{b}:{c}' for b, c in sorted(bands.items())) or '-'
        self.lbl_stats.config(text=f'{s.t["title"]}\n\nQSOs:      {n}\nRate 10m:  {s.rate(10)}/hr\n'
                                   f'Rate 60m:  {s.rate(60)}/hr\nBands:     {bl}\n\nMode: '
                                   f'{"RUN" if s.mode == "run" else "S&P"}')

    def _fill_log(self):
        self.log.delete(*self.log.get_children())
        for q in self.session.qsos:
            self._log_row(q)

    def _log_row(self, q):
        self.log.insert('', 0, values=(q['utc'], band(q['freq']), q['call'], self.session.sent_str(q),
                                       self.session.rcvd_str(q)))

    def _tick(self):
        if self.session:
            self._stats()
        self.app.root.after(5000, self._tick)

    # ---------- actions ----------
    def esm(self):
        s = self.session
        s.freq = self.app.freq
        label, text, logs = s.next_action()
        if not text:
            self.app.set_status('Nothing to send yet - wait for a CQ or type their call')
            return
        if not self.app.send(text):
            return
        s.sent(label)
        if logs:
            self._log_current()
        self.refresh()

    def _log_current(self):
        s = self.session
        q = s.log(self.app.freq)
        r = q['rcvd']
        rst_rcvd = r.get('rst', '599')
        self.app.log_external(q['call'], '', '599', rst_rcvd, r.get('name', ''), r.get('spc', ''),
                              extra=s.adif_extra(q))
        self._log_row(q)
        for v in self.vars.values():
            v.set('')
        self.typed.clear()
        self.call_entry.focus_set()

    def log_manual(self):
        if not self.session.call:
            self.app.set_status('Type their call first', err=True)
            return
        self._log_current()
        self.refresh()

    def wipe(self):
        self.typed.clear()
        self.session.reset()
        for v in self.vars.values():
            v.set('')
        self.refresh()
        self.call_entry.focus_set()

    def export(self):
        s = self.session
        if s.cid == 'POTA':
            self.app.set_status('POTA uses ADIF - your main log already has SIG=POTA on every park QSO')
            return
        if not s.qsos:
            self.app.set_status('No contest QSOs to export yet', err=True)
            return
        path = filedialog.asksaveasfilename(defaultextension='.log', initialfile=f'{s.my}_{s.cid}.log',
                                            filetypes=[('Cabrillo', '*.log *.cbr'), ('All', '*.*')])
        if path:
            with open(path, 'w', encoding='utf-8') as f:
                f.write(s.cabrillo(operator_name=self.cfg.get('name', '')))
            self.app.set_status(f'Cabrillo saved: {path}')
