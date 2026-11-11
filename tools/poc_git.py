"""Record ordinary Git history and diffs inside disposable POC project copies."""

import subprocess


def _git(project, *arguments):
  return subprocess.run(
    ["git", "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false", *arguments],
    cwd=project, capture_output=True, text=True, check=True,
  ).stdout


def git_baseline(project):
  if (project / ".git").exists():
    raise RuntimeError(f"POC Git baseline requires a fresh isolated copy: {project}")
  _git(project, "init", "--quiet", "--initial-branch=main")
  _git(project, "add", "--", "src", "assets", "texsys.toml", "texsys.lock")
  _git(project, "-c", "user.name=Texsys POC", "-c", "user.email=poc@example.invalid",
       "commit", "--quiet", "-m", "Record isolated POC inputs")
  return _git(project, "rev-parse", "HEAD").strip()


def git_diff(project, output, *files):
  diff = _git(project, "diff", "--no-ext-diff", "--no-textconv", "--", *files)
  output.parent.mkdir(parents=True, exist_ok=True)
  output.write_text(diff, encoding="utf-8")
  return diff
