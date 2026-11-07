from pathlib import Path
import os
import time
from nexus_tab_installer.downloads.browser_watcher import BrowserDownloadWatcher, _equivalent


def test_stable_completed_archive_is_matched_only_to_chosen_nexus_file(tmp_path):
    clock=[time.time()]
    watcher=BrowserDownloadWatcher([tmp_path],clock=lambda:clock[0],steady_seconds=.8)
    assert watcher.register('skyrimspecialedition',10,300,'chosen.7z',0)
    assert watcher.register('skyrimspecialedition',10,301,'other.zip',0)
    a=tmp_path/'chosen.7z'
    a.write_bytes(b'7z\xbc\xaf\x27\x1c' + b'abc'*400)
    assert watcher.poll()==[]
    clock[0]+=.9
    assert watcher.poll()==[('skyrimspecialedition',10,300,a)]
    assert watcher.poll()==[]


def test_does_not_import_old_or_partial_or_unmatched_downloads(tmp_path):
    now=time.time()
    clock=[now]
    w=BrowserDownloadWatcher([tmp_path],clock=lambda:clock[0],steady_seconds=.5)
    w.register('skyrim',2,55,'expected.zip')
    (tmp_path/'unrelated.zip').write_bytes(b'Z'*100)
    (tmp_path/'expected.zip.crdownload').write_bytes(b'P'*100)
    old=tmp_path/'expected.zip'
    old.write_bytes(b'Z'*100)
    os.utime(old,(now-500,now-500))
    assert w.poll()==[]
    clock[0]+=3
    assert w.poll()==[]
    old.write_bytes(b'PK\x03\x04'+b'Z'*200)
    os.utime(old,(now+3,now+3))
    assert w.poll()==[]
    clock[0]+=.7
    assert w.poll()==[('skyrim',2,55,old)]


def test_size_mismatch_and_duplicate_identity_not_guessed(tmp_path):
    now=time.time();clock=[now]
    w=BrowserDownloadWatcher([tmp_path],clock=lambda:clock[0],steady_seconds=.5)
    w.register('skyrim',11,12,'same.rar',size_kb=4000)
    w.register('skyrim',11,13,'same.rar',size_kb=4000)
    p=tmp_path/'same.rar';p.write_bytes(b'Rar!'*100)
    assert w.poll()==[]
    clock[0]+=.7
    assert w.poll()==[]
    w.forget('skyrim',11,13)
    p.write_bytes(b'Rar!\x1a\x07\x00'+b'Z'*(4000*1024-7))
    os.utime(p,(now+1,now+1))
    assert w.poll()==[]
    clock[0]+=.7
    assert w.poll()==[('skyrim',11,12,p)]


def test_browser_filename_suffix_and_invalid_registration(tmp_path):
    assert _equivalent('file (1).7z','file.7z')
    assert not _equivalent('file.exe','file.7z')
    assert not _equivalent('fakefile.7z','file.7z')
    w=BrowserDownloadWatcher([tmp_path]);assert not w.register('skyrim',1,2,'../evil.zip')
    assert not w.register('skyrim',1,2,'setup.exe')
