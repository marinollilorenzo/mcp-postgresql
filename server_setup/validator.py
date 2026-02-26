"""
validator.py
------------
Validazione SQL a 3 livelli:

  1. SECURITY   → solo SELECT permesso (blocca DDL/DML pericolosi)
  2. SYNTACTIC  → parsing con sqlglot (query ben formata?)
  3. SEMANTIC   → tabelle e colonne nella query esistono nello schema?

Il risultato è sempre un oggetto ValidationResult serializzabile.
"""

import re
import logging
from dataclasses import dataclass, field

import sqlglot
import sqlglot.expressions as exp

from database import TableInfo

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
#  Risultato della validazione
# ──────────────────────────────────────────────

@dataclass
class ValidationResult:
    is_valid: bool
    errors: list[str]            = field(default_factory=list)
    warnings: list[str]          = field(default_factory=list)
    normalized_sql: str          = ""
    validation_levels_passed: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "is_valid": self.is_valid,
            "errors": self.errors,
            "warnings": self.warnings,
            "normalized_sql": self.normalized_sql,
            "validation_levels_passed": self.validation_levels_passed,
        }


# ──────────────────────────────────────────────
#  Costanti di sicurezza
# ──────────────────────────────────────────────

# Tipi di statement permessi
ALLOWED_STATEMENT_TYPES = {exp.Select}

# Keyword pericolose come fallback regex (per query offuscate)
DANGEROUS_PATTERNS = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|TRUNCATE|ALTER|CREATE|REPLACE|MERGE"
    r"|GRANT|REVOKE|EXECUTE|EXEC|CALL|COPY|VACUUM|ANALYZE|COMMENT)\b",
    re.IGNORECASE,
)

# Funzioni PostgreSQL potenzialmente pericolose
DANGEROUS_FUNCTIONS = {
    "pg_sleep", "pg_cancel_backend", "pg_terminate_backend",
    "pg_reload_conf", "pg_rotate_logfile", "pg_read_file",
    "pg_read_binary_file", "pg_ls_dir", "lo_import", "lo_export",
}


# ──────────────────────────────────────────────
#  SQLValidator
# ──────────────────────────────────────────────

class SQLValidator:
    """
    Valida una query SQL a 3 livelli (security → syntactic → semantic).
    Può essere usato senza schema per la sola validazione sintattica/security.
    """

    def __init__(self, schema: dict[str, TableInfo] | None = None):
        """
        Args:
            schema: dizionario {table_name: TableInfo} dallo schema del DB.
                    Se None, la validazione semantica viene saltata.
        """
        self._schema = schema

    def validate(self, sql: str) -> ValidationResult:
        """
        Esegue la validazione completa. I 3 livelli sono sequenziali:
        se un livello fallisce, i successivi non vengono eseguiti.
        """
        result = ValidationResult(is_valid=False, normalized_sql=sql.strip())

        # ── Level 1: Security ──────────────────
        if not self._validate_security(sql, result):
            return result
        result.validation_levels_passed.append("security")

        # ── Level 2: Syntactic ─────────────────
        parsed = self._validate_syntactic(sql, result)
        if parsed is None:
            return result
        result.validation_levels_passed.append("syntactic")

        # Normalizza SQL
        try:
            result.normalized_sql = parsed.sql(dialect="postgres", pretty=True)
        except Exception:
            result.normalized_sql = sql.strip()

        # ── Level 3: Semantic ──────────────────
        if self._schema is not None:
            if not self._validate_semantic(parsed, result):
                return result
            result.validation_levels_passed.append("semantic")
        else:
            result.warnings.append(
                "Validazione semantica saltata: schema non disponibile."
            )

        result.is_valid = True
        return result

    # ── Level 1: Security ─────────────────────

    def _validate_security(self, sql: str, result: ValidationResult) -> bool:
        """
        Blocca qualsiasi statement che non sia SELECT.
        Usa sia sqlglot parsing sia regex come doppio controllo.
        """
        # Controllo regex veloce
        if DANGEROUS_PATTERNS.search(sql):
            result.errors.append(
                "Security: la query contiene keyword non permesse. "
                "Solo statement SELECT sono consentiti."
            )
            return False

        # Controllo commenti (potrebbe nascondere keyword)
        sql_no_comments = _strip_sql_comments(sql)
        if DANGEROUS_PATTERNS.search(sql_no_comments):
            result.errors.append(
                "Security: keyword pericolosa rilevata dopo la rimozione dei commenti."
            )
            return False

        # Controllo funzioni pericolose
        sql_lower = sql.lower()
        for fn in DANGEROUS_FUNCTIONS:
            if fn in sql_lower:
                result.errors.append(
                    f"Security: la funzione '{fn}' non è permessa."
                )
                return False

        # Controllo punto e virgola multipli (stacked queries)
        clean = sql.strip().rstrip(";")
        if ";" in clean:
            result.errors.append(
                "Security: query multiple (stacked queries) non sono permesse."
            )
            return False

        return True

    # ── Level 2: Syntactic ────────────────────

    def _validate_syntactic(
        self, sql: str, result: ValidationResult
    ) -> exp.Expression | None:
        """
        Usa sqlglot per fare il parsing della query.
        Restituisce l'AST se valido, None altrimenti.
        """
        try:
            statements = sqlglot.parse(sql, dialect="postgres", error_level=sqlglot.ErrorLevel.RAISE)
        except sqlglot.errors.ParseError as e:
            result.errors.append(f"Sintassi: {e}")
            return None
        except Exception as e:
            result.errors.append(f"Errore di parsing inatteso: {e}")
            return None

        if not statements:
            result.errors.append("Sintassi: nessuna query rilevata.")
            return None

        if len(statements) > 1:
            result.errors.append("Sintassi: è permessa una sola query per volta.")
            return None

        stmt = statements[0]

        # Verifica che sia un SELECT
        if not isinstance(stmt, exp.Select):
            result.errors.append(
                f"Sintassi: solo query SELECT sono permesse, "
                f"trovato: {type(stmt).__name__}."
            )
            return None

        return stmt

    # ── Level 3: Semantic ─────────────────────

    def _validate_semantic(
        self, parsed: exp.Expression, result: ValidationResult
    ) -> bool:
        """
        Controlla che tabelle e colonne referenziate nella query
        esistano nello schema del database.

        Note:
         - Gestisce alias di tabelle
         - Segnala come WARNING le colonne non verificabili
           (es: *, funzioni di aggregazione)
         - Non blocca per wildcard SELECT *
        """
        schema_tables = {name.lower(): info for name, info in self._schema.items()}
        errors_found = False

        # ── Tabelle ────────────────────────────
        # Raccogli tutte le tabelle referenziate con i loro alias
        alias_map: dict[str, str] = {}   # alias → table_name reale
        referenced_tables: set[str] = set()

        for table_node in parsed.find_all(exp.Table):
            t_name = table_node.name.lower() if table_node.name else ""
            alias  = table_node.alias.lower() if table_node.alias else t_name

            if not t_name:
                continue

            referenced_tables.add(t_name)
            alias_map[alias] = t_name

            if t_name not in schema_tables:
                result.errors.append(
                    f"Semantica: la tabella '{t_name}' non esiste nello schema."
                )
                errors_found = True

        if errors_found:
            return False

        # ── Colonne ────────────────────────────
        for col_node in parsed.find_all(exp.Column):
            col_name   = col_node.name.lower() if col_node.name else ""
            table_ref  = col_node.table.lower() if col_node.table else None

            if not col_name or col_name == "*":
                continue

            # Determina a quale tabella appartiene la colonna
            if table_ref:
                # Risolvi alias
                real_table = alias_map.get(table_ref, table_ref)
                if real_table in schema_tables:
                    table_info = schema_tables[real_table]
                    known_cols = {c.name.lower() for c in table_info.columns}
                    if col_name not in known_cols:
                        result.errors.append(
                            f"Semantica: la colonna '{col_name}' non esiste "
                            f"nella tabella '{real_table}'."
                        )
                        errors_found = True
            else:
                # Colonna senza tabella esplicita: cerca in tutte le tabelle ref
                found_in_any = any(
                    col_name in {c.name.lower() for c in schema_tables[t].columns}
                    for t in referenced_tables
                    if t in schema_tables
                )
                if not found_in_any and referenced_tables:
                    result.warnings.append(
                        f"Semantica: impossibile verificare la colonna '{col_name}' "
                        f"senza qualificatore di tabella."
                    )

        return not errors_found


# ──────────────────────────────────────────────
#  Helper privati
# ──────────────────────────────────────────────

def _strip_sql_comments(sql: str) -> str:
    """Rimuove commenti SQL (-- e /* */) dal testo."""
    # Rimuove block comments
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    # Rimuove line comments
    sql = re.sub(r"--[^\n]*", " ", sql)
    return sql