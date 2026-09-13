#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later
"""Run every Collab unit test. None of them needs FreeCAD.

    python3 src/Mod/Collab/Tests/run_all.py
"""

import os
import sys
import unittest

MODULE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    sys.path.insert(0, MODULE_ROOT)
    loader = unittest.TestLoader()
    suite = loader.discover(start_dir=os.path.join(MODULE_ROOT, "Tests"), top_level_dir=MODULE_ROOT)
    runner = unittest.TextTestRunner(verbosity=1)
    return 0 if runner.run(suite).wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
