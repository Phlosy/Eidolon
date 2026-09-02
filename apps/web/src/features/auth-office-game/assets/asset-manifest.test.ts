import { describe, expect, it } from "vitest";
import { resolveOfficeAsset, validateOfficeAssetManifest } from "./asset-manifest";

const validManifest = {
  id: "eidolon-default",
  version: "1.0.0",
  tileSize: 32,
  fallbacks: { character: "employee-0", animation: "employee.idle.down", object: "plant" },
  assets: [
    {
      id: "tiles",
      type: "tileset",
      source: "source.png",
      author: "Eidolon",
      license: "project-generated",
      version: "1",
      tileSize: 32,
      runtimePath: "runtime/tiles.png",
    },
  ],
};

describe("office asset manifest", () => {
  it("accepts complete assets and returns a same-type fallback", () => {
    const manifest = validateOfficeAssetManifest(validManifest);
    expect(resolveOfficeAsset(manifest, "missing", "tileset").id).toBe("tiles");
  });

  it("rejects unknown tile sizes, missing provenance, and escaping paths", () => {
    expect(() => validateOfficeAssetManifest({ ...validManifest, tileSize: 16 })).toThrow(/32px/);
    expect(() =>
      validateOfficeAssetManifest({
        ...validManifest,
        assets: [{ ...validManifest.assets[0], author: "" }],
      }),
    ).toThrow(/provenance/);
    expect(() =>
      validateOfficeAssetManifest({
        ...validManifest,
        assets: [{ ...validManifest.assets[0], runtimePath: "../secret.png" }],
      }),
    ).toThrow(/unsafe/);
  });
});
