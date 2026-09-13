# SPDX-License-Identifier: LGPL-2.1-or-later
"""Test entry point registered in ``FreeCAD.__unit_test__``.

Collects the unit tests of the ``grasshoppermode`` package.  All of them also
run outside FreeCAD (``python -m unittest discover`` in this directory); the
end-to-end browser test skips itself when Playwright is not installed.
"""

import unittest

from grasshoppermode.tests import (
    test_graph,
    test_layout,
    test_markers,
    test_nodes,
    test_qrcode,
    test_session,
    test_xr_client,
    test_xrserver,
)


def load_tests(loader, standard_tests, pattern):
    suite = unittest.TestSuite()
    for mod in (
        test_graph,
        test_nodes,
        test_layout,
        test_qrcode,
        test_markers,
        test_session,
        test_xrserver,
        test_xr_client,
    ):
        suite.addTests(loader.loadTestsFromModule(mod))
    try:
        from grasshoppermode.tests import test_freecad

        suite.addTests(loader.loadTestsFromModule(test_freecad))
    except ImportError:
        pass
    return suite


class TestGrasshopperImports(unittest.TestCase):
    def test_package_imports(self):
        import grasshoppermode

        self.assertTrue(grasshoppermode.__version__)
