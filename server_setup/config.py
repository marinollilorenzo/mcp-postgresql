"""
config.py
---------
Configurazione centralizzata caricata da variabili d'ambiente (.env).
Tutte le impostazioni del server MCP passano da qui.
"""

import os
from dotenv import load_dotenv
from pydantic import BaseModel, field_validator

load_dotenv()


class DatabaseConfig(BaseModel):
    host: str = "localhost"
    port: int = 5432
    name: str
    user: str
    password: str
    schema: str = "public"
    min_pool: int = 2
    max_pool: int = 10

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


class ServerConfig(BaseModel):
    transport: str = "stdio"
    sse_host: str = "0.0.0.0"
    sse_port: int = 8000
    query_row_limit: int = 500
    query_timeout: int = 30
    log_level: str = "INFO"

    @field_validator("transport")
    @classmethod
    def validate_transport(cls, v: str) -> str:
        allowed = {"stdio", "sse", "streamable-http"}
        if v.lower() not in allowed:
            raise ValueError(f"TRANSPORT deve essere uno di: {allowed}")
        return v.lower()


def load_config() -> tuple[DatabaseConfig, ServerConfig]:
    """Carica e valida tutta la configurazione dall'ambiente."""
    db_cfg = DatabaseConfig(
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", "5432")),
        name=os.environ["DB_NAME"],        # obbligatorio
        user=os.environ["DB_USER"],        # obbligatorio
        password=os.environ["DB_PASSWORD"],  # obbligatorio
        schema=os.getenv("DB_SCHEMA", "public"),
        min_pool=int(os.getenv("DB_MIN_POOL", "2")),
        max_pool=int(os.getenv("DB_MAX_POOL", "10")),
    )
    srv_cfg = ServerConfig(
        transport=os.getenv("TRANSPORT", "stdio"),
        sse_host=os.getenv("SSE_HOST", "0.0.0.0"),
        sse_port=int(os.getenv("SSE_PORT", "8000")),
        query_row_limit=int(os.getenv("QUERY_ROW_LIMIT", "500")),
        query_timeout=int(os.getenv("QUERY_TIMEOUT", "30")),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
    )
    return db_cfg, srv_cfg