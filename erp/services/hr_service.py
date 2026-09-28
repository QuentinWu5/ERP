"""Bloc RH : fiches salariés, demandes de congés (soldes calculés),paie mensuelle (bulletins HTML)."""
from __future__ import annotations

from datetime import date
from typing import Optional

from erp.db.connection import Database
from erp.services.settings_service import SettingsService

LEAVE_TYPES = ["conge_paye", "rtt", "maladie", "sans_solde"]
LEAVE_STATUS = ["soumise", "approuvee", "refusee", "annulee"]
DAYS_PER_MONTH = 2.5  # jours de congé acquis par mois travaillé


class HrError(Exception):
    pass


class HrService:
    def __init__(self, db: Database, settings: SettingsService | None = None):
        self.db = db
        self.settings = settings or SettingsService(db)

    # ------------------------------------------------------------------ salariés
    def create_employee(self, data: dict) -> int:
        matricule = (data.get("matricule") or "").strip()
        if not matricule:
            matricule = f"EMP{self.db.query_one('SELECT COUNT(*) c FROM employees')['c'] + 1:03d}"
        if not (data.get("last_name") or "").strip():
            raise HrError("Le nom du salarié est obligatoire")
        try:
            eid = self.db.execute(
                "INSERT INTO employees (matricule, first_name, last_name, "
                "position, department, email, phone, hire_date, contract_type, "
                "base_salary) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (matricule, data.get("first_name", ""), data["last_name"],
                 data.get("position", ""), data.get("department", ""),
                 data.get("email", ""), data.get("phone", ""),
                 data.get("hire_date", "") or date.today().isoformat(),
                 data.get("contract_type", "cdi"),
                 float(data.get("base_salary") or 0)))
        except Exception as e:
            raise HrError(f"Création salarié impossible : {e}")
        self.settings.audit("employee.create", "employees", eid, matricule)
        return eid

    def update_employee(self, eid: int, data: dict) -> None:
        cols = ["matricule", "first_name", "last_name", "position", "department",
                "email", "phone", "hire_date", "contract_type", "base_salary",
                "active"]
        sets, params = [], []
        for c in cols:
            if c in data:
                sets.append(f"{c}=?")
                params.append(data[c])
        if sets:
            params.append(eid)
            self.db.execute(f"UPDATE employees SET {', '.join(sets)} WHERE id=?",
                            params)
            self.settings.audit("employee.update", "employees", eid)

    def get_employee(self, eid: int) -> Optional[dict]:
        return self.db.query_one("SELECT * FROM employees WHERE id=?", (eid,))

    def list_employees(self, active_only: bool = True) -> list[dict]:
        sql = "SELECT * FROM employees"
        if active_only:
            sql += " WHERE active=1"
        return self.db.query(sql + " ORDER BY last_name, first_name")

    # ------------------------------------------------------------------ congés
    def leave_entitlement(self, eid: int) -> float:
        """Jours acquis depuis l'embauche (2,5 j/mois)."""
        emp = self.get_employee(eid)
        if not emp:
            raise HrError("Salarié introuvable")
        hire = date.fromisoformat(emp["hire_date"] or date.today().isoformat())
        months = ((date.today().year - hire.year) * 12
                  + date.today().month - hire.month)
        return round(months * DAYS_PER_MONTH, 2)

    def leave_taken(self, eid: int) -> float:
        row = self.db.query_one(
            "SELECT COALESCE(SUM(days),0) AS d FROM leave_requests WHERE "
            "employee_id=? AND status='approuvee' AND leave_type IN "
            "('conge_paye','rtt')", (eid,))
        return round(row["d"], 2)

    def leave_balance(self, eid: int) -> float:
        return round(self.leave_entitlement(eid) - self.leave_taken(eid), 2)

    def _business_days(self, start: date, end: date) -> float:
        days, d = 0, start
        while d <= end:
            if d.weekday() < 5:
                days += 1
            d = date.fromordinal(d.toordinal() + 1)
        return days

    def request_leave(self, eid: int, leave_type: str, start: str, end: str,
                      reason: str = "") -> dict:
        if leave_type not in LEAVE_TYPES:
            raise HrError("Type de congé inconnu")
        emp = self.get_employee(eid)
        if not emp:
            raise HrError("Salarié introuvable")
        try:
            d_start, d_end = date.fromisoformat(start), date.fromisoformat(end)
        except ValueError:
            raise HrError("Dates invalides (format AAAA-MM-JJ)")
        if d_end < d_start:
            raise HrError("La date de fin précède la date de début")
        days = self._business_days(d_start, d_end)
        if days == 0:
            raise HrError("Aucun jour ouvré dans cette période")
        rid = self.db.execute(
            "INSERT INTO leave_requests (employee_id, leave_type, start_date, "
            "end_date, days, reason) VALUES (?,?,?,?,?,?)",
            (eid, leave_type, start, end, days, reason))
        self.settings.audit("leave.request", "leave_requests", rid,
                            f"{emp['matricule']} {days}j")
        return {"id": rid, "days": days}

    def decide_leave(self, rid: int, approve: bool,
                     decided_by: str = "") -> None:
        req = self.db.query_one("SELECT * FROM leave_requests WHERE id=?", (rid,))
        if not req:
            raise HrError("Demande introuvable")
        if req["status"] != "soumise":
            raise HrError(f"Demande déjà traitée ({req['status']})")
        if approve:
            if req["leave_type"] in ("conge_paye", "rtt"):
                if req["days"] > self.leave_balance(req["employee_id"]) + 1e-9:
                    raise HrError(
                        f"Solde insuffisant : {self.leave_balance(req['employee_id']):g} j")
            status = "approuvee"
        else:
            status = "refusee"
        self.db.execute(
            "UPDATE leave_requests SET status=?, decided_by=? WHERE id=?",
            (status, decided_by, rid))
        self.settings.audit(f"leave.{status}", "leave_requests", rid)

    def cancel_leave(self, rid: int) -> None:
        req = self.db.query_one("SELECT * FROM leave_requests WHERE id=?", (rid,))
        if not req or req["status"] != "approuvee":
            raise HrError("Seule une demande approuvée peut être annulée")
        self.db.execute("UPDATE leave_requests SET status='annulee' WHERE id=?",
                        (rid,))
        self.settings.audit("leave.annulee", "leave_requests", rid)

    def list_leaves(self, employee_id: Optional[int] = None,
                    status: str = "") -> list[dict]:
        sql = ("SELECT lr.*, e.matricule, e.first_name, e.last_name "
               "FROM leave_requests lr JOIN employees e ON e.id=lr.employee_id "
               "WHERE 1=1")
        params: list = []
        if employee_id:
            sql += " AND lr.employee_id=?"
            params.append(employee_id)
        if status:
            sql += " AND lr.status=?"
            params.append(status)
        return self.db.query(sql + " ORDER BY lr.id DESC", params)

    def pending_leaves(self) -> list[dict]:
        return self.list_leaves(status="soumise")

    # ------------------------------------------------------------------ paie
    def _cotisation_rates(self) -> tuple[float, float]:
        sal = float(self.settings.get("hr_cotisation_salariale") or 22)
        pat = float(self.settings.get("hr_cotisation_patronale") or 42)
        return sal, pat

    def create_payslip(self, eid: int, period: str, base: Optional[float] = None,
                       bonus: float = 0, overtime: float = 0) -> dict:
        emp = self.get_employee(eid)
        if not emp:
            raise HrError("Salarié introuvable")
        if self.db.query_one("SELECT id FROM payslips WHERE employee_id=? AND "
                             "period=?", (eid, period)):
            raise HrError(f"Bulletin déjà existant pour {period}")
        if not base:
            base = emp["base_salary"]
        brut = round(base + bonus + overtime, 2)
        sal_rate, pat_rate = self._cotisation_rates()
        cot_salariales = round(brut * sal_rate / 100, 2)
        cot_patronales = round(brut * pat_rate / 100, 2)
        net = round(brut - cot_salariales, 2)
        number = self.settings.next_number("BUL")
        pid = self.db.execute(
            "INSERT INTO payslips (number, employee_id, period, base_salary, "
            "bonus, overtime, gross, cot_employee, cot_employer, net, status) "
            "VALUES (?,?,?,?,?,?,?,?,?,?, 'impaye')",
            (number, eid, period, base, bonus, overtime, brut,
             cot_salariales, cot_patronales, net))
        self.settings.audit("payslip.create", "payslips", pid, number)
        return {"id": pid, "number": number, "net": net}

    def generate_payslips(self, period: str) -> dict:
        """Génère les bulletins du mois pour tous les salariés actifs."""
        ok, errors = 0, []
        for e in self.list_employees():
            try:
                self.create_payslip(e["id"], period)
                ok += 1
            except Exception as ex:
                errors.append(f"{e['matricule']} : {ex}")
        return {"ok": ok, "errors": errors}

    def pay_payslip(self, pid: int) -> None:
        slip = self.get_payslip(pid)
        if not slip:
            raise HrError("Bulletin introuvable")
        if slip["status"] == "paye":
            raise HrError("Bulletin déjà payé")
        self.db.execute(
            "UPDATE payslips SET status='paye', "
            "paid_at=datetime('now','localtime') WHERE id=?", (pid,))
        self.settings.audit("payslip.pay", "payslips", pid, slip["number"])

    def get_payslip(self, pid: int) -> Optional[dict]:
        return self.db.query_one(
            "SELECT p.*, e.matricule, e.first_name, e.last_name, e.position "
            "FROM payslips p JOIN employees e ON e.id=p.employee_id WHERE p.id=?",
            (pid,))

    def list_payslips(self, period: str = "", status: str = "") -> list[dict]:
        sql = ("SELECT p.*, e.matricule, e.last_name, e.first_name "
               "FROM payslips p JOIN employees e ON e.id=p.employee_id WHERE 1=1")
        params: list = []
        if period:
            sql += " AND p.period=?"
            params.append(period)
        if status:
            sql += " AND p.status=?"
            params.append(status)
        return self.db.query(sql + " ORDER BY p.id DESC", params)

    # ------------------------------------------------------------------ document
    def payslip_html(self, pid: int) -> str:
        slip = self.get_payslip(pid)
        s = self.settings.all()
        name = f"{slip['first_name']} {slip['last_name']}"
        rows = [
            ("Salaire de base", slip["base_salary"]),
            ("Primes", slip["bonus"]),
            ("Heures supplémentaires", slip["overtime"]),
            ("Brut", slip["gross"]),
            (f"Cotisations salariales", -slip["cot_employee"]),
            (f"Cotisations patronales", slip["cot_employer"]),
            ("Net à payer", slip["net"]),
        ]
        lines = "".join(
            f"<tr><td>{label}</td><td style='text-align:right'>{val:.2f} €</td></tr>"
            for label, val in rows)
        paid = "Payé" if slip["status"] == "paye" else "À payer"
        return f"""<html><head><meta charset="utf-8"><title>Bulletin {slip['number']}</title><style>body{{font-family:Arial;margin:30px}} table{{border-collapse:collapse;width:100%}}td,th{{border:1px solid #999;padding:6px}}</style></head><body><h2>{s.get('company_name','')}</h2><p>{s.get('company_address','')} — SIRET {s.get('company_siret','')}</p><h1>Bulletin de paie {slip['number']}</h1><p>Période : {slip['period']}<br>Salarié : {slip['matricule']} — {name}<br>Poste : {slip['position'] or '—'}<br>Statut : {paid}</p><table>{lines}</table></body></html>"""
