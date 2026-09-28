"""Onglet RH : salariés, demandes de congés, bulletins de paie."""
from __future__ import annotations

from tkinter import messagebox, ttk

from erp.services.hr_service import HrService

LEAVE_LABELS = {
    "conge_paye": "Congé payé", "rtt": "RTT", "maladie": "Maladie",
    "sans_solde": "Sans solde",
}


class HrTab:
    def __init__(self, app, frame: ttk.Frame, hr: HrService):
        self.app, self.hr = app, hr

        # ---------------------------------------------------------------- salariés
        ttk.Label(frame, text="Salariés", font=("", 11, "bold")).pack(anchor="w")
        top = ttk.Frame(frame)
        top.pack(fill="x")
        ttk.Button(top, text="Nouveau salarié",
                   command=self.new_employee).pack(side="left", padx=3)
        ttk.Button(top, text="Solde de congés",
                   command=self.leave_balance).pack(side="left", padx=3)

        self.emp_tree = ttk.Treeview(
            frame, columns=("id", "matricule", "name", "position", "department",
                            "contract", "salary"), show="headings", height=6,
            selectmode="browse")
        for c, h, w in zip(
                ("id", "matricule", "name", "position", "department",
                 "contract", "salary"),
                ("ID", "Matricule", "Nom", "Poste", "Service", "Contrat",
                 "Salaire base"),
                (30, 80, 160, 120, 100, 70, 90)):
            self.emp_tree.heading(c, text=h)
            self.emp_tree.column(c, width=w)
        self.emp_tree.pack(fill="x", pady=3)
        self.load_employees()

        # ---------------------------------------------------------------- congés
        ttk.Label(frame, text="Demandes de congés",
                  font=("", 11, "bold")).pack(anchor="w", pady=(8, 0))
        mid = ttk.Frame(frame)
        mid.pack(fill="x")
        ttk.Button(mid, text="Demande de congé",
                   command=self.new_leave).pack(side="left", padx=3)
        ttk.Button(mid, text="Approuver",
                   command=lambda: self.decide(True)).pack(side="left", padx=3)
        ttk.Button(mid, text="Refuser",
                   command=lambda: self.decide(False)).pack(side="left", padx=3)
        ttk.Button(mid, text="Annuler (approuvée)",
                   command=self.cancel).pack(side="left", padx=3)

        self.leave_tree = ttk.Treeview(
            frame, columns=("id", "matricule", "name", "type", "start", "end",
                            "days", "status"), show="headings", height=6,
            selectmode="browse")
        for c, h, w in zip(
                ("id", "matricule", "name", "type", "start", "end", "days",
                 "status"),
                ("ID", "Matricule", "Salarié", "Type", "Du", "Au", "Jours",
                 "Statut"),
                (30, 80, 150, 90, 90, 90, 50, 90)):
            self.leave_tree.heading(c, text=h)
            self.leave_tree.column(c, width=w)
        self.leave_tree.pack(fill="x", pady=3)
        self.load_leaves()

        # ---------------------------------------------------------------- paie
        ttk.Label(frame, text="Paie",
                  font=("", 11, "bold")).pack(anchor="w", pady=(8, 0))
        bottom = ttk.Frame(frame)
        bottom.pack(fill="x")
        ttk.Button(bottom, text="Bulletin individuel",
                   command=self.new_payslip).pack(side="left", padx=3)
        ttk.Button(bottom, text="Générer le mois",
                   command=self.generate_month).pack(side="left", padx=3)
        ttk.Button(bottom, text="Marquer payé",
                   command=self.pay_slip).pack(side="left", padx=3)
        ttk.Button(bottom, text="Voir bulletin (HTML)",
                   command=self.view_slip).pack(side="left", padx=3)

        self.pay_tree = ttk.Treeview(
            frame, columns=("id", "number", "matricule", "name", "period",
                            "gross", "net", "status"), show="headings",
            height=6, selectmode="browse")
        for c, h, w in zip(
                ("id", "number", "matricule", "name", "period", "gross", "net",
                 "status"),
                ("ID", "N°", "Matricule", "Salarié", "Période", "Brut", "Net",
                 "Statut"),
                (30, 100, 80, 150, 70, 80, 80, 80)):
            self.pay_tree.heading(c, text=h)
            self.pay_tree.column(c, width=w)
        self.pay_tree.pack(fill="both", expand=True, pady=3)
        self.load_payslips()

    # ------------------------------------------------------------------ salariés
    def load_employees(self) -> None:
        self.emp_tree.delete(*self.emp_tree.get_children())
        for e in self.hr.list_employees():
            self.emp_tree.insert("", "end", values=(
                e["id"], e["matricule"],
                f"{e['first_name']} {e['last_name']}".strip(),
                e["position"], e["department"], e["contract_type"],
                f"{e['base_salary']:.2f}"))

    def _selected_employee(self) -> dict | None:
        sel = self.emp_tree.selection()
        if not sel:
            messagebox.showwarning("Sélection", "Sélectionnez un salarié.")
            return None
        return self.hr.get_employee(self.emp_tree.item(sel[0])["values"][0])

    def new_employee(self) -> None:
        values = self.app._prompt_fields([
            ("matricule", "Matricule (vide = auto)"),
            ("first_name", "Prénom"), ("last_name", "Nom *"),
            ("position", "Poste"), ("department", "Service"),
            ("email", "Email"), ("phone", "Téléphone"),
            ("hire_date", "Embauche (AAAA-MM-JJ)"),
            ("contract_type", "Contrat (cdi/cdd/stage)"),
            ("base_salary", "Salaire de base")])
        if not values or not values.get("last_name"):
            return
        try:
            self.hr.create_employee(values)
            messagebox.showinfo("RH", "Salarié créé.")
        except Exception as e:
            messagebox.showerror("Erreur", str(e))
        self.load_employees()

    def leave_balance(self) -> None:
        emp = self._selected_employee()
        if not emp:
            return
        acquired = self.hr.leave_entitlement(emp["id"])
        taken = self.hr.leave_taken(emp["id"])
        messagebox.showinfo(
            "Solde de congés",
            f"{emp['first_name']} {emp['last_name']}\n"
            f"Acquis : {acquired:g} j — Pris : {taken:g} j — "
            f"Solde : {acquired - taken:g} j")

    # ------------------------------------------------------------------ congés
    def load_leaves(self) -> None:
        self.leave_tree.delete(*self.leave_tree.get_children())
        for lr in self.hr.list_leaves():
            self.leave_tree.insert("", "end", values=(
                lr["id"], lr["matricule"],
                f"{lr['first_name']} {lr['last_name']}".strip(),
                LEAVE_LABELS.get(lr["leave_type"], lr["leave_type"]),
                lr["start_date"], lr["end_date"], lr["days"],
                lr["status"]))

    def _selected_leave(self) -> dict | None:
        rid = self._leave_id()
        if not rid:
            return None
        for lr in self.hr.list_leaves():
            if lr["id"] == rid:
                return lr
        return None

    def new_leave(self) -> None:
        emp = self._selected_employee()
        if not emp:
            return
        values = self.app._prompt_fields([
            ("type", "Type (conge_paye / rtt / maladie / sans_solde)"),
            ("start", "Du (AAAA-MM-JJ)"), ("end", "Au (AAAA-MM-JJ)"),
            ("reason", "Motif")])
        if not values or not values.get("start") or not values.get("end"):
            return
        try:
            res = self.hr.request_leave(emp["id"], values.get("type") or
                                        "conge_paye", values["start"],
                                        values["end"], values.get("reason") or "")
            messagebox.showinfo("Congés",
                                f"Demande soumise ({res['days']:g} j ouvrés).")
        except Exception as e:
            messagebox.showerror("Erreur", str(e))
        self.load_leaves()

    def _leave_id(self) -> int | None:
        sel = self.leave_tree.selection()
        if not sel:
            messagebox.showwarning("Sélection", "Sélectionnez une demande.")
            return None
        return self.leave_tree.item(sel[0])["values"][0]

    def decide(self, approve: bool) -> None:
        rid = self._leave_id()
        if not rid:
            return
        try:
            self.hr.decide_leave(rid, approve)
        except Exception as e:
            messagebox.showerror("Erreur", str(e))
        self.load_leaves()

    def cancel(self) -> None:
        rid = self._leave_id()
        if not rid:
            return
        try:
            self.hr.cancel_leave(rid)
        except Exception as e:
            messagebox.showerror("Erreur", str(e))
        self.load_leaves()

    # ------------------------------------------------------------------ paie
    def load_payslips(self) -> None:
        self.pay_tree.delete(*self.pay_tree.get_children())
        for p in self.hr.list_payslips():
            self.pay_tree.insert("", "end", values=(
                p["id"], p["number"], p["matricule"],
                f"{p['first_name']} {p['last_name']}".strip(),
                p["period"], f"{p['gross']:.2f}", f"{p['net']:.2f}",
                "Payé" if p["status"] == "paye" else "À payer"))

    def new_payslip(self) -> None:
        emp = self._selected_employee()
        if not emp:
            return
        values = self.app._prompt_fields([
            ("period", "Période (AAAA-MM)"),
            ("base", "Salaire de base (vide = fiche salarié)"),
            ("bonus", "Primes (défaut 0)"),
            ("overtime", "Heures sup. (défaut 0)")])
        if not values or not values.get("period"):
            return
        try:
            slip = self.hr.create_payslip(
                emp["id"], values["period"],
                float(values["base"]) if values.get("base") else None,
                float(values.get("bonus") or 0),
                float(values.get("overtime") or 0))
            messagebox.showinfo("Paie",
                                f"Bulletin {slip['number']} créé "
                                f"(net {slip['net']:.2f}).")
        except Exception as e:
            messagebox.showerror("Erreur", str(e))
        self.load_payslips()

    def generate_month(self) -> None:
        values = self.app._prompt_fields([("period", "Période (AAAA-MM)")])
        if not values or not values.get("period"):
            return
        report = self.hr.generate_payslips(values["period"])
        msg = f"{report['ok']} bulletin(s) généré(s)."
        if report["errors"]:
            msg += "\n" + "\n".join(report["errors"])
        messagebox.showinfo("Paie", msg)
        self.load_payslips()

    def pay_slip(self) -> None:
        sel = self.pay_tree.selection()
        if not sel:
            messagebox.showwarning("Sélection", "Sélectionnez un bulletin.")
            return
        try:
            self.hr.pay_payslip(self.pay_tree.item(sel[0])["values"][0])
        except Exception as e:
            messagebox.showerror("Erreur", str(e))
        self.load_payslips()

    def view_slip(self) -> None:
        sel = self.pay_tree.selection()
        if not sel:
            messagebox.showwarning("Sélection", "Sélectionnez un bulletin.")
            return
        from erp.ui.flux_views import _show_html
        slip = self.hr.get_payslip(self.pay_tree.item(sel[0])["values"][0])
        _show_html(self.hr.payslip_html(slip["id"]), f"BUL_{slip['number']}")
