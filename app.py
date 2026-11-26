"""ZBM2 CW Tool - decode CW, match the other op's speed, answer for you.
(c) ZBM2 Industries.
"""
import os
import queue
import re
import threading
import tkinter as tk

import numpy as np
from tkinter import ttk, messagebox

import rigcontrol as RC
import qso as Q
import plain as PL
import online as ON
from engine import Engine, dev_labels, find_dev, find_dev_like
from alerts import missing_fields_alert, license_block_alert
from endings import Ender
from qcodes import define as qdefine

from config import __version__, RES, load_cfg, save_cfg
from ui_theme import (BG, PANEL, FG, DIM, ACC, RX, TX, UI, PIX, load_fonts, apply_theme,
                      Tip, HELP)


PROFILES = {
    'usbrig': dict(
        title='My radio has a USB cable built in',
        sub='Yaesu FTX-1, FT-991A, FT-710, FTDX10, Icom IC-7300 / IC-705, Kenwood TS-590...\n'
            'One USB cable carries audio and control. The radio\'s own keyer sends the CW.',
        cfg=dict(cat_backend='hamlib_launch', key_method='rigkeyer', ptt_method='VOX',
                 hamlib_ptt='RIG', audio_in_hint=['FTX', 'USB Audio CODEC'], ptt_lead_ms=50, ptt_tail_ms=50)),
    'rigctld': dict(
        title='I already run rigctld (or use OmniRig)',
        sub='You run Hamlib rigctld for Winlink / WSJT-X / N1MM. We share it if it\'s running,\n'
            'and start it ourselves (with your radio, COM port and baud) if it isn\'t.',
        cfg=dict(cat_backend='hamlib_existing', key_method='rigkeyer', ptt_method='VOX',
                 audio_in_hint=['FTX', 'USB Audio CODEC'], ptt_lead_ms=50, ptt_tail_ms=50)),
    'digirig_hf': dict(
        title='DigiRig + HF radio (FT-857, FT-817/818, FT-891, IC-718...)',
        sub='DigiRig sound card + DigiRig COM port. CW is sent as clean audio tones with the\n'
            'radio in DATA / USB mode. PTT goes through the DigiRig (RTS).',
        cfg=dict(cat_backend='hamlib_launch', key_method='audio', ptt_method='CAT',
                 hamlib_ptt='RTS', digirig=True, ptt_lead_ms=60, ptt_tail_ms=60, tx_drive=40)),
    'digirig_ht': dict(
        title='DigiRig + BTECH UV handheld (UV-5R, UV-25X, UV-50X, GMRS-V2...)',
        sub='No CW mode on an HT - we send tone CW over FM ("MCW"). No CAT needed.\n'
            'PTT goes through the DigiRig (RTS). Use VHF/UHF ham frequencies only.',
        cfg=dict(cat_backend='none', key_method='audio', ptt_method='RTS', digirig=True,
                 ptt_lead_ms=350, ptt_tail_ms=150, tx_drive=30, tone=800, auto_tone=True,
                 min_wpm=5, max_wpm=25)),
}


def apply_profile(cfg, key):
    p = dict(PROFILES[key]['cfg'])
    cfg['profile'] = key
    hint = p.pop('audio_in_hint', None)
    if p.pop('digirig', False):
        com, snd = RC.find_digirig()
        if com:
            cfg['cat_port'] = cfg['ptt_port'] = cfg['key_port'] = com
        hint = snd
    if hint:
        i, o = find_dev_like(hint, 'input'), find_dev_like(hint, 'output')
        if i:
            cfg['audio_in'] = i
        if o:
            cfg['audio_out'] = o
    cfg.update(p)
    return cfg


class App:
    """The screens. All radio work happens in Engine (engine.py); this class only renders its events."""
    # engine-owned state, exposed for the tabs / dialogs
    tx = property(lambda s: s.eng.tx)
    rig = property(lambda s: s.eng.rig)
    freq = property(lambda s: s.eng.freq)
    mode = property(lambda s: s.eng.mode)
    rig_ok = property(lambda s: s.eng.rig_ok)
    rig_err = property(lambda s: s.eng.rig_err)
    decoder = property(lambda s: s.eng.decoder)
    fs = property(lambda s: s.eng.fs)
    qso = property(lambda s: s.eng.qso)
    gt = property(lambda s: s.eng.gt)
    ender = property(lambda s: s.eng.ender)
    earcons = property(lambda s: s.eng.earcons)
    rec = property(lambda s: s.eng.rec)
    data_dir = property(lambda s: s.eng.data_dir)
    lotw_conf = property(lambda s: s.eng.lotw_conf)
    tx_owner = property(lambda s: s.eng.tx_owner)

    def __init__(self, root, splash=None):
        self.root = root
        self.cfg = load_cfg()
        self.ui_q = queue.Queue()
        self.eng = Engine(self.cfg, lambda k, v=None: self.ui_q.put((k, v)))
        self.suggest_dirty = False
        self.last_gt_msg = ''
        self.user_edit = set()          # side-panel fields the operator typed in
        self.alert = None
        self.alert_codes = set()
        self.tabs = {}
        self.tab_frames = {}
        self.current_tab_cached = 'qso'
        self.skim_rows = []

        self._build_ui()
        if splash:
            splash()
        self.eng.start()
        self._sync_engine_vars()
        self.root.after(30, self._poll)
        if not self.cfg.get('setup_done'):
            self.root.after(400, lambda: SetupWizard(self))

    # ================= UI =================
    def _build_ui(self):
        r = self.root
        r.title(f'ZBM2 CW Tool {__version__}')
        r.configure(bg=BG)
        # compact layout for small screens (uConsole 1280x720, netbooks): cfg layout = auto | compact | full
        lay = self.cfg.get('layout', 'auto')
        self.compact = lay == 'compact' or (lay == 'auto' and r.winfo_screenheight() <= 800)
        C = self.compact
        if C:
            r.geometry(f'{min(1280, r.winfo_screenwidth())}x{min(720, r.winfo_screenheight()) - 40}+0+0')
            r.minsize(800, 480)
            try:
                r.attributes('-zoomed', True)          # X11: fill the screen
            except tk.TclError:
                pass
        else:
            r.geometry('1320x900')
            r.minsize(1150, 760)
        try:
            self._icon = tk.PhotoImage(file=os.path.join(RES, 'icon.png'))
            r.iconphoto(True, self._icon)
            if os.name == 'nt':
                r.iconbitmap(os.path.join(RES, 'icon.ico'))
        except tk.TclError:
            pass

        apply_theme(r)

        # --- header ---
        hdr = tk.Frame(r, bg=BG)
        hdr.pack(fill='x', padx=10, pady=(4, 2) if C else (8, 4))
        if C:
            ttk.Style().configure('Big.TLabel', font=(UI, -30))
        try:
            self._logo = tk.PhotoImage(file=os.path.join(RES, 'logo_small.png' if C else 'logo.png'))
            tk.Label(hdr, image=self._logo, bg=BG).pack(side='left')
        except tk.TclError:
            pass
        stats = tk.Frame(hdr, bg=BG)
        stats.pack(side='right', anchor='n')
        self._stats_frame = stats
        self.lbl_freq = ttk.Label(stats, text='--.---.---', style='Big.TLabel')
        self.lbl_freq.grid(row=0, column=0, columnspan=3, sticky='e')
        self.lbl_rig = ttk.Label(stats, text='rig: --', style='Dim.TLabel')
        self.lbl_rig.grid(row=1, column=0, columnspan=3, sticky='e')
        ttk.Button(stats, text='Setup Wizard', command=lambda: SetupWizard(self)).grid(row=2, column=0, padx=2, pady=2)
        ttk.Button(stats, text='Set rig mode', command=self.set_cw_mode).grid(row=2, column=1, padx=2, pady=2)
        ttk.Button(stats, text='Rig Setup', command=self.open_settings).grid(row=2, column=2, padx=2, pady=2)
        ttk.Button(stats, text='HELP', command=lambda: HelpWindow(self.root)).grid(row=2, column=3, padx=2, pady=2)
        tframe = tk.Frame(hdr, bg=BG)
        tframe.pack(side='left', padx=10, fill='x', expand=True)
        tk.Label(tframe, text=' ZBM2 CW TOOL ', bg=ACC, fg=BG, font=(PIX, -18 if C else -32)).pack(
            anchor='w', pady=(2, 2) if C else (4, 6))
        self.lbl_station = tk.Label(tframe, bg=BG, fg=DIM, font=(UI, -13 if C else -16), justify='left',
                                    wraplength=520)
        self.lbl_station.pack(anchor='w')

        # --- RX meter strip ---
        meter = tk.Frame(r, bg=PANEL)
        meter.pack(fill='x', padx=10, pady=2 if C else 4)
        self.v_sidetone = tk.BooleanVar(value=self.cfg['sidetone'])
        self.v_plain_cb = tk.BooleanVar(value=self.cfg.get('plain_english', True))
        vt = tk.Frame(self._stats_frame, bg=BG)
        vt.grid(row=3, column=0, columnspan=3, sticky='e')
        ttk.Checkbutton(vt, text='Plain English', variable=self.v_plain_cb,
                        command=self._toggle_plain).pack(side='left', padx=6)
        ttk.Checkbutton(vt, text='Sidetone', variable=self.v_sidetone, command=self._save_quick).pack(side='left')
        self.v_sim = tk.BooleanVar(value=False)
        self.sim_badge = tk.Label(vt, text=' SIM ', bg=BG, fg=DIM, font=(UI, -16), cursor='hand2', padx=4)
        self.sim_badge.pack(side='left', padx=(10, 0))
        self.sim_badge.bind('<Button-1>', lambda e: self.set_sim(not self.v_sim.get()))
        Tip(self.sim_badge, 'SIMULATOR: practice stations answer you through your speakers - nothing goes on the '
                            'air. Works in every tab. Set it up in PRACTICE > Practice QSO.')
        l_rx = tk.Label(meter, text='THEIR SPEED', bg=PANEL, fg=DIM, font=(UI, -16))
        l_rx.pack(side='left', padx=(10, 4))
        Tip(l_rx, HELP['rx_wpm'])
        self.lbl_rxwpm = tk.Label(meter, text='--', bg=PANEL, fg=RX, font=(UI, -22 if C else -32), width=4)
        self.lbl_rxwpm.pack(side='left')
        l_tx = tk.Label(meter, text='YOUR SPEED', bg=PANEL, fg=DIM, font=(UI, -16))
        l_tx.pack(side='left', padx=(16, 4))
        Tip(l_tx, HELP['tx_wpm'])
        self.lbl_txwpm = tk.Label(meter, text='--', bg=PANEL, fg=TX, font=(UI, -22 if C else -32), width=4)
        self.lbl_txwpm.pack(side='left')
        self.v_match = tk.BooleanVar(value=self.cfg['match_wpm'])
        ttk.Checkbutton(meter, text='Match their speed', variable=self.v_match,
                        command=self._save_quick).pack(side='left', padx=8)
        l_off = tk.Label(meter, text='+/-', bg=PANEL, fg=DIM, font=(UI, -16))
        l_off.pack(side='left')
        Tip(l_off, HELP['offset'])
        self.v_off = tk.IntVar(value=self.cfg['wpm_offset'])
        tk.Spinbox(meter, from_=-15, to=10, width=4, textvariable=self.v_off, command=self._save_quick,
                   bg=PANEL, fg=FG, buttonbackground=BG, relief='flat').pack(side='left', padx=(2, 8))
        l_man = tk.Label(meter, text='fixed WPM', bg=PANEL, fg=DIM, font=(UI, -16))
        l_man.pack(side='left')
        Tip(l_man, HELP['manual'])
        self.v_manual = tk.IntVar(value=self.cfg['manual_wpm'])
        tk.Spinbox(meter, from_=5, to=50, width=4, textvariable=self.v_manual, command=self._save_quick,
                   bg=PANEL, fg=FG, buttonbackground=BG, relief='flat').pack(side='left', padx=(2, 12))

        # TURBO: computer-to-computer contacts can go faster than "match their speed"
        self.v_turbo = tk.BooleanVar(value=self.cfg.get('turbo', False))
        tb = ttk.Checkbutton(meter, text='TURBO', variable=self.v_turbo, command=self._save_quick)
        tb.pack(side='left', padx=(12, 2))
        Tip(tb, 'For program-to-program contacts: send at the TURBO speed instead of matching theirs. '
                'We suggest it when their timing looks machine-perfect (QRQ = "send faster").')
        self.v_turbo_wpm = tk.IntVar(value=self.cfg.get('turbo_wpm', 35))
        tk.Spinbox(meter, from_=20, to=60, width=3, textvariable=self.v_turbo_wpm, command=self._save_quick,
                   bg=PANEL, fg=FG, buttonbackground=BG, relief='flat').pack(side='left')
        self.snr_canvas = tk.Canvas(meter, width=120, height=18, bg=BG, highlightthickness=0)
        self.snr_canvas.pack(side='left', padx=10)
        Tip(self.snr_canvas, HELP['snr'])
        self.lbl_snr = tk.Label(meter, text='', bg=PANEL, fg=DIM, width=11)
        self.lbl_snr.pack(side='left')
        self.lbl_conf = tk.Label(meter, text='COPY: --', bg=PANEL, fg=DIM, font=(UI, -16), width=12)
        self.lbl_conf.pack(side='left', padx=(6, 0))
        Tip(self.lbl_conf, 'How well we are copying them: GOOD / SHAKY / LOST. Based on signal-to-noise '
                           'and how many characters we could not read.')

        # --- main area: spectrum + alert banner (shared) + tabs ---
        main = tk.Frame(r, bg=BG)
        main.pack(fill='both', expand=True, padx=10, pady=2 if C else 4)
        specf = tk.Frame(main, bg=PANEL)
        specf.pack(fill='x', pady=(0, 3 if C else 6))
        self.spec = tk.Canvas(specf, height=58 if C else 80, bg='#000000', highlightthickness=0, cursor='crosshair')
        self.spec.pack(side='left', fill='x', expand=True)
        self.spec.bind('<Button-1>', self._spec_click)
        lv = tk.Frame(specf, bg=PANEL)
        lv.pack(side='left', fill='y', padx=6)
        tk.Label(lv, text='SPAN', bg=PANEL, fg=DIM, font=(UI, -16)).pack()
        self.v_span = tk.StringVar(value=str(self.cfg.get('spec_span', 600)))
        sp = ttk.Combobox(lv, textvariable=self.v_span, values=['300', '600', '1000', '1500'],
                          width=5, state='readonly')
        sp.pack(pady=(0, 4))
        sp.bind('<<ComboboxSelected>>', lambda e: (setattr(self, '_spec_w', None), self._save_quick()))
        l_p = tk.Label(lv, text='TONE Hz', bg=PANEL, fg=DIM, font=(UI, -16))
        l_p.pack()
        Tip(l_p, HELP['pitch'])
        self.v_tone = tk.IntVar(value=int(self.cfg['tone']))
        tf = tk.Frame(lv, bg=PANEL)
        tf.pack()
        tk.Spinbox(tf, from_=300, to=1200, increment=10, width=4, textvariable=self.v_tone,
                   command=self._tone_changed, bg=PANEL, fg=FG, buttonbackground=BG, relief='flat').pack(side='left')
        self.v_auto = tk.BooleanVar(value=self.cfg['auto_tone'])
        ttk.Checkbutton(tf, text='A', variable=self.v_auto, command=self._auto_toggled).pack(side='left')
        tk.Label(lv, text='RX level', bg=PANEL, fg=DIM, font=(UI, -16)).pack()
        self.lvl = tk.Canvas(lv, width=60, height=10, bg='#000000', highlightthickness=0)
        self.lvl.pack()
        gf = tk.Frame(lv, bg=PANEL)             # level readout + software gain share one row
        gf.pack()
        self.lbl_lvl = tk.Label(gf, text='', bg=PANEL, fg=DIM, font=(UI, -16), width=6)
        if C:   # two short rows instead of a tall column: SPAN / TONE / A  and  level / gain
            rows = list(lv.pack_slaves())
            for w_ in rows:
                w_.pack_forget()
            for i, w_ in enumerate(rows[:4]):
                w_.grid(row=0, column=i, padx=2)
            for i, w_ in enumerate(rows[4:]):
                w_.grid(row=1, column=i, padx=2, columnspan=2 if i == 2 else 1)
        self.lbl_lvl.pack(side='left')
        tk.Label(gf, text='+', bg=PANEL, fg=DIM, font=(UI, -14)).pack(side='left')
        self.v_rxgain = tk.IntVar(value=int(self.cfg.get('rx_gain_db', 0)))
        sg = tk.Spinbox(gf, from_=0, to=40, increment=3, width=3, textvariable=self.v_rxgain,
                        command=self._rxgain_changed, bg=PANEL, fg=FG, buttonbackground=BG, relief='flat')
        sg.pack(side='left')
        sg.bind('<Return>', lambda e: self._rxgain_changed())
        sg.bind('<FocusOut>', lambda e: self._rxgain_changed())
        Tip(sg, 'Software RX boost in dB, for radios whose USB audio is too quiet. '
                'Raise it until the RX level reads about -20 dB. Too much shows CLIP!')
        self.flash = tk.Label(specf, text='', bg=ACC, fg=BG, font=(UI, -48), padx=14)
        # DOS alert banner (hidden until something happens)
        self.banner = tk.Frame(main, bg=ACC, highlightthickness=2, highlightbackground=ACC)
        self.b_head = tk.Label(self.banner, text='', bg=ACC, fg=BG, font=(UI, -18 if C else -24), anchor='w', padx=8)
        self.b_head.pack(fill='x')
        self.b_def = tk.Label(self.banner, text='', bg=BG, fg=FG, font=(UI, -16), anchor='w', padx=8,
                              justify='left', wraplength=1100)
        self.b_def.pack(fill='x')
        self.b_plain = tk.Label(self.banner, text='', bg=BG, fg=ACC, font=(UI, -16), anchor='w', padx=8,
                                justify='left', wraplength=1100)
        self.b_plain.pack(fill='x')
        self.b_head.bind('<Button-1>', lambda e: self.clear_banner())
        self.nb = ttk.Notebook(main)
        self.nb.pack(fill='both', expand=True)
        self.nb.bind('<<NotebookTabChanged>>', lambda e: self._tab_changed())
        qso_tab = tk.Frame(self.nb, bg=BG)
        self.nb.add(qso_tab, text=' QSO ')
        right = tk.Frame(qso_tab, bg=BG)
        right.pack(side='right', fill='y', padx=(10, 0), pady=(6, 0))
        left = tk.Frame(qso_tab, bg=BG)
        left.pack(side='left', fill='both', expand=True, pady=(6, 0))
        self.specf, self.main_area = specf, main

        cb = tk.Frame(left, bg=BG)
        cb.pack(fill='x', pady=(0, 4))
        lc = tk.Label(cb, text='ON FREQ>', bg=BG, fg=DIM, font=(UI, -16))
        lc.pack(side='left')
        Tip(lc, 'Every station we can hear right now, decoded at the same time (pileup mode). '
                'Click one to work them - the main decoder locks onto their signal.')
        self.callers = tk.Frame(cb, bg=BG)
        self.callers.pack(side='left', fill='x')
        self._caller_keys = None
        self.txt = tk.Text(left, bg='#000000', fg=RX, highlightthickness=2, highlightbackground=DIM,
                           highlightcolor=ACC, selectbackground=ACC, selectforeground=BG, insertbackground=FG, wrap='word',
                           font=(UI, -20 if C else -24), relief='flat', padx=8, pady=6 if not C else 3,
                           height=3 if C else 6)
        self.engf = tk.Frame(left, bg=BG)
        hdr_e = tk.Frame(self.engf, bg=BG)
        hdr_e.pack(fill='x')
        tk.Label(hdr_e, text=' PLAIN ENGLISH ', bg=ACC, fg=BG, font=(UI, -16)).pack(side='left')
        tk.Label(hdr_e, text='  what the shorthand means', bg=BG, fg=DIM, font=(UI, -16)).pack(side='left')
        self.ptxt = tk.Text(self.engf, bg='#000000', fg=FG, wrap='word', font=(UI, -16), relief='flat',
                           height=2 if C else 5, padx=8, pady=4, highlightthickness=1, highlightbackground=DIM)
        self.ptxt.pack(fill='x')
        self.ptxt.tag_configure('them', foreground=RX)
        self.ptxt.tag_configure('me', foreground=TX)
        self.ptxt.tag_configure('live', foreground=DIM)
        self.ptxt.bind('<Key>', lambda e: 'break')
        self.rx_seg = ''
        self.v_plain = tk.BooleanVar(value=self.cfg.get('plain_english', True))
        self._left = left
        self.txt.tag_configure('tx', foreground=TX)
        self.txt.tag_configure('sys', foreground=DIM, font=(UI, -16))
        self.txt.bind('<Key>', lambda e: 'break' if e.keysym not in ('c', 'C') or not (e.state & 4) else None)

        # suggestion + send
        sug = tk.Frame(left, bg=PANEL)
        l_next = tk.Label(sug, text='NEXT>', bg=PANEL, fg=ACC, font=(PIX, -16))
        l_next.pack(side='left', padx=8)
        Tip(l_next, HELP['next'])
        self.v_sug = tk.StringVar()
        self.ent_sug = tk.Entry(sug, textvariable=self.v_sug, bg='#000000', fg=FG, insertbackground=FG,
                                font=(UI, -16), relief='flat')
        self.ent_sug.pack(side='left', fill='x', expand=True, ipady=6, padx=4, pady=6)
        self.ent_sug.bind('<Key>', lambda e: setattr(self, 'suggest_dirty', True))
        self.ent_sug.bind('<Return>', lambda e: self.send_suggested())
        ttk.Button(sug, text='SEND  F1', style='Send.TButton', command=self.send_suggested).pack(side='left', padx=4)
        ttk.Button(sug, text='STOP  Esc', style='Stop.TButton', command=self.stop_tx).pack(side='left', padx=(0, 6))

        self.lbl_means = tk.Label(left, text='', bg=BG, fg=DIM, font=(UI, -16), anchor='w',
                                  justify='left', wraplength=1000)
        self.v_sug.trace_add('write', lambda *_: self._update_means())
        macros = tk.Frame(left, bg=BG)
        for i, (lbl, kind, hk) in enumerate([('CQ call all', 'cq', 'cq'), ('Answer', 'answer', 'answer'),
                                             ('Report RST', 'report', 'report'), ('73 bye', '73', '73'),
                                             ('AGN? repeat', 'agn', 'agn'), ('QRS slower', 'qrs', 'qrs'),
                                             ('My call', 'call', 'call_btn')]):
            k = tk.Label(macros, text=f'F{i + 2}', bg=BG, fg=FG, font=(UI, -16), padx=2)
            v = tk.Label(macros, text=lbl, bg=ACC, fg=BG, font=(UI, -16), padx=4, cursor='hand2')
            Tip(v, HELP[hk])
            k.pack(side='left')
            v.pack(side='left', padx=(0, 6))
            for wdg in (k, v):
                wdg.bind('<Button-1>', lambda e, kk=kind: self.send_macro(kk))
            v.bind('<Enter>', lambda e, w=v: w.config(bg=FG), add='+')
            v.bind('<Leave>', lambda e, w=v: w.config(bg=ACC), add='+')
            self.root.bind(f'<F{i + 2}>', lambda e, kk=kind: self.send_macro(kk))
            self.root.bind(f'<Control-Key-{i + 2}>', lambda e, kk=kind: self.send_macro(kk))   # no-F-key keyboards
        free = tk.Frame(left, bg=BG)
        tk.Label(free, text='TYPE+ENTER>', bg=BG, fg=DIM, font=(UI, -16)).pack(side='left')
        self.v_free = tk.StringVar()
        e = tk.Entry(free, textvariable=self.v_free, bg=PANEL, fg=FG, insertbackground=FG,
                     font=(UI, -16), relief='flat')
        e.pack(side='left', fill='x', expand=True, padx=6, ipady=4)
        e.bind('<Return>', lambda ev: self._send_free())
        e.bind('<Shift-Return>', lambda ev: (self._send_free(keep_over=True), 'break')[1])
        e.bind('<KeyRelease>', lambda ev: self._preview_free(), add='+')
        Tip(e, 'Type what you want to say in plain CW shorthand. Enter sends it and adds the right '
               'ending (K / KN / BK / SK) and your callsign when needed. Shift+Enter keeps your turn going.')
        # bottom rows first (side=bottom), so a short window squeezes the copy pane, not the controls
        free.pack(side='bottom', fill='x')
        macros.pack(side='bottom', fill='x', pady=2 if C else 4)
        self.lbl_means.pack(side='bottom', fill='x')
        sug.pack(side='bottom', fill='x', pady=(3 if C else 6, 0))
        if self.v_plain.get():
            self.engf.pack(side='bottom', fill='x', pady=(3 if C else 6, 0))
        self.txt.pack(fill='both', expand=True)

        # --- right panel: the other station ---
        tk.Label(right, text=' THEM ', bg=ACC, fg=BG, font=(PIX, -16)).pack(anchor='w', pady=(0, 4))
        self.fields = {}
        short = {'call': 'Call', 'rst_rcvd': 'RST rcvd', 'rst_sent': 'RST sent', 'name': 'Name',
                 'qth': 'QTH', 'grid': 'Grid'}
        if C:                                   # label beside the field: half the height
            fgrid = tk.Frame(right, bg=BG)
            fgrid.pack(fill='x')
            fgrid.columnconfigure(1, weight=1)
        for row, (key, lbl) in enumerate([('call', 'Their callsign'), ('rst_rcvd', 'Your report from them (RST)'),
                                          ('rst_sent', 'Your report for them (RST)'), ('name', 'Their name'),
                                          ('qth', 'Their location (QTH)'), ('grid', 'Grid square (map)')]):
            host = fgrid if C else right
            lab = tk.Label(host, text=short[key] if C else lbl + ' [?]', bg=BG, fg=DIM, font=(UI, -16),
                           cursor='question_arrow', anchor='w')
            Tip(lab, HELP[key])
            v = tk.StringVar()
            ent = tk.Entry(host, textvariable=v, bg=PANEL, fg=FG, insertbackground=FG,
                           font=(UI, (-20 if C else -24) if key == 'call' else -16), relief='flat',
                           width=12 if C else 18)
            if C:
                lab.grid(row=row, column=0, sticky='w', padx=(0, 4), pady=1)
                ent.grid(row=row, column=1, sticky='ew', pady=1)
            else:
                lab.pack(anchor='w', pady=(2, 0))
                ent.pack(fill='x', ipady=0)
            ent.bind('<KeyRelease>', lambda ev, k=key: self.user_edit.add(k))
            ent.bind('<FocusOut>', lambda ev, k=key: self._field_edited(k))
            ent.bind('<Return>', lambda ev, k=key: self._field_edited(k))
            self.fields[key] = v
        self.lbl_dist = tk.Label(right, text='', bg=BG, fg=FG, font=(UI, -16), justify='left', wraplength=230)
        self.lbl_dist.pack(anchor='w', pady=(2 if C else 8, 0))
        self.lbl_wkd = tk.Label(right, text='', bg=BG, fg=ACC, font=(UI, -16), justify='left', wraplength=230)
        self.lbl_wkd.pack(anchor='w')
        self.lbl_state = tk.Label(right, text='', bg=BG, fg=DIM, font=(UI, -16))
        self.lbl_state.pack(anchor='w', pady=(2 if C else 8, 0))
        bb = tk.Frame(right, bg=BG)
        bb.pack(fill='x', pady=3 if C else 6)
        per_row = 4 if C else 2
        for i, (t_, cmd) in enumerate((('LOG' if C else 'LOG ^L', self.log_qso),
                                       ('NEW' if C else 'NEW ^N', self.new_qso),
                                       ('CLEAR', self._clear_all),
                                       ('AUDIO' if C else 'SAVE AUDIO', lambda: self.save_audio(self.qso.call)))):
            b_ = ttk.Button(bb, text=t_, command=cmd, width=5 if C else None)
            b_.grid(row=i // per_row, column=i % per_row, sticky='ew', padx=1, pady=1)
        for col in range(per_row):
            bb.columnconfigure(col, weight=1)

        self.lbl_lookup = tk.Label(right, text='', bg=BG, fg=DIM, font=(UI, -16), justify='left', wraplength=230)
        self.lbl_lookup.pack(anchor='w', pady=(6, 0))

        # other tabs are built the first time they're opened (faster start-up)
        for name, title in (('chat', ' CHAT '), ('contest', ' CONTEST '), ('practice', ' PRACTICE '),
                            ('log', ' LOG ')):
            self.tab_frames[name] = self._add_tab(title)
        self.txt.bind('<Button-1>', self._define_click, add='+')
        self.txt.tag_configure('alert', foreground=BG, background=ACC)
        self.txt.tag_configure('alert_block', foreground=BG, background=TX)

        self.status = tk.Label(r, text='', bg=ACC, fg=BG, anchor='w', font=(UI, -16), padx=6)
        self.status.pack(fill='x', side='bottom', before=main)

        r.bind('<F1>', lambda e: self._f1())
        r.bind('<Control-Key-1>', lambda e: self._f1())
        r.bind('<Escape>', lambda e: self.stop_tx())
        r.bind('<Control-l>', lambda e: self.log_qso())
        r.bind('<Control-n>', lambda e: self.new_qso())
        r.protocol('WM_DELETE_WINDOW', self.quit)
        self._refresh_station()
        self._refresh_suggestion(force=True)

    def _refresh_station(self):
        c = self.cfg
        self.lbl_station.config(text=f"{c['call']} ({c['license_class']}) | {c['grid']} | {c['rig_model_name']} | "
                                     f"CAT: {c['cat_backend']} | key: {c['key_method']} | PTT: {c['ptt_method']}")

    def set_status(self, text, err=False):
        self.status.config(text=(' !! ' if err else ' ') + text, bg=TX if err else ACC)

    def sysmsg(self, s):
        self.txt.insert('end', f'\n[{s}]\n', 'sys')
        self.txt.see('end')

    # ================= audio / DSP =================
    def start_audio(self):
        self.eng.start_audio()

    def stop_audio(self):
        self.eng.stop_audio()

    @staticmethod
    def _find_dev(label, kind):
        return find_dev(label, kind)

    # ================= rig / GridTracker loop =================
    # ================= TX =================
    def _apply_tx_backend(self):
        self.eng.apply_config()
        for t in self.tabs.values():
            if hasattr(t, 'on_config'):
                t.on_config()

    def tx_wpm(self):
        return self.eng.tx_wpm()

    def check_tx(self):
        """License guard. -> True if we may transmit here (engine emits 'blocked' otherwise)."""
        return self.eng.check_tx()[0]

    def send(self, text):
        if not self.eng.send(text, owner=self.current_tab()):
            return False
        self.suggest_dirty = False
        return True

    def _f1(self):
        t = self.tabs.get(self.current_tab())
        if t and hasattr(t, 'f1'):
            t.f1()
        else:
            self.send_suggested()

    def send_suggested(self):
        if self.send(self.v_sug.get()) and self.alert and self.alert.severity != 'block':
            self.clear_banner()

    def send_macro(self, kind):
        self._pull_fields()
        self.send(Q.macro(kind, self.cfg, self.qso, self._rst_out()))

    def _send_free(self, keep_over=False):
        full, code = self.ender.finish(self.v_free.get(), self.qso.call, keep_over)
        if full:
            self.send(full)
        self.v_free.set('')
        self._update_means()

    def _preview_free(self):
        t = self.v_free.get().strip()
        if not t or not self.v_plain.get():
            self._update_means()
            return
        full, code = self.ender.finish(t, self.qso.call)
        self.lbl_means.config(text=f'SENDS> {full}\nMEANS> {PL.translate(full, self.cfg["call"])} - '
                                   f'{Ender.explain(code, self.qso.call)}')

    def stop_tx(self):
        self.eng.stop_tx()
        self.sysmsg('TX aborted')

    def set_cw_mode(self):
        try:
            m = self.eng.set_rig_mode()
            self.set_status(f'Rig set to {m}' if m else 'Rig refused the mode change', err=not m)
        except Exception as e:
            self.set_status(f'CAT: {e}', err=True)

    # ================= QSO =================
    def _rst_out(self):
        v = self.fields['rst_sent'].get().strip()
        return v or Q.rst_from_snr(self.decoder.snr if self.decoder else 30)

    def _pull_fields(self):
        q = self.qso
        q.call = self.fields['call'].get().strip().upper()
        q.rst_rcvd = self.fields['rst_rcvd'].get().strip().upper()
        q.name = self.fields['name'].get().strip().upper()
        q.qth = self.fields['qth'].get().strip().upper()
        q.grid = self.fields['grid'].get().strip().upper()

    def _push_fields(self):
        q = self.qso
        for k in ('call', 'rst_rcvd', 'name', 'qth', 'grid'):
            self.fields[k].set(getattr(q, k))
        if q.call and not self.fields['rst_sent'].get():
            self.fields['rst_sent'].set(self._rst_out())
        if q.call and q.call != getattr(self, '_looked', ''):
            self._looked = q.call
            self.lbl_lookup.config(text='')
            self.request_lookup(q.call)
        self._refresh_info()

    def _field_edited(self, key):
        old_call = self.qso.call
        self._pull_fields()
        if key == 'call' and self.qso.call != old_call:
            self.qso.time_on = self.qso.time_on or __import__('datetime').datetime.now(
                __import__('datetime').timezone.utc)
        self._refresh_info()
        self._refresh_suggestion(force=True)

    def _refresh_info(self):
        q = self.qso
        g = q.grid
        if g and self.cfg['grid'] and len(g) >= 4:
            try:
                km, brg = Q.dist_bearing(self.cfg['grid'], g)
                self.lbl_dist.config(text=f'{km * 0.621371:,.0f} mi / {km:,.0f} km\nbeam heading {brg:.0f} deg')
            except Exception:
                self.lbl_dist.config(text='')
        else:
            self.lbl_dist.config(text='')
        wk = Q.worked_before(self.cfg['log_path'], q.call)
        if wk:
            band = Q.band_for(self.freq)
            same = [x for x in wk if x[1] == band]
            self.lbl_wkd.config(text=f'WORKED BEFORE x{len(wk)}' + (f'  (DUPE on {band})' if same else '')
                                + f'\nlast {wk[-1][0]} {wk[-1][1]}'
                                + ('\nCONFIRMED on LoTW' if self.confirmed(q.call) else ''))
        else:
            self.lbl_wkd.config(text='new station' if q.call else '')
        names = {'idle': 'Listening', 'cq_heard': 'Heard a CQ', 'called_me': 'They called you',
                 'in_qso': 'In QSO', 'ending': 'Wrapping up'}
        s = names.get(q.state, q.state)
        if q.they_ended:
            s += ' | they sent 73'
        if q.want_agn:
            s += ' | they asked AGN'
        self.lbl_state.config(text=s)

    def _refresh_suggestion(self, force=False):
        if self.suggest_dirty and not force:
            return
        self.v_sug.set(self.qso.suggest(self.cfg, self._rst_out()))
        self.suggest_dirty = False

    def _gt_decode(self):
        q = self.qso
        if not q.call:
            return
        if q.state == 'cq_heard':
            msg = f'CQ {q.call} {q.grid[:4]}'.strip()
        else:
            msg = f'{self.cfg["call"]} {q.call} {q.grid[:4] or q.rst_rcvd}'.strip()
        if msg != self.last_gt_msg:
            self.last_gt_msg = msg
            snr = int(self.decoder.snr - 25) if self.decoder else 0
            self.gt.decode(msg, snr, self.v_tone.get())

    def log_qso(self):
        self._pull_fields()
        q = self.qso
        if not q.call:
            self.set_status('Nothing to log - no call')
            return
        rst_sent = self._rst_out()
        self.log_external(q.call, q.grid, rst_sent, q.rst_rcvd, q.name, q.qth, time_on=q.time_on)
        self.sysmsg(f'LOGGED {q.call} {Q.band_for(self.freq)} {rst_sent}/{q.rst_rcvd} {q.name} {q.grid}')
        self.new_qso()

    def new_qso(self):
        self.eng.new_contact()
        self.user_edit.clear()
        self.qso.reset()
        self.fields['rst_sent'].set('')
        self._push_fields()
        self._refresh_suggestion(force=True)
        self.last_gt_msg = ''


    # ================= v3: tabs / alerts / earcons / logging / online =================
    TAB_NAMES = ['qso', 'chat', 'contest', 'practice', 'log']

    def _add_tab(self, title):
        f = tk.Frame(self.nb, bg=BG)
        self.nb.add(f, text=title)
        return f

    def current_tab(self):
        try:
            return self.TAB_NAMES[self.nb.index(self.nb.select())]
        except Exception:
            return 'qso'

    def set_sim(self, on):
        c = self.cfg
        self.v_sim.set(on)
        self.eng.set_sim(on, scenario=c['sim_scenario'], wpm=int(c['sim_wpm']), snr_db=float(c['sim_snr']),
                         qsb=bool(c['sim_qsb']), qrn=bool(c['sim_qrn']), callers=int(c['sim_callers']))
        self.sim_badge.config(bg=TX if on else BG, fg=BG if on else DIM,
                              text=' SIM ON - NOT ON THE AIR ' if on else ' SIM ')
        from config import __version__ as v
        self.root.title(f'ZBM2 CW Tool {v}' + ('   [SIMULATOR - practice only]' if on else ''))
        self.new_qso()
        if 'practice' in self.tabs:
            self.tabs['practice'].sim_changed(on)

    def ensure_tab(self, name):
        if name in self.tabs or name not in self.tab_frames:
            return self.tabs.get(name)
        if name == 'chat':
            from tab_chat import ChatTab as T
        elif name == 'contest':
            from tab_contest import ContestTab as T
        elif name == 'practice':
            from tab_practice import PracticeTab as T
        else:
            from tab_log import LogTab as T
        self.tabs[name] = T(self, self.tab_frames[name])
        if name == 'practice':
            self.eng.element_sink = self.tabs[name].element
        return self.tabs[name]

    def _tab_changed(self):
        self.current_tab_cached = self.current_tab()
        # noise gate holds back all-short-letter text; contest exchanges (5NN TU) are exactly that
        self.eng.textgate_on = self.current_tab_cached != 'contest'
        if self.eng.decoder:
            self.eng.decoder.textgate_on = self.eng.textgate_on
        if getattr(self.eng, 'soft', None) is not None:
            self.eng.soft.gate_on = self.eng.textgate_on
        self.ensure_tab(self.current_tab_cached)
        t = self.tabs.get(self.current_tab_cached)
        if t and hasattr(t, 'on_show'):
            t.on_show()

    # ---- DOS alert banner ----
    def show_alert(self, al, play=True, preload=True):
        self.alert = al
        bad = al.severity == 'block'
        col = TX if bad else ACC
        label = al.label or 'NOTE'
        self.banner.config(bg=col, highlightbackground=col)
        self.b_head.config(text=f'*** {al.code} ***   {label}', bg=col)
        self.b_def.config(text=al.definition or '')
        act = f'   F1 = {al.action}' if al.reply and preload else (f'   {al.action}' if al.action else '')
        self.b_plain.config(text=f'{al.plain}{act}', fg=col)
        if not self.banner.winfo_ismapped():
            self.banner.pack(fill='x', pady=(0, 6), before=self.nb)
        self._blink(6, col)
        import time as _t
        last = getattr(self, '_inline_alert', {})
        tail = self.txt.get('end-40c', 'end-1c')
        repeat = (_t.time() - last.get(al.code, 0) < 30) or tail.rstrip().endswith('***]')
        if al.severity != 'info' and not repeat:   # mark where copy went bad, on its own line (once)
            last[al.code] = _t.time()
            self._inline_alert = last
            if self.txt.get('end-2c', 'end-1c') not in ('\n', ''):
                self.txt.insert('end', '\n')
            self.txt.insert('end', f'[*** {al.code} ***]', 'alert_block' if bad else 'alert')
            self.txt.insert('end', '\n')
            self.txt.see('end')
        if play and al.severity != 'info':
            self.earcons.play('alert', {'PILEUP': 'QRZ?', 'QRO': 'QRO?'}.get(al.code, al.code))
        if preload and al.reply:
            if self.current_tab() == 'qso' and not self.suggest_dirty:
                self.v_sug.set(al.reply)
            t = self.tabs.get(self.current_tab())
            if t and hasattr(t, 'preload'):
                t.preload(al.reply)
        self.alert_codes.add(al.code)

    def _blink(self, n, col):
        if n <= 0 or not self.banner.winfo_ismapped():
            self.b_head.config(bg=col, fg=BG)
            return
        on = n % 2 == 0
        self.b_head.config(bg=col if on else BG, fg=BG if on else col)
        self.root.after(250, lambda: self._blink(n - 1, col))

    def clear_banner(self, code=None):
        if code and self.alert and self.alert.code != code:
            return
        self.alert = None
        self.banner.pack_forget()
        if code:
            self.alert_codes.discard(code)

    def _define_click(self, e):
        idx = self.txt.index(f'@{e.x},{e.y}')
        word = self.txt.get(f'{idx} wordstart', f'{idx} wordend').strip()
        line = self.txt.get(f'{idx} linestart', f'{idx} lineend')
        col = int(idx.split('.')[1])
        # include a trailing '?' or surrounding <> for prosigns
        m = re.search(r'<?[A-Z0-9]+>?\??', line[max(0, col - 6):col + 8])
        cand = [word, m.group(0) if m else '']
        for w in cand:
            d = qdefine(w) if w else None
            if d:
                from alerts import Alert
                self.show_alert(Alert(code=w.strip('<>').upper(), plain=PL.translate(w, self.cfg['call']),
                                      action='(click the banner to close)', reply='', severity='info',
                                      label=d[0], definition=d[1]), play=False, preload=False)
                return

    def _flash(self, txt):
        self.flash.config(text=txt.strip('<>'))
        self.flash.place(relx=1.0, x=-150, y=4, anchor='ne')
        self.flash.lift()
        if getattr(self, '_flash_job', None):
            self.root.after_cancel(self._flash_job)
        self._flash_job = self.root.after(1600, self.flash.place_forget)

    # ---- end of their over (central) ----
    def log_external(self, *args, **kw):
        return self.eng.log_external(*args, **kw)

    def save_audio(self, label=''):
        return self.eng.save_audio(label)

    def request_lookup(self, call):
        self.eng.request_lookup(call)

    def _apply_lookup(self, call, d):
        if not d:
            return
        if call == self.qso.call:
            for k in ('name', 'qth', 'grid'):
                if d.get(k) and not self.fields[k].get():
                    self.fields[k].set(d[k][:20] if k != 'grid' else d[k])
                    setattr(self.qso, k, self.fields[k].get())
            self.lbl_lookup.config(text=f"{d.get('source', '')}: {d.get('full_name', '')}\n{d.get('qth', '')} "
                                        f"{d.get('country', '')}" + ('\nLoTW user' if d.get('lotw') == '1' else ''))
            self._refresh_info()
            self._refresh_suggestion()
        if 'chat' in self.tabs:
            self.tabs['chat'].on_lookup(call, d)

    def save_lotw_conf(self, conf):
        self.eng.save_lotw_conf(conf)

    def confirmed(self, call):
        return self.eng.confirmed(call)

    # ================= UI pump =================
    def _poll(self):
        """Drain engine events. Text is batched so a fast sender costs one redraw per tick, not per character."""
        rx, txc = [], []
        latest = {}                                   # keep only the newest meter/spec/rig/skim per tick
        try:
            for _ in range(2000):
                kind, val = self.ui_q.get_nowait()
                if kind == 'rx':
                    rx.append(val)
                elif kind == 'txchar':
                    txc.append(val)
                elif kind in ('meter', 'spec', 'rig', 'skim'):
                    latest[kind] = val
                else:
                    if txc and kind in ('txdone', 'txstart'):
                        self._tx_chars(''.join(txc))
                        txc = []
                    if rx and kind in ('segment', 'chat_seg'):
                        self._rx_text(''.join(rx))
                        rx = []
                    self._event(kind, val)
        except queue.Empty:
            pass
        if rx:
            self._rx_text(''.join(rx))
        if txc:
            self._tx_chars(''.join(txc))
        for kind, val in latest.items():
            self._event(kind, val)
        self.root.after(30, self._poll)

    def _rx_text(self, val):
        self.last_rx = (getattr(self, 'last_rx', '') + val)[-200:]
        self._eng_rx(val)
        self.txt.insert('end', val)
        if int(self.txt.index('end-1c').split('.')[0]) > 2000:
            self.txt.delete('1.0', '500.0')
        self.txt.see('end')
        self._pull_fields_soft()
        changed = self.qso.feed(val)
        if changed:
            self._push_fields()
            self._refresh_suggestion()
            self._gt_decode()
        elif self.qso.want_agn:
            self._refresh_suggestion()
        snr = self.decoder.snr if self.decoder else 0
        for t in self.tabs.values():
            t.on_rx(val, snr)

    def _tx_chars(self, val):
        self.txt.insert('end', val, 'tx')
        self.txt.see('end')
        t = self.tabs.get(self.tx_owner)
        if t and hasattr(t, 'on_txchar'):
            for c in val:
                t.on_txchar(c)

    def _event(self, kind, val):
        if kind == 'meter':
            snr, wpm, locked, tone, key = val          # snr: real S/N in dB (500 Hz), None = nothing copied
            w = 0 if snr is None else int(max(0, min(1, (snr + 10) / 40)) * 120)
            if not hasattr(self, '_snr_bar'):
                self._snr_bar = self.snr_canvas.create_rectangle(0, 0, 0, 18, width=0)
            self.snr_canvas.coords(self._snr_bar, 0, 0, w, 18)
            self.snr_canvas.itemconfigure(self._snr_bar, fill=RX if key else '#5a3a00')
            self.lbl_snr.config(text='S/N  --' if snr is None else f'S/N {snr:+.0f} dB')
            self.lbl_rxwpm.config(text=f'{wpm:.0f}' if locked else '--')
            self.lbl_txwpm.config(text=str(self.tx_wpm()))
            if self.v_auto.get() and abs(tone - self.v_tone.get()) >= 10:
                self._tone_from_engine = True
                self.v_tone.set(int(round(tone / 10) * 10))
                self._tone_from_engine = False
            if 'chat' in self.tabs:
                self.tabs['chat'].on_key(key)
        elif kind == 'spec':
            self._draw_spec(*val)
        elif kind == 'skim':
            self.skim_rows = val
            self._draw_callers()
        elif kind == 'rig':
            if self.rig_ok:
                f = self.freq
                self.lbl_freq.config(text=f'{f // 1000000}.{(f // 1000) % 1000:03d}.{(f % 1000) // 10:02d}')
                self.lbl_rig.config(text=f'{self.mode}  {Q.band_for(f)} | {self.rig.name}  [OK]')
            else:
                self.lbl_rig.config(text=f'CAT: {self.rig_err or "not connected"}')
        elif kind == 'alerts':
            new, cleared, conf = val
            cols = {'good': RX, 'shaky': ACC, 'lost': TX, 'idle': DIM}
            self.lbl_conf.config(text=f'COPY: {conf.upper() if conf != "idle" else "--"}', fg=cols[conf])
            calls = [r for r in self.skim_rows if r[1]]
            for al in new:
                if al.code == 'QRM' and len(calls) >= 2:      # it's a pileup, not interference
                    from alerts import Alert
                    al = Alert(code='PILEUP', label='NOTE', severity='warn', reply='',
                               definition=f'{len(calls)} stations are calling at once: '
                                          + ', '.join(r[1] for r in calls[:5]),
                               plain='Pick ONE: click a call in ON FREQ (or type it) and press F1. '
                                     'The others will wait their turn.', action='')
                self.show_alert(al)
            for code in cleared:
                self.clear_banner(code)
                if code == 'QRM':
                    self.clear_banner('PILEUP')
        elif kind == 'segment':
            if self.current_tab() == 'qso':
                al = missing_fields_alert(self.qso)
                if al and 'AGN' not in self.alert_codes:
                    self.show_alert(al)
        elif kind == 'blocked':
            self.sysmsg('NOT SENT - ' + val)
            self.set_status(val, err=True)
            self.show_alert(license_block_alert(val))
        elif kind == 'turbo_hint':
            from alerts import Alert
            who = val or 'They'
            self.show_alert(Alert(code='QRQ', severity='warn', reply='QRQ?',
                                  plain=f'{who} {"is" if val else "are"} sending with machine-perfect timing - '
                                        f'probably a computer, like you.',
                                  action='ask "QRQ?" and tick TURBO to speed both of you up'))
        elif kind == 'flash':
            self._flash(val)
        elif kind == 'lookup':
            self._apply_lookup(*val)
        elif kind == 'status':
            self.set_status(val[0], err=val[1])
        elif kind == 'logged':
            if 'log' in self.tabs:
                self.tabs['log'].refresh()
        elif kind in ('chat_seg', 'buddy'):
            if 'chat' in self.tabs:
                self.tabs['chat'].on_event(kind, val)
        elif kind == 'practice':
            if 'practice' in self.tabs:
                self.tabs['practice'].on_text(val)
        elif kind == 'online_msg':
            self.set_status(val)
        elif kind == 'online_err':
            self.set_status(val, err=True)
        elif kind == 'txstart':
            self.txt.insert('end', '\n')
            if self.v_plain.get():
                if self.rx_seg.strip():
                    self._eng_line(PL.translate(self.rx_seg, self.cfg['call']), 'them')
                    self.rx_seg = ''
                if val:
                    self._eng_line(PL.translate(val, self.cfg['call']), 'me')
        elif kind == 'txdone':
            self.txt.insert('end', '\n')
            t = self.tabs.get(self.tx_owner)
            if t and hasattr(t, 'on_txdone'):
                t.on_txdone()
            self._refresh_info()
            self._refresh_suggestion(force=True)

    def _draw_spec(self, fr, db, rms, pk):
        c = self.spec
        W, H = max(c.winfo_width(), 100), int(c['height'])
        span = int(self.v_span.get() or 600)
        center = int(round(self.v_tone.get() / 50.0)) * 50
        lo, hi = center - span / 2, center + span / 2
        if getattr(self, '_spec_key', None) != (W, lo, hi):    # rebuild grid on resize / retune / span
            self._spec_key = (W, lo, hi)
            c.delete('all')
            step = 50 if span <= 600 else 100
            f0 = int(np.ceil(lo / step) * step)
            for f in range(f0, int(hi) + 1, step):
                x = (f - lo) / (hi - lo) * W
                major = f % (step * 2) == 0
                c.create_line(x, H - (8 if major else 4), x, H, fill=DIM)
                if major and 22 < x < W - 22:          # no half-cut labels at the edges
                    c.create_text(x, 9, text=str(f), fill=DIM, font=(UI, -16))
            self._sp_line = c.create_line(0, 0, 1, 1, fill=RX, width=1)
            self._sp_box = c.create_rectangle(0, 0, 1, 1, outline=ACC)
            self._sp_txt = c.create_text(0, 0, text='', fill=ACC, font=(UI, -16))
        m = (fr >= lo) & (fr <= hi)
        f, d = fr[m], db[m]
        if len(f) < 2:
            return
        floor = float(np.median(d))
        rng = max(float(np.max(d)) - floor + 10, 30)
        xs = (f - lo) / (hi - lo) * W
        ys = H - 4 - np.clip((d - floor + 5) / rng, 0, 1) * (H - 20)
        c.coords(self._sp_line, *np.column_stack([xs, ys]).ravel().tolist())
        t = self.decoder.tone if self.decoder else self.v_tone.get()
        x = (t - lo) / (hi - lo) * W
        bw = 100 / (hi - lo) * W                 # decoder passband ~100 Hz
        c.coords(self._sp_box, x - bw / 2, 18, x + bw / 2, H)
        c.delete('skim')
        for f0, call, wpm, snr, text, cid in self.skim_rows:
            if call and lo < f0 < hi:
                xx = (f0 - lo) / (hi - lo) * W
                tag = f'sk{cid}'
                c.create_line(xx, 30, xx, H - 4, fill=DIM, dash=(2, 3), tags=('skim', tag))
                c.create_text(xx, 30, text=f'{call} {wpm:.0f}', fill=BG, font=(UI, -16), anchor='s',
                              tags=('skim', tag, tag + 't'))
                bb = c.bbox(tag + 't')
                if bb:
                    c.create_rectangle(bb, fill=ACC if call == self.qso.call else FG, width=0, tags=('skim', tag))
                    c.tag_raise(tag + 't')
                c.tag_bind(tag, '<Button-1>', lambda e, cl=call, fq=f0: (self.work_station(cl, fq), 'break')[1])
        c.coords(self._sp_txt, x, H - 10)
        c.itemconfigure(self._sp_txt, text=f'{t:.0f}')
        # level bar
        wl = 60                                  # horizontal level bar
        frac = min(1, max(0, (rms + 70) / 70))
        col = '#5a3a00' if rms < -45 else RX if pk < -3 else TX
        if not hasattr(self, '_lv_bar'):
            self._lv_bar = self.lvl.create_rectangle(0, 0, 0, 10, fill=col, width=0)
            for lv_db in (-40, -10):
                x = (lv_db + 70) / 70 * wl
                self.lvl.create_line(x, 0, x, 10, fill=DIM)
        self.lvl.coords(self._lv_bar, 0, 0, frac * wl, 10)
        self.lvl.itemconfigure(self._lv_bar, fill=col)
        self.last_level = (rms, pk)
        self.lbl_lvl.config(text='CLIP!' if pk > -1 else 'low' if rms < -55 else f'{rms:.0f}dB',
                            fg=TX if pk > -1 else DIM)

    def _draw_callers(self):
        rows = [r for r in self.skim_rows if r[1]][:6]
        keys = [(r[1], int(r[0])) for r in rows]
        if keys != self._caller_keys:
            self._caller_keys = keys
            for w in self.callers.winfo_children():
                w.destroy()
            if not rows:
                tk.Label(self.callers, text='(nobody decoded yet)', bg=BG, fg=DIM, font=(UI, -16)).pack(side='left')
            for f, call, wpm, snr, text, cid in rows:
                chip = tk.Label(self.callers, text=f' {call} {wpm:.0f}wpm ', bg=ACC if call == self.qso.call else PANEL,
                                fg=BG if call == self.qso.call else FG, font=(UI, -16), cursor='hand2', padx=2)
                chip.pack(side='left', padx=3)
                chip.bind('<Button-1>', lambda e, c=call, fq=f: self.work_station(c, fq))
                Tip(chip, f'{call} at {f:.0f} Hz, {snr:.0f} dB: "{text.strip()[-50:]}"')

    def work_station(self, call, f):
        """Pileup pick: lock the main decoder on this signal and make them the QSO partner."""
        self.v_auto.set(False)
        self.v_tone.set(int(round(f / 10) * 10))
        self.eng.set_tone(int(round(f)), False)
        if self.qso.call != call:
            self.qso._new_partner(call)
            self.qso.state = 'called_me' if self.cfg['call'].upper() in ' '.join(
                r[4] for r in self.skim_rows if r[1] == call) else 'cq_heard'
            self.user_edit.clear()
            self._push_fields()
            self._refresh_suggestion(force=True)
        self.eng.room.partner = call
        self._caller_keys = None
        self._draw_callers()
        self.set_status(f'Working {call} at {f:.0f} Hz')

    def _spec_click(self, e):
        W = max(self.spec.winfo_width(), 100)
        lo, hi = self._spec_key[1], self._spec_key[2]
        f = lo + e.x / W * (hi - lo)
        # click a signal = lock onto that station; the tone still follows its drift
        self._tone_from_engine = True
        self.v_tone.set(int(round(f / 10) * 10))
        self._tone_from_engine = False
        self.v_auto.set(True)
        self.eng.set_tone(f, True, lock=True)
        self.set_status(f'Locked onto the signal at {f:.0f} Hz - click elsewhere to switch, or untick A and tick it again to search')
        self._save_quick()

    # ---------- plain English ----------
    def _toggle_plain(self):
        on = self.v_plain_cb.get()
        self.v_plain.set(on)
        if on:
            self.engf.pack(side='bottom', fill='x', pady=(3 if self.compact else 6, 0), before=self.txt)
        else:
            self.engf.pack_forget()
        self.lbl_means.config(text='' if not on else self.lbl_means.cget('text'))
        self._update_means()
        self.cfg['plain_english'] = on
        save_cfg(self.cfg)

    def _clear_all(self):
        """CLEAR: copy pane, plain-English pane and the ON FREQ callsign chips."""
        self.txt.delete('1.0', 'end')
        self.ptxt.delete('1.0', 'end')
        self.rx_seg = ''
        self.skim_rows = []
        sk = getattr(self.eng, 'skimmer', None)
        if sk is not None:
            sk.channels = []
        self._caller_keys = None
        self._draw_callers()

    def _eng_line(self, text, who):
        if not text:
            return
        self.ptxt.delete('live.first', 'live.last') if self.ptxt.tag_ranges('live') else None
        tag = 'me' if who == 'me' else 'them'
        prefix = 'YOU SENT: ' if who == 'me' else 'THEY SAID: '
        self.ptxt.insert('end', prefix + text + '\n', tag)
        if int(self.ptxt.index('end-1c').split('.')[0]) > 400:
            self.ptxt.delete('1.0', '100.0')
        self.ptxt.see('end')

    def _eng_rx(self, chunk):
        if not self.v_plain.get():
            return
        self.rx_seg += chunk
        mine = self.cfg['call']
        if PL.SEGMENT_END.search(self.rx_seg):
            self._eng_line(PL.translate(self.rx_seg, mine), 'them')
            self.rx_seg = ''
        else:
            done = self.rx_seg[:self.rx_seg.rfind(' ') + 1]
            if self.ptxt.tag_ranges('live'):
                self.ptxt.delete('live.first', 'live.last')
            if done.strip():
                self.ptxt.insert('end', '... ' + PL.translate(done, mine), 'live')
                self.ptxt.see('end')

    def _update_means(self):
        if not self.v_plain.get():
            self.lbl_means.config(text='')
            return
        t = PL.translate(self.v_sug.get(), self.cfg['call'])
        self.lbl_means.config(text=('MEANS> ' + t) if t else '')

    def _pull_fields_soft(self):
        # keep manual edits the user made in the side panel
        for k in self.user_edit:
            if k == 'rst_sent':
                continue
            v = self.fields[k].get().strip().upper()
            if v and v != getattr(self.qso, k):
                setattr(self.qso, k, v)

    # ================= settings =================
    def _save_quick(self):
        try:
            self.cfg.update(turbo=self.v_turbo.get(), turbo_wpm=int(self.v_turbo_wpm.get()))
        except (tk.TclError, AttributeError):
            pass
        self.cfg.update(spec_span=int(self.v_span.get()), match_wpm=self.v_match.get(), wpm_offset=self.v_off.get(),
                        manual_wpm=self.v_manual.get(), sidetone=self.v_sidetone.get(),
                        tone=self.v_tone.get(), auto_tone=self.v_auto.get())
        save_cfg(self.cfg)

    def _rxgain_changed(self):
        try:
            g = max(0, min(40, int(self.v_rxgain.get())))
        except (tk.TclError, ValueError):
            return
        self.cfg['rx_gain_db'] = g
        self._save_quick()

    def _tone_changed(self):
        """Tone typed / spun by the operator: with A on, lock onto that pitch and follow it."""
        try:
            self.eng.set_tone(int(self.v_tone.get()), self.v_auto.get(), lock=self.v_auto.get())
        except (tk.TclError, ValueError):
            return
        self._save_quick()

    def _auto_toggled(self):
        """A on: search the whole band for the strongest keyed signal. A off: stay on this tone."""
        try:
            if self.v_auto.get():
                self.eng.set_tone(None, True)
            else:
                self.eng.set_tone(int(self.v_tone.get()), False)
        except (tk.TclError, ValueError):
            return
        self._save_quick()

    def _sync_engine_vars(self):
        """Speed / tone controls write straight into the config the engine reads."""
        def upd(*_):
            try:
                self.cfg.update(match_wpm=self.v_match.get(), wpm_offset=int(self.v_off.get()),
                                manual_wpm=int(self.v_manual.get()), turbo=self.v_turbo.get(),
                                turbo_wpm=int(self.v_turbo_wpm.get()), sidetone=self.v_sidetone.get())
            except (tk.TclError, ValueError):
                pass
        for v in (self.v_match, self.v_off, self.v_manual, self.v_turbo, self.v_turbo_wpm, self.v_sidetone):
            v.trace_add('write', upd)
        upd()
        self.v_tone.trace_add('write', lambda *_: None if getattr(self, '_tone_from_engine', False)
                              else self._tone_changed())

    def open_settings(self):
        SettingsDialog(self)

    def quit(self):
        try:
            if 'practice' in self.tabs:
                self.tabs['practice'].stop_serial()
        except Exception:
            pass
        self.eng.shutdown()            # also stops a rigctld we launched
        self._save_quick()
        self.root.destroy()


CAT_CHOICES = [('hamlib_launch', 'Hamlib - this app starts rigctld (pick radio + COM)'),
               ('hamlib_existing', 'Hamlib - connect to my running rigctld'),
               ('omnirig', 'OmniRig'),
               ('none', 'None (no CAT)')]
KEY_CHOICES = [('rigkeyer', "Radio's internal keyer via CAT (Hamlib)"),
               ('serial', 'Serial port line (DTR or RTS)'),
               ('audio', 'Audio tones into rig (DATA-U / USB mode)')]
PTT_CHOICES = [('VOX', 'None - break-in / VOX'), ('CAT', 'CAT command'),
               ('RTS', 'RTS line'), ('DTR', 'DTR line')]
HS_CHOICES = [('Default', 'Default'), ('None', 'None'), ('Hardware', 'Hardware (RTS/CTS)'),
              ('XONXOFF', 'XON/XOFF')]


def _lbl(choices, val):
    return dict(choices).get(val, choices[0][1])


def _val(choices, lbl):
    return {v: k for k, v in choices}.get(lbl, choices[0][0])


class SettingsDialog:
    def __init__(self, app):
        self.app = app
        self.c = dict(app.cfg)
        w = self.w = tk.Toplevel(app.root)
        w.title('Rig Setup - ZBM2 CW Tool')
        w.configure(bg=BG)
        w.transient(app.root)
        w.grab_set()
        w.geometry('1040x700')
        self.ins = [''] + dev_labels('input')
        self.outs = [''] + dev_labels('output')
        self.v = {}
        self.models = []

        nb = ttk.Notebook(w)
        nb.pack(fill='both', expand=True, padx=8, pady=8)
        self.t_station = self._tab(nb, 'Station')
        self.t_radio = self._tab(nb, 'Radio / CAT')
        self.t_key = self._tab(nb, 'CW Keying & PTT')
        self.t_audio = self._tab(nb, 'Audio')
        self.t_gt = self._tab(nb, 'GridTracker & Log')
        self.t_snd = self._tab(nb, 'Sounds & Chat')
        self.t_on = self._tab(nb, 'Online')

        # ---- station ----
        t = self.t_station
        self._entry(t, 'call', 'Callsign')
        self._entry(t, 'grid', 'Grid square')
        self._entry(t, 'name', 'Name (sent)')
        self._entry(t, 'qth', 'QTH (sent)')
        self._combo(t, 'license_class', 'License class', Q.LICENSE_CLASSES, self.c['license_class'])

        # ---- radio / CAT ----
        t = self.t_radio
        self._combo(t, 'cat_backend', 'Rig control', [x[1] for x in CAT_CHOICES],
                    _lbl(CAT_CHOICES, self.c['cat_backend']), width=52, cb=self._radio_vis)
        self.f_hl = tk.Frame(t, bg=BG)
        self.f_ex = tk.Frame(t, bg=BG)
        self.f_om = tk.Frame(t, bg=BG)
        # hamlib launch
        f = self.f_hl
        r = self._row(f, 'Hamlib folder')
        self.v['hamlib_dir'] = tk.StringVar(value=self.c['hamlib_dir'] or RC.find_hamlib_bin())
        tk.Entry(r, textvariable=self.v['hamlib_dir'], bg=PANEL, fg=FG, insertbackground=FG,
                 relief='flat', width=40).pack(side='left', ipady=2)
        ttk.Button(r, text='Browse', command=self._browse).pack(side='left', padx=4)
        ttk.Button(r, text='Reload radios', command=self._load_models).pack(side='left')
        r = self._row(f, 'Hamlib version')
        self.lbl_hlver = tk.Label(r, text='', bg=BG, fg=FG, font=(UI, -16))
        self.lbl_hlver.pack(side='left')
        self.btn_hlupd = ttk.Button(r, text='Update Hamlib', command=self._update_hamlib)
        self.btn_hlupd.pack(side='left', padx=8)
        Tip(self.btn_hlupd, 'Downloads the newest Hamlib from github.com/Hamlib (checksum-verified) into this '
                            "app's own folder. Newer radios like the Yaesu FTX-1 need Hamlib 4.7 or later. "
                            'Your old Hamlib is left in place.')
        self.w.after(200, self._show_hlver)
        r = self._row(f, 'Search radios')
        self.v_search = tk.StringVar()
        es = tk.Entry(r, textvariable=self.v_search, bg=PANEL, fg=FG, insertbackground=FG,
                      relief='flat', width=20)
        es.pack(side='left', ipady=2)
        es.bind('<KeyRelease>', lambda e: self._filter_models())
        tk.Label(r, text='(type e.g. FTX, 991, 7300)', bg=BG, fg=DIM).pack(side='left', padx=6)
        r = self._row(f, 'Radio')
        self.v_model = tk.StringVar()
        self.cb_model = ttk.Combobox(r, textvariable=self.v_model, width=50, state='readonly')
        self.cb_model.pack(side='left')
        self.cb_model.bind('<<ComboboxSelected>>', lambda e: self._preview())
        r = self._row(f, 'CAT COM port')
        self.v['cat_port'] = tk.StringVar(value=self.c['cat_port'])
        self.cb_cat = ttk.Combobox(r, textvariable=self.v['cat_port'], width=36)
        self.cb_cat.pack(side='left')
        self.cb_cat.bind('<<ComboboxSelected>>', lambda e: self._strip_port(self.v['cat_port']))
        ttk.Button(r, text='Refresh', command=self._refresh_ports).pack(side='left', padx=4)
        self._combo(f, 'cat_baud', 'Baud', ['4800', '9600', '19200', '38400', '57600', '115200'],
                    str(self.c['cat_baud']), cb=self._preview)
        self._combo(f, 'cat_databits', 'Data bits', ['Default', '7', '8'], self.c['cat_databits'], cb=self._preview)
        self._combo(f, 'cat_stopbits', 'Stop bits', ['Default', '1', '2'], self.c['cat_stopbits'], cb=self._preview)
        self._combo(f, 'cat_handshake', 'Handshake', [x[1] for x in HS_CHOICES],
                    _lbl(HS_CHOICES, self.c['cat_handshake']), cb=self._preview)
        self._combo(f, 'cat_dtr', 'DTR line on CAT port', ['Default', 'On', 'Off'], self.c['cat_dtr'], cb=self._preview)
        self._combo(f, 'cat_rts', 'RTS line on CAT port', ['Default', 'On', 'Off'], self.c['cat_rts'], cb=self._preview)
        self._entry(f, 'rigctld_port', 'rigctld TCP port', width=8)
        self._combo(f, 'hamlib_ptt', 'Hamlib PTT method', ['RIG', 'RTS', 'DTR', 'NONE'],
                    self.c['hamlib_ptt'], cb=self._preview)
        self.lbl_cmd = tk.Label(f, bg=BG, fg=DIM, font=(UI, -16), wraplength=660, justify='left')
        self.lbl_cmd.pack(anchor='w', padx=10, pady=4)
        # hamlib existing
        f = self.f_ex
        self._entry(f, 'rigctld_host', 'rigctld host')
        self.v['rigctld_port_ex'] = tk.StringVar(value=str(self.c['rigctld_port']))
        r = self._row(f, 'rigctld port')
        tk.Entry(r, textvariable=self.v['rigctld_port_ex'], bg=PANEL, fg=FG, insertbackground=FG,
                 relief='flat', width=8).pack(side='left', ipady=2)
        # omnirig
        f = self.f_om
        self._combo(f, 'omnirig_rig', 'OmniRig rig', ['1', '2'], str(self.c['omnirig_rig']))
        r = self._row(f, 'OmniRig program')
        ttk.Button(r, text='Get / update OmniRig', command=lambda: __import__('webbrowser').open(
            'https://www.dxatlas.com/omnirig/')).pack(side='left')
        tk.Label(r, text='  (Windows only; its installer needs admin rights, so it updates itself outside this app)',
                 bg=BG, fg=DIM, font=(UI, -14)).pack(side='left')
        tk.Label(f, text='Configure the radio model + COM port inside OmniRig itself.\n'
                         'OmniRig cannot send CW text - use Serial or Audio keying.',
                 bg=BG, fg=DIM, justify='left').pack(anchor='w', padx=10, pady=6)
        bb = tk.Frame(t, bg=BG)
        bb.pack(side='bottom', fill='x', pady=6)
        ttk.Button(bb, text='Test CAT', command=self.test_cat).pack(side='left', padx=10)
        self.lbl_test = tk.Label(bb, text='', bg=BG, fg=ACC)
        self.lbl_test.pack(side='left')

        # ---- keying / PTT ----
        t = self.t_key
        self._combo(t, 'key_method', 'CW keying', [x[1] for x in KEY_CHOICES],
                    _lbl(KEY_CHOICES, self.c['key_method']), width=44, cb=self._warn)
        r = self._row(t, 'Keying COM port')
        self.v['key_port'] = tk.StringVar(value=self.c['key_port'])
        self.cb_key = ttk.Combobox(r, textvariable=self.v['key_port'], width=36)
        self.cb_key.pack(side='left')
        self.cb_key.bind('<<ComboboxSelected>>', lambda e: (self._strip_port(self.v['key_port']), self._warn()))
        self._combo(t, 'key_line', 'Keying line', ['DTR', 'RTS'], self.c['key_line'], cb=self._warn)
        tk.Label(t, text='CTS/DSR are inputs on a COM port and cannot key a radio - use DTR or RTS.',
                 bg=BG, fg=DIM).pack(anchor='w', padx=10)
        self._combo(t, 'ptt_method', 'PTT', [x[1] for x in PTT_CHOICES],
                    _lbl(PTT_CHOICES, self.c['ptt_method']), width=30, cb=self._warn)
        r = self._row(t, 'PTT COM port')
        self.v['ptt_port'] = tk.StringVar(value=self.c['ptt_port'])
        self.cb_ptt = ttk.Combobox(r, textvariable=self.v['ptt_port'], width=36)
        self.cb_ptt.pack(side='left')
        self.cb_ptt.bind('<<ComboboxSelected>>', lambda e: (self._strip_port(self.v['ptt_port']), self._warn()))
        self._entry(t, 'min_wpm', 'Never send slower than (WPM)', width=6)
        self._entry(t, 'max_wpm', 'Never send faster than (WPM)', width=6)
        self.lbl_warn = tk.Label(t, text='', bg=BG, fg=ACC, wraplength=660, justify='left')
        self.lbl_warn.pack(anchor='w', padx=10, pady=8)

        # ---- audio ----
        t = self.t_audio
        self._combo(t, 'audio_in', 'RX: sound card from radio', self.ins, self.c['audio_in'], width=50)
        self._combo(t, 'audio_in_ch', 'RX channel', ['Left/Mono', 'Right'], self.c['audio_in_ch'])
        self._combo(t, 'audio_out', 'TX: sound card to radio', self.outs, self.c['audio_out'], width=50)
        self._combo(t, 'audio_out_ch', 'TX channel', ['Both', 'Left', 'Right'], self.c['audio_out_ch'])
        r = self._row(t, 'TX drive level')
        self.v['tx_drive'] = tk.IntVar(value=int(self.c['tx_drive']))
        tk.Scale(r, from_=0, to=100, orient='horizontal', variable=self.v['tx_drive'], length=260,
                 bg=BG, fg=FG, troughcolor=PANEL, highlightthickness=0).pack(side='left')
        ttk.Button(r, text='Tune 10 s', command=self._tune).pack(side='left', padx=6)
        ttk.Button(r, text='Stop', command=lambda: self.app.tx.stop()).pack(side='left')
        tk.Label(t, text='Tune sends a steady tone. Raise drive until power is full, then back off until\n'
                         'the radio\'s ALC meter shows nothing (HF). On an HT just keep it clean and not distorted.',
                 bg=BG, fg=DIM, justify='left').pack(anchor='w', padx=10)
        self._entry(t, 'ptt_lead_ms', 'PTT lead time (ms)', width=6)
        self._entry(t, 'ptt_tail_ms', 'PTT tail time (ms)', width=6)
        self._combo(t, 'sidetone_out', 'Sidetone speakers (NOT the rig)', self.outs, self.c['sidetone_out'], width=50)
        r = tk.Frame(t, bg=BG)
        r.pack(fill='x', padx=10, pady=8)
        ttk.Button(r, text='Windows Sound settings', command=lambda: os.system('start mmsys.cpl')
                   if os.name == 'nt' else None).pack(side='left')
        tk.Label(r, text='  Set the radio\'s sound card RX level so the RX bar sits in the middle (no CLIP).',
                 bg=BG, fg=DIM).pack(side='left')

        # ---- gridtracker / log ----
        t = self.t_gt
        r = self._row(t, 'Send to GridTracker')
        self.v['gt_enable'] = tk.BooleanVar(value=self.c['gt_enable'])
        ttk.Checkbutton(r, variable=self.v['gt_enable']).pack(side='left')
        self._entry(t, 'gt_host', 'UDP host')
        self._entry(t, 'gt_port', 'UDP port', width=8)
        self._entry(t, 'log_path', 'ADIF log file', width=60)

        # ---- sounds & chat ----
        t = self.t_snd
        self._check(t, 'earcons_on', 'Morse earcons (UI sounds are real CW)')
        self._check(t, 'earcons_startup_only', 'Only the ZBM2 startup sound')
        self._entry(t, 'earcon_pitch', 'Earcon pitch Hz (differs from RX)', width=6)
        self._entry(t, 'earcon_wpm', 'Earcon character speed WPM', width=6)
        self._entry(t, 'earcon_vol', 'Earcon volume 0-1', width=6)
        self._check(t, 'earcon_speak', 'Also say the letter out loud (Windows)')
        tk.Label(t, text='Your rag-chew profile (CHAT tab quick buttons):', bg=BG, fg=ACC,
                 font=(UI, -16)).pack(anchor='w', padx=10, pady=(10, 2))
        self._entry(t, 'chat_rig', 'My rig (e.g. FTX1)')
        self._entry(t, 'chat_ant', 'My antenna (e.g. EFHW)')
        self._entry(t, 'chat_pwr', 'My power (e.g. 100W)', width=8)
        self._entry(t, 'chat_age', 'Age (optional)', width=6)
        self._entry(t, 'chat_lic', 'Years licensed (optional)', width=6)
        self._entry(t, 'rec_seconds', 'Audio replay buffer (seconds)', width=6)
        self._check(t, 'rec_autosave', 'Auto-save audio per QSO')

        # ---- online ----
        t = self.t_on
        tk.Label(t, text='QRZ.com (full lookups need a QRZ XML subscription)', bg=BG, fg=ACC,
                 font=(UI, -16)).pack(anchor='w', padx=10, pady=(8, 0))
        self._entry(t, 'qrz_user', 'QRZ username')
        self._entry(t, 'qrz_pass', 'QRZ password', show='*')
        self._entry(t, 'qrz_logbook_key', 'QRZ Logbook API key', show='*')
        tk.Label(t, text='HamQTH.com (free lookups - fallback if no QRZ)', bg=BG, fg=ACC,
                 font=(UI, -16)).pack(anchor='w', padx=10, pady=(8, 0))
        self._entry(t, 'hamqth_user', 'HamQTH username')
        self._entry(t, 'hamqth_pass', 'HamQTH password', show='*')
        tk.Label(t, text='LoTW (Logbook of the World)', bg=BG, fg=ACC,
                 font=(UI, -16)).pack(anchor='w', padx=10, pady=(8, 0))
        self._entry(t, 'lotw_user', 'LoTW username')
        self._entry(t, 'lotw_pass', 'LoTW password', show='*')
        r = self._row(t, 'TQSL program')
        self.v['tqsl_path'] = tk.StringVar(value=self.c['tqsl_path'] or ON.find_tqsl())
        tk.Entry(r, textvariable=self.v['tqsl_path'], bg=PANEL, fg=FG, insertbackground=FG, relief='flat',
                 width=40).pack(side='left', ipady=2)
        self._entry(t, 'tqsl_location', 'TQSL station location name')
        self._check(t, 'auto_lookup', 'Look up callsigns automatically')
        self._check(t, 'auto_qrz_upload', 'Auto-upload to QRZ Logbook')
        self._check(t, 'auto_lotw_upload', 'Auto-upload to LoTW (TQSL)')
        r = tk.Frame(t, bg=BG)
        r.pack(fill='x', padx=10, pady=6)
        ttk.Button(r, text='Test lookup (W1AW)', command=self._test_lookup).pack(side='left')
        self.lbl_on = tk.Label(r, text='', bg=BG, fg=ACC, font=(UI, -16))
        self.lbl_on.pack(side='left', padx=8)
        tk.Label(t, text='Passwords go in the Windows Credential Manager (or config.json on this PC if there '
                         'is no credential store).', bg=BG, fg=DIM,
                 font=(UI, -16)).pack(anchor='w', padx=10)

        bf = tk.Frame(w, bg=BG)
        bf.pack(fill='x', pady=(0, 10))
        ttk.Button(bf, text='Save', style='Send.TButton', command=self.save).pack(side='right', padx=10)
        ttk.Button(bf, text='Cancel', command=self.cancel).pack(side='right')
        w.protocol('WM_DELETE_WINDOW', self.cancel)

        self._refresh_ports()
        self._load_models()
        self._radio_vis()
        self._warn()

    # ---------- widget helpers ----------
    def _tab(self, nb, title):
        f = tk.Frame(nb, bg=BG)
        nb.add(f, text=title)
        return f

    def _row(self, parent, label):
        r = tk.Frame(parent, bg=BG)
        r.pack(fill='x', padx=10, pady=3)
        tk.Label(r, text=label, bg=BG, fg=FG, width=32, anchor='w', font=(UI, -16)).pack(side='left')
        return r

    def _entry(self, parent, key, label, width=40, show=''):
        r = self._row(parent, label)
        self.v[key] = tk.StringVar(value=str(self.c[key]))
        tk.Entry(r, textvariable=self.v[key], bg=PANEL, fg=FG, insertbackground=FG,
                 relief='flat', width=width, show=show).pack(side='left', ipady=2)

    def _check(self, parent, key, label):
        r = self._row(parent, label)
        self.v[key] = tk.BooleanVar(value=bool(self.c[key]))
        ttk.Checkbutton(r, variable=self.v[key]).pack(side='left')

    def _test_lookup(self):
        c = self._collect()
        lk = ON.Lookup(c)
        if not lk.enabled():
            self.lbl_on.config(text='enter QRZ or HamQTH login first')
            return
        self.lbl_on.config(text='looking up...')

        def job():
            try:
                d = lk.lookup('W1AW')
                msg = f"[OK] {d.get('source')}: {d.get('full_name') or d.get('name')} {d.get('qth', '')}"
            except Exception as e:
                msg = f'[!!] {e}'
            self.w.after(0, lambda: self.lbl_on.config(text=msg[:70]))
        threading.Thread(target=job, daemon=True).start()

    def _combo(self, parent, key, label, values, cur, width=20, cb=None):
        r = self._row(parent, label)
        self.v[key] = tk.StringVar(value=cur)
        c = ttk.Combobox(r, textvariable=self.v[key], values=values, width=width, state='readonly')
        c.pack(side='left')
        if cb:
            c.bind('<<ComboboxSelected>>', lambda e: cb())
        return c

    @staticmethod
    def _strip_port(var):
        var.set(var.get().split(' ')[0])

    def _show_hlver(self):
        import hamlibupd
        d = self.v['hamlib_dir'].get()
        v = hamlibupd.installed_version(d) if d else None
        self.lbl_hlver.config(text=v or 'not found', fg=FG if v else TX)

    def _update_hamlib(self):
        import threading
        import hamlibupd
        self.btn_hlupd.state(['disabled'])
        say = lambda m: self.w.after(0, lambda: self.lbl_hlver.config(text=m[:70], fg=ACC))

        def work():
            try:
                ver, bindir = hamlibupd.update(say)
            except Exception as e:
                msg = str(e)
                self.w.after(0, lambda: (self.btn_hlupd.state(['!disabled']), self._show_hlver(),
                                         messagebox.showerror('Update Hamlib', msg, parent=self.w)))
                return

            def done():
                self.btn_hlupd.state(['!disabled'])
                self.v['hamlib_dir'].set(bindir)
                self._load_models()
                self._show_hlver()
                messagebox.showinfo('Update Hamlib', f'Hamlib {ver} is installed and selected.\n'
                                    'Press Save; the radio link restarts with the new version.', parent=self.w)
            self.w.after(0, done)
        threading.Thread(target=work, daemon=True, name='hamlib-update').start()

    def _browse(self):
        from tkinter import filedialog
        d = filedialog.askdirectory(parent=self.w, title='Folder containing rigctld.exe')
        if d:
            self.v['hamlib_dir'].set(d)
            self._load_models()

    def _refresh_ports(self):
        ports = [f'{p}  {d}' for p, d in RC.list_com_ports()]
        for cb in (self.cb_cat, self.cb_key, self.cb_ptt):
            cb['values'] = ports

    def _load_models(self):
        self.models = RC.list_hamlib_models(self.v['hamlib_dir'].get())
        self._filter_models()
        want = int(self.c['rig_model'])
        for m in self.models:
            if m[0] == want:
                self.v_model.set(self._mlabel(m))
                break
        self._preview()

    @staticmethod
    def _mlabel(m):
        return f'{m[1]} {m[2]}   (#{m[0]})'

    def _filter_models(self):
        q = self.v_search.get().strip().lower()
        rows = [m for m in self.models if q in f'{m[1]} {m[2]} {m[0]}'.lower()] if q else self.models
        self.cb_model['values'] = [self._mlabel(m) for m in rows]
        if q and rows and len(rows) <= 3:
            self.v_model.set(self._mlabel(rows[0]))
            self._preview()

    def _model_no(self):
        m = re.search(r'#(\d+)\)', self.v_model.get())
        return int(m.group(1)) if m else int(self.c['rig_model'])

    def _radio_vis(self):
        b = _val(CAT_CHOICES, self.v['cat_backend'].get())
        for f in (self.f_hl, self.f_ex, self.f_om):
            f.pack_forget()
        {'hamlib_launch': self.f_hl, 'hamlib_existing': self.f_ex, 'omnirig': self.f_om}.get(
            b, tk.Frame(self.t_radio)).pack(fill='x')
        self._preview()
        self._warn()

    def _preview(self):
        try:
            c = self._collect()
            if c['cat_backend'] == 'hamlib_launch':
                cmd = RC.make_rig(c).command_line()
                self.lbl_cmd.config(text='Runs: ' + ' '.join(f'"{x}"' if ' ' in x else x for x in cmd))
        except Exception:
            pass

    def _warn(self):
        try:
            c = self._collect()
        except Exception:
            return
        msgs = []
        catport = c['cat_port'] if c['cat_backend'] == 'hamlib_launch' else None
        if c['key_method'] == 'rigkeyer' and c['cat_backend'] in ('omnirig', 'none'):
            msgs.append('OmniRig / no-CAT cannot send CW text - choose Serial or Audio keying.')
        if c['key_method'] == 'serial':
            if catport and c['key_port'] == catport:
                msgs.append(f'{catport} is the CAT port. Key on the radio\'s second (Standard) COM port, '
                            'and set the radio\'s PC KEYING menu to the same line.')
            else:
                msgs.append(f'Set the radio\'s PC KEYING menu to {c["key_line"]}. Turn break-in ON.')
        if c['key_method'] == 'audio':
            msgs.append('Radio in DATA-U (or USB) with USB audio input selected. Keep ALC at zero.')
            if c['ptt_method'] == 'VOX':
                msgs.append('PTT is None - the radio\'s VOX must be on, or pick CAT / RTS / DTR.')
        if c['ptt_method'] == 'CAT' and c['cat_backend'] == 'none':
            msgs.append('CAT PTT needs a rig control backend.')
        if c['ptt_method'] in ('RTS', 'DTR') and catport and c['ptt_port'] == catport:
            msgs.append('PTT line on the CAT port: set rigctld\'s matching line to Default so it is not fought over.')
        self.lbl_warn.config(text='\n'.join('* ' + m for m in msgs))

    # ---------- collect / save ----------
    def _collect(self):
        c = dict(self.c)
        g = lambda k: self.v[k].get()
        c['cat_backend'] = _val(CAT_CHOICES, g('cat_backend'))
        c['key_method'] = _val(KEY_CHOICES, g('key_method'))
        c['ptt_method'] = _val(PTT_CHOICES, g('ptt_method'))
        c['cat_handshake'] = _val(HS_CHOICES, g('cat_handshake'))
        for k in ('call', 'grid', 'name', 'qth'):
            c[k] = g(k).strip().upper()
        c['license_class'] = g('license_class')
        c['audio_in_ch'], c['audio_out_ch'] = g('audio_in_ch'), g('audio_out_ch')
        c['hamlib_ptt'] = g('hamlib_ptt')
        c['tx_drive'] = int(g('tx_drive'))
        c['ptt_lead_ms'], c['ptt_tail_ms'] = int(g('ptt_lead_ms')), int(g('ptt_tail_ms'))
        for k in ('hamlib_dir', 'cat_port', 'cat_databits', 'cat_stopbits', 'cat_dtr', 'cat_rts',
                  'rigctld_host', 'key_port', 'key_line', 'ptt_port', 'audio_in', 'audio_out',
                  'sidetone_out', 'gt_host', 'log_path'):
            c[k] = g(k).split('  ')[0].strip() if k.endswith('port') and k != 'rigctld_port' else g(k).strip()
        c['cat_baud'] = int(g('cat_baud'))
        c['rigctld_port'] = int(g('rigctld_port_ex') if c['cat_backend'] == 'hamlib_existing'
                                else g('rigctld_port'))
        c['omnirig_rig'] = int(g('omnirig_rig'))
        c['min_wpm'], c['max_wpm'] = int(g('min_wpm')), int(g('max_wpm'))
        c['gt_enable'] = bool(g('gt_enable'))
        c['gt_port'] = int(g('gt_port'))
        for k in ('earcons_on', 'earcons_startup_only', 'earcon_speak', 'rec_autosave', 'auto_lookup', 'auto_qrz_upload',
                  'auto_lotw_upload'):
            c[k] = bool(g(k))
        c['earcon_pitch'], c['earcon_wpm'] = int(g('earcon_pitch')), int(g('earcon_wpm'))
        c['earcon_vol'] = float(g('earcon_vol'))
        c['rec_seconds'] = max(30, int(g('rec_seconds')))
        for k in ('chat_rig', 'chat_ant', 'chat_pwr', 'chat_age', 'chat_lic', 'tqsl_location'):
            c[k] = g(k).strip().upper() if k.startswith('chat') else g(k).strip()
        for k in ('qrz_user', 'qrz_pass', 'qrz_logbook_key', 'hamqth_user', 'hamqth_pass', 'lotw_user',
                  'lotw_pass', 'tqsl_path'):
            c[k] = g(k).strip()
        c['rig_model'] = self._model_no()
        lbl = self.v_model.get()
        c['rig_model_name'] = lbl.split('   (#')[0] if lbl else c['rig_model_name']
        return c

    def _tune(self):
        c = self._collect()
        self.app.cfg.update(c)
        self.app._apply_tx_backend()
        self.app.tx.tone = self.app.v_tone.get()
        self.app.tx.tune(10)

    def test_cat(self):
        try:
            c = self._collect()
        except ValueError as e:
            self.lbl_test.config(text=f'Bad value: {e}')
            return
        self.lbl_test.config(text='Testing...')
        self.w.update_idletasks()
        try:
            self.app.rig.close()          # free the COM port / stop our rigctld
        except Exception:
            pass
        rig = RC.make_rig(c)
        try:
            rig.connect()
            f = rig.get_freq()
            m, _ = rig.get_mode()
            self.lbl_test.config(text=f'[OK] {rig.name}: {f / 1e6:.6f} MHz  {m}', fg=RX)
        except Exception as e:
            self.lbl_test.config(text=f'[!!] {e}', fg=TX)
        finally:
            rig.close()

    def save(self):
        try:
            new = self._collect()
        except ValueError as e:
            messagebox.showerror('Rig Setup', f'Bad value: {e}', parent=self.w)
            return
        a = self.app
        a.cfg.update(new)
        save_cfg(a.cfg)
        a._apply_tx_backend()
        a._refresh_station()
        a.start_audio()
        a.rec.set_rate(a.fs, new['rec_seconds'])
        a._refresh_suggestion(force=True)
        self.w.destroy()

    def cancel(self):
        self.app._apply_tx_backend()     # restore whatever Test CAT closed
        self.w.destroy()


QUICK_START = [
    ('FIRST TIME', [
        'Setup Wizard (top right) walks you through: your call, radio, COM ports, audio, keying.',
        'Radio in CW mode, 500 Hz filter, AGC FAST, DNF / NB / DNR off. Tune your antenna for the band.',
        'Turn the radio audio up until the RX level bar sits in the middle (not CLIP).',
    ]),
    ('COPYING A STATION', [
        'Find CW on the band (e.g. 7.030-7.060, 14.030-14.060). The A box auto-picks the strongest signal.',
        'Or click a signal on the spectrum to lock onto it. Text prints about 2 s behind the audio.',
        'PLAIN ENGLISH under the copy says what the shorthand means. The THEM panel fills in call, RST, name, QTH.',
        'CLEAR wipes the copy, plain English and ON FREQ list.',
    ]),
    ('MAKING A CONTACT', [
        'The NEXT box writes your reply for you. F1 sends it at their speed (Match their speed).',
        'F2 CQ   F3 answer   F4 report   F5 73   F6 AGN?   F7 QRS (slower)   F8 your call.',
        'No F-keys (uConsole, small keyboards)? Ctrl+1 ... Ctrl+8 do the same as F1 ... F8.',
        'Esc stops sending. LOG (Ctrl+L) saves the QSO; NEW (Ctrl+N) starts the next one.',
        'Nothing transmits until you press a key, and never outside your license class CW segments.',
    ]),
    ('NO RADIO YET?', [
        'Click SIM: practice stations call CQ and answer you through the speakers. Nothing goes on the air.',
        'PRACTICE tab: key it yourself and get your fist graded.',
    ]),
    ('IF SOMETHING IS WRONG', [
        'Nothing decodes: check the RX level bar, the tone box, and that the radio is in CW mode.',
        'Not transmitting: Rig Setup > keying. Yaesu FTX-1 = DTR on the Standard COM port, break-in on.',
        'Errors are written to zbm2cw_debug.log in the settings folder.',
    ]),
]


class HelpWindow:
    def __init__(self, root):
        w = self.w = tk.Toplevel(root)
        w.title('ZBM2 CW Tool - Quick start')
        w.configure(bg=BG)
        w.geometry('820x620')
        t = tk.Text(w, bg='#000000', fg=FG, wrap='word', font=(UI, -16), relief='flat', padx=14, pady=10)
        t.pack(fill='both', expand=True)
        t.tag_configure('h', foreground=BG, background=ACC, font=(UI, -18))
        t.tag_configure('dim', foreground=DIM)
        for head, lines in QUICK_START:
            t.insert('end', f' {head} ', 'h')
            t.insert('end', '\n')
            for ln in lines:
                t.insert('end', '  * ' + ln + '\n')
            t.insert('end', '\n')
        t.insert('end', 'Hover over anything on the main screen for a tip.\n'
                        'Full manual: https://github.com/ko6ejv/zbm2-cw-tool#readme\n', 'dim')
        t.configure(state='disabled')
        bar = tk.Frame(w, bg=BG)
        bar.pack(fill='x', pady=6)
        ttk.Button(bar, text='Open full manual', command=lambda: __import__('webbrowser').open(
            'https://github.com/ko6ejv/zbm2-cw-tool#readme')).pack(side='left', padx=8)
        ttk.Button(bar, text='Close', command=w.destroy).pack(side='right', padx=8)


class SetupWizard:
    """Plain-English first-run setup for brand-new CW operators."""
    STEPS = ['You', 'Your gear', 'Connect', 'Listen test', 'Transmit test', 'Extras', 'Done']

    def __init__(self, app):
        self.app = app
        self.c = dict(app.cfg)
        w = self.w = tk.Toplevel(app.root)
        w.title('Setup Wizard - ZBM2 CW Tool')
        w.configure(bg=BG)
        w.geometry('840x720')
        w.transient(app.root)
        w.grab_set()
        top = tk.Frame(w, bg=BG)
        top.pack(fill='x', padx=16, pady=(12, 0))
        try:
            tk.Label(top, image=app._logo, bg=BG).pack(side='left')
        except Exception:
            pass
        self.lbl_steps = tk.Label(top, bg=BG, fg=DIM, font=(UI, -16), justify='left', wraplength=640)
        self.lbl_steps.pack(side='left', padx=12)
        self.body = tk.Frame(w, bg=BG)
        self.body.pack(fill='both', expand=True, padx=20, pady=10)
        nav = tk.Frame(w, bg=BG)
        nav.pack(fill='x', padx=16, pady=12)
        self.btn_back = ttk.Button(nav, text='< BACK', command=self.back)
        self.btn_back.pack(side='left')
        ttk.Button(nav, text='Advanced setup...', command=self._advanced).pack(side='left', padx=8)
        self.btn_next = ttk.Button(nav, text='NEXT >', style='Send.TButton', command=self.next)
        self.btn_next.pack(side='right')
        self.v = {}
        self.step = 0
        self._polling = False
        w.protocol('WM_DELETE_WINDOW', self.close)
        self.show()

    # ---------- helpers ----------
    def h1(self, t):
        tk.Label(self.body, text=f' {t.upper()} ', bg=ACC, fg=BG, font=(PIX, -16), wraplength=760,
                 justify='left').pack(anchor='w', pady=(0, 10))

    def p(self, t, fg=DIM, size=10):
        tk.Label(self.body, text=t, bg=BG, fg=fg, font=(UI, -16), justify='left',
                 wraplength=700).pack(anchor='w', pady=(2, 6))

    def field(self, key, label, hint='', width=20, values=None):
        r = tk.Frame(self.body, bg=BG)
        r.pack(fill='x', pady=4)
        tk.Label(r, text=label, bg=BG, fg=FG, width=22, anchor='w', font=(UI, -16)).pack(side='left')
        var = self.v.get(key) or tk.StringVar(value=str(self.c.get(key, '')))
        self.v[key] = var
        if values is not None:
            wdg = ttk.Combobox(r, textvariable=var, values=values, width=width, state='readonly')
        else:
            wdg = tk.Entry(r, textvariable=var, bg=PANEL, fg=FG, insertbackground=FG, relief='flat',
                           width=width, font=(UI, -16), show='*' if 'pass' in key else '')
        wdg.pack(side='left', ipady=3)
        if hint:
            tk.Label(r, text=hint, bg=BG, fg=DIM, font=(UI, -16)).pack(side='left', padx=8)
        return wdg

    def _sync(self):
        for k, var in self.v.items():
            val = var.get()
            if isinstance(val, bool):
                pass
            elif k in ('call', 'grid', 'name', 'qth'):
                val = val.strip().upper()
            elif k.endswith('port') and k != 'rigctld_port':
                val = val.split('  ')[0].strip()
            elif k in ('cat_baud', 'rigctld_port', 'tx_drive'):
                val = int(val)
            self.c[k] = val

    def _install_hamlib(self, btn):
        import threading
        import hamlibupd
        btn.state(['disabled'])
        say = lambda m: self.w.after(0, lambda: self.lbl_hl.config(text=m))

        def work():
            try:
                _v, bindir = hamlibupd.update(say)
            except Exception as e:
                msg = str(e)
                self.w.after(0, lambda: (btn.state(['!disabled']), self.lbl_hl.config(text=msg, fg=TX)))
                return
            self.w.after(0, lambda: (self.c.__setitem__('hamlib_dir', bindir), self._sync(), self.show()))
        threading.Thread(target=work, daemon=True, name='hamlib-update').start()

    def show(self):
        self._polling = False
        for wdg in self.body.winfo_children():
            wdg.destroy()
        self.lbl_steps.config(text='  '.join((f'[{i + 1}]' if i == self.step else f' {i + 1} ') + s
                                             for i, s in enumerate(self.STEPS)))
        self.btn_back.config(state='normal' if self.step else 'disabled')
        self.btn_next.config(text='FINISH' if self.step == len(self.STEPS) - 1 else 'NEXT >',
                             state='normal')
        getattr(self, f'step{self.step}')()

    def back(self):
        self._sync()
        self.step -= 1
        self.show()

    def next(self):
        self._sync()
        err = getattr(self, f'check{self.step}', lambda: None)()
        if err:
            messagebox.showwarning('One more thing', err, parent=self.w)
            return
        if self.step == len(self.STEPS) - 1:
            return self.finish()
        if self.step == 2:
            self._apply_live()
        self.step += 1
        self.show()

    def _apply_live(self):
        a = self.app
        a.cfg.update(self.c)
        a._apply_tx_backend()
        a.start_audio()
        a._refresh_station()

    def _advanced(self):
        self._sync()
        self._apply_live()
        self.close()
        SettingsDialog(self.app)

    def close(self):
        self._polling = False
        self.w.destroy()

    # ---------- step 0: you ----------
    def step0(self):
        self.h1('Welcome! Let\'s get you on CW.')
        self.p('This program listens to your radio, turns the beeps into letters, and writes your next '
               'reply for you. You press F1 and it sends it - at the same speed the other person is using. '
               'First, a few things about you. These go out in your messages.', FG, 11)
        self.field('call', 'Your callsign', 'e.g. KO6EJV')
        self.field('name', 'Your first name', 'what other hams will call you')
        self.field('qth', 'Your town', 'e.g. LA MESA CA')
        self.field('grid', 'Grid square', '4 or 6 characters, e.g. DM12 or DM12LS')
        self.field('license_class', 'License class', 'we won\'t let you transmit outside it',
                   18, Q.LICENSE_CLASSES)
        self.p('Don\'t know your grid square? Look up your callsign on qrz.com - it\'s listed on your page. '
               'It\'s just a short code for where you are on the map.')

    def check0(self):
        if not re.fullmatch(r'(?:[A-Z0-9]{1,4}/)?[A-Z0-9]{1,3}\d[A-Z]{1,4}(?:/[A-Z0-9]{1,4})?', self.c['call']):
            return 'That callsign doesn\'t look right. Example: KO6EJV'
        if self.c['grid'] and not re.fullmatch(r'[A-R]{2}\d{2}(?:[A-X]{2})?', self.c['grid']):
            return 'Grid squares look like DM12 or DM12LS (two letters, two numbers, optional two letters).'

    # ---------- step 1: gear ----------
    def step1(self):
        self.h1('What radio setup do you have?')
        self.p('Pick the one that sounds most like yours. You can change it later.', FG, 11)
        self.v_prof = getattr(self, 'v_prof', tk.StringVar(value=self.c.get('profile') or 'usbrig'))
        for key, pr in PROFILES.items():
            f = tk.Frame(self.body, bg=PANEL)
            f.pack(fill='x', pady=4)
            tk.Radiobutton(f, variable=self.v_prof, value=key, text=pr['title'], bg=PANEL, fg=FG,
                           selectcolor=BG, activebackground=PANEL, activeforeground=ACC,
                           font=(UI, -16), anchor='w').pack(fill='x', padx=8, pady=(6, 0))
            tk.Label(f, text=pr['sub'], bg=PANEL, fg=DIM, justify='left',
                     font=(UI, -16)).pack(anchor='w', padx=34, pady=(0, 6))

    def check1(self):
        key = self.v_prof.get()
        if self.c.get('profile') != key:
            apply_profile(self.c, key)
            for k in list(self.v):            # re-read fields from new profile values
                if k not in ('call', 'name', 'qth', 'grid'):
                    del self.v[k]

    # ---------- step 2: connect ----------
    def step2(self):
        prof = self.c.get('profile')
        self.h1('Plug everything in')
        ports = [f'{p}  {d}' for p, d in RC.list_com_ports()]
        ins, outs = dev_labels('input'), dev_labels('output')
        if prof in ('digirig_hf', 'digirig_ht'):
            com, _ = RC.find_digirig()
            self.p(('[OK] Found your DigiRig on ' + com) if com else
                   '[!!] DigiRig not found. Plug it in (USB), wait 10 seconds, then press Refresh. '
                   'If it never shows up, install the "CP210x" driver from digirig.net.',
                   RX if com else TX, 11)
        if prof == 'rigctld':
            self.p('If rigctld is already running we use it. If not, we start it with the settings below.', DIM, 11)
        if prof in ('usbrig', 'rigctld'):
            bindir = RC.find_hamlib_bin(self.c.get('hamlib_dir', ''))
            if not bindir:
                self.p('[!!] Hamlib isn\'t installed. It\'s the free radio-control program most ham software uses. '
                       'Press Install Hamlib now (downloads the official release, about 3 MB on Windows; on Linux '
                       'it is built, about 15 minutes on a Raspberry Pi).', TX, 11)
                self.lbl_hl = tk.Label(self.body, text='', bg=BG, fg=ACC, font=(UI, -16), anchor='w')
                bi = ttk.Button(self.body, text='Install Hamlib now', command=lambda: self._install_hamlib(bi))
                bi.pack(anchor='w', pady=4)
                self.lbl_hl.pack(anchor='w')
            else:
                self.c['hamlib_dir'] = bindir
                models = RC.list_hamlib_models(bindir)
                self._models = models
                labels = [f'{m[1]} {m[2]}   (#{m[0]})' for m in models]
                cur = next((l for l, m in zip(labels, models) if m[0] == int(self.c['rig_model'])), '')
                r = tk.Frame(self.body, bg=BG)
                r.pack(fill='x', pady=4)
                tk.Label(r, text='Your radio', bg=BG, fg=FG, width=22, anchor='w',
                         font=(UI, -16)).pack(side='left')
                self.v_find = tk.StringVar()
                ef = tk.Entry(r, textvariable=self.v_find, bg=PANEL, fg=FG, insertbackground=FG,
                              relief='flat', width=10, font=(UI, -16))
                ef.pack(side='left', ipady=3)
                self.v_model = tk.StringVar(value=cur)
                cb = ttk.Combobox(r, textvariable=self.v_model, values=labels, width=36, state='readonly')
                cb.pack(side='left', padx=6)

                def filt(_e=None):
                    q = self.v_find.get().lower()
                    vals = [l for l in labels if q in l.lower()]
                    cb['values'] = vals
                    if vals:
                        self.v_model.set(vals[0])
                ef.bind('<KeyRelease>', filt)
                tk.Label(self.body, text='Type part of the model (FTX, 991, 7300) to search.', bg=BG, fg=DIM,
                         font=(UI, -16)).pack(anchor='w', padx=4)
            self.field('cat_port', 'Radio\'s COM port', 'Yaesu shows two: pick "Enhanced"', 40, ports)
            self.field('cat_baud', 'Speed (baud)', 'must match the radio\'s CAT RATE menu', 10,
                       ['4800', '9600', '19200', '38400', '115200'])
        if prof == 'digirig_hf':
            self.field('cat_port', 'DigiRig COM port', '', 40, ports)
            self.field('cat_baud', 'Speed (baud)', 'must match the radio\'s CAT RATE menu (FT-857: 4800/9600)',
                       10, ['4800', '9600', '19200', '38400'])
            self.p('Radio model is set on the Advanced setup page (Radio / CAT tab) - default works for testing.')
        if prof == 'digirig_ht':
            self.field('ptt_port', 'DigiRig COM port', 'PTT uses this', 40, ports)
        if prof == 'rigctld':
            self.field('rigctld_host', 'rigctld computer', 'usually 127.0.0.1 (this PC)')
            self.field('rigctld_port', 'rigctld port', 'usually 4532')
        self.field('audio_in', 'Sound from radio', 'radio -> PC', 48, ins)
        self.field('audio_out', 'Sound to radio', 'PC -> radio (for tone CW)', 48, outs)
        self.p('Tip: the radio\'s sound card is usually called "USB Audio CODEC" (Yaesu / Icom) or '
               '"USB PnP Sound Device" (DigiRig). Pick the [MME] one if unsure. Pro tip: rename them in '
               'Windows Sound settings to short names like FTXmic / FTXspk so they\'re easy to spot.')
        ttk.Button(self.body, text='Refresh', command=lambda: (self._sync(), self.show())).pack(anchor='w')

    def check2(self):
        if hasattr(self, 'v_model') and self.v_model.get():
            m = re.search(r'#(\d+)\)', self.v_model.get())
            if m:
                self.c['rig_model'] = int(m.group(1))
                self.c['rig_model_name'] = self.v_model.get().split('   (#')[0]
        if not self.c.get('audio_in'):
            return 'Pick the sound card that carries audio FROM your radio.'
        if self.c['key_method'] == 'audio' and not self.c.get('audio_out'):
            return 'Pick the sound card that sends audio TO your radio.'

    # ---------- step 3: listen ----------
    def step3(self):
        ht = self.c.get('profile') == 'digirig_ht'
        self.h1('Listen test')
        if ht:
            self.p('Turn the HT\'s volume to about half. Have a friend send tone CW on a simplex frequency, '
                   'or play a CW practice recording near the radio.', FG, 11)
        else:
            self.p('Put the radio in CW mode and tune to where CW lives: 7.030-7.050 MHz or 14.030-14.060 MHz. '
                   'W1AW sends code practice on a schedule (arrl.org -> W1AW). Turn off any noise blanker '
                   'for now.', FG, 11)
        self.p('The bar should sit in the GREEN middle. If it says CLIP, turn the level down in '
               'Windows Sound settings (or the radio\'s USB OUT level menu). If it says low, turn it up.')
        self.cv = tk.Canvas(self.body, width=600, height=26, bg='#000000', highlightthickness=0)
        self.cv.pack(anchor='w', pady=6)
        self.lbl_l = tk.Label(self.body, text='', bg=BG, fg=FG, font=(UI, -16))
        self.lbl_l.pack(anchor='w')
        ttk.Button(self.body, text='Open Windows Sound settings',
                   command=lambda: os.system('start mmsys.cpl') if os.name == 'nt' else None).pack(anchor='w', pady=4)
        tk.Label(self.body, text='What we\'re hearing:', bg=BG, fg=DIM).pack(anchor='w', pady=(10, 0))
        self.lbl_rx = tk.Label(self.body, text='', bg='#000000', fg=RX, font=(UI, -24),
                               anchor='w', width=60, height=3, justify='left', wraplength=680)
        self.lbl_rx.pack(anchor='w')
        self.lbl_w = tk.Label(self.body, text='', bg=BG, fg=ACC, font=(UI, -16))
        self.lbl_w.pack(anchor='w', pady=4)
        self.app.last_rx = ''
        self._polling = True
        self._poll_listen()

    def _poll_listen(self):
        if not self._polling or not self.w.winfo_exists():
            return
        rms, pk = getattr(self.app, 'last_level', (-90, -90))
        frac = min(1, max(0, (rms + 70) / 70))
        ok = -45 <= rms and pk < -1
        col = TX if pk >= -1 else RX if ok else '#5a3a00'
        self.cv.delete('all')
        x1, x2 = (25 / 70) * 600, (60 / 70) * 600
        self.cv.create_rectangle(x1, 0, x2, 26, fill='#2a1a00', width=0)
        self.cv.create_rectangle(0, 4, frac * 600, 22, fill=col, width=0)
        self.lbl_l.config(text='CLIP - turn it DOWN' if pk >= -1 else 'Good level [OK]' if ok
                          else 'Too low - turn it UP (or nothing connected?)', fg=col if col != '#5a3a00' else FG)
        self.lbl_rx.config(text=getattr(self.app, 'last_rx', '')[-120:])
        d = self.app.decoder
        if d and d.locked:
            self.lbl_w.config(text=f'Copying at about {d.wpm:.0f} WPM, tone {d.tone:.0f} Hz [OK]')
        self.w.after(100, self._poll_listen)

    # ---------- step 4: transmit ----------
    def step4(self):
        self.h1('Transmit test')
        km = self.c['key_method']
        self.p('This makes your radio transmit. Before you press anything:', FG, 11)
        self.p('  *  Antenna (or a dummy load) is connected.\n'
               '  *  You\'re on a frequency your license allows, in the CW part of the band (or a simplex '
               'channel on an HT).\n'
               '  *  Listen first and send "QRL?" - make sure nobody is using it.', FG, 11)
        self.v_ok = tk.BooleanVar(value=False)
        tk.Checkbutton(self.body, text='Yes - antenna connected and the frequency is clear', variable=self.v_ok,
                       bg=BG, fg=ACC, selectcolor=BG, activebackground=BG, font=(UI, -16),
                       command=self._ok_changed).pack(anchor='w', pady=6)
        r = tk.Frame(self.body, bg=BG)
        r.pack(anchor='w', pady=6)
        self.b_test = ttk.Button(r, text=f'Send test:  VVV DE {self.c["call"]}', style='Send.TButton',
                                 command=self._send_test, state='disabled')
        self.b_test.pack(side='left')
        self.b_tune = ttk.Button(r, text='Tune (steady tone 10 s)', command=self._tune, state='disabled')
        if km == 'audio':
            self.b_tune.pack(side='left', padx=8)
        ttk.Button(r, text='STOP', style='Stop.TButton', command=self.app.tx.stop).pack(side='left', padx=8)
        if km == 'audio':
            self.p('Tone CW: start with Tune. Slide "Drive" until power is full and the radio\'s ALC shows '
                   'nothing (HF), or until a friend says it sounds clean (HT). Then Send test.')
            self.v['tx_drive'] = self.v.get('tx_drive') or tk.IntVar(value=int(self.c['tx_drive']))
            rr = tk.Frame(self.body, bg=BG)
            rr.pack(anchor='w')
            tk.Label(rr, text='Drive', bg=BG, fg=FG, font=(UI, -16)).pack(side='left')
            tk.Scale(rr, from_=0, to=100, orient='horizontal', variable=self.v['tx_drive'], length=320,
                     bg=BG, fg=FG, troughcolor=PANEL, highlightthickness=0,
                     command=lambda _v: self._live_drive()).pack(side='left', padx=8)
        else:
            self.p('The radio\'s own keyer sends this. Turn break-in (BK-IN) ON so it transmits by itself. '
                   'If you get "rig rejected send_morse", open Advanced setup -> CW Keying and choose '
                   'Serial or Audio.')
        self.lbl_tx = tk.Label(self.body, text='', bg=BG, fg=TX, font=(UI, -16))
        self.lbl_tx.pack(anchor='w', pady=6)

    def _ok_changed(self):
        st = 'normal' if self.v_ok.get() else 'disabled'
        self.b_test.config(state=st)
        self.b_tune.config(state=st)

    def _live_drive(self):
        self.app.cfg['tx_drive'] = int(self.v['tx_drive'].get())
        self.app.tx.cfg['tx_drive'] = self.app.cfg['tx_drive']

    def _send_test(self):
        self._sync()
        self._apply_live()
        self.app.tx.wpm = 15
        self.app.tx.tone = self.app.v_tone.get()
        self.app.tx.send(f'VVV DE {self.c["call"]}')
        self.lbl_tx.config(text='Sending at 15 WPM... listen for it / watch the TX light.')

    def _tune(self):
        self._sync()
        self._apply_live()
        self.app.tx.tone = self.app.v_tone.get()
        self.app.tx.tune(10)

    # ---------- step 5: extras (all optional) ----------
    def step5(self):
        self.h1('Extras (all optional - skip if you like)')
        self.p('Callsign lookups fill in the other station\'s name and location before they even send it.',
               FG, 11)
        self.field('qrz_user', 'QRZ username', 'needs a QRZ XML subscription for full data')
        self.field('qrz_pass', 'QRZ password')
        self.field('hamqth_user', 'HamQTH username', 'free alternative - hamqth.com')
        self.field('hamqth_pass', 'HamQTH password')
        self.p('Logbook of the World: install TrustedQSL (TQSL) from the ARRL and set up your certificate. '
               'Then the LOG tab can upload your contacts with one click.', FG, 11)
        self.field('lotw_user', 'LoTW username')
        self.field('lotw_pass', 'LoTW password')
        self.field('tqsl_location', 'TQSL station location', 'the name you gave it inside TQSL')
        self.p('Morse earcons: every sound this program makes is a real CW letter (K = your turn, '
               'R = sent, TU = logged). You\'ll learn them without trying.', FG, 11)
        self.v['earcons_on'] = self.v.get('earcons_on') or tk.BooleanVar(value=self.c.get('earcons_on', True))
        tk.Checkbutton(self.body, text='Yes, play Morse earcons', variable=self.v['earcons_on'], bg=BG, fg=ACC,
                       selectcolor=BG, activebackground=BG, font=(UI, -16)).pack(anchor='w')

    # ---------- step 6: done ----------
    def step6(self):
        self.h1('You\'re set up! Here\'s how a contact goes:')
        for t in [
            '1.  Listen. Letters appear in the big window. Their speed shows as THEIR SPEED at the top.',
            '2.  When someone sends CQ (= "anyone want to talk?"), the NEXT box fills in your answer. '
            'Press F1 to send it.',
            '3.  When they reply, their name, signal report and location fill in on the right. '
            'NEXT becomes your report - press F1 again.',
            '4.  When they say 73 (= "best regards"), NEXT becomes your goodbye. F1. Then Ctrl+L to log it.',
            '',
            'Anything wrong? Press Esc to stop transmitting instantly.',
            'Want them to slow down? Press F7 (QRS = "please send slower") or set the +/- speed box to -3.',
            'You can always edit the NEXT box before pressing F1.',
            'Not sure what the shorthand means? The PLAIN ENGLISH box translates everything they send, '
            'and MEANS> under the NEXT box tells you what you are about to send.',
            'More tabs: CHAT (old-school chat room over CW), CONTEST (one key per exchange), '
            'PRACTICE (key it yourself and get your fist graded), LOG (QRZ + LoTW uploads).',
            'Nervous? Click SIM (top right) or PRACTICE > Practice QSO: simulated stations answer you through '
            'your speakers and NOTHING goes on the air. Make a few practice contacts first!',
            'Technicians: your HF CW spots are 3.525-3.600, 7.025-7.125, 21.025-21.200 and 28.000-28.500 MHz. '
            'The app blocks TX outside your privileges when it can read the radio\'s frequency.',
        ]:
            self.p(t, FG if t[:1].isdigit() else ACC, 11)

    def finish(self):
        self.c['setup_done'] = True
        self.app.cfg.update(self.c)
        save_cfg(self.app.cfg)
        self.app._apply_tx_backend()
        self.app.start_audio()
        self.app._refresh_station()
        self.app._refresh_suggestion(force=True)
        self.close()


def main():
    if os.name == 'nt':
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('ZBM2.CWTool')
        except Exception:
            pass
    load_fonts()
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == '__main__':
    import main as _launcher           # boot screen + background loading
    _launcher.main()
