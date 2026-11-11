"""Command-line interface for texsys."""

from importlib.metadata import version as package_version
import json
from pathlib import Path
import sys
from typing import Annotated

import typer

from texsys.dependencies import (
  add_dependency,
  lock_project,
  remove_dependency,
  sync_project,
)
from texsys.editing import apply_edit, plan_edit, read_design
from texsys.execution import build_project, evaluate_project, simulate_project, test_project
from texsys.formal import CheckResult, _load_json, check_project, format_project, lint_project
from texsys.project import (
  DEPENDENCY_FILES,
  LOCK_FILES,
  MANIFEST_NAME,
  ProjectError,
  initialize_project,
  inspect_project,
)

app = typer.Typer(
  name="texsys",
  help="Reference implementation, toolchain and ecosystem for SDL.",
  add_completion=False,
  no_args_is_help=True,
)
design_app = typer.Typer(help="Inspect, preview and apply SDL design edits.", no_args_is_help=True)
app.add_typer(design_app, name="design")
engineering_app = typer.Typer(help="Inspect engineering inputs and evaluate them with reproducible evidence.", no_args_is_help=True)
app.add_typer(engineering_app, name="engineering")
package_app = typer.Typer(help="Publish, inspect and manage versioned engineering capability packages.", no_args_is_help=True)
app.add_typer(package_app, name="package")

PathOption = Annotated[
  Path,
  typer.Option("--path", "-p", help="Project root; defaults to the current directory."),
]
CheckOption = Annotated[
  bool,
  typer.Option("--check", help="Check without modifying files."),
]
DiffOption = Annotated[
  bool,
  typer.Option("--diff", help="Print a unified diff without modifying files."),
]


def _version_callback(value: bool) -> None:
  if value:
    typer.echo(f"texsys {package_version('texsys')}")
    raise typer.Exit()


def _print_error(error: ProjectError, code: int = 1) -> None:
  typer.echo(f"Error: {error}", err=True)
  raise typer.Exit(code=code) from error


@app.callback()
def main(
  version: Annotated[
    bool,
    typer.Option(
      "--version",
      callback=_version_callback,
      is_eager=True,
      help="Show the version and exit.",
    ),
  ] = False,
) -> None:
  """Process SDL descriptions and support design automation."""


@app.command("init")
def init_command(path: PathOption = Path(".")) -> None:
  """Initialize a minimal texsys project without overwriting files."""
  try:
    result = initialize_project(path)
  except ProjectError as error:
    _print_error(error)

  root = result.project.root
  if not result.created:
    typer.echo(f"Project already initialized: {root}")
    typer.echo("No changes needed.")
    return

  action = "Updated" if result.manifest_existed else "Initialized"
  typer.echo(f"{action} project: {root}")
  for name in result.created:
    typer.echo(f"  created {name}")


def _print_file_states(root: Path, title: str, names: tuple[str, ...]) -> None:
  typer.echo(f"{title}:")
  for name in names:
    target = root / name
    if target.is_file():
      status = "present"
    else:
      status = "missing" if not target.exists() else "not a file"
    typer.echo(f"  {name}: {status}")


@app.command("info")
def info_command(path: PathOption = Path(".")) -> None:
  """Report project metadata, dependencies, locks and layout."""
  try:
    project = inspect_project(path)
  except ProjectError as error:
    _print_error(error)

  if project is None:
    typer.echo("Project: not initialized")
    typer.echo(f"Root: {path.expanduser().resolve()}")
    typer.echo(f"Manifest: {MANIFEST_NAME} (missing)")
    return

  typer.echo(f"Project: {project.name}")
  typer.echo(f"Root: {project.root}")
  typer.echo("Schema: 1")
  typer.echo("Layout:")
  layout_valid = True
  for key in ("source", "assets", "build"):
    relative_path = getattr(project, key)
    target = project.root / relative_path
    if target.is_dir():
      status = "present"
    else:
      status = "missing" if not target.exists() else "not a directory"
      layout_valid = False
    typer.echo(f"  {key}: {relative_path} ({status})")

  _print_file_states(project.root, "Dependency files", DEPENDENCY_FILES)
  _print_file_states(project.root, "Lock files", LOCK_FILES)
  if not layout_valid:
    raise typer.Exit(code=1)


@app.command("add")
def add_command(
  source: Annotated[str, typer.Argument(help="Local package path, or package name with --registry.")],
  path: PathOption = Path("."),
  version: Annotated[
    str | None,
    typer.Option("--version", help="Require an exact package version."),
  ] = None,
  registry: Annotated[Path | None, typer.Option("--registry", help="Shared package repository directory.")] = None,
) -> None:
  """Add a local or released dependency, lock its graph and synchronize it."""
  try:
    if registry is None:
      result = add_dependency(path, Path(source), version)
    else:
      from texsys.dependencies import add_registry_dependency
      if version is None:
        raise ProjectError("Registry packages require an explicit --version.")
      result = add_registry_dependency(path, source, version, registry.expanduser().resolve())
  except ProjectError as error:
    _print_error(error)
  typer.echo(f"Added dependency; locked and synced {len(result.packages)} package(s).")


@package_app.command("pack")
def package_pack_command(
  source: Annotated[Path, typer.Argument(help="Capability package source directory.")],
  output: Annotated[Path | None, typer.Option("--output", "-o", help="Output directory for portable .texpkg releases.")] = None,
) -> None:
  """Prepare immutable package archives, including declared dependencies."""
  from texsys.registry import pack_package
  try:
    result = pack_package(source, output)
  except ProjectError as error:
    _print_error(error)
  typer.echo(json.dumps(result, ensure_ascii=False, indent=2))


@package_app.command("publish")
def package_publish_command(
  source: Annotated[Path, typer.Argument(help="Capability directory or .texpkg archive.")],
  registry: Annotated[Path, typer.Option("--registry", help="Shared package repository directory.")],
) -> None:
  """Publish immutable exact versions to a local or shared repository."""
  from texsys.registry import publish_package
  try:
    result = publish_package(source, registry)
  except ProjectError as error:
    _print_error(error)
  typer.echo(json.dumps(result, ensure_ascii=False, indent=2))


@package_app.command("list")
def package_list_command(
  registry: Annotated[Path, typer.Option("--registry", help="Shared package repository directory.")],
  query: Annotated[str, typer.Option("--query", "-q", help="Search package names and descriptions.")] = "",
) -> None:
  """Browse available package versions without changing a project."""
  from texsys.registry import search_packages
  try:
    result = search_packages(registry, query)
  except ProjectError as error:
    _print_error(error)
  typer.echo(json.dumps(result, ensure_ascii=False, indent=2))


@package_app.command("show")
def package_show_command(
  name: Annotated[str, typer.Argument(help="Released package name.")],
  version: Annotated[str, typer.Option("--version", help="Exact package version.")],
  registry: Annotated[Path, typer.Option("--registry", help="Shared package repository directory.")],
) -> None:
  """Show package contents, capabilities, scope and runtime requirements."""
  from texsys.registry import package_details
  try:
    result = package_details(registry, name, version)
  except ProjectError as error:
    _print_error(error)
  typer.echo(json.dumps(result, ensure_ascii=False, indent=2))


@package_app.command("status")
def package_status_command(path: PathOption = Path(".")) -> None:
  """Report declared, locked, synchronized and runtime states separately."""
  from texsys.dependencies import dependency_status
  from texsys.packages import runtime_status
  try:
    result = dependency_status(path)
    result["runtime"] = runtime_status(path)
  except ProjectError as error:
    _print_error(error)
  typer.echo(json.dumps(result, ensure_ascii=False, indent=2))


@package_app.command("plan")
def package_plan_command(
  request: Annotated[Path, typer.Argument(help="JSON dependency change request.")],
  path: PathOption = Path("."),
) -> None:
  """Preview package version and content changes before applying them."""
  from texsys.dependencies import plan_dependency_change
  try:
    result = plan_dependency_change(path, _load_json(request.read_text(encoding="utf-8")))
  except (ProjectError, OSError, ValueError) as error:
    _print_error(error if isinstance(error, ProjectError) else ProjectError(str(error)))
  typer.echo(json.dumps(result, ensure_ascii=False, indent=2))


@package_app.command("test")
def package_test_command(
  name: Annotated[str, typer.Argument(help="Locked package whose bundled tests should run.")],
  path: PathOption = Path("."),
) -> None:
  """Run the capability's own tests; this does not accept a project design."""
  from texsys.package_tests import test_capability_package
  try:
    result = test_capability_package(path, name)
  except ProjectError as error:
    _print_error(error)
  typer.echo(json.dumps(result, ensure_ascii=False, indent=2))
  if result["status"] != "pass":
    raise typer.Exit(code=1 if result["status"] == "fail" else 2)


@package_app.command("apply")
def package_apply_command(
  request: Annotated[Path, typer.Argument(help="Previously previewed JSON dependency change request.")],
  plan: Annotated[str, typer.Option("--plan", help="Exact preview digest.")],
  path: PathOption = Path("."),
) -> None:
  """Apply only an unchanged, explicitly previewed dependency change."""
  from texsys.dependencies import apply_dependency_change
  try:
    result = apply_dependency_change(path, _load_json(request.read_text(encoding="utf-8")), plan)
  except (ProjectError, OSError, ValueError) as error:
    _print_error(error if isinstance(error, ProjectError) else ProjectError(str(error)))
  typer.echo(json.dumps(result, ensure_ascii=False, indent=2))


@app.command("remove")
def remove_command(
  name: Annotated[str, typer.Argument(help="Direct dependency name.")],
  path: PathOption = Path("."),
) -> None:
  """Remove a direct dependency, relock and synchronize."""
  try:
    result = remove_dependency(path, name)
  except ProjectError as error:
    _print_error(error)
  typer.echo(f"Removed {name}; locked and synced {len(result.packages)} package(s).")


@app.command("lock")
def lock_command(
  path: PathOption = Path("."),
  check: Annotated[
    bool,
    typer.Option("--check", help="Verify the lock without modifying it."),
  ] = False,
) -> None:
  """Resolve design dependencies and write or verify texsys.lock."""
  try:
    result = lock_project(path, check=check)
  except ProjectError as error:
    _print_error(error)
  if check:
    typer.echo("Lock file is up to date.")
  else:
    typer.echo(f"Locked {len(result.packages)} package(s): {path.expanduser().resolve()}")


@app.command("sync")
def sync_command(path: PathOption = Path(".")) -> None:
  """Restore design dependencies exactly from texsys.lock."""
  try:
    packages = sync_project(path)
  except ProjectError as error:
    _print_error(error)
  typer.echo(f"Synced {len(packages)} package(s).")


def _print_design_result(result: dict) -> None:
  typer.echo(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
  if result.get("status") in {"fail", "conflict", "error"}:
    raise typer.Exit(code={"fail": 1, "conflict": 3, "error": 2}[result["status"]])


@design_app.command("inspect")
def inspect_design_command(path: PathOption = Path("."), source: str = "system.sdl.json") -> None:
  """Return the current SDL document and input revision as JSON."""
  try:
    result = read_design(path, source)
  except (OSError, ValueError, ProjectError) as error:
    result = {"status": "error", "message": str(error)}
  _print_design_result(result)


def _run_design_edit(path: Path, request: str, plan: str | None) -> None:
  try:
    text = sys.stdin.read() if request == "-" else Path(request).read_text(encoding="utf-8")
    payload = _load_json(text)
    result = plan_edit(path, payload) if plan is None else apply_edit(path, payload, plan)
  except (OSError, ValueError, ProjectError) as error:
    result = {"status": "error", "message": str(error)}
  _print_design_result(result)


@design_app.command("plan")
def plan_design_command(request: str, path: PathOption = Path(".")) -> None:
  """Preview a JSON request from a file or - for stdin."""
  _run_design_edit(path, request, None)


@design_app.command("apply")
def apply_design_command(
  request: str,
  plan: Annotated[str, typer.Option(help="Digest returned by design plan.")],
  path: PathOption = Path("."),
) -> None:
  """Apply a previewed JSON request to the same input revision."""
  _run_design_edit(path, request, plan)


def _engineering_result(action, *args, **kwargs):
  try:
    result = action(*args, **kwargs)
  except (ProjectError, OSError, ValueError) as error:
    result = {"status": "error", "message": str(error)}
  _print_design_result(result)
  if result.get("execution") == "error":
    raise typer.Exit(code=2)
  assessment = result.get("result", {}).get("assessment")
  if assessment in {"fail", "indeterminate"}:
    raise typer.Exit(code=1 if assessment == "fail" else 3)


@engineering_app.command("inspect")
def inspect_engineering_command(path: PathOption = Path("."), adapter: str | None = None):
  """Show engineering inputs, current evidence and run history as JSON."""
  from texsys.engineering import inspect_engineering
  _engineering_result(inspect_engineering, path, adapter)


@engineering_app.command("run")
def run_engineering_command(adapter: str, path: PathOption = Path(".")):
  """Evaluate a fixed design and preserve evidence; exit 1 means engineering failure."""
  from texsys.engineering import run_engineering
  _engineering_result(run_engineering, path, adapter)


@app.command("check")
def check_command(
  path: PathOption = Path("."),
  json_output: Annotated[
    bool,
    typer.Option("--json", help="Emit machine-readable diagnostics."),
  ] = False,
) -> None:
  """Check project structure, parse and validate SDL, and run domain checks."""
  try:
    result = check_project(path)
  except ProjectError as error:
    _print_error(error, code=2)
  _print_check_result(result, json_output)


@app.command("lint")
def lint_command(
  path: PathOption = Path("."),
  json_output: Annotated[
    bool,
    typer.Option("--json", help="Emit machine-readable diagnostics."),
  ] = False,
) -> None:
  """Report SDL source style issues without modifying files."""
  try:
    result = lint_project(path)
  except ProjectError as error:
    _print_error(error, code=2)
  _print_check_result(result, json_output)


def _print_check_result(result: CheckResult, json_output: bool) -> None:
  if json_output:
    typer.echo(json.dumps(result.as_dict(), ensure_ascii=False, indent=2, sort_keys=True))
  else:
    typer.echo(f"Status: {result.status}")
    typer.echo(f"Files: {result.files}")
    for item in result.diagnostics:
      location = item.file
      if item.line is not None:
        location += f":{item.line}"
        if item.column is not None:
          location += f":{item.column}"
      object_suffix = f" [{item.object}]" if item.object else ""
      typer.echo(
        f"{location}: {item.status} {item.rule}: {item.message}{object_suffix}"
      )
  if result.status == "fail":
    raise typer.Exit(code=1)
  if result.status == "indeterminate":
    raise typer.Exit(code=3)
  if result.status == "error":
    raise typer.Exit(code=2)


def _format(path: Path, check: bool, diff: bool) -> None:
  try:
    result = format_project(path, check=check, diff=diff)
  except ProjectError as error:
    _print_error(error, code=2)
  if result.diff:
    typer.echo(result.diff, nl=False)
  for item in result.diagnostics:
    typer.echo(f"{item.file}: {item.status} {item.rule}: {item.message}", err=True)
  for name in result.changed:
    action = "Would format" if check or diff else "Formatted"
    typer.echo(f"{action} {name}")
  if not result.changed and not result.diagnostics:
    typer.echo("No changes needed.")
  if result.diagnostics or ((check or diff) and result.changed):
    raise typer.Exit(code=1)


@app.command("format")
def format_command(
  path: PathOption = Path("."),
  check: CheckOption = False,
  diff: DiffOption = False,
) -> None:
  """Format SDL JSON exchange documents without changing their values."""
  _format(path, check, diff)


@app.command("fmt", hidden=True)
def fmt_command(
  path: PathOption = Path("."),
  check: CheckOption = False,
  diff: DiffOption = False,
) -> None:
  """Alias for format."""
  _format(path, check, diff)


def _finish_execution(status: str, report: Path, root: Path) -> None:
  typer.echo(f"Status: {status}")
  typer.echo(f"Evidence: {report.relative_to(root.expanduser().resolve())}")
  if status == "fail":
    raise typer.Exit(code=1)
  if status == "indeterminate":
    raise typer.Exit(code=3)
  if status == "error":
    raise typer.Exit(code=2)


@app.command("build")
def build_command(
  path: PathOption = Path("."),
  target: Annotated[
    list[str] | None,
    typer.Option("--target", help="Build this target and its dependencies; repeatable."),
  ] = None,
  profile: Annotated[
    str | None,
    typer.Option("--profile", help="Override the selected target profile."),
  ] = None,
  scope: Annotated[
    str | None,
    typer.Option("--scope", help="Override the selected target scope with a JSON object."),
  ] = None,
) -> None:
  """Check locked inputs and deterministically generate selected target artifacts."""
  parsed_scope = None
  if scope is not None:
    try:
      parsed_scope = json.loads(scope)
    except json.JSONDecodeError as error:
      _print_error(ProjectError(f"Invalid --scope JSON: {error}"), code=2)
    if not isinstance(parsed_scope, dict):
      _print_error(ProjectError("--scope must be a JSON object."), code=2)
  try:
    result = build_project(path, tuple(target or ()), profile, parsed_scope)
  except ProjectError as error:
    _print_error(error, code=2)
  for resolved in result.targets:
    typer.echo(
      f"Target {resolved.definition.id} (profile={resolved.profile}, "
      f"source={resolved.resolution_source})"
    )
  for item in result.items:
    typer.echo(f"Generated {item.relative_to(path.expanduser().resolve())}")
  _finish_execution(result.status, result.report, path)


@app.command("test")
def test_command(
  path: PathOption = Path("."),
  scenario: Annotated[str | None, typer.Option("--scenario", help="Fixed behavior scenario inside project assets.")] = None,
  backend: Annotated[str, typer.Option("--backend", help="Behavior backend: scipy or openmodelica.")] = "scipy",
) -> None:
  """Run behavior and capacity cases as stimulus, solve and assertions."""
  try:
    result = evaluate_project(path, scenario, backend) if scenario else test_project(path)
  except ProjectError as error:
    _print_error(error, code=2)
  if scenario:
    for item in result.items:
      typer.echo(f"Generated {item.relative_to(path.expanduser().resolve())}")
    _finish_execution(result.status, result.report, path)
    return
  for item in result.items:
    typer.echo(f"{item.name}: {item.status}")
    for assertion in item.assertions:
      typer.echo(
        f"  {assertion.status}: {assertion.assertion.metric} "
        f"{assertion.assertion.operator} {assertion.assertion.expected} "
        f"(actual: {assertion.actual})"
      )
  _finish_execution(result.status, result.report, path)


@app.command("sim")
def sim_command(
  path: PathOption = Path("."),
  kind: Annotated[
    str,
    typer.Option("--kind", help="Simulation model: event; field is reserved and unimplemented."),
  ] = "event",
) -> None:
  """Run geometric installation events and independently assert collisions."""
  try:
    result = simulate_project(path, kind)
  except ProjectError as error:
    _print_error(error, code=2)
  for item in result.items:
    typer.echo(f"{item['name']}: {item['status']}")
  _finish_execution(result.status, result.report, path)


@app.command("studio")
def studio_command(
  path: PathOption = Path("."),
  port: Annotated[int, typer.Option("--port", min=0, max=65535, help="Local Studio HTTP port.")] = 8710,
  open_browser: Annotated[bool, typer.Option("--open", help="Open Studio in the default browser.")] = False,
  hmr: Annotated[bool, typer.Option("--hmr", help="Run the Studio frontend with Vite hot module replacement.")] = False,
) -> None:
  """Open the plugin-based Tex Studio workbench for a local project."""
  from texsys.studio.server import serve, serve_hmr
  try:
    (serve_hmr if hmr else serve)(path, port, open_browser)
  except (ProjectError, OSError) as error:
    _print_error(ProjectError(str(error)), code=2)


def run() -> None:
  app()
