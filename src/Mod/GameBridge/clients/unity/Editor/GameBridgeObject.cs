// SPDX-License-Identifier: LGPL-2.1-or-later
//
// Copyright (c) 2026 FreeCAD Project Association
//
// This file is part of FreeCAD. FreeCAD is free software: you can redistribute
// it and/or modify it under the terms of the GNU Lesser General Public License
// as published by the Free Software Foundation, either version 2.1 of the
// License, or (at your option) any later version. See <https://www.gnu.org/licenses/>.

using UnityEngine;

namespace FreeCAD.GameBridge
{
    /// <summary>
    /// Remembers which FreeCAD object a GameObject came from.
    /// </summary>
    /// <remarks>
    /// Without this a re-import has no way to tell that the bracket in the
    /// scene and the bracket in the new export are the same thing, and the
    /// artist loses every component they attached. It also gives anyone
    /// debugging a model a way back to the part that produced it.
    /// </remarks>
    [DisallowMultipleComponent]
    public class GameBridgeObject : MonoBehaviour
    {
        [Tooltip("Name of the FreeCAD document this object was exported from.")]
        public string FreeCadDocument;

        [Tooltip("Internal name of the FreeCAD object, which is stable across renames.")]
        public string FreeCadObject;

        [Tooltip("The label the object had in FreeCAD when it was exported.")]
        public string SourceLabel;
    }
}
