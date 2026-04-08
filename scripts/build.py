#!/usr/bin/env python3
"""
NAVIG Build Script

Automated build system for creating single-binary distributions using
PyInstaller or Nuitka. Includes benchmarking and cross-platform support.

Usage:
    python scripts/build.py --tool pyinstaller    # Build with PyInstaller
    python scripts/build.py --tool nuitka         # Build with Nuitka
    python scripts/build.py --compare             # Benchmark both tools
    python scripts/build.py --compile-bytecode    # Pre-compile .pyc files
    python scripts/build.py --measure-startup     # Measure startup time
"""

import argparse
import os
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import List, Optional, Tuple

# Project root (parent of scripts/)
PROJECT_ROOT = Path(__file__).parent.parent
NAVIG_DIR = PROJECT_ROOT / "navig"
DIST_DIR = PROJECT_ROOT / "dist"
BUILD_DIR = PROJECT_ROOT / "build"


def run_command(
    cmd: List[str], cwd: Optional[Path] = None, capture: bool = False
) -> Tuple[int, str, str]:
    """Run a shell command and return exit code, stdout, stderr."""
    print(f"  Running: {' '.join(str(c) for c in cmd)}")
    try:
        result = subprocess.run(
            cmd,
            cwd=cwd or PROJECT_ROOT,
            capture_output=capture,
            text=True,
        )
        return result.returncode, result.stdout or "", result.stderr or ""
    except FileNotFoundError:
        return 1, "", f"Command not found: {cmd[0]}"


def get_version() -> str:
    """Get NAVIG version from __init__.py."""
    init_file = NAVIG_DIR / "__init__.py"
    with open(init_file) as f:
        for line in f:
            if line.startswith("__version__"):
                return line.split("=")[1].strip().strip("\"'")
    return "0.0.0"


def get_binary_name() -> str:
    """Get platform-appropriate binary name."""
    version = get_version()
    system = platform.system().lower()
    arch = platform.machine().lower()
    if arch in ("x86_64", "amd64"):
        arch = "x64"
    elif arch in ("aarch64", "arm64"):
        arch = "arm64"

    ext = ".exe" if system == "windows" else ""
    return f"navig-{version}-{system}-{arch}{ext}"


def measure_startup_time(executable: str, iterations: int = 5) -> float:
    """Measure average startup time for --help."""
    times = []
    for _ in range(iterations):
        start = time.perf_counter()
        subprocess.run([executable, "--help"], capture_output=True)
        elapsed = (time.perf_counter() - start) * 1000  # ms
        times.append(elapsed)
    return sum(times) / len(times)


def measure_python_startup(iterations: int = 5) -> float:
    """Measure Python CLI startup time for baseline."""
    times = []
    for _ in range(iterations):
        start = time.perf_counter()
        subprocess.run(
            [sys.executable, "-m", "navig", "--help"],
            capture_output=True,
            cwd=PROJECT_ROOT,
        )
        elapsed = (time.perf_counter() - start) * 1000
        times.append(elapsed)
    return sum(times) / len(times)


def get_file_size(path: Path) -> float:
    """Get file size in MB."""
    if path.exists():
        return path.stat().st_size / (1024 * 1024)
    return 0.0


def compile_bytecode():
    """Pre-compile all Python files to .pyc."""
    print("\n=== Compiling Python bytecode ===")

    import compileall

    # Standard compilation
    print("  Compiling with standard optimization...")
    compileall.compile_dir(str(NAVIG_DIR), quiet=1, force=True)

    # Optimized compilation (-O removes asserts)
    print("  Compiling with -O optimization...")
    code, _, _ = run_command(
        [sys.executable, "-O", "-m", "compileall", "-q", "-f", str(NAVIG_DIR)]
    )

    # Double optimized (-OO removes docstrings)
    print("  Compiling with -OO optimization...")
    code, _, _ = run_command(
        [sys.executable, "-OO", "-m", "compileall", "-q", "-f", str(NAVIG_DIR)]
    )

    print("  Bytecode compilation complete!")


def get_hidden_imports() -> List[str]:
    """Return list of hidden imports for bundlers."""
    return [
        # Core dependencies
        "typer",
        "typer.core",
        "typer.main",
        "click",
        "click.core",
        "rich",
        "rich.console",
        "rich.table",
        "rich.panel",
        "rich.progress",
        "rich.markdown",
        "rich.syntax",
        "rich.tree",
        "rich.prompt",
        "rich.live",
        "rich.spinner",
        "rich.layout",
        "yaml",
        "requests",
        "paramiko",
        "psutil",
        "pyperclip",
        "colorama",
        "jinja2",
        # NAVIG modules (lazy-loaded)
        "navig",
        "navig.cli",
        "navig.main",
        "navig.config",
        "navig.tunnel",
        "navig.remote",
        "navig.ai",
        "navig.ai_context",
        "navig.console_helper",
        "navig.lazy_loader",
        "navig.debug_logger",
        "navig.migration",
        "navig.discovery",
        "navig.template_manager",
        "navig.core.yaml_io",
        # NAVIG commands
        "navig.commands",
        "navig.commands.host",
        "navig.commands.app",
        "navig.commands.database",
        "navig.commands.db",
        "navig.commands.tunnel",
        "navig.commands.files",
        "navig.commands.webserver",
        "navig.commands.backup",
        "navig.commands.maintenance",
        "navig.commands.monitoring",
        "navig.commands.security",
        "navig.commands.interactive",
        "navig.commands.hestia",
        "navig.commands.template",
        "navig.commands.scaffold",
        "navig.commands.docker",
        # Plugins
        "navig.plugins",
        "navig.plugins.base",
        "navig.plugins.hello",
        "navig.plugins.hello.plugin",
        # Modules
        "navig.modules",
        "navig.modules.proactive_display",
        "navig.core",
    ]


def get_data_files() -> List[Tuple[str, str]]:
    """Return list of data files to include."""
    data = []

    # Help files
    help_dir = NAVIG_DIR / "help"
    if help_dir.exists():
        for f in help_dir.glob("*.md"):
            data.append((str(f), "navig/help"))

    # Schemas
    schemas_dir = NAVIG_DIR / "schemas"
    if schemas_dir.exists():
        for f in schemas_dir.glob("*.json"):
            data.append((str(f), "navig/schemas"))

    # Resources/workflows
    resources_dir = NAVIG_DIR / "resources"
    if resources_dir.exists():
        for f in resources_dir.rglob("*.yaml"):
            rel_path = f.parent.relative_to(NAVIG_DIR)
            data.append((str(f), str(Path("navig") / rel_path)))

    return data


def build_pyinstaller(onefile: bool = True) -> Optional[Path]:
    """Build with PyInstaller."""
    print("\n=== Building with PyInstaller ===")

    # Check if PyInstaller is installed
    try:
        import PyInstaller

        print(f"  PyInstaller version: {PyInstaller.__version__}")
    except ImportError:
        print("  ERROR: PyInstaller not installed. Run: pip install pyinstaller")
        return None

    # Clean previous build
    if BUILD_DIR.exists():
        shutil.rmtree(BUILD_DIR)

    # Build command
    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--name",
        "navig",
        "--console",
        "--noconfirm",
        "--clean",
        "--distpath",
        str(DIST_DIR),
        "--workpath",
        str(BUILD_DIR),
    ]

    if onefile:
        cmd.append("--onefile")
    else:
        cmd.append("--onedir")

    # Add hidden imports
    for imp in get_hidden_imports():
        cmd.extend(["--hidden-import", imp])

    # Add data files
    for src, dst in get_data_files():
        cmd.extend(["--add-data", f"{src}{os.pathsep}{dst}"])

    # Add icon if exists
    icon_path = PROJECT_ROOT / "navig.ico"
    if icon_path.exists():
        cmd.extend(["--icon", str(icon_path)])

    # Entry point
    cmd.append(str(NAVIG_DIR / "main.py"))

    # Run PyInstaller
    print("  Building...")
    code, stdout, stderr = run_command(cmd)

    if code != 0:
        print(f"  ERROR: PyInstaller failed with code {code}")
        if stderr:
            print(f"  {stderr[:500]}")
        return None

    # Find output
    if onefile:
        if platform.system() == "Windows":
            output = DIST_DIR / "navig.exe"
        else:
            output = DIST_DIR / "navig"
    else:
        output = DIST_DIR / "navig"

    if output.exists():
        # Rename to versioned name
        final_name = DIST_DIR / get_binary_name()
        if onefile:
            shutil.move(output, final_name)
        else:
            shutil.move(output, final_name)
        print(f"  Output: {final_name}")
        print(f"  Size: {get_file_size(final_name):.2f} MB")
        return final_name

    print("  ERROR: Output binary not found")
    return None


def build_nuitka(onefile: bool = True) -> Optional[Path]:
    """Build with Nuitka."""
    print("\n=== Building with Nuitka ===")

    # Check if Nuitka is installed
    try:
        code, stdout, _ = run_command(
            [sys.executable, "-m", "nuitka", "--version"], capture=True
        )
        if code == 0:
            print(
                f"  Nuitka version: {stdout.strip().split()[-1] if stdout else 'unknown'}"
            )
        else:
            raise ImportError()
    except Exception:
        print("  ERROR: Nuitka not installed. Run: pip install nuitka")
        return None

    # Clean previous build
    build_nuitka_dir = PROJECT_ROOT / "navig.build"
    if build_nuitka_dir.exists():
        shutil.rmtree(build_nuitka_dir)

    DIST_DIR.mkdir(parents=True, exist_ok=True)

    # Build command
    cmd = [
        sys.executable,
        "-m",
        "nuitka",
        "--output-dir=" + str(DIST_DIR),
        "--assume-yes-for-downloads",  # Auto-download dependencies
        "--follow-imports",
        "--include-package=navig",
        "--include-package=navig.commands",
        "--include-package=navig.modules",
        "--include-package=navig.plugins",
        "--include-package=navig.core",
        "--enable-plugin=anti-bloat",  # Reduce size
    ]

    if onefile:
        cmd.append("--onefile")
    else:
        cmd.append("--standalone")

    # LTO for smaller binary (slower build)
    cmd.append("--lto=yes")

    # Add data files
    for src, dst in get_data_files():
        cmd.append(f"--include-data-files={src}={dst}/")

    # Entry point
    cmd.append(str(NAVIG_DIR / "main.py"))

    # Run Nuitka
    print("  Building (this may take several minutes)...")
    code, stdout, stderr = run_command(cmd)

    if code != 0:
        print(f"  ERROR: Nuitka failed with code {code}")
        if stderr:
            print(f"  {stderr[:500]}")
        return None

    # Find output
    if platform.system() == "Windows":
        output = DIST_DIR / "main.exe"
        if not output.exists():
            output = DIST_DIR / "main.dist" / "main.exe"
    else:
        output = DIST_DIR / "main.bin"
        if not output.exists():
            output = DIST_DIR / "main.dist" / "main"

    if output.exists():
        final_name = DIST_DIR / get_binary_name()
        shutil.move(output, final_name)
        print(f"  Output: {final_name}")
        print(f"  Size: {get_file_size(final_name):.2f} MB")
        return final_name

    print("  ERROR: Output binary not found")
    return None


def compare_builds():
    """Build with both tools and compare."""
    print("\n" + "=" * 60)
    print("NAVIG Build Comparison")
    print("=" * 60)

    results = {}

    # Measure Python baseline
    print("\n--- Measuring Python baseline ---")
    python_startup = measure_python_startup()
    print(f"  Python startup: {python_startup:.0f} ms")
    results["python"] = {"startup_ms": python_startup, "size_mb": 0}

    # Build with PyInstaller
    pyinstaller_bin = build_pyinstaller()
    if pyinstaller_bin and pyinstaller_bin.exists():
        startup = measure_startup_time(str(pyinstaller_bin))
        size = get_file_size(pyinstaller_bin)
        results["pyinstaller"] = {
            "startup_ms": startup,
            "size_mb": size,
            "path": pyinstaller_bin,
        }
        print(f"  Startup: {startup:.0f} ms")

    # Build with Nuitka
    nuitka_bin = build_nuitka()
    if nuitka_bin and nuitka_bin.exists():
        startup = measure_startup_time(str(nuitka_bin))
        size = get_file_size(nuitka_bin)
        results["nuitka"] = {"startup_ms": startup, "size_mb": size, "path": nuitka_bin}
        print(f"  Startup: {startup:.0f} ms")

    # Summary
    print("\n" + "=" * 60)
    print("COMPARISON SUMMARY")
    print("=" * 60)
    print(f"{'Tool':<15} {'Startup (ms)':<15} {'Size (MB)':<15}")
    print("-" * 45)

    for tool, data in results.items():
        startup = f"{data['startup_ms']:.0f}"
        size = f"{data['size_mb']:.1f}" if data["size_mb"] > 0 else "N/A"
        print(f"{tool:<15} {startup:<15} {size:<15}")

    # Recommendation
    print("\n--- Recommendation ---")
    if "pyinstaller" in results and "nuitka" in results:
        pi = results["pyinstaller"]
        nu = results["nuitka"]
        if nu["size_mb"] < pi["size_mb"] and nu["startup_ms"] < pi["startup_ms"]:
            print(
                "  Nuitka produces smaller, faster binaries. Recommended for production."
            )
        elif pi["size_mb"] < nu["size_mb"]:
            print(
                "  PyInstaller produces smaller binaries. Consider for size-constrained deployments."
            )
        else:
            print("  PyInstaller is faster to build. Consider for development cycles.")

    return results


def measure_startup():
    """Measure and report startup times."""
    print("\n=== Startup Time Analysis ===")

    # Python baseline
    print("\n1. Python module import time:")
    code, stdout, _ = run_command(
        [sys.executable, "-X", "importtime", "-c", "import navig.cli"], capture=True
    )

    # Get total import time from stderr (importtime writes there)
    code, _, stderr = run_command(
        [sys.executable, "-X", "importtime", "-c", "import navig.cli"], capture=True
    )

    # Parse import times
    if stderr:
        lines = stderr.strip().split("\n")
        print("  Top 10 slowest imports:")
        # Sort by cumulative time
        import_times = []
        for line in lines:
            if "import time:" in line:
                parts = line.split("|")
                if len(parts) >= 3:
                    try:
                        cum_time = int(parts[1].strip())
                        module = parts[2].strip()
                        import_times.append((cum_time, module))
                    except ValueError:
                        pass  # malformed value; skip

        import_times.sort(reverse=True)
        for cum_time, module in import_times[:10]:
            print(f"    {cum_time/1000:.1f}ms - {module}")

    # CLI startup time
    print("\n2. CLI startup time (navig --help):")
    startup = measure_python_startup()
    print(f"  Average: {startup:.0f} ms")

    if startup < 100:
        print("  Status: EXCELLENT (<100ms)")
    elif startup < 200:
        print("  Status: GOOD (<200ms)")
    elif startup < 500:
        print("  Status: ACCEPTABLE (<500ms)")
    else:
        print("  Status: SLOW (>500ms) - Consider optimizing imports")


def main():
    parser = argparse.ArgumentParser(
        description="NAVIG Build Script - Create optimized distributions",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scripts/build.py --tool pyinstaller    Build single binary with PyInstaller
  python scripts/build.py --tool nuitka         Build optimized binary with Nuitka
  python scripts/build.py --compare             Build both and compare
  python scripts/build.py --compile-bytecode    Pre-compile Python bytecode
  python scripts/build.py --measure-startup     Analyze startup performance
        """,
    )

    parser.add_argument(
        "--tool", choices=["pyinstaller", "nuitka"], help="Build tool to use"
    )
    parser.add_argument(
        "--compare", action="store_true", help="Build with both tools and compare"
    )
    parser.add_argument(
        "--compile-bytecode",
        action="store_true",
        help="Pre-compile all .py files to .pyc",
    )
    parser.add_argument(
        "--measure-startup",
        action="store_true",
        help="Measure and analyze startup time",
    )
    parser.add_argument(
        "--onedir",
        action="store_true",
        help="Create directory distribution instead of single file",
    )

    args = parser.parse_args()

    # Ensure we're in project root
    os.chdir(PROJECT_ROOT)

    if args.compile_bytecode:
        compile_bytecode()

    if args.measure_startup:
        measure_startup()

    if args.compare:
        compare_builds()
    elif args.tool == "pyinstaller":
        build_pyinstaller(onefile=not args.onedir)
    elif args.tool == "nuitka":
        build_nuitka(onefile=not args.onedir)
    elif not args.compile_bytecode and not args.measure_startup:
        parser.print_help()


if __name__ == "__main__":
    main()
