# awscore/db/postgres/connection.py
import logging
from contextlib import contextmanager
from typing import Optional, Generator
import boto3
import psycopg2
from psycopg2 import pool
from psycopg2.extensions import connection as PgConn
from awscore import AutoLogger

log = logging.getLogger(__name__)

class PostgresConnectionPool(AutoLogger):
    """
    Thread-safe connection pool with IAM support.
    """

    def __init__(
        self,
        host: str,
        port: int = 5432,
        database: str = "postgres",
        user: str = "postgres",
        password: Optional[str] = None,
        region: Optional[str] = None,
        iam_enabled: bool = False,
        min_conn: int = 1,
        max_conn: int = 10,
        ssl_mode: str = "require",
        **extra_context,
    ):
        """
        Initialize connection pool.

        Args:
            host: Database host.
            port: Database port.
            database: Database name.
            user: Database user.
            password: Password (if not IAM).
            region: AWS region (required for IAM).
            iam_enabled: Use RDS IAM auth.
            min_conn: Minimum connections.
            max_conn: Maximum connections.
            ssl_mode: SSL mode.
            **extra_context: Log context.
        """
        super().__init__(host=host, database=database, **extra_context)
        self.host = host
        self.port = port
        self.database = database
        self.user = user
        self.password = password
        self.region = region
        self.iam_enabled = iam_enabled
        self.ssl_mode = ssl_mode

        self._pool: Optional[pool.ThreadedConnectionPool] = None
        self._create_pool(min_conn, max_conn)

    def _create_pool(self, min_conn: int, max_conn: int) -> None:
        dsn = self._build_dsn()
        self.log.info("Creating pool", extra={"min": min_conn, "max": max_conn})
        self._pool = pool.ThreadedConnectionPool(min_conn, max_conn, dsn)

    def _build_dsn(self) -> str:
        parts = [
            f"host={self.host}", f"port={self.port}", f"dbname={self.database}",
            f"user={self.user}", f"sslmode={self.ssl_mode}"
        ]
        if self.iam_enabled:
            parts.append(f"password={self._generate_iam_token()}")
        elif self.password:
            parts.append(f"password={self.password}")
        return " ".join(parts)

    def _generate_iam_token(self) -> str:
        if not self.region:
            raise ValueError("region required for IAM")
        client = boto3.client("rds", region_name=self.region)
        return client.generate_db_auth_token(
            DBHostname=self.host, Port=self.port, DBUser=self.user, Region=self.region
        )

    @contextmanager
    def get_connection(self) -> Generator[PgConn, None, None]:
        """
        Get a connection from the pool.

        Yields:
            psycopg2 connection.
        """
        if not self._pool:
            raise RuntimeError("Pool not initialized")
        conn = None
        try:
            conn = self._pool.getconn()
            yield conn
            conn.commit()
        except Exception as e:
            if conn:
                conn.rollback()
            self.log.exception("DB error", extra={"error": str(e)})
            raise
        finally:
            if conn:
                self._pool.putconn(conn)

    def close(self) -> None:
        """Close all connections."""
        if self._pool:
            self._pool.closeall()
            self.log.info("Pool closed")