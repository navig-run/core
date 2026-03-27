from typing import Any, Dict

from app.settings import RESTRICTED_RATE_LIMIT, SAFE_RATE_LIMIT
from pydantic import BaseModel, Field


class ActionSpec(BaseModel):
    action_name: str
    risk_level: str = Field(pattern="^(safe|restricted)$")
    required_params: list[str]
    max_frequency: int


ALLOWLIST: Dict[str, ActionSpec] = {
    "imap_read": ActionSpec(
        action_name="imap_read",
        risk_level="safe",
        required_params=["limit"],
        max_frequency=SAFE_RATE_LIMIT,
    ),
    "create_email_draft": ActionSpec(
        action_name="create_email_draft",
        risk_level="safe",
        required_params=["subject", "body", "to"],
        max_frequency=SAFE_RATE_LIMIT,
    ),
    "create_task": ActionSpec(
        action_name="create_task",
        risk_level="safe",
        required_params=["title"],
        max_frequency=SAFE_RATE_LIMIT,
    ),
    "repo_scan_readonly": ActionSpec(
        action_name="repo_scan_readonly",
        risk_level="safe",
        required_params=["repo_path"],
        max_frequency=SAFE_RATE_LIMIT,
    ),
    "sandbox_patch_generate": ActionSpec(
        action_name="sandbox_patch_generate",
        risk_level="safe",
        required_params=["repo_path", "instructions"],
        max_frequency=SAFE_RATE_LIMIT,
    ),
    "sandbox_lint_test": ActionSpec(
        action_name="sandbox_lint_test",
        risk_level="safe",
        required_params=["repo_path", "command"],
        max_frequency=SAFE_RATE_LIMIT,
    ),
    "read_logs_bounded": ActionSpec(
        action_name="read_logs_bounded",
        risk_level="safe",
        required_params=["path", "lines"],
        max_frequency=SAFE_RATE_LIMIT,
    ),
    "send_email": ActionSpec(
        action_name="send_email",
        risk_level="restricted",
        required_params=["to", "subject", "body"],
        max_frequency=RESTRICTED_RATE_LIMIT,
    ),
    "merge_pr": ActionSpec(
        action_name="merge_pr",
        risk_level="restricted",
        required_params=["repo", "pr"],
        max_frequency=RESTRICTED_RATE_LIMIT,
    ),
    "deploy_production": ActionSpec(
        action_name="deploy_production",
        risk_level="restricted",
        required_params=["target"],
        max_frequency=RESTRICTED_RATE_LIMIT,
    ),
    "network_modify": ActionSpec(
        action_name="network_modify",
        risk_level="restricted",
        required_params=["command"],
        max_frequency=RESTRICTED_RATE_LIMIT,
    ),
    "pay_invoice": ActionSpec(
        action_name="pay_invoice",
        risk_level="restricted",
        required_params=["amount", "vendor"],
        max_frequency=RESTRICTED_RATE_LIMIT,
    ),
    "delete_data": ActionSpec(
        action_name="delete_data",
        risk_level="restricted",
        required_params=["resource"],
        max_frequency=RESTRICTED_RATE_LIMIT,
    ),
    "mass_message": ActionSpec(
        action_name="mass_message",
        risk_level="restricted",
        required_params=["channel", "message", "audience_size"],
        max_frequency=RESTRICTED_RATE_LIMIT,
    ),
}


class ActionRequest(BaseModel):
    tenant_slug: str
    actor_id: str
    action_name: str
    params: Dict[str, Any]
    reason: str = ""


def validate_request(payload: Dict[str, Any]) -> tuple[ActionRequest, ActionSpec]:
    req = ActionRequest(**payload)
    spec = ALLOWLIST.get(req.action_name)
    if not spec:
        raise ValueError(f"Action not allowlisted: {req.action_name}")
    missing = [p for p in spec.required_params if p not in req.params]
    if missing:
        raise ValueError(f"Missing required params: {', '.join(missing)}")
    return req, spec
