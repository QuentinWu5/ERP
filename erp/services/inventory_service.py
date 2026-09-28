"""Bloc Inventaire : articles, mouvements, CMUP, emplacements, alertes,
inventaire physique, reprise de stock initial, import/export CSV."""
from __future__ import annotations

import csv
import io
from typing import Optional

from erp.db.connection import Database
from erp.services.settings_service import SettingsService

ARTICLE_TYPES = ["produit_fini", "composant", "consommable", "marchandise", "service"]


class StockError(Exception):
    pass


class InventoryService:
    def __init__(self, db: Database, settings: SettingsService | None = None):
        self.db = db
        self.settings = settings or SettingsService(db)

    # ------------------------------------------------------------------ articles
    def create_article(self, data: dict) -> int:
        cols = ["sku", "designation", "type", "unit", "purchase_price",
                "sale_price", "vat_rate", "min_stock", "track_lots"]
        values = {c: data.get(c, "") for c in cols}
        values["type"] = values["type"] or "composant"
        values["unit"] = values["unit"] or "pce"
        values["vat_rate"] = values["vat_rate"] or float(self.settings.get("default_vat_rate"))
        for c in ["purchase_price", "sale_price", "min_stock"]:
            values[c] = float(values[c] or 0)
        values["track_lots"] = 1 if values["track_lots"] else 0
        placeholders = ", ".join(["?"] * len(cols))
        try:
            aid = self.db.execute(
                f"INSERT INTO articles ({', '.join(cols)}) VALUES ({placeholders})",
                [values[c] for c in cols],
            )
        except Exception as e:
            raise StockError(f"Création article impossible : {e}")
        self.settings.audit("article.create", "articles", aid, values["sku"])
        return aid

    def update_article(self, aid: int, data: dict) -> None:
        cols = ["sku", "designation", "type", "unit", "purchase_price",
                "sale_price", "vat_rate", "min_stock", "track_lots", "active"]
        sets, params = [], []
        for c in cols:
            if c in data:
                sets.append(f"{c}=?")
                params.append(data[c])
        if sets:
            params.append(aid)
            self.db.execute(f"UPDATE articles SET {', '.join(sets)}, "
                             "updated_at=datetime('now','localtime') WHERE id=?", params)
            self.settings.audit("article.update", "articles", aid)

    def get_article(self, aid: int) -> Optional[dict]:
        return self.db.query_one("SELECT * FROM articles WHERE id=?", (aid,))

    def get_by_sku(self, sku: str) -> Optional[dict]:
        return self.db.query_one("SELECT * FROM articles WHERE sku=?", (sku,))

    def list_articles(self, search: str = "", type_filter: str = "",
                      active_only: bool = False) -> list[dict]:
        sql = ("SELECT * FROM articles WHERE 1=1")
        params: list = []
        if search:
            sql += " AND (sku LIKE ? OR designation LIKE ?)"
            params += [f"%{search}%"] * 2
        if type_filter:
            sql += " AND type=?"
            params.append(type_filter)
        if active_only:
            sql += " AND active=1"
        sql += " ORDER BY sku"
        return self.db.query(sql, params)

    def stock_state(self) -> list[dict]:
        return self.db.query(
            "SELECT a.*, (a.stock_qty - a.reserved_qty) AS available_qty, "
            "(a.stock_qty * a.cmup) AS stock_value "
            "FROM articles a WHERE a.type != 'service' ORDER BY a.sku"
        )

    def low_stock(self) -> list[dict]:
        return self.db.query(
            "SELECT a.*, (a.stock_qty - a.reserved_qty) AS available_qty "
            "FROM articles a WHERE a.active=1 AND a.type != 'service' "
            "AND (a.stock_qty - a.reserved_qty) <= a.min_stock ORDER BY a.sku"
        )

    # ------------------------------------------------------------------ stock
    def available_qty(self, aid: int) -> float:
        a = self.get_article(aid)
        return (a["stock_qty"] - a["reserved_qty"]) if a else 0.0

    def _cmup(self, aid: int, qty_in: float, unit_cost: float) -> float:
        a = self.get_article(aid)
        old_qty, old_cmup = a["stock_qty"], a["cmup"]
        if old_qty + qty_in <= 0:
            return unit_cost
        return round((old_qty * old_cmup + qty_in * unit_cost) / (old_qty + qty_in), 4)

    def _apply_move(self, m: dict) -> None:
        """Applique un mouvement dans une transaction (atomique)."""
        with self.db.transaction():
            a = self.get_article(m["article_id"])
            if not a:
                raise StockError(f"Article {m['article_id']} introuvable")
            qty = float(m["qty"])
            new_qty = a["stock_qty"] + qty
            if new_qty < -1e-9:
                raise StockError(
                    f"Stock négatif interdit pour {a['sku']} "
                    f"(dispo {a['stock_qty']}, demandé {-qty})"
                )
            cmup_after = a["cmup"]
            if qty > 0 and m.get("unit_cost"):
                cmup_after = self._cmup(a["id"], qty, float(m["unit_cost"]))
            self.db.execute(
                "INSERT INTO stock_moves (article_id, move_type, qty, unit_cost, "
                "cmup_after, reason, source_doc, lot, location_from, location_to) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (m["article_id"], m["move_type"], qty, m.get("unit_cost", 0),
                 cmup_after, m.get("reason", ""), m.get("source_doc", ""),
                 m.get("lot", ""), m.get("location_from", ""),
                 m.get("location_to", "")),
            )
            self.db.execute(
                "UPDATE articles SET stock_qty=?, cmup=? WHERE id=?",
                (new_qty, cmup_after, a["id"]),
            )
            self._move_location(a["id"], qty,
                                m.get("location_to", "") or m.get("location_from", ""))

    def _move_location(self, aid: int, qty: float, loc: str) -> None:
        if not loc:
            return
        store, _, shelf = loc.partition("/")
        row = self.db.query_one(
            "SELECT id, qty FROM locations WHERE article_id=? AND store=? AND shelf=?",
            (aid, store or "MAG", shelf),
        )
        if row:
            self.db.execute("UPDATE locations SET qty=qty+? WHERE id=?", (qty, row["id"]))
        elif qty > 0:
            self.db.execute(
                "INSERT INTO locations (article_id, store, shelf, qty) VALUES (?,?,?,?)",
                (aid, store or "MAG", shelf, qty),
            )

    def entry(self, aid: int, qty: float, unit_cost: float = 0.0, **kw) -> None:
        self._apply_move({"article_id": aid, "move_type": "entree", "qty": qty,
                          "unit_cost": unit_cost, **kw})

    def exit_(self, aid: int, qty: float, **kw) -> None:
        self._apply_move({"article_id": aid, "move_type": "sortie", "qty": -qty, **kw})

    def adjust(self, aid: int, new_qty: float, reason: str = "") -> None:
        a = self.get_article(aid)
        delta = new_qty - a["stock_qty"]
        self._apply_move({"article_id": aid, "move_type": "ajustement",
                          "qty": delta, "reason": reason})

    def transfer(self, aid: int, qty: float, loc_from: str, loc_to: str) -> None:
        self._apply_move({"article_id": aid, "move_type": "transfert", "qty": 0,
                          "location_from": loc_from, "location_to": loc_to})
        with self.db.transaction():
            self._move_location(aid, -qty, loc_from)
            self._move_location(aid, qty, loc_to)

    # Réservations (utilisées par Ventes/Fabrication en P2/P3)
    def reserve(self, aid: int, qty: float) -> None:
        with self.db.transaction():
            a = self.get_article(aid)
            if a["stock_qty"] - a["reserved_qty"] < qty - 1e-9:
                raise StockError(f"Stock disponible insuffisant pour {a['sku']}")
            self.db.execute("UPDATE articles SET reserved_qty=reserved_qty+? WHERE id=?",
                            (qty, aid))
            self.settings.audit("stock.reserve", "articles", aid,
                                f"{a['sku']} +{qty}")

    def unreserve(self, aid: int, qty: float) -> None:
        with self.db.transaction():
            self.db.execute(
                "UPDATE articles SET reserved_qty=MAX(0, reserved_qty-?) WHERE id=?",
                (qty, aid))

    # ------------------------------------------------------------------ historique
    def moves(self, aid: int | None = None, move_type: str = "",
              source_doc: str = "", limit: int = 500) -> list[dict]:
        sql = ("SELECT sm.*, a.sku, a.designation FROM stock_moves sm "
               "JOIN articles a ON a.id = sm.article_id WHERE 1=1")
        params: list = []
        if aid:
            sql += " AND sm.article_id=?"
            params.append(aid)
        if move_type:
            sql += " AND sm.move_type=?"
            params.append(move_type)
        if source_doc:
            sql += " AND sm.source_doc LIKE ?"
            params.append(f"%{source_doc}%")
        sql += " ORDER BY sm.id DESC LIMIT ?"
        params.append(limit)
        return self.db.query(sql, params)

    def locations(self, aid: int) -> list[dict]:
        return self.db.query(
            "SELECT * FROM locations WHERE article_id=? ORDER BY store, shelf", (aid,))

    # ------------------------------------------------------------------ inventaire physique
    def open_count(self) -> int:
        ref = self.settings.next_number("INV", with_year=False)
        return self.db.execute(
            "INSERT INTO stock_counts (reference) VALUES (?)", (ref,))

    def set_counted(self, count_id: int, aid: int, counted_qty: float) -> None:
        a = self.get_article(aid)
        self.db.execute(
            "INSERT INTO stock_count_lines (count_id, article_id, counted_qty, "
            "system_qty) VALUES (?,?,?,?) "
            "ON CONFLICT(count_id, article_id) "
            "DO UPDATE SET counted_qty=excluded.counted_qty",
            (count_id, aid, counted_qty, a["stock_qty"]),
        )

    def count_lines(self, count_id: int) -> list[dict]:
        return self.db.query(
            "SELECT scl.*, a.sku, a.designation, a.unit FROM stock_count_lines scl "
            "JOIN articles a ON a.id=scl.article_id WHERE count_id=? "
            "AND counted_qty != system_qty ORDER BY a.sku", (count_id,))

    def validate_count(self, count_id: int) -> None:
        for line in self.count_lines(count_id):
            self.adjust(line["article_id"], line["counted_qty"],
                        reason="Inventaire physique")
        self.db.execute(
            "UPDATE stock_counts SET status='valide', "
            "validated_at=datetime('now','localtime') WHERE id=?", (count_id,))
        self.settings.audit("stock_count.validate", "stock_counts", count_id)

    def counts(self) -> list[dict]:
        return self.db.query("SELECT * FROM stock_counts ORDER BY id DESC")

    # ------------------------------------------------------------------ import / export
    def import_articles_csv(self, csv_text: str) -> dict:
        """Import articles + stock initial. Colonnes attendues :
        reference;designation;type;unite;prix_achat;prix_vente;tva;
        stock_initial;emplacement;cout_init;stock_mini
        Le stock initial génère un mouvement 'reprise_initiale' tracé."""
        required = ["reference", "designation"]
        rows = list(csv.DictReader(io.StringIO(csv_text), delimiter=";"))
        if not rows:
            raise StockError("Fichier CSV vide ou en-têtes manquantes")
        report = {"ok": 0, "errors": []}
        for i, row in enumerate(rows, start=2):
            try:
                sku = (row.get("reference") or "").strip()
                if not sku or not (row.get("designation") or "").strip():
                    raise ValueError("référence et désignation obligatoires")
                if self.get_by_sku(sku):
                    raise ValueError(f"SKU {sku} déjà existant")
                aid = self.create_article({
                    "sku": sku,
                    "designation": row["designation"],
                    "type": row.get("type", "").strip() or "composant",
                    "unit": row.get("unite", "").strip() or "pce",
                    "purchase_price": row.get("prix_achat", 0) or 0,
                    "sale_price": row.get("prix_vente", 0) or 0,
                    "vat_rate": row.get("tva", 0) or 0,
                    "min_stock": row.get("stock_mini", 0) or 0,
                })
                qty = float(row.get("stock_initial") or 0)
                cost = row.get("cout_init") or row.get("prix_achat") or 0
                if qty:
                    self.entry(aid, qty, float(cost),
                               move_type="reprise_initiale",
                               reason="Reprise initiale (import CSV)",
                               location_to=row.get("emplacement", ""),
                               source_doc="IMPORT")
                report["ok"] += 1
            except Exception as e:
                report["errors"].append(f"Ligne {i} : {e}")
        self.settings.audit("inventory.import", "articles", "", f"{report['ok']} ok")
        return report

    def export_stock_csv(self) -> str:
        out = io.StringIO()
        w = csv.writer(out, delimiter=";")
        w.writerow(["reference", "designation", "type", "stock_physique",
                    "stock_reserve", "stock_disponible", "cmup", "valeur",
                    "stock_mini"])
        for a in self.stock_state():
            w.writerow([a["sku"], a["designation"], a["type"], a["stock_qty"],
                        a["reserved_qty"], a["available_qty"], a["cmup"],
                        round(a["stock_value"], 2), a["min_stock"]])
        return out.getvalue()
