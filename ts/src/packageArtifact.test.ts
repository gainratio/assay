import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

const PACKAGE_ROOT = new URL("..", import.meta.url);
const ARCHIVE_NAME = "edgeproc-assay-0.5.0-dev.3.tgz";
const EXPECTED_ARCHIVE_SHA256 =
  "eb115cbd809762f43cf45212682144581f1f18cefa5208300251886a10d1b3ee";
const OPTIONAL_INTEGRATION =
  "Assay computes scores; Avow seals evidence. They are separate products and neither requires the other.";
const EXPECTED_MEMBERS = [
  "package/LICENSE",
  "package/README.md",
  "package/dist/additive.d.ts",
  "package/dist/additive.js",
  "package/dist/compose.d.ts",
  "package/dist/compose.js",
  "package/dist/contracts.d.ts",
  "package/dist/contracts.js",
  "package/dist/errors.d.ts",
  "package/dist/errors.js",
  "package/dist/index.d.ts",
  "package/dist/index.js",
  "package/dist/metrics.d.ts",
  "package/dist/metrics.js",
  "package/dist/minimum.d.ts",
  "package/dist/minimum.js",
  "package/dist/normalize.d.ts",
  "package/dist/normalize.js",
  "package/dist/ranking.d.ts",
  "package/dist/ranking.js",
  "package/dist/requestHash.d.ts",
  "package/dist/requestHash.js",
  "package/dist/weightedMean.d.ts",
  "package/dist/weightedMean.js",
  "package/package.json",
] as const;

function runAt(
  cwd: string | URL,
  command: string,
  args: ReadonlyArray<string>,
): string {
  return execFileSync(command, args, {
    cwd,
    encoding: "utf8",
    env: process.env,
    stdio: ["ignore", "pipe", "pipe"],
  });
}

function run(command: string, args: ReadonlyArray<string>): string {
  return runAt(PACKAGE_ROOT, command, args);
}

function pack(destination: string): string {
  run("pnpm", ["pack", "--pack-destination", destination]);
  const archive = join(destination, ARCHIVE_NAME);
  run("node", ["scripts/normalize-package-archive.mjs", archive]);
  return archive;
}

function archiveHash(path: string): string {
  return createHash("sha256").update(readFileSync(path)).digest("hex");
}

describe("the real npm artifact", () => {
  it("contains only the Assay runtime, types, metadata, license, and README", () => {
    const destination = mkdtempSync(join(tmpdir(), "assay-pack-"));
    try {
      const archive = pack(destination);
      const names = run("tar", ["-tzf", archive]).trim().split("\n").sort();

      expect(names).toEqual([...EXPECTED_MEMBERS].sort());

      const text = run("tar", ["-xOzf", archive]);
      expect(text).not.toMatch(
        /canonicalBytes|generateSeedHex|signPayload|SignedReceipt|verifySignature|receipt-ui|@noble\/ed25519|canonicalize|Writ/u,
      );
      const manifest = JSON.parse(
        run("tar", ["-xOzf", archive, "package/package.json"]),
      ) as Readonly<Record<string, unknown>>;
      const readme = run("tar", ["-xOzf", archive, "package/README.md"]);
      expect(manifest).toMatchObject({
        name: "@edgeproc/assay",
        version: "0.5.0-dev.3",
        type: "module",
        dependencies: {},
        exports: {
          ".": {
            types: "./dist/index.d.ts",
            import: "./dist/index.js",
          },
        },
      });
      expect(run("tar", ["-xOzf", archive, "package/LICENSE"])).toBe(
        readFileSync(new URL("../../LICENSE", import.meta.url), "utf8"),
      );
      expect(readme.split(OPTIONAL_INTEGRATION)).toHaveLength(2);
      expect(readme.replace(OPTIONAL_INTEGRATION, "")).not.toMatch(
        /\b(?:Avow|evidence|signing|signature|receipt|ledger)\b/iu,
      );
    } finally {
      rmSync(destination, { recursive: true, force: true });
    }
  }, 30_000);

  it("reproduces the exact archive digest with the pinned toolchain", () => {
    const first = mkdtempSync(join(tmpdir(), "assay-repro-first-"));
    const second = mkdtempSync(join(tmpdir(), "assay-repro-second-"));
    try {
      expect(process.versions.node).toBe("22.13.0");
      expect(run("pnpm", ["--version"]).trim()).toBe("11.5.0");
      const firstHash = archiveHash(pack(first));
      const secondHash = archiveHash(pack(second));

      expect(firstHash).toBe(secondHash);
      expect(firstHash).toBe(EXPECTED_ARCHIVE_SHA256);
    } finally {
      rmSync(first, { recursive: true, force: true });
      rmSync(second, { recursive: true, force: true });
    }
  }, 30_000);

  it("installs cleanly and runs every method while legacy subpaths stay closed", () => {
    const destination = mkdtempSync(join(tmpdir(), "assay-install-"));
    try {
      const archive = pack(destination);
      writeFileSync(
        join(destination, "package.json"),
        JSON.stringify({
          private: true,
          type: "module",
          packageManager: "pnpm@11.5.0",
        }),
      );
      runAt(destination, "pnpm", ["add", "--ignore-scripts", archive]);
      writeFileSync(
        join(destination, "verify.mjs"),
        `import { readFileSync } from "node:fs";
import { compose, parseRequest, parseScoreResult } from "@edgeproc/assay";

const vectors = JSON.parse(readFileSync(process.argv[2], "utf8"));
const ids = [];
for (const vector of vectors) {
  const result = compose(parseRequest(vector.request));
  if (JSON.stringify(result) !== JSON.stringify(vector.expected)) throw new Error(vector.id);
  parseScoreResult(JSON.parse(JSON.stringify(result)));
  ids.push(vector.id);
}
for (const path of ["receipt", "keys", "canonical", "ledger", "writ"]) {
  try {
    await import(\`@edgeproc/assay/\${path}\`);
    throw new Error(\`legacy subpath resolved: \${path}\`);
  } catch (error) {
    if (error instanceof Error && error.message.startsWith("legacy subpath")) throw error;
    if (!(error && typeof error === "object" && error.code === "ERR_PACKAGE_PATH_NOT_EXPORTED")) {
      throw error;
    }
  }
}
console.log(JSON.stringify(ids));
`,
      );
      const vectorPath = new URL(
        "../../testdata/vectors/composition.json",
        import.meta.url,
      );
      const output = runAt(destination, "node", [
        "verify.mjs",
        vectorPath.pathname,
      ]);

      expect(JSON.parse(output) as ReadonlyArray<string>).toEqual([
        "northstar_uncapped_weighted",
        "edgereco_recommendation",
        "amlfilter_match_confidence",
        "almamesh_domain_strength_forward_tie",
        "almamesh_domain_strength_reverse_tie",
      ]);
    } finally {
      rmSync(destination, { recursive: true, force: true });
    }
  }, 30_000);
});
