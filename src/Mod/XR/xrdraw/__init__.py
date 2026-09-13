# SPDX-License-Identifier: LGPL-2.1-or-later
"""In-VR technical drawings: a TechDraw sheet on a virtual drafting table.

::

    table.py       the table in the room; ray -> page mm, page mm -> world
    dimension.py   snapping to view geometry and inferring a dimension from picks
    to_techdraw.py reading a page's views, creating the dimension, rendering the sheet
    session.py     point, pick, place — with events for haptics and the HUD
"""

from .table import PAGE_SIZES, DraftingTable
from .dimension import Edge, DimensionSpec, InferenceError, Pick, Vertex, ViewGeometry, infer
from .session import DrawEvent, DrawSession
from .to_techdraw import make_dimension, page_image, page_views, view_geometry

__all__ = ["PAGE_SIZES", "DraftingTable", "Edge", "DimensionSpec", "InferenceError", "Pick", "Vertex", "ViewGeometry",
           "infer", "DrawEvent", "DrawSession", "make_dimension", "page_image", "page_views", "view_geometry"]
