#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later
# ***************************************************************************
# *   Copyright (c) 2026 FreeCAD Project Association                        *
# *                                                                         *
# *   This file is part of FreeCAD.                                         *
# *                                                                         *
# *   FreeCAD is free software: you can redistribute it and/or modify it    *
# *   under the terms of the GNU Lesser General Public License as           *
# *   published by the Free Software Foundation, either version 2.1 of the  *
# *   License, or (at your option) any later version.                       *
# *                                                                         *
# *   FreeCAD is distributed in the hope that it will be useful, but        *
# *   WITHOUT ANY WARRANTY; without even the implied warranty of            *
# *   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU     *
# *   Lesser General Public License for more details.                       *
# *                                                                         *
# *   You should have received a copy of the GNU Lesser General Public      *
# *   License along with FreeCAD. If not, see                               *
# *   <https://www.gnu.org/licenses/>.                                      *
# *                                                                         *
# ***************************************************************************
"""Run every GameBridge unit test that does not need FreeCAD.

    python3 src/Mod/GameBridge/Tests/run_all.py [pattern]
"""

import os
import sys
import unittest

MODULE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main(argv=None):
    argv = list(argv if argv is not None else sys.argv[1:])
    pattern = argv[0] if argv else "test_*.py"
    sys.path.insert(0, MODULE_ROOT)
    loader = unittest.TestLoader()
    suite = loader.discover(
        start_dir=os.path.join(MODULE_ROOT, "Tests"),
        pattern=pattern,
        top_level_dir=MODULE_ROOT,
    )
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
