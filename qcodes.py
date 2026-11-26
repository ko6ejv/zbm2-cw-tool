"""Q-codes and common CW abbreviations with formal definitions, for banners + click-to-define."""

# code: (statement, question)
QCODES = {
    'QRM': ('Your transmission is being interfered with.', 'Is my transmission being interfered with?'),
    'QRN': ('I am troubled by static.', 'Are you troubled by static?'),
    'QSB': ('Your signals are fading.', 'Are my signals fading?'),
    'QRS': ('Send more slowly.', 'Shall I send more slowly?'),
    'QRQ': ('Send faster.', 'Shall I send faster?'),
    'QRO': ('Increase power.', 'Shall I increase power?'),
    'QRP': ('Decrease power.', 'Shall I decrease power?'),
    'QRL': ('I am busy (this frequency is in use).', 'Are you busy? / Is this frequency in use?'),
    'QRZ': ('You are being called by ...', 'Who is calling me?'),
    'QSL': ('I acknowledge receipt.', 'Can you acknowledge receipt?'),
    'QSY': ('Change to another frequency.', 'Shall I change to another frequency?'),
    'QTH': ('My location is ...', 'What is your location?'),
    'QRT': ('Stop sending.', 'Shall I stop sending?'),
    'QRX': ('Wait - I will call you again.', 'When will you call me again?'),
    'QRV': ('I am ready.', 'Are you ready?'),
    'QRU': ('I have nothing for you.', 'Have you anything for me?'),
    'QSO': ('I can communicate with ... (a contact).', 'Can you communicate with ...?'),
    'QSK': ('I can hear you between my signals (full break-in).', 'Can you hear me between your signals?'),
}

# abbreviations that are NOT Q-codes
ABBR = {
    'AGN': 'Again', 'PSE': 'Please', 'RPT': 'Repeat', 'TNX': 'Thanks', 'TU': 'Thank you',
    'FB': 'Fine business (great)', 'OM': 'Old man (friend)', 'ES': 'And', 'HW': 'How (do you copy)?',
    'CPY': 'Copy', 'UR': 'Your / you are', 'HR': 'Here', 'FER': 'For', 'RST': 'Signal report: Readability, Strength, Tone',
    'CQ': 'Calling any station', 'DE': 'From (this is)', 'K': 'Over - anyone may reply',
    'KN': 'Over - only the station I am working may reply', 'BK': 'Back to you (quick turn-around)',
    'SK': 'End of contact', 'AR': 'End of message', 'CL': 'Closing station (going off the air)',
    '73': 'Best regards', 'CUL': 'See you later', 'GL': 'Good luck', 'WX': 'Weather', 'RIG': 'Radio',
    'ANT': 'Antenna', 'PWR': 'Power', 'NR': 'Number', 'SRI': 'Sorry', 'GM': 'Good morning',
    'GA': 'Good afternoon', 'GE': 'Good evening', 'HI': 'Laughter', 'DX': 'Distant station',
    '5NN': '599 - perfect signal report (N = 9)', 'EE': 'Dit dit - friendly goodbye', 'R': 'Received / roger',
}


def define(word):
    """(label, text) for a Q-code or abbreviation, or None."""
    w = word.upper().strip('<>')
    q = w.rstrip('?')
    if q in QCODES:
        st, qu = QCODES[q]
        return 'Q-CODE', f'{q} = "{st}"   ({q}? = "{qu}")'
    if w in ABBR or q in ABBR:
        k = w if w in ABBR else q
        return 'ABBR', f'{k} = {ABBR[k]} (abbreviation, not a Q-code)'
    return None
