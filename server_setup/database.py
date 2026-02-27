"""
database.py
-----------
Gestione del pool di connessioni asyncpg e operazioni sul database.

Responsabilità:
  - Lifecycle del pool (init / teardown)
  - Introspezione dello schema PostgreSQL con filtro tabelle
  - Esecuzione sicura di query SELECT con timeout, row-limit e execution time
  - Cache schema con TTL automatico e schema versioning (hash)
"""

import asyncio
import asyncpg
import hashlib
import json
import logging
import time
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
    def __init__(self, name: str, table_type: str, columns: list[ColumnInfo],
                 pk: list[str], fks: list[dict]):
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
    - introspezione schema (con TTL cache e schema versioning)
    - esecuzione query (con execution time)
    - health check
    """

    def __init__(self, db_cfg: DatabaseConfig, srv_cfg: ServerConfig):
        self._db_cfg  = db_cfg
        self._srv_cfg = srv_cfg
        self._pool: asyncpg.Pool | None = None
        self._schema_cache: dict[str, TableInfo] | None = None
        self._cache_time: float = 0.0          # timestamp ultimo caricamento
        self._schema_hash: str = ""            # hash per schema versioning

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
            command_timeout=self._srv_cfg.query_timeout,  # FIX: era max_pool per errore
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

    # ── Cache helpers ─────────────────────────

    def _is_cache_valid(self) -> bool:
        """Controlla se la cache è ancora valida secondo il TTL configurato."""
        if self._schema_cache is None:
            return False
        ttl = self._srv_cfg.schema_cache_ttl
        if ttl == 0:
            return True   # TTL=0 → cache infinita
        return (time.monotonic() - self._cache_time) < ttl

    def invalidate_cache(self) -> None:
        """Invalida la cache dello schema manualmente."""
        self._schema_cache = None
        self._cache_time = 0.0
        self._schema_hash = ""
        logger.info("Cache schema invalidata.")

    @staticmethod
    def _compute_schema_hash(schema: dict[str, TableInfo]) -> str:
        """Calcola un hash MD5 dello schema per rilevare cambiamenti strutturali."""
        fingerprint = json.dumps(
            {name: info.to_dict() for name, info in sorted(schema.items())},
            sort_keys=True,
        )
        return hashlib.md5(fingerprint.encode()).hexdigest()

    # ── Access control ────────────────────────

    def _filter_tables(self, schema: dict[str, TableInfo]) -> dict[str, TableInfo]:
        """
        Applica whitelist/blacklist alle tabelle secondo la configurazione.
        - allowed_tables valorizzato → mostra SOLO quelle tabelle
        - denied_tables valorizzato  → nasconde quelle tabelle
        - allowed_tables ha precedenza su denied_tables
        """
        allowed = self._srv_cfg.allowed_tables
        denied  = self._srv_cfg.denied_tables

        if allowed:
            return {k: v for k, v in schema.items() if k.lower() in allowed}
        if denied:
            return {k: v for k, v in schema.items() if k.lower() not in denied}
        return schema

    # ── Schema Introspection ───────────────────

    async def get_full_schema(self, use_cache: bool = True) -> dict[str, TableInfo]:
        """
        Restituisce lo schema completo del database (tabelle + viste).
        - Usa cache con TTL automatico (SCHEMA_CACHE_TTL secondi)
        - Rileva cambiamenti di schema tramite hash e logga warning
        - Applica filtro tabelle (whitelist/blacklist)
        """
        if use_cache and self._is_cache_valid():
            return self._schema_cache

        pool = self._ensure_pool()
        db_schema = self._db_cfg.schema

        async with pool.acquire() as conn:
            # 1. Lista tabelle e viste
            tables_rows = await conn.fetch("""
                SELECT table_name, table_type
                FROM information_schema.tables
                WHERE table_schema = $1
                  AND table_type IN ('BASE TABLE', 'VIEW')
                ORDER BY table_name
            """, db_schema)

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
                """, db_schema, t_name)
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
                """, db_schema, t_name)
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
                """, db_schema, t_name)
                fks = [dict(r) for r in fk_rows]

                result[t_name] = TableInfo(t_name, t_type, columns, pk, fks)

        # Schema versioning: rileva cambiamenti strutturali
        new_hash = self._compute_schema_hash(result)
        if self._schema_hash and new_hash != self._schema_hash:
            logger.warning(
                "Schema DB cambiato (hash %s → %s). Cache invalidata automaticamente.",
                self._schema_hash[:8], new_hash[:8],
            )
        self._schema_hash = new_hash

        # Aggiorna cache
        self._schema_cache = result
        self._cache_time = time.monotonic()
        logger.info("Schema caricato: %d oggetti trovati (hash: %s).",
                    len(result), new_hash[:8])

        # Applica filtro e restituisce
        return self._filter_tables(result)

    async def get_table_schema(self, table_name: str) -> TableInfo | None:
        """Restituisce lo schema di una singola tabella/vista."""
        schema = await self.get_full_schema()
        return schema.get(table_name)

    # ── Health check ──────────────────────────

    async def health_check(self) -> dict[str, Any]:
        """
        Verifica lo stato del pool e la latenza verso il database.
        Usato dalla resource db://health.
        """
        pool = self._ensure_pool()
        start = time.monotonic()
        try:
            async with pool.acquire() as conn:
                await conn.fetchval("SELECT 1")
            latency_ms = round((time.monotonic() - start) * 1000, 2)

            ttl = self._srv_cfg.schema_cache_ttl
            cache_age = round(time.monotonic() - self._cache_time, 1) if self._cache_time else None
            cache_expires_in = None
            if ttl > 0 and cache_age is not None:
                cache_expires_in = max(0, ttl - cache_age)

            return {
                "status": "ok",
                "latency_ms": latency_ms,
                "pool": {
                    "min_size": pool.get_min_size(),
                    "max_size": pool.get_max_size(),
                    "size": pool.get_size(),
                    "idle": pool.get_idle_size(),
                },
                "schema_cache": {
                    "loaded": self._schema_cache is not None,
                    "hash": self._schema_hash[:8] if self._schema_hash else None,
                    "age_seconds": cache_age,
                    "expires_in_seconds": cache_expires_in,
                    "ttl_seconds": ttl if ttl > 0 else "infinite",
                },
            }
        except Exception as e:
            return {
                "status": "error",
                "error": str(e),
            }

    # ── Query Execution ────────────────────────

    async def execute_query(self, sql: str) -> dict[str, Any]:
        """
        Esegue una query SELECT e restituisce righe + metadata.
        - Row limit applicato via CTE wrap (più robusto di string append)
        - Execution time incluso nella risposta
        - Transazione readonly a livello DB
        """
        pool = self._ensure_pool()
        row_limit = self._srv_cfg.query_row_limit
        timeout   = self._srv_cfg.query_timeout

        sql_with_limit = _apply_row_limit(sql, row_limit)

        start = time.monotonic()
        try:
            async with pool.acquire() as conn:
                async with conn.transaction(readonly=True):
                    rows = await asyncio.wait_for(
                        conn.fetch(sql_with_limit),
                        timeout=timeout,
                    )

            exec_ms = round((time.monotonic() - start) * 1000, 2)
            columns = list(rows[0].keys()) if rows else []
            data    = [dict(r) for r in rows]

            return {
                "success": True,
                "columns": columns,
                "rows": data,
                "row_count": len(data),
                "truncated": len(data) >= row_limit,
                "execution_ms": exec_ms,
            }

        except asyncio.TimeoutError:
            return {
                "success": False,
                "error": f"Query timeout dopo {timeout} secondi.",
                "error_type": "timeout",
                "execution_ms": round((time.monotonic() - start) * 1000, 2),
            }
        except asyncpg.PostgresError as e:
            return {
                "success": False,
                "error": str(e),
                "error_type": "postgres_error",
                "pg_code": e.sqlstate if hasattr(e, "sqlstate") else None,
                "execution_ms": round((time.monotonic() - start) * 1000, 2),
            }
        except Exception as e:
            logger.exception("Errore inatteso nell'esecuzione della query")
            return {
                "success": False,
                "error": str(e),
                "error_type": "unexpected_error",
                "execution_ms": round((time.monotonic() - start) * 1000, 2),
            }


# ──────────────────────────────────────────────
#  Helper privati
# ──────────────────────────────────────────────

def _apply_row_limit(sql: str, limit: int) -> str:
    """
    Aggiunge LIMIT alla query se non già presente.
    Usa un wrap CTE invece di string append per essere sicuro
    anche con query che hanno LIMIT nelle subquery.
    """
    normalized = sql.strip().rstrip(";")
    if "LIMIT" not in normalized.upper():
        return f"WITH __query AS ({normalized}) SELECT * FROM __query LIMIT {limit}"
    return normalized