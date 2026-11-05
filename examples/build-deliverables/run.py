"""Build the same HVAC design through the public local Python API."""

import argparse
import json
from pathlib import Path

from texsys.api import ProjectError, build, evaluate
from texsys.evidence import write_json

EXAMPLE = Path(__file__).resolve().parents[1] / "hvac-dynamic-station"
RECIPES = {
  "bom": ("hvac:equipment-bom", {}),
  "physical": ("hvac:physical-model", {}),
  "system": ("hvac:system-schematic", {"view": "system"}),
  "schematic": ("hvac:system-schematic", {"view": "schematic"}),
}
EXIT_CODES = {"pass": 0, "fail": 1, "error": 2, "indeterminate": 3}


def build_one(root, kind, profile, scope):
  target, defaults = RECIPES[kind]
  if "view" in defaults and "view" in scope and scope["view"] != defaults["view"]:
    raise ProjectError("scope.view conflicts with the selected deliverable kind.")
  result = build(root, targets=(target,), profile=profile, scope={**scope, **defaults})
  report = json.loads(result.report.read_text(encoding="utf-8"))
  return {"kind": kind, "status": result.status, "project": str(root), "report": str(result.report),
          "targets": [{"id": t.definition.id, "profile": t.profile, "scope": dict(t.scope)} for t in result.targets],
          "artifacts": report["artifacts"]}


def demo(root, backend):
  runs, result_paths = [], []
  for name, expected in (("load-step", "pass"), ("pump-speed", "pass"), ("pump-trip", "fail")):
    result = evaluate(root, scenario=f"assets/scenarios/{name}.json", backend=backend)
    relative = result.report.relative_to(root).as_posix()
    runs.append({"scenario": name, "status": result.status, "expected": expected, "report": relative})
    if result.status in {"pass", "fail"}:
      result_paths.append(relative)
  outputs = [build_one(root, kind, None, {"results": result_paths} if kind in {"system", "schematic"} else {}) for kind in RECIPES]
  verified = all(run["status"] == run["expected"] for run in runs) and all(item["status"] == "pass" for item in outputs)
  summary = {"verification": "pass" if verified else "fail", "engineering_status": "fail" if any(r["status"] == "fail" for r in runs) else "indeterminate",
             "runs": runs, "outputs": outputs,
             "note": "停泵工况故意违反供冷温度约束。verification 只表示示例行为符合预期，不能作为工况合格结论。"}
  write_json(root / "build/demo.json", summary)
  return summary


def main(argv=None):
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("kind", choices=(*RECIPES, "transient", "all"))
  parser.add_argument("--project", type=Path, default=EXAMPLE)
  parser.add_argument("--profile", help="Target profile; default is declared by the target.")
  parser.add_argument("--scope", default="{}", help="JSON build scope; view is selected by kind.")
  parser.add_argument("--results", action="append", default=[], help="Attach a fixed scenario results.json to a drawing.")
  parser.add_argument("--scenario", default="assets/scenarios/load-step.json")
  parser.add_argument("--backend", choices=("scipy", "openmodelica"), default="scipy")
  args = parser.parse_args(argv)
  try:
    scope = json.loads(args.scope)
  except json.JSONDecodeError as error:
    parser.error(f"Invalid --scope JSON: {error}")
  if not isinstance(scope, dict):
    parser.error("--scope must be a JSON object.")
  if args.results:
    scope["results"] = args.results
  root = args.project.expanduser().resolve()
  try:
    if args.kind == "all":
      if args.profile or scope:
        parser.error("all uses the declared profiles and attaches its own run results.")
      output = demo(root, args.backend)
      code = 0 if output["verification"] == "pass" else 1
    elif args.kind == "transient":
      result = evaluate(root, scenario=args.scenario, backend=args.backend)
      output = {"kind": "transient", "status": result.status, "report": str(result.report), "artifacts": [str(p) for p in result.items]}
      code = EXIT_CODES[result.status]
    else:
      output = build_one(root, args.kind, args.profile, scope)
      code = EXIT_CODES[output["status"]]
  except ProjectError as error:
    output, code = {"status": "error", "message": str(error)}, 2
  print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))
  return code


if __name__ == "__main__":
  raise SystemExit(main())
