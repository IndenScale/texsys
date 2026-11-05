"""Command-line interface for texsys."""

from importlib.metadata import version as package_version
import json
from pathlib import Path
from typing import Annotated

import typer

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



def run() -> None:
  app()
