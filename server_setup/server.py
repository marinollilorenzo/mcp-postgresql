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
#  FastMCP Lifespan: connetti/disconnetti il DB
# ──────────────────────────────────────────────

@asynccontextmanager
async def lifespan(server: FastMCP):
    """Gestisce il ciclo di vita del pool di connessioni al DB."""
    logger.info("Avvio server MCP PostgreSQL...")
    await db_manager.connect()
    logger.info("Pool DB connesso. Server MCP pronto all'uso.")
    try:
        yield
    finally:
        await db_manager.disconnect()
        logger.info("Pool DB chiuso.")


# ──────────────────────────────────────────────
#  FastMCP server initialization
# ──────────────────────────────────────────────

mcp = FastMCP(
    name="mcp-postgres",
    instructions=(
        "Server MCP per interrogare un database PostgreSQL aziendale. "
        "Leggi la resource 'db://schema' per vedere la struttura delle tabelle. "
        "Usa lo strumento 'validate_query' per verificare una query SQL, "
        "e poi 'execute_query' per eseguirla. Solo query SELECT sono permesse."
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
#  TOOLS (Azioni attive)
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

    Chiama questo tool PRIMA di execute_query per evitare errori a runtime.

    Args:
        sql: La query SQL da validare.
    """
    sql = sql.strip()
    if not sql:
        return _json({"success": False, "error": "Il parametro 'sql' è obbligatorio."})

    schema = await db_manager.get_full_schema()

    validator = SQLValidator(max_joins=srv_cfg.max_joins)
    result = validator.validate(sql=sql, schema=schema)

    await ctx.info(
        f"Validazione: {'OK' if result.is_valid else 'FALLITA'} "
        f"(livelli superati: {result.validation_levels_passed})"
    )

    return _json(result.to_dict())


@mcp.tool()
async def execute_query(sql: str, ctx: Context, skip_validation: bool = False) -> str:
    """
    Esegue una query SELECT sul database e restituisce i risultati in JSON.
    La query viene eseguita in una transazione readonly sicura.
    È buona norma usare 'validate_query' prima di chiamare questo tool.

    Args:
        sql: La query SELECT da eseguire.
        skip_validation: Se True salta la validazione interna (non raccomandato, default: False).
    """
    sql = sql.strip()
    if not sql:
        return _json({"success": False, "error": "Il parametro 'sql' è obbligatorio."})

    # Validazione interna di sicurezza (sempre raccomandata prima di colpire il DB)
    if not skip_validation:
        schema = await db_manager.get_full_schema()
        
        # FIX: Inizializzazione stateless del Validator
        validator = SQLValidator(max_joins=srv_cfg.max_joins)
        validation = validator.validate(sql=sql, schema=schema)

        if not validation.is_valid:
            await ctx.warning(f"Esecuzione bloccata dalla validazione: {validation.errors}")
            return _json({
                "success": False,
                "error": "Query non valida o pericolosa. Dettagli nel campo 'validation'.",
                "validation": validation.to_dict(),
            })

    await ctx.info("Esecuzione query sul database in corso...")
    result = await db_manager.execute_query(sql)

    if result.get("success"):
        await ctx.info(f"Query completata con successo: {result.get('row_count', 0)} righe estratte.")
    else:
        await ctx.error(f"Errore durante l'esecuzione della query: {result.get('error')}")

    return _json(result)

# ══════════════════════════════════════════════
#  RESOURCES (Dati Passivi)
# ══════════════════════════════════════════════

@mcp.resource(
    "db://schema",
    name="Database Schema",
    description="Schema completo del database PostgreSQL aziendale: tabelle, viste, colonne, PK e FK.",
    mime_type="application/json",
)
async def resource_full_schema() -> str:
    """Esponi lo schema completo del database come risorsa MCP passiva."""
    schema = await db_manager.get_full_schema()
    schema_dict = {name: info.to_dict() for name, info in schema.items()}
    return _json({
        "database": db_cfg.name,
        "pg_schema": db_cfg.schema_name,  # FIX: Rinominato per allineamento Pydantic V2
        "tables": schema_dict,
    })


@mcp.resource(
    "db://table/{table_name}",
    name="Table Schema",
    description="Schema dettagliato di una singola tabella: colonne, tipi di dato, chiavi primarie (PK) e relazioni (FK).",
    mime_type="application/json",
)
async def resource_table_schema(table_name: str) -> str:
    """Esponi lo schema di una tabella specifica come risorsa MCP."""
    table_info = await db_manager.get_table_schema(table_name)
    if table_info is None:
        schema = await db_manager.get_full_schema()
        return _json({
            "error": f"La tabella '{table_name}' non è stata trovata nello schema configurato.",
            "available_tables": sorted(schema.keys()),
        })
    return _json(table_info.to_dict())


@mcp.resource(
    "db://health",
    name="Database Health",
    description="Statistiche di salute del server: latenza DB in millisecondi, stato del pool di connessioni e validità della cache schema.",
    mime_type="application/json",
)
async def resource_health() -> str:
    """Health check del database e del pool di connessioni."""
    health = await db_manager.health_check()
    return _json(health)

# ══════════════════════════════════════════════
#  PROMPTS (Istruzioni aggiuntive per l'LLM)
# ══════════════════════════════════════════════

mcp.prompt()(sql_query_builder)
mcp.prompt()(schema_explorer)


# ══════════════════════════════════════════════
#  ENTRYPOINT & TRANSPORT STARTUP
# ══════════════════════════════════════════════

def main() -> None:
    """Avvia il server sul trasporto scelto in `.env`."""
    transport = srv_cfg.transport.lower()

    logger.info("Inizializzazione server MCP completata. Trasporto selezionato: %s", transport)

    if transport == "sse":
        # Legacy HTTP Server-Sent Events
        mcp.run(transport="sse")
    elif transport == "streamable-http":
        # Nuovo standard MCP 2025 su HTTP
        mcp.run(
            transport="streamable-http",
            uvicorn_kwargs={
                "host": srv_cfg.sse_host,
                "port": srv_cfg.sse_port,
            }
        )
    else:
        # Default: STDIO (Standard Input/Output) - Usato per processi figli / chiamate locali
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
