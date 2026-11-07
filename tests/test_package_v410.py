"""The archive for another person holds the program and nothing personal."""
import importlib.util
import json
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('make_package', ROOT / 'scripts' / 'make_package.py')
make_package = importlib.util.module_from_spec(spec)
spec.loader.exec_module(make_package)


def names_in(path):
    with zipfile.ZipFile(path) as archive:
        return archive.namelist()


def test_archive_has_what_the_program_needs_and_nothing_else(tmp_path):
    names = names_in(make_package.build(output=tmp_path / 'package.zip'))
    assert all(name.startswith('NexusTabInstaller/') for name in names)
    inside = {name.split('/', 1)[1] for name in names}
    for needed in ('START_WINDOWS.bat', 'README.md', 'requirements.txt', 'scripts/start_windows.bat', 'scripts/nxm_entry.py',
                   'docs/BROWSER_SETUP_GUIDE.html', 'extensions/chromium/manifest.json', 'extensions/firefox/manifest.json',
                   'src/nexus_tab_installer/ui/flet_app.py'):
        assert needed in inside, needed
    for name in inside:
        first = name.split('/')[0]
        assert first not in ('data', 'tests', 'dist'), name
        assert '__pycache__' not in name and not name.endswith('.pyc') and not name.endswith('.pem'), name
        assert not name.startswith('docs/history'), name


def test_bundled_runtime_goes_into_the_archive_even_with_folders_named_data(tmp_path):
    root = fake_project(tmp_path)
    runtime = tmp_path / 'built-runtime'
    (runtime / 'flet' / 'data').mkdir(parents=True)
    (runtime / 'flet' / 'flet.exe').write_bytes(b'exe')
    (runtime / 'flet' / 'data' / 'app.so').write_bytes(b'lib')
    names = names_in(make_package.build(root, tmp_path / 'package.zip', include=('src',), runtime=runtime))
    assert 'NexusTabInstaller/runtime/flet/flet.exe' in names
    assert 'NexusTabInstaller/runtime/flet/data/app.so' in names


def test_bundled_python_finds_the_program_by_relative_paths_only(tmp_path):
    make_package.write_path_file(tmp_path)
    lines = (tmp_path / 'python314._pth').read_text(encoding='ascii').splitlines()
    assert r'..\..\src' in lines and r'Lib\site-packages' in lines and 'import site' in lines
    assert not any(':' in line for line in lines), 'No drive letters: the folder can move anywhere'


def test_launcher_prefers_the_bundled_python():
    text = (ROOT / 'scripts' / 'start_windows.bat').read_text(encoding='ascii')
    assert text.index(r'runtime\python\pythonw.exe') < text.index('where py'), 'No Python on the computer is needed'
    assert 'cd /d "%~dp0.."' in text, 'It must work from any folder'


def test_python_version_matches_its_path_file():
    assert make_package.PYTHON_VERSION.startswith('3.14.')
    assert make_package.PATH_FILE_LINES[0] == 'python314.zip'


def test_batch_files_have_windows_line_ends():
    """cmd.exe misreads a batch file with bare LF, and first of all its Cyrillic lines."""
    for path in [ROOT / 'START_WINDOWS.bat', *(ROOT / 'scripts').glob('*.bat')]:
        data = path.read_bytes()
        assert data.count(b'\n') == data.count(b'\r\n'), path.name


def test_program_finds_the_files_it_opens_itself():
    """These paths are built in the code at run time, so a missing one would show up only for the user."""
    included = make_package.INCLUDE
    for needed in ('extensions', 'docs/BROWSER_SETUP_GUIDE.html', 'scripts/nxm_entry.py'):
        assert needed in included


def fake_project(tmp_path, secret_in_source=False):
    (tmp_path / 'src').mkdir()
    (tmp_path / 'src' / 'app.py').write_text('KEY = "' + ('my-secret-api-key' if secret_in_source else 'none') + '"', encoding='utf-8')
    (tmp_path / 'src' / 'data').mkdir()
    (tmp_path / 'src' / 'data' / 'private.txt').write_text('private', encoding='utf-8')
    (tmp_path / 'data' / 'config').mkdir(parents=True)
    (tmp_path / 'data' / 'config' / 'config.json').write_text(json.dumps({'api_key': 'my-secret-api-key'}), encoding='utf-8')
    return tmp_path


def test_your_key_stops_the_build_if_it_ever_gets_into_a_file(tmp_path):
    root = fake_project(tmp_path, secret_in_source=True)
    target = tmp_path / 'package.zip'
    with pytest.raises(ValueError):
        make_package.build(root, target, include=('src',))
    assert not target.exists()


def test_personal_folders_are_skipped_even_inside_a_listed_one(tmp_path):
    root = fake_project(tmp_path)
    names = names_in(make_package.build(root, tmp_path / 'package.zip', include=('src',)))
    assert names == ['NexusTabInstaller/src/app.py']


def test_missing_file_stops_the_build(tmp_path):
    with pytest.raises(FileNotFoundError):
        make_package.build(tmp_path, tmp_path / 'package.zip', include=('nothing',))
