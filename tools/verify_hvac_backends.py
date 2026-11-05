"""Compare actual SciPy and OpenModelica executions of the same input snapshots."""

import argparse
import json
from pathlib import Path

import numpy as np

from texsys.api import evaluate
from texsys.evidence import write_json


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('--project', type=Path, default=Path('examples/hvac-dynamic-station'))
  args = parser.parse_args()
  root = args.project.resolve()
  comparisons = []
  for name, expected in (('load-step', 'pass'), ('pump-speed', 'pass'), ('pump-trip', 'fail')):
    outputs = {}
    for backend in ('scipy', 'openmodelica'):
      execution = evaluate(root, scenario=f'assets/scenarios/{name}.json', backend=backend)
      outputs[backend] = json.loads(execution.report.read_text())
    a, b = outputs['scipy'], outputs['openmodelica']
    if 'variables' not in a or 'variables' not in b:
      comparisons.append({'scenario': name, 'verification': 'fail', 'results': outputs})
      continue
    differences = {}
    for key, variable in a['variables'].items():
      if variable['role'] not in {'fluid-temperature', 'electric-power', 'mass-flow'}:
        continue
      reference = np.interp(a['time_s'], b['time_s'], b['variables'][key]['values'])
      difference = float(np.max(np.abs(reference - np.array(variable['values']))))
      tolerance = {'degC': .002, 'kg/s': 1e-5, 'W': 2.0}[variable['unit']]
      differences[key] = {'maximum_difference': difference, 'unit': variable['unit'], 'tolerance': tolerance, 'status': 'pass' if difference <= tolerance else 'fail'}
    verified = a['model_digest'] == b['model_digest'] and a['scenario_digest'] == b['scenario_digest'] and a['status'] == b['status'] == expected and all(v['status'] == 'pass' for v in differences.values())
    comparisons.append({'scenario': name, 'verification': 'pass' if verified else 'fail', 'engineering_status': a['status'], 'expected': expected,
                        'model_digest': a['model_digest'], 'scenario_digest': a['scenario_digest'],
                        'backends': {k: {'backend': v['backend'], 'checks': v['checks'], 'initialization': v['initialization'], 'external_audit': v.get('external_audit')} for k,v in outputs.items()}, 'differences': differences})
  report = {'verification': 'pass' if all(c['verification'] == 'pass' for c in comparisons) else 'fail',
            'note': 'Verification compares implementations of declared concept equations. It does not validate manufacturer performance or approve the pump-trip operating condition.',
            'comparisons': comparisons}
  output = root / 'build/evidence/backend-comparison.json'
  write_json(output, report)
  print(report['verification'], output)
  return 0 if report['verification'] == 'pass' else 1


if __name__ == '__main__':
  raise SystemExit(main())
