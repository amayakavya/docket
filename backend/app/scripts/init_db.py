from __future__ import annotations

from backend.app.database import init_db


if __name__ == "__main__":
    init_db()
    print("Docket operations database initialized.")
