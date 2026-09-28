"""Tests Projets & Calendrier : import commande, tâches, calendrier, rappels."""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import date, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from erp.db.connection import Database, set_db_path
from erp.db.migrations import migrate
from erp.seed import seed_demo
from erp.services.inventory_service import InventoryService
from erp.services.project_service import ProjectError, ProjectService
from erp.services.sales_service import SalesService
from erp.services.settings_service import SettingsService


def main() -> None:
    path = os.path.join(tempfile.mkdtemp(), "erp_test.db")
    set_db_path(path)
    db = Database()
    migrate(db.conn)
    seed_demo(db)

    st = SettingsService(db)
    inv = InventoryService(db, st)
    sales = SalesService(db, inv, st)
    proj = ProjectService(db, st)

    # -- projet manuel
    p = proj.create_project("Refonte site web", due_date="2026-12-31")
    assert p["number"] == "PRJ-2026-0001"
    try:
        proj.create_project("  ")
        raise AssertionError("projet sans nom créé")
    except ProjectError:
        pass
    tid = proj.add_task(p["id"], "Maquette", due_date="2026-11-15")
    proj.set_task_status(tid, "termine")
    assert all(t["status"] == "termine" for t in proj.tasks(p["id"]))

    # -- import depuis une commande client
    cust = sales.create_customer({"name": "Client Pro"})
    order = sales.create_order(cust)
    art = inv.get_by_sku("CTRL-100")
    sales.add_line(order["id"], art["id"], 3)
    due = (date.today() + timedelta(days=10)).isoformat()
    db.execute("UPDATE sales_orders SET desired_date=?, status='confirmee' "
               "WHERE id=?", (due, order["id"]))
    res = proj.import_from_sales_order(order["id"])
    project = proj.get_project(res["project_id"])
    assert project["order_number"] == order["number"]
    assert project["due_date"] == due
    tasks = proj.tasks(res["project_id"])
    assert len(tasks) == 1 and "CTRL-100" in tasks[0]["title"]
    assert tasks[0]["due_date"] == due
    # tâche ouverte comptée
    row = [r for r in proj.list_projects() if r["id"] == res["project_id"]][0]
    assert row["open_tasks"] == 1

    # -- calendrier : congés + échéances du mois courant
    from erp.services.hr_service import HrService
    hr = HrService(db, st)
    emp = hr.create_employee({"first_name": "Léa", "last_name": "Bernard",
                              "base_salary": 2000,
                              "hire_date": (date.today() -
                                             timedelta(days=400)).isoformat()})
    start = date.today() + timedelta(days=5)
    end = start + timedelta(days=4)
    rid = hr.request_leave(emp, "conge_paye", start.isoformat(),
                           end.isoformat())["id"]
    hr.decide_leave(rid, approve=True)
    # mois contenant le congé (peut différer du mois courant)
    conge_month = f"{start.year}-{start.month:02d}"
    events = proj.calendar_events(conge_month)
    kinds = {e["kind"] for e in events}
    assert "conges" in kinds, events
    # mois contenant l'échéance de la commande importée
    due_month = f"{date.fromisoformat(due).year}:{date.fromisoformat(due).month:02d}".replace(":", "-")
    events_due = proj.calendar_events(due_month)
    assert any(e["kind"] in ("commande", "tache", "projet")
               for e in events_due), events_due

    # -- rappels : échéance dans 10 j -> détectée, SMTP absent -> skippée
    pending = proj.pending_reminders(days_before=15)
    assert any(item["kind"] == "projet" and item["due"] == due
               for item in pending), pending
    report = proj.send_reminders(days_before=15)
    assert report["skipped"] >= 1 and report["sent"] == 0, report
    # anti-doublon : plus rien en attente
    assert not proj.pending_reminders(days_before=15)

    # -- statut projet
    proj.set_project_status(p["id"], "termine")
    assert proj.get_project(p["id"])["status"] == "termine"

    db.close()
    os.remove(path)
    print("ALL PROJECTS TESTS PASSED")


if __name__ == "__main__":
    main()
