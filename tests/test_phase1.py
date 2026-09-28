"""Tests Phase 1 : cœur, paramètres, inventaire, nomenclature.
Lancer : python3 tests/test_phase1.py"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from erp.db.connection import Database, set_db_path
from erp.db.migrations import current_version, migrate
from erp.seed import seed_demo
from erp.services.bom_service import BomError, BomService
from erp.services.inventory_service import InventoryService, StockError
from erp.services.settings_service import SettingsService


def main() -> None:
    path = os.path.join(tempfile.mkdtemp(), "erp_test.db")
    set_db_path(path)
    db = Database()
    migrate(db.conn)
    assert current_version(db.conn) >= 1
    seed_demo(db)
    st = SettingsService(db)
    inv = InventoryService(db, st)
    bom = BomService(db, inv, st)

    # -- Paramètres / numérotation
    assert st.get("default_vat_rate") == "20.0"
    assert st.next_number("CMD") == "CMD-2026-0001"
    assert st.next_number("CMD") == "CMD-2026-0002"
    assert st.next_number("OF", with_year=False) == "OF-0001"
    st.set_module("bom", False)
    assert not st.module_enabled("bom")
    st.set_module("bom", True)
    assert st.module_enabled("bom")

    # -- Inventaire
    ctrl = inv.get_by_sku("CTRL-100")
    vis = inv.get_by_sku("VIS-M5")
    boxalu = inv.get_by_sku("BOITIER-ALU")
    assert ctrl and vis and boxalu

    inv.entry(vis["id"], 100, 0.10, reason="Test")
    a = inv.get_article(vis["id"])
    assert abs(a["cmup"] - (1000 * 0.08 + 100 * 0.10) / 1100) < 1e-4

    try:
        inv.exit_(boxalu["id"], 10000)
        raise AssertionError("stock négatif autorisé")
    except StockError:
        pass

    inv.reserve(ctrl["id"], 3)
    try:
        inv.reserve(ctrl["id"], 100)
        raise AssertionError("réservation excessive autorisée")
    except StockError:
        pass
    inv.unreserve(ctrl["id"], 3)

    # transfert d'emplacement
    inv.transfer(vis["id"], 50, "MAG/A1", "MAG/A9")
    locs = {l["store"] + "/" + l["shelf"]: l["qty"] for l in inv.locations(vis["id"])}
    assert locs.get("MAG/A9") == 50

    # inventaire physique
    cid = inv.open_count()
    inv.set_counted(cid, vis["id"], a["stock_qty"] - 5)
    diffs = inv.count_lines(cid)
    assert len(diffs) == 1 and diffs[0]["sku"] == "VIS-M5"
    inv.validate_count(cid)
    assert inv.get_article(vis["id"])["stock_qty"] == a["stock_qty"] - 5

    # -- Nomenclature
    cost = bom.standard_cost(ctrl["id"])
    assert cost > 30
    rows = bom.explode(ctrl["id"], 2)
    assert any(r["sku"] == "VIS-M5" and r["qty_total"] == 16.8 for r in rows)
    parents = bom.where_used(vis["id"])
    assert any(p["sku"] == "CTRL-100" for p in parents)

    pf2_id = inv.create_article({"sku": "PF2", "designation": "Sous-ens",
                                 "type": "produit_fini"})
    bom.add_line(bom.create_bom(pf2_id, "A"), ctrl["id"], 1)
    try:
        bom.add_line(bom.create_bom(ctrl["id"], "Z"), pf2_id, 1)
        raise AssertionError("cycle non détecté")
    except BomError:
        pass

    # -- Import CSV
    here = os.path.dirname(__file__)
    with open(os.path.join(here, "..", "csv_samples", "articles.csv"),
              encoding="utf-8-sig") as f:
        r = inv.import_articles_csv(f.read())
    assert r["ok"] == 5 and not r["errors"], r
    axe = inv.get_by_sku("AXE-10")
    assert any(m["move_type"] == "reprise_initiale" for m in inv.moves(axe["id"]))

    with open(os.path.join(here, "..", "csv_samples", "boms.csv"),
              encoding="utf-8-sig") as f:
        r = bom.import_bom_csv(f.read())
    assert r["ok"] == 4 and not r["errors"], r

    # -- Export / audit
    lines = inv.export_stock_csv().strip().splitlines()
    assert len(lines) >= 14
    assert st.audit_list()

    db.close()
    os.remove(path)
    print("ALL PHASE 1 TESTS PASSED")


if __name__ == "__main__":
    main()
