"""Bloc Magasin/Logistique : préparation de commande (picking list),
bons de livraison, livraisons partielles."""
from __future__ import annotations

from typing import Optional

from erp.db.connection import Database
from erp.services.inventory_service import InventoryService
from erp.services.sales_service import SalesService
from erp.services.settings_service import SettingsService


class WarehouseError(Exception):
    pass


class WarehouseService:
    def __init__(self, db: Database, inventory: InventoryService | None = None,
                 sales: SalesService | None = None,
                 settings: SettingsService | None = None):
        self.db = db
        self.inventory = inventory or InventoryService(db)
        self.sales = sales or SalesService(db, self.inventory)
        self.settings = settings or SettingsService(db)

    # ------------------------------------------------------------------ picking
    def picking_list(self, order_id: int) -> list[dict]:
        """Liste de préparation : quantités restant à livrer par ligne,
        avec les emplacements de stock des articles."""
        lines = self.sales.order_lines(order_id)
        result = []
        for line in lines:
            remaining = line["qty"] - line["delivered_qty"]
            if remaining <= 1e-9 or line["type"] == "service":
                continue
            locs = self.inventory.locations(line["article_id"])
            result.append({
                "line_id": line["id"], "article_id": line["article_id"],
                "sku": line["sku"], "designation": line["designation"],
                "qty_to_pick": remaining,
                "locations": ", ".join(
                    f"{l['store']}/{l['shelf']} ({l['qty']:g})" for l in locs) or "—",
            })
        return result

    # ------------------------------------------------------------------ BL
    def create_delivery_note(self, order_id: int, carrier: str = "",
                             notes: str = "") -> dict:
        order = self.sales.get_order(order_id)
        if not order:
            raise WarehouseError("Commande introuvable")
        if order["status"] not in ("confirmee", "en_production", "prete"):
            raise WarehouseError(
                f"Commande {order['number']} non préparable (statut "
                f"{order['status']})")
        picking = self.picking_list(order_id)
        if not picking:
            raise WarehouseError("Rien à livrer : commandes complètes")
        number = self.settings.next_number("BL")
        nid = self.db.execute(
            "INSERT INTO delivery_notes (number, sales_order_id, carrier, notes) "
            "VALUES (?,?,?,?)", (number, order_id, carrier, notes))
        for p in picking:
            self.db.execute(
                "INSERT INTO delivery_note_lines (note_id, order_line_id, qty) "
                "VALUES (?,?,?)", (nid, p["line_id"], p["qty_to_pick"]))
        self.settings.audit("bl.create", "delivery_notes", nid, number)
        return {"id": nid, "number": number}

    def get_note(self, note_id: int) -> Optional[dict]:
        return self.db.query_one(
            "SELECT dn.*, so.number AS order_number, c.name AS customer_name, "
            "c.address AS customer_address "
            "FROM delivery_notes dn JOIN sales_orders so ON so.id=dn.sales_order_id "
            "JOIN customers c ON c.id=so.customer_id WHERE dn.id=?", (note_id,))

    def note_lines(self, note_id: int) -> list[dict]:
        return self.db.query(
            "SELECT dnl.*, a.sku, a.designation, a.unit, sol.price "
            "FROM delivery_note_lines dnl "
            "JOIN sales_order_lines sol ON sol.id=dnl.order_line_id "
            "JOIN articles a ON a.id=sol.article_id WHERE dnl.note_id=? "
            "ORDER BY dnl.id", (note_id,))

    def list_notes(self, order_id: Optional[int] = None) -> list[dict]:
        sql = ("SELECT dn.*, so.number AS order_number, c.name AS customer_name "
               "FROM delivery_notes dn JOIN sales_orders so ON so.id=dn.sales_order_id "
               "JOIN customers c ON c.id=so.customer_id WHERE 1=1")
        params: list = []
        if order_id:
            sql += " AND dn.sales_order_id=?"
            params.append(order_id)
        return self.db.query(sql + " ORDER BY dn.id DESC", params)

    def deliver(self, note_id: int) -> None:
        """Marque le BL livré : sorties de stock, libération des réservations,
        mise à jour des lignes de commande et du statut de la commande."""
        note = self.get_note(note_id)
        if not note:
            raise WarehouseError("BL introuvable")
        if note["status"] == "livre":
            raise WarehouseError("BL déjà livré")
        lines = self.note_lines(note_id)
        with self.db.transaction():
            for line in lines:
                art_id = self.db.query_one(
                    "SELECT article_id FROM sales_order_lines WHERE id=?",
                    (line["order_line_id"],))["article_id"]
                self.inventory.exit_(
                    art_id, line["qty"],
                    reason=f"Livraison BL {note['number']}",
                    source_doc=note["number"])
                self.inventory.unreserve(art_id, line["qty"])
                self.db.execute(
                    "UPDATE sales_order_lines SET delivered_qty=delivered_qty+? "
                    "WHERE id=?", (line["qty"], line["order_line_id"]))
            self.db.execute(
                "UPDATE delivery_notes SET status='livre', "
                "delivered_at=datetime('now','localtime') WHERE id=?", (note_id,))
            order_id = note["sales_order_id"]
            # les lignes service ne passent pas par le picking : soldées à la livraison
            self.db.execute(
                "UPDATE sales_order_lines SET delivered_qty=qty "
                "WHERE order_id=? AND article_id IN "
                "(SELECT id FROM articles WHERE type='service')", (order_id,))
            fully = self.db.query_one(
                "SELECT COUNT(*) AS c FROM sales_order_lines "
                "WHERE order_id=? AND qty > delivered_qty + 1e-9",
                (order_id,))["c"] == 0
            new_status = "livree" if fully else "prete"
            self.db.execute(
                "UPDATE sales_orders SET status=? WHERE id=? AND "
                "status NOT IN ('facturee','annulee')", (new_status, order_id))
            self.settings.audit("bl.deliver", "delivery_notes", note_id,
                                note["number"])

    def bl_html(self, note_id: int) -> str:
        note = self.get_note(note_id)
        rows = ""
        for line in self.note_lines(note_id):
            rows += (f"<tr><td>{line['sku']}</td><td>{line['designation']}</td>"
                     f"<td style='text-align:right'>{line['qty']:g} {line['unit']}</td></tr>")
        return f"""<html><head><meta charset="utf-8"><title>BL {note['number']}</title>
<style>body{{font-family:Arial;margin:30px}} table{{border-collapse:collapse;width:100%}}
td,th{{border:1px solid #999;padding:6px}}</style></head><body>
<h2>Bon de livraison {note['number']}</h2>
<p>Commande : {note['order_number']}<br>
Client : {note['customer_name']}<br>
Adresse : {note['customer_address']}<br>
Transporteur : {note['carrier'] or '—'}</p>
<table><tr><th>Réf.</th><th>Désignation</th><th>Qté</th></tr>{rows}</table>
</body></html>"""
