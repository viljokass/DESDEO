"""Database configuration file for the API."""

from sqlmodel import Session, create_engine

from desdeo.api.config import DatabaseDebugConfig, SettingsConfig
import os

DB_USER = os.getenv("DB_USER", None)
DB_PASSWORD = os.getenv("DB_PASSWORD", None)
DB_HOST = os.getenv("DB_HOST", None)
DB_PORT = os.getenv("DB_PORT", None)
DB_NAME = os.getenv("DB_NAME", None)

if None in [DB_USER, DB_PASSWORD, DB_HOST, DB_PORT, DB_NAME]:
    # debug and development stuff

    # SQLite setup
    engine = create_engine(DatabaseDebugConfig.db_database, connect_args={"check_same_thread": False})

else:
    # deployment stuff

    # Postgresql setup
    # check from config.toml
    # SQLALCHEMY_DATABASE_URL = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
    # For rahti purposes, read necessary fields from environment.

    engine = create_engine(f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}")

def get_session():
    """Yield the current database session."""
    with Session(engine) as session:
        yield session
