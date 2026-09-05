import re
import logging
from dataclasses import dataclass, field
from typing import Optional, Dict, Set

import sqlglot
import sqlglot.expressions as exp

# Assumendo che TableInfo sia definito nel tuo database.py
from database import TableInfo

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
#  Risultato della validazione
# ──────────────────────────────────────────────

@dataclass
class ValidationResult:
    is_valid: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    normalized_sql: str = ""
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

SQL_DIALECT = "postgres"

DANGEROUS_PATTERNS = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|TRUNCATE|ALTER|CREATE|REPLACE|MERGE"
    r"|GRANT|REVOKE|EXECUTE|EXEC|CALL|COPY|VACUUM|ANALYZE|COMMENT)\b",
    re.IGNORECASE,
)

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
    Stateless: riceve lo schema direttamente nel metodo validate().
    """

    def __init__(self, max_joins: int = 8):
        self._max_joins = max_joins

    def validate(self, sql: str, schema: Optional[Dict[str, TableInfo]] = None) -> ValidationResult:
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

        try:
            result.normalized_sql = parsed.sql(dialect=SQL_DIALECT, pretty=True)
        except Exception:
            result.normalized_sql = sql.strip()

        # ── Level 3: Semantic ──────────────────
        if schema is not None:
            if not self._validate_semantic(parsed, schema, result):
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
        if DANGEROUS_PATTERNS.search(sql):
            result.errors.append(
                "Security: la query contiene keyword non permesse. "
                "Solo statement SELECT sono consentiti."
            )
            return False

        sql_no_comments = _strip_sql_comments(sql)
        if DANGEROUS_PATTERNS.search(sql_no_comments):
            result.errors.append(
                "Security: keyword pericolosa rilevata dopo la rimozione dei commenti."
            )
            return False

        sql_lower = sql.lower()
        for fn in DANGEROUS_FUNCTIONS:
            if fn in sql_lower:
                result.errors.append(f"Security: la funzione '{fn}' non è permessa.")
                return False

        clean = sql.strip().rstrip(";")
        if ";" in clean:
            result.errors.append(
                "Security: query multiple (stacked queries) non sono permesse."
            )
            return False

        return True

    # ── Level 2: Syntactic ────────────────────

    def _validate_syntactic(self, sql: str, result: ValidationResult) -> Optional[exp.Expression]:
        try:
            statements = sqlglot.parse(
                sql, read=SQL_DIALECT, error_level=sqlglot.ErrorLevel.RAISE
            )
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

        if not isinstance(stmt, exp.Select):
            result.errors.append(
                f"Sintassi: solo query SELECT sono permesse, "
                f"trovato: {type(stmt).__name__}."
            )
            return None

        return stmt

    # ── Level 3: Semantic ─────────────────────

    def _validate_semantic(
        self, 
        parsed: exp.Expression, 
        schema: Dict[str, TableInfo], 
        result: ValidationResult
    ) -> bool:
        schema_tables = {name.lower(): info for name, info in schema.items()}
        errors_found = False

        # ── SELECT * warning ───────────────────
        if list(parsed.find_all(exp.Star)):
            result.warnings.append(
                "Semantica: uso di SELECT * rilevato. "
                "Preferisci selezionare colonne specifiche per query più efficienti."
            )

        # ── Complessità JOIN ───────────────────
        join_count = len(list(parsed.find_all(exp.Join)))
        if join_count > self._max_joins:
            result.errors.append(
                f"Complessità: la query ha {join_count} JOIN, "
                f"il massimo consentito è {self._max_joins}."
            )
            return False

        # ── Estrazione CTE ─────────────────────
        cte_names: Set[str] = {
            cte.alias.lower() for cte in parsed.find_all(exp.CTE) if cte.alias
        }

        # ── Verifica Tabelle ───────────────────
        alias_map: Dict[str, str] = {}
        referenced_tables: Set[str] = set()

        for table_node in parsed.find_all(exp.Table):
            t_name = table_node.name.lower() if table_node.name else ""
            
            # Sqlglot gestisce gli alias in modo leggermente diverso a volte
            alias = table_node.alias.lower() if table_node.alias else t_name

            if not t_name:
                continue

            # Se è una CTE, la registriamo nella mappa degli alias ma non la verifichiamo sul DB
            if t_name in cte_names:
                alias_map[alias] = t_name
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

        # ── Verifica Colonne ───────────────────
        for col_node in parsed.find_all(exp.Column):
            col_name = col_node.name.lower() if col_node.name else ""
            table_ref = col_node.table.lower() if col_node.table else None

            if not col_name or col_name == "*":
                continue

            if table_ref:
                real_table = alias_map.get(table_ref, table_ref)
                
                # Se la tabella di riferimento è una CTE, non possiamo validare le colonne
                if real_table in cte_names:
                    continue
                    
                if real_table in schema_tables:
                    known_cols = {c.name.lower() for c in schema_tables[real_table].columns}
                    if col_name not in known_cols:
                        result.errors.append(
                            f"Semantica: la colonna '{col_name}' non esiste "
                            f"nella tabella '{real_table}'."
                        )
                        errors_found = True
            else:
                # Colonna senza alias (es. SELECT id FROM users)
                real_tables = {t for t in referenced_tables if t not in cte_names}
                
                # Cerchiamo la colonna in tutte le tabelle referenziate (non CTE)
                found_in_any = any(
                    col_name in {c.name.lower() for c in schema_tables[t].columns}
                    for t in real_tables
                    if t in schema_tables
                )
                
                if not found_in_any and real_tables:
                    result.warnings.append(
                        f"Semantica: la colonna '{col_name}' non è stata trovata in nessuna "
                        f"delle tabelle referenziate. Assicurati che esista o usa alias espliciti."
                    )

        return not errors_found


# ──────────────────────────────────────────────
#  Helper privati
# ──────────────────────────────────────────────

def _strip_sql_comments(sql: str) -> str:
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    sql = re.sub(r"--[^\n]*", " ", sql)
    return sql