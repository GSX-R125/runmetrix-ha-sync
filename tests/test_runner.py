import importlib.util
import tempfile
import unittest
import zipfile
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
            self.assertEqual(set(result), runner.REQUIRED)
            self.assertEqual({p.name for p in dest.iterdir()}, runner.REQUIRED)


if __name__ == "__main__":
    unittest.main()
