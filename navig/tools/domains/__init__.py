"""Tool Domains - Domain-specific tool registrations.

Each sub-module (web_pack, exec_pack, system_pack, …) registers its tools
into a ToolRegistry via a ``register_tools(registry)`` function.
All sub-modules are loaded lazily by ToolRouter._load_builtin_packs().
"""
