"""Regression coverage for diagnostics data and the new Flet log workspace."""
import json
from pathlib import Path
from zipfile import ZipFile

from nexus_tab_installer.core.trace import TraceLog, classify_event, describe_event


def test_friendly_labels_filters_and_live_read(tmp_path):
    log = TraceLog(tmp_path)
    log.emit('ui.download_clicked', selected=2)
    log.emit('extension.window.created', job_id='a'*24, window_id=32)
    log.emit('extension.content.command_failed', job_id='a'*24,
             reason='content_script_unavailable')
    rows = log.read_recent()
    assert len(rows) == 3
    assert [classify_event(r['event']) for r in rows] == ['download','browser','error']
    assert 'Команда не дошла' in describe_event(rows[2])
    assert 'Скачать' not in describe_event(rows[0]) or describe_event(rows[0])


def test_corrupted_partial_jsonl_not_crash_and_rotated_files_visible(tmp_path):
    log = TraceLog(tmp_path, max_bytes=200)
    for i in range(9):
        log.emit('ui.heartbeat', count=i, stage='active')
    with log.path.open('a',encoding='utf8') as out:
        out.write('{incomplete')
    assert log.read_recent(100)
    assert not any(r.get('event') == '{incomplete' for r in log.read_recent())
    assert len(log.read_recent(2)) == 2


def test_exception_is_redacted_and_export_has_summary(tmp_path):
    log = TraceLog(tmp_path)
    secret = 'nxm://some-game/mods/777/files/8?key=sensitive'
    try:
        raise ValueError(secret)
    except ValueError as exc:
        log.emit_exception(exc, area='network')
    records = log.read_recent()
    assert records[0]['data']['error_type'] == 'valueerror'
    assert records[0]['data']['area'] == 'network'
    raw = log.path.read_text(encoding='utf8')
    assert secret not in raw
    assert 'sensitive' not in raw
    archive=log.export()
    with ZipFile(archive) as z:
        assert 'SUMMARY.txt' in z.namelist()
        assert 'download-trace.jsonl' in z.namelist()
        assert secret not in '\n'.join(z.read(n).decode('utf8') for n in z.namelist())


def test_user_log_screen_is_connected_and_no_unsafe_wrap_expand():
    app=Path(__file__).parents[1]/'src/nexus_tab_installer/ui/flet_app.py'
    source=app.read_text(encoding='utf8')
    for required in ["('logs','Журнал'",'def make_logs()', 'def refresh_log_view(',
                     'on_select=refresh_log_view', "'logs':make_logs", 'trace.emit_exception(exc, area=\'eventloop\')']:
        if required == 'on_select=refresh_log_view':
            assert 'log_filter.on_select = refresh_log_view' in source
        else:
            assert required in source
