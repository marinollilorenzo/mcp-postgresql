"""
main.py
-------
Entry point del server MCP PostgreSQL.

I moduli in `server_setup/` si importano fra loro per nome (`from config import ...`),
quindi quella cartella va messa sul path prima di caricarli. Fatto qui, il server
parte da qualsiasi directory:

    uv run python main.py
"""

import sys
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parent / "server_setup"
sys.path.insert(0, str(SERVER_DIR))

from server import main as run_server  # noqa: E402


if __name__ == "__main__":
    run_server()
