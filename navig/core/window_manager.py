"""
Window Manager Module

Handles higher-level window management, layout saving/restoring, and dashboard data.
"""

import json
from dataclasses import asdict
from pathlib import Path

from navig.console_helper import error, info, warning


class WindowManager:
    def __init__(self, ahk_adapter):
        self.ahk = ahk_adapter
        self.layout_dir = Path.home() / ".navig" / "layouts"
        self.layout_dir.mkdir(parents=True, exist_ok=True)

    def get_windows(self):
        """Get current window list."""
        if not self.ahk:
            return []
        return self.ahk.get_all_windows()

    def save_layout(self, name: str):
        """Save current window positions."""
        if not self.ahk:
            error("AHK adapter not available")
            return

        windows = self.get_windows()
        layout_data = []
        for w in windows:
            # Filter out system windows or unlikely targets
            if (
                w.title
                and w.title != "Program Manager"
                and w.width > 0
                and w.height > 0
            ):
                layout_data.append(asdict(w))

        file_path = self.layout_dir / f"{name}.json"
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(layout_data, f, indent=2)
        info(f"Saved layout '{name}' with {len(layout_data)} windows")

    def restore_layout(self, name: str):
        """Restore window positions from layout."""
        if not self.ahk:
            error("AHK adapter not available")
            return

        file_path = self.layout_dir / f"{name}.json"
        if not file_path.exists():
            error(f"Layout '{name}' not found")
            return

        try:
            with open(file_path, encoding="utf-8") as f:
                layout_data = json.load(f)
        except Exception as e:
            error(f"Failed to load layout: {e}")
            return

        info(f"Restoring layout '{name}'...")

        # Optimized restoration
        # We fetch current windows once to match against
        current_windows = self.get_windows()

        for saved_win in layout_data:
            target_title = saved_win.get("title")
            target_class = saved_win.get("class_name")
            target_proc = saved_win.get("process_name")

            if not target_title:
                continue

            # Find best match in current windows
            match = None
            for cur in current_windows:
                # Perfect match
                if cur.title == target_title:
                    match = cur
                    break
                # Process + Class match (fallback if title changed)
                if cur.process_name == target_proc and cur.class_name == target_class:
                    match = cur
                    # Don't break, prefer exact title match later? No, this is imprecise.
                    # Let's stick to title match first, fallback to class/process if unique?
                    # Too complex for now. Title matching is primary.

            if match:
                # Restore state
                selector = f"ahk_id {match.id}"

                if saved_win.get("is_maximized"):
                    self.ahk.maximize_window(selector)
                elif saved_win.get("is_minimized"):
                    self.ahk.minimize_window(selector)
                else:
                    # Restore position
                    x, y, w, h = (
                        saved_win["x"],
                        saved_win["y"],
                        saved_win["width"],
                        saved_win["height"],
                    )

                    # Ensure window is restored (not minimized/maximized) before moving
                    if match.is_maximized or match.is_minimized:
                        self.ahk.restore_window(selector)

                    self.ahk.move_window(selector, x, y, width=w, height=h)
            else:
                warning(f"Window not found for restore: {target_title}")

    def list_layouts(self) -> list[str]:
        """List available layouts."""
        return [f.stem for f in self.layout_dir.glob("*.json")]
