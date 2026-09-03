<!-- SPDX-License-Identifier: LGPL-2.1-or-later -->
# Environment specs

This directory is filled at build time. The Gradle task `copyEnvironments`
copies `src/Mod/XR/Resources/environments/*.json` here before the assets are
merged, so the specs live in exactly one place in the repository — the
generated output of `tools/gen_environments.py` in the workbench.

If the source directory is empty the build still succeeds; the app simply
starts with nothing in the room picker. Generate the specs on the desktop
first:

    python3 src/Mod/XR/tools/gen_environments.py

The five that ship with the workbench are `bambu_x1c`, `laser_cutter`,
`workshop`, `studio` and `void`. Their format is ARCHITECTURE.md §2, and how
the app turns them into triangles is documented in `quest/docs/TESSELLATION.md`.
