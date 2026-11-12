import json
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time

from texsys.build_records import build_lock


def test_watch_keeps_published_files_through_invalid_save_and_rebuilds(tmp_path):
  repository = Path(__file__).parents[1]
  root = tmp_path / "project"
  shutil.copytree(repository / "examples/hvac-dynamic-station", root, ignore=shutil.ignore_patterns("build", "texsys.lock"))
  manifest = root / "texsys.toml"
  manifest.write_text(manifest.read_text().replace('"../../packages/', f'"{repository / "packages"}/'))
  command = [sys.executable, "-B", "-m", "texsys"]
  subprocess.run([*command, "lock", "-p", str(root)], check=True, capture_output=True)
  layout = root / "assets/layout.json"
  original = json.loads(layout.read_text())
  report = root / "build/build.json"
  log = tmp_path / "watch.log"

  def wait_for_log(predicate):
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
      contents = log.read_text()
      if predicate(contents):
        return
      assert watcher.poll() is None, contents
      time.sleep(0.05)
    raise AssertionError(log.read_text())

  with log.open("wb") as output:
    watcher = subprocess.Popen(
      [*command, "build", "-p", str(root), "--target", "hvac:project-layout", "--watch", "--debounce", "0.2"],
      stdout=output, stderr=subprocess.STDOUT,
    )
    try:
      wait_for_log(lambda text: text.count("Evidence:") == 1)
      first = json.loads(report.read_text())
      assert first["status"] == "pass"
      scene = next(item for item in first["artifacts"] if (item.get("preview") or {}).get("kind") == "scene")
      path = root / scene["path"]
      before = path.read_bytes()

      layout.write_text("{")
      wait_for_log(lambda text: "layout.json:" in text and "Error:" in text)
      assert json.loads(report.read_text())["run_id"] == first["run_id"]
      assert path.read_bytes() == before

      with build_lock(root, "build"):
        original["objects"]["CH-01"]["origin"][0] += 0.5
        layout.write_text(json.dumps(original))
        wait_for_log(lambda text: "Waiting for the project build lock." in text)
        assert path.read_bytes() == before
      wait_for_log(lambda text: text.count("Evidence:") == 2)
      after = json.loads(report.read_text())
      assert after["previous_run_id"] == first["run_id"]
      assert after["status"] == "pass"
      assert path.read_bytes() != before
      assert (root / scene["stored_path"]).read_bytes() == before

      # Output writes and the watcher log cannot trigger another build.
      time.sleep(0.45)
      assert json.loads(report.read_text())["run_id"] == after["run_id"]
      watcher.send_signal(signal.SIGINT)
      assert watcher.wait(timeout=5) == 0
    finally:
      if watcher.poll() is None:
        watcher.kill()
        watcher.wait(timeout=5)
