import json
from datetime import datetime, timezone

from app.db import db_session, fetch_one_dict


def seed_agents():
    contracts = [
        (
            "executive_assistant",
            "Executive Assistant",
            "contracts/executive_assistant.yaml",
        ),
        ("email_support", "Email Support Agent", "contracts/email_support_agent.yaml"),
        ("sales_bd", "Sales/BD Agent", "contracts/sales_bd_agent.yaml"),
        ("dev_agent", "Dev Agent", "contracts/dev_agent.yaml"),
        ("ops_agent", "Ops Agent", "contracts/ops_agent.yaml"),
        ("advisor_bundle", "Advisor Agents", "contracts/advisor_agents.yaml"),
    ]

    with db_session() as session:
        tenant = fetch_one_dict(
            session, "SELECT id FROM tenants WHERE slug='solo-company'"
        )
        if not tenant:
            raise RuntimeError("tenant solo-company missing")
        tenant_id = tenant["id"]

        for key, role, path in contracts:
            exists = fetch_one_dict(
                session,
                "SELECT id FROM agents WHERE tenant_id=:tenant_id AND agent_key=:agent_key",
                {"tenant_id": tenant_id, "agent_key": key},
            )
            if exists:
                continue
            session.execute(
                __import__("sqlalchemy").text(
                    """
                    INSERT INTO agents (tenant_id, agent_key, role, contract_path, active, created_at)
                    VALUES (:tenant_id, :agent_key, :role, :contract_path, true, :created_at)
                    """
                ),
                {
                    "tenant_id": tenant_id,
                    "agent_key": key,
                    "role": role,
                    "contract_path": path,
                    "created_at": datetime.now(timezone.utc),
                },
            )


if __name__ == "__main__":
    seed_agents()
    print("Seed complete")
