"""
Database connection and collection log helpers.
Every API call is logged to raw.collection_log for traceability.
"""

import uuid
import logging
from datetime import datetime
from contextlib import contextmanager

import psycopg2
import psycopg2.extras

from pipeline.config import get_db_params

logger = logging.getLogger(__name__)


def get_connection():
    """Create a new database connection."""
    return psycopg2.connect(**get_db_params())


@contextmanager
def get_cursor(commit=True):
    """Context manager that yields a cursor and handles commit/rollback."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            yield cur
        if commit:
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


class CollectionLog:
    """
    Manages a single collection_log entry lifecycle.

    Usage:
        log = CollectionLog(source="KOSTAT", endpoint="getPriceInfo", params={...})
        log.start(cursor)
        try:
            # ... do work ...
            log.succeed(cursor, records_fetched=1234)
        except Exception as e:
            log.fail(cursor, error=str(e))
    """

    def __init__(self, source: str, endpoint: str, params: dict = None):
        self.id = uuid.uuid4()
        self.source = source
        self.endpoint = endpoint
        self.params = params or {}
        self.started_at = None

    def start(self, cursor):
        """Insert the initial log row with status=RUNNING."""
        self.started_at = datetime.now()
        cursor.execute(
            """
            INSERT INTO raw.collection_log
                (id, source, endpoint, request_params, started_at, status)
            VALUES (%s, %s, %s, %s, %s, 'RUNNING')
            """,
            (
                str(self.id),
                self.source,
                self.endpoint,
                psycopg2.extras.Json(self.params),
                self.started_at,
            ),
        )
        logger.info(
            "Collection started: %s/%s [%s]",
            self.source, self.endpoint, self.id
        )

    def succeed(self, cursor, records_fetched: int, http_status: int = 200):
        """Mark the log entry as SUCCESS."""
        cursor.execute(
            """
            UPDATE raw.collection_log
            SET finished_at = %s, status = 'SUCCESS',
                records_fetched = %s, http_status = %s
            WHERE id = %s
            """,
            (datetime.now(), records_fetched, http_status, str(self.id)),
        )
        logger.info(
            "Collection succeeded: %s/%s — %d records [%s]",
            self.source, self.endpoint, records_fetched, self.id
        )

    def fail(self, cursor, error: str, http_status: int = None):
        """Mark the log entry as FAILED."""
        cursor.execute(
            """
            UPDATE raw.collection_log
            SET finished_at = %s, status = 'FAILED',
                error_message = %s, http_status = %s
            WHERE id = %s
            """,
            (datetime.now(), error, http_status, str(self.id)),
        )
        logger.error(
            "Collection failed: %s/%s — %s [%s]",
            self.source, self.endpoint, error, self.id
        )
