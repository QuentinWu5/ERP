"""Tests du bloc RH : salariés, congés, paie.Lancer : python3 tests/test_hr.py"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import date, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from erp.db.connection import Database, set_db_path
from erp.db.migrations import migrate
from erp.seed import seed_demo
from erp.services.hr_service import HrError, HrService
from erp.services.settings_service import SettingsService


def main() -> None:
    path = os.path.join(tempfile.mkdtemp(), "erp_test.db")
    set_db_path(path)
    db = Database()
    migrate(db.conn)
    seed_demo(db)

    st = SettingsService(db)
    hr = HrService(db, st)

    # -- Salariés
    eid = hr.create_employee({
        "first_name": "Marie", "last_name": "Dupont", "position": "Assembleuse",
        "department": "Production", "hire_date": "2024-01-15",
        "contract_type": "cdi", "base_salary": 2200})
    emp = hr.get_employee(eid)
    assert emp["matricule"] == "EMP001"
    eid2 = hr.create_employee({"last_name": "Martin", "base_salary": 2500,
                               "hire_date": "2025-06-01"})
    assert hr.list_employees()[0]["last_name"] == "Dupont"
    try:
        hr.create_employee({"first_name": "X"})
        raise AssertionError("salarié sans nom créé")
    except HrError:
        pass

    # -- Congés
    ent = hr.leave_entitlement(eid)
    assert ent > 0
    assert hr.leave_balance(eid) == ent
    start = date.today() + timedelta(days=30)
    end = start + timedelta(days=6)  # inclut un week-end -> 5 j ouvrés
    res = hr.request_leave(eid, "conge_paye", start.isoformat(),
                           end.isoformat(), "Vacances")
    assert res["days"] == 5, res
    lr = hr.list_leaves(employee_id=eid)[0]
    assert lr["status"] == "soumise"
    # approbation avec solde suffisant
    hr.decide_leave(lr["id"], approve=True)
    assert hr.leave_taken(eid) == 5
    assert hr.leave_balance(eid) == ent - 5
    # refus d'une demande déjà traitée
    try:
        hr.decide_leave(lr["id"], approve=True)
        raise AssertionError("double décision autorisée")
    except HrError:
        pass
    # annulation d'une demande approuvée -> solde restitué
    hr.cancel_leave(lr["id"])
    assert hr.leave_balance(eid) == ent
    # demande dépassant le solde -> refus (salarié récemment embauché)
    eid3 = hr.create_employee({"last_name": "Neuf", "base_salary": 1800,
                               "hire_date": date.today().isoformat()})
    big_start = date.today() + timedelta(days=30)
    big_end = big_start + timedelta(days=60)
    rid = hr.request_leave(eid3, "conge_paye", big_start.isoformat(),
                           big_end.isoformat())["id"]
    try:
        hr.decide_leave(rid, approve=True)
        raise AssertionError("congé au-delà du solde approuvé")
    except HrError:
        pass
    # type inconnu
    try:
        hr.request_leave(eid, "sabbatique", "2026-01-01", "2026-01-05")
        raise AssertionError("type de congé invalide accepté")
    except HrError:
        pass

    # -- Paie
    period = f"{date.today().year}-{date.today().month:02d}"
    slip = hr.create_payslip(eid, period, bonus=100, overtime=50)
    assert slip["number"] == f"BUL-{date.today().year}-0001"
    detail = hr.get_payslip(slip["id"])
    assert detail["gross"] == 2200 + 100 + 50
    assert detail["net"] == round(detail["gross"] * 0.78, 2)
    assert detail["status"] == "impaye"
    # doublon interdit
    try:
        hr.create_payslip(eid, period)
        raise AssertionError("bulletin dupliqué")
    except HrError:
        pass
    # génération du mois : seulement le salarié restant
    report = hr.generate_payslips(period)
    assert report["ok"] == 2 and len(report["errors"]) == 1
    # paiement
    hr.pay_payslip(slip["id"])
    assert hr.get_payslip(slip["id"])["status"] == "paye"
    try:
        hr.pay_payslip(slip["id"])
        raise AssertionError("double paiement")
    except HrError:
        pass
    # document HTML
    html = hr.payslip_html(slip["id"])
    assert "Bulletin de paie" in html and "Net à payer" in html

    db.close()
    os.remove(path)
    print("ALL HR TESTS PASSED")


if __name__ == "__main__":
    main()
