import json
from pathlib import Path
import runpy
import shutil
import xml.etree.ElementTree as ET

import pytest

from texsys.api import build
from texsys.dependencies import lock_project

ROOT = Path(__file__).parents[1]


@pytest.fixture
def pipeline(tmp_path, capsys):
  root = tmp_path / 'station'
  shutil.copytree(ROOT / 'examples/hvac-dynamic-station', root, ignore=shutil.ignore_patterns('build'))
  (root / 'build').mkdir()
  manifest = root / 'texsys.toml'
  manifest.write_text(manifest.read_text().replace('"../../packages/', f'"{ROOT / "packages"}/'))
  lock_project(root)
  script = runpy.run_path(str(ROOT / 'examples/build-deliverables/run.py'))
  assert script['main'](['all', '--project', str(root)]) == 0
  output = json.loads(capsys.readouterr().out)
  assert output['verification'] == 'pass' and output['engineering_status'] == 'fail'
  return root


def test_same_design_snapshot_owns_bom_both_drawings_and_all_runs(pipeline):
  root = pipeline
  model = json.loads((root / 'build/hvac/behavior/model.json').read_text())
  bom = json.loads((root / 'build/hvac/bom.json').read_text())
  expected_ids = {c['identity'] for c in model['components']}
  assert len(expected_ids) == 8
  assert bom['model_digest'] == model['snapshot_digest']
  for view in ('system', 'schematic'):
    document = json.loads((root / f'build/hvac/{view}/system.json').read_text())
    assert document['model_digest'] == model['snapshot_digest']
    assert {n['semantic_ref'] for n in document['nodes']} == expected_ids
    assert len(document['scenario_results']) == 3
    svg = (root / f'build/hvac/{view}/system.svg').read_text()
    assert 'assets/physics' not in svg and 'Behavior [assumed]' not in svg
    assert ('COP 4.5' in svg) == (view == 'schematic')
  for name, expected in (('load-step', 'pass'), ('pump-speed', 'pass'), ('pump-trip', 'fail')):
    result = json.loads((root / f'build/tests/scenarios/{name}/scipy/results.json').read_text())
    assert result['model_digest'] == model['snapshot_digest']
    assert result['status'] == expected
    assert {v['semantic_ref'] for v in result['variables'].values() if v['role'] == 'fluid-temperature'} == expected_ids


def test_static_drawio_and_elk_share_exact_engineering_port_geometry(pipeline):
  directory = pipeline / 'build/hvac/schematic'
  document = json.loads((directory / 'system.json').read_text())
  xml = ET.fromstring((directory / 'system.drawio').read_text())
  objects = {obj.attrib['id']: obj for obj in xml.findall('.//UserObject')}
  for node in document['nodes']:
    assert node['graphic']['template'] != 'neutral'
    assert node['ports'] == node['graphic']['ports']
    for port in node['ports']:
      geometry = objects[port['id']].find('./mxCell/mxGeometry')
      assert float(geometry.attrib['x']) * node['width'] == pytest.approx(port['x'], abs=.16)
      assert float(geometry.attrib['y']) * node['height'] == pytest.approx(port['y'], abs=.11)
  # Regenerate one view without changing engineering inputs or run evidence.
  before = {p.name: p.read_bytes() for p in directory.iterdir() if p.is_file()}
  results = [f'build/tests/scenarios/{n}/scipy/results.json' for n in ('load-step', 'pump-speed', 'pump-trip')]
  execution = build(pipeline, targets=('hvac:system-schematic',), scope={'view': 'schematic', 'results': results})
  assert execution.status == 'pass'
  assert {p.name: p.read_bytes() for p in directory.iterdir() if p.is_file()} == before


def test_changed_scenario_cannot_be_replayed_as_current_design_evidence(pipeline):
  path = pipeline / 'assets/scenarios/load-step.json'
  scenario = json.loads(path.read_text())
  scenario['signals']['ColdStation.LOAD-01.load_w']['points'][1][1] += 1000
  path.write_text(json.dumps(scenario))
  execution = build(pipeline, targets=('hvac:system-schematic',), scope={'view': 'schematic', 'results': ['build/tests/scenarios/load-step/scipy/results.json']})
  assert execution.status == 'error'
  assert 'stale' in execution.report.read_text()


def test_missing_constitutive_input_downgrades_only_behavior_not_spatial_status(pipeline):
  path = pipeline / 'src/system.sdl.json'
  source = json.loads(path.read_text())
  del source['components']['Chiller']['behavior']['parameters']['capacity_kw']
  path.write_text(json.dumps(source))
  execution = build(pipeline, targets=('hvac:system-schematic',), scope={'view': 'schematic'})
  assert execution.status == 'pass'  # Review draft generation is allowed.
  document = json.loads((pipeline / 'build/hvac/schematic/system.json').read_text())
  assert document['deliverable'] == 'system-diagram'
  assert document['behavior_state'] == 'missing'
  assert document['spatial_state'] == 'connectivity-only'
