# MCP PostgreSQL Server

Server MCP che dà a un agente AI accesso in **sola lettura** a un database
PostgreSQL: gli mostra lo schema, valida la SQL che genera prima di eseguirla,
e la esegue dentro una transazione readonly con un limite di righe.

Il punto non è far scrivere SQL a un modello — è impedirgli di fare danni quando
la scrive male. La validazione lavora su tre livelli: **sicurezza** (nessuno
statement di scrittura, nessuna funzione che tocca il filesystem, niente query
concatenate), **sintassi** (parsing con `sqlglot` sul dialetto PostgreSQL), e
**semantica** (le tabelle e le colonne citate esistono davvero nello schema).

## Struttura del progetto

```
mcp-postgresql/
├── main.py                    # Entry point: avvia il server
├── server_setup/
│   ├── server.py              # Server FastMCP: tool, resource e prompt
│   ├── database.py            # Pool asyncpg, introspezione schema con cache, esecuzione
│   ├── validator.py           # Validatore SQL a 3 livelli (sqlglot)
│   ├── prompts.py             # Prompt MCP con esempi SQL
│   └── config.py              # Configurazione da .env (pydantic-settings)
├── client_setup/
│   └── agent.py               # Client LangChain di esempio, per provare il server
├── db/
│   ├── schema_and_data.sql    # Schema di prova: clienti, ordini, prodotti, dipendenti
│   └── generate_faker_data.py # Riempie lo schema di dati finti
├── .env.example
├── pyproject.toml
└── README.md
```

## Come farlo girare

Serve **Python 3.14** e [`uv`](https://docs.astral.sh/uv/).

```bash
uv sync
cp .env.example .env
```

Poi in `.env` vanno almeno `DB_NAME`, `DB_USER` e `DB_PASSWORD`: senza quei tre
il server si rifiuta di partire, invece di avviarsi e fallire alla prima query.

Per provarlo senza avere un database sottomano, in `db/` c'è uno schema di
esempio — clienti, ordini, prodotti, dipendenti, recensioni — con un generatore
di dati finti:

```bash
psql -d il_tuo_db -f db/schema_and_data.sql
uv run python db/generate_faker_data.py
```

## Avvio

```bash
uv run python main.py
```

Il trasporto si sceglie da `.env`, non dalla riga di comando:

| `TRANSPORT` | Quando usarlo |
|---|---|
| `stdio` | uso locale: Claude Desktop, o un client che lancia il server come sottoprocesso |
| `streamable-http` | server e agente su macchine diverse — è lo standard MCP attuale |
| `sse` | come sopra, ma nella forma legacy |

Con `streamable-http` o `sse` valgono anche `SSE_HOST` e `SSE_PORT`.

Per provare il server da terminale c'è un client di esempio che usa LangChain e
un modello Groq. Vuole `GROQ_API_KEY` in `.env`:

```bash
uv run python client_setup/agent.py
```

## Configurazione `.env`

| Variabile | Default | Descrizione |
|---|---|---|
| `DB_NAME` | — | **Obbligatorio** |
| `DB_USER` | — | **Obbligatorio** |
| `DB_PASSWORD` | — | **Obbligatorio** |
| `DB_HOST` | localhost | Host PostgreSQL |
| `DB_PORT` | 5432 | Porta PostgreSQL |
| `DB_SCHEMA` | public | Schema PostgreSQL da esporre |
| `DB_MIN_POOL` / `DB_MAX_POOL` | 2 / 10 | Dimensioni del pool di connessioni |
| `TRANSPORT` | stdio | `stdio`, `sse` o `streamable-http` |
| `SSE_HOST` | 0.0.0.0 | Host di ascolto (solo per sse / streamable-http) |
| `SSE_PORT` | 8000 | Porta di ascolto (solo per sse / streamable-http) |
| `QUERY_ROW_LIMIT` | 500 | Max righe restituite da `execute_query` |
| `QUERY_TIMEOUT` | 30 | Timeout query in secondi |
| `MAX_JOINS` | 8 | Max JOIN per query, contro le query che stendono il DB |
| `SCHEMA_CACHE_TTL` | 300 | Secondi di validità della cache schema (0 = infinita) |
| `ALLOWED_TABLES` | vuoto | Se valorizzato, l'agente vede **solo** queste tabelle |
| `DENIED_TABLES` | vuoto | Tabelle nascoste; ignorato se `ALLOWED_TABLES` è pieno |
| `LOG_LEVEL` | INFO | DEBUG / INFO / WARNING / ERROR |

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






---
## Domande da fare per testare l'agente
🟢 Livello 1 — Tool di base (schema + query semplici)
Questi testano che la connessione, lo schema e le query basilari funzionino:
- schema
(comando speciale del client — verifica che get_schema risponda)
- Quanti clienti abbiamo nel database?
- Mostrami tutti i prodotti con stock inferiore a 30 pezzi, ordinati dal meno fornito
- Quali ordini sono ancora in stato pending o confirmed?
- Mostrami tutti i dipendenti del reparto Engineering con il loro stipendio

🟡 Livello 2 — JOIN tra tabelle
Questi testano che l'LLM legga correttamente le FK dello schema:
- Mostrami tutti gli ordini con il nome del cliente, lo stato e il totale
- Quali prodotti ha ordinato Mario Rossi? Mostrami nome prodotto, quantità e prezzo pagato
- Mostrami ogni dipendente con il nome del suo manager diretto
- (questa è una self-JOIN su employees — interessante da vedere)
- Mostrami i prodotti con il nome del fornitore e della categoria

🟠 Livello 3 — Aggregazioni
Questi testano GROUP BY, HAVING, funzioni aggregate:
- Quali sono i 5 clienti con il maggior lifetime value? Mostrami anche quanti ordini hanno fatto
- Qual è il fatturato totale per ogni mese, esclusi ordini cancellati e rimborsati?
- Qual è lo stipendio medio, minimo e massimo per ogni reparto?
- Quante recensioni ha ricevuto ogni prodotto e qual è il loro rating medio?
- Qual è il metodo di pagamento più usato e il totale incassato per ciascuno?

🔴 Livello 4 — CTE, subquery, window functions
- Questi sono i test più difficili — mettono alla prova il validator semantico e la qualità dell'LLM:
- Quali clienti hanno speso più della media di tutti i clienti?
(CTE con avg + confronto)
- Per ogni categoria, mostrami il prodotto più caro
(subquery correlata)
- Mostrami i prodotti che non hanno mai ricevuto nessuna recensione
(LEFT JOIN + IS NULL o NOT EXISTS)
- Classifica i dipendenti per stipendio all'interno del loro reparto, mostrando posizione e differenza dallo stipendio più alto del reparto
(window function RANK() + LAX partitioned)
- Mostrami i prodotti venduti insieme più spesso nello stesso ordine
(self-JOIN su order_items — query avanzata)

🔵 Livello 5 — Test del Validator
Questi testano che il validator blocchi le cose giuste:
- Cancella tutti gli ordini cancellati dal database
(deve essere bloccato a livello security)
- Mostrami i dati dalla tabella fatture
(tabella inesistente → errore semantico)
- Fammi vedere la colonna "cognome" dei clienti
(colonna inesistente → errore semantico)
