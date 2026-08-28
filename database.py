# With help from Google Gemini

import logging
import os
from contextlib import contextmanager

import psycopg2
from dotenv import load_dotenv
from psycopg2.extras import DictCursor
from rich.logging import RichHandler

logging.basicConfig(level=logging.INFO, format="%(message)s", handlers=[RichHandler()])
logger = logging.getLogger(__name__)

load_dotenv()

DATABASE_URL = (
    os.getenv("DATABASE_URL_DEV")
    if os.getenv("APP_ENV") == "dev"
    else os.getenv("DATABASE_URL_PROD")
)


class Database:
    def __init__(self, connection_url: str = DATABASE_URL):
        self.connection_url = connection_url

    @contextmanager
    def get_cursor(self):
        """Context manager for acquiring a cursor with automatic commit/rollback."""
        with (
            psycopg2.connect(self.connection_url) as conn,
            conn.cursor(cursor_factory=DictCursor) as cursor,
        ):
            yield cursor

    def fetch_all(self, query: str, params: tuple = ()) -> list[dict]:
        """Executes a SELECT query and returns all matching rows as dictionaries."""
        with self.get_cursor() as cursor:
            cursor.execute(query, params)
            return cursor.fetchall()

    def fetch_one(self, query: str, params: tuple = ()):
        """Executes a SELECT query and returns a single row."""
        with self.get_cursor() as cursor:
            cursor.execute(query, params)
            return cursor.fetchone()

    def execute(self, query: str, params: tuple = ()):
        """Executes INSERT/UPDATE/DELETE queries and commits."""
        with self.get_cursor() as cursor:
            cursor.execute(query, params)


from functools import wraps


def with_cursor(func):
    """Decorator that injects an active psycopg2 DictCursor and manages transaction commit/rollback."""

    @wraps(func)
    def wrapper(*args, **kwargs):
        with (
            psycopg2.connect(DATABASE_URL) as conn,
            conn.cursor(cursor_factory=DictCursor) as cursor,
        ):
            try:
                return func(cursor, *args, **kwargs)
            except Exception as e:
                logger.error(f"Database error in {func.__name__}: {e}")
                raise

    return wrapper
