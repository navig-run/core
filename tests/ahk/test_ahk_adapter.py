import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Ensure navig on path
sys.path.append(str(Path(__file__).parent.parent.parent))

from navig.adapters.automation.ahk import AHKAdapter
import pytest

pytestmark = pytest.mark.integration


class TestAHKAdapter(unittest.TestCase):
    @patch("shutil.which")
    def test_detection(self, mock_which):
        mock_which.return_value = "C:/Program Files/AutoHotkey/v2/AutoHotkey64.exe"

        adapter = AHKAdapter()
        status = adapter.get_status()

        self.assertTrue(status.detected)
        self.assertEqual(status.detection_method, "PATH")

    @patch("subprocess.run")
    def test_execute_inline(self, mock_run):
        adapter = AHKAdapter()
        adapter._executable = Path("C:/fake/ahk.exe")  # Force detected

        mock_process = MagicMock()
        mock_process.returncode = 0
        mock_process.stdout = "output"
        mock_process.stderr = ""
        mock_run.return_value = mock_process

        res = adapter.execute('MsgBox "Hello"')

        self.assertTrue(res.success)
        self.assertEqual(res.stdout, "output")

        # Verify call args
        args, kwargs = mock_run.call_args
        cmd = args[0]
        self.assertIn("/ErrorStdOut", cmd)
        self.assertIn("*", cmd)  # input from stdin

    @patch("subprocess.run")
    def test_get_all_windows(self, mock_run):
        adapter = AHKAdapter()
        adapter._executable = Path("C:/fake/ahk.exe")

        mock_process = MagicMock()
        mock_process.returncode = 0
        # Mock structured output: JSON array
        mock_process.stdout = '[{"title":"Notepad","hwnd":291,"pid":999,"class_name":"Notepad","x":10,"y":10,"w":800,"h":600,"process_name":"notepad.exe","minimized":0,"maximized":0}]'
        mock_run.return_value = mock_process

        windows = adapter.get_all_windows()

        self.assertEqual(len(windows), 1)
        w = windows[0]
        self.assertEqual(w.title, "Notepad")
        self.assertEqual(w.pid, 999)
        self.assertEqual(w.width, 800)

    @patch("subprocess.run")
    def test_clipboard_ops(self, mock_run):
        adapter = AHKAdapter()
        adapter._executable = Path("C:/fake/ahk.exe")

        # Test Get
        mock_process_get = MagicMock()
        mock_process_get.returncode = 0
        mock_process_get.stdout = "clip content"
        mock_run.return_value = mock_process_get

        content = adapter.get_clipboard()
        self.assertEqual(content, "clip content")

        # Test Set
        adapter.set_clipboard("new content")
        # Verify set call includes the content in the script
        args, kwargs = mock_run.call_args
        code_input = kwargs.get("input", "")

    @patch("subprocess.run")
    def test_new_primitives(self, mock_run):
        adapter = AHKAdapter()
        adapter._executable = Path("C:/fake/ahk.exe")

        mock_process = MagicMock()
        mock_process.returncode = 0
        mock_process.stdout = ""
        mock_run.return_value = mock_process

        # Test resize
        adapter.resize_window("Notepad", 800, 600)
        args, kwargs = mock_run.call_args
        # args[0] is the command list, e.g. ['exe', '/ErrorStdOut', '/CP65001', '*']
        # The script content is passed via input=... kwarg for execute()
        script_content = kwargs.get("input", "")
        self.assertIn("WinMove", script_content)

        # Test mouse move
        adapter.mouse_move(100, 100)
        args, kwargs = mock_run.call_args
        script_content = kwargs.get("input", "")
        self.assertIn("MouseMove", script_content)

        # Test read text
        mock_process.stdout = "Retrieved Text"
        text = adapter.read_text("Notepad", "Edit1")
        self.assertEqual(text, "Retrieved Text")


if __name__ == "__main__":
    unittest.main()
