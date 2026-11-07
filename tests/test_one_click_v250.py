from pathlib import Path
from threading import Thread
from types import SimpleNamespace
from nexus_tab_installer.core.workflow_session import WorkflowSession, identity
from nexus_tab_installer.nexus.free_workflow import FreeQueue

ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT/'src/nexus_tab_installer/ui/flet_app.py').read_text(encoding='utf-8')
VIEWS = (ROOT/'src/nexus_tab_installer/ui/views.py').read_text(encoding='utf-8')


def test_cancel_removes_running_tasks_and_prevents_future_addition():
    items = [{'game':'skyrimspecialedition', 'mod_id':1}, {'game':'fallout4', 'mod_id':2}]
    run = WorkflowSession(items)
    created = []
    job = run.attach_job(items[0], lambda: SimpleNamespace(id=11))
    assert job.id == 11
    assert run.set_status(items[1], 'Проверка файлов')
    assert run.stop(created.append) == 1
    assert created == [11]
    assert not run.set_status(items[1], 'В очереди')
    assert run.attach_job(items[1], lambda: SimpleNamespace(id=22)) is None
    assert all(st == 'Отменено' for st, _ in run.snapshot()[0].values())


def test_cancel_races_job_enqueuing():
    items=[{'game':'skyrimspecialedition','mod_id':7}]
    run=WorkflowSession(items)
    cancelled=[]
    started=[]
    def worker():
        for i in range(20):
            run.attach_job(items[0], lambda i=i: SimpleNamespace(id=i))
    t=Thread(target=worker)
    t.start()
    run.stop(cancelled.append)
    t.join()
    assert run.cancelled.is_set()
    assert set(run.snapshot()[1].values()).issubset(set(cancelled))


def test_free_queue_clear_persists(tmp_path):
    path=tmp_path/'pending.json'
    q=FreeQueue(path)
    q.add(['https://www.nexusmods.com/skyrimspecialedition/mods/12?tab=files&file_id=6'])
    assert q.clear()==1
    assert len(FreeQueue(path))==0


def test_single_primary_action_no_duplicates():
    assert APP.count("ft.Button('Скачать моды из открытых вкладок'") == 1
    assert APP.count("ft.Button('Скачать выбранные'") == 1
    assert "ft.Button('Отмена'" in APP
    assert "start_button.on_click = start_workflow" in APP
    assert "cancel_button.on_click = cancel_workflow" in APP
    assert 'prepare_downloads(valid, run)' in APP
    assert 'download_selected_button.on_click = download_selected' in APP
    assert "snapshots.collect(max_age=120)" in APP
    assert 'Сканировать вкладки браузеров' not in VIEWS
    assert 'Последние версии · скачать выбранные' not in VIEWS
