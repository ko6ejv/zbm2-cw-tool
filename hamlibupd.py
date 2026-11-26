"""Install / update Hamlib (rigctld) from the official GitHub releases, from inside the app.

Windows: downloads hamlib-w64-X.Y.Z.zip, checks its SHA-256 against Hamlib's own SHA256SUM file,
         unpacks it under the app's data folder. No admin rights, nothing system-wide changes.
Linux / Raspberry Pi / uConsole: downloads hamlib-X.Y.Z.tar.gz (checked the same way) and builds
         it into the data folder (needs a C compiler: sudo apt install build-essential).

The app then points 'hamlib_dir' at the new bin folder. Older copies are kept, so rolling back is
just choosing the old folder in Rig Setup.
"""
import glob
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile

from config import HERE

API = 'https://api.github.com/repos/Hamlib/Hamlib/releases/latest'
ROOT = os.path.join(HERE, 'hamlib')            # <data>/hamlib/<version>/bin
_NOWIN = 0x08000000 if os.name == 'nt' else 0  # CREATE_NO_WINDOW
UA = {'User-Agent': 'ZBM2-CW-Tool'}


def exe(name):
    return name + '.exe' if os.name == 'nt' else name


def installed_version(bindir):
    """'4.7.2' from `rigctld --version` in bindir (or PATH when bindir is ''), else None."""
    path = os.path.join(bindir, exe('rigctld')) if bindir else shutil.which('rigctld')
    if not path or not os.path.isfile(path):
        return None
    try:
        out = subprocess.run([path, '--version'], capture_output=True, text=True, timeout=10,
                             creationflags=_NOWIN).stdout
    except Exception:
        return None
    m = re.search(r'Hamlib\s+(\d+\.\d+(?:\.\d+)?)', out)
    return m.group(1) if m else None


def vtuple(v):
    return tuple(int(x) for x in re.findall(r'\d+', v or '0'))[:3]


def latest():
    """-> (version, {asset name: url}) of the newest Hamlib release."""
    with urllib.request.urlopen(urllib.request.Request(API, headers=UA), timeout=20) as r:
        rel = json.load(r)
    return rel['tag_name'].lstrip('v'), {a['name']: a['browser_download_url'] for a in rel['assets']}


def managed_bins():
    """bin folders of Hamlib copies this app installed, newest first."""
    out = []
    for d in glob.glob(os.path.join(ROOT, '*')):
        for b in (os.path.join(d, 'bin'), *glob.glob(os.path.join(d, '*', 'bin'))):
            if os.path.isfile(os.path.join(b, exe('rigctld'))):
                out.append(b)
    ver = lambda b: vtuple(os.path.relpath(b, ROOT).replace('\\', '/').split('/')[0])   # <ROOT>/<version>/...
    return sorted(out, key=ver, reverse=True)


def _get(url, dest, progress):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r, \
            open(dest, 'wb') as f:
        total, done, shown = int(r.headers.get('Content-Length') or 0), 0, -1
        while True:
            chunk = r.read(65536)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            pct = done * 100 // total if total else 0
            if total and pct // 10 != shown:
                shown = pct // 10
                progress(f'Downloading {os.path.basename(dest)}  {pct}%')


def _sha256_ok(path, sums_text):
    h = hashlib.sha256(open(path, 'rb').read()).hexdigest()
    name = os.path.basename(path)
    for line in sums_text.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[-1].lstrip('*') == name:
            return parts[0].lower() == h
    return False


def build_tools_missing():
    """Linux: which build tools are missing (empty list = ready)."""
    if os.name == 'nt':
        return []
    return [t for t in ('cc', 'make') if not shutil.which(t)]


def update(progress=print):
    """Install the newest Hamlib. -> (version, bin folder). Raises RuntimeError with a plain message."""
    progress('Checking the newest Hamlib release...')
    try:
        ver, assets = latest()
    except Exception as e:
        raise RuntimeError(f'Could not reach GitHub to check for Hamlib ({e}). Are you online?')
    dest = os.path.join(ROOT, ver)
    for b in managed_bins():
        if os.path.normpath(b).startswith(os.path.normpath(dest)):
            progress(f'Hamlib {ver} is already installed.')
            return ver, b
    win = os.name == 'nt'
    if win:
        bits = 'w64' if sys.maxsize > 2 ** 32 else 'w32'
        name, sums = f'hamlib-{bits}-{ver}.zip', f'SHA256SUM-{bits}-{ver}'
    else:
        missing = build_tools_missing()
        if missing:
            raise RuntimeError('Hamlib has to be built on Linux and the C compiler is missing. Run this once '
                               'in a terminal, then press Update again:\n    sudo apt install build-essential')
        name, sums = f'hamlib-{ver}.tar.gz', f'SHA256SUM-{ver}'
    if name not in assets or sums not in assets:
        raise RuntimeError(f'Hamlib {ver} has no {name} download yet - try again in a few days.')
    tmp = tempfile.mkdtemp(prefix='zbm2-hamlib-')
    try:
        arc = os.path.join(tmp, name)
        _get(assets[name], arc, progress)
        sums_text = urllib.request.urlopen(urllib.request.Request(assets[sums], headers=UA), timeout=30).read().decode()
        if not _sha256_ok(arc, sums_text):
            raise RuntimeError('The Hamlib download did not match its published checksum - not installing it.')
        progress('Checksum OK. Unpacking...')
        os.makedirs(ROOT, exist_ok=True)
        if win:
            with zipfile.ZipFile(arc) as z:
                z.extractall(dest)
        else:
            src = os.path.join(tmp, 'src')
            with tarfile.open(arc) as t:
                t.extractall(src, filter='data') if hasattr(tarfile, 'data_filter') else t.extractall(src)
            top = glob.glob(os.path.join(src, 'hamlib-*'))[0]
            steps = [(['./configure', f'--prefix={dest}', '--without-cxx-binding', '--disable-static'],
                      'Configuring (1/3)...'),
                     (['make', f'-j{os.cpu_count() or 2}'], 'Compiling (2/3) - about 15 minutes on a Raspberry Pi...'),
                     (['make', 'install'], 'Installing (3/3)...')]
            for cmd, msg in steps:
                progress(msg)
                r = subprocess.run(cmd, cwd=top, capture_output=True, text=True)
                if r.returncode:
                    raise RuntimeError(f'Hamlib build failed at "{" ".join(cmd)}":\n' + (r.stderr or r.stdout)[-600:])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    for b in managed_bins():
        if os.path.normpath(b).startswith(os.path.normpath(dest)):
            got = installed_version(b)
            progress(f'Hamlib {got or ver} installed.')
            return got or ver, b
    raise RuntimeError('Hamlib unpacked but rigctld was not found in it.')


if __name__ == '__main__':          # python hamlibupd.py  -> update from a terminal
    try:
        v, b = update()
        print(f'OK: Hamlib {v} in {b}')
    except RuntimeError as e:
        print('ERROR:', e)
        sys.exit(1)
