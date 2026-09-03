// SPDX-License-Identifier: LGPL-2.1-or-later
//
// Copyright (c) 2026 FreeCAD Project Association
//
// This file is part of FreeCAD. FreeCAD is free software: you can redistribute
// it and/or modify it under the terms of the GNU Lesser General Public License
// as published by the Free Software Foundation, either version 2.1 of the
// License, or (at your option) any later version. See <https://www.gnu.org/licenses/>.

using System.Collections.Generic;
using System.IO;
using UnityEditor;
using UnityEngine;

namespace FreeCAD.GameBridge
{
    /// <summary>
    /// Rebuilds a FreeCAD export as a Unity hierarchy.
    /// </summary>
    /// <remarks>
    /// The mesh files arrive already converted into Unity's space - metres, Y
    /// up, left handed, with the winding reversed to match. A glTF importer
    /// that converts again leaves the model on its side, so after each asset is
    /// loaded its bounds are measured against the size the manifest recorded
    /// and a mismatch is reported with the setting to change. Guessing which
    /// glTF package the project uses is not possible; checking the result is.
    /// </remarks>
    public static class GameBridgeImporter
    {
        /// <summary>How far an imported asset may differ from the manifest before we complain.</summary>
        public const float BoundsTolerance = 0.02f;

        public static GameObject Import(string manifestPath, GameBridgeImportOptions options = null)
        {
            options = options ?? new GameBridgeImportOptions();
            var json = File.ReadAllText(manifestPath);
            var manifest = GameBridgeManifest.Parse(json);
            var directory = Path.GetDirectoryName(Path.GetFullPath(manifestPath));

            var rootName = string.IsNullOrEmpty(options.RootName)
                ? (string.IsNullOrEmpty(manifest.scene) ? "FreeCAD" : manifest.scene)
                : options.RootName;

            var existing = GameObject.Find(rootName);
            if (existing != null && options.ReplaceExisting)
            {
                // A re-import replaces the previous one instead of stacking a
                // second copy in the same place.
                Object.DestroyImmediate(existing);
            }

            var root = new GameObject(rootName);
            Undo.RegisterCreatedObjectUndo(root, "Import FreeCAD scene");

            var meshes = LoadAssets(manifest, directory);
            var objects = new Dictionary<int, Transform>();

            if (manifest.flatNodes != null)
            {
                foreach (var node in manifest.flatNodes)
                {
                    var created = CreateNode(node, manifest, meshes, options);
                    Transform parent;
                    created.transform.SetParent(
                        node.parent >= 0 && objects.TryGetValue(node.parent, out parent)
                            ? parent
                            : root.transform,
                        false);
                    objects[node.index] = created.transform;
                }
            }

            Debug.Log(string.Format(
                "GameBridge: imported {0} ({1} object(s), {2} triangle(s)) from {3}",
                rootName,
                objects.Count,
                manifest.stats == null ? 0 : manifest.stats.triangles,
                manifestPath));

            if (options.CreatePrefab)
            {
                SavePrefab(root, directory, rootName);
            }

            Selection.activeGameObject = root;
            return root;
        }

        private static GameObject CreateNode(
            GameBridgeNode node,
            GameBridgeManifest manifest,
            Dictionary<int, GameObject> meshes,
            GameBridgeImportOptions options)
        {
            GameObject created = null;
            GameObject source;
            if (node.asset >= 0 && meshes.TryGetValue(node.asset, out source) && source != null)
            {
                created = (GameObject)PrefabUtility.InstantiatePrefab(source);
            }

            if (created == null)
            {
                created = new GameObject();
            }

            created.name = string.IsNullOrEmpty(node.name) ? node.label : node.name;

            var transform = created.transform;
            if (node.trs != null)
            {
                transform.localPosition = node.trs.Translation;
                transform.localRotation = node.trs.Rotation;
                transform.localScale = node.trs.Scale;
            }

            if (!node.visible)
            {
                created.SetActive(false);
            }

            if (options.MarkStatic)
            {
                GameObjectUtility.SetStaticEditorFlags(
                    created, StaticEditorFlags.ContributeGI | StaticEditorFlags.BatchingStatic);
            }

            if (options.GenerateColliders && created.GetComponent<MeshFilter>() != null)
            {
                created.AddComponent<MeshCollider>();
            }

            var marker = created.AddComponent<GameBridgeObject>();
            marker.FreeCadDocument = manifest.document;
            marker.FreeCadObject = node.source;
            marker.SourceLabel = node.label;

            Undo.RegisterCreatedObjectUndo(created, "Import FreeCAD object");
            return created;
        }

        private static Dictionary<int, GameObject> LoadAssets(GameBridgeManifest manifest, string directory)
        {
            var loaded = new Dictionary<int, GameObject>();
            if (manifest.assets == null)
            {
                return loaded;
            }

            foreach (var asset in manifest.assets)
            {
                if (string.IsNullOrEmpty(asset.path))
                {
                    continue;
                }

                var absolute = Path.GetFullPath(Path.Combine(directory, asset.path.Replace('/', Path.DirectorySeparatorChar)));
                var relative = ToProjectPath(absolute);
                if (relative == null)
                {
                    Debug.LogWarningFormat(
                        "GameBridge: {0} is outside the Unity project, so Unity cannot import it. " +
                        "Export into a folder under Assets/.", absolute);
                    continue;
                }

                AssetDatabase.ImportAsset(relative, ImportAssetOptions.ForceUpdate);
                var prefab = AssetDatabase.LoadAssetAtPath<GameObject>(relative);
                if (prefab == null)
                {
                    Debug.LogWarningFormat(
                        "GameBridge: Unity could not import {0}. A glTF importer package " +
                        "(glTFast or UnityGLTF) has to be installed for .glb files.", relative);
                    continue;
                }

                loaded[asset.id] = prefab;
                WarnIfConvertedTwice(asset, prefab);
            }

            return loaded;
        }

        /// <summary>Compare an imported asset against the size the exporter recorded.</summary>
        private static void WarnIfConvertedTwice(GameBridgeAsset asset, GameObject prefab)
        {
            if (asset.bounds == null)
            {
                return;
            }

            var expected = asset.bounds.Size;
            if (expected == Vector3.zero)
            {
                return;
            }

            var filter = prefab.GetComponentInChildren<MeshFilter>();
            if (filter == null || filter.sharedMesh == null)
            {
                return;
            }

            var actual = filter.sharedMesh.bounds.size;
            var tolerance = BoundsTolerance * Mathf.Max(expected.x, expected.y, expected.z);
            if (Vector3.Distance(expected, actual) <= tolerance)
            {
                return;
            }

            var ratio = actual.magnitude / Mathf.Max(1e-6f, expected.magnitude);
            if (Mathf.Abs(ratio - 1f) > BoundsTolerance && Mathf.Abs(actual.x - expected.x) > tolerance)
            {
                Debug.LogWarningFormat(
                    "GameBridge: {0} imported at {1}, but the export says {2}. Every axis is " +
                    "{3:0.###} times too large, so the glTF importer converted units the exporter " +
                    "had already converted - set its scale factor to 1.",
                    asset.name, actual, expected, ratio);
                return;
            }

            Debug.LogWarningFormat(
                "GameBridge: {0} imported at {1}, but the export says {2}. The axes look " +
                "permuted, so the glTF importer converted the coordinate system a second time - " +
                "turn its axis conversion off.",
                asset.name, actual, expected);
        }

        private static void SavePrefab(GameObject root, string directory, string name)
        {
            var folder = ToProjectPath(Path.Combine(directory, "Prefabs"));
            if (folder == null)
            {
                Debug.LogWarning("GameBridge: prefabs can only be written inside the project's Assets folder.");
                return;
            }

            if (!AssetDatabase.IsValidFolder(folder))
            {
                var parent = Path.GetDirectoryName(folder).Replace('\\', '/');
                AssetDatabase.CreateFolder(parent, "Prefabs");
            }

            var path = AssetDatabase.GenerateUniqueAssetPath(folder + "/" + name + ".prefab");
            PrefabUtility.SaveAsPrefabAssetAndConnect(root, path, InteractionMode.AutomatedAction);
            Debug.LogFormat("GameBridge: saved prefab {0}", path);
        }

        /// <summary>Turn an absolute path into the "Assets/..." form Unity's asset database needs.</summary>
        public static string ToProjectPath(string absolute)
        {
            var full = Path.GetFullPath(absolute).Replace('\\', '/');
            var root = Path.GetFullPath(Application.dataPath).Replace('\\', '/');
            if (!full.StartsWith(root + "/", System.StringComparison.OrdinalIgnoreCase))
            {
                return null;
            }

            return "Assets/" + full.Substring(root.Length + 1);
        }
    }

    public class GameBridgeImportOptions
    {
        public string RootName;
        public bool ReplaceExisting = true;
        public bool CreatePrefab = true;
        public bool GenerateColliders;
        public bool MarkStatic = true;
    }
}
