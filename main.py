"""ZBM2 CW Tool launcher: DOS boot screen up instantly, heavy modules load in the background."""
import os
import sys
import threading
import time
import tkinter as tk

from ui_theme import BG, ACC, FG, UI, RES, load_fonts

BOOT = ['ZBM2 CW TOOL', '(C) ZBM2 INDUSTRIES', '',
        'Memory test .......... 640K OK', 'Loading DSP engine ....',
        'Loading decoders .....', 'Loading rig control ...', 'Starting audio ........']


def _diagnostics(root):
    """Log uncaught errors and UI stalls to zbm2cw_debug.log (pythonw has no console)."""
    import faulthandler
    import traceback
    from config import HERE
    path = os.path.join(HERE, 'zbm2cw_debug.log')
    try:
        if os.path.exists(path) and os.path.getsize(path) > 2_000_000:
            os.replace(path, path + '.old')
        f = open(path, 'a', buffering=1, encoding='utf-8')
    except OSError:
        return
    f.write(f'\n==== start {time.strftime("%Y-%m-%d %H:%M:%S")} ====\n')

    def log_exc(kind, et, ev, tb):
        f.write(f'[{time.strftime("%H:%M:%S")}] {kind}:\n' + ''.join(traceback.format_exception(et, ev, tb)))
    sys.excepthook = lambda et, ev, tb: log_exc('uncaught', et, ev, tb)
    threading.excepthook = lambda a: log_exc(f'thread {a.thread.name}', a.exc_type, a.exc_value, a.exc_traceback)
    root.report_callback_exception = lambda et, ev, tb: log_exc('tk callback', et, ev, tb)

    beat = [time.monotonic()]

    def heartbeat():
        beat[0] = time.monotonic()
        root.after(250, heartbeat)
    root.after(250, heartbeat)

    def watchdog():
        dumped_for = None
        while True:
            time.sleep(0.5)
            lag = time.monotonic() - beat[0]
            if lag > 2.0 and dumped_for != beat[0]:
                dumped_for = beat[0]
                f.write(f'[{time.strftime("%H:%M:%S")}] UI stalled {lag:.1f}s - all threads:\n')
                faulthandler.dump_traceback(file=f, all_threads=True)
                f.flush()
    threading.Thread(target=watchdog, daemon=True, name='watchdog').start()


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
    root.configure(bg=BG)
    root.title('ZBM2 CW Tool')
    root.geometry('1320x900')
    try:
        root.iconphoto(True, tk.PhotoImage(file=os.path.join(RES, 'icon.png')))
    except tk.TclError:
        pass
    splash = tk.Frame(root, bg=BG)
    splash.place(relx=0.5, rely=0.45, anchor='center')
    try:
        big = os.path.join(RES, 'logo_big.png')
        logo = tk.PhotoImage(file=big) if os.path.exists(big) else tk.PhotoImage(file=os.path.join(RES, 'logo.png')).zoom(2)
        tk.Label(splash, image=logo, bg=BG).pack(pady=(0, 20))
        splash.logo = logo
    except tk.TclError:
        pass
    txt = tk.Label(splash, text='', bg=BG, fg=ACC, font=(UI, -24), justify='left', anchor='w', width=36)
    txt.pack()
    cur = tk.Label(splash, text='_', bg=BG, fg=FG, font=(UI, -24))
    cur.pack(anchor='w')
    root.update()
    _diagnostics(root)

    loaded = {}

    def load():                          # numpy, DSP, rig control... (no Tk calls here)
        t0 = time.perf_counter()
        try:
            import app                   # noqa: F401
            loaded['app'] = app
        except Exception as e:           # surface import errors on the boot screen
            loaded['err'] = e
        loaded['t'] = time.perf_counter() - t0
    threading.Thread(target=load, daemon=True).start()

    step = [0]

    def tick():
        lines = BOOT[:min(len(BOOT), step[0] + 1)]
        if 'app' in loaded or 'err' in loaded:
            lines = BOOT + (['READY.'] if 'app' in loaded else [f'ERROR: {loaded["err"]}'])
        txt.config(text='\n'.join(lines))
        cur.config(text='_' if step[0] % 2 else ' ')
        step[0] += 1
        if 'err' in loaded:
            return
        if 'app' in loaded and step[0] > 1:
            root.after(120, start)
            return
        root.after(90, tick)

    def start():
        app = loaded['app']
        splash.destroy()
        app.App(root)

    tick()
    root.mainloop()


if __name__ == '__main__':
    if '--headless' in sys.argv:
        from engine import headless
        headless(sys.argv)
    else:
        main()
