import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import urllib.request
import zipfile

NAME = 'vendor-pillow-13'


def hashes(folder):
    result = {}
    if folder.is_symlink() or getattr(folder, 'is_junction', lambda: False)():
        raise ValueError('Media dependency must not be a link')
    for path in folder.rglob('*'):
        if path.is_symlink() or getattr(path, 'is_junction', lambda: False)() or not path.resolve().is_relative_to(folder.resolve()):
            raise ValueError('Media dependency contains a link')
        if path.is_file():
            result[path.relative_to(folder).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def unpack(raw, settings, target):
    if len(raw) != settings['size'] or hashlib.sha256(raw).hexdigest() != settings['sha256']:
        raise ValueError('Pillow wheel checksum mismatch')
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        entries = archive.infolist()
        if len(entries) > 1000 or sum(e.file_size for e in entries) > 64*1024*1024:
            raise ValueError('Unexpected Pillow wheel size')
        names = set()
        for item in entries:
            path = PurePosixPath(item.filename)
            if (path.is_absolute() or '\\' in item.filename or ':' in item.filename or '..' in path.parts or
                    not path.parts or path.parts[0] not in ('PIL','pillow.libs','pillow-13.0.0.dist-info') or
                    item.filename.casefold() in names or item.flag_bits & 1 or (item.external_attr>>16)&0o170000 == 0o120000):
                raise ValueError('Unexpected Pillow wheel entry')
            names.add(item.filename.casefold())
        for item in entries:
            dest = target / item.filename
            if item.is_dir():
                dest.mkdir(parents=True,exist_ok=True)
            else:
                dest.parent.mkdir(parents=True,exist_ok=True)
                dest.write_bytes(archive.read(item))


def stage(root, source, job):
    settings = json.loads((source/'deploy/media.json').read_text('utf-8'))
    if settings['version'] != '13.0.0' or not settings['url'].startswith('https://files.pythonhosted.org/packages/'):
        raise ValueError('Unsupported media dependency')
    if sys.version_info[:2] != (3,12) or sys.platform != 'win32':
        raise ValueError('This media wheel requires Windows Python 3.12 x64')
    request = urllib.request.Request(settings['url'], headers={'User-Agent':'Threadle deployment'})
    with urllib.request.urlopen(request, timeout=30) as response:
        if not response.url.startswith('https://files.pythonhosted.org/packages/'):
            raise ValueError('Unexpected media wheel download destination')
        raw = response.read(settings['size']+1)
    candidate = job/NAME
    unpack(raw,settings,candidate)
    script = ("import sys,io; sys.path.insert(0,sys.argv[1]); from PIL import Image; "
              "assert Image.__version__=='13.0.0'; im=Image.new('RGB',(4,4)); "
              "b=io.BytesIO(); im.save(b,format='PNG'); Image.open(io.BytesIO(b.getvalue())).load(); "
              "g=io.BytesIO(); im.save(g,format='GIF',save_all=True,append_images=[Image.new('RGB',(4,4),'red')]); "
              "assert Image.open(io.BytesIO(g.getvalue())).n_frames==2; print('Pillow 13 PNG/GIF verified')")
    subprocess.run([sys.executable,'-B','-I','-c',script,str(candidate)],check=True,timeout=30)
    target = root/NAME
    expected = hashes(candidate)
    if target.exists() and hashes(target) != expected:
        raise ValueError('Existing isolated media dependency differs; no files changed')
    (job/'media-state.json').write_text(json.dumps({'existing':target.exists(),'files':expected}),'utf-8')


def install(root, job):
    state_path = job/'media-state.json'
    if not state_path.exists():
        return
    state = json.loads(state_path.read_text('utf-8'))
    source, target = job/NAME, root/NAME
    if state['existing']:
        if hashes(target) != state['files']:
            raise ValueError('Installed media dependency changed after preflight')
        return
    if target.exists() or hashes(source) != state['files']:
        raise ValueError('Media dependency changed after preflight')
    os.rename(source,target)


def restore(root, job):
    state_path = job/'media-state.json'
    if not state_path.exists():
        return
    state = json.loads(state_path.read_text('utf-8'))
    target = root/NAME
    if not state['existing'] and target.exists():
        if hashes(target) != state['files']:
            raise ValueError('Isolated media dependency changed; preserved for inspection')
        if target.resolve().parent != root.resolve():
            raise ValueError('Unexpected media restore path')
        shutil.rmtree(target)
