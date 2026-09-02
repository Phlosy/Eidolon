export type OfficeAssetType = "tileset" | "atlas" | "spritesheet";

export type OfficeAsset = {
  id: string;
  type: OfficeAssetType;
  source: string;
  author: string;
  license: string;
  version: string;
  tileSize?: number;
  spriteSize?: [number, number];
  runtimePath: string;
  dataPath?: string;
};

export type OfficeAssetManifest = {
  id: string;
  version: string;
  tileSize: number;
  fallbacks: { character: string; animation: string; object: string };
  assets: OfficeAsset[];
};

const allowedTypes = new Set<OfficeAssetType>(["tileset", "atlas", "spritesheet"]);

function isSafeRelativePath(path: string): boolean {
  return !path.startsWith("/") && !path.split("/").includes("..");
}

export function validateOfficeAssetManifest(value: unknown): OfficeAssetManifest {
  if (!value || typeof value !== "object") throw new Error("Office asset manifest must be an object");
  const manifest = value as Partial<OfficeAssetManifest>;
  if (!manifest.id || !manifest.version || manifest.tileSize !== 32) {
    throw new Error("Office asset manifest requires an id, version, and 32px tile size");
  }
  if (!manifest.fallbacks?.character || !manifest.fallbacks.animation || !manifest.fallbacks.object) {
    throw new Error("Office asset manifest requires character, animation, and object fallbacks");
  }
  if (!Array.isArray(manifest.assets) || manifest.assets.length === 0) {
    throw new Error("Office asset manifest must contain assets");
  }

  const ids = new Set<string>();
  for (const asset of manifest.assets) {
    if (!asset.id || ids.has(asset.id)) throw new Error(`Duplicate or missing asset id: ${asset.id}`);
    ids.add(asset.id);
    if (!allowedTypes.has(asset.type) || !asset.source || !asset.author || !asset.license || !asset.version) {
      throw new Error(`Asset ${asset.id} is missing type or provenance`);
    }
    if (!isSafeRelativePath(asset.runtimePath) || (asset.dataPath && !isSafeRelativePath(asset.dataPath))) {
      throw new Error(`Asset ${asset.id} has an unsafe runtime path`);
    }
    if (asset.type === "tileset" && asset.tileSize !== manifest.tileSize) {
      throw new Error(`Tileset ${asset.id} does not use the theme tile size`);
    }
  }
  return manifest as OfficeAssetManifest;
}

export function resolveOfficeAsset(
  manifest: OfficeAssetManifest,
  id: string,
  type: OfficeAssetType,
): OfficeAsset {
  const exact = manifest.assets.find((asset) => asset.id === id && asset.type === type);
  if (exact) return exact;
  const sameType = manifest.assets.find((asset) => asset.type === type);
  if (!sameType) throw new Error(`Theme ${manifest.id} has no ${type} fallback`);
  return sameType;
}

