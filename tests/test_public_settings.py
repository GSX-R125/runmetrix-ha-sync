import unittest
from unittest.mock import patch
from test_upload_by_month import u

class PublicSettingsTests(unittest.TestCase):
    def test_destination_is_explicit_and_trimmed(self):
        with patch.object(u,'ROOT_ID',''):
            u.configure_destination({'root_folder_id':'  example-folder_123  '})
            self.assertEqual(u.ROOT_ID,'example-folder_123')
    def test_invalid_or_missing_destination_rejected(self):
        for value in ['',None,True,'https://drive.google.com/drive/folders/id','../../x','a b']:
            with self.subTest(value=value), self.assertRaises(RuntimeError):
                u.configure_destination({'root_folder_id':value})
    def test_destination_change_does_not_reset_existing_history(self):
        import json,tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'state.json'
            path.write_text(json.dumps({'schema':1,'root_folder_id':'old','activities':{}}))
            with patch.object(u,'STATE',path),patch.object(u,'ROOT_ID','new'),self.assertRaises(RuntimeError):u.load_state()
            self.assertEqual(json.loads(path.read_text())['root_folder_id'],'old')
