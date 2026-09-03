// SPDX-License-Identifier: LGPL-2.1-or-later
//
// Copyright (c) 2026 FreeCAD Project Association
//
// This file is part of FreeCAD. FreeCAD is free software: you can redistribute
// it and/or modify it under the terms of the GNU Lesser General Public License
// as published by the Free Software Foundation, either version 2.1 of the
// License, or (at your option) any later version. See <https://www.gnu.org/licenses/>.

using System;
using UnityEngine;

namespace FreeCAD.GameBridge
{
    /// <summary>
    /// The .gbscene manifest, in the shape Unity's JsonUtility can read.
    /// </summary>
    /// <remarks>
    /// JsonUtility cannot deserialise a self-referencing type, which is why the
    /// exporter writes <c>flatNodes</c> alongside the hierarchical <c>nodes</c>:
    /// the same tree, depth first, with each entry naming its parent's index.
    /// Fields the importer does not use are simply left out - JsonUtility
    /// ignores anything it has no field for, which is what lets a newer
    /// exporter add fields without breaking an installed package.
    /// </remarks>
    [Serializable]
    public class GameBridgeManifest
    {
        public const string ExpectedFormat = "freecad-gamebridge-scene";
        public const int SupportedVersion = 1;

        public string format;
        public int version;
        public string bridgeVersion;
        public string generated;
        public string document;
        public string scene;
        public string checksum;
        public GameBridgeTarget target;
        public GameBridgeStats stats;
        public GameBridgeAsset[] assets;
        public GameBridgeMaterial[] materials;
        public GameBridgeNode[] flatNodes;

        public static GameBridgeManifest Parse(string json)
        {
            var manifest = JsonUtility.FromJson<GameBridgeManifest>(json);
            if (manifest == null || manifest.format != ExpectedFormat)
            {
                throw new GameBridgeException("this file is not a GameBridge scene manifest");
            }

            if (manifest.version > SupportedVersion)
            {
                throw new GameBridgeException(string.Format(
                    "the manifest was written by a newer bridge (format {0}, this package reads {1}); " +
                    "update the FreeCAD GameBridge package",
                    manifest.version, SupportedVersion));
            }

            if (manifest.target == null || manifest.target.name != "unity")
            {
                throw new GameBridgeException(string.Format(
                    "this manifest was exported for {0}, not for Unity; re-export with the Unity target",
                    manifest.target == null ? "an unknown target" : manifest.target.name));
            }

            return manifest;
        }

        public GameBridgeAsset FindAsset(int id)
        {
            if (assets == null)
            {
                return null;
            }

            foreach (var asset in assets)
            {
                if (asset.id == id)
                {
                    return asset;
                }
            }

            return null;
        }
    }

    [Serializable]
    public class GameBridgeTarget
    {
        public string name;
        public double mmPerUnit;
        public string up;
        public string forward;
        public string handedness;
        public bool flipsWinding;
    }

    [Serializable]
    public class GameBridgeStats
    {
        public int nodes;
        public int meshes;
        public int materials;
        public int triangles;
        public int vertices;
    }

    [Serializable]
    public class GameBridgeAsset
    {
        public int id;
        public string name;
        public string path;
        public string kind;
        public int triangles;
        public int vertices;
        public string checksum;
        public int material = -1;
        public GameBridgeBounds bounds;
    }

    [Serializable]
    public class GameBridgeBounds
    {
        public float[] min;
        public float[] max;

        public Vector3 Size
        {
            get
            {
                if (min == null || max == null || min.Length < 3 || max.Length < 3)
                {
                    return Vector3.zero;
                }

                return new Vector3(max[0] - min[0], max[1] - min[1], max[2] - min[2]);
            }
        }
    }

    [Serializable]
    public class GameBridgeMaterial
    {
        public string name;
        public float[] baseColor;
        public float metallic;
        public float roughness;
        public float[] emissive;
        public bool doubleSided;
        public string alphaMode;

        public Color BaseColor
        {
            get
            {
                if (baseColor == null || baseColor.Length < 4)
                {
                    return Color.gray;
                }

                return new Color(baseColor[0], baseColor[1], baseColor[2], baseColor[3]);
            }
        }

        public bool IsTransparent
        {
            get { return alphaMode == "BLEND" || alphaMode == "MASK" || BaseColor.a < 1f; }
        }
    }

    [Serializable]
    public class GameBridgeNode
    {
        public int index;
        public int parent = -1;
        public string name;
        public string label;
        public bool visible = true;
        public string source;
        public int asset = -1;
        public GameBridgeTrs trs;
    }

    [Serializable]
    public class GameBridgeTrs
    {
        public float[] translation;
        public float[] rotation;
        public float[] scale;

        public Vector3 Translation
        {
            get { return ToVector(translation, Vector3.zero); }
        }

        /// <summary>The exporter writes quaternions in glTF's (x, y, z, w) order,
        /// which is also Unity's, so no reordering is needed here.</summary>
        public Quaternion Rotation
        {
            get
            {
                if (rotation == null || rotation.Length < 4)
                {
                    return Quaternion.identity;
                }

                var quaternion = new Quaternion(rotation[0], rotation[1], rotation[2], rotation[3]);
                return quaternion.normalized;
            }
        }

        public Vector3 Scale
        {
            get { return ToVector(scale, Vector3.one); }
        }

        private static Vector3 ToVector(float[] values, Vector3 fallback)
        {
            if (values == null || values.Length < 3)
            {
                return fallback;
            }

            return new Vector3(values[0], values[1], values[2]);
        }
    }

    public class GameBridgeException : Exception
    {
        public GameBridgeException(string message) : base(message)
        {
        }
    }
}
