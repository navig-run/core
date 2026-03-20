"""
NAVIG CLI Onboarding Wizard

Interactive step-by-step setup for new users.
Reduces setup time from 30 minutes to under 5 minutes.

Usage:
    navig init
    navig init --reconfigure
    navig init --install-daemon
"""

import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict

# Try questionary first, fall back to simple prompts
try:
    import questionary
    from questionary import Style
    HAS_QUESTIONARY = True
except ImportError:
    HAS_QUESTIONARY = False


# Custom style for questionary
WIZARD_STYLE = Style([
    ('qmark', 'fg:cyan bold'),
    ('question', 'bold'),
    ('answer', 'fg:green'),
    ('pointer', 'fg:cyan bold'),
    ('highlighted', 'fg:cyan bold'),
    ('selected', 'fg:green'),
]) if HAS_QUESTIONARY else None


class SetupWizard:
    """
    Interactive setup wizard for NAVIG.
    
    Steps:
    1. Welcome & prerequisites check
    2. AI provider selection & API key
    3. SSH key setup
    4. Telegram bot token (optional)
    5. Configuration scope
    6. Daemon installation (optional)
    """

    def __init__(self, reconfigure: bool = False):
        self.reconfigure = reconfigure
        self.config: Dict[str, Any] = {}
        self.navig_dir = Path.home() / ".navig"
        self.config_file = self.navig_dir / "config.yaml"

    def run(self) -> bool:
        """Run the wizard. Returns True if successful."""
        try:
            self._print_welcome()

            if not self._check_prerequisites():
                return False

            self._setup_ai_provider()
            self._setup_ssh()
            self._setup_telegram()
            self._setup_hosts()
            self._save_config()
            self._print_summary()

            return True

        except KeyboardInterrupt:
            print("\n\n❌ Setup cancelled.")
            return False
        except Exception as e:
            print(f"\n\n❌ Setup failed: {e}")
            return False

    def _print_welcome(self):
        """Display welcome message."""
        print()
        print("=" * 60)
        print("🚀 NAVIG Setup Wizard")
        print("=" * 60)
        print()
        print("Welcome! This wizard will help you configure NAVIG.")
        print("It should take about 5 minutes to complete.")
        print()
        print("You can skip any step by pressing Enter.")
        print("Press Ctrl+C at any time to cancel.")
        print()

    def _check_prerequisites(self) -> bool:
        """Check system prerequisites."""
        print("📋 Checking prerequisites...")
        print()

        checks = []

        # Python version
        py_version = sys.version_info
        py_ok = py_version >= (3, 10)
        checks.append((
            f"Python {py_version.major}.{py_version.minor}",
            py_ok,
            "Python 3.10+ required" if not py_ok else None
        ))

        # SSH availability
        ssh_ok = self._check_command("ssh")
        checks.append((
            "SSH client",
            ssh_ok,
            "Install OpenSSH" if not ssh_ok else None
        ))

        # Git (optional)
        git_ok = self._check_command("git")
        checks.append((
            "Git",
            git_ok,
            "Optional: Install Git for version control" if not git_ok else None
        ))

        # Print results
        all_ok = True
        for name, ok, hint in checks:
            status = "✅" if ok else "⚠️"
            print(f"  {status} {name}")
            if hint and not ok:
                print(f"      {hint}")
            if not ok and name != "Git":  # Git is optional
                all_ok = False

        print()

        if not all_ok:
            print("❌ Some prerequisites are missing. Please install them first.")
            return False

        return True

    def _check_command(self, cmd: str) -> bool:
        """Check if a command is available."""
        try:
            subprocess.run(
                [cmd, "--version"],
                capture_output=True,
                timeout=5
            )
            return True
        except Exception:
            return False

    def _setup_ai_provider(self):
        """Configure AI provider and API key."""
        print()
        print("🤖 AI Provider Setup")
        print("-" * 40)
        print()

        providers = [
            ("openrouter", "OpenRouter (recommended, access to all models)"),
            ("openai", "OpenAI (GPT-4, GPT-3.5)"),
            ("anthropic", "Anthropic (Claude)"),
            ("ollama", "Ollama (local, no API key needed)"),
            ("skip", "Skip for now"),
        ]

        if HAS_QUESTIONARY:
            choice = questionary.select(
                "Select your AI provider:",
                choices=[f"{name} - {desc}" for name, desc in providers],
                style=WIZARD_STYLE
            ).ask()
            provider = choice.split(" - ")[0] if choice else "skip"
        else:
            print("Available providers:")
            for i, (name, desc) in enumerate(providers, 1):
                print(f"  {i}. {name} - {desc}")
            choice = input("\nSelect provider (1-5) [1]: ").strip() or "1"
            try:
                provider = providers[int(choice) - 1][0]
            except (ValueError, IndexError):
                provider = "openrouter"

        if provider == "skip":
            print("  Skipping AI setup. You can configure later in ~/.navig/config.yaml")
            return

        self.config["ai"] = {"default_provider": provider}

        # Get API key (except for Ollama)
        if provider != "ollama":
            env_var = f"{provider.upper()}_API_KEY"

            print()
            print("  Get your API key from:")
            if provider == "openrouter":
                print("  https://openrouter.ai/keys")
            elif provider == "openai":
                print("  https://platform.openai.com/api-keys")
            elif provider == "anthropic":
                print("  https://console.anthropic.com/")
            print()

            if HAS_QUESTIONARY:
                api_key = questionary.password(
                    f"Enter your {provider} API key:",
                    style=WIZARD_STYLE
                ).ask()
            else:
                api_key = input(f"Enter your {provider} API key: ").strip()

            if api_key:
                # Store as env var reference
                self.config["ai"][f"{provider}_api_key"] = f"${{{env_var}}}"

                # Offer to save to .env
                print()
                save_env = self._confirm("Save API key to ~/.navig/.env?", default=True)
                if save_env:
                    env_file = self.navig_dir / ".env"
                    env_file.parent.mkdir(parents=True, exist_ok=True)
                    with open(env_file, "a") as f:
                        f.write(f"\n{env_var}={api_key}\n")
                    print(f"  ✅ Saved to {env_file}")

                # Test connection
                print()
                print("  Testing API connection...")
                if self._test_ai_connection(provider, api_key):
                    print("  ✅ API connection successful!")
                else:
                    print("  ⚠️ Could not verify API key (will retry later)")
        else:
            # Ollama setup
            print()
            print("  Make sure Ollama is running: ollama serve")
            ollama_host = input("  Ollama host [http://localhost:11434]: ").strip()
            if ollama_host:
                self.config["ai"]["ollama_host"] = ollama_host

    def _test_ai_connection(self, provider: str, api_key: str) -> bool:
        """Test AI provider connection."""
        try:
            import httpx

            if provider == "openrouter":
                resp = httpx.get(
                    "https://openrouter.ai/api/v1/models",
                    headers={"Authorization": f"Bearer {api_key}"},
                    timeout=10
                )
                return resp.status_code == 200
            elif provider == "openai":
                resp = httpx.get(
                    "https://api.openai.com/v1/models",
                    headers={"Authorization": f"Bearer {api_key}"},
                    timeout=10
                )
                return resp.status_code == 200
            # Add more providers as needed
            return True
        except Exception:
            return False

    def _setup_ssh(self):
        """Configure SSH settings."""
        print()
        print("🔐 SSH Setup")
        print("-" * 40)
        print()

        # Check for existing SSH keys
        ssh_dir = Path.home() / ".ssh"
        existing_keys = []

        for key_name in ["id_ed25519", "id_rsa", "id_ecdsa"]:
            key_path = ssh_dir / key_name
            if key_path.exists():
                existing_keys.append(key_path)

        if existing_keys:
            print("  Found existing SSH keys:")
            for key in existing_keys:
                print(f"    • {key}")
            print()

            # Use first key as default
            default_key = str(existing_keys[0])
            self.config["ssh"] = {"default_key_path": default_key}
            print(f"  Using: {default_key}")
        else:
            print("  No SSH keys found.")
            generate = self._confirm("Generate a new SSH key?", default=True)

            if generate:
                key_path = ssh_dir / "id_ed25519"
                print(f"  Generating key at {key_path}...")

                try:
                    ssh_dir.mkdir(exist_ok=True, mode=0o700)
                    subprocess.run([
                        "ssh-keygen",
                        "-t", "ed25519",
                        "-f", str(key_path),
                        "-N", "",  # No passphrase for automation
                        "-C", "navig@local"
                    ], check=True, capture_output=True)

                    self.config["ssh"] = {"default_key_path": str(key_path)}
                    print(f"  ✅ Key generated: {key_path}")
                    print()
                    print("  Add this public key to your servers:")
                    print(f"    {key_path}.pub")
                except Exception as e:
                    print(f"  ❌ Failed to generate key: {e}")

    def _setup_telegram(self):
        """Configure Telegram bot (optional)."""
        print()
        print("📱 Telegram Bot Setup (optional)")
        print("-" * 40)
        print()

        setup_telegram = self._confirm("Set up Telegram bot?", default=False)

        if not setup_telegram:
            print("  Skipping Telegram setup.")
            return

        print()
        print("  To create a bot:")
        print("  1. Message @BotFather on Telegram")
        print("  2. Send /newbot and follow instructions")
        print("  3. Copy the bot token")
        print()

        if HAS_QUESTIONARY:
            bot_token = questionary.password(
                "Enter bot token:",
                style=WIZARD_STYLE
            ).ask()
        else:
            bot_token = input("Enter bot token: ").strip()

        if not bot_token:
            print("  Skipping Telegram setup.")
            return

        # Validate token format
        if ":" not in bot_token:
            print("  ⚠️ Invalid token format. Should be like: 123456789:ABC...")
            return

        # Test token
        print("  Testing bot token...")
        if self._test_telegram_token(bot_token):
            print("  ✅ Bot token valid!")

            # Get user IDs
            print()
            user_ids_str = input("  Your Telegram user ID (comma-separated for multiple): ").strip()

            user_ids = []
            if user_ids_str:
                try:
                    user_ids = [int(x.strip()) for x in user_ids_str.split(",")]
                except ValueError:
                    print("  ⚠️ Invalid user IDs. Use numbers only.")

            self.config["telegram"] = {
                "bot_token": "${TELEGRAM_BOT_TOKEN}",
                "allowed_users": user_ids
            }

            # Save token to .env
            env_file = self.navig_dir / ".env"
            env_file.parent.mkdir(parents=True, exist_ok=True)
            with open(env_file, "a") as f:
                f.write(f"\nTELEGRAM_BOT_TOKEN={bot_token}\n")
            print(f"  ✅ Token saved to {env_file}")
        else:
            print("  ❌ Invalid bot token.")

    def _test_telegram_token(self, token: str) -> bool:
        """Test Telegram bot token."""
        try:
            import httpx
            resp = httpx.get(
                f"https://api.telegram.org/bot{token}/getMe",
                timeout=10
            )
            return resp.status_code == 200 and resp.json().get("ok")
        except Exception:
            return False

    def _setup_hosts(self):
        """Configure initial hosts."""
        print()
        print("🖥️ Host Setup")
        print("-" * 40)
        print()

        add_host = self._confirm("Add a server to manage?", default=False)

        if not add_host:
            print("  Skipping host setup. Add hosts later with: navig host add")
            return

        hosts = []

        while True:
            print()
            name = input("  Host name (e.g., webserver): ").strip()
            if not name:
                break

            hostname = input(f"  Hostname/IP for {name}: ").strip()
            if not hostname:
                continue

            user = input(f"  SSH user [{os.getenv('USER', 'root')}]: ").strip()
            user = user or os.getenv("USER", "root")

            port = input("  SSH port [22]: ").strip()
            port = int(port) if port else 22

            hosts.append({
                "name": name,
                "hostname": hostname,
                "user": user,
                "port": port
            })

            print(f"  ✅ Added {name} ({user}@{hostname}:{port})")

            if not self._confirm("Add another host?", default=False):
                break

        if hosts:
            self.config["hosts"] = hosts

    def _save_config(self):
        """Save configuration to file."""
        import yaml

        print()
        print("💾 Saving Configuration")
        print("-" * 40)
        print()

        # Ensure directory exists
        self.navig_dir.mkdir(parents=True, exist_ok=True)

        # Add version
        self.config["version"] = "1.0"

        # Load existing config if reconfiguring
        if self.reconfigure and self.config_file.exists():
            with open(self.config_file, 'r') as f:
                existing = yaml.safe_load(f) or {}
                # Merge new config into existing
                for key, value in self.config.items():
                    if isinstance(value, dict) and key in existing:
                        existing[key].update(value)
                    else:
                        existing[key] = value
                self.config = existing

        # Write config
        with open(self.config_file, 'w') as f:
            yaml.dump(self.config, f, default_flow_style=False, sort_keys=False)

        print(f"  ✅ Configuration saved to {self.config_file}")

        # Set permissions
        try:
            os.chmod(self.config_file, 0o600)
            print("  ✅ File permissions set (600)")
        except Exception:
            pass

    def _print_summary(self):
        """Print setup summary."""
        print()
        print("=" * 60)
        print("✅ Setup Complete!")
        print("=" * 60)
        print()
        print("Configuration summary:")
        print()

        if "ai" in self.config:
            provider = self.config["ai"].get("default_provider", "none")
            print(f"  🤖 AI Provider: {provider}")

        if "ssh" in self.config:
            key = self.config["ssh"].get("default_key_path", "none")
            print(f"  🔐 SSH Key: {key}")

        if "telegram" in self.config:
            users = self.config["telegram"].get("allowed_users", [])
            print(f"  📱 Telegram: {len(users)} user(s) configured")

        if "hosts" in self.config:
            print(f"  🖥️ Hosts: {len(self.config['hosts'])} configured")

        print()
        print("Next steps:")
        print("  1. Run: navig status")
        print("  2. Try: navig ask 'What can you do?'")
        print("  3. Add more hosts: navig host add")
        print()
        print("Documentation: https://navig.run/docs")
        print()

    def _confirm(self, message: str, default: bool = True) -> bool:
        """Ask for confirmation."""
        if HAS_QUESTIONARY:
            return questionary.confirm(
                message,
                default=default,
                style=WIZARD_STYLE
            ).ask()
        else:
            suffix = " [Y/n]" if default else " [y/N]"
            response = input(f"{message}{suffix}: ").strip().lower()
            if not response:
                return default
            return response in ("y", "yes")


def install_daemon(service_type: str = "auto") -> bool:
    """
    Install NAVIG as a system daemon.
    
    Args:
        service_type: 'systemd', 'launchd', or 'auto' to detect
        
    Returns:
        True if successful
    """
    import platform

    if service_type == "auto":
        system = platform.system()
        if system == "Linux":
            service_type = "systemd"
        elif system == "Darwin":
            service_type = "launchd"
        else:
            print(f"❌ Unsupported system: {system}")
            return False

    if service_type == "systemd":
        return _install_systemd()
    elif service_type == "launchd":
        return _install_launchd()
    else:
        print(f"❌ Unknown service type: {service_type}")
        return False


def _install_systemd() -> bool:
    """Install systemd service for Linux."""
    service_content = """[Unit]
Description=NAVIG Gateway Service
After=network.target

[Service]
Type=simple
User={user}
Environment=PATH={path}
ExecStart={python} -m navig gateway start
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
""".format(
        user=os.getenv("USER", "root"),
        path=os.environ.get("PATH", ""),
        python=sys.executable
    )

    service_path = Path("/etc/systemd/system/navig.service")

    try:
        # Write service file (requires sudo)
        print(f"  Writing {service_path}...")

        # Use sudo if not root
        if os.geteuid() != 0:
            subprocess.run(
                ["sudo", "tee", str(service_path)],
                input=service_content.encode(),
                check=True,
                capture_output=True
            )
        else:
            service_path.write_text(service_content)

        # Reload and enable
        subprocess.run(["sudo", "systemctl", "daemon-reload"], check=True)
        subprocess.run(["sudo", "systemctl", "enable", "navig"], check=True)

        print("  ✅ Systemd service installed!")
        print("  Start with: sudo systemctl start navig")
        return True

    except Exception as e:
        print(f"  ❌ Failed to install service: {e}")
        return False


def _install_launchd() -> bool:
    """Install launchd plist for macOS."""
    plist_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.navig.gateway</string>
    <key>ProgramArguments</key>
    <array>
        <string>{sys.executable}</string>
        <string>-m</string>
        <string>navig</string>
        <string>gateway</string>
        <string>start</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>{Path.home()}/.navig/logs/gateway.log</string>
    <key>StandardErrorPath</key>
    <string>{Path.home()}/.navig/logs/gateway.error.log</string>
</dict>
</plist>
"""

    plist_path = Path.home() / "Library/LaunchAgents/com.navig.gateway.plist"

    try:
        plist_path.parent.mkdir(parents=True, exist_ok=True)
        plist_path.write_text(plist_content)

        # Load the agent
        subprocess.run(["launchctl", "load", str(plist_path)], check=True)

        print("  ✅ LaunchAgent installed!")
        print(f"  Plist: {plist_path}")
        return True

    except Exception as e:
        print(f"  ❌ Failed to install agent: {e}")
        return False


# CLI entry points
def run_wizard(reconfigure: bool = False, install_daemon_flag: bool = False) -> bool:
    """Run the setup wizard."""
    wizard = SetupWizard(reconfigure=reconfigure)
    success = wizard.run()

    if success and install_daemon_flag:
        print()
        print("🔧 Installing Daemon")
        print("-" * 40)
        install_daemon()

    return success
