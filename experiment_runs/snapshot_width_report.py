import json, sqlite3, sys
from pathlib import Path
root=Path(__file__).resolve().parent.parent
project=root/'完全版本/完全版本/openDriveMapEditor'
sys.path.insert(0,str(project))
from testfucs import test_data_store as store
db=project/'testfucs/agent_trace_viewer/request_tests.sqlite3'
c=sqlite3.connect(db.as_uri()+'?mode=ro',uri=True)
c.row_factory=sqlite3.Row
reviews=[dict(r) for r in c.execute('select * from case_reviews where run_id=?',('width_200_real_20261017',))]
ids=[r['case_id'] for r in reviews]
payload={'reviews':reviews,'cases':store.list_auto_review_baselines(c,ids),'source':{'workbook':{'sheets':{'requests':store.build_viewer_request_rows(c)}},'roadAlignments':store.list_case_road_alignments(c)}}
out=root/'experiment_runs/width_200/failure_analysis/report_snapshot.json'
out.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
print(out)
