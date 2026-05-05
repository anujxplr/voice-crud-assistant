from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings

engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False},  # required for SQLite
) #An engine acts as an interface that SQLAlchemy uses to communicate with the Database. Handles resource pooling 

SessionLocal = sessionmaker(bind=engine) 
#Session is a container for database operations. IT tracks objects loaded from the databases and manages changed before they are committed to DB
#ORM is the technique to map DB tables to python classes. 

class Base(DeclarativeBase):
    pass

#DeclarativeBase allows SQLAlchemy to map python classes to DB Tables

def init_db() -> None:
    """Create all tables. Import models before calling this."""
    import app.models  # noqa: F401 — ensure models are registered

    Base.metadata.create_all(bind=engine)


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency that yields a DB session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
