"""
server.py
---------
MCP Server PostgreSQL riscritto con FastMCP (API moderna).

Usa decoratori @mcp.tool(), @mcp.resource(), @mcp.prompt()
invece del vecchio approccio low-level con Server + list_tools/call_tool.

Transport selezionabile via .env:
  TRANSPORT=stdio            → uso locale / Claude Desktop
  TRANSPORT=sse              → HTTP Server-Sent Events (legacy)
  TRANSPORT=streamable-http  → nuovo standard MCP 2025 (raccomandato per rete)
"""

import asyncio
import json
import logging
import sys
from contextlib import asynccontextmanager
from typing import Any

from mcp.server.fastmcp import FastMCP, Context

from config import load_config
from database import DatabaseManager
from validator import SQLValidator
from prompts import sql_query_builder, schema_explorer

# ──────────────────────────────────────────────
#  Config & logging
# ──────────────────────────────────────────────

db_cfg, srv_cfg = load_config()

logging.basicConfig(
    level=getattr(logging, srv_cfg.log_level.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("mcp-postgres")

# ──────────────────────────────────────────────
#  Database manager (singleton)
# ──────────────────────────────────────────────

db_manager = DatabaseManager(db_cfg, srv_cfg)

# ──────────────────────────────────────────────
#  Lifespan: connetti/disconnetti il DB
# ──────────────────────────────────────────────

@asynccontextmanager
async def lifespan(server: FastMCP):
    """Gestisce il ciclo di vita del pool di connessioni al DB."""
    logger.info("Avvio server MCP PostgreSQL...")
    await db_manager.connect()
    logger.info("Pool DB connesso. Server pronto.")
    try:
        yield
    finally:
        await db_manager.disconnect()
        logger.info("Pool DB chiuso.")

# ──────────────────────────────────────────────
#  FastMCP server
# ──────────────────────────────────────────────

mcp = FastMCP(
    name="mcp-postgres",
    instructions=(
        "Server MCP per interrogare un database PostgreSQL. "
        "Leggi la resource db://schema per vedere le tabelle disponibili. "
        "Usa validate_query per verificare una query, poi execute_query per eseguirla. "
        "Solo query SELECT sono permesse. "
        "Usa refresh_schema_cache solo se la struttura del DB è cambiata."
    ),
    lifespan=lifespan,
    json_response=True,
)

# ──────────────────────────────────────────────
#  Helper JSON serializer
# ──────────────────────────────────────────────

def _json(obj: Any) -> str:
    import decimal, datetime

    def default(o):
        if isinstance(o, (datetime.date, datetime.datetime)):
            return o.isoformat()
        if isinstance(o, decimal.Decimal):
            return float(o)
        raise TypeError(f"Tipo non serializzabile: {type(o)}")

    return json.dumps(obj, ensure_ascii=False, indent=2, default=default)


# ══════════════════════════════════════════════
#  TOOLS
#  Solo azioni — la lettura dello schema avviene
#  tramite le Resource (db://schema, db://table/{name})
# ══════════════════════════════════════════════

@mcp.tool()
async def refresh_schema_cache(ctx: Context) -> str:
    """
    Invalida la cache dello schema e la ricarica dal database.
    Chiamare solo se la struttura del database è cambiata
    (nuove tabelle, colonne aggiunte/rimosse, ecc.).
    In condizioni normali non è necessario — la cache è automatica.
    """
    db_manager.invalidate_cache()
    await ctx.info("Cache invalidata, ricarico schema dal DB...")
    schema = await db_manager.get_full_schema()
    await ctx.info(f"Schema ricaricato: {len(schema)} oggetti trovati.")
    return _json({
        "success": True,
        "message": f"Schema ricaricato: {len(schema)} tabelle/viste.",
        "tables": sorted(schema.keys()),
    })


@mcp.tool()
async def validate_query(sql: str, ctx: Context) -> str:
    """
    Valida una query SQL a 3 livelli prima dell'esecuzione:
    1. Security: blocca tutto tranne SELECT (DDL, DML, funzioni pericolose)
    2. Syntactic: verifica la sintassi PostgreSQL con sqlglot
    3. Semantic: controlla che tabelle e colonne esistano nello schema

    Chiama questo tool PRIMA di execute_query.

    Args:
        sql: La query SQL da validare.
    """
    sql = sql.strip()
    if not sql:
        return _json({"success": False, "error": "sql è obbligatorio."})

    schema = await db_manager.get_full_schema()
    validator = SQLValidator(schema=schema, max_joins=srv_cfg.max_joins)
    result = validator.validate(sql)

    await ctx.info(
        f"Validazione: {'OK' if result.is_valid else 'FALLITA'} "
        f"(livelli: {result.validation_levels_passed})"
    )

    return _json(result.to_dict())


@mcp.tool()
async def execute_query(sql: str, ctx: Context, skip_validation: bool = False) -> str:
    """
    Esegue una query SELECT sul database e restituisce i risultati in JSON.
    La query viene eseguita in una transazione readonly.
    Usa validate_query prima per assicurarti che la query sia corretta.

    Args:
        sql: La query SELECT da eseguire.
        skip_validation: Se True salta la validazione interna (non raccomandato).
    """
    sql = sql.strip()
    if not sql:
        return _json({"success": False, "error": "sql è obbligatorio."})

    # Validazione interna (sempre raccomandata)
    if not skip_validation:
        schema = await db_manager.get_full_schema()
        validator = SQLValidator(schema=schema, max_joins=srv_cfg.max_joins)
        validation = validator.validate(sql)

        if not validation.is_valid:
            await ctx.warning(f"Query bloccata dalla validazione: {validation.errors}")
            return _json({
                "success": False,
                "error": "Query non valida. Usa validate_query per i dettagli.",
                "validation": validation.to_dict(),
            })

    await ctx.info("Esecuzione query in corso...")
    result = await db_manager.execute_query(sql)

    if result.get("success"):
        await ctx.info(f"Query completata: {result['row_count']} righe restituite.")
    else:
        await ctx.error(f"Errore query: {result.get('error')}")

    return _json(result)


# ══════════════════════════════════════════════
#  RESOURCES
# ══════════════════════════════════════════════

@mcp.resource(
    "db://schema",
    name="Database Schema",
    description="Schema completo del database PostgreSQL: tabelle, viste, colonne, PK e FK.",
    mime_type="application/json",
)
async def resource_full_schema() -> str:
    """Schema completo del database come risorsa MCP."""
    schema = await db_manager.get_full_schema()
    schema_dict = {name: info.to_dict() for name, info in schema.items()}
    return _json({
        "database": db_cfg.name,
        "pg_schema": db_cfg.schema,
        "tables": schema_dict,
    })


@mcp.resource(
    "db://table/{table_name}",
    name="Table Schema",
    description="Schema di una singola tabella: colonne, tipi, PK e FK.",
    mime_type="application/json",
)
async def resource_table_schema(table_name: str) -> str:
    """Schema di una tabella specifica come risorsa MCP."""
    table_info = await db_manager.get_table_schema(table_name)
    if table_info is None:
        schema = await db_manager.get_full_schema()
        return _json({
            "error": f"Tabella '{table_name}' non trovata.",
            "available_tables": sorted(schema.keys()),
        })
    return _json(table_info.to_dict())



@mcp.resource(
    "db://health",
    name="Database Health",
    description="Stato del server: latenza DB, pool connessioni, cache schema.",
    mime_type="application/json",
)
async def resource_health() -> str:
    """Health check del database e del pool di connessioni."""
    health = await db_manager.health_check()
    return _json(health)

# ══════════════════════════════════════════════
#  PROMPTS  (registrati dalle funzioni in prompts.py)
# ══════════════════════════════════════════════

mcp.prompt()(sql_query_builder)
mcp.prompt()(schema_explorer)


# ══════════════════════════════════════════════
#  ENTRYPOINT
# ══════════════════════════════════════════════

if __name__ == "__main__":
    transport = srv_cfg.transport  # "stdio" | "sse" | "streamable-http"

    logger.info("Avvio con transport: %s", transport)

    if transport == "sse":
        mcp.run(
            transport="sse",
            host=srv_cfg.sse_host,
            port=srv_cfg.sse_port,
        )
    elif transport == "streamable-http":
        mcp.run(
            transport="streamable-http",
            host=srv_cfg.sse_host,
            port=srv_cfg.sse_port,
        )
    else:
        # stdio — default per uso locale
        mcp.run(transport="stdio")