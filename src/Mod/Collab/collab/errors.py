# SPDX-License-Identifier: LGPL-2.1-or-later
"""Exceptions raised by the collaboration module.

Every error carries enough context to say *which* layer, operation or file was
at fault, because these are read by agents as often as by people.
"""


class CollabError(Exception):
    """Base class for everything this module raises."""


class LayerFormatError(CollabError):
    """A layer or index document does not match the schema.

    The message names the offending field path, e.g. ``operations[2].target``.
    """

    def __init__(self, message, path=None):
        self.path = path
        super().__init__(f"{path}: {message}" if path else message)


class StoreError(CollabError):
    """The ``.layers/`` folder is missing, inconsistent or unwritable."""


class ReplayError(CollabError):
    """An operation could not be applied to a document.

    Raised only for programming errors; ordinary replay failures are reported
    as :class:`collab.replay.ReplayFailure` entries rather than raised, because
    a failed replay is data the merge algorithm needs, not an exception.
    """


class EvaluationError(CollabError):
    """A geometry evaluator failed in a way that is not a modelling result."""
