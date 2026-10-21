import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('u',Path(__file__).parent.parent/'runmetrix_drive_sync'/'upload_by_month.py')
u=importlib.util.module_from_spec(spec)
spec.loader.exec_module(u)

class UploadTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)
        self.source=self.root/'source'
        self.source.mkdir()
        self.state=self.root/'state.json'
        self.a=patch.object(u,'SOURCE',self.source); self.a.start()
        self.b=patch.object(u,'STATE',self.state); self.b.start()
        self.verifier=patch.object(u,'verify_backup',side_effect=self.mock_verify);self.verifier.start()
        self.calls=[]
        self.folders={'':{'2026'},'2026':{'10'},'2027':set()}
    def mock_verify(self,directory,relative,key,before):
        u.rclone('check',str(directory),u.REMOTE+relative,'--download')
    def tearDown(self):
        self.verifier.stop()
        self.a.stop();self.b.stop();self.tmp.cleanup()
    def source_manifest(self,*keys):
        activities={}
        for key in keys:
            (self.source/key).mkdir(exist_ok=True)
            (self.source/key/'lap_form_data.csv').write_text('a,b\n1,2')
            activities[key]={'rdm_datetime':key}
        u.atomic_json(self.source/'manifest.json',{'schema':1,'activities':activities})
    def folders_list(self,path): return self.folders[path]
    def command(self,*args):
        self.calls.append(args)
        if args[0]=='mkdir':
            path=args[1].removeprefix(u.REMOTE)
            parts=path.split('/')
            parent='/'.join(parts[:-1])
            self.folders.setdefault(parent,set()).add(parts[-1])
            self.folders.setdefault(path,set())
        return ''
    def existing_state(self,*keys):
        u.atomic_json(self.state,{'schema':1,'root_folder_id':u.ROOT_ID,
          'activities':{k:{'status':'baseline_existing','relative_path':u.activity_path(k)} for k in keys}})
    def test_path_year_and_month(self):
        self.assertEqual(u.activity_path('20270101_0600'),'2027/01/20270101_0600')
    def test_invalid_date_rejected(self):
        for key in ['20261301_0600','../../bad','20260230_0600']:
            with self.assertRaises(ValueError):u.activity_path(key)
    def test_initial_run_uploads_local_without_assuming_backup_exists(self):
        key='20261008_1800'
        self.source_manifest(key)
        with patch.object(u,'list_folders',side_effect=self.folders_list),patch.object(u,'rclone',side_effect=self.command):u.once()
        self.assertEqual(len([c for c in self.calls if c[0]=='copy']),1)
        self.assertEqual(u.load_state()['activities'][key]['status'],'uploaded')
    def test_existing_month_reused_and_restart_skips(self):
        self.existing_state('20261008_1800')
        self.source_manifest('20261008_1800','20261009_1800')
        with patch.object(u,'list_folders',side_effect=self.folders_list),patch.object(u,'rclone',side_effect=self.command):
            u.once();u.once()
        self.assertEqual(len([c for c in self.calls if c[0]=='copy']),1)
        self.assertFalse([c for c in self.calls if c[0]=='mkdir'])
    def test_new_year_and_month_created_in_order(self):
        self.existing_state()
        self.source_manifest('20270101_0600')
        with patch.object(u,'list_folders',side_effect=self.folders_list),patch.object(u,'rclone',side_effect=self.command):u.once()
        self.assertEqual(self.calls[0],('mkdir','runmetrix_drive:2027'))
        self.assertEqual(self.calls[1],('mkdir','runmetrix_drive:2027/01'))
        self.assertEqual(self.calls[2][2],'runmetrix_drive:2027/01/20270101_0600')
    def test_new_month_only(self):
        self.existing_state()
        self.source_manifest('20261101_0600')
        with patch.object(u,'list_folders',side_effect=self.folders_list),patch.object(u,'rclone',side_effect=self.command):u.once()
        self.assertEqual(self.calls[0],('mkdir','runmetrix_drive:2026/11'))
    def test_initial_run_failed_listing_does_not_record_success(self):
        self.source_manifest('20261008_1800')
        with patch.object(u,'list_folders',side_effect=RuntimeError('権限なし')),patch.object(u,'rclone',side_effect=self.command):
            with self.assertRaises(RuntimeError):u.once()
        self.assertFalse(self.state.exists());self.assertFalse(self.calls)
    def test_failed_copy_is_not_marked_and_retries(self):
        self.existing_state()
        self.source_manifest('20261009_1800')
        def fail(*args):
            if args[0]=='copy':raise RuntimeError('failure')
            return ''
        with patch.object(u,'list_folders',side_effect=self.folders_list),patch.object(u,'rclone',side_effect=fail):
            with self.assertRaises(RuntimeError):u.once()
        self.assertNotIn('20261009_1800',u.load_state()['activities'])
        with patch.object(u,'list_folders',side_effect=self.folders_list),patch.object(u,'rclone',side_effect=self.command):u.once()
        self.assertEqual(u.load_state()['activities']['20261009_1800']['status'],'uploaded')


class RetentionTests(unittest.TestCase):
    mock_verify=UploadTests.mock_verify
    setUp=UploadTests.setUp
    tearDown=UploadTests.tearDown
    source_manifest=UploadTests.source_manifest
    existing_state=UploadTests.existing_state
    command=UploadTests.command
    def prepare(self):
        keys=[f'202610{i:02d}_0600' for i in range(1,14)]
        self.source_manifest(*keys)
        self.existing_state(*keys)
        self.state_obj=u.load_state()
        return keys
    def test_retention_keeps_latest_ten_and_manifest(self):
        keys=self.prepare()
        manifest_before=(self.source/'manifest.json').read_bytes()
        with patch.object(u,'list_folders',return_value=set(keys)),patch.object(u,'rclone',side_effect=self.command):
            u.prune_local(self.state_obj,u.source_activities(self.state_obj),10)
        remaining=sorted(p.name for p in self.source.iterdir() if p.is_dir())
        self.assertEqual(remaining,keys[3:])
        self.assertEqual((self.source/'manifest.json').read_bytes(),manifest_before)
        self.assertEqual(len(u.source_activities(u.load_state())),10)
        self.assertTrue(u.load_state()['activities'][keys[0]]['local_pruned'])
        self.assertFalse(any(c[0] in ('delete','purge','sync','move') for c in self.calls))
    def test_backup_mismatch_preserves_all(self):
        keys=self.prepare()
        def fail(*args):raise RuntimeError('mismatch')
        with patch.object(u,'list_folders',return_value=set(keys)),patch.object(u,'rclone',side_effect=fail):
            u.prune_local(self.state_obj,u.source_activities(self.state_obj),10)
        self.assertTrue(all((self.source/k).is_dir() for k in keys))
        self.assertFalse(any(e.get('local_prune_started') for e in u.load_state()['activities'].values()))
    def test_missing_remote_preserves_all_without_upload(self):
        keys=self.prepare()
        with patch.object(u,'list_folders',return_value=set()),patch.object(u,'rclone',side_effect=self.command):
            u.prune_local(self.state_obj,u.source_activities(self.state_obj),10)
        self.assertFalse(self.calls)
        self.assertTrue(all((self.source/k).is_dir() for k in keys))
    def test_changed_local_during_check_preserved(self):
        keys=self.prepare()
        def mutate(*args):
            if args[0]=='check':(Path(args[1])/'lap_form_data.csv').write_text('changed')
            return ''
        with patch.object(u,'list_folders',return_value=set(keys)),patch.object(u,'rclone',side_effect=mutate):
            u.prune_local(self.state_obj,u.source_activities(self.state_obj),10)
        self.assertTrue(all((self.source/k).is_dir() for k in keys))
    def test_symlink_is_not_deleted(self):
        keys=self.prepare()
        outside=self.root/'outside.csv';outside.write_text('keep')
        (self.source/keys[0]/'linked.csv').symlink_to(outside)
        with patch.object(u,'list_folders',return_value=set(keys)),patch.object(u,'rclone',side_effect=self.command):
            u.prune_local(self.state_obj,u.source_activities(self.state_obj),10)
        self.assertTrue((self.source/keys[0]).is_dir());self.assertTrue(outside.exists())
    def test_missing_unpruned_directory_rejected(self):
        self.source_manifest('20261009_0600')
        import shutil
        shutil.rmtree(self.source/'20261009_0600')
        with self.assertRaises(RuntimeError):u.source_activities(None)
    def test_invalid_retention_rejected(self):
        for keep in [0,-1,True,'10']:
            with self.assertRaises(RuntimeError):u.prune_local({}, {}, keep)

    def test_timestamped_csv_retention_and_verification(self):
        keys=self.prepare()
        for key in keys:
            directory=self.source/key
            (directory/'lap_form_data.csv').rename(directory/f'lap_form_data-{key.replace("_", "")}.csv')
            (directory/f'lap_form_score-{key.replace("_", "")}.csv').write_text('a,b\n1,2')
            (directory/f'{key.replace("_", "")}.zip').write_bytes(b'archive')
        with patch.object(u,'list_folders',return_value=set(keys)),patch.object(u,'rclone',side_effect=self.command):
            u.prune_local(self.state_obj,u.source_activities(self.state_obj),10)
        self.assertEqual(sorted(p.name for p in self.source.iterdir() if p.is_dir()),keys[3:])
        self.assertTrue(any(c[0]=='check' and '--download' in c for c in self.calls))

if __name__=='__main__': unittest.main()
