"""Morse tables and helpers."""

MORSE = {
    'A': '.-', 'B': '-...', 'C': '-.-.', 'D': '-..', 'E': '.', 'F': '..-.',
    'G': '--.', 'H': '....', 'I': '..', 'J': '.---', 'K': '-.-', 'L': '.-..',
    'M': '--', 'N': '-.', 'O': '---', 'P': '.--.', 'Q': '--.-', 'R': '.-.',
    'S': '...', 'T': '-', 'U': '..-', 'V': '...-', 'W': '.--', 'X': '-..-',
    'Y': '-.--', 'Z': '--..',
    '0': '-----', '1': '.----', '2': '..---', '3': '...--', '4': '....-',
    '5': '.....', '6': '-....', '7': '--...', '8': '---..', '9': '----.',
    '.': '.-.-.-', ',': '--..--', '?': '..--..', '/': '-..-.', '=': '-...-',
    '+': '.-.-.', '-': '-....-', "'": '.----.', '(': '-.--.', ')': '-.--.-',
    ':': '---...', '"': '.-..-.', '@': '.--.-.',
}

# Prosigns (sent run together). '=' already covers BT, '+' covers AR.
PROSIGNS = {
    '<AR>': '.-.-.', '<SK>': '...-.-', '<BT>': '-...-', '<KN>': '-.--.',
    '<AS>': '.-...', '<BK>': '-...-.-', '<CT>': '-.-.-', '<HH>': '........',
}

DECODE = {v: k for k, v in MORSE.items()}
DECODE.update({'...-.-': '<SK>', '-.--.': '<KN>', '-...-.-': '<BK>',
               '.-...': '<AS>', '-.-.-': '<CT>', '........': '<HH>',
               '.-.-.': '<AR>', '-...-': '='})


def tokenize(text):
    """Yield tokens: single chars, prosigns like <SK>, or ' ' for word gaps."""
    text = text.upper()
    i = 0
    while i < len(text):
        c = text[i]
        if c == '<':
            j = text.find('>', i)
            if j > i:
                tok = text[i:j + 1]
                if tok in PROSIGNS:
                    yield tok
                    i = j + 1
                    continue
        if c.isspace():
            yield ' '
        elif c in MORSE:
            yield c
        i += 1


def code_for(token):
    if token in PROSIGNS:
        return PROSIGNS[token]
    return MORSE.get(token)
