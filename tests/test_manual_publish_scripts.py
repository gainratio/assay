from __future__ import annotations

import base64
import hashlib
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PYPI_SCRIPT = ROOT / "scripts/pypi-publish.sh"
NPM_SCRIPT = ROOT / "scripts/npm-publish.sh"
PYPI_URL = "https://upload.pypi.org/legacy/"
PYPI_API = "https://pypi.org/pypi/assay-engine/0.5.0.dev2/json"
NPM_URL = "https://registry.npmjs.org/"
NPM_PACKAGE_URL = f"{NPM_URL}%40gainratio%2Fassay"
NPM_VERSION_URL = f"{NPM_PACKAGE_URL}/0.5.0-dev.2"
FIXTURE_AUTH = "test-only-sentinel"
BOOTSTRAP_BYTES = b"reviewed-bootstrap-archive"


@dataclass(frozen=True)
class Fixture:
    release: Path
    fake_bin: Path
    events: Path


def _write_executable(path: Path, source: str) -> None:
    path.write_text(source, encoding="utf-8")
    path.chmod(0o755)


def _credential_environment() -> str:
    return """credential_names = sorted(name for name in (
        'HARISH_PYPI_TOKEN','HARISH_NPM_TOKEN','PYPI_API_TOKEN','NPM_TOKEN',
        'UV_PUBLISH_TOKEN') if name in os.environ)
"""


def _python_fake(events: Path) -> str:
    return f"""#!{sys.executable}
import json, os, pathlib, sys
{_credential_environment()}
if len(sys.argv) > 1 and sys.argv[1].endswith('verify_release_artifacts.py'):
    event = {{'command':'verify','script':sys.argv[1],'root':sys.argv[2],
             'credential_env':credential_names}}
    with open({str(events)!r}, 'a', encoding='utf-8') as output:
        output.write(json.dumps(event) + '\\n')
    replacement = os.environ.get('REPLACE_AFTER_VERIFY')
    if replacement:
        pathlib.Path(replacement).write_bytes(b'caller-replaced')
    raise SystemExit(int(os.environ.get('FAKE_VERIFY_STATUS', '0')))
os.execv({sys.executable!r}, [{sys.executable!r}, *sys.argv[1:]])
"""


def _uv_fake(events: Path) -> str:
    return f"""#!{sys.executable}
import hashlib, json, os, pathlib, sys
{_credential_environment()}
files = [pathlib.Path(item) for item in sys.argv[1:] if pathlib.Path(item).is_file()]
event = {{'command':'uv','argv':sys.argv[1:],'files':[str(item) for item in files],
         'digests':[hashlib.sha256(item.read_bytes()).hexdigest() for item in files],
         'token_ok':os.environ.get('UV_PUBLISH_TOKEN') == os.environ.get('EXPECTED_TOKEN'),
         'credential_env':credential_names}}
with open({str(events)!r}, 'a', encoding='utf-8') as output:
    output.write(json.dumps(event) + '\\n')
raise SystemExit(int(os.environ.get('FAKE_UV_STATUS', '0')))
"""


def _curl_fake(events: Path) -> str:
    return f"""#!{sys.executable}
import json, os, pathlib, sys
{_credential_environment()}
path = pathlib.Path({str(events)!r})
lines = path.read_text().splitlines() if path.exists() else []
index = sum(json.loads(line).get('command') == 'curl' for line in lines)
responses = json.loads(os.environ.get('FAKE_CURL_RESPONSES', '[{{"status":"404","body":""}}]'))
response = responses[min(index, len(responses) - 1)]
args = sys.argv[1:]
target = pathlib.Path(args[args.index('--output') + 1])
target.write_text(response.get('body', ''), encoding='utf-8')
with open(path, 'a', encoding='utf-8') as output:
    output.write(json.dumps({{'command':'curl','argv':args,'status':response['status'],
        'credential_env':credential_names}}) + '\\n')
sys.stdout.write(response['status'])
raise SystemExit(int(response.get('exit', 0)))
"""


def _npm_environment() -> str:
    return """credential_names = sorted(name for name in (
        'HARISH_PYPI_TOKEN','HARISH_NPM_TOKEN','PYPI_API_TOKEN','NPM_TOKEN',
        'UV_PUBLISH_TOKEN') if name in os.environ)
keys = ('NPM_CONFIG_DRY_RUN','NPM_CONFIG_PROVENANCE','NPM_CONFIG_IGNORE_SCRIPTS',
        'NPM_CONFIG_REGISTRY','NPM_CONFIG_USERCONFIG')
forced = {key: os.environ.get(key) for key in keys}
lower = any(key in os.environ for key in ('npm_config_dry_run','npm_config_provenance',
        'npm_config_ignore_scripts','npm_config_registry','npm_config_userconfig'))
"""


def _npm_fake(events: Path) -> str:
    return f"""#!{sys.executable}
import hashlib, json, os, pathlib, stat, sys
{_npm_environment()}
args = sys.argv[1:]
if args[0] == 'pack':
    root = pathlib.Path(args[1])
    destination = pathlib.Path(args[args.index('--pack-destination') + 1])
    archive = destination / 'gainratio-assay-0.0.0-bootstrap.0.tgz'
    archive.write_bytes({BOOTSTRAP_BYTES!r})
    event = {{'command':'npm-pack','argv':args,'forced':forced,'lower':lower,
             'package':json.loads((root / 'package.json').read_text()),
             'license':hashlib.sha256((root / 'LICENSE').read_bytes()).hexdigest(),
             'credential_env':credential_names}}
else:
    config = pathlib.Path(os.environ['NPM_CONFIG_USERCONFIG'])
    subject = pathlib.Path(args[1])
    event = {{'command':'npm-publish','argv':args,'forced':forced,'lower':lower,
             'config':str(config),'mode':stat.S_IMODE(config.stat().st_mode),
             'config_content':config.read_text(),
             'token_ok':os.environ.get('NPM_TOKEN') == os.environ.get('EXPECTED_TOKEN'),
             'digest':hashlib.sha512(subject.read_bytes()).hexdigest(),
             'credential_env':credential_names}}
with open({str(events)!r}, 'a', encoding='utf-8') as output:
    output.write(json.dumps(event) + '\\n')
raise SystemExit(int(os.environ.get('FAKE_NPM_STATUS', '0')))
"""


def _sleep_fake(events: Path) -> str:
    return f"""#!{sys.executable}
import json, sys
with open({str(events)!r}, 'a', encoding='utf-8') as output:
    output.write(json.dumps({{'command':'sleep','argv':sys.argv[1:]}}) + '\\n')
"""


def _fixture(tmp_path: Path, version: str = "0.5.0-dev.2") -> Fixture:
    release = tmp_path / "release"
    (release / "python").mkdir(parents=True)
    (release / "npm").mkdir()
    python_version = version.replace("-dev.", ".dev")
    (release / "python" / f"assay_engine-{python_version}-py3-none-any.whl").write_bytes(b"wheel")
    (release / "python" / f"assay_engine-{python_version}.tar.gz").write_bytes(b"sdist")
    (release / "npm" / f"gainratio-assay-{version}.tgz").write_bytes(b"npm")
    (release / "SHA256SUMS").write_text("reviewed\n", encoding="utf-8")
    return _install_fakes(tmp_path, release)


def _install_fakes(tmp_path: Path, release: Path) -> Fixture:
    fake_bin, events = tmp_path / "bin", tmp_path / "events.jsonl"
    fake_bin.mkdir()
    sources = {
        "python3": _python_fake(events),
        "uv": _uv_fake(events),
        "curl": _curl_fake(events),
        "npm": _npm_fake(events),
        "sleep": _sleep_fake(events),
    }
    for name, source in sources.items():
        _write_executable(fake_bin / name, source)
    return Fixture(release, fake_bin, events)


def _run(
    fixture: Fixture, script: Path, *arguments: str, cwd: Path = ROOT, **extra: str
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    for name in ("HARISH_PYPI_TOKEN", "HARISH_NPM_TOKEN", "PYPI_API_TOKEN", "NPM_TOKEN"):
        env.pop(name, None)
    env.update({"PATH": f"{fixture.fake_bin}:{env['PATH']}", "EXPECTED_TOKEN": FIXTURE_AUTH})
    env.update(extra)
    return subprocess.run(  # noqa: S603
        ["/bin/bash", script, *arguments, fixture.release],
        cwd=cwd,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )


def _events(fixture: Fixture) -> list[dict[str, object]]:
    if not fixture.events.exists():
        return []
    return [json.loads(line) for line in fixture.events.read_text(encoding="utf-8").splitlines()]


def _commands(fixture: Fixture, name: str) -> list[dict[str, object]]:
    return [event for event in _events(fixture) if event["command"] == name]


def _responses(*items: tuple[str, str]) -> str:
    return json.dumps([{"status": status, "body": body} for status, body in items])


def _pypi_body(release: Path) -> str:
    files = sorted((release / "python").iterdir())
    urls = [
        {
            "filename": item.name,
            "digests": {"sha256": hashlib.sha256(item.read_bytes()).hexdigest()},
        }
        for item in files
    ]
    return json.dumps({"info": {"name": "assay-engine", "version": "0.5.0.dev2"}, "urls": urls})


def _npm_integrity(payload: bytes) -> str:
    digest = base64.b64encode(hashlib.sha512(payload).digest()).decode()
    return f"sha512-{digest}"


def _npm_version_body(release: Path) -> str:
    archive = release / "npm/gainratio-assay-0.5.0-dev.2.tgz"
    return json.dumps(
        {
            "name": "@gainratio/assay",
            "version": "0.5.0-dev.2",
            "dist": {"integrity": _npm_integrity(archive.read_bytes())},
        }
    )


def _npm_package_body(version: str, integrity: str, tag: str) -> str:
    record = {"name": "@gainratio/assay", "version": version, "dist": {"integrity": integrity}}
    return json.dumps(
        {"name": "@gainratio/assay", "dist-tags": {tag: version}, "versions": {version: record}}
    )


def _bootstrap_body(integrity: str | None = None) -> str:
    expected = integrity or _npm_integrity(BOOTSTRAP_BYTES)
    return _npm_package_body("0.0.0-bootstrap.0", expected, "bootstrap")


def _conflicting_bootstrap_body(case: str) -> str:
    payload = json.loads(_bootstrap_body())
    if case == "additional-version":
        payload["versions"]["1.0.0"] = {"name": "@gainratio/assay", "version": "1.0.0"}
    elif case == "identity":
        payload["name"] = "@hostile/assay"
    elif case == "integrity":
        payload["versions"]["0.0.0-bootstrap.0"]["dist"]["integrity"] = "sha512-conflict"
    else:
        payload["dist-tags"] = {"latest": "0.0.0-bootstrap.0"}
    return json.dumps(payload)


def test_should_plan_pypi_from_a_private_verified_snapshot(tmp_path: Path) -> None:
    # Given a reviewed bundle and no credential
    fixture = _fixture(tmp_path)
    # When the default PyPI plan runs
    result = _run(fixture, PYPI_SCRIPT)
    # Then only private snapshot paths reach the trusted verifier and dry-run publisher
    verify, uv = _commands(fixture, "verify")[0], _commands(fixture, "uv")[0]
    assert result.returncode == 0
    assert verify["script"] == str(ROOT / "scripts/verify_release_artifacts.py")
    assert Path(str(verify["root"])).parent != fixture.release.parent
    assert "--dry-run" in uv["argv"]
    assert all(str(fixture.release) not in path for path in uv["files"])
    assert not Path(str(verify["root"])).exists()
    assert "account-wide API token can bootstrap assay-engine" in result.stdout
    assert "HARISH_PYPI_TOKEN" in result.stdout


def test_should_refuse_pypi_publish_without_environment_token(tmp_path: Path) -> None:
    # Given a missing registry version and no token
    fixture = _fixture(tmp_path)
    # When publication is requested
    result = _run(fixture, PYPI_SCRIPT, "--publish")
    # Then mutation is refused after verification and preflight
    assert result.returncode != 0
    assert "HARISH_PYPI_TOKEN is required" in result.stderr
    assert _commands(fixture, "uv") == []


@pytest.mark.parametrize(
    ("script", "arguments", "legacy_name", "required_name", "client"),
    [
        (PYPI_SCRIPT, ("--publish",), "PYPI_API_TOKEN", "HARISH_PYPI_TOKEN", "uv"),
        (
            NPM_SCRIPT,
            ("--bootstrap", "--publish"),
            "NPM_TOKEN",
            "HARISH_NPM_TOKEN",
            "npm-publish",
        ),
    ],
)
def test_should_refuse_legacy_token_name_without_registry_mutation(
    tmp_path: Path,
    script: Path,
    arguments: tuple[str, ...],
    *,
    legacy_name: str,
    required_name: str,
    client: str,
) -> None:
    # Given only a legacy generic token name is exported
    fixture = _fixture(tmp_path)
    # When explicit publication is requested
    result = _run(fixture, script, *arguments, **{legacy_name: FIXTURE_AUTH})
    # Then the request fails before a registry client can mutate state
    assert result.returncode != 0
    assert required_name in result.stderr
    assert _commands(fixture, client) == []
    assert FIXTURE_AUTH not in result.stdout + result.stderr


@pytest.mark.parametrize(
    ("script", "arguments", "client"),
    [
        (PYPI_SCRIPT, ("--publish",), "uv"),
        (NPM_SCRIPT, ("--bootstrap", "--publish"), "npm-publish"),
    ],
)
def test_should_not_source_zshrc_for_credentials(
    tmp_path: Path, script: Path, arguments: tuple[str, ...], client: str
) -> None:
    # Given a zshrc would export the new tokens and create an observable marker
    fixture, marker = _fixture(tmp_path), tmp_path / "zshrc-sourced"
    zshrc = tmp_path / ".zshrc"
    zshrc.write_text(
        f"touch {marker}\nexport HARISH_PYPI_TOKEN={FIXTURE_AUTH}\n"
        f"export HARISH_NPM_TOKEN={FIXTURE_AUTH}\n"
    )
    # When the publisher runs without the caller having sourced that file
    result = _run(fixture, script, *arguments, HOME=str(tmp_path))
    # Then the file stays untouched and no registry mutation is attempted
    assert result.returncode != 0
    assert not marker.exists()
    assert _commands(fixture, client) == []
    assert FIXTURE_AUTH not in result.stdout + result.stderr


def test_should_expose_only_uv_translation_to_pypi_client(tmp_path: Path) -> None:
    # Given every public and generic token name is exported in the caller
    fixture = _fixture(tmp_path)
    responses = _responses(("404", ""), ("200", _pypi_body(fixture.release)))
    # When PyPI bootstrap publication is explicitly requested
    result = _run(
        fixture,
        PYPI_SCRIPT,
        "--publish",
        HARISH_PYPI_TOKEN=FIXTURE_AUTH,
        HARISH_NPM_TOKEN=FIXTURE_AUTH,
        PYPI_API_TOKEN=FIXTURE_AUTH,
        NPM_TOKEN=FIXTURE_AUTH,
        FAKE_CURL_RESPONSES=responses,
    )
    # Then only uv receives the required one-time translation
    assert result.returncode == 0
    assert _commands(fixture, "verify")[0]["credential_env"] == []
    assert all(event["credential_env"] == [] for event in _commands(fixture, "curl"))
    assert _commands(fixture, "uv")[0]["credential_env"] == ["UV_PUBLISH_TOKEN"]


def test_should_expose_only_npm_translation_to_npm_publish_client(tmp_path: Path) -> None:
    # Given every public and generic token name is exported in the caller
    fixture = _fixture(tmp_path)
    responses = _responses(("404", ""), ("200", _bootstrap_body()))
    # When npm bootstrap publication is explicitly requested
    result = _run(
        fixture,
        NPM_SCRIPT,
        "--bootstrap",
        "--publish",
        HARISH_PYPI_TOKEN=FIXTURE_AUTH,
        HARISH_NPM_TOKEN=FIXTURE_AUTH,
        PYPI_API_TOKEN=FIXTURE_AUTH,
        NPM_TOKEN=FIXTURE_AUTH,
        FAKE_CURL_RESPONSES=responses,
    )
    # Then only the final npm publish child receives its required translation
    assert result.returncode == 0
    assert _commands(fixture, "verify")[0]["credential_env"] == []
    assert all(event["credential_env"] == [] for event in _commands(fixture, "curl"))
    assert _commands(fixture, "npm-pack")[0]["credential_env"] == []
    assert _commands(fixture, "npm-publish")[0]["credential_env"] == ["NPM_TOKEN"]


def test_should_publish_immutable_pypi_snapshot_then_verify_served_hashes(tmp_path: Path) -> None:
    # Given the caller replaces its wheel after the private snapshot is verified
    fixture = _fixture(tmp_path)
    wheel = fixture.release / "python/assay_engine-0.5.0.dev2-py3-none-any.whl"
    original = hashlib.sha256(wheel.read_bytes()).hexdigest()
    responses = _responses(("404", ""), ("404", ""), ("200", _pypi_body(fixture.release)))
    # When token bootstrap publication is explicitly requested
    result = _run(
        fixture,
        PYPI_SCRIPT,
        "--publish",
        HARISH_PYPI_TOKEN=FIXTURE_AUTH,
        REPLACE_AFTER_VERIFY=str(wheel),
        FAKE_CURL_RESPONSES=responses,
    )
    # Then uv uploads only the immutable snapshot and completion follows served-byte verification
    uv = _commands(fixture, "uv")[0]
    assert result.returncode == 0
    assert uv["token_ok"] is True
    assert FIXTURE_AUTH not in json.dumps(uv["argv"]) + result.stdout + result.stderr
    assert original in uv["digests"]
    assert hashlib.sha256(wheel.read_bytes()).hexdigest() != original
    assert all(str(fixture.release) not in path for path in uv["files"])
    assert len(_commands(fixture, "curl")) == 3
    assert len(_commands(fixture, "sleep")) == 1
    assert "Verified PyPI serves the reviewed filenames and hashes" in result.stdout
    assert all(not Path(path).exists() for path in uv["files"])


def test_should_refuse_pypi_completion_when_served_hashes_conflict(tmp_path: Path) -> None:
    # Given PyPI returns conflicting metadata after upload
    fixture = _fixture(tmp_path)
    conflict = json.dumps({"info": {"name": "assay-engine", "version": "0.5.0.dev2"}, "urls": []})
    responses = _responses(("404", ""), ("200", conflict))
    # When token bootstrap publication runs
    result = _run(
        fixture,
        PYPI_SCRIPT,
        "--publish",
        HARISH_PYPI_TOKEN=FIXTURE_AUTH,
        FAKE_CURL_RESPONSES=responses,
    )
    # Then the script fails without claiming completion
    assert result.returncode != 0
    assert "Verified PyPI" not in result.stdout


def test_should_refuse_duplicate_pypi_served_filename(tmp_path: Path) -> None:
    # Given PyPI repeats an otherwise exact file record
    fixture = _fixture(tmp_path)
    payload = json.loads(_pypi_body(fixture.release))
    payload["urls"].append(payload["urls"][0])
    responses = _responses(("404", ""), ("200", json.dumps(payload)))
    # When post-upload registry truth is checked
    result = _run(
        fixture,
        PYPI_SCRIPT,
        "--publish",
        HARISH_PYPI_TOKEN=FIXTURE_AUTH,
        FAKE_CURL_RESPONSES=responses,
    )
    # Then duplicate filename evidence cannot be collapsed into an exact match
    assert result.returncode != 0
    assert "Verified PyPI" not in result.stdout


@pytest.mark.parametrize("script", [PYPI_SCRIPT, NPM_SCRIPT])
def test_should_disable_inherited_xtrace_before_reading_secrets(
    tmp_path: Path, script: Path
) -> None:
    # Given Bash inherits xtrace, a hostile trace prefix, and a token-bearing request
    fixture = _fixture(tmp_path)
    arguments = ("--publish",) if script == PYPI_SCRIPT else ("--bootstrap", "--publish")
    token = (
        {"HARISH_PYPI_TOKEN": FIXTURE_AUTH}
        if script == PYPI_SCRIPT
        else {"HARISH_NPM_TOKEN": FIXTURE_AUTH}
    )
    # When the request exits after the read-only registry preflight
    result = _run(
        fixture,
        script,
        *arguments,
        SHELLOPTS="braceexpand:xtrace",
        PS4=f"trace:{FIXTURE_AUTH} ",
        **token,
    )
    # Then even the first trace line cannot disclose the token on actual stderr
    assert FIXTURE_AUTH not in result.stderr
    assert FIXTURE_AUTH not in result.stdout


@pytest.mark.parametrize(
    ("script", "relative"),
    [(PYPI_SCRIPT, "python/wheel-link.whl"), (NPM_SCRIPT, "npm/archive-link.tgz")],
)
def test_should_reject_symlinks_inside_release_envelope(
    tmp_path: Path, script: Path, relative: str
) -> None:
    # Given an extra symlink inside the caller-controlled release envelope
    fixture = _fixture(tmp_path)
    (fixture.release / relative).symlink_to(ROOT / "LICENSE")
    # When either publisher snapshots the bundle
    result = _run(fixture, script)
    # Then it fails before verification or registry access
    assert result.returncode != 0
    assert "symlink or nonregular" in result.stderr
    assert _commands(fixture, "verify") == []
    assert _commands(fixture, "curl") == []


def test_should_reject_nonregular_release_envelope_member(tmp_path: Path) -> None:
    # Given a FIFO is planted beside reviewed artifacts
    fixture = _fixture(tmp_path)
    os.mkfifo(fixture.release / "python/untrusted.fifo")
    # When snapshotting begins
    result = _run(fixture, PYPI_SCRIPT)
    # Then the publisher refuses to copy or open it
    assert result.returncode != 0
    assert "symlink or nonregular" in result.stderr
    assert _commands(fixture, "verify") == []


def test_should_use_trusted_repo_paths_from_hostile_working_directory(tmp_path: Path) -> None:
    # Given cwd contains replacement verifier and license files
    fixture = _fixture(tmp_path)
    hostile = tmp_path / "hostile"
    (hostile / "scripts").mkdir(parents=True)
    (hostile / "scripts/verify_release_artifacts.py").write_text("raise SystemExit(99)")
    (hostile / "LICENSE").write_text("hostile license", encoding="utf-8")
    responses = _responses(("404", ""), ("200", _bootstrap_body()))
    # When the absolute npm script bootstraps from that directory
    result = _run(
        fixture,
        NPM_SCRIPT,
        "--bootstrap",
        "--publish",
        cwd=hostile,
        HARISH_NPM_TOKEN=FIXTURE_AUTH,
        FAKE_CURL_RESPONSES=responses,
    )
    # Then verifier and packaged license come only from the script's repository
    verify, packed = _commands(fixture, "verify")[0], _commands(fixture, "npm-pack")[0]
    assert result.returncode == 0
    assert verify["script"] == str(ROOT / "scripts/verify_release_artifacts.py")
    assert packed["license"] == hashlib.sha256((ROOT / "LICENSE").read_bytes()).hexdigest()


def test_should_forbid_manual_real_npm_release_publication(tmp_path: Path) -> None:
    # Given a reviewed npm release and a valid token
    fixture = _fixture(tmp_path)
    # When manual release mutation is explicitly requested
    result = _run(fixture, NPM_SCRIPT, "--release", "--publish", HARISH_NPM_TOKEN=FIXTURE_AUTH)
    # Then the script directs the release to provenance-bearing OIDC without invoking npm
    assert result.returncode != 0
    assert "OIDC" in result.stderr
    assert _commands(fixture, "npm-publish") == []


def _hostile_npm_environment() -> dict[str, str]:
    keys = ("DRY_RUN", "REGISTRY", "PROVENANCE", "IGNORE_SCRIPTS", "USERCONFIG")
    return {prefix + key: "hostile" for key in keys for prefix in ("NPM_CONFIG_", "npm_config_")}


def test_should_bootstrap_with_sanitized_config_and_verify_registry_state(tmp_path: Path) -> None:
    # Given a missing package, hostile inherited npm config, and a granular token
    fixture = _fixture(tmp_path)
    responses = _responses(("404", ""), ("404", ""), ("200", _bootstrap_body()))
    environment = _hostile_npm_environment() | {
        "HARISH_NPM_TOKEN": FIXTURE_AUTH,
        "FAKE_CURL_RESPONSES": responses,
    }
    # When the exact bootstrap is published
    result = _run(fixture, NPM_SCRIPT, "--bootstrap", "--publish", **environment)
    # Then npm gets forced-safe settings, literal env auth, exact archive, and bounded verification
    packed = _commands(fixture, "npm-pack")[0]
    published = _commands(fixture, "npm-publish")[0]
    assert result.returncode == 0
    assert packed["package"]["version"] == "0.0.0-bootstrap.0"
    assert published["forced"] == {
        "NPM_CONFIG_DRY_RUN": "false",
        "NPM_CONFIG_PROVENANCE": "false",
        "NPM_CONFIG_IGNORE_SCRIPTS": "true",
        "NPM_CONFIG_REGISTRY": NPM_URL,
        "NPM_CONFIG_USERCONFIG": published["config"],
    }
    assert packed["lower"] is False
    assert published["lower"] is False
    assert published["config_content"].count("${NPM_TOKEN}") == 1
    assert FIXTURE_AUTH not in published["config_content"]
    assert published["token_ok"] is True
    assert FIXTURE_AUTH not in json.dumps(published["argv"]) + result.stdout + result.stderr
    assert published["mode"] == 0o600
    assert published["digest"] == hashlib.sha512(BOOTSTRAP_BYTES).hexdigest()
    assert "--dry-run=false" in published["argv"]
    assert "--provenance=false" in published["argv"]
    assert "--ignore-scripts=true" in published["argv"]
    assert NPM_URL in published["argv"]
    assert len(_commands(fixture, "curl")) == 3
    assert len(_commands(fixture, "sleep")) == 1
    assert "Verified npm serves the bootstrap identity, bytes, and tag" in result.stdout
    assert "HARISH_NPM_TOKEN" in result.stdout
    assert not Path(str(published["config"])).exists()


def test_should_bootstrap_only_when_package_endpoint_is_authoritative_404(tmp_path: Path) -> None:
    # Given the npm package already exists, regardless of bootstrap version presence
    fixture = _fixture(tmp_path)
    existing = json.dumps({"name": "@gainratio/assay", "dist-tags": {}, "versions": {}})
    # When bootstrap publication is requested
    result = _run(
        fixture,
        NPM_SCRIPT,
        "--bootstrap",
        "--publish",
        HARISH_NPM_TOKEN=FIXTURE_AUTH,
        FAKE_CURL_RESPONSES=_responses(("200", existing)),
    )
    # Then package-level existence blocks the one-time bootstrap
    assert result.returncode != 0
    assert _commands(fixture, "curl")[0]["argv"][-1] == NPM_PACKAGE_URL
    assert _commands(fixture, "npm-pack") == []
    assert _commands(fixture, "npm-publish") == []


def test_should_accept_exact_completed_bootstrap_retry_without_a_token(tmp_path: Path) -> None:
    # Given a previous publish succeeded before the caller observed completion
    fixture = _fixture(tmp_path)
    responses = _responses(("200", _bootstrap_body()))
    # When the explicit bootstrap request is retried without a token
    result = _run(
        fixture,
        NPM_SCRIPT,
        "--bootstrap",
        "--publish",
        FAKE_CURL_RESPONSES=responses,
    )
    # Then exact registry state is accepted without another registry mutation
    assert result.returncode == 0
    assert "already complete" in result.stdout
    assert len(_commands(fixture, "curl")) == 1
    assert len(_commands(fixture, "npm-pack")) == 1
    assert _commands(fixture, "npm-publish") == []


@pytest.mark.parametrize("case", ["additional-version", "identity", "integrity", "tag"])
def test_should_refuse_conflicting_existing_bootstrap_state(tmp_path: Path, case: str) -> None:
    # Given an existing package differs from the exact bootstrap-only state
    fixture = _fixture(tmp_path)
    responses = _responses(("200", _conflicting_bootstrap_body(case)))
    # When bootstrap is retried without a token
    result = _run(
        fixture,
        NPM_SCRIPT,
        "--bootstrap",
        "--publish",
        FAKE_CURL_RESPONSES=responses,
    )
    # Then the registry conflict is terminal and never reaches publish
    assert result.returncode != 0
    assert "bootstrap registry state conflicts" in result.stderr
    assert _commands(fixture, "npm-publish") == []


def test_should_refuse_bootstrap_completion_on_wrong_integrity_or_tag(tmp_path: Path) -> None:
    # Given npm propagates a bootstrap record with conflicting bytes
    fixture = _fixture(tmp_path)
    responses = _responses(("404", ""), ("200", _bootstrap_body("sha512-conflict")))
    # When bootstrap publication finishes
    result = _run(
        fixture,
        NPM_SCRIPT,
        "--bootstrap",
        "--publish",
        HARISH_NPM_TOKEN=FIXTURE_AUTH,
        FAKE_CURL_RESPONSES=responses,
    )
    # Then the post-publish mismatch is terminal and success is not claimed
    assert result.returncode != 0
    assert "Verified npm" not in result.stdout


def test_should_verify_existing_real_release_integrity_and_expected_tag(tmp_path: Path) -> None:
    # Given npm serves the exact reviewed prerelease and next points to it
    fixture = _fixture(tmp_path)
    package = _npm_package_body("0.5.0-dev.2", _npm_integrity(b"npm"), "next")
    responses = _responses(("200", _npm_version_body(fixture.release)), ("200", package))
    # When the non-mutating release retry plan runs
    result = _run(fixture, NPM_SCRIPT, "--release", FAKE_CURL_RESPONSES=responses)
    # Then both exact bytes and channel state are verified without npm mutation
    urls = [event["argv"][-1] for event in _commands(fixture, "curl")]
    assert result.returncode == 0
    assert urls == [NPM_VERSION_URL, NPM_PACKAGE_URL]
    assert "already serves the reviewed bytes under next" in result.stdout
    assert _commands(fixture, "npm-publish") == []


def test_should_refuse_existing_real_release_under_wrong_tag(tmp_path: Path) -> None:
    # Given exact release bytes exist but the expected next tag points elsewhere
    fixture = _fixture(tmp_path)
    package = _npm_package_body("0.5.0-dev.2", _npm_integrity(b"npm"), "other")
    responses = _responses(("200", _npm_version_body(fixture.release)), ("200", package))
    # When the release retry plan runs
    result = _run(fixture, NPM_SCRIPT, "--release", FAKE_CURL_RESPONSES=responses)
    # Then the channel mismatch fails closed
    assert result.returncode != 0
    assert "expected next dist-tag" in result.stderr
