"""Shared look & feel: amber DOS theme, fonts, hover help."""
import os
import sys
import tkinter as tk
from tkinter import ttk

HERE = os.path.dirname(os.path.abspath(sys.argv[0]))
RES = getattr(sys, '_MEIPASS', HERE)

# ---- HAMKIT-style amber CRT theme ----
BG, PANEL, FG, DIM, ACC, RX, TX = '#000000', '#140b00', '#ffb000', '#8a5a00', '#ff8c00', '#ffb000', '#ff3c00'
DOS = 'Px437 IBM VGA 8x16'   # IBM VGA text-mode font (bundled, CC BY-SA 4.0, VileR / int10h.org)
UI = DOS
PIX = DOS


def load_fonts():
    """Register bundled fonts (Windows: this process only; Linux/macOS: user font folder)."""
    fdir = os.path.join(RES, 'fonts')
    if os.name != 'nt':
        import shutil
        import subprocess
        dest = os.path.expanduser('~/Library/Fonts' if sys.platform == 'darwin' else '~/.local/share/fonts')
        try:
            os.makedirs(dest, exist_ok=True)
            new = False
            for f in ('Px437_IBM_VGA_8x16.ttf', 'VT323-Regular.ttf', 'PressStart2P-Regular.ttf'):
                src, dst = os.path.join(fdir, f), os.path.join(dest, f)
                if os.path.exists(src) and not os.path.exists(dst):
                    shutil.copy(src, dst)
                    new = True
            if new and shutil.which('fc-cache'):
                subprocess.run(['fc-cache', '-f', dest], capture_output=True, timeout=30)
        except Exception:
            pass
        return
    if os.name == 'nt':
        import ctypes
        for f in ('Px437_IBM_VGA_8x16.ttf', 'VT323-Regular.ttf', 'PressStart2P-Regular.ttf'):
            p = os.path.join(fdir, f)
            if os.path.exists(p):
                ctypes.windll.gdi32.AddFontResourceExW(p, 0x10, 0)   # FR_PRIVATE


def apply_theme(root):
    st = ttk.Style(root)
    st.theme_use('clam')
    base = (UI, -16)
    st.configure('.', background=BG, foreground=FG, fieldbackground=PANEL, font=base,
                 bordercolor=DIM, lightcolor=BG, darkcolor=BG, troughcolor=PANEL,
                 selectbackground=ACC, selectforeground=BG, insertcolor=FG, arrowcolor=ACC)
    st.configure('TLabel', background=BG, foreground=FG)
    st.configure('Dim.TLabel', foreground=DIM)
    st.configure('Big.TLabel', font=(UI, -48), foreground=ACC)
    st.configure('TButton', background=BG, foreground=FG, bordercolor=FG, padding=(10, 2),
                 relief='solid', borderwidth=2, font=(UI, -16))
    st.map('TButton', background=[('active', '#2a1a00'), ('disabled', BG)],
           foreground=[('disabled', DIM)], bordercolor=[('disabled', DIM)])
    st.configure('Send.TButton', background=ACC, foreground=BG, bordercolor=ACC, font=(UI, -16))
    st.map('Send.TButton', background=[('active', '#ffb347'), ('disabled', '#3a2400')])
    st.configure('Stop.TButton', background=TX, foreground=BG, bordercolor=TX, font=(UI, -16))
    st.map('Stop.TButton', background=[('active', '#ff6a33')])
    st.configure('TCheckbutton', background=BG, foreground=FG, indicatorbackground=BG,
                 indicatorforeground=ACC, font=(UI, -16))
    st.map('TCheckbutton', background=[('active', BG)], indicatorbackground=[('selected', ACC)])
    st.configure('TCombobox', fieldbackground=PANEL, foreground=FG, background=BG,
                 bordercolor=DIM, arrowcolor=ACC, font=(UI, -16))
    st.map('TCombobox', fieldbackground=[('readonly', PANEL)], foreground=[('readonly', FG)],
           selectbackground=[('readonly', PANEL)], selectforeground=[('readonly', FG)])
    st.configure('TNotebook', background=BG, bordercolor=DIM, tabmargins=(4, 4, 4, 0))
    st.configure('TNotebook.Tab', background=BG, foreground=DIM, bordercolor=DIM,
                 padding=(12, 4), font=(UI, -16))
    st.map('TNotebook.Tab', background=[('selected', ACC)], foreground=[('selected', BG)])
    root.option_add('*TCombobox*Listbox.background', PANEL)
    root.option_add('*TCombobox*Listbox.foreground', FG)
    root.option_add('*TCombobox*Listbox.selectBackground', ACC)
    root.option_add('*TCombobox*Listbox.selectForeground', BG)
    root.option_add('*TCombobox*Listbox.font', (UI, -16))
    root.option_add('*Entry.highlightThickness', 1)
    root.option_add('*Entry.highlightBackground', DIM)
    root.option_add('*Entry.highlightColor', ACC)
    root.option_add('*Entry.disabledBackground', BG)
    root.option_add('*Spinbox.highlightThickness', 1)
    root.option_add('*Spinbox.highlightBackground', DIM)
    root.option_add('*Spinbox.font', (UI, -16))
    root.option_add('*Spinbox.insertBackground', FG)
    root.option_add('*Scale.font', (UI, -16))
    root.option_add('*Scale.activeBackground', ACC)
    root.option_add('*Radiobutton.highlightThickness', 0)
    root.option_add('*Checkbutton.highlightThickness', 0)




class Tip:
    """Hover help box (amber on black, DOS style)."""
    def __init__(self, widget, text):
        self.w, self.text, self.top = widget, text, None
        widget.bind('<Enter>', self.show, add='+')
        widget.bind('<Leave>', self.hide, add='+')

    def show(self, _e=None):
        if self.top:
            return
        x = self.w.winfo_rootx() + 10
        y = self.w.winfo_rooty() + self.w.winfo_height() + 4
        self.top = t = tk.Toplevel(self.w)
        t.wm_overrideredirect(True)
        t.wm_geometry(f'+{x}+{y}')
        tk.Label(t, text=self.text, bg=BG, fg=FG, font=(UI, -16), justify='left', wraplength=420,
                 padx=8, pady=6, highlightthickness=1, highlightbackground=ACC).pack()
        t.update_idletasks()                       # keep the box on screen
        x = min(x, self.w.winfo_screenwidth() - t.winfo_width() - 8)
        y = min(y, self.w.winfo_screenheight() - t.winfo_height() - 8)
        t.wm_geometry(f'+{max(0, x)}+{y}')

    def hide(self, _e=None):
        if self.top:
            self.top.destroy()
            self.top = None


HELP = {
    'rx_wpm': 'Their speed in WPM (words per minute), measured from their signal.',
    'tx_wpm': 'The speed YOU will send at. With "Match their speed" on, it copies theirs.',
    'offset': 'Send this many WPM faster (+) or slower (-) than them. -3 nudges them to slow down.',
    'manual': 'Speed used when "Match their speed" is off, or before we\'ve measured them.',
    'pitch': 'Tone pitch in Hz of the CW signal we\'re decoding. "Auto" finds it for you.',
    'snr': 'How far their signal sits above the background noise (S/N, in dB). Higher = easier copy.',
    'call': 'Their callsign - the station you\'re talking to.',
    'rst_rcvd': 'RST = signal report: Readability 1-5, Strength 1-9, Tone 1-9.\n'
                'This is the report THEY gave YOU. 599 means perfect.',
    'rst_sent': 'The RST report YOU give THEM. Filled in from how strong they are here; change it if you like.\n'
                'R = Readability 1-5, S = Strength 1-9, T = Tone 1-9.',
    'name': 'Their first name (they send it after NAME or OP).',
    'qth': 'QTH = their location (town / state). Comes from "QTH ...".',
    'grid': 'Grid square = map location code, e.g. FN31. Gives distance and beam heading.',
    'cq': 'CQ = "calling anyone". Starts a contact.',
    'answer': 'Answer a station that called CQ: THEIRCALL DE YOURCALL.',
    'report': 'Send your signal report (RST), name and location.',
    '73': '73 = "best regards". Ends the contact politely, with SK (end of contact).',
    'agn': 'AGN? = "say again?". Asks them to repeat.',
    'qrs': 'QRS = Q-code for "send more slowly".',
    'call_btn': 'Send just your callsign twice (to answer a "QRZ?" or break in).',
    'next': 'The reply we suggest for this point in the contact. Edit it if you like, then F1 sends it.',
}


