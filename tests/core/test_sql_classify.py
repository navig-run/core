"""navig.core.sql_classify — the one SQL write classifier (CLI `db query` + deck DB route).

Regression: the old substring classifier called `SELECT * FROM deleted_items` critical
and waved `CALL`, `COPY`, `SET`, `MERGE` and data-modifying CTEs through as read-only.
"""

from __future__ import annotations

import pytest

from navig.core.sql_classify import classify_sql, statement_keywords


@pytest.mark.parametrize(
    "query",
    [
        "SELECT * FROM users",
        "select * from deleted_items where note = 'drop table x'",
        "SHOW TABLES",
        "DESCRIBE users",
        "EXPLAIN SELECT * FROM orders",
        "SELECT 1; -- DELETE FROM users",
        "/* TRUNCATE */ SELECT 1",
        "SELECT $$DROP TABLE x$$",
        'SELECT "delete" FROM t',
        "PRAGMA table_info(users)",
        "WITH recent AS (SELECT * FROM orders) SELECT count(*) FROM recent",
        "BEGIN; SELECT 1; COMMIT",
        "",
        "   ;  ; ",
    ],
)
def test_read_only_reads_as_verbose(query):
    assert classify_sql(query) == "verbose"


@pytest.mark.parametrize(
    "query",
    [
        "CALL refresh_all()",
        "COPY users TO '/tmp/u.csv'",
        "SET GLOBAL max_connections = 1",
        "MERGE INTO t USING s ON t.id = s.id WHEN MATCHED THEN UPDATE SET x = 1",
        "SELECT * INTO backup_users FROM users",
        "PRAGMA journal_mode = DELETE",
        "LOAD DATA INFILE 'x' INTO TABLE t",
        "VACUUM",
        "WITH moved AS (INSERT INTO archive SELECT * FROM t RETURNING id) SELECT * FROM moved",
        "SELECT 1; UPDATE users SET admin = 1",
        "EXPLAIN ANALYZE UPDATE users SET x = 1",
    ],
)
def test_writes_and_unknowns_fail_closed(query):
    assert classify_sql(query) == "standard"


@pytest.mark.parametrize(
    "query",
    [
        "DELETE FROM orders",
        "WITH gone AS (DELETE FROM orders RETURNING id) SELECT * FROM gone",
        "SELECT 1; DROP TABLE users",
        "EXPLAIN (ANALYZE) DELETE FROM orders",
        "begin; truncate t; commit",
    ],
)
def test_removals_are_critical(query):
    assert classify_sql(query) == "critical"


def test_statement_keywords_carry_no_data():
    assert statement_keywords("SELECT secret FROM t WHERE pw = 'hunter2'; DELETE FROM t") == [
        "SELECT",
        "DELETE",
    ]
