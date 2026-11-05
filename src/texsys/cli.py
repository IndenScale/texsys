"""Command-line interface for texsys."""

from importlib.metadata import version as package_version
import json
from pathlib import Path
from typing import Annotated

import typer

from texsys.dependencies import (
  add_dependency,
  lock_project,
  remove_dependency,
  sync_project,
)
from texsys.formal import format_project, lint_project
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
  source: Annotated[Path, typer.Argument(help="Path to a texsys dependency package.")],
  path: PathOption = Path("."),
  version: Annotated[
    str | None,
    typer.Option("--version", help="Require an exact package version."),
  ] = None,
) -> None:
  """Add a path dependency, lock its graph and synchronize it."""
  try:
    result = add_dependency(path, source, version)
  except ProjectError as error:
    _print_error(error)
  typer.echo(f"Added dependency; locked and synced {len(result.packages)} package(s).")


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


@app.command("lint")
def lint_command(
  path: PathOption = Path("."),
  json_output: Annotated[
    bool,
    typer.Option("--json", help="Emit machine-readable diagnostics."),
  ] = False,
) -> None:
  """Check project structure and SDL JSON exchange documents."""
  try:
    result = lint_project(path)
  except ProjectError as error:
    _print_error(error, code=2)

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



def run() -> None:
  app()
