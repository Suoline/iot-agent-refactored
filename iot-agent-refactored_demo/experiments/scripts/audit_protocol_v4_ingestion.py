from __future__ import annotations
import argparse,json,sys,tempfile
from datetime import timedelta
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from experiments.memory.service import MemoryService
from experiments.memory.text_ingestion import ingest_user_text
from experiments.world_model.ha_oracle import HAOracle
def main():
 p=argparse.ArgumentParser(); p.add_argument('--output',type=Path,required=True); a=p.parse_args(); world=HAOracle(); db=a.output.parent/'ingestion_audit.sqlite3'
 if db.exists(): db.unlink()
 service=MemoryService(db); first=ingest_user_text(service,text='我喜欢把卧室空调设为24度',now=world.current_time,turn_id='create'); second=ingest_user_text(service,text='不对，改成卧室空调26度',now=world.current_time+timedelta(days=1),turn_id='correct'); negated=ingest_user_text(service,text='我不喜欢把卧室空调设为26度',now=world.current_time+timedelta(days=2),turn_id='negated'); ambiguous=ingest_user_text(service,text='我喜欢把空调设为26度',now=world.current_time+timedelta(days=2),turn_id='ambiguous')
 records=service.list_records(include_deleted=True); latest=[r for r in records if r.status=='active']
 checks={'create_accepted':first['accepted'],'correction_supersedes':second['replaced_memory_id']==first['memory_id'],'latest_value_is_26':len(latest)==1 and latest[0].object=='26','negation_rejected':not negated['accepted'],'room_ambiguity_rejected':not ambiguous['accepted']}
 report={'protocol':'v4','input_mode':'raw_user_text','structured_runtime_input_used':False,'results':{'create':first,'correction':second,'negation':negated,'ambiguous_room':ambiguous},'checks':checks,'status':'pass' if all(checks.values()) else 'fail','scope_note':'Rule-based ingestion supports explicit temperature creation/correction only; negated and room-ambiguous utterances are deliberately rejected rather than inferred.'}
 a.output.parent.mkdir(parents=True,exist_ok=True); a.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n'); print(a.output)
if __name__=='__main__': main()
