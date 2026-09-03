# SPDX-License-Identifier: LGPL-2.1-or-later
"""The two tessellators must not drift apart.

An environment is authored once and tessellated twice — in Python for the
desktop viewer, in C++ for the headset. When they disagree, the same machine
looks different depending on where you stand in it, and the failure modes
(flipped winding, inverted normals, a primitive on the wrong axis) are the ones
that are invisible when reading either implementation on its own.

Skipped when no host C++ compiler is available, which is the normal case on a
machine that only builds the Python side.
"""

import os
import sys
import unittest

MODULE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if MODULE_ROOT not in sys.path:
    sys.path.insert(0, MODULE_ROOT)

TOOLS = os.path.join(MODULE_ROOT, "tools")
if TOOLS not in sys.path:
    sys.path.insert(0, TOOLS)

try:
    import check_tessellator_parity as parity
except ImportError:  # pragma: no cover - the tool is part of the module
    parity = None

HAVE_COMPILER = parity is not None and parity.find_compiler() is not None
HAVE_CPP = parity is not None and os.path.isdir(parity.CPP_DIR)
HAVE_SPECS = parity is not None and os.path.isdir(parity.SPEC_DIR)


@unittest.skipUnless(parity is not None, "the parity tool is missing")
@unittest.skipUnless(HAVE_CPP, "the Quest C++ sources are not present")
@unittest.skipUnless(HAVE_SPECS, "no generated environment specs")
@unittest.skipUnless(HAVE_COMPILER, "no host C++ compiler")
class TestTessellatorParity(unittest.TestCase):
    build_dir = None
    binary = None

    @classmethod
    def setUpClass(cls):
        import tempfile

        cls.build_dir = tempfile.mkdtemp(prefix="xr-tess-parity-test-")
        try:
            cls.binary = parity.build_driver(cls.build_dir)
        except parity.ParityError as exc:
            raise unittest.SkipTest(str(exc))

    @classmethod
    def tearDownClass(cls):
        import shutil

        if cls.build_dir:
            shutil.rmtree(cls.build_dir, ignore_errors=True)

    def test_every_environment_tessellates_identically(self):
        specs = sorted(
            name for name in os.listdir(parity.SPEC_DIR) if name.endswith(".json")
        )
        self.assertTrue(specs, "no environment specs to compare")
        total = 0
        for name in specs:
            path = os.path.join(parity.SPEC_DIR, name)
            problems, count = parity.compare(self.binary, path)
            self.assertFalse(
                problems,
                f"{name}: the C++ and Python tessellators disagree\n"
                + "\n".join(problems[:5]),
            )
            self.assertGreater(count, 0, f"{name} tessellated nothing")
            total += count
        self.assertGreater(total, 1000, "the environments got suspiciously small")


if __name__ == "__main__":
    unittest.main()
