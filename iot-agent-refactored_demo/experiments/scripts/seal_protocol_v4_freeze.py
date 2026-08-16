from __future__ import annotations
import argparse,hashlib,json,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
 p=argparse.ArgumentParser(description='Create post-commit protocol-v4 freeze receipt without changing tracked assets.'); p.add_argument('--manifest',type=Path,default=ROOT/'experiments/configs/protocol_v4_formal_freeze_manifest.json'); p.add_argument('--output',type=Path,required=True); a=p.parse_args()
 manifest=json.loads(a.manifest.read_text()); rev=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(); files=[]
 for row in manifest['files']:
  f=ROOT/row['path']; files.append({'path':row['path'],'sha256':sha(f),'exists':f.exists()})
 receipt={'protocol':'v4','receipt_type':'post_commit_freeze','git_revision':rev,'source_manifest_sha256':sha(a.manifest),'files':files,'rebuild_command':'PYTHONDONTWRITEBYTECODE=1 python3 experiments/scripts/seal_protocol_v4_freeze.py --output <results-root>/reports/<run-id>/freeze_receipt.json'}
 a.output.parent.mkdir(parents=True,exist_ok=True); a.output.write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n'); print(a.output)
if __name__=='__main__': main()
