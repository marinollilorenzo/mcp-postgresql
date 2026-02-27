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






------------------------------------------------------------------------------
🟢 Livello 1 — Tool di base (schema + query semplici)
Questi testano che la connessione, lo schema e le query basilari funzionino:
schema
(comando speciale del client — verifica che get_schema risponda)
Quanti clienti abbiamo nel database?
Mostrami tutti i prodotti con stock inferiore a 30 pezzi, ordinati dal meno fornito
Quali ordini sono ancora in stato pending o confirmed?
Mostrami tutti i dipendenti del reparto Engineering con il loro stipendio

🟡 Livello 2 — JOIN tra tabelle
Questi testano che l'LLM legga correttamente le FK dello schema:
Mostrami tutti gli ordini con il nome del cliente, lo stato e il totale
Quali prodotti ha ordinato Mario Rossi? Mostrami nome prodotto, quantità e prezzo pagato
Mostrami ogni dipendente con il nome del suo manager diretto
(questa è una self-JOIN su employees — interessante da vedere)
Mostrami i prodotti con il nome del fornitore e della categoria

🟠 Livello 3 — Aggregazioni
Questi testano GROUP BY, HAVING, funzioni aggregate:
Quali sono i 5 clienti con il maggior lifetime value? Mostrami anche quanti ordini hanno fatto
Qual è il fatturato totale per ogni mese, esclusi ordini cancellati e rimborsati?
Qual è lo stipendio medio, minimo e massimo per ogni reparto?
Quante recensioni ha ricevuto ogni prodotto e qual è il loro rating medio?
Qual è il metodo di pagamento più usato e il totale incassato per ciascuno?

🔴 Livello 4 — CTE, subquery, window functions
Questi sono i test più difficili — mettono alla prova il validator semantico e la qualità dell'LLM:
Quali clienti hanno speso più della media di tutti i clienti?
(CTE con avg + confronto)
Per ogni categoria, mostrami il prodotto più caro
(subquery correlata)
Mostrami i prodotti che non hanno mai ricevuto nessuna recensione
(LEFT JOIN + IS NULL o NOT EXISTS)
Classifica i dipendenti per stipendio all'interno del loro reparto, mostrando posizione e differenza dallo stipendio più alto del reparto
(window function RANK() + LAX partitioned)
Mostrami i prodotti venduti insieme più spesso nello stesso ordine
(self-JOIN su order_items — query avanzata)

🔵 Livello 5 — Test del Validator
Questi testano che il validator blocchi le cose giuste:
Cancella tutti gli ordini cancellati dal database
(deve essere bloccato a livello security)
Mostrami i dati dalla tabella fatture
(tabella inesistente → errore semantico)
Fammi vedere la colonna "cognome" dei clienti
(colonna inesistente → errore semantico)