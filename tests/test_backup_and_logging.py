import io
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch
from test_upload_by_month import u

class BackupTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)
        self.local=self.root/'local';self.local.mkdir()
        self.remote=self.root/'remote';self.remote.mkdir()
        self.key='20260926_1529'
        (self.local/'lap_form_data.csv').write_bytes(b'data')
        (self.local/'lap_form_score.csv').write_bytes(b'score')
        (self.local/'202609261529.zip').write_bytes(b'zip')
        (self.remote/'lap_form_data-202609261529.csv').write_bytes(b'data')
        (self.remote/'lap_form_score-202609261529.csv').write_bytes(b'score')
        (self.remote/'original.zip').write_bytes(b'zip')
        self.commands=[]
    def tearDown(self):self.tmp.cleanup()
    def command(self,*args):
        self.commands.append(args)
        if args[0]=='copy':
            for p in self.remote.iterdir():shutil.copy2(p,Path(args[2])/p.name)
        elif args[0]=='copyto':
            target=self.remote/args[2].rsplit('/',1)[-1]
            if not target.exists():shutil.copy2(args[1],target)
        else:raise AssertionError(args)
        return ''
    def verify(self):
        with patch.object(u,'rclone',side_effect=self.command):
            u.verify_backup(self.local,'2026/09/'+self.key,self.key,u.local_snapshot(self.local))
    def test_legacy_local_timestamped_remote_byte_match(self):
        self.verify()
        self.assertFalse(any(c[0]=='copyto' for c in self.commands))
        self.assertEqual(len(list(self.remote.iterdir())),3)
    def test_reverse_timestamped_local_legacy_remote(self):
        for kind in ('data','score'):
            (self.local/f'lap_form_{kind}.csv').rename(self.local/f'lap_form_{kind}-202609261529.csv')
            (self.remote/f'lap_form_{kind}-202609261529.csv').rename(self.remote/f'lap_form_{kind}.csv')
        self.verify()
    def test_changed_csv_not_overwritten(self):
        (self.remote/'lap_form_data-202609261529.csv').write_bytes(b'wrong')
        with self.assertRaisesRegex(RuntimeError,'内容不一致: lap_form_data.csv'):self.verify()
        self.assertFalse(any(c[0]=='copyto' for c in self.commands))
    def test_exact_name_conflict_even_if_alias_matches(self):
        (self.remote/'lap_form_data.csv').write_bytes(b'bad')
        with self.assertRaisesRegex(RuntimeError,'内容不一致'):self.verify()
    def test_missing_zip_filled_then_downloaded_again(self):
        (self.remote/'original.zip').unlink()
        self.verify()
        self.assertEqual((self.remote/'202609261529.zip').read_bytes(),b'zip')
        self.assertEqual(len([c for c in self.commands if c[0]=='copy']),2)
    def test_failed_upload_preserves_local(self):
        (self.remote/'original.zip').unlink()
        def fail(*args):
            if args[0]=='copyto':raise RuntimeError('Drive操作 copyto に失敗')
            return self.command(*args)
        with patch.object(u,'rclone',side_effect=fail),self.assertRaises(RuntimeError):
            u.verify_backup(self.local,'2026/09/'+self.key,self.key,u.local_snapshot(self.local))
        self.assertTrue((self.local/'202609261529.zip').exists())
    def test_race_existing_wrong_zip_detected(self):
        (self.remote/'original.zip').unlink()
        def race(*args):
            if args[0]=='copyto':(self.remote/'202609261529.zip').write_bytes(b'wrong')
            return self.command(*args)
        with patch.object(u,'rclone',side_effect=race),self.assertRaisesRegex(RuntimeError,'最終照合不一致'):
            u.verify_backup(self.local,'2026/09/'+self.key,self.key,u.local_snapshot(self.local))
    def test_other_activity_timestamp_not_alias(self):
        self.assertEqual(u.equivalent_names('lap_form_data.csv',{'lap_form_data-202609271529.csv':'x'},self.key),[])
    def test_retention_real_bytes_leaves_latest_ten(self):
        source=self.root/'source';source.mkdir()
        keys=[f'202610{i:02d}_0600' for i in range(1,15)]
        state={'activities':{k:{'status':'baseline_existing','relative_path':u.activity_path(k)} for k in keys}}
        for k in keys:
            d=source/k;d.mkdir();(d/'lap_form_data.csv').write_bytes(b'data')
        def remote_copy(*args):
            if args[0]!='copy':raise AssertionError(args)
            k=args[1].rsplit('/',1)[-1]
            (Path(args[2])/f'lap_form_data-{k.replace("_", "")}.csv').write_bytes(b'data')
            return ''
        with patch.object(u,'SOURCE',source),patch.object(u,'STATE',self.root/'state.json'),patch.object(u,'list_folders',return_value=set(keys)),patch.object(u,'rclone',side_effect=remote_copy):
            self.assertEqual(u.prune_local(state,dict.fromkeys(keys),10),0)
        self.assertEqual(sorted(p.name for p in source.iterdir()),keys[4:])

class LoggingTests(unittest.TestCase):
    def cycle(self,effect=None,result=None):
        output=io.StringIO()
        now=datetime(2026,10,21,9,40,tzinfo=timezone(timedelta(hours=9)))
        with patch.object(u,'CONFIG') as config,patch.object(u,'once',side_effect=effect,return_value=result),patch.object(u,'jst_now',return_value=now),redirect_stdout(output):
            config.is_file.return_value=True
            delay=u.run_cycle(10,2)
        return delay,output.getvalue()
    def test_success_start_end_next_jst(self):
        delay,log=self.cycle()
        self.assertEqual(delay,7200)
        for text in ['2026/10/21 09:40 同期処理実行開始','2026/10/21 09:40 同期処理終了（正常終了）','次回実行予定 2時間後 / 2026/10/21 11:40（日本時間）']:
            self.assertIn(text,log)
    def test_failure_still_has_end_and_retry(self):
        delay,log=self.cycle(RuntimeError('failed'))
        self.assertEqual(delay,300)
        self.assertIn('同期処理終了（エラー終了）',log)
        self.assertIn('5分後（再試行） / 2026/10/21 09:45',log)
    def test_pending_not_reported_as_success(self):
        _,log=self.cycle(result=4)
        self.assertIn('同期処理終了（照合保留 4活動）',log)
    def test_jst_fixed_independent_of_host_timezone(self):
        self.assertEqual(u.jst_now().utcoffset(),timedelta(hours=9))

if __name__=='__main__':unittest.main()
