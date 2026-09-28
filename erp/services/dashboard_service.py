"""Bloc Tableau de bord : vue d'ensemble (commandes, OF, retards, alertes)."""
from __future__ import annotations

from erp.db.connection import Database
from erp.services.bom_service import BomService
from erp.services.invoicing_service import InvoicingService
from erp.services.inventory_service import InventoryService
from erp.services.manufacturing_service import ManufacturingService
from erp.services.purchasing_service import PurchasingService
from erp.services.sales_service import SalesService
from erp.services.settings_service import SettingsService


class DashboardService:
    def __init__(self, db: Database, settings: SettingsService | None = None,
                 inventory: InventoryService | None = None,
                 sales: SalesService | None = None,
                 manufacturing: ManufacturingService | None = None,
                 purchasing: PurchasingService | None = None,
                 invoicing: InvoicingService | None = None,
                 bom: BomService | None = None):
        self.db = db
        self.settings = settings or SettingsService(db)
        self.inventory = inventory or InventoryService(db, self.settings)
        self.bom = bom or BomService(db, self.inventory, self.settings)
        self.sales = sales or SalesService(db, self.inventory, self.settings)
        self.manufacturing = manufacturing or ManufacturingService(
            db, self.inventory, self.bom, self.settings)
        self.purchasing = purchasing or PurchasingService(db, self.inventory,
                                                          self.settings)
        self.invoicing = invoicing or InvoicingService(db, self.sales, self.settings)

    def overview(self) -> dict:
        open_orders = self.sales.list_orders()
        wos = self.manufacturing.list_wos()
        invoices = self.invoicing.list_invoices()
        return {
            "orders_in_progress": [o for o in open_orders
                                   if o["status"] in ("confirmee", "en_production",
                                                      "prete")],
            "orders_draft": [o for o in open_orders if o["status"] == "brouillon"],
            "wos_open": [w for w in wos
                         if w["status"] in ("planifie", "lance", "en_cours")],
            "late_invoices": self.invoicing.late_invoices(),
            "unpaid_invoices": [i for i in invoices
                                if i["status"] in ("en_attente",
                                                   "partiellement_payee")],
            "stock_alerts": self.inventory.low_stock(),
            "pending_pos": self.purchasing.list_pos(
            ) if hasattr(self.purchasing, "list_pos") else [],
            "stock_value": round(sum(
                a["stock_qty"] * a["cmup"]
                for a in self.inventory.stock_state()), 2),
        }

    def counters(self) -> dict[str, int]:
        o = self.overview()
        return {
            "Commandes en cours": len(o["orders_in_progress"]),
            "OF ouverts": len(o["wos_open"]),
            "Factures en retard": len(o["late_invoices"]),
            "Factures impayées": len(o["unpaid_invoices"]),
            "Alertes de stock": len(o["stock_alerts"]),
            "Commandes fournisseurs en attente": len(
                [p for p in o["pending_pos"]
                 if p["status"] in ("brouillon", "envoyee", "recue_partiel")]),
            "Valeur du stock": o["stock_value"],
        }
