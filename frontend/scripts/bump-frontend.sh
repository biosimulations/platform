#!/usr/bin/env bash
#
# Bump the patch version in frontend/package.json, commit, and tag
# frontend-vX.Y.Z on the current branch.
#
# Usage: frontend/scripts/bump-frontend.sh
#
# Run from anywhere; the script locates the repo root via git.
# Does not push — run `git push && git push origin frontend-vX.Y.Z`
# yourself once you're happy with the bump.

set -euo pipefail

REPO_ROOT=$(git rev-parse --show-toplevel)
cd "$REPO_ROOT/frontend"

if [[ ! -f package.json ]]; then
  echo "error: frontend/package.json not found" >&2
  exit 1
fi

if [[ -n "$(git status --porcelain package.json package-lock.json)" ]]; then
  echo "error: frontend/package.json or package-lock.json has uncommitted changes; commit or stash first" >&2
  exit 1
fi

OLD=$(node -p "require('./package.json').version")

# Bumps package.json and package-lock.json; does not commit or tag.
NEW=$(npm version patch --no-git-tag-version)
NEW=${NEW#v}

cd "$REPO_ROOT"

TAG="frontend-v$NEW"

if git rev-parse -q --verify "refs/tags/$TAG" >/dev/null; then
  echo "error: tag $TAG already exists" >&2
  exit 1
fi

git add frontend/package.json frontend/package-lock.json
git commit -m "Bump frontend to $NEW"
git tag "$TAG"

BRANCH=$(git rev-parse --abbrev-ref HEAD)
echo
echo "Bumped frontend $OLD -> $NEW on branch $BRANCH"
echo "Created tag: $TAG"
echo
echo "To publish:"
echo "  git push origin $BRANCH"
echo "  git push origin $TAG"
