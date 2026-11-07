from nexus_tab_installer.core.file_catalog import FileChoice, catalog, default_files

FILES = [
    {'file_id': 10, 'category_name': 'MAIN', 'file_name':'main older.7z', 'version':'1.0', 'uploaded_timestamp':100},
    {'file_id': 12, 'category_name': 'MAIN', 'file_name':'main latest.7z', 'version':'2.0', 'uploaded_timestamp':200},
    {'file_id': 13, 'category_name': 'OPTIONAL', 'file_name':'textures.7z', 'version':'2.0', 'uploaded_timestamp':300},
    {'file_id': 14, 'category_name': 'UPDATE', 'file_name':'patch.7z', 'uploaded_timestamp':150},
    {'file_id': 15, 'category_name': 'OLD_VERSION', 'file_name':'old.7z', 'version':'0.9', 'uploaded_timestamp':25},
    {'file_id': 16, 'category_name': 'ARCHIVED', 'file_name':'unavailable.7z'},
]

def test_default_only_latest_main():
    assert default_files(FILES) == [12]
    assert [x['file_id'] for x in FileChoice().resolve(FILES)] == [12]

def test_categories_and_versions_preserved():
    records = catalog(FILES)
    assert [r['file_id'] for r in records] == [12, 10, 14, 13, 15, 16]
    assert records[0]['version'] == '2.0'
    assert not records[-1]['available']

def test_multiple_files_including_older_versions():
    c = FileChoice()
    c.toggle(13, True, FILES)
    c.toggle(14, True, FILES)
    c.toggle(12, False, FILES)
    c.toggle(10, True, FILES)
    assert [f['file_id'] for f in c.resolve(FILES)] == [13, 14, 10]
    assert FileChoice.from_dict(c.as_dict()).as_dict() == c.as_dict()

def test_selected_files_are_exact_and_unavailable_rejected():
    c = FileChoice('manual', (13, 15))
    assert [f['file_id'] for f in c.resolve(FILES)] == [13, 15]
    try:
        c.resolve(FILES[:3]); assert False
    except ValueError as e:
        assert '15' in str(e)
    try:
        c.toggle(16, True, FILES); assert False
    except ValueError:
        pass

def test_auto_updates_to_newest_and_manual_is_pinned():
    c = FileChoice()
    assert [f['file_id'] for f in c.resolve(FILES)] == [12]
    new = FILES + [{'file_id': 20, 'category_name': 'MAIN', 'version':'3.0', 'uploaded_timestamp':900}]
    assert [f['file_id'] for f in c.resolve(new)] == [20]
    c.toggle(10, True, FILES)
    assert [f['file_id'] for f in c.resolve(new)] == [12, 10]
    c.reset()
    assert c.mode == 'auto'

def test_empty_selection_and_no_main_are_not_guessed():
    c = FileChoice('manual', ())
    assert c.resolve(FILES) == []
    assert FileChoice().resolve([FILES[2]]) == []
    assert FileChoice.from_dict({'mode':'manual','file_ids':['13','13','bad',-1]}).file_ids == (13,)


def test_removed_main_never_enabled_after_catalog_is_normalized_again():
    raw = [{'file_id':33,'category_name':'MAIN','file_status':'removed', 'size_kb':'1.5'}]
    normalized = catalog(raw)
    assert normalized[0]['size_kb'] == 1
    assert not normalized[0]['available']
    assert default_files(normalized) == []
    try:
        FileChoice().toggle(33, True, normalized)
        assert False
    except ValueError:
        pass
