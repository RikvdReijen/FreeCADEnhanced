# SPDX-License-Identifier: LGPL-2.1-or-later
"""Deviation layers: parallel AI–human collaboration on parametric CAD.

Implements ``docs/concepts/ai-cad-collaboration/``. The concept in one line:
git's three-way merge assumes stable per-line identity, parametric CAD does
not have it, so nothing here merges files. Everything operates on recorded
*operations* against *stable references*, and treats geometry as something to
verify against.

Modules, in the order they build on one another:

``model``      the document view: feature tree, parameters, topology
``schema``     the layer format (reader, writer, validation)
``anchors``    stable references — semantic queries, fingerprints, datums
``claims``     conflict detection before the work starts
``evaluate``   the evaluator interface; structural and scripted evaluators
``replay``     applying a layer's operations to a document
``merge``      two layers, one base, five conflict classes
``stack``      muting, reordering, geometric diff
``contracts``  interface contracts and pinned parameters
``store``      the ``.layers/`` folder
``freecad_adapter``  the bridge to a real FreeCAD document
``cli``        ``python3 -m collab``

Nothing outside ``freecad_adapter`` imports FreeCAD.
"""

from .anchors import Ambiguous, Lost, ResolveOptions, Resolved, record_anchor, resolve, resolve_all
from .claims import ClaimIssue, ClaimRegistry, derive_claims, undeclared_targets
from .contracts import Contract, ContractSet, KeepOut, Mating, Violation
from .errors import CollabError, EvaluationError, LayerFormatError, ReplayError, StoreError
from .evaluate import (
    Evaluator,
    GeometryIssue,
    RecomputeResult,
    ScriptedEvaluator,
    StructuralEvaluator,
    default_evaluator,
)
from .merge import Conflict, CriterionOutcome, MergeResult, merge
from .model import DocumentModel, Entity, Feature
from .replay import ReplayFailure, ReplayResult, rebase, replay, replay_stack
from .schema import (
    SCHEMA_VERSION,
    AddDatum,
    AddFeature,
    Anchor,
    Author,
    Claims,
    Criterion,
    Dependency,
    EditSketch,
    Fingerprint,
    Intent,
    Layer,
    MoveFeature,
    RemoveFeature,
    SetParam,
    SetProperty,
    Validation,
)
from .stack import GeometricDiff, StackResult, evaluate_stack, geometric_diff
from .store import Index, LayerStore, layers_dir_for

__version__ = "0.1.0"
