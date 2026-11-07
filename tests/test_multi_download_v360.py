from types import SimpleNamespace
from nexus_tab_installer.core.workflow_session import WorkflowSession


def test_session_cancels_every_file_of_one_mod():
    item={'game':'skyrimspecialedition','mod_id':123}
    run=WorkflowSession([item])
    for n in (10, 11, 12):
        job=run.attach_job(item, lambda n=n:SimpleNamespace(id=n, file_id=n))
        assert job.id == n
    assert run.all_job_ids() == [10, 11, 12]
    got=[]
    assert run.stop(got.append)==3
    assert set(got)=={10,11,12}
    assert run.attach_job(item, lambda:SimpleNamespace(id=40,file_id=40)) is None


def test_nxm_job_can_be_registered_per_file():
    item={'game':'fallout4','mod_id':66}
    run=WorkflowSession([item])
    run.register_job(item, SimpleNamespace(id=4,file_id=20))
    run.register_job(item, SimpleNamespace(id=7,file_id=30))
    assert run.all_job_ids()==[4,7]


def test_ui_uses_file_choices_in_real_download_path():
    from pathlib import Path
    app=(Path(__file__).resolve().parents[1]/'src/nexus_tab_installer/ui/flet_app.py').read_text(encoding='utf-8')
    assert "chosen = pref.resolve(available)" in app
    assert 'for f in chosen:' in app
    assert "session.expect(item, spec['task'])" in app
    assert "session.attach_job(item, enqueue)" in app
    assert "file_preferences[identity(item)] = FileChoice.from_dict(choice.get('files'))" in app
    assert "'files': file_preferences.get((g, mid), FileChoice()).as_dict()" in app
