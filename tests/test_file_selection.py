import unittest
from nexus_tab_installer.core.file_selection import latest_main
class TestLatest(unittest.TestCase):
    def test_latest_main_not_optional(self):
        files=[{'file_id':11,'category_name':'MAIN','uploaded_timestamp':100}, {'file_id':12,'category_name':'MAIN','uploaded_timestamp':200}, {'file_id':13,'category_name':'OPTIONAL','uploaded_timestamp':900}]
        self.assertEqual(latest_main(files)['file_id'],12)
    def test_no_main(self):
        self.assertIsNone(latest_main([{'file_id':1,'category_name':'OLD_VERSION'}]))
    def test_tiebreaker(self):
        self.assertEqual(latest_main([{'file_id':4,'category_name':'MAIN'},{'file_id':5,'category_name':'MAIN'}])['file_id'],5)
