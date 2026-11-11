"""Read-only navigation index shared by Studio's file, library and instance views."""

from hashlib import sha256
from pathlib import Path

from texsys.dependencies import _read_manifest
from texsys.formal import resolve_sdl_imports
from texsys.library import library_entries
from texsys.sdl_catalog import read_sdl_catalog


def _catalog_tags(manifest: dict, field: str) -> tuple[dict[str, dict[str, list[str]]], list[str]]:
  studio = manifest.get("studio", {})
  raw = studio.get(field, {}) if isinstance(studio, dict) else None
  tags = {}
  errors = []
  if not isinstance(raw, dict):
    return tags, [f"studio.{field}"]
  for identity, dimensions in raw.items():
    if not isinstance(dimensions, dict):
      errors.append(f"studio.{field}.{identity}")
      continue
    entry = {}
    for dimension, values in dimensions.items():
      values = [values] if isinstance(values, str) else values
      if not dimension.strip() or not isinstance(values, list) or not all(
        isinstance(value, str) and value.strip() for value in values
      ):
        errors.append(f"studio.{field}.{identity}.{dimension}")
        continue
      if values:
        entry[dimension] = list(dict.fromkeys(value.strip() for value in values))
    tags[identity] = entry
  return tags, errors


def design_index(root: Path, source: str) -> dict:
  catalog = read_sdl_catalog(root / source, root)
  documents = catalog.documents
  manifest = _read_manifest(root)
  tags, tag_errors = _catalog_tags(manifest, "library_tags")
  instance_tags, instance_errors = _catalog_tags(manifest, "instance_tags")
  tag_errors.extend(instance_errors)
  for identity, dimensions in tags.items():
    for dimension in ("floor", "lot"):
      if dimension in dimensions:
        del dimensions[dimension]
        tag_errors.append(f"studio.library_tags.{identity}.{dimension}")
  entries = library_entries(set(documents))
  issues = [item.as_dict() for item in catalog.diagnostics]
  modules = catalog.modules
  sources = {name: {"id": sha256(str(pkg.root).encode()).hexdigest(), "path": str(pkg.root),
                    "name": name, "version": pkg.version, "digest": pkg.digest, "locked": True}
             for name, pkg in catalog.packages.items()}

  def source_id(path: str) -> dict:
    return {"source": sources[path[1:].split("/", 1)[0]]["id"]} if path.startswith("@") else {}

  def file_label(path: str) -> str:
    return path if path.startswith("@") else f"{source}/{path}"

  def identity(path: str, name: str) -> str:
    return f"{file_label(path)}#{name}"

  definitions = {}
  for path, document in documents.items():
    imports, errors = resolve_sdl_imports(documents, entries, path)
    for alias, message in errors.items():
      issues.append({"file": file_label(path), "object": f"imports.{alias}", "message": message})
    for name, definition in document["components"].items():
      instances = []
      for instance_name, instance in definition.get("instances", {}).items():
        alias = instance["component"]
        reference = imports.get(alias, (path, alias))
        target = documents[reference[0]]["components"].get(reference[1])
        instances.append({"name": instance_name, "type": alias,
                          "definition": identity(*reference) if target is not None else None,
                          "parameters": instance.get("parameters", {})})
      definitions[identity(path, name)] = {
        "name": name, "file": file_label(path), "instances": instances, **source_id(path),
        "ports": definition.get("ports", {}), "behavior": definition.get("behavior"),
        "connections": definition.get("connections", []),
      }
      if path in modules and name == document["root"]:
        definitions[identity(path, name)]["module_package"] = modules[path]

  def document_root(path: str) -> str | None:
    if path not in documents:
      return None
    key = identity(path, documents[path]["root"])
    return key if key in definitions else None

  project_root = document_root("system.sdl.json")

  def known_occurrence(path: str) -> bool:
    root_name, *parts = path.split(".")
    if project_root is None or definitions[project_root]["name"] != root_name or not parts:
      return False
    key = project_root
    for name in parts:
      instance = next((item for item in definitions.get(key, {}).get("instances", []) if item["name"] == name), None)
      if instance is None:
        return False
      key = instance["definition"]
    return True

  for path in tuple(instance_tags):
    if not known_occurrence(path):
      del instance_tags[path]
      tag_errors.append(f"studio.instance_tags.{path}")

  return {"root": project_root, "definitions": definitions, "instance_tags": instance_tags,
          "dependency_sources": list(sources.values()),
          "library": [{"definition": key, "kind": entry.kind,
                       "owner": document_root(entry.owner) if entry.owner else None,
                       "folder": file_label(entry.folder) if entry.folder else None, **source_id(path),
                       "tags": tags.get(key, {})}
                      for path, entry in entries.items()
                      if (key := document_root(path)) and (not entry.owner or document_root(entry.owner))],
          "diagnostics": issues, "tag_errors": tag_errors}
