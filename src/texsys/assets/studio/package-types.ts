export type PackageRecord = {
  name: string;
  version: string;
  source?: string;
  registry?: string;
  path?: string;
  digest: string;
  archive_digest?: string;
  direct?: boolean;
  installed?: boolean;
  state?: string;
  description?: string;
  dependencies?: unknown;
  runtime?: unknown;
  runtime_status?: unknown;
  capabilities?: unknown;
  limitations?: unknown;
  scope?: unknown;
  tests?: string[];
};

export type PackageState = {
  revision: string;
  packages: PackageRecord[];
  diagnostics: unknown[];
  registries: unknown[];
};

export type PackageRequest = {
  action: "add" | "update" | "remove";
  name: string;
  version?: string;
  registry?: string;
  revision?: string;
};

export type PackagePlan = {
  revision: string;
  plan: string;
  changes: {
    name: string;
    before?: Partial<PackageRecord> | null;
    after?: Partial<PackageRecord> | null;
  }[];
  packages: PackageRecord[];
};

/** Display core diagnostics without inferring install or execution success. */
export function packageMessages(value: unknown): string[] {
  if (value == null) return [];
  if (Array.isArray(value)) return value.flatMap(packageMessages);
  if (typeof value === "string") return value.trim() ? [value] : [];
  if (typeof value === "object") {
    const item = value as Record<string, unknown>;
    if (typeof item.message === "string" && item.message.trim()) return [item.message];
    if (typeof item.description === "string" && item.description.trim()) return [item.description];
    return Object.entries(item).flatMap(([key, entry]) =>
      packageMessages(entry).map((text) => `${key}: ${text}`),
    );
  }
  return [String(value)];
}

export function packageKey(
  record: Pick<PackageRecord, "name" | "version" | "digest" | "registry">,
) {
  return JSON.stringify([record.registry ?? "", record.name, record.version, record.digest]);
}

export function packageSelectionAction(installed?: PackageRecord): "add" | "update" {
  return installed?.direct && installed.source === "registry" ? "update" : "add";
}

export function matchesInstalledPackage(
  selected?: PackageRecord,
  installed?: PackageRecord,
): boolean {
  return (
    !!selected &&
    !!installed &&
    installed.installed === true &&
    packageKey(selected) === packageKey(installed)
  );
}
