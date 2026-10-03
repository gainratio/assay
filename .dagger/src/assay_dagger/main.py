"""Assay's complete quality, security, and release graph."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Final, Protocol, Self, cast

import dagger
from dagger import check, dag, field, function, object_type

PYTHON_IMAGE: Final = (
    "python:3.13.14-slim@sha256:9662417aace5ae7b8e2609cce472b72a8958e134ba372808abe9cc1a0c0125e6"
)
UV_IMAGE: Final = (
    "ghcr.io/astral-sh/uv:0.11.32@sha256:"
    "df4cae8f3a96d175e2e5f992e597550000edbe78fdc2594d5cd8de1a217f504c"
)
NODE_IMAGE: Final = (
    "node:24.16.0-bookworm-slim@sha256:"
    "2c87ef9bd3c6a3bd4b472b4bec2ce9d16354b0c574f736c476489d09f560a203"
)
NODE_URL: Final = "https://nodejs.org/dist/v22.13.0/node-v22.13.0-linux-x64.tar.xz"
NODE_SHA256: Final = "3ff0d57063c33313d73d0bdcebc4c778ad6be948234584694a042c6fe57164f6"
NPM_PUBLISHER_SHA512: Final = (
    "b885e890b9418fa1693544d05f53e64f9a73ec194837d4258b15fecdd692347b1dd2a517b1b0cbaf"
    "9d31cd8e92c3b70956bd2ecc72833a57b4b3098f5bfa7943"
)
PNPM_VERSION: Final = "11.5.0"
REPOSITORY: Final = "hseshadr/assay"
REPOSITORY_URL: Final = f"https://github.com/{REPOSITORY}.git"
SHA_LENGTH: Final = 40
PYTHON_ARTIFACT_COUNT: Final = 2
CANDIDATE_ROOTS: Final = ["CANDIDATE-SHA256SUMS", "publication/", "publish-tools/", "release/"]
SOURCE_EXCLUDES: Final = [
    ".git",
    ".venv",
    ".dagger/.venv",
    ".dagger/sdk",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "**/__pycache__",
    "**/node_modules",
    "**/dist",
    ".env",
    "**/.env",
    "*.key",
    "**/*.key",
    "*.pem",
    "**/*.pem",
]
DIGITS: Final = re.compile(r"[0-9]+")
BRANCH_REF: Final = r"refs/heads/[A-Za-z0-9._/-]+"
# npm detects GitHub Actions only through ci-info's GITHUB_ACTIONS. Without it,
# npm skips the OIDC trusted-publishing exchange and `--provenance` fails with
# `EUSAGE: Automatic provenance generation not supported for provider: null`.
# npm writes these values into the SLSA statement (libnpmpublish provenance.js)
# and the registry checks them against the Sigstore certificate. They come from
# the lineage-proven publish run record and are validated before npm sees them.
PROVENANCE_CONTEXT: Final[dict[str, re.Pattern[str]]] = {
    "GITHUB_EVENT_NAME": re.compile(r"workflow_run"),
    "GITHUB_REF": re.compile(BRANCH_REF),
    "GITHUB_REPOSITORY": re.compile(re.escape(REPOSITORY)),
    "GITHUB_REPOSITORY_ID": DIGITS,
    "GITHUB_REPOSITORY_OWNER_ID": DIGITS,
    "GITHUB_RUN_ATTEMPT": DIGITS,
    "GITHUB_RUN_ID": DIGITS,
    "GITHUB_SERVER_URL": re.compile(r"https://github\.com"),
    "GITHUB_SHA": re.compile(r"[0-9a-f]{40}"),
    "GITHUB_WORKFLOW": re.compile(r"[ -~]+"),
    # npm trusted publishing is bound to this exact workflow file.
    "GITHUB_WORKFLOW_REF": re.compile(
        re.escape(f"{REPOSITORY}/.github/workflows/publish.yml@") + BRANCH_REF
    ),
    "RUNNER_ENVIRONMENT": re.compile(r"github-hosted"),
}
NPM_ARCHIVE = re.compile(r"^gainratio-assay-[0-9]+\.[0-9]+\.[0-9]+(?:-dev\.[0-9]+)?\.tgz$")
PYTHON_WHEEL = re.compile(
    r"^assay_engine-[0-9]+\.[0-9]+\.[0-9]+(?:\.dev[0-9]+)?-py3-none-any\.whl$"
)
PYTHON_SDIST = re.compile(r"^assay_engine-[0-9]+\.[0-9]+\.[0-9]+(?:\.dev[0-9]+)?\.tar\.gz$")


class FoundationClient(Protocol):
    """The generated Foundation surface used by Assay's local adapter."""

    def source(
        self, source: dagger.Directory, repository: str, commit_sha: str
    ) -> dagger.Directory: ...

    def guard(
        self, source: dagger.Directory, repository: str, commit_sha: str
    ) -> dagger.Container: ...


def _foundation() -> FoundationClient:
    """Return the exact-SHA generated Foundation dependency."""
    return cast(FoundationClient, dag.foundation())  # type: ignore[attr-defined]


@dataclass(frozen=True)
class NpmPlan:
    """The exact registry state observed by the unprivileged candidate."""

    publish: bool
    channel: str
    publish_tag: str
    channel_version: str
    publish_tag_version: str


@object_type
class Assay:
    """Run the same typed Assay graph locally and on GitHub."""

    source: dagger.Directory = field()

    @classmethod
    def create(cls, workspace: dagger.Workspace) -> Self:
        """Construct the graph from one explicit typed workspace snapshot."""
        instance = cls.__new__(cls)
        instance.source = workspace.directory("/", exclude=SOURCE_EXCLUDES)
        return instance

    @function
    def python(self) -> dagger.Container:
        """Run lint, format, strict typing, Grade A complexity, and tests."""
        return self._python_gate(self._source_with_history(self.source))

    @function
    def typescript(self) -> dagger.Container:
        """Run the complete TypeScript quality and package-build gate."""
        return self._typescript_gate(self._source_with_history(self.source))

    @function
    def artifacts(self) -> dagger.Directory:
        """Build the exact three-artifact release envelope without publishing."""
        source = self._source_with_history(self.source)
        return self._artifact_container(source).directory("/release")

    @function
    async def security(self, commit_sha: str = "") -> str:
        """Run dependency, workflow, shell, snapshot, and history security."""
        complete = await self._verified_source(self.source, commit_sha)
        await self._security(complete)
        return "Assay Dagger security gate passed"

    @function
    @check
    async def ci(self, commit_sha: str = "") -> str:
        """Run Assay's canonical gate sequentially to bound runner memory."""
        await self._run_ci(self.source, commit_sha)
        return "Assay canonical Dagger gate passed"

    async def _run_ci(self, source: dagger.Directory, commit_sha: str) -> None:
        complete = await self._verified_source(source, commit_sha)
        await self._python_gate(complete).sync()
        await self._typescript_gate(complete).sync()
        await self._release_evidence(complete).sync()
        await self._security(complete)
        await self._artifact_container(complete).sync()

    @function
    async def release_candidate(
        self, tag: str, commit_sha: str, github_token: dagger.Secret
    ) -> dagger.Directory:
        """Build one exact, Dagger-proven dual-package candidate."""
        self._require_sha(commit_sha)
        await self._hosted(tag, commit_sha, github_token).sync()
        source = self._source_with_history(self._release_source(commit_sha), commit_sha)
        await self._identity(source, tag, commit_sha).sync()
        await self._run_ci(source, commit_sha)
        return self._candidate(source, tag, commit_sha).directory("/candidate")

    @function
    async def pypi_required(self, candidate: dagger.Directory, expected_sha: str) -> bool:
        """Validate a source-free candidate and report whether PyPI is missing it."""
        self._require_sha(expected_sha)
        await (await self._validated_candidate(candidate)).sync()
        return await self._pypi_decision(candidate, expected_sha)

    @function
    async def publish_npm(
        self,
        candidate: dagger.Directory,
        expected_sha: str,
        oidc_url: dagger.Secret,
        oidc_token: dagger.Secret,
        github_context: dagger.File,
    ) -> str:
        """Publish one source-free npm artifact with OIDC provenance."""
        self._require_sha(expected_sha)
        environment = self._provenance_environment(await github_context.contents())
        await (await self._validated_candidate(candidate)).sync()
        plan = await self._npm_decision(candidate, expected_sha)
        if not plan.publish:
            return "verified existing npm bytes and provenance"
        publisher = self._npm_publisher(candidate, oidc_url, oidc_token, environment)
        return await self._publish_npm(candidate, plan, publisher)

    @staticmethod
    def _provenance_environment(context: str) -> dict[str, str]:
        """Validate the publish run's GitHub Actions context into npm's environment."""
        parsed: object = json.loads(context)
        if not isinstance(parsed, dict) or set(parsed) != set(PROVENANCE_CONTEXT):
            raise ValueError("provenance context must carry exactly the npm provenance variables")
        values = {name: Assay._context_value(name, parsed[name]) for name in parsed}
        return {"CI": "true", "GITHUB_ACTIONS": "true", **values}

    @staticmethod
    def _context_value(name: str, value: object) -> str:
        if not isinstance(value, str) or PROVENANCE_CONTEXT[name].fullmatch(value) is None:
            raise ValueError(f"provenance context {name} is not this repository's publisher")
        return value

    def _python_gate(self, source: dagger.Directory) -> dagger.Container:
        return self._repository(source).with_exec(["uv", "run", "poe", "gate"])

    def _typescript_gate(self, source: dagger.Directory) -> dagger.Container:
        return self._repository(source).with_exec(["pnpm", "--dir", "ts", "gate"])

    def _release_evidence(self, source: dagger.Directory) -> dagger.Container:
        return (
            self._repository(source)
            .with_exec(["uv", "run", "poe", "mutants"])
            .with_exec(["uv", "run", "poe", "benchmark"])
            .with_exec(["pnpm", "--dir", "ts", "benchmark"])
            .with_exec(["bash", "examples/run_composite.sh"])
        )

    async def _security(self, source: dagger.Directory) -> None:
        audited = self._repository(source).with_exec(["uv", "run", "poe", "audit-python"])
        audited = audited.with_exec(["uv", "run", "poe", "audit-typescript"])
        audited = audited.with_exec(["uv", "run", "poe", "workflow-security"])
        await audited.sync()
        await self._shellcheck(source).sync()

    async def _verified_source(self, source: dagger.Directory, commit_sha: str) -> dagger.Directory:
        complete = self._canonical_source(source, commit_sha)
        await self._shared_guard(complete, commit_sha).sync()
        return self._source_with_history(complete, commit_sha)

    @staticmethod
    def _canonical_source(source: dagger.Directory, commit_sha: str) -> dagger.Directory:
        return _foundation().source(
            source=source,
            repository=REPOSITORY,
            commit_sha=commit_sha,
        )

    @staticmethod
    def _shared_guard(source: dagger.Directory, commit_sha: str) -> dagger.Container:
        return _foundation().guard(source=source, repository=REPOSITORY, commit_sha=commit_sha)

    def _shellcheck(self, source: dagger.Directory) -> dagger.Container:
        command = "shellcheck examples/*.sh scripts/*.sh"
        return self._repository(source).with_exec(["sh", "-ceu", command])

    def _source_with_history(
        self, source: dagger.Directory, commit_sha: str = ""
    ) -> dagger.Directory:
        history = self._history(commit_sha)
        git_metadata = history.filter(include=[".git", ".git/**"])
        return git_metadata.with_directory("/", source)

    def _hosted(self, tag: str, commit_sha: str, github_token: dagger.Secret) -> dagger.Container:
        command = [
            "uv",
            "run",
            "python",
            "scripts/verify_release_identity.py",
            "github",
            REPOSITORY,
            tag,
            commit_sha,
        ]
        return (
            self._repository(self.source)
            .with_secret_variable("GITHUB_TOKEN", github_token)
            .with_exec(command)
        )

    def _identity(self, source: dagger.Directory, tag: str, commit_sha: str) -> dagger.Container:
        # A dag.git commit tree has no refs/remotes/origin/main. Derive it from an
        # independently fetched main branch, never from the release sha itself.
        main = self._history("").filter(include=[".git", ".git/**"])
        protected = self._repository(main.with_directory("/", source))
        track = ["git", "update-ref", "refs/remotes/origin/main", "refs/heads/main"]
        command = ["uv", "run", "python", "scripts/verify_release_identity.py", tag, commit_sha]
        return protected.with_exec(track).with_exec(command)

    def _artifact_container(self, source: dagger.Directory) -> dagger.Container:
        return self._repository(source).with_exec(
            ["bash", "scripts/build_release_artifacts.sh", "/release"]
        )

    def _candidate(self, source: dagger.Directory, tag: str, commit_sha: str) -> dagger.Container:
        built = self._artifact_container(source)
        built = built.with_exec(["mkdir", "-p", "/candidate/publication"])
        built = built.with_exec(["cp", "-R", "/release", "/candidate/release"])
        publisher = ["bash", "scripts/stage_npm_publisher.sh", "/candidate/publish-tools"]
        built = built.with_exec(publisher)
        built = self._registry_preflight(built, tag, commit_sha)
        manifest = (
            "cd /candidate && find . -type f ! -name CANDIDATE-SHA256SUMS -print0 | "
            "LC_ALL=C sort -z | xargs -0 sha256sum > CANDIDATE-SHA256SUMS"
        )
        return built.with_exec(["sh", "-ceu", manifest])

    def _registry_preflight(
        self, container: dagger.Container, tag: str, commit_sha: str
    ) -> dagger.Container:
        npm_version = tag.removeprefix("v")
        python_version = npm_version.replace("-dev.", ".dev")
        base = container.with_env_variable("RELEASE_TAG", tag)
        base = base.with_env_variable("GITHUB_SHA", commit_sha)
        pypi = self._registry_command("pypi", "/candidate/release/python", python_version)
        npm = self._registry_command("npm", "/candidate/release/npm", npm_version)
        return base.with_exec(pypi).with_exec(npm).with_exec(self._record_sha(commit_sha))

    @staticmethod
    def _registry_command(registry: str, root: str, version: str) -> list[str]:
        output = f"/candidate/publication/{registry}.env"
        return [
            "uv",
            "run",
            "python",
            "-m",
            "scripts.registry_release_guard",
            registry,
            root,
            version,
            output,
        ]

    @staticmethod
    def _record_sha(commit_sha: str) -> list[str]:
        script = (
            "for file in /candidate/publication/*.env; do "
            f"printf 'expected-sha={commit_sha}\\n' >> \"$file\"; done"
        )
        return ["sh", "-ceu", script]

    async def _validated_candidate(self, candidate: dagger.Directory) -> dagger.Container:
        await self._require_candidate_shape(candidate)
        return (
            dag.container()
            .from_(NODE_IMAGE)
            .with_directory("/candidate", candidate)
            .with_workdir("/candidate")
            .with_exec(["sha256sum", "--check", "CANDIDATE-SHA256SUMS"])
            .with_workdir("/candidate/release")
            .with_exec(["sha256sum", "--check", "SHA256SUMS"])
        )

    @staticmethod
    async def _require_candidate_shape(candidate: dagger.Directory) -> None:
        # Directory.entries() names subdirectories with a trailing "/", so these
        # lists also pin which entries are files and which are directories.
        if sorted(await candidate.entries()) != CANDIDATE_ROOTS:
            raise ValueError("candidate envelope contains unexpected material")
        publication = await candidate.directory("publication").entries()
        if sorted(publication) != ["npm.env", "pypi.env"]:
            raise ValueError("candidate publication plan differs")
        tools = await candidate.directory("publish-tools").entries()
        if tools != ["npm-12.0.2.tgz"]:
            raise ValueError("candidate npm publisher differs")
        await Assay._require_release_shape(candidate.directory("release"))

    @staticmethod
    async def _require_release_shape(release: dagger.Directory) -> None:
        if sorted(await release.entries()) != ["SHA256SUMS", "npm/", "python/"]:
            raise ValueError("candidate release envelope differs")
        npm = await release.directory("npm").entries()
        python = await release.directory("python").entries()
        if not Assay._valid_release_artifacts(npm, python):
            raise ValueError("candidate release envelope differs")

    @staticmethod
    def _valid_release_artifacts(npm: list[str], python: list[str]) -> bool:
        npm_valid = len(npm) == 1 and NPM_ARCHIVE.fullmatch(npm[0]) is not None
        wheel_valid = Assay._one_match(PYTHON_WHEEL, python)
        sdist_valid = Assay._one_match(PYTHON_SDIST, python)
        return npm_valid and len(python) == PYTHON_ARTIFACT_COUNT and wheel_valid and sdist_valid

    @staticmethod
    def _one_match(pattern: re.Pattern[str], entries: list[str]) -> bool:
        return sum(pattern.fullmatch(item) is not None for item in entries) == 1

    @staticmethod
    async def _pypi_decision(candidate: dagger.Directory, expected_sha: str) -> bool:
        values = Assay._plan(await candidate.file("publication/pypi.env").contents())
        Assay._require_plan_sha(values, expected_sha)
        publish = values.get("publish")
        if publish not in ("true", "false"):
            raise ValueError("candidate PyPI decision is malformed")
        return publish == "true"

    @staticmethod
    async def _npm_decision(candidate: dagger.Directory, expected_sha: str) -> NpmPlan:
        values = Assay._plan(await candidate.file("publication/npm.env").contents())
        Assay._require_plan_sha(values, expected_sha)
        required = ("publish", "dist-tag", "publish-tag", "channel-version", "publish-tag-version")
        if any(not values.get(key) for key in required) or values["publish"] not in (
            "true",
            "false",
        ):
            raise ValueError("candidate npm decision is malformed")
        return NpmPlan(
            publish=values["publish"] == "true",
            channel=values["dist-tag"],
            publish_tag=values["publish-tag"],
            channel_version=values["channel-version"],
            publish_tag_version=values["publish-tag-version"],
        )

    @staticmethod
    def _plan(contents: str) -> dict[str, str]:
        pairs = tuple(Assay._plan_pair(line) for line in contents.splitlines())
        values = dict(pairs)
        if len(values) != len(pairs):
            raise ValueError("candidate publication plan has duplicate fields")
        return values

    @staticmethod
    def _plan_pair(line: str) -> tuple[str, str]:
        key, separator, value = line.partition("=")
        if not key or separator != "=" or not value:
            raise ValueError("candidate publication plan is malformed")
        return key, value

    @staticmethod
    def _require_plan_sha(values: dict[str, str], expected_sha: str) -> None:
        if values.get("expected-sha") != expected_sha:
            raise ValueError("candidate commit identity differs")

    async def _publish_npm(
        self,
        candidate: dagger.Directory,
        plan: NpmPlan,
        publisher: dagger.Container,
    ) -> str:
        archive = await self._npm_archive(candidate)
        publisher = publisher.with_exec(["sh", "-ceu", self._publisher_digest_command()])
        publisher = publisher.with_exec(["mkdir", "-p", "/publisher"])
        publisher = publisher.with_exec(
            ["tar", "-xzf", "publish-tools/npm-12.0.2.tgz", "-C", "/publisher"]
        )
        publisher = publisher.with_exec(["node", "/publisher/package/bin/npm-cli.js", "--version"])
        publisher = publisher.with_exec(["node", "-e", self._channel_recheck(plan)])
        command = self._npm_publish_command(archive, plan.publish_tag)
        return await publisher.with_exec(command).stdout()

    @staticmethod
    async def _npm_archive(candidate: dagger.Directory) -> str:
        entries = await candidate.directory("release/npm").entries()
        archives = tuple(entry for entry in entries if NPM_ARCHIVE.fullmatch(entry))
        if len(entries) != 1 or len(archives) != 1:
            raise ValueError("candidate must contain one exact npm archive")
        return f"release/npm/{archives[0]}"

    @staticmethod
    def _npm_publisher(
        candidate: dagger.Directory,
        oidc_url: dagger.Secret,
        oidc_token: dagger.Secret,
        environment: dict[str, str],
    ) -> dagger.Container:
        base = dag.container().from_(NODE_IMAGE).with_directory("/candidate", candidate)
        for name, value in sorted(environment.items()):
            base = base.with_env_variable(name, value)
        base = base.with_workdir("/candidate").with_secret_variable(
            "ACTIONS_ID_TOKEN_REQUEST_URL", oidc_url
        )
        return base.with_secret_variable("ACTIONS_ID_TOKEN_REQUEST_TOKEN", oidc_token)

    @staticmethod
    def _publisher_digest_command() -> str:
        archive = "publish-tools/npm-12.0.2.tgz"
        return f'test "$(sha512sum {archive} | cut -d " " -f 1)" = "{NPM_PUBLISHER_SHA512}"'

    @staticmethod
    def _channel_recheck(plan: NpmPlan) -> str:
        return (
            "const u='https://registry.npmjs.org/%40gainratio%2Fassay';"
            "const r=await fetch(u);if(![200,404].includes(r.status))"
            "throw Error('registry unavailable');"
            "const t=r.status===404?{}:(await r.json())['dist-tags'];"
            "if(typeof t!=='object'||t===null)throw Error('malformed registry tags');"
            f"if((t['{plan.channel}']??'__ABSENT__')!=='{plan.channel_version}')"
            "throw Error('npm channel changed after review');"
            f"if((t['{plan.publish_tag}']??'__ABSENT__')!=='{plan.publish_tag_version}')"
            "throw Error('npm publish tag changed after review');"
        )

    @staticmethod
    def _npm_publish_command(archive: str, publish_tag: str) -> list[str]:
        return [
            "node",
            "/publisher/package/bin/npm-cli.js",
            "publish",
            archive,
            "--access",
            "public",
            "--provenance",
            "--ignore-scripts",
            "--tag",
            publish_tag,
        ]

    def _repository(self, source: dagger.Directory) -> dagger.Container:
        return (
            self._toolchain()
            .with_env_variable("CI", "true")
            .with_directory("/src", source)
            .with_workdir("/src")
            .with_mounted_cache("/root/.cache/uv", dag.cache_volume("assay-uv"))
            .with_mounted_cache("/root/.local/share/pnpm/store", dag.cache_volume("assay-pnpm"))
            .with_exec(["uv", "sync", "--frozen", "--all-groups", "--all-extras"])
            .with_exec(["pnpm", "--dir", "ts", "install", "--frozen-lockfile", "--ignore-scripts"])
        )

    def _toolchain(self) -> dagger.Container:
        uv_image = dag.container().from_(UV_IMAGE)
        uv = uv_image.file("/uv")
        uvx = uv_image.file("/uvx")
        node = dag.http(NODE_URL, checksum=f"sha256:{NODE_SHA256}")
        return (
            self._base()
            .with_file("/usr/local/bin/uv", uv)
            .with_file("/usr/local/bin/uvx", uvx)
            .with_file("/opt/node.tar.xz", node)
            .with_exec(
                ["tar", "-xJf", "/opt/node.tar.xz", "-C", "/usr/local", "--strip-components=1"]
            )
            .with_exec(["npm", "install", "--global", f"pnpm@{PNPM_VERSION}"])
        )

    @staticmethod
    def _base() -> dagger.Container:
        install = (
            "apt-get update && apt-get install -y --no-install-recommends "
            "ca-certificates=20250419 curl=8.14.1-2+deb13u5 git=1:2.47.3-0+deb13u1 "
            "jq=1.7.1-6+deb13u4 shellcheck=0.10.0-1 xz-utils=5.8.1-1+deb13u1 && "
            "rm -rf /var/lib/apt/lists/*"
        )
        return (
            dag.container(platform=dagger.Platform("linux/amd64"))
            .from_(PYTHON_IMAGE)
            .with_exec(["sh", "-ceu", install])
        )

    @staticmethod
    def _history(commit_sha: str) -> dagger.Directory:
        if commit_sha:
            Assay._require_sha(commit_sha)
            return Assay._release_source(commit_sha)
        return dag.git(REPOSITORY_URL).branch("main").tree(depth=0, include_tags=True)

    @staticmethod
    def _release_source(commit_sha: str) -> dagger.Directory:
        return dag.git(REPOSITORY_URL).commit(commit_sha).tree(depth=0, include_tags=True)

    @staticmethod
    def _require_sha(commit_sha: str) -> None:
        valid = len(commit_sha) == SHA_LENGTH
        valid = valid and all(char in "0123456789abcdef" for char in commit_sha)
        if not valid:
            raise ValueError("commit_sha must be a lowercase 40-character Git SHA")
