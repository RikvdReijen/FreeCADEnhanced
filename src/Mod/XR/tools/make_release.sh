#!/usr/bin/env bash
# SPDX-License-Identifier: LGPL-2.1-or-later
#
# Cut the XR workbench release: tag the commit and publish the release from
# src/Mod/XR/CHANGELOG.md.
#
# The release inherits the repository's visibility. On a private repository it
# is visible only to people with access to that repository — there is no
# separate "private release" switch to set.
#
#   tools/make_release.sh                 # tag xr-v0.1.0 at the branch head
#   tools/make_release.sh xr-v0.2.0       # a different tag
#   tools/make_release.sh xr-v0.1.0 HEAD  # a different commit
#
# Needs push rights for tags. Without the gh CLI it stops after pushing the tag
# and tells you where to click.
set -euo pipefail

TAG="${1:-xr-v0.1.0}"
REF="${2:-HEAD}"

repo_root="$(git rev-parse --show-toplevel)"
notes="$repo_root/src/Mod/XR/CHANGELOG.md"
[ -f "$notes" ] || { echo "no changelog at $notes" >&2; exit 1; }

commit="$(git rev-parse "$REF")"
echo "tagging $TAG at $commit"

# Run the tests first: a release nobody checked is worse than a late one.
if command -v python3 >/dev/null; then
  echo "running the XR test suite…"
  ( cd "$repo_root/src/Mod/XR" && python3 -m unittest discover -s Tests -t . -b )
fi

if git rev-parse -q --verify "refs/tags/$TAG" >/dev/null; then
  echo "tag $TAG already exists locally; reusing it"
else
  git tag -a "$TAG" "$commit" -F "$notes"
fi

git push origin "refs/tags/$TAG"

if command -v gh >/dev/null; then
  gh release create "$TAG" \
    --target "$commit" \
    --title "FreeCAD XR workbench $TAG" \
    --notes-file "$notes" \
    --prerelease
  gh release view "$TAG" --json url --jq .url
else
  remote="$(git remote get-url origin)"
  slug="${remote#*github.com[:/]}"
  slug="${slug%.git}"
  cat <<MSG

The tag is pushed. The gh CLI is not installed, so finish it in the browser:

  https://github.com/$slug/releases/new?tag=$TAG

Paste src/Mod/XR/CHANGELOG.md as the body and tick "Set as a pre-release".
MSG
fi
