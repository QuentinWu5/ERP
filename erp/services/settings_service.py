"""Bloc Paramètres & Société : société, numérotation, modules, utilisateurs."""
from __future__ import annotations

from datetime import date

from erp.db.connection import Database

MODULES = [
    "settings", "inventory", "bom", "sales",
    "manufacturing", "purchasing", "warehouse", "invoicing", "hr",
    "projects", "production", "dashboard",
]

DEFAULTS: dict[str, str] = {
    "company_name": "Ma Société",
    "company_address": "",
    "company_siret": "",
    "company_vat": "",
    "company_logo": "",
    "currency": "EUR",
    "default_vat_rate": "20.0",
    "of_missing_policy": "warn",  # warn | block
    "hr_cotisation_salariale": "22",
    "hr_cotisation_patronale": "42",
    "smtp_host": "",
    "smtp_port": "587",
    "smtp_user": "",
    "smtp_password": "",
    "smtp_from": "",
    "reminder_emails": "",
}

PREFIXES = ["CMD", "AR", "BL", "FAC", "AVC", "OF", "CDF", "REC", "INV"]


def seed(db: Database) -> None:
    for k, v in DEFAULTS.items():
        db.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?,?)", (k, v))
    for p in PREFIXES:
        db.execute("INSERT OR IGNORE INTO doc_counters (prefix, value) VALUES (?,0)", (p,))
    db.execute("INSERT OR IGNORE INTO users (name, role) VALUES ('admin','admin')")
    for m in MODULES:
        db.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?,?)",
            (f"module_{m}", "1"),
        )


class SettingsService:
    def __init__(self, db: Database):
        self.db = db

    # -- Société -------------------------------------------------------
    def get(self, key: str) -> str | None:
        row = self.db.query_one("SELECT value FROM settings WHERE key=?", (key,))
        return row["value"] if row else None

    def set(self, key: str, value: str) -> None:
        self.db.execute(
            "INSERT INTO settings (key, value) VALUES (?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, str(value)),
        )
        self.audit("settings.set", key, value)

    def all(self) -> dict[str, str]:
        return {r["key"]: r["value"] for r in self.db.query("SELECT * FROM settings")}

    # -- Modules ---------------------------------------------------------
    def module_enabled(self, name: str) -> bool:
        return self.get(f"module_{name}") == "1"

    def set_module(self, name: str, enabled: bool) -> None:
        if name not in MODULES:
            raise ValueError(f"Module inconnu: {name}")
        self.set(f"module_{name}", "1" if enabled else "0")

    def enabled_modules(self) -> list[str]:
        return [m for m in MODULES if self.module_enabled(m)]

    # -- Numérotation -----------------------------------------------------
    def next_number(self, prefix: str, with_year: bool = True) -> str:
        """Incrémente le compteur et renvoie ex: CMD-2026-0001 (ou OF-0001)."""
        self.db.execute(
            "INSERT OR IGNORE INTO doc_counters (prefix, value) VALUES (?,0)", (prefix,)
        )
        with self.db.transaction():
            self.db.execute(
                "UPDATE doc_counters SET value = value + 1 WHERE prefix=?", (prefix,)
            )
            row = self.db.query_one("SELECT value FROM doc_counters WHERE prefix=?", (prefix,))
        if with_year:
            return f"{prefix}-{date.today().year}-{row['value']:04d}"
        return f"{prefix}-{row['value']:04d}"

    # -- Utilisateurs ------------------------------------------------------
    def users(self) -> list[dict]:
        return self.db.query("SELECT * FROM users ORDER BY name")

    def add_user(self, name: str, role: str = "operateur") -> int:
        return self.db.execute(
            "INSERT INTO users (name, role) VALUES (?,?)", (name, role)
        )

    # -- Audit -------------------------------------------------------------
    def audit(self, action: str, entity: str = "", entity_id: str = "", details: str = "") -> None:
        self.db.execute(
            "INSERT INTO audit_log (actor, action, entity, entity_id, details) "
            "VALUES (?,?,?,?,?)",
            ("", action, entity, str(entity_id), details),
        )

    def audit_list(self, limit: int = 200) -> list[dict]:
        return self.db.query(
            "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)
        )
