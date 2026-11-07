import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from nexus_tab_installer.integrations.mo2_install import install_simple, available_profiles, MO2InstallError

class MO2InstallTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        # A person may have MO2 open while the tests run: it is not the one being tested here.
        running = mock.patch('nexus_tab_installer.integrations.mo2_install.mo2_is_running', return_value=False)
        running.start()
        self.addCleanup(running.stop)
        self.root=Path(self.tmp.name)
        (self.root/'mods').mkdir()
        (self.root/'profiles'/'Default').mkdir(parents=True)
        (self.root/'profiles'/'Default'/'modlist.txt').write_bytes(b'# existing\r\n')
        (self.root/'ModOrganizer.ini').write_text('[Settings]\nbase_directory=%BASE_DIR%\n',encoding='utf-8')
        (self.root/'stage').mkdir()
        (self.root/'stage'/'textures').mkdir()
        (self.root/'stage'/'textures'/'new.dds').write_bytes(b'abc')
    def test_installs_and_enables(self):
        r=install_simple(self.root/'stage',self.root/'ModOrganizer.ini','Default','New Textures')
        self.assertTrue(r['enabled'])
        self.assertEqual((self.root/'mods'/'New Textures'/'textures'/'new.dds').read_bytes(),b'abc')
        self.assertIn(b'+New Textures', (self.root/'profiles'/'Default'/'modlist.txt').read_bytes())
    def test_prevents_overwrite(self):
        install_simple(self.root/'stage',self.root/'ModOrganizer.ini','Default','New Textures')
        with self.assertRaises(MO2InstallError):install_simple(self.root/'stage',self.root/'ModOrganizer.ini','Default','New Textures')
    def test_fomod_refused(self):
        (self.root/'stage'/'fomod').mkdir()
        (self.root/'stage'/'fomod'/'ModuleConfig.xml').write_text('<config/>')
        with self.assertRaises(MO2InstallError): install_simple(self.root/'stage',self.root/'ModOrganizer.ini','Default','Fomod')
    def test_profile_required(self):
        with self.assertRaises(MO2InstallError):install_simple(self.root/'stage',self.root/'ModOrganizer.ini','Missing','Test')
    def test_profiles(self):
        self.assertEqual(available_profiles(self.root/'ModOrganizer.ini'), ['Default'])
if __name__=='__main__': unittest.main()
