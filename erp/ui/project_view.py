"""Onglet Projets & Calendrier : projets, livrables, calendrier mensuelpartagé (congés + échéances), rappels email."""
from __future__ import annotations

import calendar
from datetime import date
from tkinter import messagebox, ttk

from erp.services.project_service import ProjectService
from erp.services.sales_service import SalesService

STATUS_LABELS = {"ouvert": "Ouvert", "en_cours": "En cours",
                 "termine": "Terminé", "annule": "Annulé",
                 "a_faire": "À faire", "en_cours": "En cours",
                 "termine": "Terminé", "annule": "Annulé"}


class ProjectsTab:
    def __init__(self, app, frame: ttk.Frame, proj: ProjectService,
                 sales: SalesService):
        self.app, self.proj, self.sales = app, proj, sales

        top = ttk.Frame(frame)
        top.pack(fill="x")
        ttk.Button(top, text="Nouveau projet",
                   command=self.new_project).pack(side="left", padx=3)
        ttk.Button(top, text="Importer une commande client",
                   command=self.import_order).pack(side="left", padx=3)
        ttk.Button(top, text="Ajouter tâche",
                   command=self.add_task).pack(side="left", padx=3)
        ttk.Button(top, text="Tâche terminée",
                   command=self.finish_task).pack(side="left", padx=3)
        ttk.Button(top, text="Clôturer projet",
                   command=self.close_project).pack(side="left", padx=3)

        self.proj_tree = ttk.Treeview(
            frame, columns=("id", "number", "name", "customer", "due",
                            "status", "open_tasks"), show="headings",
            height=6, selectmode="browse")
        for c, h, w in zip(
                ("id", "number", "name", "customer", "due", "status",
                 "open_tasks"),
                ("ID", "N°", "Projet", "Client", "Échéance", "Statut",
                 "Tâches ouvertes"),
                (30, 100, 240, 140, 90, 80, 100)):
            self.proj_tree.heading(c, text=h)
            self.proj_tree.column(c, width=w)
        self.proj_tree.pack(fill="x", pady=3)
        self.proj_tree.bind("<<TreeviewSelect>>", self.load_tasks)

        ttk.Label(frame, text="Tâches / livrables du projet sélectionné"
                  ).pack(anchor="w")
        self.task_tree = ttk.Treeview(
            frame, columns=("id", "title", "assignee", "due", "status"),
            show="headings", height=5, selectmode="browse")
        for c, h, w in zip(
                ("id", "title", "assignee", "due", "status"),
                ("ID", "Tâche", "Assignée à", "Échéance", "Statut"),
                (30, 300, 140, 90, 80)):
            self.task_tree.heading(c, text=h)
            self.task_tree.column(c, width=w)
        self.task_tree.pack(fill="x", pady=3)
        self.load_projects()

        # ------------------------------------------------------------ calendrier
        cal_bar = ttk.Frame(frame)
        cal_bar.pack(fill="x", pady=(8, 0))
        ttk.Label(cal_bar, text="Calendrier partagé — ",
                  font=("", 11, "bold")).pack(side="left")
        self._cal_date = date.today()
        self._cal_var = ttk.Label(cal_bar, text="")
        self._cal_var.pack(side="left")
        ttk.Button(cal_bar, text="◀", width=3,
                   command=lambda: self._move_month(-1)).pack(side="left",
                                                              padx=2)
        ttk.Button(cal_bar, text="▶", width=3,
                   command=lambda: self._move_month(1)).pack(side="left",
                                                             padx=2)
        ttk.Button(cal_bar, text="Rappels email…",
                   command=self.send_reminders).pack(side="right", padx=3)

        self.cal_grid = ttk.Frame(frame)
        self.cal_grid.pack(fill="both", expand=True, pady=3)
        self.render_calendar()

    # ------------------------------------------------------------------ projets
    def load_projects(self) -> None:
        self.proj_tree.delete(*self.proj_tree.get_children())
        for p in self.proj.list_projects():
            self.proj_tree.insert("", "end", values=(
                p["id"], p["number"], p["name"], p["customer_name"] or "",
                p["due_date"], STATUS_LABELS.get(p["status"], p["status"]),
                p["open_tasks"]))

    def _selected_project(self) -> dict | None:
        sel = self.proj_tree.selection()
        if not sel:
            messagebox.showwarning("Sélection", "Sélectionnez un projet.")
            return None
        return self.proj.get_project(self.proj_tree.item(sel[0])["values"][0])

    def new_project(self) -> None:
        values = self.app._prompt_fields([
            ("name", "Nom du projet *"),
            ("due", "Échéance (AAAA-MM-JJ)"),
            ("notes", "Notes")])
        if not values or not values.get("name"):
            return
        try:
            p = self.proj.create_project(values["name"],
                                         due_date=values.get("due") or "",
                                         notes=values.get("notes") or "")
            messagebox.showinfo("Projets", f"Projet {p['number']} créé.")
        except Exception as e:
            messagebox.showerror("Erreur", str(e))
        self.load_projects()

    def import_order(self) -> None:
        orders = [o for o in self.sales.list_orders()
                  if o["status"] != "annulee"]
        if not orders:
            messagebox.showwarning("Projets", "Aucune commande à importer.")
            return
        win = self.app._form_window("Importer une commande en projet")
        from tkinter import ttk as _ttk
        cb = _ttk.Combobox(win, values=[
            f"{o['id']} — {o['number']} ({o['customer_name']})"
            for o in orders], state="readonly", width=50)
        cb.grid(row=0, column=1)
        cb.current(0)
        _ttk.Label(win, text="Commande").grid(row=0, column=0)
        self.app.wait_window(win)
        if not cb.get():
            return
        try:
            res = self.proj.import_from_sales_order(
                int(cb.get().split(" — ")[0]))
            messagebox.showinfo(
                "Projets",
                f"Projet {res['number']} créé avec {res['tasks']} livrable(s).")
        except Exception as e:
            messagebox.showerror("Erreur", str(e))
        self.load_projects()

    def close_project(self) -> None:
        p = self._selected_project()
        if not p:
            return
        try:
            self.proj.set_project_status(p["id"], "termine")
        except Exception as e:
            messagebox.showerror("Erreur", str(e))
        self.load_projects()

    # ------------------------------------------------------------------ tâches
    def load_tasks(self, _e=None) -> None:
        p = self._selected_project()
        if not p:
            return
        self.task_tree.delete(*self.task_tree.get_children())
        for t in self.proj.tasks(p["id"]):
            self.task_tree.insert("", "end", values=(
                t["id"], t["title"],
                f"{t['first_name'] or ''} {t['last_name'] or ''}".strip(),
                t["due_date"], STATUS_LABELS.get(t["status"], t["status"])))

    def add_task(self) -> None:
        p = self._selected_project()
        if not p:
            return
        values = self.app._prompt_fields([
            ("title", "Tâche / livrable *"),
            ("due", "Échéance (AAAA-MM-JJ)"),
            ("notes", "Notes")])
        if not values or not values.get("title"):
            return
        try:
            self.proj.add_task(p["id"], values["title"],
                               values.get("due") or "",
                               notes=values.get("notes") or "")
        except Exception as e:
            messagebox.showerror("Erreur", str(e))
        self.load_tasks()
        self.load_projects()

    def finish_task(self) -> None:
        sel = self.task_tree.selection()
        if not sel:
            messagebox.showwarning("Sélection", "Sélectionnez une tâche.")
            return
        self.proj.set_task_status(self.task_tree.item(sel[0])["values"][0],
                                  "termine")
        self.load_tasks()
        self.load_projects()

    # ------------------------------------------------------------------ calendrier
    def _move_month(self, delta: int) -> None:
        m = self._cal_date.month + delta
        y = self._cal_date.year + (m - 1) // 12
        m = (m - 1) % 12 + 1
        self._cal_date = self._cal_date.replace(year=y, month=m)
        self.render_calendar()

    def render_calendar(self) -> None:
        for child in self.cal_grid.winfo_children():
            child.destroy()
        month = f"{self._cal_date.year}-{self._cal_date.month:02d}"
        self._cal_var.config(
            text=f"{calendar.month_name[self._cal_date.month]} "
                 f"{self._cal_date.year}")
        for col, day_name in enumerate(
                ("Lun", "Mar", "Mer", "Jeu", "Ven", "Sam", "Dim")):
            ttk.Label(self.cal_grid, text=day_name, font=("", 9, "bold")
                      ).grid(row=0, column=col, sticky="nwe", padx=1)
        events = self.proj.calendar_events(month)
        cal = calendar.Calendar(firstweekday=0)
        for row, week in enumerate(cal.monthdayscalendar(
                self._cal_date.year, self._cal_date.month), start=1):
            for col, day in enumerate(week):
                cell = ttk.Frame(self.cal_grid, relief="solid", borderwidth=1)
                cell.grid(row=row, column=col, sticky="nsew", padx=1, pady=1)
                self.cal_grid.columnconfigure(col, weight=1)
                self.cal_grid.rowconfigure(row, weight=1)
                if day == 0:
                    continue
                dstr = f"{month}-{day:02d}"
                ttk.Label(cell, text=str(day), font=("", 8)).pack(anchor="e")
                for ev in events:
                    start = ev["date"]
                    end = ev["end_date"] or ev["date"]
                    if start <= dstr <= end:
                        ttk.Label(cell, text=ev["label"], font=("", 7),
                                  wraplength=110, anchor="w"
                                  ).pack(anchor="w", fill="x")

    # ------------------------------------------------------------------ rappels
    def send_reminders(self) -> None:
        values = self.app._prompt_fields([
            ("emails", "Destinataires (emails séparés par des virgules, "
                       "vide = Paramètres)"),
            ("days", "Jours d'anticipation (défaut 7)")])
        recipients = values.get("emails") or "" if values else ""
        days = int(values.get("days") or 7) if values else 7
        report = self.proj.send_reminders(days_before=days,
                                          recipients=recipients)
        if report["errors"]:
            messagebox.showwarning(
                "Rappels",
                f"{report['sent']} envoyé(s), {report['skipped']} en attente "
                f"(SMTP non configuré).\nErreurs :\n"
                + "\n".join(report["errors"]))
        else:
            messagebox.showinfo(
                "Rappels",
                f"{report['sent']} rappel(s) envoyé(s), "
                f"{report['skipped']} enregistré(s) en attente "
                "(configurez SMTP dans Paramètres pour l'envoi).")
        self.render_calendar()
