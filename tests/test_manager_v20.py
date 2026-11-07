import json
import tempfile
import unittest
from pathlib import Path
from nexus_tab_installer.integrations.discovery import discover_vortex
from nexus_tab_installer.integrations.managed_install import deploy, uninstall, InstallError

class ManagerTests(unittest.TestCase):
    def test_vortex_detection_never_assumes_custom_location(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp)/'Roaming'; (base/'Vortex'/'downloads').mkdir(parents=True)
            result=discover_vortex({'APPDATA':str(base)})
            self.assertEqual(len(result),1)
            self.assertFalse(result[0].verified)
            self.assertEqual(result[0].downloads,base/'Vortex'/'downloads')

    def test_restore_existing_and_remove_new(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);stage=p/'stage';target=p/'game';journal=p/'journal'
            stage.mkdir();target.mkdir()
            (stage/'a.txt').write_text('new');(stage/'b.txt').write_text('added');(target/'a.txt').write_text('original')
            mid=deploy(stage,target,journal,'A')
            self.assertEqual((target/'a.txt').read_text(),'new')
            uninstall(journal,mid)
            self.assertEqual((target/'a.txt').read_text(),'original')
            self.assertFalse((target/'b.txt').exists())

    def test_changed_file_blocks_uninstall_without_partial_deletion(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);stage=p/'stage';target=p/'game';journal=p/'journal'
            stage.mkdir();target.mkdir()
            (stage/'a').write_text('1');(stage/'b').write_text('2')
            mid=deploy(stage,target,journal,'A')
            (target/'b').write_text('other app changed')
            with self.assertRaises(InstallError):uninstall(journal,mid)
            self.assertEqual((target/'a').read_text(),'1')

    def test_no_stage_target_overlap(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);stage=p/'stage';stage.mkdir()
            with self.assertRaises(InstallError):deploy(stage,stage,p/'journal','test')

if __name__=='__main__':unittest.main()
