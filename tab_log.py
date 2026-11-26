"""LOG tab - your ADIF log, QRZ Logbook / LoTW uploads, LoTW confirmations, audio recordings."""
import os
import re
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import ttk

import online as ON
from ui_theme import BG, PANEL, FG, DIM, ACC, UI, Tip

COLS = (('QSO_DATE', 'DATE', 90), ('TIME_ON', 'UTC', 70), ('CALL', 'CALL', 110), ('BAND', 'BAND', 60),
        ('RST_SENT', 'SENT', 60), ('RST_RCVD', 'RCVD', 60), ('NAME', 'NAME', 100), ('QTH', 'QTH', 160),
        ('GRIDSQUARE', 'GRID', 80), ('CONTEST_ID', 'CONTEST', 100), ('LOTW', 'LoTW', 60))


def read_adif(path):
    """-> list of (fields dict, raw record text)"""
    if not os.path.exists(path):
        return []
    with open(path, encoding='utf-8', errors='replace') as f:
        text = f.read()
    body = re.split(r'<EOH>', text, flags=re.I)[-1]
    out = []
    for rec in re.split(r'<EOR>', body, flags=re.I):
        if not rec.strip():
            continue
        d = {m.group(1).upper(): m.group(3) for m in re.finditer(r'<(\w+):(\d+)(?::\w)?>([^<]*)', rec)}
        d = {k: v.strip() for k, v in d.items()}
        if d.get('CALL'):
            out.append((d, rec.strip() + ' <EOR>\n'))
    return out


class LogTab:
    def __init__(self, app, parent):
        self.app, self.cfg = app, app.cfg
        bar = tk.Frame(parent, bg=PANEL)
        bar.pack(fill='x', pady=(6, 0))
        tk.Label(bar, text=' LOGBOOK ', bg=ACC, fg=BG, font=(UI, -16)).pack(side='left', padx=(0, 8))
        self.lbl_count = tk.Label(bar, text='', bg=PANEL, fg=FG, font=(UI, -16))
        self.lbl_count.pack(side='left')
        for txt, cmd, tip in (
                ('REFRESH', self.refresh, 'Reload the log file.'),
                ('UPLOAD SELECTED > LoTW', self.lotw_selected,
                 'Signs the selected QSOs with your TQSL certificate and uploads them to Logbook of the World.'),
                ('UPLOAD SELECTED > QRZ', self.qrz_selected, 'Sends the selected QSOs to your QRZ Logbook.'),
                ('CHECK LoTW CONFIRMATIONS', self.check_lotw,
                 'Downloads your LoTW confirmations and marks them with a + here and in the buddy list.'),
                ('OPEN DATA FOLDER', self.open_folder, 'Recordings, chat logs and contest logs live here.')):
            b = ttk.Button(bar, text=txt, command=cmd)
            b.pack(side='right', padx=3, pady=4)
            Tip(b, tip)
        st = ttk.Style()
        st.configure('Log.Treeview', background='#000000', fieldbackground='#000000', foreground=FG,
                     font=(UI, -16), rowheight=20)
        st.configure('Log.Treeview.Heading', background=PANEL, foreground=DIM, font=(UI, -16))
        st.map('Log.Treeview', background=[('selected', ACC)], foreground=[('selected', BG)])
        self.tree = ttk.Treeview(parent, columns=[c[0] for c in COLS], show='headings', style='Log.Treeview',
                                 selectmode='extended')
        for key, lbl, w in COLS:
            self.tree.heading(key, text=lbl)
            self.tree.column(key, width=w)
        self.tree.pack(fill='both', expand=True, pady=(6, 0))
        self.lbl_hint = tk.Label(parent, text='Tip: Ctrl/Shift-click to select several QSOs. '
                                              'Set up QRZ / LoTW in Rig Setup > Online.',
                                 bg=BG, fg=DIM, font=(UI, -16), anchor='w')
        self.lbl_hint.pack(fill='x', side='bottom', before=self.tree)
        self.raw = {}
        self.refresh()

    def on_rx(self, chunk, snr):
        pass

    def on_show(self):
        self.refresh()

    def refresh(self):
        self.tree.delete(*self.tree.get_children())
        self.raw = {}
        recs = read_adif(self.cfg['log_path'])
        conf = 0
        for i, (d, raw) in enumerate(reversed(recs)):
            ok = (d.get('CALL', '').upper(), d.get('BAND', '').upper()) in self.app.lotw_conf
            conf += ok
            d = dict(d, LOTW='+' if ok else '')
            iid = f'q{i}'
            self.tree.insert('', 'end', iid=iid, values=[d.get(k, '') for k, _, _ in COLS])
            self.raw[iid] = raw
        self.lbl_count.config(text=f'{len(recs)} QSOs | {conf} confirmed on LoTW')

    def _selected_raw(self):
        sel = self.tree.selection()
        if not sel:
            self.app.set_status('Select one or more QSOs first', err=True)
        return [self.raw[i] for i in sel]

    def lotw_selected(self):
        recs = self._selected_raw()
        if not recs:
            return
        c = self.cfg
        tq = ON.find_tqsl(c.get('tqsl_path', ''))

        def job():
            ok, msg = ON.lotw_upload(tq, c.get('tqsl_location', ''), recs)
            self.app.ui_q.put(('online_msg' if ok else 'online_err', f'LoTW ({len(recs)} QSOs): {msg}'))
        threading.Thread(target=job, daemon=True).start()
        self.app.set_status(f'Signing {len(recs)} QSOs with TQSL...')

    def qrz_selected(self):
        recs = self._selected_raw()
        key = self.cfg.get('qrz_logbook_key', '')
        if not recs:
            return
        if not key:
            self.app.set_status('Add your QRZ Logbook API key in Rig Setup > Online', err=True)
            return

        def job():
            good, bad = 0, 0
            for r in recs:
                try:
                    ON.qrz_logbook_upload(key, r)
                    good += 1
                except Exception:
                    bad += 1
            self.app.ui_q.put(('online_msg' if not bad else 'online_err',
                               f'QRZ Logbook: {good} uploaded' + (f', {bad} failed' if bad else '')))
        threading.Thread(target=job, daemon=True).start()

    def check_lotw(self):
        c = self.cfg
        if not c.get('lotw_user'):
            self.app.set_status('Add your LoTW username/password in Rig Setup > Online', err=True)
            return

        def job():
            try:
                conf = ON.lotw_confirmations(c['lotw_user'], c['lotw_pass'])
                self.app.save_lotw_conf(conf)
                self.app.ui_q.put(('online_msg', f'LoTW: {len(conf)} confirmations downloaded'))
                self.app.root.after(0, self.refresh)
            except Exception as e:
                self.app.ui_q.put(('online_err', f'LoTW: {e}'))
        threading.Thread(target=job, daemon=True).start()
        self.app.set_status('Checking LoTW...')

    def open_folder(self):
        d = self.app.data_dir
        os.makedirs(d, exist_ok=True)
        if os.name == 'nt':
            os.startfile(d)
        else:
            subprocess.Popen(['xdg-open' if sys.platform.startswith('linux') else 'open', d])
