from pathlib import Path
import pytest
from nexus_tab_installer.downloads.library import file_directory


def test_same_filename_can_exist_for_two_nexus_file_ids(tmp_path):
    first=file_directory(tmp_path,'skyrimspecialedition',100,10)
    second=file_directory(tmp_path,'skyrimspecialedition',100,11)
    first.mkdir(parents=True)
    second.mkdir(parents=True)
    (first/'same.7z').write_bytes(b'old version')
    (second/'same.7z').write_bytes(b'new version')
    assert (first/'same.7z').read_bytes() != (second/'same.7z').read_bytes()
    assert len(list(tmp_path.rglob('same.7z'))) == 2


def test_directory_rejects_traversal(tmp_path):
    with pytest.raises(ValueError):
        file_directory(tmp_path,'../evil',1,1)
    with pytest.raises(ValueError):
        file_directory(tmp_path,'skyrim',1,0)
