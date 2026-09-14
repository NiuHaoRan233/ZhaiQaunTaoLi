"""Launch disjoint, resumable local partitions of the frozen historical plan."""
import argparse,json,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
p=argparse.ArgumentParser();p.add_argument('--part',type=int,required=True);p.add_argument('--parts',type=int,default=2)
a=p.parse_args()
assert 0<=a.part<a.parts
plan=json.loads((ROOT/'广义套利/reports/dadao_v1_followup_selection.json').read_text(encoding='utf-8'))
codes=plan['codes'][a.part::a.parts]
subprocess.run([sys.executable,'-X','utf8',str(ROOT/'scripts/probe_commodity_dadao.py'),
    '--output',str(ROOT/f'广义套利/reports/dadao_v1_validation_{a.part}'),
    '--dates',*plan['dates'],'--codes',*codes,'--download','--prefix-checks'],check=True,cwd=ROOT)
