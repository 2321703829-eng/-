"""Check complete map and scene diffs, allowing only width edit effects."""
import collections
import json
import xml.etree.ElementTree as ET
from datetime import datetime
from run_real_batches import BASE, write_json

def canonical(node):
    attributes=tuple(sorted((k,v) for k,v in node.attrib.items() if not (node.tag=='width' and k in ('a','b','c','d'))))
    return node.tag,attributes,(node.text or '').strip(),tuple(canonical(c) for c in node)

def differences(a,b,path=''):
    if isinstance(a,dict) and isinstance(b,dict):
        for k in a.keys()|b.keys():
            if k not in a or k not in b:yield path+'/'+k,'structure'
            else:yield from differences(a[k],b[k],path+'/'+k)
    elif isinstance(a,list) and isinstance(b,list):
        if len(a)!=len(b):yield path,'length'
        for aa,bb in zip(a,b):yield from differences(aa,bb,path+'/*')
    elif a!=b:yield path,'value'

results=[]
for group in ('width_200','single_width_200'):
    for folder in sorted((BASE/group/'cases').iterdir()):
        record={'group':group,'case_id':folder.name}
        before=ET.parse(folder/'before.xodr').getroot();after=ET.parse(folder/'after.xodr').getroot()
        record['only_width_coefficients_changed']=canonical(before)==canonical(after)
        scenes=[json.loads((folder/(s+'.json')).read_text(encoding='utf-8')) for s in ('before','after')]
        diffs=list(differences(*scenes))
        record['scene_change_count']=len(diffs)
        record['scene_changed_paths']=dict(collections.Counter(path for path,kind in diffs))
        record['unexpected_scene_changes']=[{'path':path,'kind':kind} for path,kind in diffs
            if not (path.startswith('/agents/') and path.endswith(('/position/x','/position/z')) and kind=='value')]
        results.append(record)
summary={'checked':len(results),'non_width_map_changes':[r for r in results if not r['only_width_coefficients_changed']],
         'unexpected_scene_changes':[r for r in results if r['unexpected_scene_changes']],
         'unchanged_scenes':[r for r in results if not r['scene_change_count']]}
write_json(BASE/'audit_current_400/map_pair_integrity.json',{'created_at':datetime.now().isoformat(),'summary':summary,'cases':results})
print(json.dumps(summary,ensure_ascii=False))
