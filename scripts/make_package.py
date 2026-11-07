"""Build the portable program: dist/NexusTabInstaller-<version>-portable.zip

Unpack it anywhere, even on a flash drive, and run START_WINDOWS.bat: Python, the
packages and the window of the program are all inside. Nothing gets installed.

Only the files listed here go in. The list is deliberately not "everything except":
data/ holds your API key, the browser token, your archives and logs, and a new file
there must never slip into a copy for someone else.

Needs Python 3.14 with requirements.txt installed and internet access:
    py -3 scripts/make_package.py
"""
import hashlib
import json
import re
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INCLUDE = (
    'START_WINDOWS.bat', 'README.md', 'README_RU.md', 'LICENSE', 'CHANGELOG.md', 'requirements.txt',
    'scripts/start_windows.bat', 'scripts/nxm_entry.py',
    'docs/BROWSER_SETUP_GUIDE.html', 'docs/BROWSER_SETUP_GUIDE_EN.html', 'docs/SETTINGS.md',
    'src', 'extensions',
)
SKIP_PARTS = {'__pycache__', '.pytest_cache', 'data'}
SKIP_SUFFIXES = {'.pyc', '.pyo', '.tmp'}
MIN_SECRET_LENGTH = 8

# The official embeddable Python; its checksum comes from the release page on python.org.
PYTHON_VERSION = '3.14.10'
PYTHON_URL = f'https://www.python.org/ftp/python/{PYTHON_VERSION}/python-{PYTHON_VERSION}-embed-amd64.zip'
PYTHON_SHA256 = '90905e64a840557d8c1bc4f5b74db3d7ee85e3ffb217611cac1c7813acea4159'
PATH_FILE_LINES = ('python314.zip', '.', r'Lib\site-packages', r'..\..\src', 'import site')


def version_of(root):
    match = re.search(r'^version = "([^"]+)"', (root / 'pyproject.toml').read_text(encoding='utf-8'), re.M)
    return match[1]


def secrets_of(root):
    """Your key and token, read only to prove they are not in the archive."""
    try:
        config = json.loads((root / 'data' / 'config' / 'config.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return []
    values = (config.get(name) for name in ('api_key', 'browser_token'))
    return [v.encode() for v in values if isinstance(v, str) and len(v) >= MIN_SECRET_LENGTH]


def files_of(root, include=INCLUDE):
    for name in include:
        path = root / name
        if not path.exists():
            raise FileNotFoundError(f'Нет обязательного файла: {name}')
        candidates = [path] if path.is_file() else sorted(p for p in path.rglob('*') if p.is_file())
        for candidate in candidates:
            relative = candidate.relative_to(root)
            if not SKIP_PARTS.intersection(relative.parts) and candidate.suffix not in SKIP_SUFFIXES:
                yield candidate, relative


def write_path_file(python_dir):
    """Where the bundled Python looks for code. All relative: the folder can move anywhere."""
    (python_dir / 'python314._pth').write_text('\n'.join(PATH_FILE_LINES) + '\n', encoding='ascii')


def prepare_runtime(work):
    """Python, the packages and the window of the program, in one folder."""
    if sys.version_info[:2] != (3, 14):
        raise RuntimeError('Сборка запускается на Python 3.14: пакеты должны подходить встроенному Python.')
    import flet_desktop
    runtime = work / 'runtime'
    python_dir = runtime / 'python'
    python_dir.mkdir(parents=True)

    archive = work / 'python-embed.zip'
    with urllib.request.urlopen(PYTHON_URL, timeout=300) as reply, archive.open('wb') as out:
        shutil.copyfileobj(reply, out)
    if hashlib.sha256(archive.read_bytes()).hexdigest() != PYTHON_SHA256:
        raise RuntimeError('Контрольная сумма скачанного Python не совпала с указанной на python.org.')
    with zipfile.ZipFile(archive) as bundle:
        bundle.extractall(python_dir)
    archive.unlink()
    write_path_file(python_dir)

    site = python_dir / 'Lib' / 'site-packages'
    site.mkdir(parents=True)
    # The same version of the window and of its Python package.
    flet_version = flet_desktop.version.version
    subprocess.run([sys.executable, '-m', 'pip', 'install', '--quiet', '--disable-pip-version-check', '--no-compile',
                    '--only-binary=:all:', '--target', str(site), '-r', str(ROOT / 'requirements.txt'),
                    f'flet=={flet_version}', f'flet-desktop=={flet_version}'], check=True)
    shutil.copytree(Path(flet_desktop.ensure_client_cached()) / 'flet', runtime / 'flet')
    # Compiled now, so that the first start from a slow flash drive is not slow. "Unchecked" because
    # unpacking can change the dates of the files, and that would otherwise throw this work away.
    subprocess.run([str(python_dir / 'python.exe'), '-W', 'ignore', '-m', 'compileall', '-q', '-j', '0',
                    '--invalidation-mode', 'unchecked-hash', 'Lib/site-packages'],
                   cwd=python_dir, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return runtime


def build(root=ROOT, output=None, include=INCLUDE, runtime=None):
    target = Path(output) if output else root / 'dist' / f'NexusTabInstaller-{version_of(root)}-portable.zip'
    target.parent.mkdir(parents=True, exist_ok=True)
    secrets = secrets_of(root)
    temporary = target.with_name(target.name + '.tmp')
    try:
        with zipfile.ZipFile(temporary, 'w', zipfile.ZIP_DEFLATED) as archive:
            for path, relative in files_of(root, include):
                content = path.read_bytes()
                if any(secret in content for secret in secrets):
                    raise ValueError(f'В файле {relative} найден ваш ключ или токен, архив не создан')
                archive.writestr(str(Path('NexusTabInstaller') / relative).replace('\\', '/'), content)
            if runtime is not None:
                for path in sorted(p for p in Path(runtime).rglob('*') if p.is_file()):
                    inside = Path('NexusTabInstaller') / 'runtime' / path.relative_to(runtime)
                    archive.write(path, str(inside).replace('\\', '/'))
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return target


if __name__ == '__main__':
    work = ROOT / 'dist' / 'portable-build'
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    try:
        built = build(runtime=prepare_runtime(work))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    print(f'{built} ({built.stat().st_size // 1048576} МБ)')
