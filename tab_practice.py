"""PRACTICE tab - raw mode: key it yourself, see what you sent, get your fist graded."""
import tkinter as tk
from tkinter import ttk

from decoder import CWDecoder
from practice import DRILLS, FistGrader, SerialKeyInput
from rigcontrol import list_com_ports
from ui_theme import BG, PANEL, FG, DIM, ACC, RX, TX, UI, Tip


def ct_title(sc):
    from contest import TEMPLATES
    return TEMPLATES['SST' if sc == 'sst' else 'FD']['title']


class PracticeTab:
    def __init__(self, app, parent):
        self.app, self.cfg = app, app.cfg
        self.grader = FistGrader()
        self.active = False
        self.serial = None
        self.drill_i = 0

        top = tk.Frame(parent, bg=PANEL)
        top.pack(fill='x', pady=(6, 0))
        tk.Label(top, text=' MY FIST ', bg=ACC, fg=BG, font=(UI, -16)).pack(side='left', padx=(0, 8))
        self.v_src = tk.StringVar(value=self.cfg.get('key_input', 'audio'))
        for val, lbl, tip in (
                ('audio', 'Hear my sidetone (radio audio / mic)',
                 'Key your radio as normal. We decode your sidetone from the radio audio input. '
                 'To practice without transmitting, turn break-in (BK-IN) OFF and keep the monitor on - '
                 'or use a code practice oscillator next to a PC microphone.'),
                ('serial', 'Key wired to a COM port',
                 'Most accurate: wire your straight key (or keyer output) between RTS and CTS (or DTR and DSR) '
                 'of a USB-serial adapter.')):
            rb = tk.Radiobutton(top, text=lbl, variable=self.v_src, value=val, bg=PANEL, fg=FG, selectcolor=BG,
                                activebackground=PANEL, font=(UI, -16), command=self._src_changed)
            rb.pack(side='left', padx=6)
            Tip(rb, tip)
        self.v_port = tk.StringVar(value=self.cfg.get('key_input_port', 'COM9'))
        self.cb_port = ttk.Combobox(top, textvariable=self.v_port, width=8,
                                    values=[p for p, _ in list_com_ports()])
        self.cb_port.pack(side='left', padx=4)
        self.v_line = tk.StringVar(value=self.cfg.get('key_input_line', 'CTS'))
        ttk.Combobox(top, textvariable=self.v_line, values=['CTS', 'DSR'], width=5,
                     state='readonly').pack(side='left')

        drill = tk.Frame(parent, bg=BG)
        drill.pack(fill='x', pady=(10, 0))
        tk.Label(drill, text='SEND THIS:', bg=BG, fg=DIM, font=(UI, -16)).pack(anchor='w')
        self.lbl_target = tk.Label(drill, text='', bg=BG, fg=ACC, font=(UI, -32), anchor='w')
        self.lbl_target.pack(fill='x')
        btns = tk.Frame(drill, bg=BG)
        btns.pack(fill='x', pady=4)
        self.b_start = ttk.Button(btns, text='START', style='Send.TButton', command=self.toggle)
        self.b_start.pack(side='left')
        ttk.Button(btns, text='NEXT DRILL', command=self.next_drill).pack(side='left', padx=6)
        ttk.Button(btns, text='CLEAR', command=self.clear).pack(side='left')
        self.lbl_hint = tk.Label(btns, text='', bg=BG, fg=DIM, font=(UI, -16))
        self.lbl_hint.pack(side='left', padx=10)

        tk.Label(parent, text='YOU SENT (what a decoder hears):', bg=BG, fg=DIM, font=(UI, -16),
                 anchor='w').pack(fill='x', pady=(8, 0))
        self.sent = tk.Text(parent, bg='#000000', fg=RX, font=(UI, -24), height=3, wrap='word', relief='flat',
                            padx=8, highlightthickness=1, highlightbackground=DIM)
        self.sent.pack(fill='x')
        self.sent.bind('<Key>', lambda e: 'break')

        res = tk.Frame(parent, bg=BG)
        res.pack(fill='both', expand=True, pady=(8, 0))
        self._build_sim(parent)
        sc = tk.Frame(res, bg=PANEL, padx=16, pady=8)
        sc.pack(side='left', fill='y')
        tk.Label(sc, text='FIST SCORE', bg=PANEL, fg=DIM, font=(UI, -16)).pack()
        self.lbl_score = tk.Label(sc, text='--', bg=PANEL, fg=ACC, font=(UI, -48), width=4)
        self.lbl_score.pack()
        self.lbl_acc = tk.Label(sc, text='', bg=PANEL, fg=FG, font=(UI, -16))
        self.lbl_acc.pack()
        self.lbl_stats = tk.Label(res, text='', bg='#000000', fg=FG, font=(UI, -16), justify='left', anchor='nw',
                                  padx=10, pady=8, highlightthickness=1, highlightbackground=DIM, width=48)
        self.lbl_stats.pack(side='left', fill='both', padx=8)
        Tip(self.lbl_stats, 'Standard Morse timing: a dah is 3 dits long; the gap inside a letter is 1 dit, '
                            'between letters 3 dits, between words 7 dits.')
        self.lbl_tips = tk.Label(res, text='', bg=BG, fg=ACC, font=(UI, -16), justify='left', anchor='nw',
                                 wraplength=420)
        self.lbl_tips.pack(side='left', fill='both', expand=True)
        self._show_drill()
        self._src_changed()
        app.root.after(700, self._tick)

    # ---------- simulator ----------
    def _build_sim(self, parent):
        from simulator import Simulator
        f = tk.Frame(parent, bg=PANEL)
        f.pack(fill='x', side='bottom', pady=(8, 0))
        tk.Label(f, text=' PRACTICE QSO (SIMULATOR) ', bg=ACC, fg=BG, font=(UI, -16)).grid(row=0, column=0,
                                                                                         columnspan=8, sticky='w')
        tk.Label(f, text='Simulated stations answer you through your speakers - make full contacts in the QSO, '
                         'CHAT and CONTEST tabs without going on the air.', bg=PANEL, fg=DIM,
                 font=(UI, -16)).grid(row=1, column=0, columnspan=8, sticky='w', padx=6)
        c = self.cfg
        self.sc_map = Simulator.SCENARIOS
        self.v_sc = tk.StringVar(value=self.sc_map.get(c['sim_scenario'], self.sc_map['answer']))
        ttk.Combobox(f, textvariable=self.v_sc, values=list(self.sc_map.values()), width=40,
                     state='readonly').grid(row=2, column=0, padx=6, pady=6, sticky='w')
        self.v_swpm = tk.IntVar(value=c['sim_wpm'])
        tk.Label(f, text='their WPM', bg=PANEL, fg=FG, font=(UI, -16)).grid(row=2, column=1)
        tk.Spinbox(f, from_=5, to=40, width=3, textvariable=self.v_swpm, bg=BG, fg=FG,
                   buttonbackground=BG, relief='flat').grid(row=2, column=2)
        self.v_sig = tk.StringVar(value={20: 'strong', 12: 'medium', 7: 'weak'}.get(int(c['sim_snr']), 'medium'))
        tk.Label(f, text='signal', bg=PANEL, fg=FG, font=(UI, -16)).grid(row=2, column=3, padx=(10, 2))
        ttk.Combobox(f, textvariable=self.v_sig, values=['strong', 'medium', 'weak'], width=7,
                     state='readonly').grid(row=2, column=4)
        self.v_qsb = tk.BooleanVar(value=c['sim_qsb'])
        self.v_qrn = tk.BooleanVar(value=c['sim_qrn'])
        ttk.Checkbutton(f, text='fading (QSB)', variable=self.v_qsb).grid(row=2, column=5, padx=8)
        ttk.Checkbutton(f, text='static (QRN)', variable=self.v_qrn).grid(row=2, column=6)
        self.b_sim = ttk.Button(f, text='START SIM', style='Send.TButton', command=self.toggle_sim)
        self.b_sim.grid(row=2, column=7, padx=8)

    def toggle_sim(self):
        on = not self.app.v_sim.get()
        if on:
            sc = next(k for k, v in self.sc_map.items() if v == self.v_sc.get())
            self.cfg.update(sim_scenario=sc, sim_wpm=int(self.v_swpm.get()),
                            sim_snr={'strong': 20, 'medium': 12, 'weak': 7}[self.v_sig.get()],
                            sim_qsb=self.v_qsb.get(), sim_qrn=self.v_qrn.get())
        self.app.set_sim(on)
        if on:
            tab = {'sst': 2, 'fd': 2}.get(self.cfg['sim_scenario'], 0)
            if tab == 2:
                self.app.ensure_tab('contest')
                ct = self.app.tabs['contest']
                ct.v_cid.set(ct_title(self.cfg['sim_scenario']))
                ct.v_mode.set('run')
                ct.new_session()
            self.app.nb.select(tab)

    def sim_changed(self, on):
        self.b_sim.config(text='STOP SIM' if on else 'START SIM', style='Stop.TButton' if on else 'Send.TButton')

    # ---------- drills ----------
    def _drill_text(self):
        c = self.cfg
        return DRILLS[self.drill_i % len(DRILLS)].format(my=c['call'], name=(c.get('name') or 'OP').split()[0],
                                                         qth=c.get('qth') or 'HOME')

    def _show_drill(self):
        self.lbl_target.config(text=self._drill_text())

    def next_drill(self):
        self.drill_i += 1
        self._show_drill()
        self.clear()

    def clear(self):
        self.grader.reset()
        self.sent.delete('1.0', 'end')
        self.lbl_score.config(text='--')
        self.lbl_acc.config(text='')
        self.lbl_stats.config(text='')
        self.lbl_tips.config(text='')

    # ---------- start / stop ----------
    def toggle(self):
        if self.active:
            self.active = False
            self.stop_serial()
            self.b_start.config(text='START')
            self.lbl_hint.config(text='stopped')
            return
        self.clear()
        self.active = True
        self.b_start.config(text='STOP')
        self.cfg.update(key_input=self.v_src.get(), key_input_port=self.v_port.get(),
                        key_input_line=self.v_line.get())
        if self.v_src.get() == 'serial':
            try:
                self.pd = CWDecoder(fs=48000, auto_tone=False, on_element=self.grader.element, ride_dips=False)
                self.pd._warm = 0
                self.pd.noise, self.pd.peak = 0.01, 1.0
                self.serial = SerialKeyInput(self.v_port.get(), self.v_line.get(), on_level=self._level)
                self.serial.start()
                self.lbl_hint.config(text=f'listening to your key on {self.v_port.get()} {self.v_line.get()}...')
            except Exception as e:
                self.active = False
                self.b_start.config(text='START')
                self.app.set_status(f'Key input: {e}', err=True)
        else:
            self.lbl_hint.config(text='go ahead - send the drill (break-in OFF to practice off-air)')

    def _src_changed(self):
        self.cb_port.config(state='normal' if self.v_src.get() == 'serial' else 'disabled')

    def stop_serial(self):
        if self.serial:
            self.serial.stop()
            self.serial = None

    def _level(self, lv):            # serial thread
        out = []
        self.pd._step(lv, out)
        if out:
            self.app.ui_q.put(('practice', ''.join(out)))

    # ---------- app hooks ----------
    def on_show(self):
        pass

    def on_config(self):
        self._show_drill()

    def on_rx(self, chunk, snr):
        if self.active and self.v_src.get() == 'audio' and self.app.current_tab() == 'practice':
            self.on_text(chunk)

    def on_text(self, s):
        self.grader.add_text(s)
        self.sent.insert('end', s)
        self.sent.see('end')

    def element(self, kind, ms, level):          # called from the engine's audio thread
        if self.active and self.v_src.get() == 'audio' and self.app.current_tab_cached == 'practice':
            self.grader.element(kind, ms, level)

    # ---------- results ----------
    def _tick(self):
        if self.active:
            score, tips, s = self.grader.grade()
            if s:
                col = RX if score >= 85 else ACC if score >= 65 else TX
                self.lbl_score.config(text=str(score), fg=col)
                f = lambda v, ideal: f'{v:.1f}  (ideal {ideal})' if v else '-'
                self.lbl_stats.config(text=(
                    f'Speed:              {s["wpm"]:.0f} WPM\n'
                    f'Dah length:         {f(s["ratio"], "3.0")} x dit\n'
                    f'Gap inside letter:  {f(s["intra"], "1.0")} x dit\n'
                    f'Gap between letters:{f(s["char"], "3.0")} x dit\n'
                    f'Gap between words:  {f(s["word"], "7.0")} x dit\n'
                    f'Steadiness:         {max(0, 100 - 100 * max(s["dit_cv"], s["dah_cv"])):.0f}%\n'
                    f'Elements measured:  {s["n"]}'))
                self.lbl_tips.config(text='\n\n'.join('> ' + t for t in tips))
            txt = self.sent.get('1.0', 'end')
            if txt.strip():
                self.lbl_acc.config(text=f'copy match {self.grader.accuracy(txt, self._drill_text())}%')
        self.app.root.after(700, self._tick)
