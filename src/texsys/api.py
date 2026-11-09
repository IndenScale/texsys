"""Public local API: one authority for builds, behavior experiments and checks."""

from texsys.dependencies import add_dependency, lock_project as lock_dependencies, remove_dependency, sync_project as sync_dependencies
from texsys.editing import apply_edit, plan_edit, read_design
from texsys.execution import ExecutionResult, build_project as build, evaluate_project as evaluate
from texsys.formal import check_project as check, lint_project as lint
from texsys.project import ProjectError

__all__ = [
  "ExecutionResult", "ProjectError", "add_dependency", "apply_edit", "build", "check", "evaluate", "lint",
  "lock_dependencies", "plan_edit", "read_design", "remove_dependency", "sync_dependencies",
]
