"""Frozen Petstore OpenAPI-3 request/response oracle. No HTTP execution."""
from __future__ import annotations
import json
import math
from pathlib import Path
from urllib.parse import urljoin
ROOT=Path(__file__).resolve().parents[1]
SOURCE_URL='https://petstore3.swagger.io/api/v3/openapi.json'
TASKS={'API-P':('/pet','post'),'API-G':('/pet/{petId}','get'),'API-D':('/pet/{petId}','delete')}
def operation_for(case,doc):
    oldroute,method=TASKS[case['task_id']]
    if oldroute in doc['paths'] and method in doc['paths'][oldroute]:return oldroute,method
    candidates=[(route,method) for route,ops in doc['paths'].items() if method in ops]
    if len(candidates)!=1:raise ValueError('AMBIGUOUS_OPERATION_SOURCE')
    return candidates[0]

def read_source():return json.loads((ROOT/'source/source.json').read_text(encoding='utf-8'))
def pointer(doc,ref):
    if not ref.startswith('#/'):raise ValueError('External reference requires explicit admission')
    result=doc
    for part in ref[2:].split('/'):result=result[part.replace('~1','/').replace('~0','~')]
    return result
def deref(schema,doc):return pointer(doc,schema['$ref']) if '$ref' in schema else schema
def issue(path,code):return {'path':path,'code':code}
def schema_issues(value,schema,doc,path):
    schema=deref(schema,doc)
    if value is None and schema.get('nullable'):return []
    typ=schema.get('type')
    numeric=type(value) in (int,float) and (type(value) is int or math.isfinite(value))
    valid={'object':isinstance(value,dict),'array':isinstance(value,list),'string':isinstance(value,str),'integer':numeric and int(value)==value,'number':numeric,'boolean':type(value) is bool}.get(typ,True)
    if not valid:return [issue(path,'type')]
    errors=[]
    if 'enum' in schema and value not in schema['enum']:errors.append(issue(path,'enum'))
    if typ=='integer' and schema.get('format') in ('int32','int64'):
        bits=int(schema['format'][3:])
        if not -(2**(bits-1))<=value<2**(bits-1):errors.append(issue(path,'format'))
    if typ=='object':
        for key in schema.get('required',[]):
            if key not in value:errors.append(issue(path+'/'+key,'required'))
        for key,val in value.items():
            if key in schema.get('properties',{}):errors.extend(schema_issues(val,schema['properties'][key],doc,path+'/'+key))
            elif schema.get('additionalProperties') is False:errors.append(issue(path+'/'+key,'additional'))
    elif typ=='array':
        for i,val in enumerate(value):errors.extend(schema_issues(val,schema.get('items',{}),doc,path+'/'+str(i)))
    return errors
def auth_options(case,operation):
    auth=case['input'].get('auth',{});options=[]
    for scheme in operation.get('security',[]):
        headers={};ok=True
        for key,scopes in scheme.items():
            if key=='api_key':
                token=auth.get('api_key');ok=ok and isinstance(token,str) and bool(token)
                if token:headers['api_key']=token
            elif key=='petstore_auth':
                token=auth.get('access_token');supplied=auth.get('scopes',[])
                ok=ok and isinstance(token,str) and bool(token) and isinstance(supplied,list) and set(scopes).issubset(set(supplied))
                if token:headers['authorization']='Bearer '+token
            else:raise ValueError('Unadmitted security scheme: '+key)
        if ok:options.append(headers)
    return options
def expected(case,doc=None):
    doc=doc or read_source();route,method=operation_for(case,doc);op=doc['paths'][route][method];x=case['input']
    answer={'case_id':case['case_id'],'decision':'send','issues':[],'request':None,'response':None}
    if case.get('kind','request')=='response':
        status=x['status'];key=str(status) if str(status) in op['responses'] else 'default';spec=op['responses'].get(key)
        if spec is None:raise ValueError('Undocumented response without default')
        valid=None;classification='unexpected' if key=='default' else ('success' if 200<=status<300 else 'error')
        if 'content' in spec:
            media=x.get('content_type','application/json')
            if media!='application/json':raise ValueError('Only JSON response media admitted in WP01')
            valid=media in spec['content'] and not schema_issues(x.get('body'),spec['content'][media]['schema'],doc,'/body')
            if not valid:classification='response_invalid'
        answer.update(decision='report',response={'status':status,'matched_response':key,'classification':classification,'body_schema_valid':valid});return answer
    errors=[];opts=auth_options(case,op)
    if not opts:errors.append(issue('/auth','authentication'))
    for param in op.get('parameters',[]):
        location,name=param['in'],param['name'];values=x.get('path' if location=='path' else 'headers',{})
        if param.get('required') and name not in values:errors.append(issue('/'+location+'/'+name,'required'))
        elif name in values:errors.extend(schema_issues(values[name],param.get('schema',{}),doc,'/'+location+'/'+name))
    if 'requestBody' in op:
        body_spec=deref(op['requestBody'],doc)
        if 'body' not in x:
            if body_spec.get('required',False):errors.append(issue('/body','required'))
        else:errors.extend(schema_issues(x['body'],body_spec['content']['application/json']['schema'],doc,'/body'))
    if errors:
        answer.update(decision='reject',issues=sorted(errors,key=lambda a:(a['path'],a['code'])));return answer
    path=route
    for key,value in x.get('path',{}).items():path=path.replace('{'+key+'}',str(int(value)))
    base=urljoin(SOURCE_URL,doc['servers'][0]['url']).rstrip('/');headers=dict(opts[0])
    if method=='post':headers['content-type']='application/json'
    if method=='delete' and 'api_key' in x.get('headers',{}):headers['api_key']=x['headers']['api_key']
    answer['request']={'method':method.upper(),'url':base+path,'headers':headers,'body':x.get('body') if method=='post' else None};return answer
def strict_load(raw):
    def pairs(items):
        result={}
        for key,value in items:
            if key in result:raise ValueError('Duplicate JSON key')
            result[key]=value
        return result
    def nonfinite(value):raise ValueError('Non-finite JSON value')
    obj=json.loads(raw,object_pairs_hook=pairs,parse_constant=nonfinite)
    if not isinstance(obj,dict):raise ValueError('Top-level JSON object required')
    return obj
def equal_json(a,b):
    if type(a) is bool or type(b) is bool:return type(a) is type(b) and a==b
    if isinstance(a,dict) and isinstance(b,dict):return set(a)==set(b) and all(equal_json(a[k],b[k]) for k in a)
    if isinstance(a,list) and isinstance(b,list):return len(a)==len(b) and all(equal_json(x,y) for x,y in zip(a,b))
    if type(a) in (int,float) and type(b) in (int,float):return a==b
    return type(a) is type(b) and a==b
def strict_grade(case,raw,*,execution_status='success',enabled=True,doc=None):
    result={'case_id':case['case_id'],'execution_status':execution_status,'semantic_status':'NOT_EVALUATED','format_ok':None,'reasons':[]}
    if execution_status!='success':result['reasons']=['execution_or_environment_failure'];return result
    if not enabled:result['reasons']=['no_evaluation_contract'];return result
    try:obj=strict_load(raw)
    except (ValueError,TypeError):result.update(semantic_status='FAIL_FORMAT',format_ok=False,reasons=['complete_plain_json_required']);return result
    if set(obj)!={'case_id','decision','issues','request','response'} or not isinstance(obj['issues'],list):
        result.update(semantic_status='FAIL_FORMAT',format_ok=False,reasons=['output_contract']);return result
    if any(not isinstance(e,dict) or set(e)!={'path','code'} or not all(isinstance(v,str) for v in e.values()) for e in obj['issues']):
        result.update(semantic_status='FAIL_FORMAT',format_ok=False,reasons=['issue_contract']);return result
    result['format_ok']=True;want=expected(case,doc);reasons=[]
    if obj['case_id']!=want['case_id']:reasons.append('case_identity')
    if obj['decision']!=want['decision']:reasons.append('decision')
    if {(a['path'],a['code']) for a in obj['issues']}!={(a['path'],a['code']) for a in want['issues']}:reasons.append('issues')
    if len(obj['issues'])!=len({(a['path'],a['code']) for a in obj['issues']}):reasons.append('duplicate_issue')
    if not equal_json(obj['response'],want['response']):reasons.append('response')
    req=obj['request'];target=want['request']
    if target is None:
        if req is not None:reasons.append('unexpected_request')
    elif not isinstance(req,dict) or set(req)!={'method','url','headers','body'}:reasons.append('request_contract')
    else:
        for field in ['method','url','body']:
            if not equal_json(req[field],target[field]):reasons.append('request_'+field)
        headers=req['headers']
        if not isinstance(headers,dict) or any(not isinstance(k,str) or not isinstance(v,str) for k,v in headers.items()):reasons.append('headers_contract')
        else:
            actual={k.lower():v for k,v in headers.items()}
            if len(actual)!=len(headers):reasons.append('duplicate_header')
            source=doc or read_source();route,method=operation_for(case,source);options=auth_options(case,source['paths'][route][method]);allowed=[]
            for auth in options:
                candidate=dict(auth)
                if method=='post':candidate['content-type']='application/json'
                if method=='delete' and 'api_key' in case['input'].get('headers',{}):candidate['api_key']=case['input']['headers']['api_key']
                allowed.append(candidate)
            if not any(all(actual.get(k)==v for k,v in option.items()) for option in allowed):reasons.append('request_headers')
    result.update(semantic_status='PASS' if not reasons else 'FAIL_SEMANTIC',reasons=reasons);return result


RULE_VERSION='final-expansion-primary-r1-20261108'
def grade(case, raw, *, execution_status='success', enabled=True, doc=None, service_base_url='https://petstore3.swagger.io/api/v3'):
    """Preserve admitted Primary URL and supplied-response issue alternatives."""
    from urllib.parse import urlsplit
    source=doc if doc is not None else read_source()
    want=expected(case,source);normalized=raw;representation='not_matched'
    if execution_status=='success' and enabled:
        try:
            obj=strict_load(raw)
            if isinstance(obj.get('request'),dict) and want['request'] is not None:
                absolute=want['request']['url'];parts=urlsplit(absolute)
                relative=parts.path+('?' + parts.query if parts.query else '')
                if obj['request'].get('url') in [absolute,relative]:
                    representation='absolute' if obj['request']['url']==absolute else 'origin_relative'
                    obj['request']['url']=absolute
            if case.get('kind')=='response':
                route,method=operation_for(case,source);x=case['input'];responses=source['paths'][route][method]['responses']
                spec=responses.get(str(x['status']),responses.get('default',{}))
                schema=spec.get('content',{}).get(x.get('content_type','application/json'),{}).get('schema')
                errors=schema_issues(x.get('body'),schema,source,'/body') if schema is not None else []
                issues=obj.get('issues')
                if isinstance(issues,list) and all(isinstance(i,dict) and set(i)=={'path','code'} and all(isinstance(v,str) for v in i.values()) for i in issues):
                    keys=[(i['path'],i['code']) for i in issues]
                    if len(keys)==len(set(keys)) and (not keys or set(keys)=={(i['path'],i['code']) for i in errors}):obj['issues']=[]
            normalized=json.dumps(obj,ensure_ascii=False,allow_nan=False)
        except (ValueError,TypeError):pass
    result=strict_grade(case,normalized,execution_status=execution_status,enabled=enabled,doc=source)
    return {**result,'rule_version':RULE_VERSION,'url_representation':representation}
