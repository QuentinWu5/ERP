"""Bloc Achats : fournisseurs, commandes fournisseurs, suggestions d'achat
(besoin net), réceptions, prix par fournisseur."""
from __future__ import annotations

import csv
import io
from typing import Optional

from erp.db.connection import Database
from erp.services.inventory_service import InventoryService
from erp.services.settings_service import SettingsService

PO_STATUSES = ["brouillon", "envoyee", "recue_partiel", "recue", "annulee"]


class PurchasingError(Exception):
    pass


class PurchasingService:
    def __init__(self, db: Database, inventory: InventoryService | None = None,
                 settings: SettingsService | None = None):
        self.db = db
        self.inventory = inventory or InventoryService(db)
        self.settings = settings or SettingsService(db)

    # ------------------------------------------------------------------ fournisseurs
    def create_supplier(self, data: dict) -> int:
        code = (data.get("code") or "").strip()
        if not code:
            code = f"F{self.db.query_one('SELECT COUNT(*) c FROM suppliers')['c'] + 1:03d}"
        data = dict(data, code=code)
        try:
            sid = self.db.execute(
                "INSERT INTO suppliers (code, name, address, email, phone, "
                "payment_terms, notes) VALUES (?,?,?,?,?,?,?)",
                (code, data["name"], data.get("address", ""), data.get("email", ""),
                 data.get("phone", ""), data.get("payment_terms", "30 jours"),
                 data.get("notes", "")))
        except Exception as e:
            raise PurchasingError(f"Création fournisseur impossible : {e}")
        self.settings.audit("supplier.create", "suppliers", sid, code)
        return sid

    def update_supplier(self, sid: int, data: dict) -> None:
        cols = ["code", "name", "address", "email", "phone", "payment_terms",
                "notes", "active"]
        sets, params = [], []
        for c in cols:
            if c in data:
                sets.append(f"{c}=?")
                params.append(data[c])
        if sets:
            params.append(sid)
            self.db.execute(
                f"UPDATE suppliers SET {', '.join(sets)} WHERE id=?", params)
            self.settings.audit("supplier.update", "suppliers", sid)

    def get_supplier(self, sid: int) -> Optional[dict]:
        return self.db.query_one("SELECT * FROM suppliers WHERE id=?", (sid,))

    def list_suppliers(self, search: str = "") -> list[dict]:
        sql, params = "SELECT * FROM suppliers WHERE active=1", []
        if search:
            sql += " AND (code LIKE ? OR name LIKE ?)"
            params = [f"%{search}%"] * 2
        return self.db.query(sql + " ORDER BY name", params)

    def set_price(self, supplier_id: int, article_id: int, price: float) -> None:
        self.db.execute(
            "INSERT INTO supplier_prices (supplier_id, article_id, price) "
            "VALUES (?,?,?) ON CONFLICT(supplier_id, article_id) DO UPDATE "
            "SET price=excluded.price, "
            "updated_at=datetime('now','localtime')",
            (supplier_id, article_id, price))
        self.settings.audit("supplier_price.set", "supplier_prices",
                             f"{supplier_id}/{article_id}", str(price))

    def best_price(self, article_id: int) -> Optional[dict]:
        return self.db.query_one(
            "SELECT sp.*, s.name AS supplier_name, s.code AS supplier_code "
            "FROM supplier_prices sp JOIN suppliers s ON s.id=sp.supplier_id "
            "WHERE sp.article_id=? AND s.active=1 "
            "ORDER BY sp.price LIMIT 1", (article_id,))

    def prices_for(self, article_id: int) -> list[dict]:
        return self.db.query(
            "SELECT sp.*, s.name AS supplier_name FROM supplier_prices sp "
            "JOIN suppliers s ON s.id=sp.supplier_id WHERE sp.article_id=? "
            "ORDER BY sp.price", (article_id,))

    # ------------------------------------------------------------------ suggestions
    def suggestions(self, exclude_article_ids: Optional[set[int]] = None) -> list[dict]:
        """Suggestions d'achat : besoin net = besoin (OF lancés + commandes
        confirmées non livrées + stock mini) - stock disponible - en commande."""
        exclude = exclude_article_ids or set()
        need: dict[int, float] = {}
        # besoins des OF lancés (composants non encore consommés)
        for wo in self.db.query(
                "SELECT id, article_id, qty_to_produce, bom_id FROM work_orders "
                "WHERE status IN ('planifie','lance')"):
            try:
                flat = self._wo_flat(wo)
            except Exception:
                continue
            for aid, q in flat.items():
                need[aid] = need.get(aid, 0) + q
        # besoins des commandes clients confirmées non livrées (produits finis/marchandises)
        for line in self.db.query(
                "SELECT sol.article_id, sol.qty FROM sales_order_lines sol "
                "JOIN sales_orders so ON so.id=sol.order_id "
                "WHERE so.status IN ('confirmee','en_production','prete')"):
            need[line["article_id"]] = need.get(line["article_id"], 0) + line["qty"]
        result = []
        for aid, qty_need in need.items():
            if aid in exclude:
                continue
            art = self.inventory.get_article(aid)
            if not art or art["type"] == "service":
                continue
            avail = art["stock_qty"] - art["reserved_qty"]
            on_order = self.on_order_qty(aid)
            net = round(qty_need + art["min_stock"] - avail - on_order, 4)
            if net > 1e-9:
                best = self.best_price(aid)
                result.append({
                    "article_id": aid, "sku": art["sku"],
                    "designation": art["designation"], "unit": art["unit"],
                    "qty_needed": round(qty_need, 4), "qty_available": avail,
                    "qty_on_order": on_order, "qty_suggested": net,
                    "best_price": best["price"] if best else art["purchase_price"],
                    "supplier_id": best["supplier_id"] if best else None,
                    "supplier_name": best["supplier_name"] if best else "",
                })
        result.sort(key=lambda r: r["sku"])
        return result

    def _wo_flat(self, wo: dict) -> dict[int, float]:
        from erp.services.bom_service import BomService
        return BomService(self.db, self.inventory).explode_flat(
            wo["article_id"], wo["qty_to_produce"])

    def on_order_qty(self, article_id: int) -> float:
        row = self.db.query_one(
            "SELECT COALESCE(SUM(qty - received_qty),0) AS q "
            "FROM purchase_order_lines pol JOIN purchase_orders po "
            "ON po.id=pol.order_id WHERE pol.article_id=? AND "
            "po.status IN ('brouillon','envoyee','recue_partiel')",
            (article_id,))
        return row["q"]

    # ------------------------------------------------------------------ commandes
    def create_po(self, supplier_id: int, notes: str = "") -> dict:
        supplier = self.get_supplier(supplier_id)
        if not supplier:
            raise PurchasingError("Fournisseur introuvable")
        number = self.settings.next_number("CDF")
        pid = self.db.execute(
            "INSERT INTO purchase_orders (number, supplier_id, notes) "
            "VALUES (?,?,?)", (number, supplier_id, notes))
        self.settings.audit("po.create", "purchase_orders", pid, number)
        return {"id": pid, "number": number}

    def add_line(self, po_id: int, article_id: int, qty: float,
                 price: Optional[float] = None) -> int:
        art = self.inventory.get_article(article_id)
        if not art:
            raise PurchasingError("Article introuvable")
        if qty <= 0:
            raise PurchasingError("Quantité doit être > 0")
        if price is None:
            best = self.best_price(article_id)
            price = best["price"] if best else art["purchase_price"]
        return self.db.execute(
            "INSERT INTO purchase_order_lines (order_id, article_id, qty, price) "
            "VALUES (?,?,?,?) ON CONFLICT(order_id, article_id) DO UPDATE SET "
            "qty=excluded.qty, price=excluded.price",
            (po_id, article_id, qty, price))

    def get_po(self, po_id: int) -> Optional[dict]:
        return self.db.query_one(
            "SELECT po.*, s.name AS supplier_name, s.code AS supplier_code "
            "FROM purchase_orders po JOIN suppliers s ON s.id=po.supplier_id "
            "WHERE po.id=?", (po_id,))

    def po_lines(self, po_id: int) -> list[dict]:
        return self.db.query(
            "SELECT pol.*, a.sku, a.designation, a.unit FROM purchase_order_lines pol "
            "JOIN articles a ON a.id=pol.article_id WHERE pol.order_id=? "
            "ORDER BY pol.id", (po_id,))

    def list_pos(self, status: str = "") -> list[dict]:
        sql = ("SELECT po.*, s.name AS supplier_name, "
               "(SELECT COALESCE(SUM(pol.qty*pol.price),0) FROM purchase_order_lines pol "
               "WHERE pol.order_id=po.id) AS total_ht "
               "FROM purchase_orders po JOIN suppliers s ON s.id=po.supplier_id "
               "WHERE 1=1")
        params: list = []
        if status:
            sql += " AND po.status=?"
            params.append(status)
        return self.db.query(sql + " ORDER BY po.id DESC", params)

    def send_po(self, po_id: int) -> None:
        lines = self.po_lines(po_id)
        if not lines:
            raise PurchasingError("Commande fournisseur vide")
        self.db.execute(
            "UPDATE purchase_orders SET status='envoyee' WHERE id=? AND "
            "status='brouillon'", (po_id,))
        self.settings.audit("po.send", "purchase_orders", po_id)

    # ------------------------------------------------------------------ réception
    def receive(self, po_id: int, article_id: int, qty: float) -> None:
        """Réception partielle ou totale : entrée en stock + CMUP + prix fournisseur."""
        if qty <= 0:
            raise PurchasingError("Quantité doit être > 0")
        po = self.get_po(po_id)
        if po["status"] in ("brouillon", "annulee"):
            raise PurchasingError("Commande non envoyée")
        line = self.db.query_one(
            "SELECT * FROM purchase_order_lines WHERE order_id=? AND article_id=?",
            (po_id, article_id))
        if not line:
            raise PurchasingError("Article absent de la commande")
        remaining = line["qty"] - line["received_qty"]
        if qty > remaining + 1e-9:
            raise PurchasingError(
                f"Quantité reçue ({qty}) supérieure au restant dû ({remaining})")
        with self.db.transaction():
            self.db.execute(
                "UPDATE purchase_order_lines SET received_qty=received_qty+? "
                "WHERE id=?", (qty, line["id"]))
            self.inventory.entry(
                article_id, qty, line["price"],
                reason=f"Réception {po['number']}", source_doc=po["number"])
            self.set_price(po["supplier_id"], article_id, line["price"])
            all_received = self.db.query_one(
                "SELECT COUNT(*) AS c FROM purchase_order_lines "
                "WHERE order_id=? AND qty > received_qty + 1e-9", (po_id,))["c"] == 0
            new_status = "recue" if all_received else "recue_partiel"
            self.db.execute(
                "UPDATE purchase_orders SET status=? WHERE id=?",
                (new_status, po_id))
            self.settings.audit("po.receive", "purchase_orders", po_id,
                                f"article {article_id} x{qty}")

    def create_po_from_suggestions(self, supplier_id: int,
                                   article_ids: list[int]) -> dict:
        """Crée une commande fournisseur depuis les suggestions (articles fournis)."""
        sugg = {s["article_id"]: s for s in self.suggestions()}
        po = self.create_po(supplier_id, notes="Générée depuis suggestions d'achat")
        for aid in article_ids:
            if aid in sugg:
                self.add_line(po["id"], aid, sugg[aid]["qty_suggested"],
                              sugg[aid]["best_price"])
        self.settings.audit("po.from_suggestions", "purchase_orders", po["id"])
        return po

    # ------------------------------------------------------------------ import CSV
    def import_suppliers_csv(self, csv_text: str) -> dict:
        rows = list(csv.DictReader(io.StringIO(csv_text), delimiter=";"))
        if not rows:
            raise PurchasingError("Fichier CSV vide ou en-têtes manquantes")
        report = {"ok": 0, "errors": []}
        for i, row in enumerate(rows, start=2):
            try:
                if self.db.query_one("SELECT id FROM suppliers WHERE code=?",
                                     ((row.get("code") or "").strip(),)):
                    raise ValueError(f"code {row['code']} déjà existant")
                self.create_supplier({
                    "code": row.get("code", ""), "name": row.get("nom", ""),
                    "address": row.get("adresse", ""), "email": row.get("email", ""),
                    "phone": row.get("telephone", ""),
                    "payment_terms": row.get("conditions_paiement", "30 jours"),
                })
                report["ok"] += 1
            except Exception as e:
                report["errors"].append(f"Ligne {i} : {e}")
        return report
