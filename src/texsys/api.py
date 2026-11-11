"""Public local API: one authority for builds, behavior experiments and checks."""

from texsys.dependencies import (add_dependency, add_registry_dependency, apply_dependency_change, dependency_status,
                                 lock_project as lock_dependencies, plan_dependency_change, remove_dependency,
                                 sync_project as sync_dependencies)
from texsys.editing import apply_edit, plan_edit, read_design
from texsys.engineering import (accept_engineering, apply_engineering_change, inspect_engineering,
                               plan_engineering_change, run_engineering)
from texsys.execution import ExecutionResult, build_project as build, evaluate_project as evaluate
from texsys.formal import check_project as check, lint_project as lint
from texsys.project import ProjectError
from texsys.registry import pack_package, package_details, publish_package, search_packages
from texsys.package_tests import test_capability_package

__all__ = [
  "ExecutionResult", "ProjectError", "add_dependency", "apply_edit", "build", "check", "evaluate", "lint",
  "lock_dependencies", "plan_edit", "read_design", "remove_dependency", "sync_dependencies",
  "accept_engineering", "apply_engineering_change", "inspect_engineering", "plan_engineering_change", "run_engineering",
  "add_registry_dependency", "apply_dependency_change", "dependency_status", "plan_dependency_change",
  "pack_package", "package_details", "publish_package", "search_packages", "test_capability_package",
]
