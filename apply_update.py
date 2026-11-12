#!/usr/bin/env python3
"""Apply this exact ZIP's additions, replacements and removals to a local repo."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
from datetime import datetime

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None

def inside(root, name):
    candidate=(root/name).resolve()
    if not candidate.is_relative_to(root) or candidate==root:
        raise ValueError('Unsafe relative path: '+name)
    return candidate

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('repository',type=Path,help='Local drc_mobility repository folder')
    ap.add_argument('--apply',action='store_true',help='Apply after validation; otherwise preview only')
    args=ap.parse_args()
    bundle=Path(__file__).resolve().parent
    target=args.repository.resolve()
    if bundle==target or not (target/'index.html').is_file():
        raise SystemExit('Use a separate existing dashboard repository folder.')
    manifest=json.loads((bundle/'update_manifest.json').read_text(encoding='utf-8'))
    writes=[];deletes=[];conflicts=[]
    for entry in manifest['changed']:
        relative=entry['path'];source=inside(bundle,relative);dest=inside(target,relative)
        if digest(source)!=entry['new_sha256']:raise SystemExit('Damaged update file: '+relative)
        current=digest(dest)
        if current==entry['new_sha256']:continue
        if current!=entry['old_sha256'] or (dest.exists() and not dest.is_file()):
            conflicts.append(relative)
        else:writes.append((relative,source,dest))
    for entry in manifest['deleted']:
        relative=entry['path'];dest=inside(target,relative)
        if not dest.exists():continue
        if digest(dest)!=entry['old_sha256']:conflicts.append(relative)
        else:deletes.append((relative,dest))
    if conflicts:raise SystemExit('Stopped: local edits differ from supplied (18).zip:\n'+'\n'.join(conflicts))
    print(f'Validated: {len(writes)} writes and {len(deletes)} removals.')
    if not args.apply:
        print('Preview only. Add --apply to perform these changes. No files changed.')
        return
    if not writes and not deletes:
        print('Already applied.');return
    backup=target.parent/(target.name+'-backup-'+datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
    backup.mkdir()
    for relative,dest in [(x[0],x[2]) for x in writes]+deletes:
        if dest.exists():
            b=backup/relative;b.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(dest,b)
    for relative,source,dest in writes:
        dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,dest)
    for relative,dest in deletes:dest.unlink()
    print('Applied. Backup: '+str(backup))
    print('Review the local Git diff, then commit and push using your usual Git client.')

if __name__=='__main__':main()
