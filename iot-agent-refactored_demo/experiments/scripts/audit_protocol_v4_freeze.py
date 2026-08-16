from __future__ import annotations
import argparse, hashlib, json, subprocess
from pathlib import Path
REPO_ROOT=Path(__file__).resolve().parents[2]
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
 p=argparse.ArgumentParser(); p.add_argument('--manifest',type=Path,default=REPO_ROOT/'experiments/configs/protocol_v4_formal_freeze_manifest.json'); p.add_argument('--receipt',type=Path); p.add_argument('--output',type=Path,required=True); a=p.parse_args()
 m=json.loads(a.manifest.read_text()); rev=subprocess.check_output(['git','rev-parse','HEAD'],cwd=REPO_ROOT,text=True).strip(); issues=[]
 for row in m.get('files',[]):
  f=REPO_ROOT/row['path']
  if not f.exists(): issues.append('missing:'+row['path'])
  elif sha(f)!=row['sha256']: issues.append('hash_mismatch:'+row['path'])
 if a.receipt:
  receipt=json.loads(a.receipt.read_text())
  if receipt.get('git_revision')!=rev: issues.append('receipt_revision_mismatch')
  if receipt.get('source_manifest_sha256')!=sha(a.manifest): issues.append('receipt_manifest_hash_mismatch')
  for row in receipt.get('files',[]):
   f=REPO_ROOT/row['path']
   if not f.exists() or sha(f)!=row['sha256']: issues.append('receipt_hash_mismatch:'+row['path'])
 else:
  issues.append('post_commit_receipt_required')
 raw=subprocess.check_output(['git','status','--porcelain'],cwd=REPO_ROOT,text=True).splitlines()
 clean=not [line for line in raw if not line.endswith('.DS_Store')]
 if not clean: issues.append('working_tree_not_clean')
 result={'protocol':'v4','status':'pass' if not issues else 'fail','code_revision':rev,'manifest_revision':m.get('code_revision_at_generation'),'working_tree_clean':clean,'issues':issues}
 a.output.parent.mkdir(parents=True,exist_ok=True); a.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n'); print(a.output)
if __name__=='__main__': main()
