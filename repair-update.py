import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('action',choices=['verify','prepare'])
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--package',type=Path,required=True)
    args=parser.parse_args()
    root=args.root.resolve();source=Path(__file__).resolve().parent
    for folder in ('vendor-public','vendor','vendor-presence-v1'):
        sys.path.insert(0,str(root/folder))
    spec=importlib.util.spec_from_file_location('threadle_recovery_driver',source/'admin_updates.py')
    driver=importlib.util.module_from_spec(spec);spec.loader.exec_module(driver)
    release,files=driver.verify_package(args.package.read_bytes(),public_key='129c6ce9db5362ecd60e05dc1f608fc25a367cf407ed2f97ea9a69ee0de05dd4')
    for name,content in files.items():
        path=source/name
        if not path.resolve().is_relative_to(source) or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=hashlib.sha256(content).hexdigest():
            raise ValueError('Extracted file does not match the signed package: '+name)
    if args.action=='verify':
        print('Signed cumulative package and extracted files verified: '+release['version'])
        return
    updates=driver.Updates(root)
    # The PowerShell installer calls prepare only after the update task is idle and Receiver is stopped.
    with updates.db() as db:
        db.execute('BEGIN IMMEDIATE')
        db.execute("UPDATE updates SET state='failed',detail='Superseded by local recovery. Existing backup and logs preserved.',changed=? WHERE state IN ('staged','scheduled','applying','restarting')",(__import__('time').time(),))
    identity,_=updates.stage(args.package.read_bytes(),'Local administrator recovery')
    print(identity)


if __name__=='__main__':
    main()
