"""Read-only project previews selected from immutable build manifests."""

import hashlib
import json
from collections import OrderedDict, defaultdict
from pathlib import Path
import re
from stat import S_ISDIR, S_ISREG
from threading import RLock
from typing import Any

from texsys.evidence import canonical_digest, file_digest, write_json
from texsys.project import ProjectError, inspect_project

PREVIEW_KINDS = {"bom", "topology", "schematic", "scene", "report"}
MAX_REPORT_BYTES = 16 * 1024 * 1024
MAX_ARTIFACT_BYTES = 8 * 1024 * 1024
MAX_HISTORY = 100
_RUN = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_MAX_HISTORICAL_REPORT_BYTES = 16 * 1024 * 1024
_INDEX_FORMAT = 1
_MAX_DISK_INDEX_BYTES = 8 * 1024 * 1024


def file_stamp(path: Path):
  try:
    info = path.lstat()
  except FileNotFoundError:
    return None
  if not S_ISREG(info.st_mode):
    raise ProjectError("Preview input must be a regular file.")
  return (info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _directory_stamp(path: Path):
  try:
    info = path.lstat()
  except FileNotFoundError:
    return None
  if not S_ISDIR(info.st_mode):
    raise ProjectError("Preview directory cannot use symbolic links.")
  return (info.st_dev, info.st_ino, info.st_mode)


def _identity(record):
  target, identifier = record.get("target"), record.get("id")
  return (target, identifier) if all(isinstance(value, str) and value for value in (target, identifier)) else None


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
  def __init__(self, root: Path, build: str, *, persist_index=False):
    self.root = root.resolve()
    self.build = (self.root / build).resolve()
    if not self.build.is_relative_to(self.root):
      raise ProjectError("Project build directory must remain inside the project.")
    self._metadata_lock = RLock()
    self._latest_metadata = None
    self._indexed_metadata = None
    self._historical_metadata = OrderedDict()
    self._verified_locations = OrderedDict()
    self._index_file = self.build / "studio" / "file-preview-index.json" if persist_index else None

  def _history_stamp(self, run):
    try:
      return file_stamp(self.build / "runs" / run / "build.json")
    except (OSError, ProjectError):
      return None

  def _metadata_report(self):
    path = self.build / "build.json"
    try:
      parents = tuple(_directory_stamp(parent) for parent in path.parents if parent.is_relative_to(self.root))
      stamp = (parents, file_stamp(path))
    except (OSError, ProjectError):
      self._latest_metadata = self._indexed_metadata = None
      return None
    if self._latest_metadata is not None and self._latest_metadata[0] == stamp:
      return self._latest_metadata[1]
    self._indexed_metadata = None
    try:
      report = self._report()
    except (OSError, ValueError, ProjectError):
      report = None
    self._latest_metadata = (stamp, report)
    return report

  def _load_index(self, latest, runs):
    path = self._index_file
    if path is None:
      return None
    try:
      if _directory_stamp(path.parent) is None or file_stamp(path) is None:
        return None
      envelope = _json(path, _MAX_DISK_INDEX_BYTES)
      index = envelope["index"]
      if (index["schema"] != _INDEX_FORMAT or index["project"] != str(self.root)
          or index["publication"] != latest["record_digest"]
          or index["runs"] != (list(runs) if runs else None)
          or envelope["digest"] != canonical_digest(index)):
        return None
      reports = index["reports"]
      stamps = {run: tuple(stamp) if stamp is not None else None for run, stamp in index["stamps"].items()}
      if (not reports or reports[0] != latest["run_id"] or len(reports) > MAX_HISTORY
          or len(stamps) > MAX_HISTORY or any(not _RUN.fullmatch(run) for run in stamps)
          or any(run not in stamps for run in reports[1:])
          or any(self._history_stamp(run) != stamp for run, stamp in stamps.items())):
        return None
      records = {(target, identifier): matches for target, identifier, matches in index["records"]}
      identities = {path: {tuple(identity) for identity in matching} for path, matching in index["paths"].items()}
      return runs, stamps, reports, identities, records
    except (AttributeError, KeyError, OSError, TypeError, ValueError, ProjectError):
      return None

  def _save_index(self, latest, cached):
    path = self._index_file
    if path is None:
      return
    runs, stamps, reports, identities, records = cached
    index = {"schema": _INDEX_FORMAT, "project": str(self.root), "publication": latest["record_digest"],
             "runs": runs, "stamps": stamps, "reports": reports,
             "paths": {name: sorted(matching) for name, matching in identities.items()},
             "records": [[target, identifier, matches] for (target, identifier), matches in records.items()]}
    try:
      if _directory_stamp(path.parent) is not None:
        envelope = {"index": index, "digest": canonical_digest(index)}
        if len(json.dumps(envelope, indent=2, ensure_ascii=False).encode("utf-8")) <= _MAX_DISK_INDEX_BYTES:
          write_json(path, envelope)
    except (OSError, ValueError, ProjectError):
      # The private cache is optional. The verified in-memory index can
      # still serve this process on read-only workspaces or a full disk.
      pass

  def _metadata_history(self, latest, relative):
    cached = self._indexed_metadata
    try:
      info = (self.build / "runs").lstat()
      if not S_ISDIR(info.st_mode):
        raise ProjectError("Preview directory cannot use symbolic links.")
      # Replacement of any run directory changes its parent. Otherwise only
      # the manifest's own stamp is needed to detect in-place edits.
      runs = (info.st_dev, info.st_ino, info.st_mtime_ns, info.st_ctime_ns)
    except FileNotFoundError:
      runs = None
    except (OSError, ProjectError):
      return () if latest is None else ((latest, latest["artifacts"]),)
    if cached is not None and (cached[0] != runs or any(
        self._history_stamp(run) != stamp for run, stamp in cached[1].items())):
      cached = None
    cached = cached if cached is not None else self._load_index(latest, runs)
    if cached is None:
      reports = []
      identities = defaultdict(set)
      records = defaultdict(list)
      stamps = {}
      latest_ids = {_identity(record) for record in latest["artifacts"]}
      targets = {item.get("id"): item for item in latest["targets"] if isinstance(item.get("id"), str)}
      report = latest
      visited = set()
      while report and len(reports) < MAX_HISTORY and report["run_id"] not in visited:
        index = len(reports)
        visited.add(report["run_id"])
        reports.append(report["run_id"])
        for position, record in enumerate(report["artifacts"]):
          identity = _identity(record)
          path = record.get("path")
          if identity is None:
            continue
          target = targets.get(identity[0])
          if index and (target is None or
                        (target.get("status") == "pass" and target.get("execution") != "blocked"
                         and identity not in latest_ids)):
            continue
          if isinstance(path, str):
            identities[path].add(identity)
          records[identity].append((index, position))
        previous = report.get("previous_run_id")
        if not isinstance(previous, str) or not _RUN.fullmatch(previous) or previous in visited or len(reports) >= MAX_HISTORY:
          break
        stamps[previous] = self._history_stamp(previous)
        try:
          report = self._report(previous)
        except (OSError, ValueError, ProjectError):
          break
      cached = (runs, stamps, reports, identities, records)
      self._save_index(latest, cached)
    self._indexed_metadata = cached
    _, stamps, reports, identities, records = cached
    if relative is None:
      selected = records.keys()
    else:
      selected = identities.get(relative, ())
    grouped = defaultdict(list)
    for identity in selected:
      for index, position in records[identity]:
        grouped[index].append(position)

    def selected_reports():
      yield latest, [latest["artifacts"][position] for position in sorted(grouped.get(0, ()))]
      for index in sorted(i for i in grouped if i):
        run = reports[index]
        report = self._history_details(run, stamps[run])
        if report is None:
          break
        yield report, [report["artifacts"][position] for position in sorted(grouped[index])]

    return selected_reports()

  def _history_details(self, run, stamp):
    with self._metadata_lock:
      if stamp is None or self._history_stamp(run) != stamp:
        return None
      entry = self._historical_metadata.get(run)
      if entry and entry[0] == stamp:
        self._historical_metadata.move_to_end(run)
        return entry[1]
      try:
        report = self._report(run)
      except (OSError, ValueError, ProjectError):
        return None
      if self._history_stamp(run) != stamp:
        return None
      size = stamp[3]
      self._historical_metadata[run] = (stamp, report, size)
      self._historical_metadata.move_to_end(run)
      while sum(item[2] for item in self._historical_metadata.values()) > _MAX_HISTORICAL_REPORT_BYTES:
        self._historical_metadata.popitem(last=False)
      return report

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

  def _metadata_location(self, report: dict, record: dict) -> Path:
    stored = record.get("stored_path")
    digest = record.get("digest")
    if not isinstance(stored, str) or not isinstance(digest, str):
      return self._location(report, record)
    key = (report["run_id"], stored, digest)
    snapshot = self.build / "runs" / report["run_id"] / "files"
    file = _inside(self.root, stored, snapshot)
    stamp = file_stamp(file)
    with self._metadata_lock:
      if stamp and self._verified_locations.get(key) == stamp:
        self._verified_locations.move_to_end(key)
        return file
    verified = self._location(report, record)
    with self._metadata_lock:
      if stamp and file_stamp(file) == stamp:
        self._verified_locations[key] = stamp
        self._verified_locations.move_to_end(key)
        if len(self._verified_locations) > 512:
          self._verified_locations.popitem(last=False)
    return verified

  def _describe(self, report: dict, record: dict, validity: dict, fallback: bool = False, *, allow_unpreviewed=False, verify_location=True) -> dict:
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
    if verify_location:
      try:
        if allow_unpreviewed:
          self._metadata_location(report, record)
        else:
          self._location(report, record)
      except (OSError, ValueError, ProjectError) as error:
        item.update(available=False, current=False, freshness="artifact-invalid", error=str(error))
    return item

  def _select(self, latest: dict | None, root: str = "", *, file_metadata=False, relative: str | None = None,
              indexed=None) -> dict:
    result = {"build": None, "artifacts": []}
    seen: dict[tuple[str, str], dict] = {}
    latest_targets = {item.get("id"): item for item in latest["targets"]} if latest else {}
    invalid_current = set()
    reports = indexed if indexed is not None else ((report, report["artifacts"]) for report in self._history(latest))
    for index, (report, artifacts) in enumerate(reports):
      validity = {"current": False, "freshness": "historical"}
      if report is latest:
        validity = {"current": False, "freshness": "unchecked"} if file_metadata else _freshness(self.root, report)
      if latest and index == 0:
        result["build"] = {"run_id": report["run_id"], "status": report.get("status"), **validity,
                           "targets": report["targets"], "diagnostics": report.get("diagnostics", [])}
      for record in artifacts:
        identity = _identity(record)
        if identity is None:
          continue
        metadata = record.get("preview") if isinstance(record, dict) else None
        if not isinstance(metadata, dict) or metadata.get("kind") not in PREVIEW_KINDS:
          if not file_metadata:
            continue
          metadata = {}
        if root and metadata.get("root") != root:
          continue
        if file_metadata and record.get("published") is False:
          if report is latest:
            invalid_current.add(identity)
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
        # Ordinary generated text has no preview identity to verify. Its own
        # bytes are read by the file API, independently of immutable snapshots.
        item = self._describe(report, record, validity, fallback=report is not latest,
                              allow_unpreviewed=file_metadata, verify_location=not file_metadata or bool(metadata))
        if report is latest and not item["available"]:
          invalid_current.add(identity)
        if identity in seen and not item["available"]:
          continue
        if item["fallback"]:
          item["latest_target"] = latest_targets[identity[0]]
        seen[identity] = item
      if (report is latest and not invalid_current and report["targets"]
          and all(target.get("status") == "pass" for target in report["targets"])
          and all(item["available"] for item in seen.values())):
        break
    result["artifacts"] = [item for item in seen.values() if relative is None or item["path"] == relative]
    return result

  def read(self, root: str = "") -> dict:
    diagnostics = []
    try:
      latest = self._report()
    except (OSError, ValueError, ProjectError) as error:
      latest = None
      diagnostics.append({"message": str(error)})
    project = inspect_project(self.root)
    result: dict[str, Any] = {"schema_version": 1,
      "project": {"name": project.name if project else self.root.name, "root": root or None},
      "diagnostics": diagnostics, **self._select(latest, root)}
    if not root:
      roots = sorted({item["root"] for item in result["artifacts"] if isinstance(item["root"], str)})
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

  def file_entries(self, relative: str | None = None, *, previews_only=False, include_artifact=True) -> list[dict]:
    """Expose declared canonical files, preserving the bytes the user opens.

    Snapshot selection only supplies a possible preview identity. It never
    substitutes another path or another version for the selected file. File
    access does not verify engineering applicability; that remains the full
    preview API's responsibility. Unchecked applicability is never current.
    """
    manifest = (self.build / "build.json").relative_to(self.root).as_posix()
    candidates: dict[str, list[dict]] = {} if previews_only else {manifest: []}
    with self._metadata_lock:
      latest = self._metadata_report()
      matching = ([record for record in latest["artifacts"] if record.get("path") == relative]
                  if latest and relative is not None else [])
      ordinary = matching and all(record.get("published") is not False and
                                  (not isinstance(record.get("preview"), dict)
                                   or record["preview"].get("kind") not in PREVIEW_KINDS) for record in matching)
      indexed = self._metadata_history(latest, relative) if latest and relative not in (None, manifest) and not ordinary else None
    selected = (self._select(latest, file_metadata=True, relative=relative, indexed=indexed)["artifacts"]
                if latest and relative != manifest and not ordinary else [])
    for item in selected:
      if isinstance(item.get("path"), str) and (not previews_only or item["kind"] in PREVIEW_KINDS):
        candidates.setdefault(item["path"], []).append(item)
    # A missing/bad immutable snapshot must not hide the actual current file.
    # Keep it readable, but do not borrow a different path's preview identity.
    for record in latest["artifacts"] if latest and not previews_only else ():
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
          if include_artifact and item["available"] and file_digest(file) == item["digest"]:
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
