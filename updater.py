"""Update check + one-click update.

latest()  -> newest GitHub release {version, page, setup_url, setup_name, size, notes} or None
newer()   -> True when that version is above ours
how()     -> 'installer' (Windows .exe install), 'git' (git clone, e.g. uConsole) or 'page' (open the release page)
apply()   -> does the update for that case
"""
import re
import json
import os
import subprocess
import sys
import tempfile
import urllib.request

from config import __version__

REPO = 'ko6ejv/zbm2-cw-tool'
API = f'https://api.github.com/repos/{REPO}/releases/latest'
PAGE = f'https://github.com/{REPO}/releases/latest'
HERE = os.path.dirname(os.path.abspath(__file__))
AGENT = f'ZBM2-CW-Tool/{__version__}'


def _vt(v):
    out = []
    for p in str(v).lstrip('vV').split('.'):
        n = ''.join(ch for ch in p if ch.isdigit())
        out.append(int(n) if n else 0)
    return tuple(out + [0] * (3 - len(out)))


def newer(v, than=__version__):
    return _vt(v) > _vt(than)


def latest(timeout=10):
    req = urllib.request.Request(API, headers={'User-Agent': AGENT, 'Accept': 'application/vnd.github+json'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        rel = json.loads(r.read().decode('utf-8', 'replace'))
    v = (rel.get('tag_name') or '').lstrip('vV')
    if not v:
        return None
    setup = [a for a in rel.get('assets', []) if a['name'].lower().endswith('.exe') and 'setup' in a['name'].lower()]
    setup.sort(key=lambda a: len(a['name']), reverse=True)        # the versioned name over the plain copy
    a = setup[0] if setup else {}
    return {'version': v, 'page': rel.get('html_url') or PAGE, 'notes': rel.get('body') or '',
            'setup_url': a.get('browser_download_url'), 'setup_name': a.get('name'), 'size': a.get('size', 0)}


def how():
    if getattr(sys, 'frozen', False) and sys.platform == 'win32':
        exe_dir = os.path.dirname(sys.executable)          # Inno Setup leaves its uninstaller next to the exe
        if any(f.lower().startswith('unins') and f.lower().endswith('.exe') for f in os.listdir(exe_dir)):
            return 'installer'
        return 'page'                                      # portable zip: don't install a second copy
    if os.path.isdir(os.path.join(HERE, '.git')):
        return 'git'
    return 'page'


def _download(url, dest, progress=None):
    req = urllib.request.Request(url, headers={'User-Agent': AGENT})
    with urllib.request.urlopen(req, timeout=60) as r, open(dest, 'wb') as f:
        total = int(r.headers.get('Content-Length') or 0)
        done = 0
        while True:
            b = r.read(1 << 16)
            if not b:
                break
            f.write(b)
            done += len(b)
            if progress and total:
                progress(done / total)
    return dest


def apply(rel, progress=None):
    """Run the update. -> ('restart', None) when the caller should close the app now,
    ('restarted', None) when a fresh copy was started, or ('page', url) to open in a browser."""
    m = how()
    if m == 'installer' and rel.get('setup_url'):
        dest = os.path.join(tempfile.gettempdir(), rel['setup_name'] or 'ZBM2-CW-Tool-Setup.exe')
        _download(rel['setup_url'], dest, progress)
        if rel.get('size') and os.path.getsize(dest) != rel['size']:
            raise RuntimeError('download incomplete - try again')
        # Inno Setup: reuses the old install folder, closes this app, starts the new one when done
        subprocess.Popen([dest, '/SILENT', '/SP-', '/SUPPRESSMSGBOXES', '/NORESTART', '/CLOSEAPPLICATIONS'],
                         close_fds=True)
        return 'restart', None
    if m == 'git':
        run = dict(cwd=HERE, capture_output=True, text=True, timeout=300)
        r = subprocess.run(['git', 'pull', '--ff-only'], **run)
        if r.returncode:
            raise RuntimeError('git pull: ' + (r.stderr or r.stdout).strip()[-200:])
        subprocess.run([sys.executable, '-m', 'pip', 'install', '-q', '-r', 'requirements.txt'], **run)
        subprocess.Popen([sys.executable, os.path.join(HERE, 'main.py')], cwd=HERE, close_fds=True)
        return 'restarted', None
    return 'page', rel.get('page') or PAGE


if __name__ == '__main__':
    r = latest()
    print('this:', __version__, '| latest:', r and r['version'], '| newer:', bool(r and newer(r['version'])),
          '| update by:', how())


def changes_since(seen, text, upto=__version__):
    """CHANGELOG sections newer than `seen`, up to `upto`, newest first: [(version, body)]."""
    out = []
    for m in re.finditer(r'^## ZBM2 CW Tool (\S+)\s*\n(.*?)(?=^## |\Z)', text, re.M | re.S):
        v, body = m.group(1), m.group(2).strip()
        if newer(v, seen) and not newer(v, upto):
            out.append((v, body))
    return out
