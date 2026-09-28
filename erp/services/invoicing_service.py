"""Bloc Facturation : factures depuis commandes livrées (partielles ou
totales), avoirs, suivi des paiements, documents HTML."""
from __future__ import annotations

from typing import Optional

from erp.db.connection import Database
from erp.services.sales_service import SalesService
from erp.services.settings_service import SettingsService


class InvoicingError(Exception):
    pass


class InvoicingService:
    def __init__(self, db: Database, sales: SalesService | None = None,
                 settings: SettingsService | None = None):
        self.db = db
        self.sales = sales or SalesService(db)
        self.settings = settings or SettingsService(db)

    # ------------------------------------------------------------------ création
    def create_invoice_from_order(self, order_id: int,
                                  notes: str = "") -> dict:
        """Facture les quantités livrées non encore facturées (livraison
        partielle supportée). Les lignes de service sont facturées en totalité."""
        order = self.sales.get_order(order_id)
        if not order:
            raise InvoicingError("Commande introuvable")
        if order["status"] not in ("prete", "livree"):
            raise InvoicingError(
                "Seule une commande prête ou livrée peut être facturée")
        lines = self.sales.order_lines(order_id)
        billable = []
        for line in lines:
            qty = line["delivered_qty"] if line["type"] != "service" else line["qty"]
            invoiced = self.db.query_one(
                "SELECT COALESCE(SUM(il.qty),0) AS q FROM invoice_lines il "
                "JOIN invoices i ON i.id=il.invoice_id "
                "WHERE i.sales_order_id=? AND i.kind='facture' AND "
                "il.description LIKE ?",
                (order_id, f"{line['sku']}|%"))["q"]
            if qty - invoiced > 1e-9:
                billable.append((line, qty - invoiced))
        if not billable:
            raise InvoicingError("Rien à facturer : tout est déjà facturé")
        number = self.settings.next_number("FAC")
        customer = self.sales.get_customer(order["customer_id"])
        with self.db.transaction():
            inv_id = self.db.execute(
                "INSERT INTO invoices (number, customer_id, sales_order_id, "
                "payment_terms, due_date, notes) VALUES (?,?,?,?,?,?)",
                (number, order["customer_id"], order_id,
                 customer["payment_terms"], SalesService.due_date(customer["payment_terms"]),
                 notes))
            for line, qty in billable:
                self.db.execute(
                    "INSERT INTO invoice_lines (invoice_id, description, qty, "
                    "price, vat_rate, discount_pct) VALUES (?,?,?,?,?,?)",
                    (inv_id, f"{line['sku']}|{line['designation']}", qty,
                     line["price"], line["vat_rate"], line["discount_pct"]))
            self._recalc_totals(inv_id)
            if order["status"] == "livree":
                self.db.execute(
                    "UPDATE sales_orders SET status='facturee', "
                    "closed_at=datetime('now','localtime') WHERE id=?", (order_id,))
            self.settings.audit("invoice.create", "invoices", inv_id, number)
        return {"id": inv_id, "number": number}

    def create_credit_note(self, invoice_id: int, amount: float,
                           reason: str = "") -> dict:
        """Avoir simple sur une facture existante."""
        inv = self.get_invoice(invoice_id)
        if not inv:
            raise InvoicingError("Facture introuvable")
        if amount <= 0 or amount > inv["total_ttc"] + 1e-9:
            raise InvoicingError("Montant d'avoir invalide (max total TTC)")
        number = self.settings.next_number("AVC")
        av_id = self.db.execute(
            "INSERT INTO invoices (number, customer_id, sales_order_id, kind, "
            "payment_terms, due_date, notes) VALUES (?,?,?,?,?,?,?)",
            (number, inv["customer_id"], inv["sales_order_id"], "avoir",
             "immédiat", "", reason))
        self.db.execute(
            "INSERT INTO invoice_lines (invoice_id, description, qty, price, "
            "vat_rate, discount_pct) VALUES (?,?,?,?,?,?)",
            (av_id, f"Avoir sur {inv['number']}|{reason}", 1, amount,
             0, 0))
        self._recalc_totals(av_id)
        self.settings.audit("invoice.credit_note", "invoices", av_id, number)
        return {"id": av_id, "number": number}

    def _recalc_totals(self, invoice_id: int) -> None:
        row = self.db.query_one(
            "SELECT COALESCE(SUM(qty*price*(1-discount_pct/100)),0) AS ht, "
            "COALESCE(SUM(qty*price*(1-discount_pct/100)*vat_rate/100),0) AS vat "
            "FROM invoice_lines WHERE invoice_id=?", (invoice_id,))
        sign = -1 if (self.get_invoice(invoice_id) or {}).get("kind") == "avoir" else 1
        self.db.execute(
            "UPDATE invoices SET total_ht=?, total_vat=?, total_ttc=? WHERE id=?",
            (sign * round(row["ht"], 2), sign * round(row["vat"], 2),
             sign * round(row["ht"] + row["vat"], 2), invoice_id))

    # ------------------------------------------------------------------ paiements
    def register_payment(self, invoice_id: int, amount: float,
                         method: str = "virement") -> None:
        inv = self.get_invoice(invoice_id)
        if not inv:
            raise InvoicingError("Facture introuvable")
        remaining = inv["total_ttc"] - inv["paid_amount"]
        if amount <= 0:
            raise InvoicingError("Montant invalide")
        if amount > remaining + 1e-9:
            raise InvoicingError(
                f"Montant supérieur au restant dû ({remaining:.2f})")
        with self.db.transaction():
            self.db.execute(
                "INSERT INTO payments (invoice_id, amount, method) VALUES (?,?,?)",
                (invoice_id, amount, method))
            paid = inv["paid_amount"] + amount
            status = "payee" if paid >= inv["total_ttc"] - 1e-9 \
                else "partiellement_payee"
            self.db.execute(
                "UPDATE invoices SET paid_amount=?, status=? WHERE id=?",
                (paid, status, invoice_id))
            self.settings.audit("invoice.payment", "invoices", invoice_id,
                                f"{amount:.2f} {method}")

    def invoice_status(self, invoice_id: int) -> str:
        inv = self.get_invoice(invoice_id)
        if not inv:
            return ""
        if inv["status"] == "payee":
            return "Payée"
        if inv["due_date"] and inv["due_date"] < today_str():
            return "En retard"
        if inv["status"] == "partiellement_payee":
            return "Partiellement payée"
        return "En attente"

    # ------------------------------------------------------------------ lectures
    def get_invoice(self, invoice_id: int) -> Optional[dict]:
        return self.db.query_one(
            "SELECT i.*, c.name AS customer_name, c.code AS customer_code "
            "FROM invoices i JOIN customers c ON c.id=i.customer_id "
            "WHERE i.id=?", (invoice_id,))

    def invoice_lines(self, invoice_id: int) -> list[dict]:
        return self.db.query(
            "SELECT * FROM invoice_lines WHERE invoice_id=? ORDER BY id",
            (invoice_id,))

    def payments(self, invoice_id: int) -> list[dict]:
        return self.db.query(
            "SELECT * FROM payments WHERE invoice_id=? ORDER BY id", (invoice_id,))

    def list_invoices(self, kind: str = "facture",
                      status: str = "") -> list[dict]:
        sql = ("SELECT i.*, c.name AS customer_name, "
               "(i.total_ttc - i.paid_amount) AS remaining "
               "FROM invoices i JOIN customers c ON c.id=i.customer_id WHERE i.kind=?")
        params: list = [kind]
        if status:
            sql += " AND i.status=?"
            params.append(status)
        return self.db.query(sql + " ORDER BY i.id DESC", params)

    def late_invoices(self) -> list[dict]:
        return [i for i in self.list_invoices()
                if self.invoice_status(i["id"]) == "En retard"]

    # ------------------------------------------------------------------ document
    def invoice_html(self, invoice_id: int) -> str:
        inv = self.get_invoice(invoice_id)
        s = self.settings.all()
        rows = ""
        for line in self.invoice_lines(invoice_id):
            sku, _, des = line["description"].partition("|")
            total = line["qty"] * line["price"] * (1 - line["discount_pct"] / 100)
            rows += (f"<tr><td>{sku}</td><td>{des}</td>"
                     f"<td style='text-align:right'>{line['qty']:g}</td>"
                     f"<td style='text-align:right'>{line['price']:.2f}</td>"
                     f"<td style='text-align:right'>{line['vat_rate']:g}%</td>"
                     f"<td style='text-align:right'>{total:.2f}</td></tr>")
        title = "Facture" if inv["kind"] == "facture" else "Avoir"
        return f"""<html><head><meta charset="utf-8"><title>{title} {inv['number']}</title>
<style>body{{font-family:Arial;margin:30px}} table{{border-collapse:collapse;width:100%}}
td,th{{border:1px solid #999;padding:6px}} .right{{text-align:right}}</style></head><body>
<h2>{s.get('company_name','')}</h2>
<p>{s.get('company_address','')} — SIRET {s.get('company_siret','')} — TVA {s.get('company_vat','')}</p>
<h1>{title} {inv['number']}</h1>
<p>Date : {inv['created_at']}<br>
Client : {inv['customer_code']} {inv['customer_name']}<br>
Conditions : {inv['payment_terms']} — Échéance : {inv['due_date'] or '—'}</p>
<table><tr><th>Réf.</th><th>Désignation</th><th>Qté</th><th>PU HT</th>
<th>TVA</th><th>Total HT</th></tr>{rows}
<tr><th colspan="5" class="right">Total HT</th><th class="right">{inv['total_ht']:.2f}</th></tr>
<tr><th colspan="5" class="right">TVA</th><th class="right">{inv['total_vat']:.2f}</th></tr>
<tr><th colspan="5" class="right">Total TTC</th><th class="right">{inv['total_ttc']:.2f}</th></tr>
<tr><th colspan="5" class="right">Déjà payé</th><th class="right">{inv['paid_amount']:.2f}</th></tr>
<tr><th colspan="5" class="right">Restant dû</th><th class="right">{inv['total_ttc']-inv['paid_amount']:.2f}</th></tr>
</table>
</body></html>"""


def today_str() -> str:
    from datetime import date
    return date.today().isoformat()
