#!/usr/bin/env python3
"""Lancement de l'ERP : python main.py"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))


def main() -> None:
    from erp.db.connection import Database, set_db_path
    from erp.db.migrations import migrate
    from erp.seed import seed_demo
    from erp.ui.app import App

    db_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent / "erp.db"
    set_db_path(db_path)
    db = Database()
    migrate(db.conn)
    seed_demo(db)

    app = App(db)
    app.mainloop()
    db.close()


if __name__ == "__main__":
    main()
