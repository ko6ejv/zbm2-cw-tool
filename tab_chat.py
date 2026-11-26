"""CHAT tab - "ZBM2 Online": AOL/Yahoo-chat style keyboard-to-keyboard CW rag-chew."""
import datetime as dt
import threading
import time
import tkinter as tk
from tkinter import ttk

import plain as PL
import qso as Q
from chat import TECH_SPOTS, quick_fills
from endings import Ender
from ui_theme import BG, PANEL, FG, DIM, ACC, RX, TX, UI, Tip


class ChatTab:
    def __init__(self, app, parent):
        self.app, self.cfg = app, app.cfg
        self.room = app.eng.room           # lives in the engine: collects stations even before this tab exists
        self.sent_words = []          # words already on the air this over
        self.my_open = False
        self.my_text = ''
        self.last_cq = 0.0
        self.key_down_t = 0.0

        # --- room bar ---
        bar = tk.Frame(parent, bg=PANEL)
        bar.pack(fill='x', pady=(6, 0))
        self.lbl_room = tk.Label(bar, text=' ROOM ', bg=ACC, fg=BG, font=(UI, -24), padx=6)
        self.lbl_room.pack(side='left')
        self.lbl_members = tk.Label(bar, text='', bg=PANEL, fg=DIM, font=(UI, -16), padx=8)
        self.lbl_members.pack(side='left')
        self.mb = tk.Menubutton(bar, text='WHERE DO I GO?', bg=BG, fg=FG, font=(UI, -16), relief='solid',
                                bd=2, activebackground='#2a1a00', activeforeground=FG)
        menu = tk.Menu(self.mb, tearoff=0, bg=BG, fg=FG, font=(UI, -16), activebackground=ACC,
                       activeforeground=BG)
        for lbl, hz, why in TECH_SPOTS:
            menu.add_command(label=f'{lbl} MHz - {why}', command=lambda h=hz: self.go_to(h))
        self.mb['menu'] = menu
        self.mb.pack(side='right', padx=6, pady=4)
        Tip(self.mb, 'Popular CW spots inside the Technician CW segments. Picks the frequency on your radio '
                     '(needs CAT) and sets CW mode.')
        self.v_secs = tk.IntVar(value=self.cfg.get('auto_cq_secs', 45))
        tk.Label(bar, text='s', bg=PANEL, fg=DIM, font=(UI, -16)).pack(side='right', padx=(2, 8))
        tk.Spinbox(bar, from_=20, to=300, increment=5, width=4, textvariable=self.v_secs, bg=PANEL, fg=FG,
                   buttonbackground=BG, relief='flat').pack(side='right')
        self.v_auto = tk.BooleanVar(value=False)
        cb = ttk.Checkbutton(bar, text='AUTO CQ every', variable=self.v_auto)
        cb.pack(side='right')
        Tip(cb, 'Calls CQ for you every N seconds while the frequency is quiet. Stops when someone answers. '
                'You still need to be at the radio.')
        self.v_live = tk.BooleanVar(value=self.cfg.get('live_typing', True))
        lt = ttk.Checkbutton(bar, text='Live typing', variable=self.v_live)
        lt.pack(side='right', padx=8)
        Tip(lt, 'Words go out as you type them (after each space). Backspace works until a word is sent. '
                'Off: nothing goes out until you press Enter.')

        self.lbl_sending = tk.Label(parent, text='', bg=BG, fg=ACC, font=(UI, -16), anchor='w')
        self.lbl_sending.pack(fill='x')

        body = tk.Frame(parent, bg=BG)
        body.pack(fill='both', expand=True)
        side = tk.Frame(body, bg=BG, width=300)
        side.pack(side='right', fill='y', padx=(8, 0))
        side.pack_propagate(False)
        self.txt = tk.Text(body, bg='#000000', fg=FG, wrap='word', font=(UI, -16), relief='flat', padx=8,
                           pady=6, highlightthickness=2, highlightbackground=DIM, height=10)
        self.txt.pack(side='left', fill='both', expand=True)
        self.txt.tag_configure('time', foreground=DIM)
        self.txt.tag_configure('them', foreground=RX, font=(UI, -24))
        self.txt.tag_configure('who_them', foreground=BG, background=RX)
        self.txt.tag_configure('me', foreground=TX, font=(UI, -24))
        self.txt.tag_configure('who_me', foreground=BG, background=TX)
        self.txt.tag_configure('plain', foreground=DIM, lmargin1=40, lmargin2=40)
        self.txt.tag_configure('sys', foreground=DIM, justify='center')
        self.txt.bind('<Key>', lambda e: 'break')

        # buddy list
        tk.Label(side, text=' BUDDY LIST ', bg=ACC, fg=BG, font=(UI, -16)).pack(anchor='w')
        st = ttk.Style()
        st.configure('Buddy.Treeview', background='#000000', fieldbackground='#000000', foreground=FG,
                     font=(UI, -16), rowheight=20, bordercolor=DIM)
        st.configure('Buddy.Treeview.Heading', background=PANEL, foreground=DIM, font=(UI, -16))
        st.map('Buddy.Treeview', background=[('selected', ACC)], foreground=[('selected', BG)])
        self.tree = ttk.Treeview(side, columns=('heard', 'snr', 'w'), show='tree headings', height=5,
                                 style='Buddy.Treeview')
        self.tree.heading('#0', text='CALL')
        self.tree.heading('heard', text='HEARD')
        self.tree.heading('snr', text='S/N')
        self.tree.heading('w', text='')
        self.tree.column('#0', width=110)
        self.tree.column('heard', width=60, anchor='e')
        self.tree.column('snr', width=50, anchor='e')
        self.tree.column('w', width=40, anchor='center')
        self.tree.pack(fill='x')
        self.tree.bind('<<TreeviewSelect>>', lambda e: self.select_buddy())
        Tip(self.tree, 'Stations heard on this frequency. * = worked before, + = confirmed on LoTW. '
                       'Click one to make them your chat partner.')
        # profile card
        tk.Label(side, text=' PROFILE ', bg=ACC, fg=BG, font=(UI, -16)).pack(anchor='w', pady=(8, 0))
        self.prof = tk.Label(side, text='Click a station in the buddy list.', bg='#000000', fg=FG,
                             font=(UI, -16), justify='left', anchor='nw', wraplength=280, padx=6, pady=6,
                             highlightthickness=1, highlightbackground=DIM)
        self.prof.pack(fill='both', expand=True)
        ttk.Button(side, text='LOG THIS CHAT', command=self.log_chat).pack(fill='x', pady=(6, 0))

        # quick fills
        self.qf = tk.Frame(parent, bg=BG)
        self.qf.pack(fill='x', pady=(6, 0))
        self._build_quickfills()
        # input
        inp = tk.Frame(parent, bg=PANEL)
        inp.pack(fill='x', pady=(4, 0))
        tk.Label(inp, text='SAY>', bg=PANEL, fg=ACC, font=(UI, -16)).pack(side='left', padx=6)
        self.lbl_onair = tk.Label(inp, text='', bg=TX, fg=BG, font=(UI, -16))
        self.lbl_onair.pack(side='left')
        self.v_in = tk.StringVar()
        self.ent = tk.Entry(inp, textvariable=self.v_in, bg='#000000', fg=FG, insertbackground=FG,
                            font=(UI, -24), relief='flat')
        self.ent.pack(side='left', fill='x', expand=True, padx=4, pady=6, ipady=4)
        self.ent.bind('<KeyRelease>', lambda e: self._typed(user=True))
        self.ent.bind('<Return>', lambda e: self.send_over())
        self.ent.bind('<Shift-Return>', lambda e: (self.send_over(keep_over=True), 'break')[1])
        ttk.Button(inp, text='SEND  Enter', style='Send.TButton', command=self.send_over).pack(side='left', padx=4)
        ttk.Button(inp, text='STOP  Esc', style='Stop.TButton', command=app.stop_tx).pack(side='left', padx=(0, 6))
        self.lbl_prev = tk.Label(parent, text='', bg=BG, fg=DIM, font=(UI, -16), anchor='w', justify='left',
                                 wraplength=1100)
        self.lbl_prev.pack(fill='x')
        # keep the input rows when the window is short (uConsole): squeeze the chat log instead
        self.lbl_prev.pack_configure(side='bottom', before=body)
        inp.pack_configure(side='bottom', before=body)
        self.qf.pack_configure(side='bottom', before=body)
        self.sys('Welcome to ZBM2 Online. Tune to a CW frequency (WHERE DO I GO?) and watch the room fill up.')
        for t, who, seg in app.eng.room_log[-30:]:         # what was said before this tab was opened
            self.bubble(who, seg)
        for call in list(self.room.buddies):
            self._upsert(call)
        app.root.after(2000, self._tick)

    # ---------- helpers ----------
    def _build_quickfills(self):
        for w in self.qf.winfo_children():
            w.destroy()
        tk.Label(self.qf, text='QUICK:', bg=BG, fg=DIM, font=(UI, -16)).pack(side='left')
        for lbl, txt in quick_fills(self.cfg):
            b = tk.Label(self.qf, text=lbl, bg=ACC, fg=BG, font=(UI, -16), padx=4, cursor='hand2')
            b.pack(side='left', padx=2)
            b.bind('<Button-1>', lambda e, t=txt: self.insert(t))
            Tip(b, f'Adds "{txt}" - {PL.translate(txt, self.cfg["call"])}')

    def insert(self, t):
        cur = self.v_in.get()
        self.v_in.set((cur + ' ' if cur and not cur.endswith(' ') else cur) + t)
        self.ent.icursor('end')
        self.ent.focus_set()
        self._preview()

    def sys(self, s):
        self.txt.insert('end', f'-- {s} --\n', 'sys')
        self.txt.see('end')

    def bubble(self, who, text, mine=False):
        now = dt.datetime.now().strftime('%H:%M')
        self.txt.insert('end', f'[{now}] ', 'time')
        self.txt.insert('end', f' {who} ', 'who_me' if mine else 'who_them')
        self.txt.insert('end', ' ' + text + '\n', 'me' if mine else 'them')
        if self.app.v_plain.get():
            self.txt.insert('end', PL.translate(text, self.cfg['call']).replace('(laughter)', 'lol')
                            + '\n', 'plain')
        self.txt.see('end')

    # ---------- app hooks ----------
    def on_show(self):
        self.ent.focus_set()

    def on_config(self):
        self.room.my = self.cfg['call'].upper()
        self._build_quickfills()

    def f1(self):
        self.send_over()

    def preload(self, reply):
        """Suggest text only - never transmits. Live typing starts with the user's next keystroke."""
        if not self.v_in.get().strip() and not self.sent_words:
            self.v_in.set(reply)
            self.ent.icursor('end')
            self._preview()

    def on_rx(self, chunk, snr):
        pass                                  # the engine feeds the room; we render its events

    def on_event(self, kind, val):
        if kind == 'buddy':
            self._upsert(val)
            self.sys(f'{val} entered the room')
            return
        seg, sender = val
        self.bubble(sender or '???', seg)
        if sender:
            self._upsert(sender)
        mine = self.cfg['call'].upper()
        if mine in seg.split() and sender:
            if self.v_auto.get():
                self.v_auto.set(False)
                self.sys(f'{sender} answered - AUTO CQ off')
            if self.room.partner != sender:
                self.room.partner = sender
                self.app.request_lookup(sender)
        if sender and sender == self.room.partner:
            self._profile(sender)

    def on_key(self, down):
        if down:
            self.key_down_t = time.time()
        live = ' '.join(self.room.live_text().split())[-60:]
        if time.time() - self.key_down_t < 1.5:
            who = self.room.partner or 'Someone'
            self.lbl_sending.config(text=f'  {who} is sending...  {live}')
        else:
            self.lbl_sending.config(text='')

    def on_lookup(self, call, d):
        b = self.room.buddies.get(call)
        if b:
            b.lookup = d
            if call == self.room.partner:
                self._profile(call)

    # ---------- buddy list ----------
    def _upsert(self, call):
        b = self.room.buddies.get(call)
        if not b:
            return
        b.worked = bool(Q.worked_before(self.cfg['log_path'], call))
        b.confirmed = self.app.confirmed(call)
        mark = ('*' if b.worked else '') + ('+' if b.confirmed else '')
        vals = (b.age(), f'{b.snr:.0f}', mark)
        if self.tree.exists(call):
            self.tree.item(call, values=vals)
        else:
            self.tree.insert('', 0, iid=call, text=call, values=vals)
        self.lbl_members.config(text=f'{len(self.room.buddies)} station(s) heard')

    def select_buddy(self):
        sel = self.tree.selection()
        if not sel:
            return
        call = sel[0]
        self.room.partner = call
        self._profile(call)
        self.app.request_lookup(call)
        hist = self.room.history(call, 12)
        if hist:
            self.sys(f'last chat with {call}')
            for line in hist:
                self.txt.insert('end', line + '\n', 'plain')
            self.txt.see('end')
        self._typed()

    def _profile(self, call):
        b = self.room.buddies.get(call)
        if not b:
            return
        f, lk = b.facts, b.lookup
        name = f.get('name') or lk.get('name', '')
        qth = f.get('qth') or lk.get('qth', '')
        grid = f.get('grid') or lk.get('grid', '')
        lines = [call, '']
        if name:
            lines.append(f'Name:  {name.title()}')
        if lk.get('full_name'):
            lines.append(f'       ({lk["full_name"]})')
        if qth:
            lines.append(f'QTH:   {qth.title()}')
        if grid and self.cfg.get('grid'):
            try:
                km, brg = Q.dist_bearing(self.cfg['grid'], grid)
                lines.append(f'Grid:  {grid}  {km * 0.621371:,.0f} mi @ {brg:.0f} deg')
            except Exception:
                lines.append(f'Grid:  {grid}')
        for k, lbl in (('rig', 'Rig'), ('ant', 'Ant'), ('pwr', 'Power'), ('wx', 'WX'), ('age', 'Age')):
            if f.get(k):
                lines.append(f'{lbl + ":":<7}{f[k]}')
        wk = Q.worked_before(self.cfg['log_path'], call)
        lines.append('')
        lines.append(f'Worked before: {len(wk)}x' if wk else 'First contact!')
        if b.confirmed:
            lines.append('Confirmed on LoTW')
        if lk.get('source'):
            lines.append(f'(lookup: {lk["source"]})')
        self.prof.config(text='\n'.join(lines))
        self.lbl_room.config(text=f' ROOM {self.app.freq / 1e6:.3f} MHz | chatting with {call} ')

    def _tick(self):
        for c in list(self.room.buddies):
            if self.tree.exists(c):
                self._upsert(c)
        if not self.room.partner:
            f = self.app.freq
            self.lbl_room.config(text=f' ROOM {f / 1e6:.3f} MHz ' if f else ' ROOM (no CAT - tune your radio) ')
        self._auto_cq()
        self.app.root.after(1000, self._tick)

    # ---------- auto CQ ----------
    def _auto_cq(self):
        if not self.v_auto.get() or self.app.tx.busy.is_set():
            return
        quiet = time.time() - self.key_down_t
        gap = max(20, int(self.v_secs.get() or 45))
        if quiet > 8 and time.time() - self.last_cq > gap:
            full, _ = self.app.ender.finish('CQ')
            if self.app.send(full):
                self.last_cq = time.time()
                self.my_open, self.my_text = False, ''

    # ---------- sending ----------
    def _words(self):
        return self.v_in.get().upper().split()

    def _typed(self, _e=None, user=False):
        txt = self.v_in.get()
        words = txt.upper().split()
        n = len(self.sent_words)
        # words already on the air can't be taken back
        if n and words[:n] != self.sent_words:
            self.v_in.set(' '.join(self.sent_words) + ' ' + ' '.join(w for w in words[n:]))
            self.ent.icursor('end')
            words = self._words()
        if user and self.v_live.get() and txt.endswith(' '):
            new = words[n:]
            if new:
                if not self.sent_words and not self.app.check_tx():
                    return
                for w in new:
                    self.app.eng.stream_word(w)
                self.sent_words = words[:]
        self.lbl_onair.config(text=(' ON AIR: ' + ' '.join(self.sent_words)[-40:] + ' ') if self.sent_words else '')
        self._preview()

    def _preview(self):
        words = self._words()
        if not words:
            self.lbl_prev.config(text='Type your message. Enter adds the right ending + callsigns for you. '
                                      'Shift+Enter keeps your turn going.')
            return
        full, code = self.app.ender.finish(' '.join(words), self.room.partner)
        self.lbl_prev.config(text=f'SENDS> {full}\nMEANS> {PL.translate(full, self.cfg["call"])} - '
                                  f'{Ender.explain(code, self.room.partner)}')

    def send_over(self, keep_over=False):
        words = self._words()
        if not words:
            return
        full, code = self.app.ender.finish(' '.join(words), self.room.partner, keep_over)
        streaming = bool(self.sent_words) or self.app.tx.streaming
        if streaming:
            rest = full.split()[len(self.sent_words):]
            for w in rest:
                self.app.eng.stream_word(w)
            if not keep_over:
                self.app.tx.stream_end()
            self.app.ender.note_sent(full)
        else:
            if not self.app.send(full):
                return
        self.v_in.set('')
        self.sent_words = []
        self.lbl_onair.config(text='')
        self._preview()

    def on_txchar(self, c):
        if not self.my_open:
            now = dt.datetime.now().strftime('%H:%M')
            self.txt.insert('end', f'[{now}] ', 'time')
            self.txt.insert('end', ' YOU ', 'who_me')
            self.txt.insert('end', ' ', 'me')
            self.my_open, self.my_text = True, ''
        self.txt.insert('end', c, 'me')
        self.my_text += c
        self.txt.see('end')

    def on_txdone(self):
        if self.my_open:
            self.txt.insert('end', '\n', 'me')
            text = ' '.join(self.my_text.split())
            if self.app.v_plain.get():
                self.txt.insert('end', PL.translate(text, self.cfg['call']) + '\n', 'plain')
            self.room.log_line(self.room.partner, 'YOU', text)
        self.my_open, self.my_text = False, ''
        self.txt.see('end')

    # ---------- misc ----------
    def go_to(self, hz):
        rig = self.app.rig

        def job():
            try:
                ok = rig.set_freq(hz)
                rig.set_cw_mode()
                msg = f'Tuned to {hz / 1e6:.3f} MHz CW' if ok else f'Tune your radio to {hz / 1e6:.3f} MHz (CW)'
            except Exception:
                msg = f'Tune your radio to {hz / 1e6:.3f} MHz (CW)'
            self.app.ui_q.put(('online_msg', msg))
        threading.Thread(target=job, daemon=True).start()
        self.sys(f'heading to {hz / 1e6:.3f} MHz - listen first, send QRL? before CQ')

    def log_chat(self):
        call = self.room.partner
        if not call:
            self.app.set_status('Pick your chat partner in the buddy list first', err=True)
            return
        b = self.room.buddies.get(call)
        f, lk = (b.facts, b.lookup) if b else ({}, {})
        self.app.log_external(call, f.get('grid') or lk.get('grid', ''), Q.rst_from_snr(b.snr if b else 20),
                              '', f.get('name') or lk.get('name', ''), f.get('qth') or lk.get('qth', ''))
        self.sys(f'logged chat with {call}')
        self._upsert(call)
