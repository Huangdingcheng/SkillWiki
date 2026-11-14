"""Extract sanitized evidence, preserving logical archive paths and exported hashes."""
from pathlib import Path
import argparse,gzip,hashlib,json,os
ROOT=Path(__file__).resolve().parent

def run():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('output',type=Path);args=p.parse_args()
 args.output=args.output.resolve()
 prefix=chr(92)*2+chr(63)+chr(92)
 if os.name=='nt' and not str(args.output).startswith(prefix):
  args.output=Path(prefix+str(args.output))
 args.output.mkdir(parents=True,exist_ok=False);manifest=json.loads((ROOT/'manifest.json').read_text(encoding='utf-8'));mapping=[]
 for batch in manifest['files']:
  source=ROOT/batch['path'];assert hashlib.sha256(source.read_bytes()).hexdigest()==batch['sha256']
  if not batch['path'].startswith('assets/'):continue
  with gzip.open(source,'rt',encoding='utf-8') as f:
   for line in f:
    row=json.loads(line);relative=Path(row['path']);assert not relative.is_absolute() and '..' not in relative.parts
    dest=args.output/relative;dest.parent.mkdir(parents=True,exist_ok=True)
    value=row['content'];text=json.dumps(value,ensure_ascii=False,indent=2)+'\n' if row['format']=='json' else value
    dest.write_text(text,encoding='utf-8');mapping.append({'path':row['path'],'original_sha256':row['original_sha256'],'export_sha256':hashlib.sha256(dest.read_bytes()).hexdigest()})
 (args.output/'extraction-map.json').write_text(json.dumps(mapping,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
 print(json.dumps({'assets_extracted':len(mapping),'output':str(args.output),'note':'Local paths and receipt IDs are sanitized. Original digests identify the source; export digests identify extracted bytes.'}))

if __name__=='__main__':run()
