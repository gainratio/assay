#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
ROOT=$(cd -- "$SCRIPT_DIR/.." && pwd -P)
TMP=$(mktemp -d "${TMPDIR:-/tmp}/assay-example.XXXXXX")

cleanup() {
  rm -rf -- "$TMP"
}
trap cleanup EXIT INT TERM

run_step() {
  local name=$1
  shift
  if ! "$@" >"$TMP/$name.log" 2>&1; then
    cat "$TMP/$name.log" >&2
    return 1
  fi
}

ARTIFACTS="$TMP/artifacts"
PY_ENV="$TMP/python"
TS_COPY="$TMP/typescript"
NODE_APP="$TMP/node-app"
REQUEST="$SCRIPT_DIR/northstar_score.json"
ORACLE="$ROOT/testdata/vectors/composition.json"
PY_RESULT="$TMP/python-result.json"
TS_RESULT="$TMP/typescript-result.json"
mkdir -p "$ARTIFACTS" "$TS_COPY" "$NODE_APP"

copy_typescript_checkout() {
  local relative target
  while IFS= read -r -d '' relative; do
    target="$TS_COPY/${relative#ts/}"
    mkdir -p "$(dirname "$target")"
    cp "$ROOT/$relative" "$target"
  done < <(git -C "$ROOT" ls-files -z -- ts)
}

run_step python-build uv build --wheel --out-dir "$ARTIFACTS" "$ROOT"
WHEELS=("$ARTIFACTS"/assay_engine-*.whl)
if [[ ${#WHEELS[@]} -ne 1 || ! -f ${WHEELS[0]} ]]; then
  echo "Expected exactly one assay-engine wheel" >&2
  exit 1
fi
run_step python-environment uv venv --python 3.13 "$PY_ENV"
run_step python-install uv pip install --python "$PY_ENV/bin/python" "${WHEELS[0]}[cli]"
run_step python-compose "$PY_ENV/bin/assay" compose --request "$REQUEST" --out "$PY_RESULT"

copy_typescript_checkout
NPM_JS=$(realpath "$(command -v npm)")
if ! NODE22=$(npx --yes --package=node@22.13.0 -c 'command -v node' 2>"$TMP/node22.log"); then
  cat "$TMP/node22.log" >&2
  exit 1
fi
if ! PNPM=$(npx --yes --package=pnpm@11.5.0 -c 'command -v pnpm' 2>"$TMP/pnpm.log"); then
  cat "$TMP/pnpm.log" >&2
  exit 1
fi
PNPM_JS=$(realpath "$PNPM")
NODE22_PATH="$(dirname "$NODE22"):$(dirname "$PNPM"):$PATH"
if [[ $("$NODE22" --version) != v22.* ]]; then
  echo "The example requires Node 22" >&2
  exit 1
fi
run_step node-install env PATH="$NODE22_PATH" "$NODE22" "$PNPM_JS" --dir "$TS_COPY" \
  install --frozen-lockfile
run_step node-pack env PATH="$NODE22_PATH" "$NODE22" "$PNPM_JS" --dir "$TS_COPY" pack \
  --pack-destination "$ARTIFACTS"
TARBALLS=("$ARTIFACTS"/gainratio-assay-*.tgz)
if [[ ${#TARBALLS[@]} -ne 1 || ! -f ${TARBALLS[0]} ]]; then
  echo "Expected exactly one @gainratio/assay tarball" >&2
  exit 1
fi
run_step node-normalize "$NODE22" "$TS_COPY/scripts/normalize-package-archive.mjs" \
  "${TARBALLS[0]}"
if [[ -n ${ASSAY_EXAMPLE_ARTIFACT_DIR:-} ]]; then
  mkdir -p "$ASSAY_EXAMPLE_ARTIFACT_DIR"
  cp "${TARBALLS[0]}" "$ASSAY_EXAMPLE_ARTIFACT_DIR/"
fi

cat >"$NODE_APP/package.json" <<'JSON'
{"private":true,"type":"module"}
JSON
run_step node-package-install "$NODE22" "$NPM_JS" install --prefix "$NODE_APP" \
  --ignore-scripts --no-audit --no-fund "${TARBALLS[0]}"
cat >"$NODE_APP/compose.mjs" <<'JAVASCRIPT'
import { readFileSync, writeFileSync } from "node:fs";
import { compose, parseRequest } from "@gainratio/assay";

const [requestPath, resultPath] = process.argv.slice(2);
const request = parseRequest(JSON.parse(readFileSync(requestPath, "utf8")));
const result = compose(request);
writeFileSync(resultPath, `${JSON.stringify(result)}\n`, "utf8");
JAVASCRIPT
run_step node-compose "$NODE22" "$NODE_APP/compose.mjs" "$REQUEST" "$TS_RESULT"

cat >"$TMP/verify.py" <<'PYTHON'
from __future__ import annotations

import json
import struct
import sys
from pathlib import Path


def load(path: str) -> object:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def kind(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    raise AssertionError("unsupported JSON value")


def same_number(first: object, second: object) -> bool:
    return struct.pack(">d", float(first)) == struct.pack(">d", float(second))


def compare(expected: object, actual: object, path: str = "$") -> None:
    assert kind(actual) == kind(expected), f"{path}: JSON scalar kind differs"
    if isinstance(expected, dict):
        assert isinstance(actual, dict)
        assert list(actual) == list(expected), f"{path}: ordered fields differ"
        for name in expected:
            compare(expected[name], actual[name], f"{path}.{name}")
    elif isinstance(expected, list):
        assert isinstance(actual, list) and len(actual) == len(expected), f"{path}: array differs"
        for index, value in enumerate(expected):
            compare(value, actual[index], f"{path}[{index}]")
    elif kind(expected) == "number":
        assert same_number(expected, actual), f"{path}: binary64 value differs"
    else:
        assert actual == expected, f"{path}: value differs"


def northstar(vectors: object) -> dict[str, object]:
    assert isinstance(vectors, list)
    return next(item for item in vectors if item["id"] == "northstar_uncapped_weighted")


def number(value: object) -> str:
    return format(float(value), ".15g")


request_path, oracle_path, python_path, typescript_path = sys.argv[1:]
vector = northstar(load(oracle_path))
request = load(request_path)
python_result = load(python_path)
typescript_result = load(typescript_path)
compare(vector["request"], request)
compare(vector["expected"], python_result)
compare(vector["expected"], typescript_result)
compare(python_result, typescript_result)
assert isinstance(request, dict) and isinstance(python_result, dict)
assert python_result["score"] == 0.92
assert python_result["interval"] is None
assert python_result["inputs_hash"] == (
    "sha256:0266b1c59c97bacf85dc945685c55bb4386856b525249c7d5663a8edf020ba06"
)
rows = python_result["components"]
components = request["components"]
assert isinstance(rows, list) and isinstance(components, list)
assert [row["id"] for row in rows] == [
    "security",
    "privacy",
    "reliability",
    "performance",
    "correctness",
    "clarity",
    "production",
]
method = python_result["method"]
assert isinstance(method, dict)
print(f"Northstar weighted score: {python_result['score']:.2f}")
print(f"Method: {method['id']} @ {method['version']}")
print("Interval: null — all inputs are deterministic\n")
for source, row in zip(components, rows, strict=True):
    ratio = f"{number(row['raw'])}/{number(source['scale']['maximum'])}"
    print(
        f"{row['id']:<14} {ratio:>5}  -> {row['normalized']:.6f} "
        f"× {row['coefficient']:.2f} = {row['contribution']:.2f}"
    )
print(f"\nTotal: {python_result['score']:.2f}")
print(f"inputs_hash: {python_result['inputs_hash']}")
print("Parity: Python and TypeScript fields and values match")
PYTHON

"$PY_ENV/bin/python" "$TMP/verify.py" "$REQUEST" "$ORACLE" "$PY_RESULT" "$TS_RESULT"
