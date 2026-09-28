"""Tests du configurateur de variantes : gabarit vanne + options,generation article/BOM/OF, idempotence."""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from erp.db.connection import Database, set_db_path
from erp.db.migrations import migrate
from erp.seed import seed_demo
from erp.services.bom_service import BomService
from erp.services.configurator_service import (ConfiguratorError,
                                                ConfiguratorService)
from erp.services.inventory_service import InventoryService
from erp.services.settings_service import SettingsService


def main() -> None:
    path = os.path.join(tempfile.mkdtemp(), "erp_test.db")
    set_db_path(path)
    db = Database()
    migrate(db.conn)
    seed_demo(db)

    st = SettingsService(db)
    inv = InventoryService(db, st)
    bom = BomService(db, inv, st)
    cfg = ConfiguratorService(db, inv, bom, st)

    # -- composants de la vanne
    corps_inox = (inv.get_by_sku("CORPS-INOX") or {"id": inv.create_article(
        {"sku": "CORPS-INOX", "designation": "Corps inox",
         "type": "composant", "purchase_price": 45})})["id"]
    corps_bronze = (inv.get_by_sku("CORPS-BRONZE") or {"id": inv.create_article(
        {"sku": "CORPS-BRONZE", "designation": "Corps bronze",
         "type": "composant", "purchase_price": 38})})["id"]
    act_man = (inv.get_by_sku("ACT-MAN") or {"id": inv.create_article(
        {"sku": "ACT-MAN", "designation": "Actionneur manuel",
         "type": "composant", "purchase_price": 12})})["id"]
    act_pneu = (inv.get_by_sku("ACT-PNEU") or {"id": inv.create_article(
        {"sku": "ACT-PNEU", "designation": "Actionneur pneumatique",
         "type": "composant", "purchase_price": 85})})["id"]
    vis = inv.get_by_sku("VIS-M5")

    # -- gabarit VANNE-BALL
    existing = db.query_one("SELECT id FROM product_templates "
                             "WHERE sku_base='VANNE-BALL'")
    tid = existing["id"] if existing else cfg.create_template(
        "VANNE-BALL", "Vanne à boisselet", 200)
    db.execute("DELETE FROM option_values WHERE option_id IN "
               "(SELECT id FROM template_options WHERE template_id=?)", (tid,))
    db.execute("DELETE FROM template_options WHERE template_id=?", (tid,))
    db.execute("DELETE FROM template_components WHERE template_id=?", (tid,))
    cfg.add_base_component(tid, vis["id"], 8, 5)
    # option DN (sans composant, juste prix)
    dn = cfg.add_option(tid, "Diamètre nominal", "DN", position=1)
    cfg.add_option_value(dn, "025", "DN25", price_extra=0)
    cfg.add_option_value(dn, "050", "DN50", price_extra=20)
    cfg.add_option_value(dn, "100", "DN100", price_extra=55)
    # option matière du corps → composant
    mat = cfg.add_option(tid, "Matière corps", "MAT", position=2)
    v_inox = cfg.add_option_value(mat, "INOX", "Inox 316",
                                  component_id=corps_inox)
    v_bronze = cfg.add_option_value(mat, "BRONZE", "Bronze",
                                    component_id=corps_bronze)
    # option actionneur
    act = cfg.add_option(tid, "Actionneur", "ACT", position=3)
    v_man = cfg.add_option_value(act, "MAN", "Manuel",
                                 component_id=act_man)
    v_pneu = cfg.add_option_value(act, "PNEU", "Pneumatique",
                                  component_id=act_pneu, price_extra=60)

    # -- configuration DN50 + inox + pneu
    res = cfg.configure(tid, {"DN": cfg.options(tid)[0]["values"][1]["id"],
                              "MAT": v_inox, "ACT": v_pneu})
    assert res["sku"] == "VANNE-BALL-050-INOX-PNEU", res
    assert res["sale_price"] == 200 + 20 + 0 + 60, res
    art = inv.get_by_sku(res["sku"])
    assert art and art["type"] == "produit_fini"
    # BOM générée : vis (base) + corps inox + actionneur pneu
    lines = bom.lines(res["bom_id"])
    skus = {l["sku"]: l for l in lines}
    assert set(skus) == {"VIS-M5", "CORPS-INOX", "ACT-PNEU"}, skus
    assert skus["VIS-M5"]["qty_per"] == 8
    assert skus["ACT-PNEU"]["qty_per"] == 1

    # -- autre configuration (bronze + manuel + DN25)
    res2 = cfg.configure(tid, {"DN": cfg.options(tid)[0]["values"][0]["id"],
                                "MAT": v_bronze, "ACT": v_man})
    assert res2["sku"] == "VANNE-BALL-025-BRONZE-MAN", res2
    assert res2["sale_price"] == 200
    skus2 = {l["sku"] for l in bom.lines(res2["bom_id"])}
    assert skus2 == {"VIS-M5", "CORPS-BRONZE", "ACT-MAN"}

    # -- idempotence : re-configurer le même SKU ne duplique rien
    res3 = cfg.configure(tid, {"DN": cfg.options(tid)[0]["values"][1]["id"],
                               "MAT": v_inox, "ACT": v_pneu})
    assert res3["article_id"] == res["article_id"]
    assert len(bom.lines(res3["bom_id"])) == 3

    # -- simulation sans création
    sim = cfg.configure(tid, {"MAT": v_bronze, "ACT": v_man}, create=False)
    assert sim["sku"] == "VANNE-BALL-BRONZE-MAN"

    # -- option inconnue
    try:
        cfg.configure(tid, {"XX": 1})
        raise AssertionError("option inconnue acceptée")
    except ConfiguratorError:
        pass

    # -- OF depuis la configuration
    wo = cfg.create_wo_for(res["article_id"], 10)
    assert wo["number"].startswith("OF-")
    # coût standard calculable depuis la BOM générée
    cost = bom.standard_cost(res["article_id"])
    assert cost > 45 + 85, cost

    db.close()
    os.remove(path)
    print("ALL CONFIGURATOR TESTS PASSED")


if __name__ == "__main__":
    main()
