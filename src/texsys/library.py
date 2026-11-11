"""Project and module component-library conventions for SDL documents."""

from dataclasses import dataclass
from pathlib import PurePosixPath


@dataclass(frozen=True)
class LibraryEntry:
  kind: str
  owner: str | None
  folder: str | None = None


def library_entries(paths: set[str]) -> dict[str, LibraryEntry]:
  """Index source-relative definitions only under recognized library roots."""
  entries = {}
  roots: dict[tuple[str, ...], str | None] = {("library",): None}
  for path in sorted(paths, key=lambda item: (item.count("/"), item)):
    parts = PurePosixPath(path).parts
    if parts[0].startswith("@"):
      roots.setdefault((parts[0], "library"), None)
    for length in range(len(parts) - 2, 0, -1):
      prefix = parts[:length]
      if prefix not in roots:
        continue
      kind = parts[length]
      if kind in {"cells", "devices"}:
        entries[path] = LibraryEntry(kind, roots[prefix])
      elif kind == "modules" and len(parts) == length + 3 and parts[-1] == parts[-2] + ".sdl.json":
        folder = parts[:-1]
        entries[path] = LibraryEntry(kind, roots[prefix], "/".join(folder))
        roots[(*folder, "library")] = path
      break
  return entries


def can_import(origin: str, entry: LibraryEntry, entries: dict[str, LibraryEntry]) -> bool:
  if entry.owner is None:
    return True
  folder = entries[entry.owner].folder
  return origin.startswith(f"{folder}/")


def import_path(origin: str, target: str) -> str:
  if origin.startswith("@") and not target.startswith("@"):
    return f"{origin.split('/', 1)[0]}/{target}"
  return target
