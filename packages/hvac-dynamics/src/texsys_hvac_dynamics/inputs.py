"""Declared local inputs for the flat SDL physical projection consumers."""

import json
from pathlib import Path

from texsys.project import inspect_project


def physical_projection_inputs(context):
  """Include arbitrary local behavior paths, conservatively falling back on error.

  Current descriptors do not import further files. Consumers using fixed build
  result paths must additionally disable caching until those are DAG inputs.
  """
  try:
    info = inspect_project(context.root)
    source = context.root / info.source / "system.sdl.json"
    sdl = json.loads(source.read_text(encoding="utf-8"))
    references = [item["behavior"]["reference"] for item in sdl["components"].values() if item.get("behavior")]
    references += [context.scope[key] for key in ("snapshot", "overlay", "approval") if key in context.scope]
    if set(context.scope) - {"include", "ids", "snapshot", "view", "overlay", "approval", "results"}:
      return None
    for reference in references:
      if not isinstance(reference, str) or Path(reference).is_absolute() or ".." in Path(reference).parts:
        return None
      path = (context.root / reference).resolve()
      if not any(path.is_relative_to((context.root / directory).resolve()) for directory in (info.source, info.assets)):
        return None
    return tuple(sorted({f"{info.source}/**", *references}))
  except (OSError, ValueError, KeyError, TypeError, AttributeError):
    return None


def projection_cacheable(context):
  return not bool(context.scope.get("results"))
