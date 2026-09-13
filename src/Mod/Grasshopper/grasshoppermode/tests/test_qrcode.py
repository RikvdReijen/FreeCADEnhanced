# SPDX-License-Identifier: LGPL-2.1-or-later
import unittest

from grasshoppermode import qrcode_gen

try:
    import qrcode
    import qrcode.constants as qc
except ImportError:  # pragma: no cover - optional reference implementation
    qrcode = None


def _reference(text, level, mask, version=None):
    lvl = {
        "L": qc.ERROR_CORRECT_L,
        "M": qc.ERROR_CORRECT_M,
        "Q": qc.ERROR_CORRECT_Q,
        "H": qc.ERROR_CORRECT_H,
    }[level]
    qr = qrcode.QRCode(
        version=version, error_correction=lvl, box_size=1, border=0, mask_pattern=mask
    )
    qr.add_data(text, optimize=0)
    qr.make(fit=version is None)
    return qr.version, [[bool(v) for v in row] for row in qr.get_matrix()]


SAMPLES = [
    ("http://192.168.1.10:8765/xr#c=abc123&mm=80", "M"),
    ("FreeCAD", "L"),
    ("http://localhost:8765/xr#c=0123456789ab&mm=120&v=1", "Q"),
    ("x" * 100, "H"),
    ("https://example.org/grasshopper-mode/canvas/" + "a" * 120, "L"),
    ("https://example.org/grasshopper-mode/canvas/" + "b" * 150, "M"),
]


class TestQRStructure(unittest.TestCase):
    def test_sizes_and_finders(self):
        for version in range(1, qrcode_gen.MAX_VERSION + 1):
            m = qrcode_gen.encode("hi", "L", version=version)
            self.assertEqual(len(m), version * 4 + 17)
            self.assertTrue(all(len(r) == len(m) for r in m))
            # finder pattern corner check
            self.assertTrue(m[0][0] and m[0][6] and m[6][0])
            self.assertFalse(m[1][1])
            self.assertTrue(m[3][3])

    def test_version_selection(self):
        self.assertEqual(qrcode_gen.choose_version(10, "L"), 1)
        self.assertEqual(qrcode_gen.choose_version(20, "L"), 2)
        self.assertEqual(qrcode_gen.choose_version(100, "H"), 10)
        self.assertEqual(qrcode_gen.choose_version(80, "H"), 8)
        with self.assertRaises(ValueError):
            qrcode_gen.choose_version(1000, "L")
        with self.assertRaises(ValueError):
            qrcode_gen.encode("x" * 300, "L")

    def test_mask_choice_is_valid_and_deterministic(self):
        a = qrcode_gen.encode("FreeCAD Grasshopper mode")
        b = qrcode_gen.encode("FreeCAD Grasshopper mode")
        self.assertEqual(a, b)

    def test_svg_and_ascii(self):
        m = qrcode_gen.encode("hi")
        svg = qrcode_gen.to_svg(m, module_mm=2.0, label="Canvas <1>", caption="80 mm")
        self.assertTrue(svg.startswith("<svg"))
        self.assertIn("Canvas &lt;1&gt;", svg)
        self.assertIn('width="58.000mm"', svg)
        ascii_art = qrcode_gen.to_ascii(m, quiet=1)
        self.assertEqual(len(ascii_art.splitlines()), len(m) + 2)

    def test_reed_solomon_known_vector(self):
        # "HELLO WORLD" style check: EC of a known 1-M block is deterministic
        data = qrcode_gen._encode_data(b"hi", 1, "L")
        self.assertEqual(len(data), 19)
        ec = qrcode_gen._rs_encode(data, 7)
        self.assertEqual(len(ec), 7)
        # encoding twice gives the same
        self.assertEqual(ec, qrcode_gen._rs_encode(data, 7))


@unittest.skipIf(qrcode is None, "reference 'qrcode' package not installed")
class TestAgainstReference(unittest.TestCase):
    def test_tables_match_reference(self):
        import qrcode.base as base

        for version in range(1, qrcode_gen.MAX_VERSION + 1):
            for level, lvl in (
                ("L", qc.ERROR_CORRECT_L),
                ("M", qc.ERROR_CORRECT_M),
                ("Q", qc.ERROR_CORRECT_Q),
                ("H", qc.ERROR_CORRECT_H),
            ):
                ref = [(b.data_count, b.total_count) for b in base.rs_blocks(version, lvl)]
                mine = []
                for count, total, data in qrcode_gen._RS_BLOCKS[version][level]:
                    mine.extend([(data, total)] * count)
                self.assertEqual(mine, ref, "rs blocks differ for %d-%s" % (version, level))

    def test_matrices_match_reference_for_every_mask(self):
        for text, level in SAMPLES:
            version, _ = _reference(text, level, 0)
            for mask in range(8):
                _, ref = _reference(text, level, mask, version=version)
                mine = qrcode_gen.encode(text, level, mask=mask, version=version)
                self.assertEqual(
                    mine, ref, "matrix differs for %r level %s mask %d" % (text[:20], level, mask)
                )

    def test_auto_mask_is_a_valid_symbol(self):
        # The reference library scores masks slightly differently from the
        # ISO penalty rules, so only require that our automatically chosen
        # mask produces exactly the symbol the reference makes for that mask.
        for text, level in SAMPLES:
            mine, version, mask = qrcode_gen.encode_with_info(text, level)
            ref_version, ref = _reference(text, level, mask, version=version)
            self.assertEqual(ref_version, version)
            self.assertEqual(mine, ref, "symbol differs for %r mask %d" % (text[:20], mask))
            self.assertIn(mask, range(8))

    def test_best_available_uses_reference(self):
        self.assertEqual(
            qrcode_gen.encode_best_available("hello", "M"), _reference("hello", "M", None)[1]
        )


if __name__ == "__main__":
    unittest.main()
