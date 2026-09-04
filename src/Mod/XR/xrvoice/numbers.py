# SPDX-License-Identifier: LGPL-2.1-or-later
"""Numbers and units as people say them.

"two millimetres", "two point five mil", "half a millimetre", "quarter
inch", "twenty five", "2,5 mm", "3/4 in", "ninety degrees" all come out of
:func:`parse_quantity` as a value in the base unit (millimetres for lengths,
degrees for angles) plus the unit family.
"""

import re

_SMALL = {
    "zero": 0, "oh": 0, "one": 1, "a": 1, "an": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}
_SCALE = {"hundred": 100, "thousand": 1000}
_FRACTIONS = {"half": 0.5, "halves": 0.5, "third": 1.0 / 3.0, "quarter": 0.25, "quarters": 0.25, "eighth": 0.125,
              "tenth": 0.1, "thirds": 1.0 / 3.0, "eighths": 0.125, "tenths": 0.1}

#: unit word -> (family, factor to base unit)
UNITS = {
    "mm": ("length", 1.0), "millimeter": ("length", 1.0), "millimeters": ("length", 1.0),
    "millimetre": ("length", 1.0), "millimetres": ("length", 1.0), "mil": ("length", 1.0), "mils": ("length", 1.0),
    "cm": ("length", 10.0), "centimeter": ("length", 10.0), "centimeters": ("length", 10.0),
    "centimetre": ("length", 10.0), "centimetres": ("length", 10.0),
    "m": ("length", 1000.0), "meter": ("length", 1000.0), "meters": ("length", 1000.0),
    "metre": ("length", 1000.0), "metres": ("length", 1000.0),
    "in": ("length", 25.4), "inch": ("length", 25.4), "inches": ("length", 25.4), '"': ("length", 25.4),
    "ft": ("length", 304.8), "foot": ("length", 304.8), "feet": ("length", 304.8),
    "micron": ("length", 0.001), "microns": ("length", 0.001), "um": ("length", 0.001),
    "deg": ("angle", 1.0), "degree": ("angle", 1.0), "degrees": ("angle", 1.0), "°": ("angle", 1.0),
    "rad": ("angle", 57.29577951308232), "radian": ("angle", 57.29577951308232), "radians": ("angle", 57.29577951308232),
    "percent": ("ratio", 0.01), "%": ("ratio", 0.01), "x": ("ratio", 1.0), "times": ("ratio", 1.0),
    "g": ("mass", 1.0), "gram": ("mass", 1.0), "grams": ("mass", 1.0), "kg": ("mass", 1000.0),
}

_NUMERIC = re.compile(r"^[+-]?(\d+([.,]\d+)?|[.,]\d+)$")
_FRACTION = re.compile(r"^(\d+)/(\d+)$")
_ATTACHED = re.compile(r"^([+-]?\d+(?:[.,]\d+)?)([a-z\"°%]+)$")


def tokenize(text):
    """Lower-cased words, with punctuation split off and attached units separated."""
    text = text.lower().replace("-", " ").replace("½", " half ").replace("¼", " quarter ").replace("¾", " three quarters ")
    out = []
    for raw in re.findall(r"[a-z]+|\d+(?:[.,]\d+)?/?\d*|[\"°%]|[.,;:!?]", text):
        m = _ATTACHED.match(raw)
        if m:
            out.extend([m.group(1), m.group(2)])
        elif raw in ".,;:!?":
            continue
        else:
            out.append(raw)
    return out


def parse_number(tokens, start=0):
    """Parse a number beginning at ``tokens[start]``.

    Returns ``(value, next_index)`` or ``None``. Handles digits (with ``.``
    or ``,`` decimals), ``3/4``, number words up to the thousands, "point"
    decimals, and fractions ("half", "a quarter", "two and a half").
    """
    i = start
    n = len(tokens)
    if i >= n:
        return None
    tok = tokens[i]
    if _NUMERIC.match(tok):
        value = float(tok.replace(",", "."))
        i += 1
        # "2 and a half"
        frac = _parse_fraction(tokens, i)
        if frac is not None:
            value += frac[0]
            i = frac[1]
        return value, i
    m = _FRACTION.match(tok)
    if m and int(m.group(2)) != 0:
        return float(m.group(1)) / float(m.group(2)), i + 1
    frac = _parse_fraction(tokens, i)
    if frac is not None and tokens[frac[1] - 1] in _FRACTIONS and frac[1] - i <= 2:
        return frac  # "half", "a quarter", "three quarters"
    words = _parse_words(tokens, i)
    if words is None:
        # a lone fraction word: "half a millimetre"
        return frac
    value, i = words
    if i < n and tokens[i] == "point":
        digits = []
        j = i + 1
        while j < n and (tokens[j] in _SMALL and _SMALL[tokens[j]] < 10 and tokens[j] not in ("a", "an")
                         or _NUMERIC.match(tokens[j]) and len(tokens[j]) == 1):
            digits.append(str(_SMALL.get(tokens[j], tokens[j])))
            j += 1
        if digits:
            value = float("%d.%s" % (int(value), "".join(digits)))
            i = j
    frac = _parse_fraction(tokens, i)
    if frac is not None:
        value += frac[0]
        i = frac[1]
    return value, i


def _parse_words(tokens, i):
    n = len(tokens)
    total, current, consumed = 0, 0, 0
    j = i
    while j < n:
        tok = tokens[j]
        if tok in ("a", "an") and consumed == 0 and j + 1 < n and tokens[j + 1] in _SCALE:
            current = 1
        elif tok in _SMALL and tok not in ("a", "an"):
            current += _SMALL[tok]
        elif tok in _TENS:
            current += _TENS[tok]
        elif tok in _SCALE:
            current = max(current, 1) * _SCALE[tok]
            if _SCALE[tok] >= 1000:
                total += current
                current = 0
        elif tok == "and" and consumed and j + 1 < n and (tokens[j + 1] in _SMALL or tokens[j + 1] in _TENS):
            pass
        else:
            break
        consumed += 1
        j += 1
    if consumed == 0:
        return None
    return float(total + current), j


def _parse_fraction(tokens, i):
    """``[and] [a|one|two|three] half|quarter|third|eighth [of a]``."""
    n = len(tokens)
    j = i
    if j < n and tokens[j] == "and":
        j += 1
    count = 1.0
    if j < n and tokens[j] in ("a", "an", "one"):
        j += 1
    elif j < n and tokens[j] in _SMALL and _SMALL[tokens[j]] < 10:
        count = float(_SMALL[tokens[j]])
        j += 1
    if j < n and tokens[j] in _FRACTIONS:
        value = count * _FRACTIONS[tokens[j]]
        j += 1
        if j + 1 < n and tokens[j] == "of" and tokens[j + 1] in ("a", "an"):
            j += 2
        return value, j
    return None


def parse_unit(tokens, i):
    """``(family, factor, next_index)`` or ``None``."""
    if i < len(tokens) and tokens[i] in UNITS:
        family, factor = UNITS[tokens[i]]
        return family, factor, i + 1
    return None


class Quantity(object):
    __slots__ = ("value", "family", "unit_given", "start", "end")

    def __init__(self, value, family, unit_given, start, end):
        self.value = float(value)
        self.family = family
        self.unit_given = unit_given
        self.start = start
        self.end = end

    def to_dict(self):
        return {"value": self.value, "family": self.family, "unit_given": self.unit_given}

    def __repr__(self):
        return "Quantity(%g %s)" % (self.value, self.family)


def parse_quantity(tokens, start=0, default_family="length"):
    """A number optionally followed by a unit, from ``tokens[start]``.

    Without a unit the value is taken in the base unit of ``default_family``
    (millimetres). Returns a :class:`Quantity` or ``None``.
    """
    number = parse_number(tokens, start)
    if number is None:
        return None
    value, i = number
    unit = parse_unit(tokens, i)
    if unit is None and i < len(tokens) and tokens[i] in ("a", "an"):
        unit = parse_unit(tokens, i + 1)  # "half an inch"
    if unit is not None:
        family, factor, i = unit
        return Quantity(value * factor, family, True, start, i)
    return Quantity(value, default_family, False, start, i)


def find_quantity(tokens, default_family="length"):
    """The first quantity anywhere in the tokens, or ``None``."""
    for i in range(len(tokens)):
        q = parse_quantity(tokens, i, default_family)
        if q is not None:
            return q
    return None
