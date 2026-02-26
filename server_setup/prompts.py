"""
prompts.py
----------
Prompt MCP registrati via @mcp.prompt() decorator (FastMCP style).

I prompt ritornano direttamente stringhe (o liste di Message) —
nessun PromptMessage/TextContent manuale.
"""

from mcp.server.fastmcp.prompts import base


def sql_query_builder(schema_json: str, user_question: str) -> str:
    """
    Guida l'LLM nella costruzione di query SQL PostgreSQL corrette
    a partire dallo schema del database. Include esempi di JOIN,
    aggregazioni (GROUP BY, COUNT, SUM), CTE e subquery.
    """
    return f"""Sei un esperto SQL che lavora con un database PostgreSQL.
Di seguito trovi lo schema del database in formato JSON, seguito da esempi
di query SQL ben costruite. Usa lo schema e gli esempi per rispondere
alla domanda dell'utente con una query SQL valida e ottimizzata.

════════════════════════════════════════
SCHEMA DEL DATABASE
════════════════════════════════════════
{schema_json}

════════════════════════════════════════
ESEMPI DI QUERY SQL
════════════════════════════════════════

── Esempio 1: JOIN tra tabelle ─────────────────────────────────────────────
Domanda: "Mostrami tutti gli ordini con i dati del cliente associato"

SELECT
    o.id          AS order_id,
    o.created_at,
    o.total_amount,
    c.full_name   AS customer_name,
    c.email       AS customer_email
FROM orders o
JOIN customers c ON o.customer_id = c.id
WHERE o.status = 'completed'
ORDER BY o.created_at DESC

── Esempio 2: Aggregazione con GROUP BY ────────────────────────────────────
Domanda: "Qual è il totale degli ordini per ogni cliente?"

SELECT
    c.full_name              AS customer_name,
    COUNT(o.id)              AS total_orders,
    SUM(o.total_amount)      AS revenue_total,
    AVG(o.total_amount)      AS avg_order_value
FROM customers c
LEFT JOIN orders o ON o.customer_id = c.id
GROUP BY c.id, c.full_name
HAVING COUNT(o.id) > 0
ORDER BY revenue_total DESC

── Esempio 3: CTE ──────────────────────────────────────────────────────────
Domanda: "Trovami i clienti il cui totale speso supera la media"

WITH customer_totals AS (
    SELECT customer_id, SUM(total_amount) AS total_spent
    FROM orders
    WHERE status = 'completed'
    GROUP BY customer_id
),
avg_spending AS (
    SELECT AVG(total_spent) AS avg_value FROM customer_totals
)
SELECT c.full_name, ct.total_spent,
       ROUND(ct.total_spent - avg.avg_value, 2) AS above_average_by
FROM customer_totals ct
JOIN customers c ON ct.customer_id = c.id
CROSS JOIN avg_spending avg
WHERE ct.total_spent > avg.avg_value
ORDER BY ct.total_spent DESC

── Esempio 4: Subquery correlata ────────────────────────────────────────────
Domanda: "Per ogni categoria, il prodotto con il prezzo più alto"

SELECT cat.name AS category, p.name AS top_product, p.price
FROM products p
JOIN categories cat ON p.category_id = cat.id
WHERE p.price = (
    SELECT MAX(p2.price) FROM products p2
    WHERE p2.category_id = p.category_id
)
ORDER BY cat.name

── Esempio 5: Window Function ───────────────────────────────────────────────
Domanda: "Classifica i clienti per fatturato"

SELECT
    c.full_name,
    SUM(o.total_amount)                                   AS total_revenue,
    RANK() OVER (ORDER BY SUM(o.total_amount) DESC)       AS revenue_rank
FROM customers c
JOIN orders o ON o.customer_id = c.id
GROUP BY c.id, c.full_name
ORDER BY total_revenue DESC

════════════════════════════════════════
REGOLE
════════════════════════════════════════
1. Usa SOLO tabelle e colonne presenti nello schema sopra
2. Usa alias chiari (AS nome_leggibile)
3. Solo SELECT — nessun INSERT/UPDATE/DELETE
4. Niente punto e virgola finale
5. NON usare SELECT *

════════════════════════════════════════
DOMANDA DELL'UTENTE
════════════════════════════════════════
{user_question}

Rispondi SOLO con la query SQL, senza spiegazioni."""


def schema_explorer(schema_json: str) -> list[base.Message]:
    """
    Analizza lo schema del database e mostra relazioni,
    chiavi primarie/foreign key e suggerimenti di JOIN.
    """
    return [
        base.UserMessage(
            f"""Analizza il seguente schema PostgreSQL e fornisci:
1. Elenco tabelle con funzione probabile
2. Relazioni FK → PK tra tabelle
3. Colonne chiave per ogni tabella
4. Suggerimenti JOIN utili

Schema:
{schema_json}"""
        ),
        base.AssistantMessage(
            "Analizzo lo schema e identifico le relazioni tra le tabelle..."
        ),
    ]