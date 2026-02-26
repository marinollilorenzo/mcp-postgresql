"""
database.py
-----------
Gestione del pool di connessioni asyncpg e operazioni sul database.

Responsabilità:
  - Lifecycle del pool (init / teardown)
  - Introspezione dello schema PostgreSQL
  - Esecuzione sicura di query SELECT con timeout e row-limit
"""

import asyncio
import asyncpg
import logging
from typing import Any

from config import DatabaseConfig, ServerConfig

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
#  Schema types (usate anche dal validator)
# ──────────────────────────────────────────────

class ColumnInfo:
    def __init__(self, row: dict):
        self.name: str        = row["column_name"]
        self.type: str        = row["data_type"]
        self.nullable: bool   = row["is_nullable"] == "YES"
        self.default: str     = row.get("column_default") or ""
        self.max_length: int | None = row.get("character_maximum_length")

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "type": self.type,
            "nullable": self.nullable,
            "default": self.default,
            "max_length": self.max_length,
        }


class TableInfo:
    def __init__(self, name: str, table_type: str, columns: list[ColumnInfo], pk: list[str], fks: list[dict]):
        self.name       = name
        self.table_type = table_type   # "table" | "view"
        self.columns    = columns
        self.pk         = pk
        self.fks        = fks

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "type": self.table_type,
            "primary_keys": self.pk,
            "foreign_keys": self.fks,
            "columns": [c.to_dict() for c in self.columns],
        }


# ──────────────────────────────────────────────
#  DatabaseManager
# ──────────────────────────────────────────────

class DatabaseManager:
    """
    Gestisce il pool asyncpg e fornisce metodi di alto livello per:
    - introspezione schema
    - esecuzione query
    """

    def __init__(self, db_cfg: DatabaseConfig, srv_cfg: ServerConfig):
        self._db_cfg  = db_cfg
        self._srv_cfg = srv_cfg
        self._pool: asyncpg.Pool | None = None
        self._schema_cache: dict[str, TableInfo] | None = None

    # ── Lifecycle ─────────────────────────────

    async def connect(self) -> None:
        """Inizializza il pool di connessioni."""
        logger.info("Connessione al database %s@%s:%d/%s",
                    self._db_cfg.user, self._db_cfg.host,
                    self._db_cfg.port, self._db_cfg.name)
        self._pool = await asyncpg.create_pool(
            dsn=self._db_cfg.dsn,
            min_size=self._db_cfg.min_pool,
            max_size=self._db_cfg.max_pool,
            command_timeout=self._db_cfg.max_pool,
        )
        logger.info("Pool creato con successo.")

    async def disconnect(self) -> None:
        """Chiude il pool di connessioni."""
        if self._pool:
            await self._pool.close()
            logger.info("Pool chiuso.")

    def _ensure_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            raise RuntimeError("DatabaseManager non inizializzato. Chiama connect() prima.")
        return self._pool

    # ── Schema Introspection ───────────────────

    async def get_full_schema(self, use_cache: bool = True) -> dict[str, TableInfo]:
        """
        Restituisce lo schema completo del database (tabelle + viste).
        Usa una cache in-memory per evitare query ripetute.
        """
        if use_cache and self._schema_cache is not None:
            return self._schema_cache

        pool = self._ensure_pool()
        schema = self._db_cfg.schema

        async with pool.acquire() as conn:
            # 1. Lista tabelle e viste
            tables_rows = await conn.fetch("""
                SELECT table_name, table_type
                FROM information_schema.tables
                WHERE table_schema = $1
                AND table_type IN ('BASE TABLE', 'VIEW')
                ORDER BY table_name
            """, schema)

            result: dict[str, TableInfo] = {}

            for table_row in tables_rows:
                t_name = table_row["table_name"]
                t_type = "view" if table_row["table_type"] == "VIEW" else "table"

                # 2. Colonne
                col_rows = await conn.fetch("""
                    SELECT column_name, data_type, is_nullable,
                           column_default, character_maximum_length
                    FROM information_schema.columns
                    WHERE table_schema = $1 AND table_name = $2
                    ORDER BY ordinal_position
                """, schema, t_name)
                columns = [ColumnInfo(dict(r)) for r in col_rows]

                # 3. Primary Keys
                pk_rows = await conn.fetch("""
                    SELECT kcu.column_name
                    FROM information_schema.table_constraints tc
                    JOIN information_schema.key_column_usage kcu
                      ON tc.constraint_name = kcu.constraint_name
                     AND tc.table_schema    = kcu.table_schema
                    WHERE tc.constraint_type = 'PRIMARY KEY'
                      AND tc.table_schema    = $1
                      AND tc.table_name      = $2
                    ORDER BY kcu.ordinal_position
                """, schema, t_name)
                pk = [r["column_name"] for r in pk_rows]

                # 4. Foreign Keys
                fk_rows = await conn.fetch("""
                    SELECT
                        kcu.column_name,
                        ccu.table_name  AS foreign_table,
                        ccu.column_name AS foreign_column
                    FROM information_schema.table_constraints tc
                    JOIN information_schema.key_column_usage kcu
                      ON tc.constraint_name = kcu.constraint_name
                     AND tc.table_schema    = kcu.table_schema
                    JOIN information_schema.constraint_column_usage ccu
                      ON ccu.constraint_name = tc.constraint_name
                     AND ccu.table_schema    = tc.table_schema
                    WHERE tc.constraint_type = 'FOREIGN KEY'
                      AND tc.table_schema    = $1
                      AND tc.table_name      = $2
                """, schema, t_name)
                fks = [dict(r) for r in fk_rows]

                result[t_name] = TableInfo(t_name, t_type, columns, pk, fks)

        self._schema_cache = result
        logger.info("Schema caricato: %d oggetti trovati.", len(result))
        return result

    async def get_table_schema(self, table_name: str) -> TableInfo | None:
        """Restituisce lo schema di una singola tabella/vista."""
        schema = await self.get_full_schema()
        return schema.get(table_name)

    def invalidate_cache(self) -> None:
        """Invalida la cache dello schema (utile se il DB cambia)."""
        self._schema_cache = None
        logger.info("Cache schema invalidata.")

    # ── Query Execution ────────────────────────

    async def execute_query(self, sql: str) -> dict[str, Any]:
        """
        Esegue una query SELECT e restituisce righe + metadata.
        Il row limit e il timeout sono applicati automaticamente.
        """
        pool = self._ensure_pool()
        row_limit = self._srv_cfg.query_row_limit
        timeout   = self._srv_cfg.query_timeout

        # Applica LIMIT se non presente nella query
        sql_with_limit = _apply_row_limit(sql, row_limit)

        try:
            async with pool.acquire() as conn:
                # Connessione in sola lettura a livello di transazione
                async with conn.transaction(readonly=True):
                    rows = await asyncio.wait_for(
                        conn.fetch(sql_with_limit),
                        timeout=timeout,
                    )

            columns = list(rows[0].keys()) if rows else []
            data    = [dict(r) for r in rows]

            return {
                "success": True,
                "columns": columns,
                "rows": data,
                "row_count": len(data),
                "truncated": len(data) >= row_limit,
            }

        except asyncio.TimeoutError:
            return {
                "success": False,
                "error": f"Query timeout dopo {timeout} secondi.",
                "error_type": "timeout",
            }
        except asyncpg.PostgresError as e:
            return {
                "success": False,
                "error": str(e),
                "error_type": "postgres_error",
                "pg_code": e.sqlstate if hasattr(e, "sqlstate") else None,
            }
        except Exception as e:
            logger.exception("Errore inatteso nell'esecuzione della query")
            return {
                "success": False,
                "error": str(e),
                "error_type": "unexpected_error",
            }


# ──────────────────────────────────────────────
#  Helper privati
# ──────────────────────────────────────────────

def _apply_row_limit(sql: str, limit: int) -> str:
    """
    Aggiunge LIMIT alla query se non già presente,
    per evitare che il DB restituisca milioni di righe.
    """
    normalized = sql.strip().rstrip(";").upper()
    if "LIMIT" not in normalized:
        return f"{sql.strip().rstrip(';')} LIMIT {limit}"
    return sql