"""
agent.py
--------
Agente LangChain + Groq che usa il MCP Server PostgreSQL.

Architettura corretta per Python 3.14 + anyio:
  - Il lifecycle della sessione MCP vive SOLO in main() come `async with`
  - L'agent riceve tools e session già pronti — non gestisce lifecycle
  - L'output strutturato usa JSON parsing manuale invece di with_structured_output
    (più robusto con modelli Groq piccoli come llama-3.1-8b-instant)
"""

import asyncio
import json
import os
import re
import sys
from typing import Literal

from dotenv import load_dotenv
from pydantic import BaseModel, Field, ValidationError
from rich import print as rprint
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
from rich import box

load_dotenv()

# Aggiunge server_setup/ al path per importare prompts.py
_src_path = os.path.join(os.path.dirname(__file__), "..", "server_setup")
sys.path.insert(0, os.path.abspath(_src_path))


# ──────────────────────────────────────────────
#  Output strutturato LLM → SQL
# ──────────────────────────────────────────────

class SQLQueryOutput(BaseModel):
    reasoning: str = Field(description="Ragionamento step-by-step")
    sql_query: str = Field(description="Query SQL SELECT, senza punto e virgola")
    tables_used: list[str] = Field(description="Tabelle usate")
    expected_columns: list[str] = Field(description="Colonne restituite")
    confidence: Literal["high", "medium", "low"] = Field(description="Confidenza")


# ──────────────────────────────────────────────
#  LLM
# ──────────────────────────────────────────────

def build_llm():
    from langchain_groq import ChatGroq
    model       = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
    temperature = float(os.getenv("GROQ_TEMPERATURE", "0.0"))
    rprint(f"[dim]🤖 LLM: {model} (Groq)[/dim]")
    return ChatGroq(model=model, temperature=temperature)


# ──────────────────────────────────────────────
#  Configurazione MCP Server
# ──────────────────────────────────────────────

def get_mcp_config() -> tuple[dict, str]:
    """
    Restituisce (config_dict, server_name).
    Config compatibile con MultiServerMCPClient.
    """
    transport = os.getenv("MCP_TRANSPORT", "stdio").lower()
    server_name = "postgres"

    if transport in ("sse", "streamable-http", "streamable_http"):
        url = os.getenv("MCP_SSE_URL", "http://localhost:8000/sse")
        t   = "streamable_http" if "streamable" in transport else "sse"
        return {server_name: {"transport": t, "url": url}}, server_name
    else:
        server_path = os.getenv(
            "MCP_SERVER_PATH",
            os.path.join(os.path.dirname(__file__), "..", "server_setup", "server.py"),
        )
        return {
            server_name: {
                "transport": "stdio",
                "command": sys.executable,
                "args": [os.path.abspath(server_path)],
                "env": dict(os.environ),
            }
        }, server_name


# ──────────────────────────────────────────────
#  Parser risposta MCP
# ──────────────────────────────────────────────

def parse_mcp_response(raw) -> dict:
    """Normalizza qualsiasi formato di risposta MCP a dict."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {"success": False, "error": raw}
    if isinstance(raw, list):
        text = "".join(
            (item.get("text", "") if isinstance(item, dict) else
             item.text if hasattr(item, "text") else str(item))
            for item in raw
        )
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"success": False, "error": text}
    return {"success": False, "error": f"Tipo risposta non riconosciuto: {type(raw)}"}




# ──────────────────────────────────────────────
#  Schema compatto per ridurre i token al LLM
# ──────────────────────────────────────────────

def compact_schema(full_schema: dict) -> str:
    """
    Converte lo schema JSON completo in una rappresentazione minimale tipo:
      orders(id PK, customer_id→customers, status, total_amount, created_at)

    Riduce i token da ~2000-4000 a ~200-400 — risparmio enorme sulla latenza LLM.
    """
    tables = full_schema.get("tables", {})
    lines = []
    for tname, tinfo in sorted(tables.items()):
        cols = []
        pk_set = set(tinfo.get("primary_keys", []))
        fk_map = {fk["column_name"]: fk["foreign_table"] for fk in tinfo.get("foreign_keys", [])}
        for col in tinfo.get("columns", []):
            cname = col["name"]
            ctype = col["type"].replace("character varying", "varchar").replace("integer","int").replace("numeric","num").replace("boolean","bool").replace("timestamp without time zone","timestamp")
            suffix = ""
            if cname in pk_set:
                suffix = " PK"
            elif cname in fk_map:
                suffix = f"→{fk_map[cname]}"
            nullable = "?" if col.get("nullable") else ""
            cols.append(f"{cname}{nullable}{suffix}")
        ttype = " [view]" if tinfo.get("type") == "view" else ""
        lines.append(f"{tname}{ttype}({', '.join(cols)})")
    return "\n".join(lines)

# ──────────────────────────────────────────────
#  Parser JSON per output strutturato
#  (più robusto di with_structured_output con modelli Groq piccoli)
# ──────────────────────────────────────────────

def _extract_json(text: str) -> dict:
    """Estrae il primo blocco JSON valido da una stringa."""
    # Prova prima blocchi ```json ... ```
    md = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if md:
        try:
            return json.loads(md.group(1))
        except json.JSONDecodeError:
            pass
    # Poi cerca il primo { ... } nel testo
    brace = re.search(r"\{.*\}", text, re.DOTALL)
    if brace:
        try:
            return json.loads(brace.group(0))
        except json.JSONDecodeError:
            pass
    raise ValueError(f"Nessun JSON trovato nella risposta:\n{text}")


async def generate_sql_structured(llm, prompt: str) -> SQLQueryOutput:
    """
    Genera la query SQL con output strutturato.
    Strategia primaria: JSON parsing manuale (funziona con tutti i modelli Groq).
    Strategia fallback: with_structured_output (per modelli che lo supportano).
    """
    json_prompt = prompt + """

IMPORTANTE: rispondi ESCLUSIVAMENTE con un oggetto JSON valido, nessun testo prima o dopo.
Formato esatto richiesto:
{
  "reasoning": "spiegazione step-by-step di come costruisci la query",
  "sql_query": "SELECT ... FROM ... (solo SELECT, senza punto e virgola)",
  "tables_used": ["tabella1", "tabella2"],
  "expected_columns": ["col1", "col2"],
  "confidence": "high"
}

Il campo confidence deve essere esattamente "high", "medium" o "low".
"""

    response = await llm.ainvoke(json_prompt)
    text = response.content.strip()

    try:
        data = _extract_json(text)
        return SQLQueryOutput(**data)
    except (ValueError, ValidationError) as e:
        # Fallback: prova with_structured_output
        rprint(f"[dim yellow]⚠ JSON parsing fallito ({e}), provo structured output...[/dim yellow]")
        try:
            llm_structured = llm.with_structured_output(SQLQueryOutput)
            return await llm_structured.ainvoke(prompt)
        except Exception as e2:
            raise RuntimeError(
                f"Impossibile ottenere output strutturato.\n"
                f"JSON error: {e}\n"
                f"Structured output error: {e2}\n"
                f"Risposta LLM:\n{text}"
            ) from e2


# ──────────────────────────────────────────────
#  MCPPostgresAgent
#  NON gestisce lifecycle — riceve tools e llm già pronti
# ──────────────────────────────────────────────

class MCPPostgresAgent:

    def __init__(self, tools: list, llm, session):
        self._tools_map    = {t.name: t for t in tools}
        self._llm          = llm
        self._session      = session   # usato per leggere le resource MCP
        self._schema_cache = None

    # ── Tool call ────────────────────────────

    async def _call(self, tool_name: str, **kwargs) -> dict:
        tool = self._tools_map.get(tool_name)
        if tool is None:
            available = list(self._tools_map.keys())
            raise ValueError(f"Tool '{tool_name}' non trovato. Disponibili: {available}")
        raw = await tool.ainvoke(kwargs)
        return parse_mcp_response(raw)

    # ── Schema ────────────────────────────────

    async def get_schema(self, refresh: bool = False) -> dict:
        if self._schema_cache is None or refresh:
            rprint("[dim]📋 Lettura schema via resource MCP...[/dim]")
            result = await self._session.read_resource("db://schema")
            # result.contents è una lista — il primo elemento ha il testo JSON
            raw = result.contents[0].text if result.contents else "{}"
            self._schema_cache = parse_mcp_response(raw)
        return self._schema_cache

    # ── Pipeline principale ───────────────────

    async def ask(self, question: str) -> None:
        rprint(Panel(
            f"[bold cyan]{question}[/bold cyan]",
            title="❓ Domanda", border_style="cyan"
        ))

        try:
            # 1. Ottieni schema (compatto per ridurre token LLM)
            schema = await self.get_schema()
            tables_compact = compact_schema(schema)

            # 2. Carica prompt template da prompts.py
            try:
                from server_setup.prompts import sql_query_builder as _prompt_fn
                prompt_text = _prompt_fn(schema_json=tables_compact, user_question=question)
            except ImportError:
                prompt_text = (
                    f"Sei un esperto SQL PostgreSQL.\nSchema:\n{tables_compact}\n\n"
                    f"Domanda: {question}\nGenera una query SELECT appropriata."
                )

            # 3. Genera SQL strutturato
            rprint("[dim]🧠 Generazione query SQL...[/dim]")
            sql_output = await generate_sql_structured(self._llm, prompt_text)

            rprint(Panel(
                f"[dim]{sql_output.reasoning}[/dim]",
                title="💭 Ragionamento", border_style="dim"
            ))
            syntax = Syntax(sql_output.sql_query, "sql", theme="monokai")
            rprint(Panel(
                syntax,
                title=f"🔍 Query SQL  [confidence: {sql_output.confidence}]",
                border_style="yellow"
            ))
            rprint(f"[dim]   Tabelle: {', '.join(sql_output.tables_used)}[/dim]")

            # 4. Valida
            validation = await self._call("validate_query", sql=sql_output.sql_query)
            if not validation.get("is_valid", False):
                errors = validation.get("errors", [])
                rprint(Panel(
                    "\n".join(f"❌ {e}" for e in errors),
                    title="⚠️  Validazione FALLITA", border_style="red"
                ))
                return

            levels = validation.get("validation_levels_passed", [])
            rprint(f"[green]✅ Validazione OK  ({' → '.join(levels)})[/green]")
            for w in validation.get("warnings", []):
                rprint(f"[yellow]   ⚠ {w}[/yellow]")

            # 5. Esegui
            rprint("[dim]⚡ Esecuzione query...[/dim]")
            result = await self._call("execute_query", sql=sql_output.sql_query)

            if not result.get("success", False):
                rprint(Panel(
                    result.get("error", "Errore sconosciuto"),
                    title="❌ Errore esecuzione", border_style="red"
                ))
                return

            _print_table(
                result.get("rows", []),
                result.get("columns", []),
                result.get("row_count", 0),
                result.get("truncated", False),
            )

            # 6. Risposta in linguaggio naturale
            # Passa tutte le righe al LLM fino a NL_PREVIEW_LIMIT (default 50).
            # Con pochi risultati non troncare mai — causa risposte incomplete.
            nl_limit = int(os.getenv("NL_PREVIEW_LIMIT", "50"))
            all_rows = result.get("rows", [])
            rows_preview = all_rows[:nl_limit]
            truncated_nl = len(all_rows) > nl_limit
            answer_prompt = (
                f"Sei un assistente che risponde in italiano.\n\n"
                f"Domanda: {question}\n\n"
                f"Risultati DB ({result.get('row_count', 0)} righe"
                f"{f', mostrate prime {nl_limit}' if truncated_nl else ''}):\n"
                f"{json.dumps(rows_preview, indent=2, ensure_ascii=False, default=str)}\n\n"
                f"Rispondi in modo chiaro e conciso. NON mostrare la query SQL."
            )
            response = await self._llm.ainvoke(answer_prompt)
            rprint(Panel(
                f"[white]{response.content}[/white]",
                title="💬 Risposta", border_style="green"
            ))

        except Exception as e:
            rprint(f"[bold red]❌ Errore: {e}[/bold red]")
            raise


# ──────────────────────────────────────────────
#  Rich table helper
# ──────────────────────────────────────────────

def _print_table(rows: list, columns: list, count: int, truncated: bool) -> None:
    if not rows:
        rprint("[dim]Nessun risultato.[/dim]")
        return
    table = Table(
        title=f"📊 {count} righe{' (prime 20 mostrate)' if truncated else ''}",
        box=box.ROUNDED, border_style="blue",
        header_style="bold blue", show_lines=True,
    )
    for col in columns:
        table.add_column(col, overflow="fold", max_width=35)
    for row in rows[:20]:
        table.add_row(*[str(row.get(c, "")) for c in columns])
    rprint(table)


# ──────────────────────────────────────────────
#  main() — lifecycle MCP vive QUI
# ──────────────────────────────────────────────

async def main():
    rprint(Panel(
        "[bold]MCP PostgreSQL Agent[/bold]\n"
        "[dim]Groq open-source LLM · MCP transport[/dim]\n\n"
        "Comandi: [cyan]schema[/cyan] = tabelle  |  [cyan]exit[/cyan] = esci",
        title="🚀 Avvio", border_style="green"
    ))

    from langchain_mcp_adapters.client import MultiServerMCPClient
    from langchain_mcp_adapters.tools import load_mcp_tools

    config, server_name = get_mcp_config()
    transport = config[server_name]["transport"]
    rprint(f"[dim]🔌 Connessione MCP ({transport.upper()})...[/dim]")

    llm = build_llm()

    # Il lifecycle della sessione MCP vive interamente in questo async with.
    # Questo evita il cancel scope error di anyio su Python 3.12+/3.14:
    # LangChain non può creare task fuori da questo scope perché
    # la sessione è già stabilita prima di qualsiasi chiamata LLM.
    async with MultiServerMCPClient(config).session(server_name) as session:
        tools = await load_mcp_tools(session)
        rprint(f"[green]✅ Connesso. Tool: {[t.name for t in tools]}[/green]")

        agent = MCPPostgresAgent(tools=tools, llm=llm, session=session)

        while True:
            try:
                rprint("\n[bold cyan]Tu:[/bold cyan] ", end="")
                question = input().strip()

                if not question:
                    continue
                if question.lower() in ("exit", "quit", "esci"):
                    rprint("[dim]Arrivederci![/dim]")
                    break
                if question.lower() == "schema":
                    schema = await agent.get_schema()
                    tables = sorted(schema.get("tables", {}).keys())
                    rprint(Panel(
                        "\n".join(f"• {t}" for t in tables),
                        title=f"📋 Tabelle ({len(tables)})", border_style="blue"
                    ))
                    continue

                await agent.ask(question)

            except KeyboardInterrupt:
                rprint("\n[dim]Interrotto.[/dim]")
                break
            except EOFError:
                break


if __name__ == "__main__":
    asyncio.run(main())