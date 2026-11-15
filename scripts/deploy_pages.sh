#!/usr/bin/env bash
# Publish web/ to the gh-pages branch, which GitHub Pages serves.
# Run from the repo root after committing changes to web/.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
git push origin "$(git subtree split --prefix web HEAD)":refs/heads/gh-pages --force
echo "Deployed. Live in ~1 min: https://hzlylcn.github.io/eudr-plot-screening/"
