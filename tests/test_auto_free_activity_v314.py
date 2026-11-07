from nexus_tab_installer.browser.auto_free import AutoFreeBatch


def test_progress_resets_stall_warning_timer_without_confirming_download():
    now = [100.0]
    batch = AutoFreeBatch(clock=lambda: now[0])
    page = 'https://www.nexusmods.com/skyrimspecialedition/mods/197526?tab=files&file_id=829609'
    assert batch.start([page]) == 1
    job = batch.claim('edge-example')
    now[0] += 29
    assert not batch.stalled_page(threshold=30)
    assert batch.progress('edge-example', job['id'], 'slow')
    assert batch.job_state(job['id']) == 'leased'
    now[0] += 29
    assert not batch.stalled_page(threshold=30)
    now[0] += 2
    assert batch.stalled_page(threshold=30)
