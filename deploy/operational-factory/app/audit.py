from datetime import datetime, timezone

from sqlalchemy import text


def sanitize(payload):
    if payload is None:
        return None
    if isinstance(payload, dict):
        masked = {}
        for k, v in payload.items():
            key = str(k).lower()
            if any(secret in key for secret in ["password", "token", "secret", "key"]):
                masked[k] = "***"
            else:
                masked[k] = sanitize(v)
        return masked
    if isinstance(payload, list):
        return [sanitize(x) for x in payload]
    if isinstance(payload, (str, int, float, bool)) or payload is None:
        return payload
    return str(payload)


def write_audit(
    session,
    *,
    tenant_id=None,
    actor_type="agent",
    actor_id=None,
    service=None,
    action: str,
    reason=None,
    input_payload=None,
    output_payload=None,
    status="ok",
    error_message=None,
):
    session.execute(
        text(
            """
            INSERT INTO audit_log
            (tenant_id, actor_type, actor_id, service, action, reason,
             input_sanitized, output_sanitized, status, error_message, created_at)
            VALUES
            (:tenant_id, :actor_type, :actor_id, :service, :action, :reason,
             CAST(:input_sanitized AS jsonb), CAST(:output_sanitized AS jsonb), :status, :error_message, :created_at)
            """
        ),
        {
            "tenant_id": tenant_id,
            "actor_type": actor_type,
            "actor_id": actor_id,
            "service": service,
            "action": action,
            "reason": reason,
            "input_sanitized": __import__("json").dumps(sanitize(input_payload)),
            "output_sanitized": __import__("json").dumps(sanitize(output_payload)),
            "status": status,
            "error_message": error_message,
            "created_at": datetime.now(timezone.utc),
        },
    )
