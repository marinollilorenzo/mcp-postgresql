# MCP PostgreSQL Server

Server MCP per PostgreSQL che espone schema, validazione e query a un agente AI client (LangChain + MCP).

## Struttura del progetto

```
mcp-postgres/
├── src/
│   ├── server.py      # Entry point, tool/resource/prompt handlers
│   ├── database.py    # Pool asyncpg, introspezione schema, esecuzione query
│   ├── validator.py   # Validatore SQL (security + sintassi + semantica)
│   ├── prompts.py     # Prompt MCP con esempi SQL
│   └── config.py      # Configurazione da .env
├── requirements.txt
├── .env.example
└── README.md
```

## Installazione

```bash
pip install -r requirements.txt
cp .env.example .env
# Modifica .env con i tuoi dati
```

## Configurazione `.env`

| Variabile | Default | Descrizione |
|---|---|---|
| `DB_HOST` | localhost | Host PostgreSQL |
| `DB_PORT` | 5432 | Porta PostgreSQL |
| `DB_NAME` | — | **Obbligatorio** |
| `DB_USER` | — | **Obbligatorio** |
| `DB_PASSWORD` | — | **Obbligatorio** |
| `DB_SCHEMA` | public | Schema PostgreSQL da esporre |
| `TRANSPORT` | stdio | `stdio` oppure `sse` |
| `SSE_HOST` | 0.0.0.0 | Host SSE (solo se TRANSPORT=sse) |
| `SSE_PORT` | 8000 | Porta SSE (solo se TRANSPORT=sse) |
| `QUERY_ROW_LIMIT` | 500 | Max righe restituite da execute_query |
| `QUERY_TIMEOUT` | 30 | Timeout query in secondi |
| `LOG_LEVEL` | INFO | DEBUG / INFO / WARNING / ERROR |

## Avvio

```bash
# Modalità stdio (locale, Claude Desktop, LangChain subprocess)
TRANSPORT=stdio python src/server.py

# Modalità SSE (remoto, agenti su rete)
TRANSPORT=sse python src/server.py
```

---

## Tool disponibili

### `get_schema`
Restituisce lo schema completo: tabelle, colonne, PK, FK.

```json
{ "refresh": false }
```

Risposta:
```json
{
  "success": true,
  "database": "mydb",
  "pg_schema": "public",
  "table_count": 5,
  "tables": {
    "customers": {
      "name": "customers",
      "type": "table",
      "primary_keys": ["id"],
      "foreign_keys": [],
      "columns": [
        { "name": "id", "type": "integer", "nullable": false },
        { "name": "full_name", "type": "character varying", "nullable": false },
        { "name": "email", "type": "character varying", "nullable": true }
      ]
    }
  }
}
```

### `get_table_schema`
Schema di una singola tabella.

```json
{ "table_name": "orders" }
```

### `validate_query`
Valida SQL a 3 livelli. Chiamare prima di `execute_query`.

```json
{ "sql": "SELECT id, name FROM customers WHERE active = true" }
```

Risposta:
```json
{
  "is_valid": true,
  "errors": [],
  "warnings": [],
  "normalized_sql": "SELECT id, name FROM customers WHERE active = TRUE",
  "validation_levels_passed": ["security", "syntactic", "semantic"]
}
```

### `execute_query`
Esegue la SELECT (readonly, con row limit automatico).

```json
{ "sql": "SELECT id, full_name FROM customers LIMIT 10" }
```

Risposta:
```json
{
  "success": true,
  "columns": ["id", "full_name"],
  "rows": [
    { "id": 1, "full_name": "Mario Rossi" }
  ],
  "row_count": 1,
  "truncated": false
}
```

---

## Prompt disponibili

### `sql_query_builder`
Genera il prompt con esempi SQL (JOIN, aggregazioni, CTE, subquery, window functions)
a partire dallo schema e dalla domanda dell'utente.

Argomenti: `schema_json`, `user_question`

### `schema_explorer`
Analizza lo schema e suggerisce relazioni e JOIN utili.

Argomenti: `schema_json`

---

## Architettura del flusso Agent → MCP

```
Utente: "Qual è il cliente che ha speso di più?"
        │
        ▼
Agent (LangChain)
  1. get_schema()                    ← vede le tabelle
  2. get_prompt("sql_query_builder") ← ottiene gli esempi
  3. LLM genera SQL (output strutturato)
  4. validate_query(sql)             ← verifica la query
  5. execute_query(sql)              ← esegue sul DB
  6. LLM formula risposta per l'utente
```

## Sicurezza

- Solo statement `SELECT` sono permessi (blocco a 3 livelli)
- Ogni query viene eseguita in una transazione `readonly`
- Row limit automatico (default 500 righe)
- Query timeout configurabile
- Funzioni PostgreSQL pericolose bloccate (`pg_read_file`, `lo_export`, ecc.)
- Stacked queries (`;` multipli) bloccate