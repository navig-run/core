"""NAVIG Tray Launcher (windowless entry point). Launch with pythonw.exe for no console."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts.navig_tray import main

if __name__ == "__main__":
    main()
