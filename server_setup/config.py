"""
config.py
---------
Configurazione centralizzata caricata da variabili d'ambiente (.env).
Tutte le impostazioni del server MCP passano da qui.
Utilizza pydantic-settings per il caricamento automatico dall'ambiente.
"""

from typing import List
from pathlib import Path
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ENV_FILE = str(Path(__file__).parent.parent / ".env")


class DatabaseConfig(BaseSettings):
    host: str = Field(default="localhost", alias="DB_HOST")
    port: int = Field(default=5432, alias="DB_PORT")
    name: str = Field(..., alias="DB_NAME")
    user: str = Field(..., alias="DB_USER")
    password: str = Field(..., alias="DB_PASSWORD")
    schema_name: str = Field(default="public", alias="DB_SCHEMA")
    min_pool: int = Field(default=2, alias="DB_MIN_POOL")
    max_pool: int = Field(default=10, alias="DB_MAX_POOL")

    model_config = SettingsConfigDict(env_file=ENV_FILE, extra="ignore")

    @field_validator("port")
    @classmethod
    def validate_port(cls, v: int) -> int:
        if not (1 <= v <= 65535):
            raise ValueError(f"Porta non valida: {v}")
        return v

    @property
    def dsn(self) -> str:
        return (
            f"postgresql://{self.user}:{self.password}"
            f"@{self.host}:{self.port}/{self.name}"
        )


class ServerConfig(BaseSettings):
    transport: str = Field(default="stdio", alias="TRANSPORT")
    sse_host: str = Field(default="0.0.0.0", alias="SSE_HOST")
    sse_port: int = Field(default=8000, alias="SSE_PORT")

    # Query limits
    query_row_limit: int = Field(default=500, alias="QUERY_ROW_LIMIT")
    query_timeout: int = Field(default=30, alias="QUERY_TIMEOUT")
    max_joins: int = Field(default=8, alias="MAX_JOINS")

    # Cache
    schema_cache_ttl: int = Field(default=300, alias="SCHEMA_CACHE_TTL")

    # Access control: Letti come stringhe per aggirare il crash JSON di Pydantic
    raw_allowed_tables: str = Field(default="", alias="ALLOWED_TABLES")
    raw_denied_tables: str = Field(default="", alias="DENIED_TABLES")

    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    model_config = SettingsConfigDict(env_file=ENV_FILE, extra="ignore")

    @field_validator("transport")
    @classmethod
    def validate_transport(cls, v: str) -> str:
        v = v.lower()
        allowed = {"stdio", "sse", "streamable-http"}
        if v not in allowed:
            raise ValueError(f"TRANSPORT deve essere uno di: {allowed}")
        return v

    @property
    def allowed_tables(self) -> List[str]:
        """Converte la stringa ALLOWED_TABLES in una lista Python in modo sicuro."""
        if not self.raw_allowed_tables.strip():
            return []
        return [t.strip().lower() for t in self.raw_allowed_tables.split(",") if t.strip()]

    @property
    def denied_tables(self) -> List[str]:
        """Converte la stringa DENIED_TABLES in una lista Python in modo sicuro."""
        if not self.raw_denied_tables.strip():
            return []
        return [t.strip().lower() for t in self.raw_denied_tables.split(",") if t.strip()]


def load_config() -> tuple[DatabaseConfig, ServerConfig]:
    """
    Carica e valida tutta la configurazione dall'ambiente.
    """
    db_cfg = DatabaseConfig()
    srv_cfg = ServerConfig()
    return db_cfg, srv_cfg