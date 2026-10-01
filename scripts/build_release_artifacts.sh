#!/usr/bin/env bash
set -euo pipefail

artifact_root="${1:-dist/release}"
rm -rf -- "${artifact_root}"
mkdir -p "${artifact_root}/python" "${artifact_root}/npm"

bash scripts/build_python_artifacts.sh "${artifact_root}/python"
pnpm --dir ts build
pnpm --dir ts pack --pack-destination "$(cd "${artifact_root}/npm" && pwd -P)"
npm_archives=("${artifact_root}"/npm/gainratio-assay-*.tgz)
if [[ ${#npm_archives[@]} -ne 1 || ! -f ${npm_archives[0]} ]]; then
  echo "Expected exactly one @gainratio/assay tarball" >&2
  exit 1
fi
node ts/scripts/normalize-package-archive.mjs "${npm_archives[0]}"
uv run python scripts/verify_release_artifacts.py "${artifact_root}"
