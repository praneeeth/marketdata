"""Text normalisation used only for detection (never for the text shown to users).

It defeats cheap obfuscation: full-width characters, zero-width characters, common
Cyrillic/Greek homoglyphs, markdown emphasis, and spaced-out letters ("b u y").
"""

from __future__ import annotations

import re
import unicodedata

_INVISIBLE = dict.fromkeys(
    map(
        ord,
        "­͏؜ᅟᅠ឴឵᠎​‌‍‎‏‪‫‬‭‮⁠⁡⁢⁣⁤⁦⁧⁨⁩ㅤ﻿ﾠ",
    ),
    None,
)

# Lookalike letters that NFKC does not fold.
_HOMOGLYPHS = str.maketrans(
    {
        "а": "a",
        "е": "e",
        "о": "o",
        "р": "p",
        "с": "c",
        "у": "y",
        "х": "x",
        "і": "i",
        "ј": "j",
        "ѕ": "s",
        "ԁ": "d",
        "һ": "h",
        "ӏ": "l",
        "к": "k",
        "м": "m",
        "т": "t",
        "в": "b",
        "н": "h",
        "А": "A",
        "В": "B",
        "Е": "E",
        "К": "K",
        "М": "M",
        "Н": "H",
        "О": "O",
        "Р": "P",
        "С": "C",
        "Т": "T",
        "У": "Y",
        "Х": "X",
        "ο": "o",
        "α": "a",
        "ε": "e",
        "ι": "i",
        "κ": "k",
        "ν": "v",
        "ρ": "p",
        "τ": "t",
        "υ": "u",
        "χ": "x",
        "Ο": "O",
        "Α": "A",
        "Β": "B",
        "Ε": "E",
        "Κ": "K",
        "Μ": "M",
        "Ν": "N",
        "Ρ": "P",
        "Τ": "T",
        "Χ": "X",
        "Υ": "Y",
    }
)

_MD_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_MD_CHARS = re.compile(r"[*_`~#>|]+")
_SPACED_LETTERS = re.compile(r"\b(?:[a-z][ .\-·]){2,}[a-z]\b")
_WS = re.compile(r"\s+")


def _join_spaced(match: re.Match[str]) -> str:
    return re.sub(r"[ .\-·]", "", match.group(0))


def normalize_for_detection(text: str) -> str:
    """Return a case-folded, de-obfuscated form of ``text`` for pattern matching."""
    out = unicodedata.normalize("NFKC", text)
    out = out.translate(_INVISIBLE).translate(_HOMOGLYPHS)
    out = _MD_LINK.sub(r"\1", out)
    out = _MD_CHARS.sub(" ", out)
    out = out.casefold()
    out = _SPACED_LETTERS.sub(_join_spaced, out)
    return _WS.sub(" ", out).strip()
