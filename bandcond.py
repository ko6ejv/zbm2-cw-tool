"""HF band conditions from N0NBH (hamqsl.com): day/night Good/Fair/Poor for band pairs, plus SFI / K."""
import re
import urllib.request

URL = 'https://www.hamqsl.com/solarxml.php'
PAIRS = ['80m-40m', '30m-20m', '17m-15m', '12m-10m']


def parse(xml):
    """-> {'bands': {'80m-40m': ('Fair', 'Good'), ...}, 'sfi': '87', 'k': '1', 'updated': '...'}"""
    bands = {}
    for name, when, val in re.findall(r'<band name="([^"]+)" time="(day|night)">\s*([^<]*?)\s*</band>', xml):
        d, n = bands.get(name, ('', ''))
        bands[name] = (val, n) if when == 'day' else (d, val)

    def tag(t):
        m = re.search(rf'<{t}>\s*([^<]*?)\s*</{t}>', xml)
        return m.group(1) if m else ''
    return {'bands': {p: bands[p] for p in PAIRS if p in bands}, 'sfi': tag('solarflux'),
            'k': tag('kindex'), 'noise': tag('signalnoise'), 'updated': tag('updated')}


def fetch(timeout=10):
    req = urllib.request.Request(URL, headers={'User-Agent': 'ZBM2-CW-Tool'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return parse(r.read().decode('utf-8', 'replace'))


if __name__ == '__main__':
    print(fetch())
