"""Collect immutable build evidence for discipline review in the Git workflow."""

from pathlib import PurePosixPath

from texsys.evidence import canonical_digest, file_digest, write_json
from texsys.project import inspect_project
from texsys.targets import TargetResult

from .spatial_design import artifact, missing_input, read_versioned, source_path


def build_review_package(context):
  path = source_path(context, "review.json")
  if not path.is_file():
    return missing_input(context, path, "review")
  declaration = read_versioned(path)
  build = PurePosixPath(inspect_project(context.root).build)
  if not declaration["disciplines"]:
    raise ValueError("The review package needs declared professional disciplines.")
  missing, disciplines = [], {}
  for key, record in declaration["disciplines"].items():
    artifacts = record["required_artifacts"]
    pending = record["pending"]
    if not artifacts or not isinstance(artifacts, list) or len(set(artifacts)) != len(artifacts):
      raise ValueError(f"{key}: specify distinct evidence files for professional review.")
    if not isinstance(pending, list) or any(not isinstance(item, str) or not item.strip() for item in pending):
      raise ValueError(f"{key}: specify outstanding information as a list of descriptions.")
    evidence = []
    for reference in artifacts:
      out = PurePosixPath(reference)
      if out.is_absolute() or ".." in out.parts or out.as_posix() != reference or not out.is_relative_to(build):
        raise ValueError(f"{key}: review artifacts must identify a project-relative build file.")
      staged = context.build_path / out.relative_to(build)
      if not staged.is_file():
        missing.append({"id": f"{key}:{reference}", "impact": "Required review evidence is absent from this run."})
        continue
      evidence.append({"file": reference, "digest": file_digest(staged)})
    disciplines[key] = {"name": record["name"], "artifacts": evidence, "open_items": pending,
                        "engineering_acceptance": "pending-responsible-review"}
  output = context.build_path / (context.output or "review/aidc-discipline-review.json")
  if output.suffix != ".json":
    raise ValueError("Review evidence output needs the JSON extension.")
  write_json(output, {"schema_version": 1, "evidence_complete": not missing,
                      "review_source": path.relative_to(context.root).as_posix(),
                      "input_digest": canonical_digest(context.inputs),
                      "dependencies_digest": file_digest(context.root / "texsys.lock"),
                      "engineering_acceptance": "pending-responsible-review",
                      "review_record_location": "Git change review for the corresponding design revision",
                      "disciplines": disciplines})
  return TargetResult("indeterminate" if missing else "pass", (
    artifact(context, "discipline-review-evidence", output),
  ), open_items=tuple(missing))
