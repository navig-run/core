from datetime import datetime, timezone
import imaplib
import email
from email.header import decode_header
import json
import os

import httpx
from fastapi import FastAPI, HTTPException
from sqlalchemy import text

from app.db import db_session, fetch_one_dict, fetch_all_dict
from app.settings import DEFAULT_TENANT, TOOL_GATEWAY_URL, OLLAMA_BASE_URL, PRIMARY_MODEL, CODE_MODEL, REPO_PATH
from app.audit import write_audit

app = FastAPI(title="NAVIG Runtime", version="0.1.0")

AGENTS = {
    "executive_assistant": "Executive Assistant",
    "email_support": "Email Support Agent",
    "sales_bd": "Sales/BD Agent",
    "dev_agent": "Dev Agent",
    "ops_agent": "Ops Agent",
    "advisor_legal": "Advisor Legal",
    "advisor_product": "Advisor Product",
    "advisor_marketing": "Advisor Marketing",
}


def _tenant_id(session):
    row = fetch_one_dict(session, "SELECT id FROM tenants WHERE slug = :slug", {"slug": DEFAULT_TENANT})
    if not row:
        raise HTTPException(status_code=500, detail="default tenant missing")
    return row["id"]


def _model_for(task_type: str):
    if task_type in {"repo", "patch", "code"}:
        return CODE_MODEL
    return PRIMARY_MODEL


def _call_ollama(prompt: str, task_type: str = "general"):
    model = _model_for(task_type)
    try:
        resp = httpx.post(
            f"{OLLAMA_BASE_URL}/api/generate",
            json={"model": model, "prompt": prompt, "stream": False},
            timeout=90,
        )
        if resp.status_code == 404 and model != PRIMARY_MODEL:
            resp = httpx.post(
                f"{OLLAMA_BASE_URL}/api/generate",
                json={"model": PRIMARY_MODEL, "prompt": prompt, "stream": False},
                timeout=90,
            )
            resp.raise_for_status()
            return {"model": PRIMARY_MODEL, "text": resp.json().get("response", ""), "fallback_from": model}

        resp.raise_for_status()
        return {"model": model, "text": resp.json().get("response", "")}
    except Exception as exc:
        return {"model": model, "text": f"Fallback summary (LLM unavailable): {exc}"}


def _artifact_summary(plan_text: str, preview: dict) -> str:
    if plan_text and not plan_text.startswith("Fallback summary (LLM unavailable):"):
        return plan_text

    changed_files = preview.get("changed_files") or []
    diff_summary = (preview.get("diff_summary") or "").strip()
    test_exit_code = preview.get("test_exit_code")

    lines = ["Automated PR proposal generated from sandbox artifacts."]
    lines.append(f"Changed files: {len(changed_files)}")
    if changed_files:
        lines.append("Files: " + ", ".join(changed_files[:5]))

    if isinstance(test_exit_code, int):
        lines.append(f"Sandbox verification exit code: {test_exit_code}")

    if diff_summary:
        lines.append("Diff summary: " + diff_summary[:500])

    if plan_text and plan_text.startswith("Fallback summary (LLM unavailable):"):
        lines.append("LLM note: fallback mode was used.")

    return "\n".join(lines)


def _imap_fetch(limit: int = 10):
    host = os.getenv("IMAP_HOST", "")
    user = os.getenv("IMAP_USER", "")
    password = os.getenv("IMAP_PASSWORD", "")
    folder = os.getenv("IMAP_FOLDER", "INBOX")
    port = int(os.getenv("IMAP_PORT", "993"))

    if not host or not user or not password:
        return [
            {"id": "demo-1", "from": "client-a@example.com", "subject": "Need a quote for integration", "body": "Can you send pricing for 10 seats?"},
            {"id": "demo-2", "from": "support-user@example.com", "subject": "Issue with login", "body": "User cannot login after password reset."},
            {"id": "demo-3", "from": "partner@example.com", "subject": "Partnership proposal", "body": "Let's discuss co-marketing campaign."},
        ]

    mails = []
    with imaplib.IMAP4_SSL(host, port) as mail:
        mail.login(user, password)
        mail.select(folder)
        _, data = mail.search(None, "ALL")
        message_ids = data[0].split()[-limit:]
        for msg_id in message_ids:
            _, msg_data = mail.fetch(msg_id, "(RFC822)")
            msg = email.message_from_bytes(msg_data[0][1])
            subj = decode_header(msg.get("Subject", ""))[0][0]
            if isinstance(subj, bytes):
                subj = subj.decode(errors="replace")
            sender = msg.get("From", "")
            body = ""
            if msg.is_multipart():
                for part in msg.walk():
                    if part.get_content_type() == "text/plain":
                        body = part.get_payload(decode=True).decode(errors="replace")
                        break
            else:
                body = msg.get_payload(decode=True).decode(errors="replace")
            mails.append({"id": msg_id.decode(), "from": sender, "subject": subj, "body": body[:3000]})
    return mails


def _classify_email(subject: str, body: str):
    text = (subject + "\n" + body).lower()
    if "quote" in text or "pricing" in text:
        return "sales"
    if "issue" in text or "error" in text or "login" in text:
        return "support"
    return "general"


@app.get("/health")
def health():
    return {"ok": True, "service": "navig-runtime"}


@app.post("/flow/email/intake")
def flow_email_intake(payload: dict):
    limit = int(payload.get("limit", 10))
    mails = _imap_fetch(limit)

    created_drafts = []
    with db_session() as session:
        tenant_id = _tenant_id(session)
        for mail in mails[:3]:
            category = _classify_email(mail["subject"], mail["body"])
            agent = "email_support" if category == "support" else ("sales_bd" if category == "sales" else "executive_assistant")
            prompt = f"Draft a concise professional reply.\nSubject: {mail['subject']}\nBody: {mail['body']}"
            draft_body = _call_ollama(prompt, task_type="general")["text"]

            draft = fetch_one_dict(
                session,
                """
                INSERT INTO drafts
                (tenant_id, draft_type, source_ref, subject, body, preview, status, created_by_agent, metadata, created_at, updated_at)
                VALUES (:tenant_id, 'email', :source_ref, :subject, :body, :preview, 'pending_approval', :agent, CAST(:meta AS jsonb), :ts, :ts)
                RETURNING id, subject
                """,
                {
                    "tenant_id": tenant_id,
                    "source_ref": mail["id"],
                    "subject": f"Re: {mail['subject']}",
                    "body": draft_body,
                    "preview": draft_body[:200],
                    "agent": agent,
                    "meta": json.dumps({"from": mail["from"], "category": category}),
                    "ts": datetime.now(timezone.utc),
                },
            )

            action_payload = {
                "tenant_slug": DEFAULT_TENANT,
                "actor_id": agent,
                "action_name": "send_email",
                "reason": f"Drafted reply for inbound {category} email",
                "params": {
                    "to": mail["from"],
                    "subject": f"Re: {mail['subject']}",
                    "body": draft_body,
                    "draft_id": str(draft["id"]),
                },
            }
            response = httpx.post(f"{TOOL_GATEWAY_URL}/tool/execute", json=action_payload, timeout=20)
            response.raise_for_status()

            created_drafts.append({"draft_id": str(draft["id"]), "subject": draft["subject"], "queued": response.json()})

        write_audit(
            session,
            tenant_id=tenant_id,
            actor_type="agent",
            actor_id="executive_assistant",
            service="navig-runtime",
            action="flow:email:intake",
            reason="IMAP classify and draft",
            input_payload={"limit": limit},
            output_payload={"draft_count": len(created_drafts)},
            status="ok",
        )

    return {"created_drafts": created_drafts}


@app.post("/flow/repo/propose")
def flow_repo_propose(payload: dict):
    repo_path = payload.get("repo_path", REPO_PATH)
    actor = "dev_agent"

    with db_session() as session:
        tenant_id = _tenant_id(session)

        scan_resp = httpx.post(
            f"{TOOL_GATEWAY_URL}/tool/execute",
            json={
                "tenant_slug": DEFAULT_TENANT,
                "actor_id": actor,
                "action_name": "repo_scan_readonly",
                "reason": "Daily repo health scan",
                "params": {"repo_path": repo_path},
            },
            timeout=20,
        )
        scan_resp.raise_for_status()

        plan_prompt = "Propose safe PR plan from git status output: " + json.dumps(scan_resp.json())
        plan = _call_ollama(plan_prompt, task_type="repo")["text"]

        draft = fetch_one_dict(
            session,
            """
            INSERT INTO drafts
            (tenant_id, draft_type, source_ref, subject, body, preview, status, created_by_agent, metadata, created_at, updated_at)
            VALUES (:tenant_id, 'pr_plan', :source_ref, :subject, :body, :preview, 'pending_approval', :agent, CAST(:meta AS jsonb), :ts, :ts)
            RETURNING id, subject
            """,
            {
                "tenant_id": tenant_id,
                "source_ref": repo_path,
                "subject": "Proposed PR plan",
                "body": plan,
                "preview": plan[:200],
                "agent": actor,
                "meta": json.dumps({"repo_path": repo_path}),
                "ts": datetime.now(timezone.utc),
            },
        )

        patch_resp = httpx.post(
            f"{TOOL_GATEWAY_URL}/tool/execute",
            json={
                "tenant_slug": DEFAULT_TENANT,
                "actor_id": actor,
                "action_name": "sandbox_patch_generate",
                "reason": "Generate patch plan in sandbox",
                "params": {"repo_path": repo_path, "instructions": plan[:1200]},
            },
            timeout=60,
        )
        patch_resp.raise_for_status()
        patch_data = patch_resp.json()

        test_resp = httpx.post(
            f"{TOOL_GATEWAY_URL}/tool/execute",
            json={
                "tenant_slug": DEFAULT_TENANT,
                "actor_id": actor,
                "action_name": "sandbox_lint_test",
                "reason": "Validate candidate patch in sandbox",
                "params": {"repo_path": repo_path, "command": "python -m compileall -q ."},
            },
            timeout=120,
        )
        test_resp.raise_for_status()
        test_data = test_resp.json()

        preview = {
            "diff_summary": patch_data.get("output", {}).get("diff_summary") if isinstance(patch_data, dict) else "",
            "changed_files": patch_data.get("output", {}).get("changed_files", []) if isinstance(patch_data, dict) else [],
            "commands": patch_data.get("output", {}).get("commands", []) if isinstance(patch_data, dict) else [],
            "test_exit_code": test_data.get("output", {}).get("result", {}).get("exit_code") if isinstance(test_data, dict) else None,
            "test_stdout": (test_data.get("output", {}).get("result", {}).get("stdout", "")[:1200] if isinstance(test_data, dict) else ""),
        }

        summary_text = _artifact_summary(plan, preview)

        merge_action = httpx.post(
            f"{TOOL_GATEWAY_URL}/tool/execute",
            json={
                "tenant_slug": DEFAULT_TENANT,
                "actor_id": actor,
                "action_name": "merge_pr",
                "reason": "Merge must stay approval-gated",
                "params": {
                    "repo": "local",
                    "pr": "draft-plan",
                    "summary": summary_text[:500],
                    "preview": preview,
                },
            },
            timeout=20,
        )
        merge_action.raise_for_status()

        write_audit(
            session,
            tenant_id=tenant_id,
            actor_type="agent",
            actor_id=actor,
            service="navig-runtime",
            action="flow:repo:propose",
            reason="Read-only scan and PR proposal",
            input_payload={"repo_path": repo_path},
            output_payload={"draft_id": str(draft["id"])},
            status="ok",
        )

    return {
        "draft": draft,
        "scan": scan_resp.json(),
        "patch": patch_resp.json(),
        "test": test_resp.json(),
        "merge": merge_action.json(),
    }


@app.post("/flow/briefing/daily")
def flow_daily_briefing():
    with db_session() as session:
        tenant_id = _tenant_id(session)
        pending_actions = fetch_all_dict(session, "SELECT id, action_name, requested_by_agent, created_at FROM proposed_actions WHERE status='pending' ORDER BY created_at ASC LIMIT 50")
        recent_drafts = fetch_all_dict(session, "SELECT id, draft_type, subject, created_by_agent, created_at FROM drafts ORDER BY created_at DESC LIMIT 50")

        lines = ["Daily Operational Briefing", "", "Agents report:"]
        for agent_id, role in AGENTS.items():
            lines.append(f"- {role}: active")

        lines.append("")
        lines.append(f"Pending approvals: {len(pending_actions)}")
        for item in pending_actions[:10]:
            lines.append(f"  - {item['action_name']} by {item['requested_by_agent']}")

        lines.append("")
        lines.append(f"Recent drafts: {len(recent_drafts)}")
        for item in recent_drafts[:10]:
            lines.append(f"  - [{item['draft_type']}] {item['subject']}")

        briefing = "\n".join(lines)
        draft = fetch_one_dict(
            session,
            """
            INSERT INTO drafts
            (tenant_id, draft_type, source_ref, subject, body, preview, status, created_by_agent, metadata, created_at, updated_at)
            VALUES (:tenant_id, 'daily_briefing', 'system', :subject, :body, :preview, 'approved', 'executive_assistant', '{}'::jsonb, :ts, :ts)
            RETURNING id
            """,
            {
                "tenant_id": tenant_id,
                "subject": f"Daily Briefing {datetime.now(timezone.utc).date()}",
                "body": briefing,
                "preview": briefing[:220],
                "ts": datetime.now(timezone.utc),
            },
        )

        write_audit(
            session,
            tenant_id=tenant_id,
            actor_type="agent",
            actor_id="executive_assistant",
            service="navig-runtime",
            action="flow:briefing:daily",
            reason="Aggregate all agent statuses",
            input_payload={},
            output_payload={"draft_id": draft["id"]},
            status="ok",
        )

    return {"status": "ok", "briefing_draft_id": str(draft["id"])}


@app.get("/agents/contracts")
def contracts():
    base = "/srv/app/contracts"
    files = []
    for name in sorted(os.listdir(base)):
        if name.endswith(".yaml"):
            files.append(name)
    return {"contracts": files}
