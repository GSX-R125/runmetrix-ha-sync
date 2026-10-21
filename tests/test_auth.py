import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from test_runner import runner

class AuthTests(unittest.TestCase):
    def test_login_location_does_not_log_auth_query(self):
        class Page:
            url = "https://id.asics.com/login?state=private-token&email=person@example.org"

        self.assertEqual(runner.login_location(Page()), "OneASICS/login")

    def test_login_diagnostics_keeps_input_values_out(self):
        class Body:
            def inner_text(self, **kwargs):
                return "メールアドレス person@example.org"

        class Page:
            url = "https://id.asics.com/login?state=private-token"

            def evaluate(self, expression):
                return "complete"

            def locator(self, selector):
                return Body()

        result = runner.login_diagnostics(Page(), ["id.asics.com xhr ERR_CONNECTION_RESET"], ["id.asics.com HTTP 200"])
        self.assertIn("ERR_CONNECTION_RESET", result)
        self.assertIn("文書応答: id.asics.com HTTP 200", result)
        self.assertNotIn("private-token", result)
        self.assertNotIn("person@example.org", result)

    def test_missing_auth_state_logs_in_before_opening_activities(self):
        with patch.object(runner, "activity_page") as activity, \
             patch.object(runner, "sign_in") as login, \
             patch.object(runner, "save_auth_state") as save:
            runner.open_authenticated_activity(object(), object(), ("user@example.org", "secret"), False)
        activity.assert_not_called()
        login.assert_called_once()
        save.assert_called_once()

    def test_expired_auth_state_logs_in_once(self):
        with patch.object(runner, "activity_page", side_effect=runner.AuthRequired("expired")) as activity, \
             patch.object(runner, "sign_in") as login, \
             patch.object(runner, "save_auth_state") as save:
            runner.open_authenticated_activity(object(), object(), ("user@example.org", "secret"), True)
        activity.assert_called_once()
        login.assert_called_once()
        save.assert_called_once()

    def test_invalid_credentials_do_not_leak_password(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rdm_credentials.json"
            path.write_text('{"email": "person@example.org", "password": ""}', encoding="utf-8")
            with patch.object(runner, "CREDENTIALS", path):
                with self.assertRaises(runner.SyncError) as caught:
                    runner.read_credentials()
            self.assertNotIn("person@example.org", str(caught.exception))
