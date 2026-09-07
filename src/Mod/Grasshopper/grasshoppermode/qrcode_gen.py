# SPDX-License-Identifier: LGPL-2.1-or-later
"""Minimal pure-Python QR code encoder (byte mode, versions 1-10).

Used to print the canvas anchor marker without adding a dependency to
FreeCAD.  If the optional ``qrcode`` package is installed it is preferred
(and used in the tests to validate this implementation bit for bit).

Only what the marker needs is implemented: 8-bit byte mode, error correction
levels L/M/Q/H, versions 1 to 10 (up to 271 bytes at level L), automatic
version selection and automatic mask selection with the standard penalty
rules from ISO/IEC 18004.
"""

# (version, level) -> list of (block_count, total_codewords, data_codewords)
_RS_BLOCKS = {
    1: {"L": [(1, 26, 19)], "M": [(1, 26, 16)], "Q": [(1, 26, 13)], "H": [(1, 26, 9)]},
    2: {"L": [(1, 44, 34)], "M": [(1, 44, 28)], "Q": [(1, 44, 22)], "H": [(1, 44, 16)]},
    3: {"L": [(1, 70, 55)], "M": [(1, 70, 44)], "Q": [(2, 35, 17)], "H": [(2, 35, 13)]},
    4: {"L": [(1, 100, 80)], "M": [(2, 50, 32)], "Q": [(2, 50, 24)], "H": [(4, 25, 9)]},
    5: {
        "L": [(1, 134, 108)],
        "M": [(2, 67, 43)],
        "Q": [(2, 33, 15), (2, 34, 16)],
        "H": [(2, 33, 11), (2, 34, 12)],
    },
    6: {"L": [(2, 86, 68)], "M": [(4, 43, 27)], "Q": [(4, 43, 19)], "H": [(4, 43, 15)]},
    7: {
        "L": [(2, 98, 78)],
        "M": [(4, 49, 31)],
        "Q": [(2, 32, 14), (4, 33, 15)],
        "H": [(4, 39, 13), (1, 40, 14)],
    },
    8: {
        "L": [(2, 121, 97)],
        "M": [(2, 60, 38), (2, 61, 39)],
        "Q": [(4, 40, 18), (2, 41, 19)],
        "H": [(4, 40, 14), (2, 41, 15)],
    },
    9: {
        "L": [(2, 146, 116)],
        "M": [(3, 58, 36), (2, 59, 37)],
        "Q": [(4, 36, 16), (4, 37, 17)],
        "H": [(4, 36, 12), (4, 37, 13)],
    },
    10: {
        "L": [(2, 86, 68), (2, 87, 69)],
        "M": [(4, 69, 43), (1, 70, 44)],
        "Q": [(6, 43, 19), (2, 44, 20)],
        "H": [(6, 43, 15), (2, 44, 16)],
    },
}

_ALIGNMENT = {
    1: [],
    2: [6, 18],
    3: [6, 22],
    4: [6, 26],
    5: [6, 30],
    6: [6, 34],
    7: [6, 22, 38],
    8: [6, 24, 42],
    9: [6, 26, 46],
    10: [6, 28, 50],
}

_LEVEL_BITS = {"L": 1, "M": 0, "Q": 3, "H": 2}
MAX_VERSION = 10

# --------------------------------------------------------------- GF(256) maths
_EXP = [0] * 512
_LOG = [0] * 256


def _init_tables():
    x = 1
    for i in range(255):
        _EXP[i] = x
        _LOG[x] = i
        x <<= 1
        if x & 0x100:
            x ^= 0x11D
    for i in range(255, 512):
        _EXP[i] = _EXP[i - 255]


_init_tables()


def _gf_mul(a, b):
    if a == 0 or b == 0:
        return 0
    return _EXP[_LOG[a] + _LOG[b]]


def _rs_generator(degree):
    poly = [1]
    for i in range(degree):
        next_poly = [0] * (len(poly) + 1)
        for j, coef in enumerate(poly):
            next_poly[j] ^= coef
            next_poly[j + 1] ^= _gf_mul(coef, _EXP[i])
        poly = next_poly
    return poly


def _rs_encode(data, degree):
    gen = _rs_generator(degree)
    remainder = [0] * degree
    for byte in data:
        factor = byte ^ remainder[0]
        remainder = remainder[1:] + [0]
        for i in range(degree):
            remainder[i] ^= _gf_mul(gen[i + 1], factor)
    return remainder


# ------------------------------------------------------------- bit handling
class _Bits:
    def __init__(self):
        self.bits = []

    def append(self, value, length):
        for i in range(length - 1, -1, -1):
            self.bits.append((value >> i) & 1)

    def __len__(self):
        return len(self.bits)


def data_capacity(version, level):
    return sum(count * data for count, _total, data in _RS_BLOCKS[version][level])


def _count_bits(version):
    return 8 if version <= 9 else 16


def choose_version(payload_len, level):
    for version in range(1, MAX_VERSION + 1):
        needed = 4 + _count_bits(version) + payload_len * 8
        if needed <= data_capacity(version, level) * 8:
            return version
    raise ValueError("payload too long for QR versions 1-%d at level %s" % (MAX_VERSION, level))


def _encode_data(payload, version, level):
    bits = _Bits()
    bits.append(0b0100, 4)
    bits.append(len(payload), _count_bits(version))
    for byte in payload:
        bits.append(byte, 8)
    capacity = data_capacity(version, level) * 8
    bits.append(0, min(4, capacity - len(bits)))
    while len(bits) % 8:
        bits.bits.append(0)
    data = []
    for i in range(0, len(bits), 8):
        value = 0
        for bit in bits.bits[i : i + 8]:
            value = (value << 1) | bit
        data.append(value)
    pads = (0xEC, 0x11)
    i = 0
    while len(data) < capacity // 8:
        data.append(pads[i % 2])
        i += 1
    return data


def _interleave(data, version, level):
    blocks = []
    ec_blocks = []
    offset = 0
    for count, total, data_len in _RS_BLOCKS[version][level]:
        for _ in range(count):
            chunk = data[offset : offset + data_len]
            offset += data_len
            blocks.append(chunk)
            ec_blocks.append(_rs_encode(chunk, total - data_len))
    out = []
    for i in range(max(len(b) for b in blocks)):
        for b in blocks:
            if i < len(b):
                out.append(b[i])
    for i in range(max(len(b) for b in ec_blocks)):
        for b in ec_blocks:
            if i < len(b):
                out.append(b[i])
    return out


# --------------------------------------------------------------- the matrix
class QRMatrix:
    def __init__(self, version):
        self.version = version
        self.size = version * 4 + 17
        self.modules = [[None] * self.size for _ in range(self.size)]
        self.function = [[False] * self.size for _ in range(self.size)]

    def _set_function(self, x, y, dark):
        self.modules[y][x] = dark
        self.function[y][x] = True

    def draw_function_patterns(self):
        n = self.size
        for cx, cy in ((3, 3), (n - 4, 3), (3, n - 4)):
            for dy in range(-4, 5):
                for dx in range(-4, 5):
                    x, y = cx + dx, cy + dy
                    if 0 <= x < n and 0 <= y < n:
                        d = max(abs(dx), abs(dy))
                        self._set_function(x, y, d != 2 and d != 4)
        for i in range(8, n - 8):
            self._set_function(i, 6, i % 2 == 0)
            self._set_function(6, i, i % 2 == 0)
        positions = _ALIGNMENT[self.version]
        for cy in positions:
            for cx in positions:
                if (cx == 6 and cy == 6) or (cx == 6 and cy == n - 7) or (cx == n - 7 and cy == 6):
                    continue
                for dy in range(-2, 3):
                    for dx in range(-2, 3):
                        self._set_function(cx + dx, cy + dy, max(abs(dx), abs(dy)) != 1)
        # reserve format areas (values written later)
        for i in range(9):
            if i != 6:
                self._set_function(8, i, False)
                self._set_function(i, 8, False)
        for i in range(8):
            self._set_function(n - 1 - i, 8, False)
            self._set_function(8, n - 1 - i, False)
        self._set_function(8, n - 8, True)  # dark module
        if self.version >= 7:
            self.draw_version()

    def draw_version(self):
        rem = self.version
        for _ in range(12):
            rem = (rem << 1) ^ ((rem >> 11) * 0x1F25)
        bits = (self.version << 12) | rem
        n = self.size
        for i in range(18):
            bit = (bits >> i) & 1
            a, b = n - 11 + i % 3, i // 3
            self._set_function(a, b, bool(bit))
            self._set_function(b, a, bool(bit))

    def draw_format(self, level, mask):
        data = (_LEVEL_BITS[level] << 3) | mask
        rem = data
        for _ in range(10):
            rem = (rem << 1) ^ ((rem >> 9) * 0x537)
        bits = ((data << 10) | rem) ^ 0x5412
        n = self.size
        for i in range(6):
            self._set_function(8, i, bool((bits >> i) & 1))
        self._set_function(8, 7, bool((bits >> 6) & 1))
        self._set_function(8, 8, bool((bits >> 7) & 1))
        self._set_function(7, 8, bool((bits >> 8) & 1))
        for i in range(9, 15):
            self._set_function(14 - i, 8, bool((bits >> i) & 1))
        for i in range(8):
            self._set_function(n - 1 - i, 8, bool((bits >> i) & 1))
        for i in range(8, 15):
            self._set_function(8, n - 15 + i, bool((bits >> i) & 1))
        self._set_function(8, n - 8, True)

    def place_data(self, codewords):
        n = self.size
        bit_index = 0
        total_bits = len(codewords) * 8
        x = n - 1
        upward = True
        while x > 0:
            if x == 6:
                x -= 1
            ys = range(n - 1, -1, -1) if upward else range(n)
            for y in ys:
                for dx in (0, -1):
                    cx = x + dx
                    if self.function[y][cx]:
                        continue
                    if bit_index < total_bits:
                        dark = (codewords[bit_index >> 3] >> (7 - (bit_index & 7))) & 1
                        bit_index += 1
                    else:
                        dark = 0
                    self.modules[y][cx] = bool(dark)
            x -= 2
            upward = not upward

    def apply_mask(self, mask):
        n = self.size
        for y in range(n):
            for x in range(n):
                if self.function[y][x]:
                    continue
                if _mask_bit(mask, x, y):
                    self.modules[y][x] = not self.modules[y][x]

    def penalty(self):
        n = self.size
        m = self.modules
        score = 0
        # rule 1: runs of five or more
        for y in range(n):
            run = 1
            for x in range(1, n):
                if m[y][x] == m[y][x - 1]:
                    run += 1
                    if run == 5:
                        score += 3
                    elif run > 5:
                        score += 1
                else:
                    run = 1
        for x in range(n):
            run = 1
            for y in range(1, n):
                if m[y][x] == m[y - 1][x]:
                    run += 1
                    if run == 5:
                        score += 3
                    elif run > 5:
                        score += 1
                else:
                    run = 1
        # rule 2: 2x2 blocks
        for y in range(n - 1):
            for x in range(n - 1):
                v = m[y][x]
                if v == m[y][x + 1] == m[y + 1][x] == m[y + 1][x + 1]:
                    score += 3
        # rule 3: finder-like patterns
        pat_a = [True, False, True, True, True, False, True, False, False, False, False]
        pat_b = list(reversed(pat_a))
        for y in range(n):
            row = m[y]
            col = [m[i][y] for i in range(n)]
            for x in range(n - 10):
                if row[x : x + 11] in (pat_a, pat_b):
                    score += 40
                if col[x : x + 11] in (pat_a, pat_b):
                    score += 40
        # rule 4: dark proportion
        dark = sum(1 for row in m for v in row if v)
        total = n * n
        k = abs(dark * 20 - total * 10) // total
        score += k * 10
        return score


def _mask_bit(mask, x, y):
    if mask == 0:
        return (x + y) % 2 == 0
    if mask == 1:
        return y % 2 == 0
    if mask == 2:
        return x % 3 == 0
    if mask == 3:
        return (x + y) % 3 == 0
    if mask == 4:
        return (y // 2 + x // 3) % 2 == 0
    if mask == 5:
        return (x * y) % 2 + (x * y) % 3 == 0
    if mask == 6:
        return ((x * y) % 2 + (x * y) % 3) % 2 == 0
    if mask == 7:
        return ((x + y) % 2 + (x * y) % 3) % 2 == 0
    raise ValueError("mask must be 0-7")


def encode(text, level="M", mask=None, version=None):
    """Encode ``text`` (str or bytes).  Returns a list of rows of booleans."""
    return encode_with_info(text, level, mask, version)[0]


def encode_with_info(text, level="M", mask=None, version=None):
    """Like :func:`encode` but returns ``(modules, version, mask)``."""
    payload = text.encode("utf-8") if isinstance(text, str) else bytes(text)
    level = level.upper()
    if level not in _LEVEL_BITS:
        raise ValueError("level must be L, M, Q or H")
    version = version or choose_version(len(payload), level)
    if not 1 <= version <= MAX_VERSION:
        raise ValueError("version must be 1-%d" % MAX_VERSION)
    if 4 + _count_bits(version) + len(payload) * 8 > data_capacity(version, level) * 8:
        raise ValueError("payload does not fit version %d-%s" % (version, level))
    codewords = _interleave(_encode_data(payload, version, level), version, level)

    def build(mask_id):
        m = QRMatrix(version)
        m.draw_function_patterns()
        m.draw_format(level, mask_id)
        m.place_data(codewords)
        m.apply_mask(mask_id)
        return m

    if mask is None:
        best = None
        for candidate in range(8):
            m = build(candidate)
            p = m.penalty()
            if best is None or p < best[0]:
                best = (p, candidate, m)
        matrix, mask = best[2], best[1]
    else:
        matrix = build(mask)
    return [[bool(v) for v in row] for row in matrix.modules], version, mask


def to_svg(modules, module_mm=2.0, quiet=4, label=None, caption=None, dark="#000", light="#fff"):
    """Render modules as an SVG string sized in millimetres."""
    n = len(modules)
    size = (n + 2 * quiet) * module_mm
    text_h = 0.0
    if label or caption:
        text_h = module_mm * 6
    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" width="%.3fmm" height="%.3fmm" viewBox="0 0 %.3f %.3f">'
        % (size, size + text_h, size, size + text_h),
        '<rect width="%.3f" height="%.3f" fill="%s"/>' % (size, size + text_h, light),
    ]
    path = []
    for y, row in enumerate(modules):
        for x, dark_module in enumerate(row):
            if dark_module:
                path.append(
                    "M%.3f %.3fh%.3fv%.3fh-%.3fz"
                    % (
                        (x + quiet) * module_mm,
                        (y + quiet) * module_mm,
                        module_mm,
                        module_mm,
                        module_mm,
                    )
                )
    parts.append('<path d="%s" fill="%s"/>' % ("".join(path), dark))
    if label:
        parts.append(
            '<text x="%.3f" y="%.3f" font-family="sans-serif" font-size="%.3f" text-anchor="middle" fill="%s">%s</text>'
            % (size / 2, size + module_mm * 2.6, module_mm * 2.2, dark, _escape(label))
        )
    if caption:
        parts.append(
            '<text x="%.3f" y="%.3f" font-family="sans-serif" font-size="%.3f" text-anchor="middle" fill="%s">%s</text>'
            % (size / 2, size + module_mm * 5.2, module_mm * 1.6, dark, _escape(caption))
        )
    parts.append("</svg>")
    return "".join(parts)


def _escape(text):
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def to_ascii(modules, quiet=2):
    n = len(modules)
    rows = []
    for y in range(-quiet, n + quiet):
        row = ""
        for x in range(-quiet, n + quiet):
            dark = 0 <= x < n and 0 <= y < n and modules[y][x]
            row += "##" if dark else "  "
        rows.append(row)
    return "\n".join(rows)


def encode_best_available(text, level="M"):
    """Use the ``qrcode`` package when present (any version), else this module."""
    try:
        import qrcode
        import qrcode.constants as constants
    except ImportError:
        return encode(text, level)
    lvl = {
        "L": constants.ERROR_CORRECT_L,
        "M": constants.ERROR_CORRECT_M,
        "Q": constants.ERROR_CORRECT_Q,
        "H": constants.ERROR_CORRECT_H,
    }[level.upper()]
    qr = qrcode.QRCode(error_correction=lvl, box_size=1, border=0)
    qr.add_data(text)
    qr.make(fit=True)
    return [[bool(v) for v in row] for row in qr.get_matrix()]
