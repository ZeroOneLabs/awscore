# ***REMOVED***/db/postgres/client.py
from __future__ import annotations
from typing import Optional, Dict, Any, List
from psycopg2.extensions import connection as PgConn
from psycopg2 import sql
from ***REMOVED*** import AutoLogger
from .connection import PostgresConnectionPool

class Postgres(AutoLogger):
    """
    Singleton PostgreSQL client with pooling and query helpers.
    """
    _instance: Optional["Postgres"] = None
    _initialized: bool = False

    def __init__(self, pool: Optional[PostgresConnectionPool] = None, **pool_kwargs):
        """
        Initialize (internal). Use `from_config()`.

        Args:
            pool: Optional pre-made pool.
            **pool_kwargs: Passed to PostgresConnectionPool.
        """
        if Postgres._initialized:
            return
        super().__init__(**pool_kwargs.get("extra_context", {}))
        self.pool = pool or PostgresConnectionPool(**pool_kwargs)
        Postgres._instance = self
        Postgres._initialized = True

    @classmethod
    def from_config(cls, config: Dict[str, Any], **override_kwargs) -> "Postgres":
        """
        Create from config dict.

        Args:
            config: DB config.
            **override_kwargs: Override values.

        Returns:
            Postgres instance.
        """
        db_config = {**config, "extra_context": override_kwargs.pop("extra_context", {}), **override_kwargs}
        return cls(**db_config)

    @classmethod
    def get_instance(cls) -> "Postgres":
        """
        Get singleton instance.

        Returns:
            Global Postgres instance.

        Raises:
            RuntimeError: If not initialized.
        """
        if cls._instance is None:
            raise RuntimeError("Call from_config() first")
        return cls._instance

    def execute(self, query: str | sql.SQL, params: Optional[Dict] = None, fetch: bool = False) -> Optional[List[Dict]]:
        """
        Execute query.

        Args:
            query: SQL string or composable.
            params: Parameters.
            fetch: Return rows.

        Returns:
            List of dicts or None.
        """
        with self.pool.get_connection() as conn:
            with conn.cursor() as cur:
                self.log.debug("Execute", extra={"query": str(query)[:200]})
                cur.execute(query, params or {})
                if fetch:
                    cols = [d[0] for d in cur.description or []]
                    return [dict(zip(cols, row)) for row in cur.fetchall()]
        return None

    def fetch_one(self, query: str | sql.SQL, params: Optional[Dict] = None) -> Optional[Dict]:
        """
        Fetch first row.

        Returns:
            Dict or None.
        """
        result = self.execute(query, params, fetch=True)
        return result[0] if result else None

    def fetch_all(self, query: str | sql.SQL, params: Optional[Dict] = None) -> List[Dict]:
        """
        Fetch all rows.

        Returns:
            List of dicts.
        """
        return self.execute(query, params, fetch=True) or []

    def health_check(self) -> bool:
        """
        Run SELECT 1.

        Returns:
            True if healthy.
        """
        try:
            self.execute("SELECT 1")
            return True
        except Exception as e:
            self.log.error("Health check failed", extra={"error": str(e)})
            return False

    def close(self) -> None:
        """Close pool."""
        self.pool.close()
        Postgres._instance = None
        Postgres._initialized = False