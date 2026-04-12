"""
NAVIG Knowledge Graph

Entity-relation graph backed by SQLite. Stores facts, habits, routines, and
preferences extracted from user interactions, AI conversations, and browser tasks.

Schema:
    entities  — named entities (Person, Website, Service, Routine, Preference, Task)
    facts     — subject-predicate-object triples with confidence and source
    routines  — scheduled routines with human-readable schedule descriptions

Usage:
    from navig.memory.knowledge_graph import get_knowledge_graph

    kg = get_knowledge_graph()

    # Store a fact
    kg.remember_fact("user", "pays_bills_on", "15th of month", source="user_statement")

    # Recall
    facts = kg.recall("user", predicate="pays_bills_on")

    # Store a routine
    kg.add_routine("pay bills", schedule="0 9 15 * *", description="Pay monthly bills on the 15th")
    routines = kg.get_routines()
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger("navig.memory.knowledge_graph")

# ─────────────────────────── schema ──────────────────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS entities (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    type        TEXT NOT NULL DEFAULT 'generic',   -- person | website | service | routine | preference | task
    metadata    TEXT DEFAULT '{}',                  -- JSON blob for extra fields
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS facts (
    id          TEXT PRIMARY KEY,
    subject     TEXT NOT NULL,         -- entity name or "user"
    predicate   TEXT NOT NULL,         -- relation (e.g. "pays_bills_on", "prefers", "uses_service")
    object      TEXT NOT NULL,         -- the value/entity (e.g. "15th of month", "dark mode")
    confidence  REAL DEFAULT 1.0,      -- 0.0 to 1.0
    source      TEXT DEFAULT 'unknown',-- "user_statement" | "habit_inference" | "task_result"
    valid_at    DATETIME,              -- NULL = always valid
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS facts_subject ON facts (subject);
CREATE INDEX IF NOT EXISTS facts_predicate ON facts (predicate);

CREATE TABLE IF NOT EXISTS routines (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    schedule    TEXT NOT NULL,  -- cron expression OR human trigger (e.g. "every 15th")
    description TEXT,
    task_spec   TEXT,           -- JSON: navig task payload to execute
    last_run    DATETIME,
    enabled     INTEGER DEFAULT 1,
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts USING fts5(
    subject, predicate, object, source,
    content='facts',
    content_rowid='rowid'
);

CREATE TRIGGER IF NOT EXISTS facts_fts_insert AFTER INSERT ON facts BEGIN
    INSERT INTO facts_fts(rowid, subject, predicate, object, source)
    VALUES (new.rowid, new.subject, new.predicate, new.object, new.source);
END;

CREATE TRIGGER IF NOT EXISTS facts_fts_delete AFTER DELETE ON facts BEGIN
    DELETE FROM facts_fts WHERE rowid=old.rowid;
END;
"""

# ─────────────────────────── data models ─────────────────────────────────────


class Fact:
    def __init__(self, row: dict[str, Any]) -> None:
        self.id: str = row["id"]
        self.subject: str = row["subject"]
        self.predicate: str = row["predicate"]
        self.object: str = row["object"]
        self.confidence: float = float(row.get("confidence") or 1.0)
        self.source: str = row.get("source") or "unknown"
        self.created_at: datetime = datetime.fromisoformat(row["created_at"])

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "subject": self.subject,
            "predicate": self.predicate,
            "object": self.object,
            "confidence": self.confidence,
            "source": self.source,
            "created_at": self.created_at.isoformat(),
        }

    def __repr__(self) -> str:
        return f"<Fact {self.subject!r} {self.predicate!r} {self.object!r} ({self.confidence:.0%})>"


class Routine:
    def __init__(self, row: dict[str, Any]) -> None:
        self.id: str = row["id"]
        self.name: str = row["name"]
        self.schedule: str = row["schedule"]
        self.description: str | None = row.get("description")
        self.task_spec: dict[str, Any] | None = (
            json.loads(row["task_spec"]) if row.get("task_spec") else None
        )
        self.last_run: datetime | None = (
            datetime.fromisoformat(row["last_run"]) if row.get("last_run") else None
        )
        self.enabled: bool = bool(row.get("enabled", True))
        self.created_at: datetime = datetime.fromisoformat(row["created_at"])

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "schedule": self.schedule,
            "description": self.description,
            "task_spec": self.task_spec,
            "last_run": self.last_run.isoformat() if self.last_run else None,
            "enabled": self.enabled,
        }


# ─────────────────────────── knowledge graph class ───────────────────────────


class KnowledgeGraph:
    """
    Entity-relation knowledge graph backed by SQLite.

    Stores structured facts as (subject, predicate, object) triples with
    confidence scores and source attribution. Also manages named routines
    (scheduled habits) with cron expressions.
    """

    def __init__(self, db_path: Path) -> None:
        self._path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._con = sqlite3.connect(str(db_path), check_same_thread=False)
        self._con.row_factory = sqlite3.Row
        self._con.execute("PRAGMA journal_mode=WAL")
        self._con.executescript(_SCHEMA)
        self._con.commit()

    # ─────────────────────── facts ─────────────────────────────────────────

    def remember_fact(
        self,
        subject: str,
        predicate: str,
        object_: str,
        *,
        confidence: float = 1.0,
        source: str = "unknown",
        overwrite: bool = False,
    ) -> str:
        """
        Store a fact triple. Returns the fact ID.

        If an identical (subject, predicate) already exists:
        - overwrite=True: updates the object value and confidence
        - overwrite=False: keeps both (multiple values allowed)
        """
        if overwrite:
            # Upsert: replace any existing fact with same subject+predicate
            self._con.execute(
                "DELETE FROM facts WHERE subject=? AND predicate=?",
                (subject, predicate),
            )

        fact_id = str(uuid.uuid4())[:8]
        self._con.execute(
            """INSERT INTO facts (id, subject, predicate, object, confidence, source)
               VALUES (?,?,?,?,?,?)""",
            (fact_id, subject, predicate, object_, confidence, source),
        )
        self._con.commit()
        return fact_id

    def recall(
        self,
        subject: str,
        predicate: str | None = None,
        min_confidence: float = 0.0,
    ) -> list[Fact]:
        """
        Retrieve facts about a subject. Optionally filter by predicate.
        Results ordered by confidence desc.
        """
        if predicate:
            rows = self._con.execute(
                "SELECT * FROM facts WHERE subject=? AND predicate=? AND confidence>=? ORDER BY confidence DESC",
                (subject, predicate, min_confidence),
            ).fetchall()
        else:
            rows = self._con.execute(
                "SELECT * FROM facts WHERE subject=? AND confidence>=? ORDER BY confidence DESC",
                (subject, min_confidence),
            ).fetchall()
        return [Fact(dict(r)) for r in rows]

    def recall_by_object(self, object_: str) -> list[Fact]:
        """Reverse lookup: find all facts with a given object value."""
        rows = self._con.execute(
            "SELECT * FROM facts WHERE object LIKE ? ORDER BY confidence DESC",
            (f"%{object_}%",),
        ).fetchall()
        return [Fact(dict(r)) for r in rows]

    def search_facts(self, query: str, limit: int = 20) -> list[Fact]:
        """Full-text search across subject, predicate, object."""
        if not query.strip():
            return []
        try:
            rows = self._con.execute(
                """SELECT f.* FROM facts f
                   INNER JOIN facts_fts fts ON f.rowid = fts.rowid
                   WHERE facts_fts MATCH ?
                   ORDER BY rank LIMIT ?""",
                (query, limit),
            ).fetchall()
        except sqlite3.OperationalError:
            like = f"%{query}%"
            rows = self._con.execute(
                "SELECT * FROM facts WHERE subject LIKE ? OR predicate LIKE ? OR object LIKE ? LIMIT ?",
                (like, like, like, limit),
            ).fetchall()
        return [Fact(dict(r)) for r in rows]

    def forget_fact(self, fact_id: str) -> bool:
        """Delete a specific fact by ID."""
        cur = self._con.execute("DELETE FROM facts WHERE id=?", (fact_id,))
        self._con.commit()
        return cur.rowcount > 0

    def get_preference(self, domain: str) -> str | None:
        """
        Get the user's preference for a given domain.
        E.g. get_preference("theme") → "dark"
        """
        facts = self.recall("user", predicate=f"prefers_{domain}", min_confidence=0.5)
        return facts[0].object if facts else None

    # ─────────────────────── routines ──────────────────────────────────────

    def add_routine(
        self,
        name: str,
        *,
        schedule: str,
        description: str | None = None,
        task_spec: dict[str, Any] | None = None,
    ) -> str:
        """
        Register a named routine.

        Args:
            schedule: cron expression (e.g. "0 9 15 * *") or human trigger
            task_spec: optional JSON task payload for the NAVIG browser/desktop agent
        """
        routine_id = str(uuid.uuid4())[:8]
        self._con.execute(
            """INSERT INTO routines (id, name, schedule, description, task_spec)
               VALUES (?,?,?,?,?)""",
            (
                routine_id,
                name,
                schedule,
                description,
                json.dumps(task_spec) if task_spec else None,
            ),
        )
        self._con.commit()
        return routine_id

    def get_routines(self, enabled_only: bool = True) -> list[Routine]:
        """List all registered routines."""
        if enabled_only:
            rows = self._con.execute(
                "SELECT * FROM routines WHERE enabled=1 ORDER BY name"
            ).fetchall()
        else:
            rows = self._con.execute("SELECT * FROM routines ORDER BY name").fetchall()
        return [Routine(dict(r)) for r in rows]

    def get_routine(self, routine_id: str) -> Routine | None:
        row = self._con.execute("SELECT * FROM routines WHERE id=?", (routine_id,)).fetchone()
        return Routine(dict(row)) if row else None

    def update_routine_last_run(self, routine_id: str) -> None:
        self._con.execute(
            "UPDATE routines SET last_run=CURRENT_TIMESTAMP WHERE id=?",
            (routine_id,),
        )
        self._con.commit()

    def disable_routine(self, routine_id: str) -> bool:
        cur = self._con.execute("UPDATE routines SET enabled=0 WHERE id=?", (routine_id,))
        self._con.commit()
        return cur.rowcount > 0

    def delete_routine(self, routine_id: str) -> bool:
        cur = self._con.execute("DELETE FROM routines WHERE id=?", (routine_id,))
        self._con.commit()
        return cur.rowcount > 0

    # ─────────────────────── auto-learning ─────────────────────────────────

    def learn_from_task_result(
        self,
        task_description: str,
        result_summary: str,
        llm_fn=None,
    ) -> list[str]:
        """
        After a successful task, optionally use an LLM to extract habits.

        Args:
            task_description: what the user asked to do
            result_summary: brief description of what happened
            llm_fn: callable(prompt:str) -> str; if None, skips LLM inference

        Returns:
            List of fact IDs of any newly stored facts.
        """
        if llm_fn is None:
            return []

        prompt = (
            f"Analyze this task and extract any recurring user habits or preferences as JSON.\n"
            f"Task: {task_description}\n"
            f"Result: {result_summary}\n"
            f"Output a JSON array of objects like: "
            f'[{{"subject":"user","predicate":"pays_bills_on","object":"15th of month","confidence":0.9}}]\n'
            f"Output only the JSON array, nothing else. Empty array if nothing found."
        )

        try:
            raw = llm_fn(prompt)
        except Exception as exc:  # noqa: BLE001
            logger.debug("KnowledgeGraph learn_from_task_result LLM call failed: %s", exc)
            return []

        try:
            import re

            match = re.search(r"\[.*\]", raw, re.DOTALL)
            if not match:
                return []
            triples = json.loads(match.group(0))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            logger.debug("KnowledgeGraph learn_from_task_result parsing failed: %s", exc)
            return []

        stored = []
        for triple in triples:
            subj = triple.get("subject", "user")
            pred = triple.get("predicate")
            obj = triple.get("object")
            conf = float(triple.get("confidence", 0.8))
            if pred and obj and conf >= 0.6:
                fid = self.remember_fact(subj, pred, obj, confidence=conf, source="habit_inference")
                stored.append(fid)

        return stored

    def close(self) -> None:
        self._con.close()


# ─────────────────────────── singleton ───────────────────────────────────────

_kg_instance: KnowledgeGraph | None = None
_kg_lock = threading.Lock()


def get_knowledge_graph() -> KnowledgeGraph:
    """Return the singleton KnowledgeGraph, initialised from the NAVIG data directory."""
    global _kg_instance
    if _kg_instance is None:
        with _kg_lock:
            if _kg_instance is None:
                from navig.config import get_config

                cfg = get_config()
                db_path = Path(cfg.data_dir) / "knowledge_graph.db"
                _kg_instance = KnowledgeGraph(db_path)
    return _kg_instance


def reset_knowledge_graph() -> None:
    """Reset singleton (for testing)."""
    global _kg_instance
    with _kg_lock:
        if _kg_instance is not None:
            _kg_instance.close()
        _kg_instance = None
