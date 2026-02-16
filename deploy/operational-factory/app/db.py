from contextlib import contextmanager
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.settings import DATABASE_URL

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


@contextmanager
def db_session():
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def fetch_one_dict(session, sql: str, params=None):
    row = session.execute(text(sql), params or {}).mappings().first()
    return dict(row) if row else None


def fetch_all_dict(session, sql: str, params=None):
    rows = session.execute(text(sql), params or {}).mappings().all()
    return [dict(r) for r in rows]
