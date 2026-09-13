// SPDX-License-Identifier: LGPL-2.1-or-later
//
// Copyright (c) 2026 FreeCAD Project Association
//
// This file is part of FreeCAD. FreeCAD is free software: you can redistribute
// it and/or modify it under the terms of the GNU Lesser General Public License
// as published by the Free Software Foundation, either version 2.1 of the
// License, or (at your option) any later version. See <https://www.gnu.org/licenses/>.

using System.IO;
using UnityEditor;
using UnityEngine;

namespace FreeCAD.GameBridge
{
    /// <summary>Menu entries and the asset post-processor that watches for exports.</summary>
    public static class GameBridgeMenu
    {
        [MenuItem("Window/FreeCAD GameBridge/Import Scene...")]
        public static void ImportScene()
        {
            var path = EditorUtility.OpenFilePanel(
                "Import FreeCAD GameBridge scene", Application.dataPath, "gbscene");
            if (string.IsNullOrEmpty(path))
            {
                return;
            }

            try
            {
                GameBridgeImporter.Import(path);
            }
            catch (GameBridgeException error)
            {
                EditorUtility.DisplayDialog("FreeCAD GameBridge", error.Message, "OK");
            }
        }

        [MenuItem("Window/FreeCAD GameBridge/About")]
        public static void About()
        {
            EditorUtility.DisplayDialog(
                "FreeCAD GameBridge",
                "Imports scenes exported from FreeCAD's GameBridge workbench.\n\n" +
                "Export from FreeCAD with the Unity target into a folder inside this " +
                "project's Assets directory. Unity picks the export up on its next " +
                "refresh, or you can import it by hand from this menu.\n\n" +
                "A glTF importer package (glTFast or UnityGLTF) has to be installed " +
                "for .glb assets.",
                "OK");
        }
    }

    /// <summary>
    /// Runs an export as soon as Unity notices it.
    /// </summary>
    /// <remarks>
    /// The exporter writes a small .gbimport job file next to the manifest.
    /// Unity has no way to be told about a file that appeared while it was in
    /// the background, but it re-scans the project when it regains focus, and
    /// that scan is what brings us here. The job file is deleted once it has
    /// been acted on, so a later refresh does not import the same scene twice.
    /// </remarks>
    public class GameBridgeJobWatcher : AssetPostprocessor
    {
        private static void OnPostprocessAllAssets(
            string[] imported, string[] deleted, string[] moved, string[] movedFrom)
        {
            foreach (var path in imported)
            {
                if (!path.EndsWith(".gbimport", System.StringComparison.OrdinalIgnoreCase))
                {
                    continue;
                }

                EditorApplication.delayCall += () => RunJob(path);
            }
        }

        private static void RunJob(string jobPath)
        {
            if (!File.Exists(jobPath))
            {
                return;
            }

            GameBridgeJob job;
            try
            {
                job = JsonUtility.FromJson<GameBridgeJob>(File.ReadAllText(jobPath));
            }
            catch (System.Exception error)
            {
                Debug.LogWarningFormat("GameBridge: could not read {0}: {1}", jobPath, error.Message);
                return;
            }

            var directory = Path.GetDirectoryName(jobPath);
            var manifest = Path.Combine(directory, string.IsNullOrEmpty(job.manifest) ? "scene.gbscene" : job.manifest);
            if (!File.Exists(manifest))
            {
                Debug.LogWarningFormat("GameBridge: {0} refers to {1}, which is missing.", jobPath, manifest);
                return;
            }

            var accepted = EditorUtility.DisplayDialog(
                "FreeCAD GameBridge",
                string.Format("A FreeCAD export appeared in the project:\n\n{0}\n\nImport it now?", manifest),
                "Import", "Not now");

            // Whether or not it was imported, the job has been seen; leaving it
            // would re-ask on every refresh until the user gives in.
            AssetDatabase.DeleteAsset(jobPath.Replace('\\', '/'));

            if (!accepted)
            {
                return;
            }

            try
            {
                GameBridgeImporter.Import(manifest, new GameBridgeImportOptions
                {
                    RootName = job.rootName,
                    CreatePrefab = job.createPrefabs,
                    GenerateColliders = job.generateColliders,
                    MarkStatic = job.staticFlags,
                });
            }
            catch (GameBridgeException error)
            {
                Debug.LogError("GameBridge: " + error.Message);
            }
        }
    }

    [System.Serializable]
    public class GameBridgeJob
    {
        public string manifest;
        public string rootName;
        public bool createPrefabs = true;
        public bool generateColliders;
        public bool staticFlags = true;
    }
}
