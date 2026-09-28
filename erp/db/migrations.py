"""Migrations simples et versionnées.

Chaque migration est une fonction qui reçoit la connexion brute et
crée/altère des tables. La version appliquée est stockée dans
schema_migrations ; les migrations déjà jouées sont ignorées.
"""
from __future__ import annotations

import sqlite3

SCHEMA_VERSION = 3

MIGRATIONS: dict[int, str] = {
    1: """
    -- Paramètres & société
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE,
        role TEXT NOT NULL DEFAULT 'operateur'
    );

    -- Compteurs de numérotation
    CREATE TABLE IF NOT EXISTS doc_counters (
        prefix TEXT PRIMARY KEY,          -- ex: CMD, OF, FAC
        value INTEGER NOT NULL DEFAULT 0
    );

    -- Inventaire
    CREATE TABLE IF NOT EXISTS articles (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        sku TEXT NOT NULL UNIQUE,
        designation TEXT NOT NULL,
        type TEXT NOT NULL DEFAULT 'composant'
            CHECK (type IN ('produit_fini','composant','consommable','marchandise','service')),
        unit TEXT NOT NULL DEFAULT 'pce',
        purchase_price REAL NOT NULL DEFAULT 0,
        sale_price REAL NOT NULL DEFAULT 0,
        vat_rate REAL NOT NULL DEFAULT 20.0,
        stock_qty REAL NOT NULL DEFAULT 0,
        reserved_qty REAL NOT NULL DEFAULT 0,
        min_stock REAL NOT NULL DEFAULT 0,
        cmup REAL NOT NULL DEFAULT 0,
        track_lots INTEGER NOT NULL DEFAULT 0,
        active INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
        updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
    );

    CREATE TABLE IF NOT EXISTS locations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        article_id INTEGER NOT NULL REFERENCES articles(id),
        store TEXT NOT NULL DEFAULT 'MAG',
        shelf TEXT NOT NULL DEFAULT '',
        qty REAL NOT NULL DEFAULT 0,
        UNIQUE (article_id, store, shelf)
    );

    CREATE TABLE IF NOT EXISTS stock_moves (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        article_id INTEGER NOT NULL REFERENCES articles(id),
        move_type TEXT NOT NULL CHECK (move_type IN
            ('entree','sortie','ajustement','transfert','reprise_initiale')),
        qty REAL NOT NULL,                 -- signé : + entrée, - sortie
        unit_cost REAL NOT NULL DEFAULT 0,
        cmup_after REAL,
        reason TEXT NOT NULL DEFAULT '',
        source_doc TEXT NOT NULL DEFAULT '',
        lot TEXT NOT NULL DEFAULT '',
        location_from TEXT NOT NULL DEFAULT '',
        location_to TEXT NOT NULL DEFAULT '',
        moved_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
    );
    CREATE INDEX IF NOT EXISTS idx_moves_article ON stock_moves(article_id);
    CREATE INDEX IF NOT EXISTS idx_moves_doc ON stock_moves(source_doc);

    CREATE TABLE IF NOT EXISTS stock_counts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        reference TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'ouvert',   -- ouvert | valide
        created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
        validated_at TEXT
    );

    CREATE TABLE IF NOT EXISTS stock_count_lines (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        count_id INTEGER NOT NULL REFERENCES stock_counts(id),
        article_id INTEGER NOT NULL REFERENCES articles(id),
        counted_qty REAL NOT NULL,
        system_qty REAL NOT NULL,
        UNIQUE (count_id, article_id)
    );

    -- Nomenclatures (BOM) multi-niveaux, versionnées
    CREATE TABLE IF NOT EXISTS boms (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        parent_id INTEGER NOT NULL REFERENCES articles(id),
        version TEXT NOT NULL DEFAULT 'A',
        variant TEXT NOT NULL DEFAULT '',
        active INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
        UNIQUE (parent_id, version, variant)
    );

    CREATE TABLE IF NOT EXISTS bom_lines (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        bom_id INTEGER NOT NULL REFERENCES boms(id),
        component_id INTEGER NOT NULL REFERENCES articles(id),
        qty_per REAL NOT NULL,
        scrap_pct REAL NOT NULL DEFAULT 0,
        UNIQUE (bom_id, component_id)
    );

    -- Journal d'audit
    CREATE TABLE IF NOT EXISTS audit_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        actor TEXT NOT NULL DEFAULT '',
        action TEXT NOT NULL,
        entity TEXT NOT NULL DEFAULT '',
        entity_id TEXT NOT NULL DEFAULT '',
        details TEXT NOT NULL DEFAULT '',
        at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
    );
    """,
    2: """
    -- Clients
    CREATE TABLE IF NOT EXISTS customers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        code TEXT NOT NULL UNIQUE,
        name TEXT NOT NULL,
        address TEXT NOT NULL DEFAULT '',
        email TEXT NOT NULL DEFAULT '',
        phone TEXT NOT NULL DEFAULT '',
        payment_terms TEXT NOT NULL DEFAULT '30 jours',
        vat_applicable INTEGER NOT NULL DEFAULT 1,
        notes TEXT NOT NULL DEFAULT '',
        active INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
    );

    -- Fournisseurs
    CREATE TABLE IF NOT EXISTS suppliers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        code TEXT NOT NULL UNIQUE,
        name TEXT NOT NULL,
        address TEXT NOT NULL DEFAULT '',
        email TEXT NOT NULL DEFAULT '',
        phone TEXT NOT NULL DEFAULT '',
        payment_terms TEXT NOT NULL DEFAULT '30 jours',
        notes TEXT NOT NULL DEFAULT '',
        active INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
    );

    -- Prix d'achat par fournisseur
    CREATE TABLE IF NOT EXISTS supplier_prices (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        supplier_id INTEGER NOT NULL REFERENCES suppliers(id),
        article_id INTEGER NOT NULL REFERENCES articles(id),
        price REAL NOT NULL,
        updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
        UNIQUE (supplier_id, article_id)
    );

    -- Commandes clients
    CREATE TABLE IF NOT EXISTS sales_orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        number TEXT NOT NULL UNIQUE,
        customer_id INTEGER NOT NULL REFERENCES customers(id),
        status TEXT NOT NULL DEFAULT 'brouillon'
            CHECK (status IN ('brouillon','confirmee','en_production','prete','livree','facturee','annulee')),
        desired_date TEXT NOT NULL DEFAULT '',
        notes TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
        confirmed_at TEXT,
        closed_at TEXT
    );

    CREATE TABLE IF NOT EXISTS sales_order_lines (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_id INTEGER NOT NULL REFERENCES sales_orders(id),
        article_id INTEGER NOT NULL REFERENCES articles(id),
        qty REAL NOT NULL,
        delivered_qty REAL NOT NULL DEFAULT 0,
        price REAL NOT NULL,
        discount_pct REAL NOT NULL DEFAULT 0,
        vat_rate REAL NOT NULL,
        UNIQUE (order_id, article_id)
    );

    -- Commandes fournisseurs
    CREATE TABLE IF NOT EXISTS purchase_orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        number TEXT NOT NULL UNIQUE,
        supplier_id INTEGER NOT NULL REFERENCES suppliers(id),
        status TEXT NOT NULL DEFAULT 'brouillon'
            CHECK (status IN ('brouillon','envoyee','recue_partiel','recue','annulee')),
        notes TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
    );

    CREATE TABLE IF NOT EXISTS purchase_order_lines (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_id INTEGER NOT NULL REFERENCES purchase_orders(id),
        article_id INTEGER NOT NULL REFERENCES articles(id),
        qty REAL NOT NULL,
        received_qty REAL NOT NULL DEFAULT 0,
        price REAL NOT NULL,
        UNIQUE (order_id, article_id)
    );

    -- Ordres de fabrication
    CREATE TABLE IF NOT EXISTS work_orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        number TEXT NOT NULL UNIQUE,
        article_id INTEGER NOT NULL REFERENCES articles(id),
        bom_id INTEGER REFERENCES boms(id),
        qty_to_produce REAL NOT NULL,
        qty_produced REAL NOT NULL DEFAULT 0,
        qty_scrap REAL NOT NULL DEFAULT 0,
        sales_order_id INTEGER REFERENCES sales_orders(id),
        status TEXT NOT NULL DEFAULT 'planifie'
            CHECK (status IN ('planifie','lance','en_cours','termine','cloture')),
        notes TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
        launched_at TEXT,
        finished_at TEXT
    );

    -- Consommation réelle des OF
    CREATE TABLE IF NOT EXISTS work_order_consumptions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        wo_id INTEGER NOT NULL REFERENCES work_orders(id),
        article_id INTEGER NOT NULL REFERENCES articles(id),
        qty_theoretical REAL NOT NULL,
        qty_consumed REAL NOT NULL,
        UNIQUE (wo_id, article_id)
    );

    -- Gammes opératoires
    CREATE TABLE IF NOT EXISTS routings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        article_id INTEGER NOT NULL REFERENCES articles(id),
        step_no INTEGER NOT NULL,
        description TEXT NOT NULL,
        estimated_time TEXT NOT NULL DEFAULT '',
        tooling TEXT NOT NULL DEFAULT '',
        plan_ref TEXT NOT NULL DEFAULT '',
        UNIQUE (article_id, step_no)
    );

    -- Gestion des plans
    CREATE TABLE IF NOT EXISTS plans (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        article_id INTEGER NOT NULL REFERENCES articles(id),
        number TEXT NOT NULL,
        version TEXT NOT NULL DEFAULT 'A',
        file_path TEXT NOT NULL DEFAULT '',
        notes TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
        UNIQUE (article_id, number, version)
    );

    -- Bons de livraison
    CREATE TABLE IF NOT EXISTS delivery_notes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        number TEXT NOT NULL UNIQUE,
        sales_order_id INTEGER NOT NULL REFERENCES sales_orders(id),
        status TEXT NOT NULL DEFAULT 'preparation'
            CHECK (status IN ('preparation','livre')),
        carrier TEXT NOT NULL DEFAULT '',
        notes TEXT NOT NULL DEFAULT '',
        delivered_at TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
    );

    CREATE TABLE IF NOT EXISTS delivery_note_lines (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        note_id INTEGER NOT NULL REFERENCES delivery_notes(id),
        order_line_id INTEGER NOT NULL REFERENCES sales_order_lines(id),
        qty REAL NOT NULL
    );

    -- Factures
    CREATE TABLE IF NOT EXISTS invoices (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        number TEXT NOT NULL UNIQUE,
        customer_id INTEGER NOT NULL REFERENCES customers(id),
        sales_order_id INTEGER REFERENCES sales_orders(id),
        kind TEXT NOT NULL DEFAULT 'facture'
            CHECK (kind IN ('facture','avoir')),
        status TEXT NOT NULL DEFAULT 'en_attente'
            CHECK (status IN ('en_attente','partiellement_payee','payee','annulee')),
        total_ht REAL NOT NULL DEFAULT 0,
        total_vat REAL NOT NULL DEFAULT 0,
        total_ttc REAL NOT NULL DEFAULT 0,
        paid_amount REAL NOT NULL DEFAULT 0,
        payment_terms TEXT NOT NULL DEFAULT '30 jours',
        due_date TEXT NOT NULL DEFAULT '',
        notes TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
    );

    CREATE TABLE IF NOT EXISTS invoice_lines (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        invoice_id INTEGER NOT NULL REFERENCES invoices(id),
        description TEXT NOT NULL,
        qty REAL NOT NULL,
        price REAL NOT NULL,
        vat_rate REAL NOT NULL,
        discount_pct REAL NOT NULL DEFAULT 0
    );

    CREATE TABLE IF NOT EXISTS payments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        invoice_id INTEGER NOT NULL REFERENCES invoices(id),
        amount REAL NOT NULL,
        method TEXT NOT NULL DEFAULT 'virement',
        paid_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
    );
    """,
    3: """
    -- Bloc RH
    CREATE TABLE IF NOT EXISTS employees (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        matricule TEXT NOT NULL UNIQUE,
        first_name TEXT NOT NULL DEFAULT '',
        last_name TEXT NOT NULL,
        position TEXT NOT NULL DEFAULT '',
        department TEXT NOT NULL DEFAULT '',
        email TEXT NOT NULL DEFAULT '',
        phone TEXT NOT NULL DEFAULT '',
        hire_date TEXT NOT NULL DEFAULT '',
        contract_type TEXT NOT NULL DEFAULT 'cdi',
        base_salary REAL NOT NULL DEFAULT 0,
        active INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
    );
    CREATE TABLE IF NOT EXISTS leave_requests (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        employee_id INTEGER NOT NULL REFERENCES employees(id),
        leave_type TEXT NOT NULL DEFAULT 'conge_paye'
            CHECK (leave_type IN ('conge_paye','rtt','maladie','sans_solde')),
        start_date TEXT NOT NULL,
        end_date TEXT NOT NULL,
        days REAL NOT NULL,
        reason TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'soumise'
            CHECK (status IN ('soumise','approuvee','refusee','annulee')),
        decided_by TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
    );
    CREATE TABLE IF NOT EXISTS payslips (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        number TEXT NOT NULL UNIQUE,
        employee_id INTEGER NOT NULL REFERENCES employees(id),
        period TEXT NOT NULL,             -- AAAA-MM
        base_salary REAL NOT NULL,
        bonus REAL NOT NULL DEFAULT 0,
        overtime REAL NOT NULL DEFAULT 0,
        gross REAL NOT NULL,
        cot_employee REAL NOT NULL,
        cot_employer REAL NOT NULL,
        net REAL NOT NULL,
        status TEXT NOT NULL DEFAULT 'impaye'
            CHECK (status IN ('impaye','paye')),
        paid_at TEXT NOT NULL DEFAULT '',
        UNIQUE (employee_id, period)
    );
    """,
}


def migrate(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        "version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL"
        " DEFAULT (datetime('now','localtime')))"
    )
    applied = {
        row[0] for row in conn.execute("SELECT version FROM schema_migrations")
    }
    for version in sorted(MIGRATIONS):
        if version in applied:
            continue
        conn.executescript(MIGRATIONS[version])
        conn.execute("INSERT INTO schema_migrations (version) VALUES (?)", (version,))
        conn.commit()


def current_version(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
    return row[0] or 0
