"""Reversible file installation owned by this application only.

Does not manipulate MO2/Vortex installations. Refuses overwrite of changed files
on uninstall, preventing accidental destruction of another manager's changes.
"""
from __future__ import annotations
import hashlib
import json
import os
import shutil
import uuid
from pathlib import Path

class InstallError(RuntimeError): pass

def digest(p: Path) -> str:
    h=hashlib.sha256()
    with p.open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024), b''):h.update(chunk)
    return h.hexdigest()

def _inside(root: Path, child: Path) -> bool:
    return child != root and root in child.parents

def deploy(stage: str | Path, target: str | Path, journal: str | Path, mod_name: str) -> str:
    """Explicitly deploy staging files into an opt-in target with rollback on error."""
    stage=Path(stage).resolve(); target=Path(target).resolve(); journal=Path(journal).resolve()
    if not stage.is_dir() or not target.is_dir(): raise InstallError('Исходная и целевая папки должны существовать')
    if stage == target or _inside(stage,target) or _inside(target,stage) or _inside(target,journal):
        raise InstallError('Папки не должны перекрывать друг друга; журнал храните отдельно')
    files=[f for f in stage.rglob('*') if f.is_file() and not f.is_symlink()]
    if not files:raise InstallError('Нет файлов для установки')
    if any(f.is_symlink() for f in stage.rglob('*')):raise InstallError('Символьные ссылки запрещены')
    if len(files)>10000:raise InstallError('Слишком много файлов')
    planned=[]
    for f in files:
        rel=f.relative_to(stage); dest=(target/rel).resolve()
        if not _inside(target,dest):raise InstallError('Путь вне папки установки')
        if dest.is_symlink():raise InstallError('Нельзя перезаписывать символические ссылки')
        if dest.exists() and not dest.is_file():raise InstallError('Конфликт с каталогом')
        planned.append((f,rel,dest))
    journal.mkdir(parents=True,exist_ok=True)
    mid=uuid.uuid4().hex; work=journal/mid; backup=work/'backup';backup.mkdir(parents=True)
    records=[]; changed=[]
    try:
        for f,rel,dest in planned:
            existed=dest.exists(); old_hash=digest(dest) if existed else None
            if existed:
                b=backup/rel;b.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(dest,b)
            dest.parent.mkdir(parents=True,exist_ok=True)
            temp=dest.with_name(dest.name+'.nxi-'+mid+'.tmp')
            shutil.copy2(f,temp);os.replace(temp,dest)
            changed.append((dest,rel,existed))
            records.append({'path':rel.as_posix(),'old_hash':old_hash,'new_hash':digest(dest),'existed':existed})
        manifest={'id':mid,'name':mod_name,'target':str(target),'files':records,'status':'installed'}
        (work/'manifest.json').write_text(json.dumps(manifest,indent=2,ensure_ascii=False),encoding='utf-8')
        return mid
    except Exception:
        for dest,rel,existed in reversed(changed):
            if existed:os.replace(backup/rel,dest)
            else:dest.unlink(missing_ok=True)
        shutil.rmtree(work,ignore_errors=True)
        raise

def uninstall(journal: str | Path, install_id: str) -> None:
    if not isinstance(install_id,str) or len(install_id)!=32 or any(c not in '0123456789abcdef' for c in install_id):raise InstallError('Неверный идентификатор установки')
    work=Path(journal).resolve()/install_id
    manifest_file=work/'manifest.json'
    try:data=json.loads(manifest_file.read_text(encoding='utf-8'))
    except (OSError,ValueError) as exc:raise InstallError('Журнал установки не найден') from exc
    if data.get('status')!='installed':raise InstallError('Мод уже удалён')
    target=Path(data['target']).resolve()
    checks=[]
    # Two-phase verification: no changes until all files have been checked.
    for row in data['files']:
        rel=Path(row['path'])
        if rel.is_absolute() or '..' in rel.parts:raise InstallError('Повреждён журнал установки')
        dest=(target/rel).resolve()
        if not _inside(target,dest) or dest.is_symlink() or not dest.is_file() or digest(dest)!=row['new_hash']:
            raise InstallError(f'Файл изменён после установки: {rel}. Удаление отменено для защиты данных.')
        original=work/'backup'/rel
        if row['existed'] and (not original.is_file() or digest(original)!=row['old_hash']):
            raise InstallError(f'Резервная копия повреждена: {rel}')
        checks.append((dest,original,row['existed']))
    for dest,original,existed in checks:
        if existed:os.replace(original,dest)
        else:dest.unlink()
    data['status']='removed'
    manifest_file.write_text(json.dumps(data,indent=2,ensure_ascii=False),encoding='utf-8')
