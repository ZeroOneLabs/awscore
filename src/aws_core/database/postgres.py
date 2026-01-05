"""
PostgreSQL database manager module.

Provides singleton connection pool manager for PostgreSQL databases
with automatic connection management, transaction support, and
parameterized query execution.
"""

from typing import Any
from contextlib import contextmanager

try:
    import psycopg2
    from psycopg2 import pool, sql, extras
    from psycopg2.extensions import connection as PGConnection, cursor as PGCursor
except ImportError:
    raise ImportError(
        "psycopg2 is required for PostgreSQL operations. Install with: pip install psycopg2-binary"
    )

from ..core.config import Config
from ..core.logging import Logger
from ..aws.secrets import SecretsManager


class PostgresError(Exception):
    """Raised when database operations fail."""

    pass


class PostgresManager:
    """
    PostgreSQL connection pool manager (Singleton).

    Manages a connection pool for PostgreSQL databases with automatic
    connection lifecycle management, transaction support, and parameterized queries.

    Features:
    - Singleton pattern ensures one pool per Lambda container
    - Connection pooling for efficient connection reuse
    - Context managers for automatic transaction management
    - Parameterized queries to prevent SQL injection
    - Automatic rollback on exceptions
    - Integration with AWS Secrets Manager for credentials

    Example:
        >>> pg = PostgresManager()
        >>>
        >>> # Execute query
        >>> results = pg.exec("SELECT * FROM users WHERE status = %s", ('active',))
        >>>
        >>> # Use context manager for transactions
        >>> with pg.transaction() as cursor:
        >>>     cursor.execute("INSERT INTO logs (message) VALUES (%s)", ('test',))
        >>>     cursor.execute("UPDATE counter SET value = value + 1")
        >>> # Auto-commits on success, auto-rolls back on exception
    """

    _instance: "PostgresManager" | None = None
    _lock = None

    def __new__(cls, pool_size: int = 5, max_overflow: int = 10):
        """Create or return existing singleton instance."""
        if cls._lock is None:
            import threading

            cls._lock = threading.Lock()

        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    instance = super().__new__(cls)
                    instance._initialized = False
                    instance._pool_size = pool_size
                    instance._max_overflow = max_overflow
                    cls._instance = instance

        return cls._instance

    def __init__(self, pool_size: int = 5, max_overflow: int = 10):
        """
        Initialize PostgreSQL connection pool (only runs once due to singleton).

        Args:
            pool_size: Minimum number of connections to maintain (default: 5)
            max_overflow: Maximum additional connections allowed (default: 10)
        """
        if self._initialized:
            return

        self.config = Config()
        self.logger = Logger()
        self._pool: pool.ThreadedConnectionPool | None = None

        # Initialize connection pool
        self._initialize_pool()

        self._initialized = True

    def _initialize_pool(self) -> None:
        """Initialize the connection pool with credentials from config or Secrets Manager."""
        self.logger.info("Initializing PostgreSQL connection pool")

        try:
            # Get database configuration
            db_config = self._get_database_config()

            # Create connection pool
            self._pool = pool.ThreadedConnectionPool(
                minconn=self._pool_size,
                maxconn=self._pool_size + self._max_overflow,
                host=db_config["host"],
                port=db_config["port"],
                database=db_config["database"],
                user=db_config["user"],
                password=db_config["password"],
                # Additional performance settings
                connect_timeout=10,
                options="-c statement_timeout=30000",  # 30 second query timeout
            )

            # Test connection
            conn = self._pool.getconn()
            conn.close()
            self._pool.putconn(conn)

            self.logger.info(
                "PostgreSQL connection pool initialized successfully",
                extra={
                    "host": db_config["host"],
                    "database": db_config["database"],
                    "pool_size": self._pool_size,
                },
            )

        except Exception as e:
            error = f"Failed to initialize PostgreSQL connection pool: {str(e)}"
            self.logger.exception(error)
            raise PostgresError(error) from e

    def _get_database_config(self) -> dict[str, Any]:
        """
        Get database configuration from config files or AWS Secrets Manager.

        Merges global database config with environment-specific overrides.

        Config structure:
        [database]
        port = 5432
        connect_timeout = 60

        [database.dev]
        secret = "PROJECT-DEV-DB"
        host = "dev-db.internal"

        Returns:
            Dict with host, port, database, user, password
        """
        environment = self.config.environment

        # Get base database config
        base_config = self.config.get("database", {})

        # Get environment-specific overrides
        env_config = {}
        if environment:
            env_config = self.config.get(f"database.{environment}", {})

        # Merge configs (environment overrides base)
        merged_config = {**base_config, **env_config}

        # Check if using a secret name
        secret_name = merged_config.get("secret")

        if secret_name:
            # Get credentials from Secrets Manager using the secret name
            try:
                secrets = SecretsManager()
                creds = secrets._get(secret_name)

                # Secrets Manager credentials override config values
                db_config = {
                    "host": creds.get("host")
                    or creds.get("hostname")
                    or merged_config.get("host"),
                    "port": int(creds.get("port", merged_config.get("port", 5432))),
                    "database": creds.get("database")
                    or creds.get("dbname")
                    or merged_config.get("database")
                    or merged_config.get("dbname"),
                    "user": creds.get("username")
                    or creds.get("user")
                    or merged_config.get("username")
                    or merged_config.get("user"),
                    "password": creds.get("password"),
                }

                self.logger.info(
                    "Loaded database credentials from Secrets Manager",
                    extra={"secret": secret_name, "environment": environment},
                )

                return db_config

            except Exception as e:
                error = f"Failed to get credentials from Secrets Manager ({secret_name}): {e}"
                self.logger.exception(error)
                raise PostgresError(error) from e

        # No secret configured - use direct config values
        # This is for local development or non-secret configurations
        try:
            db_config = {
                "host": merged_config.get("host"),
                "port": int(merged_config.get("port", 5432)),
                "database": merged_config.get("database")
                or merged_config.get("dbname"),
                "user": merged_config.get("username") or merged_config.get("user"),
                "password": merged_config.get("password"),
            }

            # Validate required fields
            required_fields = ["host", "database", "user", "password"]
            missing = [field for field in required_fields if not db_config.get(field)]

            if missing:
                raise PostgresError(
                    f"Missing required database configuration fields: {', '.join(missing)}. "
                    f"Configure in [database] or [database.{environment}] section, or set 'secret' field."
                )

            self.logger.info(
                "Loaded database credentials from config",
                extra={"host": db_config["host"], "environment": environment},
            )

            return db_config

        except KeyError as e:
            error = f"Missing required database configuration: {e}"
            self.logger.exception(error)
            raise PostgresError(error) from e

    def _get_connection(self) -> PGConnection:
        """
        Get a connection from the pool.

        Returns:
            PostgreSQL connection object
        """
        if not self._pool:
            raise PostgresError("Connection pool not initialized")

        try:
            return self._pool.getconn()
        except pool.PoolError as e:
            error = f"Failed to get connection from pool: {str(e)}"
            self.logger.exception(error)
            raise PostgresError(error) from e

    def _return_connection(self, conn: PGConnection, close: bool = False) -> None:
        """
        Return a connection to the pool.

        Args:
            conn: Connection to return
            close: Whether to close the connection instead of returning to pool
        """
        if not self._pool:
            return

        try:
            if close:
                self._pool.closeconn(conn)
            else:
                self._pool.putconn(conn)
        except Exception as e:
            self.logger.warning(f"Error returning connection to pool: {e}")

    def exec(
        self,
        query: str,
        params: tuple | None = None,
        view_name: str | None = None,
        fetch_one: bool = False,
    ) -> list[dict[str, Any]] | dict[str, Any] | None:
        """
        Execute a SQL query with optional parameters.

        Automatically handles connection management, parameterization,
        and result fetching. Returns results as list of dicts.

        Args:
            query: SQL query string (use %s for parameters)
            params: Tuple of parameter values for parameterized queries
            view_name: Optional view/table name for safe identifier handling
            fetch_one: Return only first row instead of list (default: False)

        Returns:
            - For SELECT: List of dicts (or single dict if fetch_one=True)
            - For INSERT/UPDATE/DELETE: None

        Raises:
            PostgresError: If query execution fails

        Example:
            >>> pg = PostgresManager()
            >>>
            >>> # Simple SELECT
            >>> users = pg.exec("SELECT * FROM users WHERE status = %s", ('active',))
            >>>
            >>> # Get single row
            >>> user = pg.exec("SELECT * FROM users WHERE id = %s", (123,), fetch_one=True)
            >>>
            >>> # INSERT
            >>> pg.exec("INSERT INTO logs (message, level) VALUES (%s, %s)",
            ...         ('Error occurred', 'ERROR'))
            >>>
            >>> # Safe view name handling
            >>> results = pg.exec("SELECT * FROM {}", view_name='user_stats')
        """
        conn = None

        try:
            conn = self._get_connection()

            with conn.cursor(cursor_factory=extras.RealDictCursor) as cursor:
                # Handle view name if provided (safe identifier)
                if view_name:
                    query_obj = sql.SQL(query).format(sql.Identifier(view_name))
                    cursor.execute(query_obj, params)
                else:
                    cursor.execute(query, params)

                # Fetch results for SELECT queries
                if cursor.description:
                    if fetch_one:
                        result = cursor.fetchone()
                        return dict(result) if result else None
                    else:
                        return [dict(row) for row in cursor.fetchall()]

                # Commit for INSERT/UPDATE/DELETE
                conn.commit()
                return None

        except psycopg2.Error as e:
            if conn:
                conn.rollback()
            error = f"Database query failed: {str(e)}"
            self.logger.exception(error, extra={"query": query, "params": params})
            raise PostgresError(error) from e

        except Exception as e:
            if conn:
                conn.rollback()
            error = f"Unexpected error executing query: {str(e)}"
            self.logger.exception(error)
            raise PostgresError(error) from e

        finally:
            if conn:
                self._return_connection(conn)

    @contextmanager
    def transaction(self):
        """
        Context manager for database transactions.

        Automatically commits on success and rolls back on exceptions.
        Yields a cursor for executing multiple queries in a transaction.

        Yields:
            psycopg2 cursor (RealDictCursor)

        Example:
            >>> pg = PostgresManager()
            >>>
            >>> with pg.transaction() as cursor:
            >>>     cursor.execute("INSERT INTO users (name) VALUES (%s)", ('Alice',))
            >>>     cursor.execute("UPDATE counter SET value = value + 1")
            >>>     # Auto-commits here if no exception
            >>>
            >>> # If exception occurs, automatically rolls back
        """
        conn = None

        try:
            conn = self._get_connection()
            cursor = conn.cursor(cursor_factory=extras.RealDictCursor)

            self.logger.debug("Starting database transaction")

            yield cursor

            conn.commit()
            self.logger.debug("Database transaction committed")

        except psycopg2.Error as e:
            if conn:
                conn.rollback()
                self.logger.warning("Database transaction rolled back due to error")
            error = f"Transaction failed: {str(e)}"
            self.logger.exception(error)
            raise PostgresError(error) from e

        except Exception as e:
            if conn:
                conn.rollback()
                self.logger.warning("Database transaction rolled back due to error")
            raise

        finally:
            if cursor:
                cursor.close()
            if conn:
                self._return_connection(conn)

    @contextmanager
    def connection(self):
        """
        Context manager for getting a raw connection.

        Use when you need more control over the connection lifecycle.
        Connection is automatically returned to pool after use.

        Yields:
            psycopg2 connection

        Example:
            >>> pg = PostgresManager()
            >>>
            >>> with pg.connection() as conn:
            >>>     cursor = conn.cursor()
            >>>     cursor.execute("SELECT 1")
            >>>     result = cursor.fetchone()
            >>>     conn.commit()
        """
        conn = None

        try:
            conn = self._get_connection()
            yield conn
        finally:
            if conn:
                self._return_connection(conn)

    def execute_many(self, query: str, params_list: list[tuple]) -> int:
        """
        Execute the same query multiple times with different parameters.

        More efficient than calling exec() in a loop. Uses executemany()
        for batch operations.

        Args:
            query: SQL query string
            params_list: List of parameter tuples

        Returns:
            Number of rows affected

        Example:
            >>> pg = PostgresManager()
            >>>
            >>> # Batch insert
            >>> users = [
            >>>     ('Alice', 'alice@example.com'),
            >>>     ('Bob', 'bob@example.com'),
            >>>     ('Charlie', 'charlie@example.com')
            >>> ]
            >>>
            >>> count = pg.execute_many(
            >>>     "INSERT INTO users (name, email) VALUES (%s, %s)",
            >>>     users
            >>> )
            >>> print(f"Inserted {count} users")
        """
        conn = None

        try:
            conn = self._get_connection()

            with conn.cursor() as cursor:
                cursor.executemany(query, params_list)
                rowcount = cursor.rowcount
                conn.commit()

                self.logger.info(
                    f"Batch executed {len(params_list)} statements",
                    extra={"rows_affected": rowcount},
                )

                return rowcount

        except psycopg2.Error as e:
            if conn:
                conn.rollback()
            error = f"Batch execution failed: {str(e)}"
            self.logger.exception(error)
            raise PostgresError(error) from e

        finally:
            if conn:
                self._return_connection(conn)

    def test_connection(self) -> bool:
        """
        Test database connectivity.

        Returns:
            True if connection successful, False otherwise

        Example:
            >>> pg = PostgresManager()
            >>> if pg.test_connection():
            >>>     print("Database is reachable")
        """
        try:
            result = self.exec("SELECT 1 as test", fetch_one=True)
            return result is not None and result.get("test") == 1
        except Exception as e:
            self.logger.error(f"Connection test failed: {e}")
            return False

    def close_all_connections(self) -> None:
        """
        Close all connections in the pool.

        Use when shutting down or when you need to force reconnection.
        """
        if self._pool:
            self._pool.closeall()
            self.logger.info("Closed all database connections")

    @classmethod
    def reset(cls) -> None:
        """
        Reset singleton instance. Primarily for testing.

        Closes all connections before resetting.
        """
        with cls._lock:
            if cls._instance and cls._instance._pool:
                cls._instance.close_all_connections()
            cls._instance = None

    def __repr__(self) -> str:
        host = self.config.get("database.host", "unknown")
        database = self.config.get("database.database", "unknown")
        return f"PostgresManager(host={host}, database={database}, pool_size={self._pool_size})"
