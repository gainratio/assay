#!/usr/bin/env bash
{ set +x; } 2>/dev/null
unset PS4
set -euo pipefail

readonly NPM_REGISTRY="https://registry.npmjs.org/"
readonly NPM_PACKAGE="@gainratio/assay"
readonly NPM_PACKAGE_URL="https://registry.npmjs.org/%40gainratio%2Fassay"
readonly BOOTSTRAP_VERSION="0.0.0-bootstrap.0"
readonly POLL_ATTEMPTS=6
readonly POLL_SECONDS=2

script_dir="$(cd -- "${BASH_SOURCE[0]%/*}" && pwd -P)"
repo_root="$(cd -- "$script_dir/.." && pwd -P)"
verifier="$repo_root/scripts/verify_release_artifacts.py"
trusted_license="$repo_root/LICENSE"
publish=false
mode="release"
mode_selected=false
artifact_root=""
runtime_root=""
snapshot_root=""
npm_token="${HARISH_NPM_TOKEN-}"
unset HARISH_PYPI_TOKEN HARISH_NPM_TOKEN PYPI_API_TOKEN NPM_TOKEN
unset NPM_CONFIG_DRY_RUN NPM_CONFIG_REGISTRY NPM_CONFIG_PROVENANCE
unset NPM_CONFIG_IGNORE_SCRIPTS NPM_CONFIG_USERCONFIG
unset npm_config_dry_run npm_config_registry npm_config_provenance
unset npm_config_ignore_scripts npm_config_userconfig

usage() {
  echo "usage: scripts/npm-publish.sh [--release|--bootstrap] [--publish] [ARTIFACT_ROOT]" >&2
}

die() {
  echo "$1" >&2
  exit 1
}

cleanup() {
  [[ -z "$runtime_root" ]] || rm -rf -- "$runtime_root"
}

trap cleanup EXIT HUP INT TERM

select_mode() {
  [[ "$mode_selected" == false ]] || { usage; exit 2; }
  mode="${1#--}"
  mode_selected=true
}

parse_arguments() {
  local argument
  for argument in "$@"; do
    case "$argument" in
      --release|--bootstrap) select_mode "$argument" ;;
      --publish) publish=true ;;
      -*) usage; exit 2 ;;
      *) [[ -z "$artifact_root" ]] || { usage; exit 2; }; artifact_root="$argument" ;;
    esac
  done
  artifact_root="${artifact_root:-$repo_root/dist/release}"
}

reject_manual_release() {
  if [[ "$publish" == true && "$mode" == release ]]; then
    die "real @gainratio/assay releases require the GitHub Actions OIDC workflow for provenance"
  fi
}

reject_unsafe_tree() {
  local root="$1" listing="$2" entry
  [[ -d "$root" && ! -L "$root" ]] || die "release root is a symlink or nonregular"
  /usr/bin/find "$root" -mindepth 1 -print0 >"$listing"
  while IFS= read -r -d '' entry; do
    [[ ! -L "$entry" && ( -d "$entry" || -f "$entry" ) ]] ||
      die "release envelope contains a symlink or nonregular member"
  done <"$listing"
}

initialize_snapshot() {
  umask 077
  runtime_root="$(mktemp -d "${TMPDIR:-/tmp}/assay-npm-publish.XXXXXX")"
  runtime_root="$(cd -- "$runtime_root" && pwd -P)"
  chmod 700 "$runtime_root"
  snapshot_root="$runtime_root/release"
  mkdir -m 700 "$snapshot_root"
  reject_unsafe_tree "$artifact_root" "$runtime_root/source-members"
  cp -R -- "$artifact_root/." "$snapshot_root/"
  reject_unsafe_tree "$snapshot_root" "$runtime_root/snapshot-members"
}

verify_snapshot() {
  python3 "$verifier" "$snapshot_root"
}

select_release() {
  local archives filename number='(0|[1-9][0-9]*)'
  shopt -s nullglob
  archives=("$snapshot_root"/npm/gainratio-assay-*.tgz)
  [[ ${#archives[@]} -eq 1 ]] || die "expected one reviewed @gainratio/assay tarball"
  release_archive="${archives[0]}"
  filename="${release_archive##*/gainratio-assay-}"
  release_version="${filename%.tgz}"
  [[ "$release_version" =~ ^${number}\.${number}\.${number}(-dev\.${number})?$ ]] ||
    die "malformed npm version: $release_version"
  [[ "$release_version" == *-dev.* ]] && release_tag="next" || release_tag="latest"
}

fetch_metadata() {
  local url="$1"
  curl --silent --show-error --output "$metadata_file" --write-out '%{http_code}' \
    --connect-timeout 5 --max-time 20 --max-filesize 1048576 "$url"
}

verify_version_record() {
  python3 - "$metadata_file" "$release_archive" "$release_version" <<'PY'
import base64
import hashlib
import json
import pathlib
import sys

payload = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
archive = pathlib.Path(sys.argv[2])
expected = "sha512-" + base64.b64encode(hashlib.sha512(archive.read_bytes()).digest()).decode()
identity = (payload.get("name"), payload.get("version"))
if identity != ("@gainratio/assay", sys.argv[3]) or payload.get("dist", {}).get("integrity") != expected:
    raise SystemExit(f"@gainratio/assay {sys.argv[3]} already exists with conflicting bytes")
PY
}

verify_package_record() {
  local archive="$1" version="$2" tag="$3"
  python3 - "$metadata_file" "$archive" "$version" "$tag" <<'PY'
import base64
import hashlib
import json
import pathlib
import sys

payload = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
archive = pathlib.Path(sys.argv[2])
expected = "sha512-" + base64.b64encode(hashlib.sha512(archive.read_bytes()).digest()).decode()
record = payload.get("versions", {}).get(sys.argv[3], {})
identity = (payload.get("name"), record.get("name"), record.get("version"))
tagged = payload.get("dist-tags", {}).get(sys.argv[4])
if identity != ("@gainratio/assay", "@gainratio/assay", sys.argv[3]):
    raise SystemExit("npm package identity is malformed")
if record.get("dist", {}).get("integrity") != expected:
    raise SystemExit(f"@gainratio/assay {sys.argv[3]} registry integrity conflicts")
if tagged != sys.argv[3]:
    raise SystemExit(f"expected {sys.argv[4]} dist-tag to identify {sys.argv[3]}")
PY
}

verify_bootstrap_shape() {
  python3 - "$metadata_file" "$BOOTSTRAP_VERSION" <<'PY'
import json
import pathlib
import sys

payload = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
version = sys.argv[2]
versions = payload.get("versions", {})
record = versions.get(version, {})
identity = (payload.get("name"), record.get("name"), record.get("version"))
tags = payload.get("dist-tags")
if identity != ("@gainratio/assay", "@gainratio/assay", version):
    raise SystemExit("bootstrap registry state conflicts with the required identity")
if set(versions) != {version} or tags != {"bootstrap": version}:
    raise SystemExit("bootstrap registry state conflicts with the required package state")
PY
}

prepare_bootstrap_archive() {
  [[ -z "$bootstrap_archive" ]] || return 0
  write_bootstrap_package
  pack_bootstrap
}

verify_exact_bootstrap() {
  verify_bootstrap_shape
  prepare_bootstrap_archive
  verify_package_record "$bootstrap_archive" "$BOOTSTRAP_VERSION" bootstrap ||
    die "bootstrap registry state conflicts with the reviewed bytes"
}

check_bootstrap_state() {
  local status
  status="$(fetch_metadata "$NPM_PACKAGE_URL")" || die "npm registry check failed"
  case "$status" in
    404) bootstrap_state="missing" ;;
    200) verify_exact_bootstrap; bootstrap_state="exact" ;;
    *) die "npm registry check failed with HTTP $status" ;;
  esac
}

check_release_state() {
  local status package_status version_url="$NPM_PACKAGE_URL/$release_version"
  status="$(fetch_metadata "$version_url")" || die "npm registry check failed"
  if [[ "$status" == 404 ]]; then release_state="missing"; return; fi
  [[ "$status" == 200 ]] || die "npm registry check failed with HTTP $status"
  verify_version_record
  package_status="$(fetch_metadata "$NPM_PACKAGE_URL")" || die "npm package check failed"
  [[ "$package_status" == 200 ]] || die "npm package check failed with HTTP $package_status"
  verify_package_record "$release_archive" "$release_version" "$release_tag"
  release_state="exact"
}

print_guidance() {
  echo "An npm granular write token can bootstrap @gainratio/assay; export HARISH_NPM_TOKEN in the caller only with --publish."
  echo "Trusted-publisher setup still needs account authentication and 2FA; bypass tokens cannot configure trust."
  echo "All real releases must use the OIDC workflow for provenance."
}

write_auth_config() {
  auth_config="$runtime_root/.npmrc"
  # npm expands this literal from the tightly scoped child environment.
  # shellcheck disable=SC2016
  printf '%s\n' 'registry=https://registry.npmjs.org/' \
    '//registry.npmjs.org/:_authToken=${NPM_TOKEN}' >"$auth_config"
  chmod 600 "$auth_config"
}

write_bootstrap_package() {
  bootstrap_root="$runtime_root/bootstrap"
  mkdir -m 700 "$bootstrap_root"
  cat >"$bootstrap_root/package.json" <<'JSON'
{"name":"@gainratio/assay","version":"0.0.0-bootstrap.0","description":"Harmless bootstrap placeholder for configuring npm trusted publishing; do not install.","license":"MIT","files":["README.md","LICENSE"]}
JSON
  printf '%s\n' '# Bootstrap placeholder' '' 'Do not install. Real releases use OIDC trusted publishing.' >"$bootstrap_root/README.md"
  cp -- "$trusted_license" "$bootstrap_root/LICENSE"
}

pack_bootstrap() {
  NPM_CONFIG_DRY_RUN=false NPM_CONFIG_PROVENANCE=false NPM_CONFIG_IGNORE_SCRIPTS=true \
    NPM_CONFIG_REGISTRY="$NPM_REGISTRY" NPM_CONFIG_USERCONFIG=/dev/null \
    npm pack "$bootstrap_root" --pack-destination "$runtime_root" --json --dry-run=false \
    --provenance=false --ignore-scripts=true --registry "$NPM_REGISTRY" >/dev/null
  bootstrap_archive="$runtime_root/gainratio-assay-$BOOTSTRAP_VERSION.tgz"
  [[ -f "$bootstrap_archive" && ! -L "$bootstrap_archive" ]] || die "npm bootstrap pack failed"
}

publish_bootstrap() {
  NPM_TOKEN="$npm_token" NPM_CONFIG_DRY_RUN=false NPM_CONFIG_PROVENANCE=false \
    NPM_CONFIG_IGNORE_SCRIPTS=true NPM_CONFIG_REGISTRY="$NPM_REGISTRY" \
    NPM_CONFIG_USERCONFIG="$auth_config" npm publish "$bootstrap_archive" --access public \
    --tag bootstrap --registry "$NPM_REGISTRY" --ignore-scripts=true --dry-run=false \
    --provenance=false
}

poll_bootstrap() {
  local attempt status
  for ((attempt = 1; attempt <= POLL_ATTEMPTS; attempt++)); do
    status="$(fetch_metadata "$NPM_PACKAGE_URL")" || die "npm post-publish check failed"
    if [[ "$status" == 200 ]]; then
      verify_exact_bootstrap
      return
    fi
    [[ "$status" == 404 && $attempt -lt $POLL_ATTEMPTS ]] ||
      die "npm bootstrap did not become authoritative"
    sleep "$POLL_SECONDS"
  done
}

report_existing_bootstrap() {
  [[ "$bootstrap_state" == exact ]] || return 1
  echo "npm bootstrap is already complete with the exact reviewed identity, bytes, and tag."
  print_guidance
}

validate_bootstrap_token() {
  [[ -n "$npm_token" ]] || die "HARISH_NPM_TOKEN is required with --publish"
  [[ "$npm_token" != *$'\n'* && "$npm_token" != *$'\r'* ]] || die "HARISH_NPM_TOKEN is malformed"
}

run_bootstrap() {
  check_bootstrap_state
  if report_existing_bootstrap; then return; fi
  if [[ "$publish" == false ]]; then
    echo "PLAN only; no registry mutation. $NPM_PACKAGE $BOOTSTRAP_VERSION would use bootstrap."
    print_guidance
    return
  fi
  validate_bootstrap_token
  prepare_bootstrap_archive
  write_auth_config
  publish_bootstrap
  poll_bootstrap
  echo "Verified npm serves the bootstrap identity, bytes, and tag for $NPM_PACKAGE."
  print_guidance
}

run_release_plan() {
  check_release_state
  if [[ "$release_state" == exact ]]; then
    echo "$NPM_PACKAGE $release_version already serves the reviewed bytes under $release_tag."
  else
    echo "PLAN only; no registry mutation. $NPM_PACKAGE $release_version is missing; use OIDC."
    print_guidance
  fi
}

release_archive=""
release_version=""
release_tag=""
release_state=""
bootstrap_state=""
metadata_file=""
auth_config=""
bootstrap_root=""
bootstrap_archive=""
parse_arguments "$@"
reject_manual_release
initialize_snapshot
metadata_file="$runtime_root/npm.json"
verify_snapshot
select_release
if [[ "$mode" == bootstrap ]]; then run_bootstrap; else run_release_plan; fi
