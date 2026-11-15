"""Independently rescore all archived task answers, without network or product writes."""
from pathlib import Path
import argparse,collections,csv,gzip,hashlib,importlib.util,json,sys
ROOT=Path(__file__).resolve().parent
sys.dont_write_bytecode=True

def read(path):return json.loads(Path(path).read_text(encoding='utf-8-sig'))
def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def lines(path):
 with gzip.open(path,'rt',encoding='utf-8') as stream:
  for line in stream:yield json.loads(line)
def module(name,path):
 spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);sys.modules[name]=m;spec.loader.exec_module(m);return m

def run():
 parser=argparse.ArgumentParser(description=__doc__)
 parser.add_argument('--output',type=Path,help='Optional new directory for independently derived scores and summary.')
 args=parser.parse_args()
 manifest=read(ROOT/'manifest.json')
 for name,expected in manifest.get('code_hashes',{}).items():
  assert digest(ROOT/name)==expected,name
 with (ROOT.parent/'data/call-index.csv').open(encoding='utf-8-sig') as f:
  ledger={int(x['call_index']):x for x in csv.DictReader(f)}
 for item in manifest['files']:
  assert digest(ROOT/item['path'])==item['sha256'],item['path']
 assets={}
 for item in manifest['files']:
  if item['path'].startswith('assets/'):
   for row in lines(ROOT/item['path']):
    assert row['path'] not in assets
    assets[row['path']]=row
 assert len(assets)==manifest['asset_count']
 def asset(name):return assets[name.removeprefix('archive://')]['content']
 q1dir=ROOT/'scorers/research/stage2-runtime-repair-20261030'
 sys.path.insert(0,str(q1dir))
 import evidence_tools
 import url_contract_v3
 expansion=module('expansion_oracle',ROOT/'scorers/research/stage2-final-expansion-execution-20261108-r1/01-materials/revision-r2/scorers/revision-r1/oracle.py')
 ids=set();totals=collections.Counter();batches=collections.defaultdict(collections.Counter);records=[];proposals=[];mismatch=[]
 for item in manifest['files']:
  if not item['path'].startswith('calls/'):continue
  for row in lines(ROOT/item['path']):
   rid=row['request_id_sha256'];assert rid not in ids;ids.add(rid)
   public=ledger[row['call_index']]
   assert rid==public['request_id_sha256'] and row['receipt_original_sha256']==public['receipt_sha256']
   assert row['kind']==public['kind']
   totals['calls']+=1
   wire=row['request']
   assert wire['model']=='deepseek-flash' and wire['temperature']==0
   assert wire['thinking']=={'type':'disabled'} and wire['stream'] is False
   assert row['raw_answer']==row['provider_response']['choices'][0]['message']['content']
   if not row['score_applicable']:
    assert row['kind']!='task' and not row['final_applicable'] and wire['max_tokens']==4096
    totals['proposals']+=1;proposals.append({'call_index':row['call_index'],'phase':row['phase'],'raw_present':True,'parsed_present':'parsed-proposal' in row});continue
   assert row['kind']=='task' and row['final_applicable'] and wire['max_tokens']==2048
   totals['task_answers']+=1
   case=row['case'];doc=asset(row['source']);raw=row['raw_answer'];final=row['final_output'];final_raw=final if isinstance(final,str) else json.dumps(final,ensure_ascii=False,default=str)
   if row['scorer']=='q1':
    target=evidence_tools.oracle.expected(case,doc)
    def grade(text):return url_contract_v3.grade(case,text,service_base_url='https://petstore3.swagger.io/api/v3',expected=target,grader=lambda current,response,**kw:evidence_tools.grade(current,response,doc=doc,**kw))
   else:
    def grade(text):return expansion.grade(case,text,doc=doc)
   a,b=grade(raw),grade(final_raw)
   fields=['semantic_status','format_ok','reasons','rule_version','url_representation']
   for label,actual,saved in [('raw',a,row['recorded_raw_score']),('final',b,row['recorded_final_score'])]:
    for field in fields:
     if actual.get(field)!=saved.get(field):mismatch.append({'call_index':row['call_index'],'field':label+'.'+field,'actual':actual.get(field),'saved':saved.get(field)})
   key=(row['study'],row.get('batch',''),row.get('condition',''),row.get('event_id',''),row.get('group',''))
   batches[key]['answers']+=1;batches[key]['raw_correct']+=a['semantic_status']=='PASS';batches[key]['final_correct']+=b['semantic_status']=='PASS'
   records.append({'call_index':row['call_index'],'study':row['study'],'phase':row['phase'],'batch':row.get('batch',''),'condition':row.get('condition',''),'event_id':row.get('event_id',''),'group':row.get('group',''),'case_id':row['case_id'],'raw_correct':a['semantic_status']=='PASS','final_correct':b['semantic_status']=='PASS'})
 assert len(ledger)==4590 and totals=={'calls':4590,'task_answers':4572,'proposals':18},dict(totals)
 assert not mismatch,json.dumps(mismatch[:10])
 result={'status':'PASS','network_calls':0,**dict(totals),'independently_rescored_answers':len(records),'raw_and_final_grade_mismatches':len(mismatch),'assets_verified':len(assets),'batches':[{'study':k[0],'batch':k[1],'condition':k[2],'event_id':k[3],'group':k[4],**dict(v)} for k,v in sorted(batches.items())]}
 q1=[x for x in result['batches'] if x['study']=='q1'];assert {x['condition']:x['raw_correct'] for x in q1}=={'A0':84,'A1':112,'A2':120}
 # Every record is retained, including failed first revisions and non-selected qualification batches.
 if args.output:
  args.output.mkdir(parents=True,exist_ok=False)
  (args.output/'summary.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
  with (args.output/'scores.csv').open('w',encoding='utf-8',newline='') as f:
   writer=csv.DictWriter(f,fieldnames=list(records[0]));writer.writeheader();writer.writerows(records)
  (args.output/'proposals.json').write_text(json.dumps(proposals,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
 print(json.dumps({k:v for k,v in result.items() if k!='batches'},ensure_ascii=True))

if __name__=='__main__':run()
