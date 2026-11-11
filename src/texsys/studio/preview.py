"""Read-only project previews selected from immutable build manifests."""

import hashlib
import json
from pathlib import Path
import re
from typing import Any

from texsys.evidence import canonical_digest, file_digest
from texsys.project import ProjectError, inspect_project

PREVIEW_KINDS = {"bom", "topology", "schematic", "scene", "report"}
MAX_REPORT_BYTES = 16 * 1024 * 1024
MAX_ARTIFACT_BYTES = 8 * 1024 * 1024
MAX_HISTORY = 100
_RUN = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")


def _inside(root: Path, relative: str, boundary: Path) -> Path:
  if not isinstance(relative, str) or not relative or "\0" in relative:
    raise ProjectError("Invalid preview artifact path.")
  path = Path(relative)
  if path.is_absolute() or ".." in path.parts:
    raise ProjectError("Preview artifacts must remain inside the build directory.")
  candidate = root / path
  if any(root.joinpath(*path.parts[:i]).is_symlink() for i in range(1, len(path.parts) + 1)):
    raise ProjectError("Preview artifacts cannot use symbolic links.")
  resolved = candidate.resolve()
  if not resolved.is_relative_to(boundary.resolve()) or not resolved.is_file():
    raise ProjectError("Preview artifact is missing or outside its build snapshot.")
  return resolved


def _json(path: Path, limit: int = MAX_REPORT_BYTES) -> dict:
  if path.stat().st_size > limit:
    raise ProjectError("Preview manifest exceeds the supported size.")
  data = json.loads(path.read_text(encoding="utf-8"))
  if not isinstance(data, dict):
    raise ProjectError("Preview manifest must be an object.")
  return data


def _freshness(root: Path, report: dict) -> dict:
  # The build runner owns source/tool identity. The viewer never invents a
  # second interpretation of cache validity or treats mtime as a revision.
  from texsys.build_records import build_freshness

  try:
    return build_freshness(root, report)
  except (OSError, ValueError, ProjectError) as error:
    return {"current": False, "freshness": "invalid-record", "error": str(error)}


class ProjectPreview:
  def __init__(self, root: Path, build: str):
    self.root = root.resolve()
    self.build = (self.root / build).resolve()
    if not self.build.is_relative_to(self.root):
      raise ProjectError("Project build directory must remain inside the project.")

  def _report(self, run: str = "") -> dict | None:
    if not isinstance(run, str) or run and not _RUN.fullmatch(run):
      raise ProjectError("Invalid preview build run.")
    path = self.build / "runs" / run / "build.json" if run else self.build / "build.json"
    if not path.exists():
      if run:
        raise ProjectError("Preview build run was not found.")
      return None
    path = _inside(self.root, path.relative_to(self.root).as_posix(), self.build)
    report = _json(path)
    identifier = report.get("run_id")
    if report.get("schema_version") != 3 or not isinstance(identifier, str) or not _RUN.fullmatch(identifier):
      raise ProjectError("Rebuild this project to generate a versioned preview manifest.")
    if run and identifier != run:
      raise ProjectError("Preview build run identity does not match its manifest.")
    if report.get("record_digest") != canonical_digest({key: value for key, value in report.items() if key != "record_digest"}):
      raise ProjectError("Preview manifest digest does not match its content.")
    if not isinstance(report.get("artifacts"), list) or not isinstance(report.get("targets"), list):
      raise ProjectError("Invalid preview build manifest.")
    if any(not isinstance(item, dict) for key in ("artifacts", "targets") for item in report[key]):
      raise ProjectError("Invalid preview artifact or target record.")
    return report

  def _history(self, latest: dict | None):
    # Follow explicit build lineage; do not scan output directories or infer
    # chronology from mutable filesystem timestamps.
    report = latest
    visited = set()
    while report and len(visited) < MAX_HISTORY:
      if report["run_id"] in visited:
        break
      visited.add(report["run_id"])
      yield report
      previous = report.get("previous_run_id")
      if not previous:
        break
      try:
        report = self._report(previous)
      except (OSError, ValueError, ProjectError):
        break

  def _location(self, report: dict, record: dict) -> Path:
    run = report["run_id"]
    snapshot = self.build / "runs" / run / "files"
    file = _inside(self.root, record.get("stored_path", ""), snapshot)
    if file_digest(file) != record.get("digest"):
      raise ProjectError("Preview artifact digest does not match the build manifest.")
    return file

  def _describe(self, report: dict, record: dict, validity: dict, fallback: bool = False, *, allow_unpreviewed=False) -> dict:
    preview = record.get("preview")
    if not isinstance(preview, dict) or preview.get("kind") not in PREVIEW_KINDS:
      if not allow_unpreviewed:
        raise ProjectError("Artifact does not declare a supported preview.")
      preview = {}
    target = next((item for item in report["targets"] if item.get("id") == record.get("target")), {})
    complete = record.get("complete", not record.get("partial", False)) is True
    item = {"id": record.get("id"), "target": record.get("target"), "run_id": report["run_id"],
            "path": record.get("path"), "digest": record.get("digest"), "kind": preview.get("kind"),
            "title": preview.get("title", record.get("id", "")), "root": preview.get("root"),
            "preview": preview, "current": bool(validity.get("current")) and not fallback,
            "freshness": "historical" if fallback else validity.get("freshness", "invalid-record"),
            "complete": complete, "partial": not complete, "completeness": "complete" if complete else "partial",
            "status": target.get("status", report.get("status", "indeterminate")),
            "execution": target.get("execution", "executed"), "fallback": fallback, "available": True,
            "diagnostics": target.get("diagnostics", []), "open_items": target.get("open_items", [])}
    try:
      self._location(report, record)
    except (OSError, ValueError, ProjectError) as error:
      item.update(available=False, current=False, freshness="artifact-invalid", error=str(error))
    return item

  def read(self, root: str = "", *, _all_artifacts=False) -> dict:
    diagnostics = []
    try:
      latest = self._report()
    except (OSError, ValueError, ProjectError) as error:
      latest = None
      diagnostics.append({"message": str(error)})
    project = inspect_project(self.root)
    result: dict[str, Any] = {"schema_version": 1,
      "project": {"name": project.name if project else self.root.name, "root": root or None},
      "build": None, "artifacts": [], "diagnostics": diagnostics}
    seen: dict[tuple[str, str], dict] = {}
    latest_targets = {item.get("id"): item for item in latest["targets"]} if latest else {}
    invalid_current = set()
    for index, report in enumerate(self._history(latest)):
      validity = _freshness(self.root, report) if report is latest else {"current": False, "freshness": "historical"}
      if latest and index == 0:
        result["build"] = {"run_id": report["run_id"], "status": report.get("status"), **validity,
                           "targets": report["targets"], "diagnostics": report.get("diagnostics", [])}
      for record in report["artifacts"]:
        metadata = record.get("preview") if isinstance(record, dict) else None
        if not isinstance(metadata, dict) or metadata.get("kind") not in PREVIEW_KINDS:
          if not _all_artifacts:
            continue
          metadata = {}
        if root and metadata.get("root") != root:
          continue
        identity = (record.get("target"), record.get("id"))
        if not all(isinstance(value, str) and value for value in identity):
          continue
        if report is not latest:
          latest_target = latest_targets.get(identity[0])
          # A successful target owns its complete current output set. Neither
          # an unrelated failed branch nor an unselected old target may revive
          # outputs that were deliberately omitted from that set.
          if latest_target is None:
            continue
          if latest_target.get("status") == "pass" and latest_target.get("execution") != "blocked" and identity not in invalid_current:
            continue
        if identity in seen and seen[identity]["available"]:
          continue
        item = self._describe(report, record, validity, fallback=report is not latest, allow_unpreviewed=_all_artifacts)
        if report is latest and not item["available"]:
          invalid_current.add(identity)
        if identity in seen and not item["available"]:
          continue
        if item["fallback"]:
          item["latest_target"] = latest_targets[identity[0]]
        seen[identity] = item
      if report is latest and report["targets"] and all(target.get("status") == "pass" for target in report["targets"]) and all(item["available"] for item in seen.values()):
        break
    result["artifacts"] = list(seen.values())
    if not root:
      roots = sorted({item["root"] for item in seen.values() if isinstance(item["root"], str)})
      result["project"]["root"] = roots[0] if len(roots) == 1 else None
      result["project"]["roots"] = roots
    result["revision"] = canonical_digest({"build": result["build"], "artifacts": result["artifacts"]})
    return result

  def _generated_location(self, relative: str) -> Path:
    file = _inside(self.root, relative, self.build)
    parts = file.relative_to(self.build).parts
    if (not parts or parts[0].casefold() in {"runs", "dependencies", "studio"}
        or any(part.startswith(".") for part in parts)):
      raise ProjectError("Internal build storage is not an Explorer document.")
    return file

  def file_entries(self, relative: str | None = None) -> list[dict]:
    """Expose declared canonical files, preserving the bytes the user opens.

    Snapshot selection only supplies a possible preview identity. It never
    substitutes another path or another version for the selected file.
    """
    manifest = (self.build / "build.json").relative_to(self.root).as_posix()
    candidates: dict[str, list[dict]] = {manifest: []}
    try:
      latest = self._report()
    except (OSError, ValueError, ProjectError):
      latest = None
    selected = self.read(_all_artifacts=True)["artifacts"] if latest else []
    for item in selected:
      if isinstance(item.get("path"), str):
        candidates.setdefault(item["path"], []).append(item)
    # A missing/bad immutable snapshot must not hide the actual current file.
    # Keep it readable, but do not borrow a different path's preview identity.
    for record in latest["artifacts"] if latest else ():
      if isinstance(record.get("path"), str):
        candidates.setdefault(record["path"], [])
    entries = []
    for path, items in sorted(candidates.items()):
      if relative is not None and path != relative:
        continue
      try:
        file = self._generated_location(path)
      except (OSError, ValueError, ProjectError):
        continue
      normalized = file.relative_to(self.root)
      entry = {"path": normalized.as_posix(), "name": file.name, "parent": normalized.parent.as_posix(),
               "size": file.stat().st_size, "generated": True}
      if path != manifest:
        previews = [item for item in items if item["kind"] in PREVIEW_KINDS]
        if len(previews) > 1:
          entry["preview_error"] = "多个构建产物声明了同一个文件路径，无法确定唯一预览。"
        elif previews:
          item = previews[0]
          if item["kind"] in {"schematic", "topology", "scene"}:
            entry["preview"] = {"kind": item["kind"], "source_map": item["preview"].get("source_map", {})}
          if item["available"] and file_digest(file) == item["digest"]:
            entry["artifact"] = item
      entries.append(entry)
    return entries

  def resolve(self, run: str, target: str, identifier: str) -> tuple[dict, dict, Path, dict]:
    report = self._report(run)
    if report is None:
      raise ProjectError("No preview build is available.")
    matches = [record for record in report["artifacts"]
               if record.get("id") == identifier and (not target or record.get("target") == target)]
    if len(matches) != 1:
      raise ProjectError("Preview artifact is missing or ambiguous; select its target and build run.")
    record = matches[0]
    latest = self._report()
    fallback = latest is None or latest["run_id"] != report["run_id"]
    item = self._describe(report, record, _freshness(self.root, report), fallback)
    if fallback and latest:
      item["latest_target"] = next((target for target in latest["targets"] if target.get("id") == item["target"]), None)
    file = self._location(report, record)
    return report, record, file, item

  def artifact(self, run: str, target: str, identifier: str) -> dict:
    _, _, file, item = self.resolve(run, target, identifier)
    if item["kind"] == "scene":
      raise ProjectError("Use the scene preview endpoint for USD artifacts.")
    if file.stat().st_size > MAX_ARTIFACT_BYTES:
      raise ProjectError("Preview artifact exceeds the supported size.")
    content = file.read_bytes()
    if len(content) > MAX_ARTIFACT_BYTES or "sha256:" + hashlib.sha256(content).hexdigest() != item["digest"]:
      raise ProjectError("Preview artifact changed while reading.")
    text = content.decode("utf-8")
    suffix = file.suffix.lower()
    if suffix == ".json":
      data, format = json.loads(text), "json"
    elif suffix in {".svg", ".txt", ".md", ".csv", ".log"}:
      data, format = text, "svg" if suffix == ".svg" else "text"
    else:
      raise ProjectError("Unsupported preview artifact format.")
    return {"artifact": item, "format": format, "data": data}
