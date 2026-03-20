import json
import os
import platform
import re
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional


def register(server: Any) -> None:
    """Register agent monitoring and control tools."""
    server.tools.update({
        "navig_agent_status_get": {
            "name": "navig_agent_status_get",
            "description": "Get autonomous agent runtime/config status including mode, personality, workspace, PID, and running state.",
            "inputSchema": {
                "type": "object",
                "properties": {},
                "required": []
            }
        },
        "navig_agent_goal_list": {
            "name": "navig_agent_goal_list",
            "description": "List autonomous agent goals with state/progress summary.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "state": {
                        "type": "string",
                        "description": "Optional state filter: pending, in_progress, blocked, completed, failed, cancelled"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum goals to return",
                        "default": 50
                    }
                },
                "required": []
            }
        },
        "navig_agent_goal_add": {
            "name": "navig_agent_goal_add",
            "description": "Create a new autonomous agent goal.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "description": {
                        "type": "string",
                        "description": "Goal description"
                    },
                    "metadata": {
                        "type": "object",
                        "description": "Optional metadata object"
                    }
                },
                "required": ["description"]
            }
        },
        "navig_agent_goal_start": {
            "name": "navig_agent_goal_start",
            "description": "Start execution for a pending/blocked autonomous agent goal.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "id": {
                        "type": "string",
                        "description": "Goal ID"
                    }
                },
                "required": ["id"]
            }
        },
        "navig_agent_goal_cancel": {
            "name": "navig_agent_goal_cancel",
            "description": "Cancel an autonomous agent goal by ID.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "id": {
                        "type": "string",
                        "description": "Goal ID"
                    }
                },
                "required": ["id"]
            }
        },
        "navig_agent_remediation_list": {
            "name": "navig_agent_remediation_list",
            "description": "List persisted remediation actions and recent remediation log entries.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "Maximum remediation actions to return",
                        "default": 100
                    }
                },
                "required": []
            }
        },
        "navig_agent_learning_run": {
            "name": "navig_agent_learning_run",
            "description": "Analyze recent agent debug/remediation logs and return recurring error patterns with recommendations.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "days": {
                        "type": "integer",
                        "description": "Analyze logs from the last N days",
                        "default": 7
                    },
                    "export": {
                        "type": "boolean",
                        "description": "Export pattern report to ~/.navig/workspace/error-patterns.json",
                        "default": False
                    }
                },
                "required": []
            }
        },
        "navig_agent_service_status": {
            "name": "navig_agent_service_status",
            "description": "Get OS-level service status for NAVIG agent (systemd/launchd/windows service).",
            "inputSchema": {
                "type": "object",
                "properties": {},
                "required": []
            }
        },
        "navig_agent_component_restart": {
            "name": "navig_agent_component_restart",
            "description": "Queue a remediation restart action for an agent component.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "component": {
                        "type": "string",
                        "description": "Component name (brain, eyes, ears, hands, soul, heart, nervous_system)"
                    },
                    "reason": {
                        "type": "string",
                        "description": "Why restart is requested",
                        "default": "requested_via_mcp"
                    },
                    "metadata": {
                        "type": "object",
                        "description": "Optional metadata"
                    }
                },
                "required": ["component"]
            }
        },
        "navig_agent_remediation_retry": {
            "name": "navig_agent_remediation_retry",
            "description": "Retry a remediation action by ID.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "id": {
                        "type": "string",
                        "description": "Remediation action ID"
                    },
                    "reset_attempts": {
                        "type": "boolean",
                        "description": "Reset attempt counter before retrying",
                        "default": True
                    }
                },
                "required": ["id"]
            }
        },
        "navig_agent_service_install": {
            "name": "navig_agent_service_install",
            "description": "Install NAVIG agent as an OS service.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "start_now": {
                        "type": "boolean",
                        "description": "Start service immediately after install",
                        "default": True
                    }
                },
                "required": []
            }
        },
        "navig_agent_service_uninstall": {
            "name": "navig_agent_service_uninstall",
            "description": "Uninstall NAVIG agent OS service.",
            "inputSchema": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    })

    server._tool_handlers.update({
        "navig_agent_status_get": _tool_agent_status_get,
        "navig_agent_goal_list": _tool_agent_goal_list,
        "navig_agent_goal_add": _tool_agent_goal_add,
        "navig_agent_goal_start": _tool_agent_goal_start,
        "navig_agent_goal_cancel": _tool_agent_goal_cancel,
        "navig_agent_remediation_list": _tool_agent_remediation_list,
        "navig_agent_learning_run": _tool_agent_learning_run,
        "navig_agent_service_status": _tool_agent_service_status,
        "navig_agent_component_restart": _tool_agent_component_restart,
        "navig_agent_remediation_retry": _tool_agent_remediation_retry,
        "navig_agent_service_install": _tool_agent_service_install,
        "navig_agent_service_uninstall": _tool_agent_service_uninstall,
    })


def _tool_agent_status_get(server: Any, args: Dict[str, Any]) -> Dict[str, Any]:
    """Return agent install/runtime status for control plane clients."""
    config_path = Path.home() / '.navig' / 'agent' / 'config.yaml'
    pid_path = Path.home() / '.navig' / 'agent' / 'agent.pid'
    installed = config_path.exists()
    running = False
    pid: Optional[int] = None

    if pid_path.exists():
        try:
            pid = int(pid_path.read_text(encoding='utf-8').strip())
            if platform.system().lower().startswith('win'):
                result = subprocess.run(
                    ['tasklist', '/FI', f'PID eq {pid}'],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                running = str(pid) in result.stdout
            else:
                os.kill(pid, 0)
                running = True
        except Exception:
            running = False

    mode = None
    personality = None
    workspace = None
    if installed:
        try:
            from navig.agent.config import AgentConfig
            cfg = AgentConfig.load(config_path)
            mode = cfg.mode
            personality = cfg.personality.profile
            workspace = str(cfg.workspace)
        except Exception:
            mode = "unknown"
            personality = "unknown"

    return {
        "installed": installed,
        "running": running,
        "pid": pid if running else None,
        "config_path": str(config_path),
        "mode": mode,
        "personality": personality,
        "workspace": workspace,
        "timestamp": datetime.now().isoformat(),
    }

def _resolve_goal_storage_dir() -> Path:
    """Resolve the most likely goal storage directory."""
    candidates: List[Path] = []
    try:
        from navig.agent.config import AgentConfig
        cfg_path = Path.home() / '.navig' / 'agent' / 'config.yaml'
        if cfg_path.exists():
            cfg = AgentConfig.load(cfg_path)
            candidates.append(cfg.workspace)
    except Exception:  # noqa: BLE001
        pass  # best-effort; failure is non-critical

    candidates.append(Path.home() / '.navig' / 'workspace')
    if not candidates:
        return Path.home() / '.navig' / 'workspace'

    for candidate in candidates:
        if (candidate / 'goals.json').exists():
            return candidate
    return candidates[0]

def _get_goal_planner():
    """Create a GoalPlanner against resolved storage."""
    from navig.agent.goals import GoalPlanner
    storage_dir = _resolve_goal_storage_dir()
    return GoalPlanner(storage_dir=storage_dir)

def _tool_agent_goal_list(server: Any, args: Dict[str, Any]) -> Dict[str, Any]:
    """List goals with summary fields for dashboard clients."""
    from navig.agent.goals import GoalState

    planner = _get_goal_planner()
    limit = max(1, int(args.get("limit", 50)))
    state_name = args.get("state")
    state_filter = None
    if state_name:
        try:
            state_filter = GoalState(state_name)
        except ValueError:
            return {"error": f"Invalid state: {state_name}"}

    goals = planner.list_goals(state_filter)[:limit]
    return {
        "storage_dir": str(planner.storage_dir),
        "count": len(goals),
        "goals": [
            {
                "id": g.id,
                "description": g.description,
                "state": g.state.value,
                "progress": g.progress,
                "subtasks": len(g.subtasks),
                "created_at": g.created_at.isoformat(),
                "started_at": g.started_at.isoformat() if g.started_at else None,
                "completed_at": g.completed_at.isoformat() if g.completed_at else None,
                "metadata": g.metadata,
            }
            for g in goals
        ],
    }

def _tool_agent_goal_add(server: Any, args: Dict[str, Any]) -> Dict[str, Any]:
    """Add a new agent goal."""
    planner = _get_goal_planner()
    description = args.get("description", "").strip()
    if not description:
        return {"error": "description is required"}

    metadata = args.get("metadata", {})
    if metadata is None or not isinstance(metadata, dict):
        metadata = {}

    goal_id = planner.add_goal(description, metadata=metadata)
    return {
        "ok": True,
        "goal_id": goal_id,
        "storage_dir": str(planner.storage_dir),
    }

def _tool_agent_goal_start(server: Any, args: Dict[str, Any]) -> Dict[str, Any]:
    """Start an existing goal."""
    planner = _get_goal_planner()
    goal_id = str(args.get("id", "")).strip()
    if not goal_id:
        return {"error": "id is required"}

    success = planner.start_goal(goal_id)
    goal = planner.get_goal(goal_id)
    return {
        "ok": success,
        "goal_id": goal_id,
        "state": goal.state.value if goal else None,
    }

def _tool_agent_goal_cancel(server: Any, args: Dict[str, Any]) -> Dict[str, Any]:
    """Cancel an existing goal."""
    planner = _get_goal_planner()
    goal_id = str(args.get("id", "")).strip()
    if not goal_id:
        return {"error": "id is required"}

    success = planner.cancel_goal(goal_id)
    goal = planner.get_goal(goal_id)
    return {
        "ok": success,
        "goal_id": goal_id,
        "state": goal.state.value if goal else None,
    }

def _read_recent_remediation_log(limit: int = 100) -> List[Dict[str, Any]]:
    """Parse recent remediation log lines into structured entries."""
    log_path = Path.home() / '.navig' / 'logs' / 'remediation.log'
    if not log_path.exists():
        return []

    lines = log_path.read_text(encoding='utf-8', errors='replace').splitlines()
    entries: List[Dict[str, Any]] = []
    regex = re.compile(r'^\[(?P<ts>[^\]]+)\] \[(?P<level>[^\]]+)\] (?P<msg>.*)$')
    for line in lines[-limit:]:
        m = regex.match(line)
        if not m:
            continue
        entries.append(
            {
                "timestamp": m.group('ts'),
                "level": m.group('level').lower(),
                "message": m.group('msg'),
            }
        )
    return entries

def _tool_agent_remediation_list(server: Any, args: Dict[str, Any]) -> Dict[str, Any]:
    """List remediation actions and recent remediation log lines."""
    limit = max(1, int(args.get("limit", 100)))
    actions: List[Dict[str, Any]] = []
    source = "none"

    try:
        from navig.agent.remediation import RemediationEngine
        engine = RemediationEngine()
        actions = engine.get_all_actions()
        if actions:
            source = "actions_file"
    except Exception:
        actions = []

    log_entries = _read_recent_remediation_log(limit=limit)
    if source == "none" and log_entries:
        source = "log_only"

    return {
        "source": source,
        "count": len(actions),
        "actions": actions[:limit],
        "recent_log_entries": log_entries,
    }

def _tool_agent_learning_run(server: Any, args: Dict[str, Any]) -> Dict[str, Any]:
    """Analyze agent logs and return pattern counts plus recommendations."""
    from collections import defaultdict

    days = max(1, int(args.get("days", 7)))
    export = bool(args.get("export", False))
    cutoff = datetime.now() - timedelta(days=days)
    log_dir = Path.home() / '.navig' / 'logs'
    debug_log = log_dir / 'debug.log'
    remediation_log = log_dir / 'remediation.log'

    patterns = {
        'connection_failed': r'connection.*(failed|refused|timeout)',
        'permission_denied': r'permission denied|access denied',
        'config_error': r'config.*error|invalid.*config',
        'component_error': r'component.*error|failed to start',
        'resource_exhausted': r'out of memory|disk full|quota exceeded',
    }

    counts = defaultdict(int)
    examples = defaultdict(list)
    ts_regex = re.compile(r'^\[(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]')

    for log_file in (debug_log, remediation_log):
        if not log_file.exists():
            continue
        with open(log_file, 'r', encoding='utf-8', errors='ignore') as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                ts_match = ts_regex.match(line)
                if ts_match:
                    try:
                        line_ts = datetime.strptime(ts_match.group('ts'), '%Y-%m-%d %H:%M:%S')
                        if line_ts < cutoff:
                            continue
                    except ValueError:
                        pass  # malformed value; skip

                for pattern_name, pattern in patterns.items():
                    if re.search(pattern, line, re.IGNORECASE):
                        counts[pattern_name] += 1
                        if len(examples[pattern_name]) < 3:
                            examples[pattern_name].append(line)

    recommendations: List[str] = []
    if counts.get('connection_failed', 0) > 10:
        recommendations.append('Review network connectivity and firewall rules.')
    if counts.get('permission_denied', 0) > 5:
        recommendations.append('Check file permissions and user access rights.')
    if counts.get('config_error', 0) > 3:
        recommendations.append('Validate configuration files for syntax/structure errors.')
    if counts.get('component_error', 0) > 5:
        recommendations.append('Investigate recurring component lifecycle failures.')
    if counts.get('resource_exhausted', 0) > 0:
        recommendations.append('Critical: check host memory/disk pressure immediately.')

    result = {
        'analyzed_at': datetime.now().isoformat(),
        'days': days,
        'total_errors': int(sum(counts.values())),
        'patterns': {
            name: {'count': count, 'examples': examples[name]}
            for name, count in counts.items()
        },
        'recommendations': recommendations,
    }

    if export:
        output_path = Path.home() / '.navig' / 'workspace' / 'error-patterns.json'
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(result, indent=2), encoding='utf-8')
        result['exported_to'] = str(output_path)

    return result

def _get_service_capabilities() -> Dict[str, Any]:
    """Return platform/elevation capability flags for service operations."""
    system = platform.system().lower()
    is_elevated = False
    if system == "windows":
        try:
            import ctypes
            is_elevated = bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            is_elevated = False
        return {
            "can_install": is_elevated,
            "can_uninstall": is_elevated,
            "requires_elevation": not is_elevated,
            "is_elevated": is_elevated,
        }

    if system in ("linux", "darwin"):
        if hasattr(os, "geteuid"):
            try:
                is_elevated = os.geteuid() == 0
            except Exception:
                is_elevated = False
        return {
            "can_install": True,
            "can_uninstall": True,
            "requires_elevation": False,
            "is_elevated": is_elevated,
        }

    return {
        "can_install": False,
        "can_uninstall": False,
        "requires_elevation": False,
        "is_elevated": False,
    }

def _tool_agent_service_status(server: Any, args: Dict[str, Any]) -> Dict[str, Any]:
    """Return OS service status for NAVIG agent."""
    capabilities = _get_service_capabilities()
    try:
        from navig.agent.service import ServiceInstaller
        installer = ServiceInstaller()
        is_running, status_text = installer.status()
        return {
            "running": bool(is_running),
            "platform": installer.system,
            "status": status_text,
            **capabilities,
        }
    except Exception as e:
        return {
            "error": f"service status failed: {e}",
            **capabilities,
        }

def _tool_agent_component_restart(server: Any, args: Dict[str, Any]) -> Dict[str, Any]:
    """Queue a component restart through remediation engine."""
    from navig.agent.remediation import RemediationEngine

    component = str(args.get("component", "")).strip()
    if not component:
        return {"error": "component is required"}

    reason = str(args.get("reason", "requested_via_mcp")).strip() or "requested_via_mcp"
    metadata = args.get("metadata")
    if metadata is None or not isinstance(metadata, dict):
        metadata = {}

    engine = RemediationEngine()
    action_id = engine.schedule_restart_sync(
        component=component,
        reason=reason,
        metadata=metadata,
    )
    return {
        "ok": True,
        "action_id": action_id,
        "action": engine.get_action_status(action_id),
    }

def _tool_agent_remediation_retry(server: Any, args: Dict[str, Any]) -> Dict[str, Any]:
    """Retry a remediation action by ID."""
    from navig.agent.remediation import RemediationEngine

    action_id = str(args.get("id", "")).strip()
    if not action_id:
        return {"error": "id is required"}

    reset_attempts = bool(args.get("reset_attempts", True))
    engine = RemediationEngine()
    ok = engine.retry_action(action_id, reset_attempts=reset_attempts)
    if not ok:
        return {"ok": False, "error": f"action not found: {action_id}", "action_id": action_id}

    return {
        "ok": True,
        "action_id": action_id,
        "action": engine.get_action_status(action_id),
    }

def _tool_agent_service_install(server: Any, args: Dict[str, Any]) -> Dict[str, Any]:
    """Install NAVIG agent as service."""
    try:
        from navig.agent.service import ServiceInstaller
        installer = ServiceInstaller()
        start_now = bool(args.get("start_now", True))
        success, message = installer.install(start_now=start_now)
        return {
            "ok": bool(success),
            "platform": installer.system,
            "message": message,
        }
    except Exception as e:
        return {"error": f"service install failed: {e}"}

def _tool_agent_service_uninstall(server: Any, args: Dict[str, Any]) -> Dict[str, Any]:
    """Uninstall NAVIG agent service."""
    try:
        from navig.agent.service import ServiceInstaller
        installer = ServiceInstaller()
        success, message = installer.uninstall()
        return {
            "ok": bool(success),
            "platform": installer.system,
            "message": message,
        }
    except Exception as e:
        return {"error": f"service uninstall failed: {e}"}
