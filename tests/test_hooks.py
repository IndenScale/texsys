import json
from pathlib import Path
import shutil
import sys
import textwrap

import pytest

from texsys.evidence import write_json
from texsys.execution import build_project
from texsys.packages import TexsysPackage
from texsys.targets import Artifact, TargetDefinition, TargetResult

EXAMPLE = Path(__file__).parents[1] / "examples" / "minimal"


class FakePackage(TexsysPackage):
  name = "fake"
  version = "1.0.0"

  def __init__(self, calls, status="pass"):
    self.calls = calls
    self.status = status

  def targets(self):
    def build(context):
      self.calls.append("main")
      artifact = context.build_path / "data.json"
      write_json(artifact, {"status": "original"})
      return TargetResult(
        self.status, (Artifact("data", artifact.relative_to(context.root).as_posix()),)
      )

    return (TargetDefinition("demo:a", self.name, self.version, build),)


def project(tmp_path: Path, monkeypatch, status="pass"):
  root = tmp_path / "project"
  shutil.copytree(EXAMPLE, root)
  calls = []
  package = FakePackage(calls, status)
  monkeypatch.setattr("texsys.execution.project_packages", lambda root: (package,))
  return root, calls


def script(root: Path, body: str) -> Path:
  path = root / "hook_script.py"
  path.write_text(
    "import json, os\nfrom pathlib import Path\n"
    "context = json.loads(Path(os.environ['TEXSYS_HOOK_CONTEXT']).read_text())\n"
    "result_path = Path(os.environ['TEXSYS_HOOK_RESULT'])\n"
    "outputs = [Path(item) for item in context['outputs']]\n"
    "output_names = [item.relative_to(context['project_root']).as_posix() for item in outputs]\n"
    + textwrap.dedent(body)
    + "\nresult_path.write_text(json.dumps(result), encoding='utf-8')\n",
    encoding="utf-8",
  )
  return path


def add_hook(
  root: Path,
  command: Path,
  phase="post",
  outputs=(),
  inputs=(),
  closure="authoritative",
  timeout=2,
  identifier="check",
  max_output_bytes=1_000_000,
) -> None:
  with (root / "texsys.toml").open("a", encoding="utf-8") as stream:
    stream.write(
      "\n[[build.hooks]]\n"
      f'id = "{identifier}"\n'
      'target = "demo:a"\n'
      f'phase = "{phase}"\n'
      f"command = {json.dumps([sys.executable, str(command)])}\n"
      f"inputs = {json.dumps(list(inputs))}\n"
      f"outputs = {json.dumps(list(outputs))}\n"
      f"timeout_seconds = {timeout}\n"
      'tool_version = "test-1"\n'
      f'closure = "{closure}"\n'
      f"max_output_bytes = {max_output_bytes}\n"
    )


def test_post_hook_consumes_artifact_and_adds_declared_output(tmp_path: Path, monkeypatch) -> None:
  root, calls = project(tmp_path, monkeypatch)
  hook = script(
    root,
    """
assert context['inputs'][0]['id'] == 'data'
assert context['inputs'][0]['digest'].startswith('sha256:')
outputs[0].write_text('{"checked": true}', encoding='utf-8')
result = {'status': 'pass', 'diagnostics': [], 'outputs': output_names, 'open_items': []}
""",
  )
  add_hook(root, hook, outputs=("build/check.json",), inputs=("artifact:data",))

  result = build_project(root, ("demo:a",))

  assert result.status == "pass"
  assert calls == ["main"]
  report = json.loads(result.report.read_text())
  hook_evidence = report["targets"][0]["hooks"][0]
  assert hook_evidence["phase"] == "post"
  assert hook_evidence["status"] == "pass"
  assert hook_evidence["inputs"][0]["id"] == "artifact:data"
  output = next(item for item in report["artifacts"] if item["path"] == "build/check.json")
  assert output["id"] == "hook.check.output-1"
  assert output["kind"] == "authoritative"
  assert output["partial"] is False


@pytest.mark.parametrize("status", ["fail", "indeterminate", "error"])
def test_authoritative_hook_status_closes_target_as_non_pass(tmp_path: Path, monkeypatch, status: str) -> None:
  root, _ = project(tmp_path, monkeypatch)
  hook = script(
    root,
    f"result = {{'status': '{status}', 'diagnostics': [], 'outputs': [], 'open_items': []}}\n",
  )
  add_hook(root, hook)

  result = build_project(root, ("demo:a",))

  assert result.status == status
  report = json.loads(result.report.read_text())
  assert report["targets"][0]["status"] == status
  assert all(item["partial"] == (status == "error") for item in report["artifacts"])


def test_nonzero_exit_without_result_is_execution_error(tmp_path: Path, monkeypatch) -> None:
  root, _ = project(tmp_path, monkeypatch)
  hook = script(root, "raise SystemExit(7)\n")
  add_hook(root, hook)

  result = build_project(root, ("demo:a",))

  assert result.status == "error"
  report = json.loads(result.report.read_text())
  assert "valid structured result" in report["targets"][0]["hooks"][0]["diagnostics"][0]["message"]


def test_timeout_kills_hook_and_reports_error(tmp_path: Path, monkeypatch) -> None:
  root, _ = project(tmp_path, monkeypatch)
  hook = script(root, "import time\ntime.sleep(5)\n")
  add_hook(root, hook, timeout=0.05)

  result = build_project(root, ("demo:a",))

  assert result.status == "error"
  report = json.loads(result.report.read_text())
  assert "timeout" in report["targets"][0]["hooks"][0]["diagnostics"][0]["message"]


def test_main_failure_skips_ordinary_post_hook(tmp_path: Path, monkeypatch) -> None:
  root, _ = project(tmp_path, monkeypatch, status="fail")
  hook = script(
    root,
    "outputs[0].write_text('should not exist')\nresult = {'status': 'pass', 'diagnostics': [], 'outputs': output_names, 'open_items': []}\n",
  )
  add_hook(root, hook, outputs=("build/post.json",))

  result = build_project(root, ("demo:a",))

  assert result.status == "fail"
  assert not (root / "build" / "post.json").exists()
  assert json.loads(result.report.read_text())["targets"][0]["hooks"] == []


def test_post_hook_cannot_modify_authoritative_artifact(tmp_path: Path, monkeypatch) -> None:
  root, _ = project(tmp_path, monkeypatch)
  hook = script(
    root,
    """
Path(context['artifacts'][0]['path']).write_text('modified', encoding='utf-8')
result = {'status': 'pass', 'diagnostics': [], 'outputs': [], 'open_items': []}
""",
  )
  add_hook(root, hook, inputs=("artifact:data",))

  result = build_project(root, ("demo:a",))

  assert result.status == "error"
  assert "modified a declared target artifact" in result.report.read_text()


def test_missing_declared_output_is_protocol_error(tmp_path: Path, monkeypatch) -> None:
  root, _ = project(tmp_path, monkeypatch)
  hook = script(
    root,
    "result = {'status': 'pass', 'diagnostics': [], 'outputs': output_names, 'open_items': []}\n",
  )
  add_hook(root, hook, outputs=("build/missing.json",))

  result = build_project(root, ("demo:a",))

  assert result.status == "error"
  assert "did not create declared output" in result.report.read_text()


def test_non_hermetic_diagnostic_error_does_not_change_target_pass(tmp_path: Path, monkeypatch) -> None:
  root, _ = project(tmp_path, monkeypatch)
  hook = script(
    root,
    "result = {'status': 'error', 'diagnostics': [{'message': 'offline'}], 'outputs': [], 'open_items': []}\n",
  )
  add_hook(root, hook, closure="diagnostic")

  result = build_project(root, ("demo:a",))

  assert result.status == "pass"
  target = json.loads(result.report.read_text())["targets"][0]
  assert target["status"] == "pass"
  assert target["hooks"][0]["status"] == "error"
  assert target["hooks"][0]["target_status_impact"] == "diagnostic-only"


def test_pre_hook_cannot_modify_source_and_skips_main(tmp_path: Path, monkeypatch) -> None:
  root, calls = project(tmp_path, monkeypatch)
  hook = script(
    root,
    """
Path(context['project_root'], 'src', 'system.sdl.json').write_text('modified')
result = {'status': 'pass', 'diagnostics': [], 'outputs': [], 'open_items': []}
""",
  )
  add_hook(root, hook, phase="pre")

  result = build_project(root, ("demo:a",))

  assert result.status == "error"
  assert calls == []
  assert "modified authoritative project inputs" in result.report.read_text()


def test_environment_is_allowlisted_and_deterministic(tmp_path: Path, monkeypatch) -> None:
  root, _ = project(tmp_path, monkeypatch)
  monkeypatch.setenv("TEXSYS_TEST_SECRET", "must-not-leak")
  hook = script(
    root,
    """
valid = 'TEXSYS_TEST_SECRET' not in os.environ and os.environ['TZ'] == 'UTC' and os.environ['PYTHONHASHSEED'] == '0'
result = {'status': 'pass' if valid else 'fail', 'diagnostics': [], 'outputs': [], 'open_items': []}
""",
  )
  add_hook(root, hook)

  result = build_project(root, ("demo:a",))

  assert result.status == "pass"
  assert "must-not-leak" not in result.report.read_text()


def test_hooks_run_serially_in_declaration_order(tmp_path: Path, monkeypatch) -> None:
  root, _ = project(tmp_path, monkeypatch)
  order_file = "build/.scratch/demo-a/order.log"
  first = script(
    root,
    f"""
with Path(context['project_root'], '{order_file}').open('a') as stream:
    stream.write('first-')
result = {{'status': 'pass', 'diagnostics': [], 'outputs': [], 'open_items': []}}
""",
  )
  second = root / "second.py"
  second.write_text(first.read_text().replace("first-", "second"))
  add_hook(root, first, identifier="first")
  add_hook(root, second, identifier="second")

  result = build_project(root, ("demo:a",))

  assert result.status == "pass"
  assert (root / order_file).read_text() == "first-second"
  hooks = json.loads(result.report.read_text())["targets"][0]["hooks"]
  assert [item["id"] for item in hooks] == ["first", "second"]


def test_output_size_limit_is_enforced(tmp_path: Path, monkeypatch) -> None:
  root, _ = project(tmp_path, monkeypatch)
  hook = script(root, "print('x' * 1000)\n")
  add_hook(root, hook, max_output_bytes=10)

  result = build_project(root, ("demo:a",))

  assert result.status == "error"
  assert "exceeded the declared output limit" in result.report.read_text()


def test_hook_detects_modified_source_shadowed_by_asset(tmp_path: Path, monkeypatch) -> None:
  root, calls = project(tmp_path, monkeypatch)
  (root / "assets").mkdir(exist_ok=True)
  (root / "assets/system.sdl.json").write_text("unrelated asset")
  hook = script(root, """
Path(context['project_root'], 'src/system.sdl.json').write_text('modified')
result = {'status': 'pass', 'diagnostics': [], 'outputs': [], 'open_items': []}
""")
  add_hook(root, hook, phase="pre")

  result = build_project(root, ("demo:a",))

  assert result.status == "error"
  assert calls == []
  assert "modified authoritative project inputs" in result.report.read_text()
