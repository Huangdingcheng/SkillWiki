"""Replay selected original model requests into NEW outputs; dry-run is the default.
This replays model requests, not native release/recovery state transitions.
"""
from pathlib import Path
import argparse,gzip,json,os,time,urllib.request,urllib.error
ROOT=Path(__file__).resolve().parent

def run():
 p=argparse.ArgumentParser(description=__doc__)
 p.add_argument('--call-index',type=int,action='append',required=True,help='Recorded call_index; repeat this option to select more calls.')
 p.add_argument('--execute',action='store_true',help='Allow paid model calls; otherwise show the plan without credentials or network.')
 p.add_argument('--max-calls',type=int,default=1,help='Hard count cap, independent of the selected indices.')
 p.add_argument('--output',type=Path,help='Required for execution; must be a new directory.')
 p.add_argument('--model',help='Explicit model override for a new run; original model and payload remain recorded.')
 p.add_argument('--endpoint',default='https://api.deepseek.com/chat/completions')
 p.add_argument('--timeout',type=float,default=180)
 args=p.parse_args();wanted=set(args.call_index)
 if len(wanted)!=len(args.call_index):p.error('Duplicate call indices are not allowed.')
 if args.max_calls<1 or len(wanted)>args.max_calls:p.error('Selection exceeds --max-calls.')
 rows=[]
 for source in sorted((ROOT/'calls').glob('*.jsonl.gz')):
  with gzip.open(source,'rt',encoding='utf-8') as f:
   for line in f:
    row=json.loads(line)
    if row['call_index'] in wanted:rows.append(row)
 if len(rows)!=len(wanted):p.error('Unknown call index.')
 plan={'execute':args.execute,'selected_calls':len(rows),'max_calls':args.max_calls,'automatic_retries':0,'call_indices':[r['call_index'] for r in rows],'proposal_calls':sum(r['kind']!='task' for r in rows),'model':args.model or 'deepseek-flash','temperature':0,'thinking':'disabled','scope':'New model raw answers only; no historical output replacement or automatic publication.'}
 print(json.dumps(plan),flush=True)
 if not args.execute:return
 if not args.output:p.error('--output is required with --execute.')
 key=os.environ.get('DEEPSEEK_API_KEY')
 if not key:p.error('Set DEEPSEEK_API_KEY in the environment; it is never printed or written.')
 if not args.endpoint.startswith('https://'):p.error('Use an HTTPS provider endpoint.')
 args.output.mkdir(parents=True,exist_ok=False)
 (args.output/'run-plan.json').write_text(json.dumps(plan,indent=2)+'\n',encoding='utf-8')
 for row in rows:
  folder=args.output/f"call-{row['call_index']:04d}";folder.mkdir()
  wire=dict(row['request'])
  if args.model:wire['model']=args.model
  (folder/'request.json').write_text(json.dumps(wire,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
  request=urllib.request.Request(args.endpoint,data=json.dumps(wire,ensure_ascii=False).encode(),headers={'Authorization':'Bearer '+key,'Content-Type':'application/json'},method='POST')
  started=time.time()
  try:
   with urllib.request.urlopen(request,timeout=args.timeout) as response:body=json.load(response)
  except (urllib.error.URLError,TimeoutError) as exc:
   (folder/'failure.json').write_text(json.dumps({'type':type(exc).__name__,'status':'stopped_without_retry'})+'\n',encoding='utf-8');raise SystemExit('Provider call failed; stopped without retry. See failure.json.')
  (folder/'provider-response.json').write_text(json.dumps(body,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
  (folder/'raw-answer.txt').write_text(body['choices'][0]['message']['content'],encoding='utf-8')
  (folder/'identity.json').write_text(json.dumps({'historical_call_index':row['call_index'],'new_run_utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'elapsed_s':time.time()-started,'response_model':body.get('model'),'system_fingerprint':body.get('system_fingerprint'),'historical_scores_reused':False},indent=2)+'\n',encoding='utf-8')
 print('Completed new raw-answer replay. Native system processing and lifecycle operations are not executed by this script.')

if __name__=='__main__':run()
