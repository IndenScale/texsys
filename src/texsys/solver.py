"""Backend-neutral stimulus, solver and assertion contracts."""

from dataclasses import dataclass, field
import math
from typing import Any, Mapping, Protocol


@dataclass(frozen=True)
class Stimulus:
  model: Any
  parameters: Mapping[str, float] = field(default_factory=dict)
  scenario: str = "default"


@dataclass(frozen=True)
class Solution:
  status: str
  values: Mapping[str, float]
  solver: str
  model_digest: str
  diagnostics: tuple[str, ...] = ()


class Solver(Protocol):
  def solve(self, stimulus: Stimulus) -> Solution: ...


@dataclass(frozen=True)
class Assertion:
  metric: str
  operator: str
  expected: float
  description: str
  tolerance: float = 1e-9


@dataclass(frozen=True)
class AssertionResult:
  assertion: Assertion
  status: str
  actual: float | None


@dataclass(frozen=True)
class TestCase:
  name: str
  stimulus: Stimulus
  solver: Solver
  assertions: tuple[Assertion, ...]


@dataclass(frozen=True)
class TestResult:
  name: str
  status: str
  solution: Solution
  assertions: tuple[AssertionResult, ...]

  def as_dict(self) -> dict[str, Any]:
    return {
      "name": self.name,
      "status": self.status,
      "solution": {
        "status": self.solution.status,
        "solver": self.solution.solver,
        "model_digest": self.solution.model_digest,
        "values": dict(sorted(self.solution.values.items())),
        "diagnostics": list(self.solution.diagnostics),
      },
      "assertions": [
        {
          "metric": item.assertion.metric,
          "operator": item.assertion.operator,
          "expected": item.assertion.expected,
          "actual": item.actual,
          "tolerance": item.assertion.tolerance,
          "description": item.assertion.description,
          "status": item.status,
        }
        for item in self.assertions
      ],
    }


def _compare(actual: float, assertion: Assertion) -> bool:
  expected = assertion.expected
  tolerance = assertion.tolerance
  if assertion.operator == ">=":
    return actual >= expected - tolerance
  if assertion.operator == "<=":
    return actual <= expected + tolerance
  if assertion.operator == ">":
    return actual > expected + tolerance
  if assertion.operator == "<":
    return actual < expected - tolerance
  if assertion.operator == "==":
    return math.isclose(actual, expected, rel_tol=tolerance, abs_tol=tolerance)
  raise ValueError(f"Unsupported assertion operator: {assertion.operator}")


def run_test(case: TestCase) -> TestResult:
  """Run one stimulus-solve-assert case without interpreting domain semantics."""
  try:
    solution = case.solver.solve(case.stimulus)
  except Exception as error:
    solution = Solution(
      status="error",
      values={},
      solver=type(case.solver).__name__,
      model_digest="",
      diagnostics=(str(error),),
    )
  if solution.status == "error":
    return TestResult(case.name, "error", solution, ())

  results: list[AssertionResult] = []
  for assertion in case.assertions:
    actual = solution.values.get(assertion.metric)
    if actual is None or not math.isfinite(actual):
      results.append(AssertionResult(assertion, "indeterminate", actual))
      continue
    try:
      passed = _compare(actual, assertion)
    except ValueError:
      results.append(AssertionResult(assertion, "indeterminate", actual))
      continue
    results.append(AssertionResult(assertion, "pass" if passed else "fail", actual))

  statuses = {item.status for item in results}
  if solution.status == "indeterminate":
    status = "indeterminate"
  elif "fail" in statuses:
    status = "fail"
  elif "indeterminate" in statuses:
    status = "indeterminate"
  else:
    status = "pass"
  return TestResult(case.name, status, solution, tuple(results))
