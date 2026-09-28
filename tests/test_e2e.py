"""Test end-to-end : flux complet commande → AR → achat → réception → OF →
livraison → facture → paiement, + cas limites.
Lancer : python3 tests/test_e2e.py"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from erp.db.connection import Database, set_db_path
from erp.db.migrations import migrate
from erp.seed import seed_demo
from erp.services.bom_service import BomService
from erp.services.dashboard_service import DashboardService
from erp.services.inventory_service import InventoryService
from erp.services.invoicing_service import InvoicingService
from erp.services.manufacturing_service import ManufacturingError, ManufacturingService
from erp.services.purchasing_service import PurchasingService
from erp.services.sales_service import SalesService
from erp.services.settings_service import SettingsService
from erp.services.warehouse_service import WarehouseService


def main() -> None:
    set_db_path(os.path.join(tempfile.mkdtemp(), "erp_e2e.db"))
    db = Database()
    migrate(db.conn)
    seed_demo(db)
    st = SettingsService(db)
    inv = InventoryService(db, st)
    bom = BomService(db, inv, st)
    sales = SalesService(db, inv, st)
    mfg = ManufacturingService(db, inv, bom, st)
    pur = PurchasingService(db, inv, st)
    wh = WarehouseService(db, inv, sales, st)
    bill = InvoicingService(db, sales, st)
    dash = DashboardService(db, st, inv, sales, mfg, pur, bill, bom)

    cid = sales.create_customer({"name": "Client Test SARL", "address": "1 rue A"})
    ctrl = inv.get_by_sku("CTRL-100")
    serv = inv.get_by_sku("MAINT-1H")
    vis = inv.get_by_sku("VIS-M5")
    pcb = inv.get_by_sku("PCB-CTRL")
    cap = inv.get_by_sku("CAP-STD")

    # --- flux 1 : commande → AR → OF → production → BL → facture → paiement
    order = sales.create_order(cid, desired_date="2026-10-15")
    sales.add_line(order["id"], ctrl["id"], 10, price=149.0)
    sales.add_line(order["id"], serv["id"], 2, price=45.0)
    assert sales.order_total(order["id"])["ttc"] == 1896.0
    ar = sales.confirm_order(order["id"])
    assert ar == "AR-2026-0001"

    wo = mfg.create_wo(ctrl["id"], 10, sales_order_id=order["id"])
    mfg.check_and_launch(wo["id"])
    assert mfg.get_wo(wo["id"])["status"] == "lance"
    assert sales.get_order(order["id"])["status"] == "en_production"
    assert inv.get_article(vis["id"])["reserved_qty"] == 84
    mfg.report_production(wo["id"], 10)
    assert inv.get_article(ctrl["id"])["stock_qty"] == 15
    assert inv.get_article(vis["id"])["reserved_qty"] == 0
    assert sales.get_order(order["id"])["status"] == "prete"

    bl = wh.create_delivery_note(order["id"], carrier="Chrono")
    wh.deliver(bl["id"])
    assert inv.get_article(ctrl["id"])["stock_qty"] == 5
    assert sales.get_order(order["id"])["status"] == "livree"

    inv_doc = bill.create_invoice_from_order(order["id"])
    inv1 = bill.get_invoice(inv_doc["id"])
    assert inv1["total_ttc"] == 1896.0
    bill.register_payment(inv_doc["id"], 500.0)
    assert bill.get_invoice(inv_doc["id"])["status"] == "partiellement_payee"
    bill.register_payment(inv_doc["id"], inv1["total_ttc"] - 500.0)
    assert bill.get_invoice(inv_doc["id"])["status"] == "payee"

    # --- flux 2 : manque stock → suggestion achat → réception → OF → livraison
    order2 = sales.create_order(cid)
    sales.add_line(order2["id"], ctrl["id"], 25)
    sales.confirm_order(order2["id"])
    wo2 = mfg.create_wo(ctrl["id"], 25, sales_order_id=order2["id"])
    res2 = mfg.check_and_launch(wo2["id"])
    assert any(m["sku"] == "PCB-CTRL" for m in res2["missing"])
    assert any(s["article_id"] == pcb["id"] for s in pur.suggestions())

    sid = pur.create_supplier({"name": "CompoPro"})
    po = pur.create_po(sid)
    pur.add_line(po["id"], pcb["id"], 20, price=21.50)
    pur.send_po(po["id"])
    pur.receive(po["id"], pcb["id"], 20)
    assert inv.get_article(pcb["id"])["stock_qty"] == 40
    assert pur.best_price(pcb["id"])["price"] == 21.50
    assert pur.get_po(po["id"])["status"] == "recue"

    mfg.report_production(wo2["id"], 25)
    bl2 = wh.create_delivery_note(order2["id"])
    wh.deliver(bl2["id"])
    assert sales.get_order(order2["id"])["status"] == "livree"
    inv_doc2 = bill.create_invoice_from_order(order2["id"])
    assert bill.get_invoice(inv_doc2["id"])["total_ttc"] == round(25 * 149 * 1.2, 2)

    # --- avoir, annulation, réception partielle, politique bloquante
    av = bill.create_credit_note(inv_doc["id"], 100, "Retour produit")
    assert bill.get_invoice(av["id"])["kind"] == "avoir"

    order3 = sales.create_order(cid)
    sales.add_line(order3["id"], ctrl["id"], 1)
    sales.cancel_order(order3["id"])
    assert sales.get_order(order3["id"])["status"] == "annulee"

    po2 = pur.create_po(sid)
    pur.add_line(po2["id"], vis["id"], 100, price=0.09)
    pur.send_po(po2["id"])
    pur.receive(po2["id"], vis["id"], 40)
    assert pur.get_po(po2["id"])["status"] == "recue_partiel"
    pur.receive(po2["id"], vis["id"], 60)
    assert pur.get_po(po2["id"])["status"] == "recue"

    st.set("of_missing_policy", "block")
    wo3 = mfg.create_wo(ctrl["id"], 50)
    try:
        mfg.check_and_launch(wo3["id"])
        raise AssertionError("politique bloquante non appliquée")
    except ManufacturingError:
        pass
    st.set("of_missing_policy", "warn")

    order4 = sales.create_order(cid)
    sales.add_line(order4["id"], cap["id"], 10)
    sales.confirm_order(order4["id"])
    assert inv.get_article(cap["id"])["reserved_qty"] == 10
    sales.cancel_order(order4["id"])
    assert inv.get_article(cap["id"])["reserved_qty"] == 0

    # --- dashboard + documents
    c = dash.counters()
    assert c["Factures impayées"] == 1
    assert c["Valeur du stock"] > 0
    assert "Accusé de réception" in sales.ar_html(order["id"])
    assert "Bon de livraison" in wh.bl_html(bl["id"])
    assert "Facture" in bill.invoice_html(inv_doc["id"])

    mfg.set_routing(ctrl["id"], [
        {"description": "Monter PCB", "estimated_time": "10 min", "tooling": "TX10"},
        {"description": "Fixer capot", "estimated_time": "5 min", "tooling": "Clé 8"}])
    assert len(mfg.get_routing(ctrl["id"])) == 2
    mfg.add_plan(ctrl["id"], "P-CTRL-100", "A", "/plans/ctrl100.pdf")
    assert mfg.plans(ctrl["id"])[0]["number"] == "P-CTRL-100"

    db.close()
    print("E2E ALL OK")


if __name__ == "__main__":
    main()
