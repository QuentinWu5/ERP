"""Tests postes de travail + suivi OF par étape (gammes -> wo_steps)."""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from erp.db.connection import Database, set_db_path
from erp.db.migrations import migrate
from erp.seed import seed_demo
from erp.services.configurator_service import ConfiguratorService
from erp.services.manufacturing_service import (ManufacturingError,
                                                ManufacturingService)
from erp.services.settings_service import SettingsService


def main() -> None:
    path = os.path.join(tempfile.mkdtemp(), "erp_test.db")
    set_db_path(path)
    db = Database()
    migrate(db.conn)
    seed_demo(db)

    st = SettingsService(db)
    mfg = ManufacturingService(db, settings=st)
    cfg = ConfiguratorService(db, settings=st)

    # postes de travail
    def wc(code, name):
        row = db.query_one("SELECT id FROM work_centers WHERE code=?", (code,))
        return row["id"] if row else mfg.create_work_center(code, name)
    wc_soud, wc_usin = wc("SOUD", "Soudure"), wc("USIN", "Usinage")
    wc_asse, wc_test = wc("ASSE", "Assemblage"), wc("TEST", "Test & contrôle")
    assert len(mfg.list_work_centers()) == 4

    # gabarit vanne + config
    row = db.query_one("SELECT id FROM product_templates "
                       "WHERE sku_base='VANNE-TEST'")
    tid = row["id"] if row else cfg.create_template("VANNE-TEST",
                                                    "Vanne test", 100)
    cfg.add_base_component(tid, cfg.inventory.get_by_sku("VIS-M5")["id"], 4)
    dn = cfg.add_option(tid, "Diamètre", "DN")
    cfg.add_option_value(dn, "050", "DN50")
    res = cfg.configure(tid, {"DN": cfg.options(tid)[0]["values"][0]["id"]})

    # gamme : 4 étapes rattachées aux postes (via tooling = code poste)
    art_id = res["article_id"]
    for step_no, (desc, wc_code) in enumerate([
            ("Soudure corps", "SOUD"), ("Usinage siège", "USIN"),
            ("Assemblage", "ASSE"), ("Test pression", "TEST")], start=1):
        db.execute("INSERT INTO routings (article_id, step_no, description, "
                   "estimated_time, tooling) VALUES (?,?,?,?,?)",
                   (art_id, step_no, desc, 30, wc_code))

    # OF : les étapes sont générées depuis la gamme
    wo = mfg.create_wo(art_id, 5)
    steps = mfg.wo_steps(wo["id"])
    assert len(steps) == 4, steps
    assert steps[0]["description"] == "Soudure corps"
    assert steps[0]["wc_code"] == "SOUD"
    assert steps[3]["wc_code"] == "TEST"

    mfg.check_and_launch(wo["id"])

    # séquencement : impossible de sauter une étape
    try:
        mfg.start_step(wo["id"], 3)
        raise AssertionError("étape sautée autorisée")
    except ManufacturingError:
        pass

    # déroulé complet étape par étape
    mfg.start_step(wo["id"], 1)
    assert mfg.get_wo(wo["id"])["status"] == "en_cours"
    try:
        mfg.finish_step(wo["id"], 2)
        raise AssertionError("étape non démarrée terminée")
    except ManufacturingError:
        pass
    mfg.finish_step(wo["id"], 1, actual_time=25)
    mfg.start_step(wo["id"], 2)
    mfg.finish_step(wo["id"], 2, actual_time=40)
    mfg.start_step(wo["id"], 3)
    mfg.finish_step(wo["id"], 3)
    mfg.start_step(wo["id"], 4)
    mfg.finish_step(wo["id"], 4, actual_time=15)
    assert all(s["status"] == "termine" for s in mfg.wo_steps(wo["id"]))

    # file d'attente atelier vide après achèvement
    assert not mfg.queue_by_work_center(wc_soud)

    # production finale
    mfg.report_production(wo["id"], 5)
    assert mfg.get_wo(wo["id"])["status"] == "termine"

    # nouveau OF : visible dans les files des postes
    wo2 = mfg.create_wo(art_id, 2)
    mfg.check_and_launch(wo2["id"])
    q = mfg.queue_by_work_center(wc_soud)
    assert len(q) == 1 and q[0]["wo_number"] == wo2["number"]

    db.close()
    os.remove(path)
    print("ALL WORKCENTER TESTS PASSED")


if __name__ == "__main__":
    main()
