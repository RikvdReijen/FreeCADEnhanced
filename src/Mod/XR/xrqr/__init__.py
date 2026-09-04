# SPDX-License-Identifier: LGPL-2.1-or-later
"""Snap to a QR code: printed codes as spatial anchors.

``payload`` defines what a code says (``fcxr://anchor?id=…&size=…``),
``pose`` turns four detected corners into a pose with a scale check,
``anchor`` settles repeated detections into one snap of the model, a part
or the environment onto the code. Detection itself happens on the device
with a camera — the Quest app through the passthrough camera API, or a
phone — and arrives over ``POST /api/v1/qr``.
"""

from .payload import AnchorPayload, is_anchor
from .pose import CodePose, pose_from_corners, up_correction
from .anchor import QrSession, Snap, snap_to_code

__all__ = ["AnchorPayload", "is_anchor", "CodePose", "pose_from_corners", "up_correction", "QrSession", "Snap", "snap_to_code"]
