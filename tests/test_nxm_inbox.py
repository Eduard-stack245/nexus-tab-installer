from pathlib import Path
import json
import time
from nexus_tab_installer.nexus.nxm_inbox import submit_nxm, collect_nxm

URL = 'nxm://skyrimspecialedition/mods/42/files/123?key=abc&expires=9999999999&user_id=77'

def test_roundtrip(tmp_path):
    submit_nxm(URL, tmp_path)
    assert collect_nxm(tmp_path) == [URL]
    assert collect_nxm(tmp_path) == []

def test_reject_invalid_and_does_not_store(tmp_path):
    try:
        submit_nxm('https://malicious.example/whatever', tmp_path)
        assert False, 'invalid URL accepted'
    except Exception:
        pass
    assert not list(tmp_path.glob('*.json'))

def test_expired_inbox_entry_ignored_and_deleted(tmp_path):
    (tmp_path/'old.json').write_text(json.dumps({'nxm': URL, 'created': int(time.time())-1800}), encoding='utf-8')
    assert collect_nxm(tmp_path) == []
    assert not list(tmp_path.glob('*.json'))

def test_malformed_entry_ignored(tmp_path):
    (tmp_path/'bad.json').write_text('{bad', encoding='utf-8')
    assert collect_nxm(tmp_path) == []
