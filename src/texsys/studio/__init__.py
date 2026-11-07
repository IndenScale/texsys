"""Versioned, discipline-neutral Studio plugin contract."""

from importlib.metadata import entry_points
from pathlib import Path
import re


class StudioPlugin:
  manifest: dict
  frontend: Path

  def state(self, root: Path, artifacts: list[dict]) -> dict:
    raise NotImplementedError

  def edit(self, root: Path, operation: dict) -> dict:
    raise NotImplementedError

  def validate_action(self, root: Path, action: str, options: dict) -> None:
    raise NotImplementedError

  def execute(self, root: Path, action: str, options: dict) -> dict:
    raise NotImplementedError


def discover_plugins(entries=None):
  plugins, diagnostics, duplicates = {}, [], set()
  entries = entry_points(group="texsys.studio") if entries is None else entries
  for entry in sorted(entries, key=lambda item: item.name):
    try:
      plugin = entry.load()
      if not isinstance(plugin, StudioPlugin):
        raise ValueError("Plugin does not implement StudioPlugin.")
      manifest = plugin.manifest
      identity = manifest["id"]
      if not isinstance(identity, str) or not re.fullmatch(r"[a-z][a-z0-9-]*:[a-z][a-z0-9-]*", identity):
        raise ValueError("A namespaced professional ID is required.")
      if type(manifest.get("api_version")) is not int or manifest["api_version"] != 1:
        raise ValueError("Incompatible Studio API version; expected 1.")
      if any(not isinstance(manifest.get(key), str) or not manifest[key] for key in ("name", "version", "discipline")):
        raise ValueError("Plugin name, version and discipline are required.")
      if not isinstance(manifest.get("capabilities"), list) or not plugin.frontend.is_file():
        raise ValueError("Plugin capabilities and an existing frontend entry are required.")
      if identity in plugins or identity in duplicates:
        plugins.pop(identity, None)
        duplicates.add(identity)
        raise ValueError(f"Duplicate professional ID: {identity}; neither registration is enabled.")
      plugins[identity] = plugin
    except Exception as error:
      diagnostics.append({"plugin": entry.name, "message": str(error)})
  return plugins, diagnostics
