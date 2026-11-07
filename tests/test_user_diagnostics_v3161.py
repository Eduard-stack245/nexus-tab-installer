from nexus_tab_installer.core.trace import TraceLog, diagnose_trace, safe_event, describe_event


def test_early_script_failure_does_not_override_later_delivery_and_cancellation(tmp_path):
    log = TraceLog(tmp_path)
    log.emit('batch.started', count=8)
    log.emit('job.leased', job_id='a'*24, mod_id=197526, file_id=829609)
    log.emit('extension.content.command_failed', reason='content_script_unavailable')
    log.emit('extension.page.assignment_received')
    log.emit('extension.content.command_delivered')
    log.emit('job.progress', step='file')
    log.emit('batch.stopped', count=8)
    conclusion = log.summary_text()
    assert 'Команда успешно передана' in conclusion
    assert 'Нахождение/нажатие Slow Download не подтверждено' in conclusion
    assert 'очередь' in conclusion.lower()
    assert 'Команда до страницы не дошла' not in conclusion


def test_page_button_probe_reports_only_counts_without_page_text(tmp_path):
    log=TraceLog(tmp_path)
    log.emit('batch.started',count=1)
    log.emit('job.leased',job_id='a'*24)
    log.emit('extension.page.control_scan', count=20, items=0, selected=0,
             body_hint=1, iframe_count=0, age_ms=1100,
             page_text='Slow download', api_key='SECRET', nxm='nxm://SECRET')
    record=log.read_recent()[-1]
    assert record['data']['body_hint']==1
    assert 'SECRET' not in log.path.read_text()
    assert 'Slow download' not in log.path.read_text()
    assert 'кнопка не совпала' in diagnose_trace(log.read_recent())
    assert 'совпадений Slow Download: 0' in describe_event(record)


def test_hidden_or_disabled_button_is_not_reported_as_missing(tmp_path):
    log=TraceLog(tmp_path)
    log.emit('batch.started',count=1)
    log.emit('job.leased',job_id='a'*24)
    log.emit('extension.page.control_scan',count=20,items=1,selected=0,body_hint=1)
    assert 'недоступен для нажатия' in log.summary_text()
