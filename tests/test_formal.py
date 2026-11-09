import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from texsys.cli import app
from texsys.project import initialize_project

runner = CliRunner()


def make_project(root: Path) -> Path:
  initialize_project(root)
  return root


def write_document(root: Path, document: object) -> Path:
  target = root / "src" / "system.sdl.json"
  target.write_text(json.dumps(document), encoding="utf-8")
  return target


def valid_document() -> dict:
  return {
    "schema_version": 1,
    "root": "Loop",
    "components": {
      "Pump": {
        "ports": {
          "inlet": {"kind": "water", "direction": "in"},
          "outlet": {"kind": "water", "direction": "out"},
        }
      },
      "Loop": {
        "instances": {
          "first": {"component": "Pump"},
          "second": {"component": "Pump"},
        },
        "connections": [{"from": "first.outlet", "to": "second.inlet"}],
      },
    },
  }


def test_check_reports_missing_root_component_and_instance_definition(tmp_path: Path) -> None:
  root = make_project(tmp_path)
  document = valid_document()
  document["root"] = "Missing"
  document["components"]["Loop"]["instances"]["first"]["component"] = "Absent"
  write_document(root, document)

  result = runner.invoke(app, ["check", "--json", "-p", str(root)])

  assert result.exit_code == 1
  diagnostics = json.loads(result.output)["diagnostics"]
  assert {item["rule"] for item in diagnostics} >= {"L001", "L002"}


def test_lint_reports_style_separately_from_check(tmp_path: Path) -> None:
  root = make_project(tmp_path)
  document = valid_document()
  source = write_document(root, document)
  args = ["--json", "-p", str(root)]
  original = source.read_bytes()

  assert runner.invoke(app, ["check", *args]).exit_code == 0
  result = runner.invoke(app, ["lint", *args])
  assert result.exit_code == 1
  assert [item["rule"] for item in json.loads(result.output)["diagnostics"]] == ["F001"]
  assert source.read_bytes() == original

  document["root"] = "Missing"
  source.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
  assert runner.invoke(app, ["lint", *args]).exit_code == 0
  assert runner.invoke(app, ["check", *args]).exit_code == 1

  source.write_text("{", encoding="utf-8")
  result = runner.invoke(app, ["lint", *args])
  assert result.exit_code == 3
  assert [item["rule"] for item in json.loads(result.output)["diagnostics"]] == ["F000"]
  check = runner.invoke(app, ["check", *args])
  assert [item["rule"] for item in json.loads(check.output)["diagnostics"]] == ["S001"]


def test_check_rejects_undefined_port_and_self_connection(tmp_path: Path) -> None:
  root = make_project(tmp_path)
  document = valid_document()
  document["components"]["Loop"]["connections"] = [
    {"from": "first.outlet", "to": "second.unknown"},
    {"from": "first.outlet", "to": "first.outlet"},
  ]
  write_document(root, document)

  result = runner.invoke(app, ["check", "--json", "-p", str(root)])

  assert result.exit_code == 1
  rules = {item["rule"] for item in json.loads(result.output)["diagnostics"]}
  assert rules >= {"L004", "L006", "L007", "L008"}


def test_check_rejects_duplicate_json_keys(tmp_path: Path) -> None:
  root = make_project(tmp_path)
  source = root / "src" / "system.sdl.json"
  source.write_text('{"schema_version": 1, "schema_version": 1}')

  result = runner.invoke(app, ["check", "--json", "-p", str(root)])

  assert result.exit_code == 1
  diagnostic = json.loads(result.output)["diagnostics"][0]
  assert diagnostic["rule"] == "S001"
  assert "Duplicate JSON key" in diagnostic["message"]


@pytest.mark.parametrize(("mutation", "rule"), [
  (lambda loop: loop["nets"]["CHW"]["connections"].append("missing"), "L011"),
  (lambda loop: loop["nets"]["CHW"]["connections"].append("feed"), "L012"),
  (lambda loop: loop["nets"].update(CW={"kind": "water", "connections": ["feed"]}), "L012"),
  (lambda loop: loop["nets"]["CHW"].update(kind="air"), "L013"),
  (lambda loop: loop["connections"].append({"id": "feed", "from": "second.outlet", "to": "first.inlet"}), "L010"),
])
def test_explicit_network_reports_invalid_members(tmp_path, mutation, rule):
  root = make_project(tmp_path)
  document = valid_document()
  document["schema_version"] = 2
  loop = document["components"]["Loop"]
  loop["connections"][0]["id"] = "feed"
  loop["nets"] = {"CHW": {"kind": "water", "connections": ["feed"]}}
  write_document(root, document)
  assert runner.invoke(app, ["check", "--json", "-p", str(root)]).exit_code == 0

  mutation(loop)
  write_document(root, document)
  result = runner.invoke(app, ["check", "--json", "-p", str(root)])
  assert result.exit_code == 1
  assert rule in {item["rule"] for item in json.loads(result.output)["diagnostics"]}


def test_format_check_and_diff_do_not_modify_input(tmp_path: Path) -> None:
  root = make_project(tmp_path)
  source = write_document(root, valid_document())
  original = source.read_bytes()

  check = runner.invoke(app, ["format", "--check", "-p", str(root)])
  diff = runner.invoke(app, ["fmt", "--diff", "-p", str(root)])

  assert check.exit_code == 1
  assert "Would format src/system.sdl.json" in check.output
  assert diff.exit_code == 1
  assert "--- a/src/system.sdl.json" in diff.output
  assert source.read_bytes() == original


def test_format_and_fmt_alias_are_idempotent(tmp_path: Path) -> None:
  root = make_project(tmp_path)
  source = write_document(root, valid_document())

  first = runner.invoke(app, ["fmt", "-p", str(root)])
  formatted = source.read_bytes()
  second = runner.invoke(app, ["format", "-p", str(root)])

  assert first.exit_code == 0, first.output
  assert second.exit_code == 0, second.output
  assert "No changes needed" in second.output
  assert source.read_bytes() == formatted
  assert json.loads(formatted) == valid_document()


def test_format_does_not_rewrite_invalid_json(tmp_path: Path) -> None:
  root = make_project(tmp_path)
  source = root / "src" / "broken.sdl.json"
  original = b'{"root": '
  source.write_bytes(original)

  result = runner.invoke(app, ["format", "-p", str(root)])

  assert result.exit_code == 1
  assert "S001" in result.output
  assert source.read_bytes() == original


def test_check_empty_initialized_project_passes(tmp_path: Path) -> None:
  root = make_project(tmp_path)

  result = runner.invoke(app, ["check", "--json", "-p", str(root)])

  assert result.exit_code == 0
  assert json.loads(result.output) == {"status": "pass", "files": 0, "diagnostics": []}


@pytest.mark.parametrize("direction", [[], {}])
def test_check_reports_invalid_direction_type(tmp_path: Path, direction: object) -> None:
  root = make_project(tmp_path)
  document = valid_document()
  document["components"]["Pump"]["ports"]["inlet"]["direction"] = direction
  write_document(root, document)

  result = runner.invoke(app, ["check", "--json", "-p", str(root)])

  assert result.exit_code == 1
  assert any(item["rule"] == "S002" for item in json.loads(result.output)["diagnostics"])
