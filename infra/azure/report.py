"""Combine evidence and fail the deployment for failed or missing mandatory probes."""
import json
from pathlib import Path
import sys


def collect(state):
    results=[]
    for name in ('public','internal','agent','restart','blob','isolation'):
        path=state/(name+'-report.json')
        if not path.exists():
            results.append(dict(check=name,status='FAIL',evidence='Required verification report is missing'))
        else:
            try:
                data = json.loads(path.read_text())
                if not isinstance(data, list) or not data or any(
                    not isinstance(row, dict) or row.get('status') not in ('PASS','FAIL','NOT TESTED') or
                    not isinstance(row.get('check'), str) or not isinstance(row.get('evidence'), str)
                    for row in data):
                    raise ValueError('Invalid report schema')
                results.extend(data)
            except (ValueError,TypeError):
                results.append(dict(check=name,status='FAIL',evidence='Invalid verification report'))
    return results


if __name__=='__main__':
    output=Path(sys.argv[1])
    results=collect(output.parent)
    output.write_text(json.dumps(results,indent=2))
    for result in results:
        print(result['status']+': '+result['check']+' — '+result['evidence'])
    raise SystemExit(1 if any(r['status']=='FAIL' for r in results) else 0)
