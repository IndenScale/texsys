"""Public local API: one authority for builds, behavior experiments and checks."""

from texsys.execution import ExecutionResult, build_project as build, evaluate_project as evaluate
from texsys.formal import lint_project as lint
from texsys.project import ProjectError

__all__ = ["ExecutionResult", "ProjectError", "build", "evaluate", "lint"]
