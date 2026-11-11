"""Run real package integration commands in their own CLI process.

Unit tests can inject project providers directly. Integration tests must not
inherit domain modules imported by pytest collection or another project.
"""

import os
import subprocess
import sys
from types import SimpleNamespace


class ProjectCliRunner:
  def invoke(self, app, arguments, *, input=None):
    result = subprocess.run(
      [sys.executable, "-B", "-m", "texsys", *map(str, arguments)],
      input=input, capture_output=True, text=True, check=False, timeout=180,
      env={**os.environ, "NO_COLOR": "1"},
    )
    return SimpleNamespace(exit_code=result.returncode, output=result.stdout + result.stderr,
                           stdout=result.stdout, stderr=result.stderr)
