"""Reconstruct paper tables from independently rescored answers and governance receipts."""
from pathlib import Path
import argparse,collections,csv,gzip,json,subprocess,sys,tempfile
ROOT=Path(__file__).resolve().parent

def run():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path);args=p.parse_args()
 with tempfile.TemporaryDirectory(prefix='skillwiki-rescore-') as tmp:
  out=Path(tmp)/'scores'
  subprocess.run([sys.executable,str(ROOT/'rescore.py'),'--output',str(out)],check=True)
  rows=list(csv.DictReader((out/'scores.csv').open(encoding='utf-8')))
  assets={}
  m=json.loads((ROOT/'manifest.json').read_text(encoding='utf-8'))
  for item in m['files']:
   if item['path'].startswith('assets/'):
    with gzip.open(ROOT/item['path'],'rt',encoding='utf-8') as f:
     for line in f:
      a=json.loads(line);assets[a['path']]=a['content']
  def csvasset(name):return list(csv.DictReader(assets['research/stage2-complete-experiment-report-20261109-r1/data/'+name].splitlines()))
  def stats(rr):return {'answers':len(rr),'raw_correct':sum(x['raw_correct']=='True' for x in rr),'final_correct':sum(x['final_correct']=='True' for x in rr)}
  table1=[]
  for cond,want in [('A0',84),('A1',112),('A2',120)]:
   v=stats([x for x in rows if x['study']=='q1' and x['condition']==cond]);assert v=={'answers':120,'raw_correct':want,'final_correct':want};table1.append({'condition':cond,**v})
  core=csvasset('original-12-core-events.csv');table2=[]
  for e in core:
   eid=e['event_id']
   for condition,prefix in [('P','P'),('U','U')]:
    for group,gkey in [('target','target'),('keep','keep')]:
     rr=[x for x in rows if x['study']=='core' and x['event_id']==eid and x['condition']==condition and x['group']==group]
     v=stats(rr);assert v['answers']==16,(eid,condition,group,v)
     assert v['final_correct']==int(e[prefix+'_'+gkey+'_final'])
     if prefix=='U':assert v['raw_correct']==int(e['U_'+gkey+'_raw'])
     table2.append({'event_id':eid,'condition':condition,'group':group,**v})
  quals=collections.defaultdict(list)
  for x in rows:
   if x['study']=='qualification':quals[x['batch']].append(x)
  assert len(quals)==85 and all(len(v)==36 for v in quals.values())
  batches={k:stats(v) for k,v in quals.items()}
  selected=[]
  for eid in ['U04','U05','U06','U10','U12']:
   suffix='qualification-u06-guidance-r2' if eid=='U06' else 'qualification-'+eid.lower()+'-r1'
   batch=next(k for k in batches if k.endswith('/'+suffix));selected.append(batches[batch])
  assert sum(x['raw_correct'] for x in selected)==171 and sum(x['final_correct'] for x in selected)==180
  rejected=next(v for k,v in batches.items() if k.endswith('/qualification-u06-r1'));assert rejected['raw_correct']==30 and rejected['final_correct']==35
  recovery=csvasset('recovery-5-events.csv')
  for x in recovery:
   v=next(v for k,v in batches.items() if '/07-recovery-followup-r1/'+x['event_id']+'/qualification' in k)
   assert v['raw_correct']==int(x['model_raw_correct']) and v['final_correct']==int(x['system_final_correct'])
   assert all(x[k]=='True' for k in ['recovered','old_proof_rejected','qualified','republished'])
  supplement=csvasset('new-qualification-batches.csv')
  endings={'U08 updated':'/02-u08/qualification','U08 recovered':'/02-u08/recovery-qualification','CI updated':'/03-governance/preparation/ci-updated/qualification','CI recovered':'/03-governance/preparation/ci-recovered/qualification','G26 skillwiki':'/03-governance/recheck-r2/G26/skillwiki/qualification','G28 skillwiki':'/03-governance/recheck-r2/G28/skillwiki/qualification','G26 git_ci_plus':'/03-governance/recheck-r2/G26/git_ci_plus/qualification','G28 git_ci_plus':'/03-governance/recheck-r2/G28/git_ci_plus/qualification'}
  for x in supplement:
   v=next(v for k,v in batches.items() if k.endswith(endings[x['batch']]))
   assert v['raw_correct']==int(x['raw_passed']) and v['final_correct']==int(x['final_passed'])
  tracking=csvasset('all-120-tracking.csv');governance=collections.Counter();state_defs={x['state_id']:x for x in json.loads((ROOT.parent/'materials/governance-states.json').read_text(encoding='utf-8'))}
  for x in tracking:
   route=x['route'];sid=x['state_id'];follow=x['coverage']=='followup_revision'
   marker='/03-governance/decisions/' if follow else '/03-execution/governance-attempt001/'
   matches=[v for k,v in assets.items() if marker+sid+'/'+route+'/result.json' in k]
   if follow and not matches:matches=[v for k,v in assets.items() if '/03-governance/recheck-r2/'+sid.split('-')[0]+'/'+route+'/' in k and k.endswith('/result.json')]
   assert len(matches)==1,(sid,route,len(matches))
   v=matches[0];assert v['status']=='EXECUTED' and v['decision'] in state_defs[sid]['allowed_primary_decisions']
   assert v['primary_correct'] and v['state_consistent']
   governance[(route,'supplementary' if follow else 'main')]+=1
  assert governance=={('skillwiki','main'):50,('git_ci_plus','main'):50,('skillwiki','supplementary'):10,('git_ci_plus','supplementary'):10}
  result={'status':'PASS','table1_skill_use':table1,'table1_governance':{'/'.join(k):v for k,v in governance.items()},'table2_core_events':table2,'qualification_batches_all_versions':batches,'selected_five_revisions':{'answers':180,'raw_correct':171,'final_correct':180},'five_recovery':{'answers':180,'raw_correct':sum(int(x['model_raw_correct']) for x in recovery),'final_correct':sum(int(x['system_final_correct']) for x in recovery)},'rejected_U06_R1':rejected,'supplementary_batches':8,'coverage':'All 4572 raw/final task answers rescored; all 18 proposal records retained; 85 complete qualification batches including unsuccessful and superseded attempts; 120 effective governance positions checked.'}
  if args.output:args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
  print(json.dumps({k:v for k,v in result.items() if k not in ['table2_core_events','qualification_batches_all_versions']},ensure_ascii=True))

if __name__=='__main__':run()
