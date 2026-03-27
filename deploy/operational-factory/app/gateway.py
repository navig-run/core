import json
from datetime import datetime, timezone

import httpx
from app.audit import write_audit
from app.db import db_session, fetch_all_dict, fetch_one_dict
from app.policies import validate_request
from app.rate_limit import enforce_rate_limit
from app.sandbox import run_bounded_log_read
from app.settings import SANDBOX_URL
from fastapi import FastAPI, HTTPException
from sqlalchemy import text

app = FastAPI(title="NAVIG Tool Gateway", version="0.1.0")


def _tenant_id(session, slug: str):
    row = fetch_one_dict(
        session, "SELECT id FROM tenants WHERE slug = :slug", {"slug": slug}
    )
    if not row:
        raise HTTPException(status_code=404, detail=f"Unknown tenant: {slug}")
    return row["id"]


def _queue_restricted_action(
    session, *, tenant_id, actor_id, action_name, params, reason
):
    row = fetch_one_dict(
        session,
        """
        INSERT INTO proposed_actions
        (tenant_id, action_name, action_payload, risk_level, reason, status, requested_by_agent, requires_approval, created_at, updated_at)
        VALUES (:tenant_id, :action_name, CAST(:payload AS jsonb), 'restricted', :reason, 'pending', :actor_id, true, :now, :now)
        RETURNING id, status
        """,
        {
            "tenant_id": tenant_id,
            "action_name": action_name,
            "payload": json.dumps(params),
            "reason": reason,
            "actor_id": actor_id,
            "now": datetime.now(timezone.utc),
        },
    )
    return row


def _execute_safe(action_name: str, params: dict):
    if action_name == "repo_scan_readonly":
        resp = httpx.post(
            f"{SANDBOX_URL}/repo/scan",
            json={"repo_path": params["repo_path"]},
            timeout=60,
        )
        resp.raise_for_status()
        return resp.json()
    if action_name == "sandbox_patch_generate":
        resp = httpx.post(
            f"{SANDBOX_URL}/repo/patch",
            json={
                "repo_path": params["repo_path"],
                "instructions": params.get("instructions", ""),
            },
            timeout=120,
        )
        resp.raise_for_status()
        return resp.json()
    if action_name == "sandbox_lint_test":
        resp = httpx.post(
            f"{SANDBOX_URL}/repo/test",
            json={"repo_path": params["repo_path"], "command": params["command"]},
            timeout=180,
        )
        resp.raise_for_status()
        return resp.json()
    if action_name == "read_logs_bounded":
        return run_bounded_log_read(params["path"], int(params.get("lines", 200)))
    if action_name in {"imap_read", "create_email_draft", "create_task"}:
        return {"accepted": True, "action": action_name}
    return {"accepted": True, "action": action_name, "note": "No-op safe placeholder"}


@app.get("/health")
def health():
    return {"ok": True, "service": "tool-gateway"}


@app.post("/tool/execute")
def execute_tool(payload: dict):
    try:
        req, spec = validate_request(payload)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    with db_session() as session:
        tenant_id = _tenant_id(session, req.tenant_slug)

        if not enforce_rate_limit(
            session,
            actor_id=req.actor_id,
            action_name=req.action_name,
            max_frequency=spec.max_frequency,
        ):
            write_audit(
                session,
                tenant_id=tenant_id,
                actor_type="agent",
                actor_id=req.actor_id,
                service="tool-gateway",
                action=req.action_name,
                reason=req.reason,
                input_payload=req.params,
                output_payload={"error": "rate_limited"},
                status="blocked",
                error_message="rate limit exceeded",
            )
            raise HTTPException(status_code=429, detail="rate limit exceeded")

        if spec.risk_level == "restricted":
            queued = _queue_restricted_action(
                session,
                tenant_id=tenant_id,
                actor_id=req.actor_id,
                action_name=req.action_name,
                params=req.params,
                reason=req.reason,
            )
            write_audit(
                session,
                tenant_id=tenant_id,
                actor_type="agent",
                actor_id=req.actor_id,
                service="tool-gateway",
                action=req.action_name,
                reason=req.reason,
                input_payload=req.params,
                output_payload={"queued_action_id": queued["id"]},
                status="queued_for_approval",
            )
            return {"status": "queued_for_approval", "proposed_action_id": queued["id"]}

        output = _execute_safe(req.action_name, req.params)
        write_audit(
            session,
            tenant_id=tenant_id,
            actor_type="agent",
            actor_id=req.actor_id,
            service="tool-gateway",
            action=req.action_name,
            reason=req.reason,
            input_payload=req.params,
            output_payload=output,
            status="executed",
        )
        return {"status": "executed", "output": output}


@app.get("/approval/inbox")
def approval_inbox():
    with db_session() as session:
        actions = fetch_all_dict(
            session,
            """
            SELECT id, action_name, action_payload, reason, status, requested_by_agent, created_at
            FROM proposed_actions
            WHERE status = 'pending'
            ORDER BY created_at ASC
            """,
        )
        drafts = fetch_all_dict(
            session,
            """
            SELECT id, draft_type, subject, preview, body, created_by_agent, status, created_at
            FROM drafts
            WHERE status IN ('pending_approval','approved')
            ORDER BY created_at DESC
            LIMIT 100
            """,
        )
        return {"actions": actions, "drafts": drafts}


@app.post("/approval/{action_id}/decision")
def decision(action_id: str, payload: dict):
    decision_value = payload.get("decision")
    decided_by = payload.get("decided_by", "owner")
    notes = payload.get("notes", "")
    if decision_value not in {"approved", "rejected"}:
        raise HTTPException(
            status_code=400, detail="decision must be approved|rejected"
        )

    with db_session() as session:
        action = fetch_one_dict(
            session,
            "SELECT id, tenant_id, action_name, action_payload, requested_by_agent FROM proposed_actions WHERE id = :id",
            {"id": action_id},
        )
        if not action:
            raise HTTPException(status_code=404, detail="proposed action not found")

        session.execute(
            text(
                "UPDATE proposed_actions SET status = :status, updated_at = :ts WHERE id = :id"
            ),
            {
                "status": decision_value,
                "ts": datetime.now(timezone.utc),
                "id": action_id,
            },
        )
        session.execute(
            text(
                """
                INSERT INTO approvals (proposed_action_id, decided_by, decision, notes, created_at)
                VALUES (:aid, :by, :decision, :notes, :ts)
                """
            ),
            {
                "aid": action_id,
                "by": decided_by,
                "decision": decision_value,
                "notes": notes,
                "ts": datetime.now(timezone.utc),
            },
        )

        write_audit(
            session,
            tenant_id=action["tenant_id"],
            actor_type="human",
            actor_id=decided_by,
            service="tool-gateway",
            action=f"approval:{action['action_name']}",
            reason=notes,
            input_payload={"action_id": action_id, "decision": decision_value},
            output_payload={"result": "recorded"},
            status=decision_value,
        )

        return {"status": decision_value, "id": action_id}
