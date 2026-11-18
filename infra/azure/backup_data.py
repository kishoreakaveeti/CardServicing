"""Offline consistent backups of every service-owned volume and identity material.

Archives are encrypted to the operator's age recipient before leaving this VM.
All writers stop for the snapshot. Restore requires client-side explicit consent
and creates an encrypted safety backup before replacing any persistent state.
"""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from uuid import uuid4

ROOT = Path('/opt/cardservicing')
APP = ROOT/'app'
STATE = ROOT/'state'
BACKUPS = ROOT/'backups'
HELPER = 'alpine:3.22'


def run(*args, capture=False):
    result = subprocess.run(list(map(str,args)),check=True,text=True,
                            stdout=subprocess.PIPE if capture else None)
    return result.stdout.strip() if capture else None


def compose(edge=False):
    return ['docker','compose','--env-file',str(STATE/'.env.azure'),'-f',
            str(APP/('infra/azure/compose.proxy.yaml' if edge else 'compose.azure.yaml'))]


def volume_names():
    names = []
    for edge in (False,True):
        cfg = json.loads(run(*compose(edge),'config','--format','json',capture=True))
        names += [v['name'] for v in cfg['volumes'].values()]
    if len(names) != len(set(names)) or any(not re.fullmatch(r'card-servicing-azure-[a-z-]+',n) for n in names):
        raise RuntimeError('Unexpected or shared volume names; refusing backup/restore')
    return sorted(names)


def stop():
    run(*compose(True),'stop','--timeout','30')
    run(*compose(),'stop','--timeout','60')


def start():
    run(*compose(),'up','-d','--wait','--wait-timeout','600')
    run(*compose(True),'up','-d','--wait','--wait-timeout','120')


def backup(recipient):
    if not re.fullmatch(r'age1[0-9a-z]{58}',recipient):
        raise ValueError('Invalid age recipient')
    names = volume_names()
    name = 'backup-' + datetime.now(timezone.utc).strftime('%Y-%m-%dT%H-%M-%SZ') + '-' + uuid4().hex[:12] + '.tar.gz.age'
    output = BACKUPS/name
    with tempfile.TemporaryDirectory(prefix='.backup-',dir=BACKUPS) as directory:
        stage = Path(directory)
        shutil.copytree(STATE,stage/'state')
        # Do not retain API key transfer artifacts, if an interrupted deploy left one.
        (stage/'state/bootstrap.json').unlink(missing_ok=True)
        for volume in names:
            target=stage/'volumes'/volume
            target.mkdir(parents=True)
            run('docker','run','--rm','--mount',f'type=volume,source={volume},target=/source,readonly',
                '--mount',f'type=bind,source={target},target=/backup',HELPER,
                'tar','-C','/source','-cpf','/backup/data.tar','.')
        manifest={'version':1,'deployment_id':json.loads((STATE/'deployment.json').read_text())['DeploymentId'],
                  'source_sha256':(STATE/'source.sha256').read_text().strip(),'volumes':names,
                  'timestamp':datetime.now(timezone.utc).isoformat()}
        (stage/'manifest.json').write_text(json.dumps(manifest,indent=2))
        tar = subprocess.Popen(['tar','-czf','-','-C',str(stage),'.'],stdout=subprocess.PIPE)
        try:
            subprocess.run(['age','-r',recipient,'-o',str(output)],stdin=tar.stdout,check=True)
        finally:
            tar.stdout.close()
        if tar.wait() != 0:
            output.unlink(missing_ok=True)
            raise RuntimeError('Backup tar failed')
    output.chmod(0o600)
    operator = os.environ.get('SUDO_USER')
    if operator:
        import pwd
        user = pwd.getpwnam(operator)
        os.chown(output,user.pw_uid,user.pw_gid)
    (STATE/'last-backup-name').write_text(name)
    print('PASS: encrypted consistent backup:',name)
    return output


def validate_manifest(manifest, expected_id, expected_volumes, source_hash):
    if (manifest.get('version') != 1 or manifest.get('deployment_id') != expected_id or
            sorted(manifest.get('volumes',[])) != sorted(expected_volumes) or
            manifest.get('source_sha256') != source_hash):
        raise ValueError('Backup deployment, source version or volume set does not match; deploy the matching release first')


def restore(archive, identity):
    recipient = run('age-keygen','-y',identity,capture=True)
    names = volume_names()
    with tempfile.TemporaryDirectory(prefix='.restore-',dir=BACKUPS) as directory:
        stage = Path(directory)
        plain = stage/'backup.tar.gz'
        run('age','-d','-i',identity,'-o',plain,archive)
        with tarfile.open(plain) as tar:
            tar.extractall(stage/'content',filter='data')
        content=stage/'content'
        manifest=json.loads((content/'manifest.json').read_text())
        validate_manifest(manifest,json.loads((STATE/'deployment.json').read_text())['DeploymentId'],names,
                          (STATE/'source.sha256').read_text().strip())
        for volume in names:
            data = content/'volumes'/volume/'data.tar'
            if not data.is_file():
                raise ValueError('Missing service data in backup')
            # Encryption protects confidentiality, not the sender's identity.
            # Validate inner archives before giving an operator-supplied archive
            # to privileged tar inside a mounted volume.
            with tarfile.open(data) as archive:
                for member in archive:
                    parts = member.name.split('/')
                    if (member.name.startswith('/') or '..' in parts or
                            not (member.isfile() or member.isdir())):
                        raise ValueError('Unsafe volume archive member')
        # Encryption key and archive validation happen before touching live state.
        safety = backup(recipient)
        print('Pre-restore safety backup:',safety.name)
        for volume in names:
            target = content/'volumes'/volume
            run('docker','run','--rm','--mount',f'type=volume,source={volume},target=/target',
                '--mount',f'type=bind,source={target},target=/backup,readonly',HELPER,
                'sh','-ec','find /target -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +; tar -xpf /backup/data.tar -C /target')
        # State is a fixed, dedicated deployment directory, never an input path.
        saved=content/'state'
        for item in saved.iterdir():
            destination=STATE/item.name
            if item.is_dir():
                if destination.exists():
                    shutil.rmtree(destination)
                shutil.copytree(item,destination)
            else:
                shutil.copy2(item,destination)
        # copytree does not preserve Linux ownership; restore intended key readers.
        os.chown(STATE/'identity/delegation-private.pem',0,10001)
        os.chown(STATE/'identity/realm.json',0,0)
        print('PASS: restored service-owned volumes and identity state')


def main():
    os.umask(0o077)
    os.chdir(APP)
    BACKUPS.mkdir(exist_ok=True,mode=0o711)
    # Pull before stopping service writers; lack of registry access cannot strand them.
    run('docker','pull',HELPER)
    was_running = bool(run(*compose(),'ps','--status','running','-q',capture=True))
    stop()
    try:
        if sys.argv[1]=='backup':
            backup(sys.argv[2])
        elif sys.argv[1]=='restore':
            restore(Path(sys.argv[2]),Path(sys.argv[3]))
        else:
            raise ValueError('Expected backup or restore')
    finally:
        if was_running:
            start()


if __name__=='__main__':
    main()
