"""A published capability must execute its locked code outside the author tree."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from texsys.dependencies import add_dependency, lock_project, sync_project
from texsys.packages import runtime_status
from texsys.project import initialize_project
from texsys.registry import publish_package
from texsys.studio.runtime import input_records, snapshot_project


PLUGIN = '''import json
from texsys.packages import TexsysPackage
from texsys.targets import Artifact, TargetDefinition, TargetResult
from texsys.evidence import write_json
from runtime_support import margin

class Adapter:
  id = "capacity:change"
  name = "条件容量"
  version = "1"
  def supports(self, root):
    return (root / "src/capacity.study.json").is_file()
  def inputs(self, root):
    data = json.loads((root / "src/capacity.study.json").read_text())
    return [{"id":"demand", "label":"负荷", "file":"src/capacity.study.json", "pointer":"/demand",
             "value":data["demand"], "unit":"kW", "object_ref":"capacity:load", "min":0}]
  def impact(self, root, changes):
    return [{"id":"capacity:margin", "label":"容量余量", "inputs":["demand"]}]
  def evaluate(self, root):
    data = json.loads((root / "src/capacity.study.json").read_text())
    value = margin(data["capacity"], data["demand"])
    status = "pass" if value >= 0 else "fail"
    return {"assessment":status, "checks":[{"id":"margin", "label":"余量", "status":status,
            "actual":value,"limit":0,"unit":"kW","object_ref":"capacity:load"}],
            "extraction":[], "limitations":["测试用账面容量"], "targets":[]}

class Package(TexsysPackage):
  name = "capacity-demo"
  version = "VERSION"
  def engineering_adapters(self):
    return (Adapter(),)
  def targets(self):
    return (TargetDefinition("capacity:report",self.name,self.version,self.report),)
  def report(self, context):
    data = Adapter().evaluate(context.root)
    file = context.build_path / "capacity.json"
    write_json(file,data)
    return TargetResult(data["assessment"], artifacts=(Artifact("capacity",file.relative_to(context.root).as_posix()),))

package = Package()
'''


def _project(root, version=None):
  initialize_project(root)
  manifest = root / "texsys.toml"
  if version:
    manifest.write_text(manifest.read_text().replace(f'name = "{root.name}"', f'name = "{root.name}"\nversion = "{version}"'))
  return root


def _publish(tmp_path, *, version="1.0.0", requires=(), multiplier=1, omit_source=False):
  author = tmp_path / f"author-{version}"
  support = _project(author / "arithmetic-support", version)
  (support / "src/runtime_support.py").write_text(f"def margin(capacity, demand):\n  return (capacity-demand)*{multiplier}\n")
  with (support / "texsys.toml").open("a") as stream:
    stream.write('\n[package]\nkind="capability"\n[package.runtime]\napi_version=1\nmodules=["runtime_support"]\nrequires=[]\n')
  package = _project(author / "capacity-demo", version)
  if not omit_source:
    (package / "src/runtime_capacity.py").write_text(PLUGIN.replace("VERSION", version))
  with (package / "texsys.toml").open("a") as stream:
    stream.write(f'\n[dependencies]\narithmetic-support={{path="../arithmetic-support",version="{version}"}}\n')
    stream.write('\n[package]\nkind="capability"\ncapabilities=["solver","engineering"]\n')
    stream.write('[package.runtime]\napi_version=1\nentry_point="runtime_capacity:package"\n')
    stream.write(f'requires={json.dumps(list(requires))}\n')
  registry = tmp_path / "registry"
  publish_package(package, registry)
  shutil.rmtree(author)
  return registry


def _consumer(root, registry, *, version="1.0.0", demand=80):
  _project(root)
  with (root / "texsys.toml").open("a") as stream:
    stream.write(f'\n[dependencies]\ncapacity-demo={{registry={json.dumps(str(registry))},version="{version}"}}\n')
  (root / "src/capacity.study.json").write_text(json.dumps({"capacity":100,"demand":demand}))
  lock_project(root)
  sync_project(root)
  return root


def _python(script, *arguments, check=True):
  result = subprocess.run([sys.executable,"-B","-c",script,*map(str,arguments)],cwd=Path(arguments[0]).parent,
                          capture_output=True,text=True,env=os.environ.copy(),timeout=40)
  if check:
    assert result.returncode == 0, result.stderr + result.stdout
  return result


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path, monkeypatch):
  monkeypatch.setenv("TEXSYS_CACHE_DIR",str(tmp_path / "cache"))


def test_published_runtime_and_import_library_execute_without_author_tree_in_two_projects(tmp_path):
  registry = _publish(tmp_path)
  a = _consumer(tmp_path / "a",registry,demand=80)
  b = _consumer(tmp_path / "b",registry,demand=130)
  script = '''import json,sys
from pathlib import Path
from texsys.packages import project_packages
from texsys.execution import build_project
from texsys.engineering import run_engineering
root=Path(sys.argv[1])
packages=project_packages(root)
built=build_project(root,targets=("capacity:report",))
run=run_engineering(root,"capacity:change")
print(json.dumps({"names":[p.name for p in packages],"build":built.status,"run":run}))
'''
  outcomes = [json.loads(_python(script,root).stdout) for root in (a,b)]
  assert [item["names"] for item in outcomes] == [["capacity-demo"],["capacity-demo"]]
  assert [item["build"] for item in outcomes] == ["pass","fail"]
  assert [item["run"]["result"]["assessment"] for item in outcomes] == ["pass","fail"]
  assert outcomes[0]["run"]["toolchain_digest"] == outcomes[1]["run"]["toolchain_digest"]
  assert outcomes[0]["run"]["revision"] != outcomes[1]["run"]["revision"]
  dependencies = outcomes[0]["run"]["toolchain"]["locked_runtime"][0]["dependencies"]
  assert {item["name"] for item in dependencies} == {"capacity-demo","arithmetic-support"}
  assert runtime_status(a)["status"] == "ready"


@pytest.mark.parametrize("requirement, expected",[("texsys-nonexistent-runtime>=1","missing"),("texsys>=999","incompatible")])
def test_missing_or_incompatible_runtime_blocks_code_before_execution(tmp_path,requirement,expected):
  root = _consumer(tmp_path / "project",_publish(tmp_path,requires=[requirement]))
  state = runtime_status(root)
  assert state["status"] == "unavailable"
  assert next(item for item in state["packages"] if item["name"]=="capacity-demo")["status"] == expected
  result = _python('from pathlib import Path;import sys;from texsys.packages import project_packages;project_packages(Path(sys.argv[1]))',root,check=False)
  assert result.returncode != 0 and "runtime unavailable" in result.stderr


def test_absent_declared_module_cannot_fall_back_to_ambient_plugin(tmp_path):
  root = _consumer(tmp_path / "project",_publish(tmp_path,omit_source=True))
  assert runtime_status(root)["status"] == "unavailable"
  script = '''import sys,types
from pathlib import Path
sys.modules["runtime_capacity"]=types.ModuleType("runtime_capacity")
from texsys.packages import project_packages
project_packages(Path(sys.argv[1]))
'''
  result = _python(script,root,check=False)
  assert result.returncode != 0 and "missing from locked source" in result.stderr


def test_preloaded_developer_module_is_rejected_even_when_identity_matches(tmp_path):
  root = _consumer(tmp_path / "project",_publish(tmp_path))
  script = '''import sys,types
from pathlib import Path
fake=types.ModuleType("runtime_capacity")
fake.__file__="/unrelated/developer/runtime_capacity.py"
sys.modules["runtime_capacity"]=fake
from texsys.packages import project_packages
project_packages(Path(sys.argv[1]))
'''
  result = _python(script,root,check=False)
  assert result.returncode != 0 and "verified locked source" in result.stderr


def test_upgraded_runtime_changes_tool_evidence_and_requires_fresh_process(tmp_path):
  registry = _publish(tmp_path)
  a = _consumer(tmp_path / "a",registry)
  _publish(tmp_path,version="2.0.0",multiplier=2)
  b = _consumer(tmp_path / "b",registry,version="2.0.0")
  script = 'import json,sys;from pathlib import Path;from texsys.engineering import run_engineering;print(json.dumps(run_engineering(Path(sys.argv[1]),"capacity:change")))'
  runs = [json.loads(_python(script,root).stdout) for root in (a,b)]
  assert [item["result"]["checks"][0]["actual"] for item in runs] == [20,40]
  assert runs[0]["toolchain_digest"] != runs[1]["toolchain_digest"]
  script = '''import sys
from pathlib import Path
from texsys.packages import project_packages
project_packages(Path(sys.argv[1]))
project_packages(Path(sys.argv[2]))
'''
  result = _python(script,a,b,check=False)
  assert result.returncode != 0 and "fresh process" in result.stderr


def test_registry_snapshot_preserves_original_releases_and_rejects_changed_content(tmp_path):
  root = _consumer(tmp_path / "project",_publish(tmp_path))
  original = lock_project(root,check=True).packages
  destination = tmp_path / "snapshot"
  snapshot_project(root,destination,input_records(root))
  snapshot = lock_project(destination,check=True).packages
  assert [(p.name,p.version,p.digest,p.archive_digest) for p in snapshot] == [(p.name,p.version,p.digest,p.archive_digest) for p in original]
  entry = next(item for item in original if item.name=="capacity-demo")
  (entry.root / "src/runtime_capacity.py").write_text("raise RuntimeError('tampered')\n")
  state = runtime_status(root)
  assert state["status"] == "unavailable" and "content changed" in state["errors"][0]
  result = _python('from pathlib import Path;import sys;from texsys.packages import project_packages;project_packages(Path(sys.argv[1]))',root,check=False)
  assert result.returncode != 0 and "content changed" in result.stderr
  assert "tampered" not in result.stderr


def test_locked_only_path_runtime_does_not_test_ambient_same_name_implementation(tmp_path):
  source = _project(tmp_path / "path-capability", "1.0.0")
  (source / "src/runtime_path_lock.py").write_text('''from texsys.packages import TexsysPackage
class Package(TexsysPackage):
  name = "path-capability"
  version = "1.0.0"
  implementation = "locked"
package = Package()
''')
  with (source / "texsys.toml").open("a") as stream:
    stream.write('\n[package]\nkind="capability"\n[package.runtime]\napi_version=1\nentry_point="runtime_path_lock:package"\nrequires=[]\n')
  root = _project(tmp_path / "consumer")
  add_dependency(root,source,"1.0.0")
  sync_project(root)
  script = '''import json,sys
from pathlib import Path
from importlib import metadata
from texsys.packages import TexsysPackage,project_packages
class Ambient(TexsysPackage):
  name="path-capability"
  version="1.0.0"
  implementation="ambient"
class Entry:
  name="path-capability"
  def load(self): return Ambient()
metadata.entry_points=lambda **kwargs:[Entry()]
root=Path(sys.argv[1])
legacy=project_packages(root)[0].implementation
locked=project_packages(root,locked_only=True)[0].implementation
print(json.dumps([legacy,locked]))
'''
  assert json.loads(_python(script,root).stdout) == ["ambient","locked"]


@pytest.mark.parametrize("source_kind", ["path", "registry"])
def test_snapshot_canonicalizes_destination_alias_and_preserves_exact_packages(tmp_path, source_kind):
  if source_kind == "registry":
    root = _consumer(tmp_path / "consumer", _publish(tmp_path))
  else:
    source = _project(tmp_path / "source", "1.0.0")
    (source / "src/content.txt").write_text("immutable input\n")
    root = _project(tmp_path / "consumer")
    add_dependency(root, source, "1.0.0")
  original = lock_project(root, check=True).packages
  real_parent = tmp_path / "real-destination"
  real_parent.mkdir()
  alias = tmp_path / "destination-alias"
  alias.symlink_to(real_parent, target_is_directory=True)
  snapshot_project(root, alias / "snapshot", input_records(root))
  copied = lock_project(real_parent / "snapshot", check=True).packages
  assert [(item.name, item.version, item.source, item.digest, item.archive_digest) for item in copied] == [
    (item.name, item.version, item.source, item.digest, item.archive_digest) for item in original]
