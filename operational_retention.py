from contextlib import closing
from datetime import datetime, timezone
import logging
from pathlib import Path
import re
import shutil
import sqlite3

DAYS = 30 * 86400
LOG = logging.getLogger('retention')
BACKUP_NAME = r'(?:presence|admin|domain|public-setup|app-name|service|alerts)-[0-9]{8}T[0-9]{6}(?:[0-9]{3})?Z'


def linked(path):
    return path.is_symlink() or getattr(path, 'is_junction', lambda: False)()


def remove_old_tree(root, folder, cutoff):
    root, folder = Path(root).resolve(), Path(folder)
    if linked(folder) or not folder.is_dir() or not folder.resolve().is_relative_to(root) or folder.resolve() == root:
        return False
    pending = [folder]
    while pending:
        item = pending.pop()
        if linked(item) or not item.resolve().is_relative_to(root) or item.stat().st_mtime > cutoff:
            return False
        if item.is_dir():
            pending.extend(item.iterdir())
    shutil.rmtree(folder)
    return True


def purge_operations(root, now):
    root = Path(root).resolve()
    cutoff = now - DAYS
    with closing(sqlite3.connect(root / 'connections.sqlite3', timeout=10)) as db:
        with db:
            db.execute('PRAGMA secure_delete=ON')
            db.execute('DELETE FROM admin_audit WHERE happened < ?',
                       (datetime.fromtimestamp(cutoff, timezone.utc).strftime('%Y-%m-%d %H:%M:%S'),))
    for path in root.glob('receiver.log.*'):
        if not linked(path) and path.is_file() and re.fullmatch(r'receiver\.log\.(?:[1-3]|[0-9]{4}-[0-9]{2}-[0-9]{2})', path.name) and path.stat().st_mtime <= cutoff:
            path.unlink()
    # Windows denies opening this file while the CD job holds its exclusive handle.
    try:
        with (root / 'cd-deployment.lock').open('a+b'):
            return purge_artifacts(root, cutoff)
    except OSError:
        LOG.warning('Artifact cleanup deferred; deployment lock or file unavailable')
        return 0


def purge_artifacts(root, cutoff):
    removed = 0
    updates = root / 'admin-updates'
    if linked(updates):
        return 0
    database = updates / 'updates.sqlite3'
    if database.is_file() and not linked(database):
        with closing(sqlite3.connect(database, timeout=10)) as db:
            with db:
                db.execute('BEGIN IMMEDIATE')
                db.execute('PRAGMA secure_delete=ON')
                if db.execute("SELECT 1 FROM updates WHERE state IN ('staged','scheduled','applying','restarting')").fetchone():
                    return 0
                for identity, state, detail in db.execute('SELECT id,state,detail FROM updates WHERE changed<=?', (cutoff,)).fetchall():
                    if not re.fullmatch('[a-f0-9]{32}', identity) or state not in ('success','cancelled','rolled-back'):
                        continue
                    folder = updates / identity
                    if not folder.exists() or remove_old_tree(root, folder, cutoff):
                        db.execute('DELETE FROM updates WHERE id=?', (identity,))
                        removed += 1
    for name, pattern in [('backups', BACKUP_NAME), ('diagnostics', r'startup-[0-9]{8}T[0-9]{6}Z'),
                          ('cd-deployments', r'[0-9]{14}-[a-f0-9]{32}')]:
        parent = root / name
        if linked(parent) or not parent.is_dir():
            continue
        for folder in parent.iterdir():
            if not re.fullmatch(pattern, folder.name):
                continue
            if name == 'cd-deployments':
                status = folder / 'result.txt'
                if not status.is_file() or linked(status) or status.read_text('utf-8-sig').strip() not in ('passed','rolled-back','preflight'):
                    continue
            if remove_old_tree(root, folder, cutoff):
                removed += 1
    return removed
