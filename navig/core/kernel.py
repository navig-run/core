import glob
import os
import re

import yaml

from .models import NavigCommand, NavigPack, SkillManifest
from .plugin_manager import PluginManager


class NavigKernel:
    def __init__(self, root_path: str):
        self.root_path = root_path
        self.skills: dict[str, SkillManifest] = {}
        self.commands: dict[str, NavigCommand] = {}
        self.packs: dict[str, NavigPack] = {}
        self.plugin_manager = PluginManager(os.path.join(root_path, "plugins"))

    def bootstrap(self):
        print("🚀 NAVIG Kernel Booting...")
        self.plugin_manager.discover_and_load()
        self.load_skills()
        self.load_packs()
        print(
            f"🌟 System Ready. Loaded {len(self.skills)} skills, {len(self.packs)} packs, and {len(self.plugin_manager.plugins)} active plugins."
        )

    def load_skills(self):
        # 1. Load Built-in Skills (YAML/Markdown)
        patterns = [
            os.path.join(self.root_path, "skills", "**", "SKILL.md"),
            os.path.join(self.root_path, "plugins", "**", "skills", "**", "*.md"),
            os.path.join(self.root_path, ".navig", "skills", "**", "*.md"),
        ]

        for pattern in patterns:
            for filepath in glob.glob(pattern, recursive=True):
                self._parse_skill_file(filepath)

    def _parse_skill_file(self, filepath: str):
        try:
            with open(filepath, encoding="utf-8") as f:
                content = f.read()

            match = re.search(r"^---\n(.*?)\n---", content, re.DOTALL)
            if not match:
                return

            frontmatter = yaml.safe_load(match.group(1))

            skill = SkillManifest(**frontmatter)
            self.skills[skill.name] = skill

            # Register commands
            for cmd in skill.navig_commands:
                cmd.source_skill = skill.name
                self.commands[cmd.name] = cmd

        except Exception as e:
            # Silently fail for now or log debug
            print(f"⚠️  Could not parse {filepath}: {e}")
            pass

    def load_packs(self):
        patterns = [
            os.path.join(self.root_path, "packs", "**", "*.yml"),
            os.path.join(
                self.root_path, "context", "packs", "**", "*.yml"
            ),  # Support context packs if any
            os.path.join(self.root_path, ".navig", "packs", "**", "*.yml"),
        ]

        for pattern in patterns:
            for filepath in glob.glob(pattern, recursive=True):
                self._parse_pack_file(filepath)

    def _parse_pack_file(self, filepath: str):
        try:
            with open(filepath, encoding="utf-8") as f:
                content = yaml.safe_load(f)

            # Helper to map 'steps' properly if they are strings (simple commands) or dicts
            # For robust implementation we need validation. Pydantic handles basic types.

            pack = NavigPack(**content)
            self.packs[pack.name] = pack
        except Exception:
            # print(f"⚠️  Could not parse pack {filepath}: {e}")
            pass

    def run_pack(self, pack_name: str):
        pack = self.packs.get(pack_name)
        if not pack:
            print(f"❌ Pack {pack_name} not found.")
            return

        print(f"\n📦 RUNNING PACK: {pack.name}")
        print(f"   {pack.description}")

        for i, step in enumerate(pack.steps):
            print(f"\n➡️  Step {i+1}: {step.description or step.name}")
            print(f"   Command: {step.command}")

            # Here we resolve the command in the kernel to check for safety!
            # We assume step.command is a full CLI string "navig docker restart ..."

            # Simple simulation of execution
            # In reality, we'd parse the command string, resolve intent, check safety, execute.
            parts = step.command.split()
            cmd_name = None

            # Try to resolve command from the string
            # This is tricky without a full parser, but let's try strict matching
            # e.g. "navig fs list" -> we look for a command with syntax starting with this
            # For now, just execute blindly (unsafe) or mock it.

            print(f"   ✅ (Mock) Executed: {step.command}")

    def resolve_intent(self, query: str) -> object | None:
        # Returns NavigCommand or NavigPack
        query_lower = query.lower()
        query_tokens = set(query_lower.split())

        # Check Packs first (as high level intents)
        for name, pack in self.packs.items():
            # Match exact name or name with spaces instead of hyphens
            normalized_name = name.replace("-", " ")
            if name in query_lower or normalized_name in query_lower:
                return pack

        # Check Commands (as before)
        # ... (same logic as before) ...
        best_match = None
        max_score = 0

        # print(f"DEBUG: Resolving '{query}'")

        for name, cmd in self.commands.items():
            score = 0

            # 1. Exact syntax prefix match (High confidence)
            # e.g. "navig docker" in "navig docker ps"
            cmd_start = cmd.syntax.lower().split()[0:2]
            if " ".join(cmd_start) in query_lower:
                score += 20

            # 2. Command name/trigger match
            # e.g. "recall" in "navig memory recall"
            # We strip common prefixes like "git-" or "docker-" if present, or just match the name
            if cmd.name.lower() in query_tokens:
                score += 15

            # 3. Description keyword match
            # Naive bag-of-words overlap
            if cmd.description:
                desc_tokens = set(cmd.description.lower().split())
                overlap = query_tokens.intersection(desc_tokens)
                score += len(overlap) * 2

            # 4. Example match (Highest confidence)
            skill = self.skills.get(cmd.source_skill)
            if skill:
                for ex in skill.examples:
                    # If user query is very similar to example
                    if ex.user.lower() in query_lower:
                        score += 30

                    # Token overlap with example
                    ex_tokens = set(ex.user.lower().split())
                    ex_overlap = query_tokens.intersection(ex_tokens)
                    score += len(ex_overlap) * 3

            # print(f"  - {cmd.name}: {score}")

            if score > max_score:
                max_score = score
                best_match = cmd

        if max_score > 5:  # Threshold
            return best_match
        return None

    def execute_command(self, cmd_name: str, args: list[str] = None):
        cmd = self.commands.get(cmd_name)
        if not cmd:
            print(f"❌ Command {cmd_name} not found.")
            return

        # Check safety
        if cmd.risk in ["destructive", "moderate"] or cmd.confirmation_required:
            msg = cmd.confirmation_msg or f"Execute {cmd.name}?"
            ans = input(f"🛑 {msg} [y/N] ")
            if ans.lower() != "y":
                print("❌ Aborted.")
                return

        print(f"✅ Executing {cmd.syntax}...")

        parts = cmd.syntax.split()
        if len(parts) >= 3 and parts[0] == "navig" and parts[1] == "memory":
            plugin_name = "navig-memory"
            method = parts[2]
            params = {"query": " ".join(args)} if args else {}
            self._exec_plugin(plugin_name, method, params)

        elif cmd.source_skill == "windows-automation":
            plugin_name = "navig-windows-automation"
            # Map command name to plugin method
            # cmd.name is "window-list", plugin method is "window-list"
            method = cmd.name

            # Simple arg parsing for prototype
            # args is meant to be a list of strings passed after the command?
            # actually args in execute_command comes from query.split()
            # We need to map positional args to named params if possible, or pass as list
            # The plugin methods expect named args (x, y, etc)

            # This is complex without a real parser.
            # providing empty dict for now, or naive mapping
            params = {}
            if args:
                # simplistic: if command is "type", text is remainder
                if method == "type":
                    params["text"] = " ".join(args)
                elif method == "open-app":
                    params["target"] = " ".join(args)
                elif method == "click" and len(args) >= 2:
                    params["x"] = args[0]
                    params["y"] = args[1]

            self._exec_plugin(plugin_name, method, params)

        else:
            print(f"🖥️  (System Command Simulation): {cmd.syntax}")

    def _exec_plugin(self, plugin_name, method, params):
        try:
            result = self.plugin_manager.execute_skill(plugin_name, method, params)
            print(f"✨ Plugin Result: {result}")
        except Exception as e:
            print(f"❌ Plugin execution failed: {e}")
