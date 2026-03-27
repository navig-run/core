import os
from pathlib import Path

import yaml

store_dir = Path(r"K:\_PROJECTS\navig\navig-core\store")
manifest_path = store_dir / "manifest.yaml"

manifest = {"version": "1.0", "assets": []}

for root, _, files in os.walk(store_dir):
    for f in files:
        if f.endswith(".gitkeep") or f == "manifest.yaml":
            continue
        rel_path = Path(root) / f
        rel_path_str = rel_path.relative_to(store_dir).as_posix()

        asset_type = "other"
        parts = rel_path_str.split("/")
        if parts[0] in [
            "agents",
            "prompts",
            "skills",
            "formations",
            "tools",
            "templates",
            "workflows",
            "actions",
        ]:
            asset_type = parts[0]
            if asset_type == "agents":
                asset_type = "agent"
            elif asset_type == "prompts":
                asset_type = "prompt"
            elif asset_type == "skills":
                asset_type = "skill"
            elif asset_type == "formations":
                asset_type = "formation"
            elif asset_type == "tools":
                asset_type = "tool"
            elif asset_type == "templates":
                asset_type = "template"
            elif asset_type == "workflows":
                asset_type = "workflow"
            elif asset_type == "actions":
                asset_type = "action"

        manifest["assets"].append(
            {"path": rel_path_str, "type": asset_type, "status": "active"}
        )

with open(manifest_path, "w", encoding="utf-8") as out:
    yaml.dump(manifest, out, sort_keys=False)

print(f"Generated manifest.yaml with {len(manifest['assets'])} assets.")
