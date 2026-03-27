import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# We need to mock user_profile import since it uses global state and paths
from navig.workspace import WorkspaceManager


class TestUserPreferencesIntegration(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.workspace_path = Path(self.test_dir) / "workspace"
        self.workspace_path.mkdir()

        # Create a mock USER.md
        self.user_md_path = self.workspace_path / "USER.md"
        self.user_md_content = """# User Profile

- **Name**: Test User
- **Timezone**: America/New_York
- **Work Hours**: 09:00-17:00
- **Primary Languages**: Python, Rust
- **Communication Style**: concise
- **Risk Tolerance**: high
"""
        self.user_md_path.write_text(self.user_md_content, encoding="utf-8")

        # Setup WorkspaceManager with test path
        self.wm = WorkspaceManager(workspace_path=self.workspace_path)

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def test_get_user_preferences_parsing(self):
        """Test robust parsing of USER.md fields."""
        prefs = self.wm.get_user_preferences()

        self.assertEqual(prefs["name"], "Test User")
        self.assertEqual(prefs["timezone"], "America/New_York")
        self.assertEqual(prefs["work_hours"], "09:00-17:00")
        self.assertEqual(prefs["primary_languages"], ["Python", "Rust"])
        self.assertEqual(prefs["communication_style"], "concise")
        self.assertEqual(prefs["risk_tolerance"], "high")

    @patch("navig.memory.user_profile.get_profile")
    def test_sync_to_user_profile(self, mock_get_profile):
        """Test syncing preferences to UserProfile object."""
        # Mock profile object
        mock_profile = MagicMock()
        mock_profile.update.return_value = ["identity.name", "technical_context.stack"]
        mock_get_profile.return_value = mock_profile

        # Run sync
        result = self.wm.sync_to_user_profile()

        # Verify sync happened
        self.assertTrue(result)

        # Verify update was called with correct data
        mock_profile.update.assert_called_once()
        call_args = mock_profile.update.call_args[0][0]

        self.assertEqual(call_args["identity.name"], "Test User")
        self.assertEqual(call_args["identity.timezone"], "America/New_York")
        self.assertEqual(call_args["technical_context.stack"], ["Python", "Rust"])
        self.assertEqual(call_args["preferences.communication_style"], "concise")

    def test_is_do_not_disturb_parsing(self):
        """Test complex DND time parsing."""
        # Update USER.md with tricky DND time
        vm_dnd_content = """# User
- **Do Not Disturb**: 11 PM – 7 AM
"""
        self.user_md_path.write_text(vm_dnd_content, encoding="utf-8")

        # We can't easily test "is_do_not_disturb" return value without mocking datetime
        # But we can verify it doesn't crash on parsing

        try:
            self.wm.is_do_not_disturb()
        except Exception as e:
            self.fail(f"is_do_not_disturb raised exception: {e}")


if __name__ == "__main__":
    unittest.main()
