#!/usr/bin/env python3
"""
Manual test for init functionality.
"""

import sys
import os
from pathlib import Path
import tempfile
import shutil

# Add app to path
sys.path.insert(0, str(Path(__file__).parent))

from navig.commands.init import init_app
from navig import console_helper as ch

def test_init():
    """Test app initialization."""
    # Create temp directory
    test_dir = Path(tempfile.mkdtemp(prefix="navig-test-"))
    print(f"\n{'='*70}")
    print(f"Test directory: {test_dir}")
    print(f"{'='*70}\n")
    
    try:
        # Change to test directory
        original_dir = Path.cwd()
        os.chdir(test_dir)
        
        # Test 1: Initialize app
        print("TEST 1: Initialize app")
        print("-" * 70)
        init_app({'copy_global': False, 'quiet': False, 'yes': True})
        
        # Check if .navig was created
        navig_dir = test_dir / ".navig"
        if navig_dir.exists():
            ch.success("✓ .navig directory created")
            
            # Check subdirectories
            subdirs = ['hosts', 'apps', 'cache', 'backups']
            for subdir in subdirs:
                subdir_path = navig_dir / subdir
                if subdir_path.exists():
                    ch.success(f"✓ {subdir}/ directory created")
                else:
                    ch.error(f"✗ {subdir}/ directory NOT created", "")
            
            # Check config.yaml
            config_file = navig_dir / "config.yaml"
            if config_file.exists():
                ch.success("✓ config.yaml created")
                print(f"\nConfig content:\n{config_file.read_text()}")
            else:
                ch.error("✗ config.yaml NOT created", "")
        else:
            ch.error("✗ .navig directory NOT created", "")
        
        print("\n" + "="*70)
        
        # Test 2: Try to initialize again (should fail)
        print("\nTEST 2: Try to re-initialize (should fail)")
        print("-" * 70)
        init_app({'copy_global': False, 'quiet': False, 'yes': True})
        print("\n" + "="*70)
        
        # Test 3: App root detection from subdirectory
        print("\nTEST 3: App root detection from subdirectory")
        print("-" * 70)
        subdir = test_dir / "src" / "components"
        subdir.mkdir(parents=True, exist_ok=True)
        os.chdir(subdir)
        
        from navig.config import ConfigManager
        config = ConfigManager(verbose=True)
        
        if config.base_dir == navig_dir:
            ch.success(f"✓ App root detected correctly: {config.base_dir}")
        else:
            ch.error(
                f"✗ App root detection failed",
                f"Expected: {navig_dir}\nGot: {config.base_dir}"
            )
        
        print("\n" + "="*70)
        print("All tests complete!")
        print("="*70 + "\n")
        
    finally:
        # Cleanup
        os.chdir(original_dir)
        if test_dir.exists():
            shutil.rmtree(test_dir)
            print(f"Cleaned up test directory: {test_dir}")

if __name__ == "__main__":
    test_init()
