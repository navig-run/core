from pathlib import Path
from sqlalchemy import text

from app.db import db_session


def apply_migrations():
    migrations_dir = Path('/srv/app/migrations')
    files = sorted(migrations_dir.glob('*.sql'))
    with db_session() as session:
        session.execute(text("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                filename TEXT PRIMARY KEY,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """))
        applied = {
            row[0]
            for row in session.execute(text("SELECT filename FROM schema_migrations")).all()
        }

        for migration in files:
            if migration.name in applied:
                continue
            sql = migration.read_text(encoding='utf-8')
            if '\\i ' in sql:
                sql = sql.replace('\\i /srv/app/init-db.sql', Path('/srv/app/init-db.sql').read_text(encoding='utf-8'))
            for stmt in [s.strip() for s in sql.split(';') if s.strip()]:
                session.execute(text(stmt))
            session.execute(text("INSERT INTO schema_migrations(filename) VALUES (:name)"), {"name": migration.name})


if __name__ == '__main__':
    apply_migrations()
    print('Migrations applied')
