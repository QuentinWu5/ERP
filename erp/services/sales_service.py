"""Bloc Ventes : clients, commandes clients, AR, réservation de stock, statuts."""
from __future__ import annotations

import csv
import io
from datetime import date, timedelta
from typing import Optional

from erp.db.connection import Database
from erp.services.inventory_service import InventoryService, StockError
from erp.services.settings_service import SettingsService

ORDER_STATUSES = ["brouillon", "confirmee", "en_production", "prete",
                  "livree", "facturee", "annulee"]


class SalesError(Exception):
    pass


class SalesService:
    def __init__(self, db: Database, inventory: InventoryService | None = None,
                 settings: SettingsService | None = None):
        self.db = db
        self.inventory = inventory or InventoryService(db)
        self.settings = settings or SettingsService(db)

    # ------------------------------------------------------------------ clients
    def create_customer(self, data: dict) -> int:
        code = (data.get("code") or "").strip()
        if not code:
            code = f"C{self.db.query_one('SELECT COUNT(*) c FROM customers')['c'] + 1:04d}"
        data = dict(data, code=code)
        try:
            cid = self.db.execute(
                "INSERT INTO customers (code, name, address, email, phone, "
                "payment_terms, vat_applicable, notes) VALUES (?,?,?,?,?,?,?,?)",
                (code, data["name"], data.get("address", ""), data.get("email", ""),
                 data.get("phone", ""), data.get("payment_terms", "30 jours"),
                 1 if data.get("vat_applicable", True) else 0,
                 data.get("notes", "")))
        except Exception as e:
            raise SalesError(f"Création client impossible : {e}")
        self.settings.audit("customer.create", "customers", cid, code)
        return cid

    def update_customer(self, cid: int, data: dict) -> None:
        cols = ["code", "name", "address", "email", "phone", "payment_terms",
                "vat_applicable", "notes", "active"]
        sets, params = [], []
        for c in cols:
            if c in data:
                sets.append(f"{c}=?")
                params.append(data[c])
        if sets:
            params.append(cid)
            self.db.execute(
                f"UPDATE customers SET {', '.join(sets)} WHERE id=?", params)
            self.settings.audit("customer.update", "customers", cid)

    def get_customer(self, cid: int) -> Optional[dict]:
        return self.db.query_one("SELECT * FROM customers WHERE id=?", (cid,))

    def list_customers(self, search: str = "") -> list[dict]:
        sql, params = "SELECT * FROM customers WHERE active=1", []
        if search:
            sql += " AND (code LIKE ? OR name LIKE ?)"
            params = [f"%{search}%"] * 2
        return self.db.query(sql + " ORDER BY name", params)

    # ------------------------------------------------------------------ commandes
    def create_order(self, customer_id: int, desired_date: str = "",
                     notes: str = "") -> dict:
        customer = self.get_customer(customer_id)
        if not customer:
            raise SalesError("Client introuvable")
        number = self.settings.next_number("CMD")
        oid = self.db.execute(
            "INSERT INTO sales_orders (number, customer_id, desired_date, notes) "
            "VALUES (?,?,?,?)", (number, customer_id, desired_date, notes))
        self.settings.audit("sales_order.create", "sales_orders", oid, number)
        return {"id": oid, "number": number}

    def add_line(self, order_id: int, article_id: int, qty: float,
                 price: Optional[float] = None, discount_pct: float = 0) -> int:
        art = self.inventory.get_article(article_id)
        if not art:
            raise SalesError("Article introuvable")
        if qty <= 0:
            raise SalesError("Quantité doit être > 0")
        if art["type"] == "composant":
            raise SalesError(
                f"Un composant ({art['sku']}) ne peut pas être vendu directement")
        if price is None:
            price = art["sale_price"]
        return self.db.execute(
            "INSERT INTO sales_order_lines (order_id, article_id, qty, price, "
            "discount_pct, vat_rate) VALUES (?,?,?,?,?,?) "
            "ON CONFLICT(order_id, article_id) DO UPDATE SET "
            "qty=excluded.qty, price=excluded.price, "
            "discount_pct=excluded.discount_pct",
            (order_id, article_id, qty, price, discount_pct, art["vat_rate"]))

    def remove_line(self, line_id: int) -> None:
        line = self.db.query_one(
            "SELECT * FROM sales_order_lines WHERE id=?", (line_id,))
        if not line:
            return
        order = self.get_order(line["order_id"])
        if order["status"] != "brouillon":
            raise SalesError("Commande déjà confirmée : ligne non modifiable")
        self.db.execute("DELETE FROM sales_order_lines WHERE id=?", (line_id,))

    def get_order(self, order_id: int) -> Optional[dict]:
        return self.db.query_one(
            "SELECT so.*, c.name AS customer_name, c.code AS customer_code "
            "FROM sales_orders so JOIN customers c ON c.id=so.customer_id "
            "WHERE so.id=?", (order_id,))

    def order_lines(self, order_id: int) -> list[dict]:
        return self.db.query(
            "SELECT sol.*, a.sku, a.designation, a.unit, a.type "
            "FROM sales_order_lines sol JOIN articles a ON a.id=sol.article_id "
            "WHERE sol.order_id=? ORDER BY sol.id", (order_id,))

    def list_orders(self, status: str = "", search: str = "") -> list[dict]:
        sql = ("SELECT so.*, c.name AS customer_name, c.code AS customer_code, "
               "(SELECT COALESCE(SUM(sol.qty*sol.price*(1-sol.discount_pct/100)"
               "*(1+sol.vat_rate/100)),0) FROM sales_order_lines sol "
               "WHERE sol.order_id=so.id) AS total_ttc "
               "FROM sales_orders so JOIN customers c ON c.id=so.customer_id "
               "WHERE 1=1")
        params: list = []
        if status:
            sql += " AND so.status=?"
            params.append(status)
        if search:
            sql += " AND (so.number LIKE ? OR c.name LIKE ?)"
            params += [f"%{search}%"] * 2
        return self.db.query(sql + " ORDER BY so.id DESC", params)

    def order_total(self, order_id: int) -> dict:
        row = self.db.query_one(
            "SELECT COALESCE(SUM(qty*price*(1-discount_pct/100)),0) AS ht, "
            "COALESCE(SUM(qty*price*(1-discount_pct/100)*vat_rate/100),0) AS vat "
            "FROM sales_order_lines WHERE order_id=?", (order_id,))
        ht, vat = round(row["ht"], 2), round(row["vat"], 2)
        return {"ht": ht, "vat": vat, "ttc": round(ht + vat, 2)}

    # ------------------------------------------------------------------ flux
    def confirm_order(self, order_id: int) -> str:
        """Confirme la commande, réserve le stock disponible, génère l'AR.
        Retourne le numéro d'AR. Lève SalesError si stock insuffisant pour
        les articles en stock (les produits à fabriquer ne bloquent pas :
        ils passeront par un OF)."""
        lines = self.order_lines(order_id)
        if not lines:
            raise SalesError("Commande vide : ajoutez des lignes")
        order = self.get_order(order_id)
        if order["status"] != "brouillon":
            raise SalesError(f"Commande déjà confirmée ({order['status']})")
        ar_number = self.settings.next_number("AR")
        try:
            with self.db.transaction():
                for line in lines:
                    art = self.inventory.get_article(line["article_id"])
                    if art["type"] == "service":
                        continue
                    avail = art["stock_qty"] - art["reserved_qty"]
                    need = line["qty"]
                    if need > avail + 1e-9:
                        if art["type"] == "produit_fini":
                            # à fabriquer : pas de réservation, l'OF s'en charge
                            continue
                        raise StockError(
                            f"Stock insuffisant pour {art['sku']} : "
                            f"besoin {need}, disponible {avail}")
                    self.inventory.reserve(art["id"], need)
                self.db.execute(
                    "UPDATE sales_orders SET status='confirmee', "
                    "confirmed_at=datetime('now','localtime') WHERE id=?",
                    (order_id,))
                self.db.execute(
                    "UPDATE settings SET value=value WHERE key=''", ())
                self.db.execute(
                    "INSERT INTO doc_counters (prefix, value) VALUES ('AR', ?) "
                    "ON CONFLICT(prefix) DO UPDATE SET value=excluded.value",
                    (int(ar_number.split("-")[-1]),))
                self.db.execute(
                    "INSERT INTO audit_log (action, entity, entity_id, details) "
                    "VALUES ('sales_order.confirm','sales_orders',?,?)",
                    (str(order_id), f"{order['number']} → {ar_number}"))
        except Exception:
            raise
        return ar_number

    def set_status(self, order_id: int, status: str) -> None:
        if status not in ORDER_STATUSES:
            raise SalesError(f"Statut inconnu : {status}")
        self.db.execute("UPDATE sales_orders SET status=? WHERE id=?",
                        (status, order_id))
        self.settings.audit("sales_order.status", "sales_orders", order_id, status)

    def cancel_order(self, order_id: int) -> None:
        order = self.get_order(order_id)
        if order["status"] in ("livree", "facturee"):
            raise SalesError("Commande déjà livrée/facturée : annulation impossible")
        with self.db.transaction():
            for line in self.order_lines(order_id):
                art = self.inventory.get_article(line["article_id"])
                if art and art["reserved_qty"] > 0:
                    self.inventory.unreserve(art["id"], line["qty"])
            self.set_status(order_id, "annulee")

    # ------------------------------------------------------------------ AR document
    def ar_html(self, order_id: int) -> str:
        order = self.get_order(order_id)
        customer = self.get_customer(order["customer_id"])
        s = self.settings.all()
        rows = ""
        for line in self.order_lines(order_id):
            total = line["qty"] * line["price"] * (1 - line["discount_pct"] / 100)
            rows += (f"<tr><td>{line['sku']}</td><td>{line['designation']}</td>"
                     f"<td style='text-align:right'>{line['qty']:g}</td>"
                     f"<td style='text-align:right'>{line['price']:.2f}</td>"
                     f"<td style='text-align:right'>{line['discount_pct']:g}%</td>"
                     f"<td style='text-align:right'>{total:.2f}</td></tr>")
        t = self.order_total(order_id)
        return f"""<html><head><meta charset="utf-8"><title>AR {order['number']}</title>
<style>body{{font-family:Arial;margin:30px}} table{{border-collapse:collapse;width:100%}}
td,th{{border:1px solid #999;padding:6px}} .right{{text-align:right}}</style></head><body>
<h2>{s.get('company_name','')}</h2>
<p>{s.get('company_address','')} — SIRET {s.get('company_siret','')}</p>
<h1>Accusé de réception de commande</h1>
<p><b>Commande :</b> {order['number']} — <b>Date :</b> {order['created_at']}<br>
<b>Client :</b> {customer['code']} {customer['name']}<br>
<b>Livraison souhaitée :</b> {order['desired_date'] or '—'}</p>
<table><tr><th>Réf.</th><th>Désignation</th><th>Qté</th><th>Prix</th>
<th>Remise</th><th>Total HT</th></tr>
{rows}
<tr><th colspan="5" class="right">Total HT</th><th class="right">{t['ht']:.2f}</th></tr>
<tr><th colspan="5" class="right">TVA</th><th class="right">{t['vat']:.2f}</th></tr>
<tr><th colspan="5" class="right">Total TTC</th><th class="right">{t['ttc']:.2f}</th></tr>
</table>
<p>Nous accusons réception de votre commande. Statut : {order['status']}.</p>
</body></html>"""

    # ------------------------------------------------------------------ import CSV
    def import_customers_csv(self, csv_text: str) -> dict:
        """Colonnes : code;nom;adresse;email;telephone;conditions_paiement"""
        rows = list(csv.DictReader(io.StringIO(csv_text), delimiter=";"))
        if not rows:
            raise SalesError("Fichier CSV vide ou en-têtes manquantes")
        report = {"ok": 0, "errors": []}
        for i, row in enumerate(rows, start=2):
            try:
                if self.db.query_one(
                        "SELECT id FROM customers WHERE code=?",
                        ((row.get("code") or "").strip(),)):
                    raise ValueError(f"code {row['code']} déjà existant")
                self.create_customer({
                    "code": row.get("code", ""), "name": row.get("nom", ""),
                    "address": row.get("adresse", ""), "email": row.get("email", ""),
                    "phone": row.get("telephone", ""),
                    "payment_terms": row.get("conditions_paiement", "30 jours"),
                })
                report["ok"] += 1
            except Exception as e:
                report["errors"].append(f"Ligne {i} : {e}")
        return report

    # ------------------------------------------------------------------ utilitaire date
    @staticmethod
    def due_date(payment_terms: str) -> str:
        days = 30
        for word in payment_terms.split():
            if word.isdigit():
                days = int(word)
                break
        return (date.today() + timedelta(days=days)).isoformat()
