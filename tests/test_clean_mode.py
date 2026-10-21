import fcntl
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch
from test_upload_by_month import UploadTests,u
from test_runner import runner

class CleanDriveTests(UploadTests):
    def test_clean_ignores_uploaded_history_and_forces_copy(self):
        key='20261020_2041'
        self.source_manifest(key)
        self.existing_state(key)
        with patch.object(u,'list_folders',side_effect=self.folders_list),patch.object(u,'rclone',side_effect=self.command):
            u.once(10,True);u.once(10,True)
        copies=[c for c in self.calls if c[0]=='copy']
        self.assertEqual(len(copies),2)
        self.assertTrue(all('--ignore-times' in c for c in copies))
        self.assertEqual(u.load_state()['activities'][key]['status'],'uploaded')
    def test_clean_with_no_upload_state_does_not_baseline_skip(self):
        self.source_manifest('20261020_2041')
        with patch.object(u,'list_folders',side_effect=self.folders_list),patch.object(u,'rclone',side_effect=self.command):u.once(10,True)
        self.assertEqual(len([c for c in self.calls if c[0]=='copy']),1)
    def test_clean_copies_physical_folder_outside_manifest(self):
        self.source_manifest('20261020_2041')
        self.existing_state()
        directory=self.source/'20261019_0600';directory.mkdir()
        (directory/'lap_form_data.csv').write_text('a,b\n1,2')
        with patch.object(u,'list_folders',side_effect=self.folders_list),patch.object(u,'rclone',side_effect=self.command):u.once(10,True)
        self.assertEqual(len([c for c in self.calls if c[0]=='copy']),2)
        self.assertIn('20261019_0600',u.load_state()['activities'])
    def test_clean_missing_local_is_skipped_and_history_preserved(self):
        self.source_manifest('20261020_2041')
        self.existing_state('20261020_2041','20261017_1425')
        manifest=json.loads((self.source/'manifest.json').read_text())
        manifest['activities']['20261017_1425']={}
        u.atomic_json(self.source/'manifest.json',manifest)
        with patch.object(u,'list_folders',side_effect=self.folders_list),patch.object(u,'rclone',side_effect=self.command):u.once(10,True)
        self.assertIn('20261017_1425',u.load_state()['activities'])
        self.assertEqual(len([c for c in self.calls if c[0]=='copy']),1)
    def test_failed_clean_upload_does_not_prune_or_record_success(self):
        self.source_manifest('20261020_2041');self.existing_state()
        with patch.object(u,'list_folders',side_effect=self.folders_list),patch.object(u,'rclone',side_effect=RuntimeError('offline')),patch.object(u,'prune_local') as prune:
            with self.assertRaises(RuntimeError):u.once(10,True)
        prune.assert_not_called()
        self.assertNotIn('20261020_2041',u.load_state()['activities'])
    def test_normal_detects_physical_extra_folders_for_counting(self):
        self.source_manifest('20261020_2041')
        d=self.source/'20261001_0600';d.mkdir();(d/'lap_form_data.csv').write_text('a,b\n1,2')
        self.assertEqual(len(u.source_activities()),2)

class CleanRdmTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.root_patch=patch.object(runner,'ROOT',self.root);self.root_patch.start()
        self.target=self.root/'20261020_2041'
        self.archive=self.root/'download.zip'
    def tearDown(self):self.root_patch.stop();self.tmp.cleanup()
    def write_zip(self,invalid=False):
        with zipfile.ZipFile(self.archive,'w') as z:
            for kind in ('data','score'):
                content='<html>bad' if invalid else 'lap,value\n1,2\n'
                z.writestr(f'lap_form_{kind}-202610202041.csv',content)
    def test_clean_never_skips_known_key(self):
        manifest={'activities':{'20261020_2041':{}}}
        self.assertTrue(runner.should_skip('20261020_2041',manifest,False))
        self.assertFalse(runner.should_skip('20261020_2041',manifest,True))
    def test_clean_replaces_old_export_with_exact_three_original_named_files(self):
        self.target.mkdir();(self.target/'lap_form_data.csv').write_text('old')
        (self.target/'old.zip').write_bytes(b'old')
        self.write_zip();zip_bytes=self.archive.read_bytes()
        result=runner.save_download(self.archive,self.target,True)
        self.assertEqual(len(result),2)
        self.assertEqual({p.name for p in self.target.iterdir()},{'lap_form_data-202610202041.csv','lap_form_score-202610202041.csv','202610202041.zip'})
        self.assertEqual((self.target/'202610202041.zip').read_bytes(),zip_bytes)
    def test_clean_creates_export_if_local_absent(self):
        self.write_zip();runner.save_download(self.archive,self.target,True)
        self.assertEqual(len(list(self.target.iterdir())),3)
    def test_invalid_new_download_leaves_previous_export_untouched(self):
        self.target.mkdir();(self.target/'lap_form_data.csv').write_text('keep')
        self.write_zip(True)
        with self.assertRaises(runner.SyncError):runner.save_download(self.archive,self.target,True)
        self.assertEqual((self.target/'lap_form_data.csv').read_text(),'keep')
    def test_directory_swap_failure_restores_previous_export(self):
        self.target.mkdir();(self.target/'lap_form_data.csv').write_text('keep');self.write_zip()
        rename=Path.rename
        def fail_stage(path,target):
            if path.parent.name.startswith('.rdm_refresh_') and path.name=='20261020_2041':
                raise OSError('disk error')
            return rename(path,target)
        with patch.object(Path,'rename',fail_stage),self.assertRaises(OSError):runner.save_download(self.archive,self.target,True)
        self.assertEqual((self.target/'lap_form_data.csv').read_text(),'keep')
    def test_shared_lock_is_held_during_processing(self):
        @runner.exclusive_sync
        def probe():
            with (self.root/'.sync.lock').open('a') as handle:
                with self.assertRaises(BlockingIOError):fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
        probe()

if __name__=='__main__':unittest.main()
