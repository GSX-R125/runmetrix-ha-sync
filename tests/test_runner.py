import importlib.util
import tempfile
import unittest
import zipfile
import hashlib
from pathlib import Path


RUNNER = Path(__file__).parent.parent / "runmetrix_sync" / "runner.py"
spec = importlib.util.spec_from_file_location("runner", RUNNER)
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


class ExportTests(unittest.TestCase):
    def test_zip_extraction_and_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            zpath = root / "export.zip"
            with zipfile.ZipFile(zpath, "w") as z:
                z.writestr("folder/lap_form_data.csv", "lap,value\n1,2\n")
                z.writestr("folder/lap_form_score.csv", "lap,score\n1,70\n")
                z.writestr("folder/lap_heart_rate_data.csv", "time,hr\n0,120\n")
                z.writestr("../../other.txt", "ignored")
            dest = root / runner.key_from_date("2026/09/22 18:59")
            result = runner.extract_csv(zpath, dest)
            self.assertEqual(set(result), runner.KNOWN)
            self.assertEqual(sorted(p.name for p in dest.iterdir()), sorted(runner.KNOWN))
            self.assertFalse((root / "other.txt").exists())

    def test_no_partial_write_when_required_csv_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            zpath = root / "bad.zip"
            with zipfile.ZipFile(zpath, "w") as z:
                z.writestr("lap_form_data.csv", "lap,value\n1,2\n")
            with self.assertRaises(runner.SyncError):
                runner.extract_csv(zpath, root / "dest")
            self.assertFalse((root / "dest").exists())

    def test_timestamped_form_csv_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            zpath = root / "202609221859.zip"
            with zipfile.ZipFile(zpath, "w") as z:
                z.writestr("lap_form_data-202609221859.csv", "lap,value\n1,2\n")
                z.writestr("lap_form_score-202609221859.csv", "lap,score\n1,70\n")
            dest = root / "20260922_1859"
            result = runner.extract_csv(zpath, dest)
            names = {"lap_form_data-202609221859.csv", "lap_form_score-202609221859.csv"}
            self.assertEqual(set(result), names)
            self.assertEqual({p.name for p in dest.iterdir()}, names)
            before = zpath.read_bytes()
            zpath.replace(dest / zpath.name)
            self.assertEqual(len(list(dest.iterdir())), 3)
            self.assertEqual((dest / zpath.name).read_bytes(), before)
            for name in names:
                self.assertEqual(result[name], hashlib.sha256((dest / name).read_bytes()).hexdigest())

    def test_wrong_activity_timestamp_rejected_before_write(self):
        self.reject_export([
            ('lap_form_data-202610122029.csv', 'lap,value\n1,2\n'),
            ('lap_form_score-202610122029.csv', 'lap,score\n1,70\n')])

    def test_duplicate_csv_kind_rejected_before_write(self):
        self.reject_export([
            ('lap_form_data.csv', 'lap,value\n1,2\n'),
            ('lap_form_data-202609221859.csv', 'lap,value\n1,2\n'),
            ('lap_form_score.csv', 'lap,score\n1,70\n')])

    def test_invalid_content_rejected_before_write(self):
        self.reject_export([
            ('lap_form_data-202609221859.csv', 'lap,value\n1,2\n'),
            ('lap_form_score-202609221859.csv', '<html>error</html>')])

    def reject_export(self, members):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            zpath = root / 'export.zip'
            with zipfile.ZipFile(zpath, 'w') as z:
                for name, data in members:
                    z.writestr(name, data)
            dest = root / '20260922_1859'
            with self.assertRaises(runner.SyncError):
                runner.extract_csv(zpath, dest)
            self.assertFalse(dest.exists())

    def test_existing_legacy_csv_not_duplicated_or_deleted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            zpath = root / 'export.zip'
            dest = root / '20260922_1859'
            dest.mkdir()
            legacy = dest / 'lap_form_data.csv'
            legacy.write_text('keep')
            with zipfile.ZipFile(zpath, 'w') as z:
                for kind in ('data', 'score'):
                    z.writestr(f'lap_form_{kind}-202609221859.csv', 'lap,value\n1,2\n')
            with self.assertRaises(runner.SyncError):
                runner.extract_csv(zpath, dest)
            self.assertEqual(legacy.read_text(), 'keep')
            self.assertEqual(len(list(dest.iterdir())), 1)


if __name__ == "__main__":
    unittest.main()
