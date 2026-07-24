#!/usr/bin/env bash
#
# Bump the backend version in BOTH backend/biosim_server/version.py and
# backend/pyproject.toml (kept in lockstep), commit, and tag backend-vX.Y.Z
# on the current branch.
#
# Usage:
#   backend/scripts/bump-backend.sh [patch|minor|major]   (default: patch)
#   backend/scripts/bump-backend.sh X.Y.Z                 (explicit version)
#
# Run from anywhere; the script locates the repo root via git.
# Does not push — run `git push && git push origin backend-vX.Y.Z`
# yourself once you're happy with the bump. Pushing the tag triggers the
# `release` workflow, which builds + pushes the images and cuts a Release.

set -euo pipefail

REPO_ROOT=$(git rev-parse --show-toplevel)
VERSION_PY="$REPO_ROOT/backend/biosim_server/version.py"
PYPROJECT="$REPO_ROOT/backend/pyproject.toml"
UVLOCK="$REPO_ROOT/backend/uv.lock"

for f in "$VERSION_PY" "$PYPROJECT" "$UVLOCK"; do
  [[ -f "$f" ]] || { echo "error: $f not found" >&2; exit 1; }
done

if [[ -n "$(git -C "$REPO_ROOT" status --porcelain "$VERSION_PY" "$PYPROJECT")" ]]; then
  echo "error: version.py or pyproject.toml has uncommitted changes; commit or stash first" >&2
  exit 1
fi

OLD=$(grep -oE '[0-9]+\.[0-9]+\.[0-9]+' "$VERSION_PY" | head -1)
[[ -n "$OLD" ]] || { echo "error: could not read current version from version.py" >&2; exit 1; }

LEVEL="${1:-patch}"
if [[ "$LEVEL" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  NEW="$LEVEL"
else
  IFS='.' read -r MAJ MIN PAT <<< "$OLD"
  case "$LEVEL" in
    major) NEW="$((MAJ + 1)).0.0" ;;
    minor) NEW="${MAJ}.$((MIN + 1)).0" ;;
    patch) NEW="${MAJ}.${MIN}.$((PAT + 1))" ;;
    *) echo "error: expected patch|minor|major or X.Y.Z, got '$LEVEL'" >&2; exit 1 ;;
  esac
fi

TAG="backend-v$NEW"
if git -C "$REPO_ROOT" rev-parse -q --verify "refs/tags/$TAG" >/dev/null; then
  echo "error: tag $TAG already exists" >&2
  exit 1
fi

# version.py is a single line with no trailing newline — preserve that.
printf '__version__ = "%s"' "$NEW" > "$VERSION_PY"

# pyproject.toml: bump the [project] version line.
perl -0pi -e "s/^version = \"\Q$OLD\E\"/version = \"$NEW\"/m" "$PYPROJECT"

# uv.lock: keep the biosim-server package entry's version in lockstep with
# pyproject (it's package=false, so this is the only reference and no re-resolve
# is needed). Replaces whatever version is there, so it also fixes prior drift.
perl -0pi -e "s/(\nname = \"biosim-server\"\nversion = \")[^\"]*(\")/\${1}$NEW\${2}/" "$UVLOCK"

git -C "$REPO_ROOT" add "$VERSION_PY" "$PYPROJECT" "$UVLOCK"
git -C "$REPO_ROOT" commit -m "Bump backend to $NEW"
git -C "$REPO_ROOT" tag "$TAG"

BRANCH=$(git -C "$REPO_ROOT" rev-parse --abbrev-ref HEAD)
echo
echo "Bumped backend $OLD -> $NEW on branch $BRANCH"
echo "Created tag: $TAG"
echo
echo "To publish (triggers the release workflow):"
echo "  git push origin $BRANCH"
echo "  git push origin $TAG"
