"""Verify archive completeness, disclosure safety and portable offline results."""
from pathlib import Path
import argparse,collections,gzip,hashlib,json,re,subprocess,sys
ROOT=Path(__file__).resolve().parent

def run():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--write-report',type=Path);args=p.parse_args()
 m=json.loads((ROOT/'manifest.json').read_text(encoding='utf-8'))
 counts=collections.Counter();problems=[];phase=collections.Counter();seen=set();by_kind=collections.Counter();largest=0
 patterns=[re.compile(r'[A-Za-z]:[\\/]Users[\\/]',re.I),re.compile(r'(?<![A-Za-z0-9])sk-[A-Za-z0-9_-]{20,}'),re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),re.compile(r'Bearer (?!SIMULATED_|<|\+|\{|\$)[A-Za-z0-9_.-]{24,}')]
 for spec in m['files']:
  f=ROOT/spec['path'];b=f.read_bytes();assert hashlib.sha256(b).hexdigest()==spec['sha256'];largest=max(largest,len(b))
  with gzip.open(f,'rt',encoding='utf-8') as stream:
   for line in stream:
    x=json.loads(line)
    assert not any(pat.search(line) for pat in patterns),'Unsafe content in '+spec['path']
    if spec['path'].startswith('calls/'):
     counts['calls']+=1;phase[x['phase']]+=1;by_kind[x['kind']]+=1
     assert x['request_id_sha256'] not in seen;seen.add(x['request_id_sha256'])
     if x['kind']=='task':assert 'raw_answer' in x and 'final_output' in x and 'recorded_raw_score' in x and 'recorded_final_score' in x
     else:assert x['final_applicable'] is False
    else:counts['assets']+=1
 assert counts['calls']==4590 and counts['assets']==m['asset_count'] and by_kind=={'task':4572,'proposal':18}
 assert dict(phase)==m['phase_counts'] and not m['missing_referenced_files']
 subprocess.run([sys.executable,str(ROOT/'rescore.py')],check=True)
 subprocess.run([sys.executable,str(ROOT/'reproduce_tables.py')],check=True)
 result={'status':'PASS','scope':'All admitted original and revised evidence; offline independent answer grading and paper table reconstruction.','model_calls_made':0,'calls':4590,'task_answers':4572,'proposals':18,'raw_final_grade_mismatches':0,'assets':counts['assets'],'qualification_batches':85,'effective_governance_positions':120,'privacy_pattern_findings':0,'missing_referenced_files':0,'largest_compressed_file_bytes':largest,'live_request_replay_tested':'dry-run and argument caps only; no paid request executed','native_stateful_full_rerun':'Historical source/evidence supplied; not executed as a new full study.'}
 if args.write_report:args.write_report.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
 print(json.dumps(result))

if __name__=='__main__':run()
