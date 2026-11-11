"""Read a module's declared assets from its SDL library directory."""

from pathlib import Path
import tomllib

from texsys.library import LibraryEntry


MODULE_MANIFEST = "module.toml"
ASSET_KINDS = {
  "behavior", "evidence", "geometry", "material", "mesh", "plan", "render-scene",
  "specification", "symbol", "texture",
}


def _text(value: object) -> bool:
  return isinstance(value, str) and bool(value.strip())


def read_module_manifest(path: Path, entry: str, ports: dict) -> dict:
  """Validate the inventory and resolve only module-owned, regular asset files."""
  if path.resolve() != path or not path.is_file():
    raise ValueError("Module manifest must be a regular file without symlinks.")
  with path.open("rb") as stream:
    manifest = tomllib.load(stream)
  if type(manifest.get("schema_version")) is not int or manifest["schema_version"] != 1:
    raise ValueError("Module manifest schema_version must be 1.")
  if set(manifest) - {"schema_version", "name", "version", "entry", "assets", "port_bindings"}:
    raise ValueError("Unknown module manifest fields.")
  if not all(_text(manifest.get(key)) for key in ("name", "version")):
    raise ValueError("Module manifest needs a name and version.")
  if manifest.get("entry") != entry:
    raise ValueError(f"Module entry must be {entry}.")
  assets = manifest.get("assets")
  if not isinstance(assets, dict):
    raise ValueError("Module manifest needs an assets table.")
  for key, asset in assets.items():
    if not (_text(key) and isinstance(asset, dict)
            and set(asset) == {"kind", "path", "source", "fidelity"}
            and all(_text(value) for value in asset.values()) and asset["kind"] in ASSET_KINDS):
      raise ValueError(f"Invalid module asset {key!r}.")
    relative = asset["path"]
    if (not relative.startswith("assets/") or "\\" in relative or "\0" in relative
        or any(part in {"", ".", ".."} for part in relative.split("/"))):
      raise ValueError(f"Asset {key!r} must use a canonical path under assets/.")
    file = path.parent / relative
    if file.resolve() != file or not file.is_file():
      raise ValueError(f"Asset {key!r} must identify a regular module file: {relative}")
  bindings = manifest.get("port_bindings", {})
  if not isinstance(bindings, dict):
    raise ValueError("port_bindings must be a table.")
  for port, targets in bindings.items():
    if port not in ports or not isinstance(targets, dict) or not targets:
      raise ValueError(f"Invalid module port binding {port!r}.")
    for asset, locator in targets.items():
      if asset not in assets or not _text(locator):
        raise ValueError(f"Invalid target {asset!r} for module port {port!r}.")
  return manifest


def read_module_manifests(
  source_root: Path, entries: dict[str, LibraryEntry], documents: dict[str, dict],
) -> tuple[dict[str, dict], dict[Path, str]]:
  manifests = {}
  errors = {}
  modules = {source_root / entry.folder / MODULE_MANIFEST: path
             for path, entry in entries.items() if entry.folder is not None}
  for file in sorted((source_root / "library").rglob(MODULE_MANIFEST)):
    entry = modules.get(file)
    if entry is None:
      errors[file] = "Module manifest must be next to a recognized module entry."
      continue
    document = documents[entry]
    definition = document["components"].get(document["root"], {})
    try:
      manifests[entry] = read_module_manifest(file, Path(entry).name, definition.get("ports", {}))
    except (OSError, ValueError, RuntimeError) as error:
      errors[file] = str(error)
  return manifests, errors
