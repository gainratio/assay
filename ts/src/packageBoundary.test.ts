import { readFile } from "node:fs/promises";

import { describe, expect, it } from "vitest";

interface PackageManifest {
  readonly dependencies?: Readonly<Record<string, string>>;
  readonly name?: string;
  readonly version?: string;
}

async function readPackageFile(name: string): Promise<string> {
  return readFile(new URL(`../${name}`, import.meta.url), "utf8");
}

async function readManifest(): Promise<PackageManifest> {
  const text = await readFile(
    new URL("../package.json", import.meta.url),
    "utf8",
  );
  return JSON.parse(text) as PackageManifest;
}

describe("the public Assay package boundary", () => {
  it("uses the authorized Assay prerelease identity with no runtime dependencies", async () => {
    const manifest = await readManifest();

    expect(manifest.name).toBe("@gainratio/assay");
    expect(manifest.version).toBe("0.5.0-dev.5");
    expect(manifest.dependencies ?? {}).toEqual({});
  });

  it("exports scoring but no evidence, key, receipt, or ledger API", async () => {
    const api: Record<string, unknown> = await import("./index.js");
    const names = Object.keys(api);

    expect(names).toEqual(
      expect.arrayContaining([
        "compose",
        "additive",
        "minimum",
        "normalize",
        "parseRequest",
        "parseRequestJson",
        "parseScoreResult",
        "parseScoreResultJson",
        "weightedMean",
      ]),
    );
    expect(names).not.toEqual(
      expect.arrayContaining([
        "canonicalBytes",
        "contentHash",
        "generateSeedHex",
        "publicKeyHex",
        "signPayload",
        "verifySignature",
      ]),
    );
  });

  it("documents the exact runtime and finite binary64 number contract", async () => {
    const readme = await readPackageFile("README.md");

    expect(readme).toContain("Node 22.13");
    expect(readme).toContain("ESM-only");
    expect(readme).toContain("finite IEEE-754 binary64");
  });
});
