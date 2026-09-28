"""Bloc Projets & Calendrier : projets (import depuis commandes clients),tâches/livrables, calendrier partagé (congés + échéances), rappels email SMTP."""
from __future__ import annotations

import smtplib
from datetime import date, timedelta
from email.message import EmailMessage
from typing import Optional

from erp.db.connection import Database
from erp.services.settings_service import SettingsService

REMINDER_DAYS_BEFORE = 7


class ProjectError(Exception):
    pass


class ProjectService:
    def __init__(self, db: Database, settings: SettingsService | None = None):
        self.db = db
        self.settings = settings or SettingsService(db)

    # ------------------------------------------------------------------ projets
    def create_project(self, name: str, customer_id: Optional[int] = None,
                       sales_order_id: Optional[int] = None,
                       start_date: str = "", due_date: str = "",
                       notes: str = "") -> dict:
        if not name.strip():
            raise ProjectError("Le nom du projet est obligatoire")
        number = self.settings.next_number("PRJ")
        pid = self.db.execute(
            "INSERT INTO projects (number, name, customer_id, sales_order_id, "
            "start_date, due_date, notes) VALUES (?,?,?,?,?,?,?)",
            (number, name, customer_id, sales_order_id, start_date, due_date,
             notes))
        self.settings.audit("project.create", "projects", pid, number)
        return {"id": pid, "number": number}

    def import_from_sales_order(self, order_id: int, task_per_line: bool = True,
                                include_services: bool = True) -> dict:
        """Crée un projet depuis une commande client : les lignes deviennent
        des livrables (tâches), l'échéance = date de livraison souhaitée."""
        order = self.db.query_one(
            "SELECT so.*, c.name AS customer_name FROM sales_orders so "
            "JOIN customers c ON c.id=so.customer_id WHERE so.id=?", (order_id,))
        if not order:
            raise ProjectError("Commande introuvable")
        if order["status"] == "annulee":
            raise ProjectError("Commande annulée")
        lines = self.db.query(
            "SELECT sol.*, a.sku, a.designation, a.type FROM sales_order_lines sol "
            "JOIN articles a ON a.id=sol.article_id WHERE sol.order_id=? "
            "ORDER BY sol.id", (order_id,))
        if not include_services:
            lines = [l for l in lines if l["type"] != "service"]
        project = self.create_project(
            f"Commande {order['number']} — {order['customer_name']}",
            customer_id=order["customer_id"], sales_order_id=order_id,
            due_date=order["desired_date"] or "",
            notes=f"Importé depuis la commande {order['number']}")
        created = 0
        for line in lines:
            if not task_per_line:
                break
            self.db.execute(
                "INSERT INTO project_tasks (project_id, title, due_date, notes) "
                "VALUES (?,?,?,?)",
                (project["id"],
                 f"Livrer {line['qty']:g} × {line['sku']} "
                 f"({line['designation']})",
                 order["desired_date"] or "",
                 f"Ligne de commande {order['number']}"))
            created += 1
        self.settings.audit("project.from_order", "projects", project["id"],
                            order["number"])
        return {"project_id": project["id"], "number": project["number"],
                "tasks": created}

    def get_project(self, pid: int) -> Optional[dict]:
        return self.db.query_one(
            "SELECT p.*, c.name AS customer_name, so.number AS order_number "
            "FROM projects p LEFT JOIN customers c ON c.id=p.customer_id "
            "LEFT JOIN sales_orders so ON so.id=p.sales_order_id "
            "WHERE p.id=?", (pid,))

    def list_projects(self, status: str = "") -> list[dict]:
        sql = ("SELECT p.*, c.name AS customer_name, "
               "(SELECT COUNT(*) FROM project_tasks t WHERE t.project_id=p.id "
               " AND t.status != 'termine' AND t.status != 'annule') AS open_tasks "
               "FROM projects p LEFT JOIN customers c ON c.id=p.customer_id "
               "WHERE 1=1")
        params: list = []
        if status:
            sql += " AND p.status=?"
            params.append(status)
        return self.db.query(sql + " ORDER BY p.id DESC", params)

    def set_project_status(self, pid: int, status: str) -> None:
        if status not in ("ouvert", "en_cours", "termine", "annule"):
            raise ProjectError("Statut inconnu")
        self.db.execute("UPDATE projects SET status=? WHERE id=?", (status, pid))
        self.settings.audit("project.status", "projects", pid, status)

    # ------------------------------------------------------------------ tâches
    def add_task(self, pid: int, title: str, due_date: str = "",
                 assigned_to: Optional[int] = None, notes: str = "") -> int:
        if not title.strip():
            raise ProjectError("Le titre de la tâche est obligatoire")
        tid = self.db.execute(
            "INSERT INTO project_tasks (project_id, title, assigned_to, "
            "due_date, notes) VALUES (?,?,?,?,?)",
            (pid, title, assigned_to, due_date, notes))
        self.settings.audit("task.create", "project_tasks", tid, title)
        return tid

    def tasks(self, pid: int) -> list[dict]:
        return self.db.query(
            "SELECT t.*, e.last_name, e.first_name FROM project_tasks t "
            "LEFT JOIN employees e ON e.id=t.assigned_to "
            "WHERE t.project_id=? ORDER BY t.due_date, t.id", (pid,))

    def set_task_status(self, tid: int, status: str) -> None:
        if status not in ("a_faire", "en_cours", "termine", "annule"):
            raise ProjectError("Statut inconnu")
        self.db.execute("UPDATE project_tasks SET status=? WHERE id=?",
                        (status, tid))
        self.settings.audit("task.status", "project_tasks", tid, status)

    def all_open_tasks(self) -> list[dict]:
        return self.db.query(
            "SELECT t.*, p.number AS project_number, p.name AS project_name "
            "FROM project_tasks t JOIN projects p ON p.id=t.project_id "
            "WHERE t.status IN ('a_faire','en_cours') AND t.due_date != '' "
            "ORDER BY t.due_date")

    # ------------------------------------------------------------------ calendrier
    def calendar_events(self, month: str) -> list[dict]:
        """Événements d'un mois (AAAA-MM) : congés approuvés, échéances
        projets, tâches, OF à finir."""
        start = f"{month}-01"
        y, m = int(month[:4]), int(month[5:7])
        last = date(y + (m == 12), (m % 12) + 1, 1) - timedelta(days=1)
        end = last.isoformat()
        events: list[dict] = []
        for lr in self.db.query(
                "SELECT lr.*, e.first_name, e.last_name FROM leave_requests lr "
                "JOIN employees e ON e.id=lr.employee_id "
                "WHERE lr.status='approuvee' AND lr.start_date <= ? "
                "AND lr.end_date >= ?", (end, start)):
            events.append({
                "kind": "conges", "date": lr["start_date"],
                "end_date": lr["end_date"],
                "label": f"🌴 {lr['first_name']} {lr['last_name']} "
                         f"({lr['days']:g} j)",
            })
        for p in self.db.query(
                "SELECT * FROM projects WHERE due_date BETWEEN ? AND ? "
                "AND status IN ('ouvert','en_cours')", (start, end)):
            events.append({"kind": "projet", "date": p["due_date"], "end_date": "",
                           "label": f"📦 {p['number']} — {p['name']}"})
        for t in self.db.query(
                "SELECT t.*, p.number FROM project_tasks t JOIN projects p "
                "ON p.id=t.project_id WHERE t.due_date BETWEEN ? AND ? "
                "AND t.status IN ('a_faire','en_cours')", (start, end)):
            events.append({"kind": "tache", "date": t["due_date"], "end_date": "",
                           "label": f"✅ {t['title']} ({t['number']})"})
        for so in self.db.query(
                "SELECT * FROM sales_orders WHERE desired_date BETWEEN ? AND ? "
                "AND status IN ('confirmee','en_production','prete')",
                (start, end)):
            events.append({"kind": "commande", "date": so["desired_date"],
                           "end_date": "",
                           "label": f"🚚 Livraison {so['number']}"})
        for wo in self.db.query(
                "SELECT * FROM work_orders WHERE status IN ('lance','en_cours') "
                "AND (finished_at IS NULL OR finished_at = '')"):
            events.append({"kind": "of", "date": "", "end_date": "",
                           "label": f"⚙️ OF {wo['number']} en cours"})
        return events

    # ------------------------------------------------------------------ rappels
    def _smtp_config(self) -> dict:
        return {
            "host": self.settings.get("smtp_host") or "",
            "port": int(self.settings.get("smtp_port") or 587),
            "user": self.settings.get("smtp_user") or "",
            "password": self.settings.get("smtp_password") or "",
            "from_addr": self.settings.get("smtp_from") or
                         self.settings.get("smtp_user") or "",
            "starttls": self.settings.get("smtp_starttls") != "0",
        }

    def send_email(self, to: str, subject: str, body: str) -> None:
        """Envoi via le serveur SMTP configuré dans les Paramètres."""
        cfg = self._smtp_config()
        if not cfg["host"] or not cfg["from_addr"] or not to:
            raise ProjectError(
                "SMTP non configuré (Paramètres : smtp_host, smtp_user, "
                "smtp_password, smtp_from) ou destinataire absent")
        msg = EmailMessage()
        msg["From"] = cfg["from_addr"]
        msg["To"] = to
        msg["Subject"] = subject
        msg.set_content(body)
        with smtplib.SMTP(cfg["host"], cfg["port"]) as server:
            if cfg["starttls"]:
                server.starttls()
            if cfg["user"]:
                server.login(cfg["user"], cfg["password"])
            server.send_message(msg)

    def _due_items(self, horizon: date) -> list[dict]:
        """Livrables et tâches à échéance dans les N prochains jours."""
        items: list[dict] = []
        today = date.today()
        for p in self.db.query(
                "SELECT p.*, c.name AS customer_name FROM projects p LEFT JOIN "
                "customers c ON c.id=p.customer_id WHERE p.due_date != '' "
                "AND p.status IN ('ouvert','en_cours')"):
            due = date.fromisoformat(p["due_date"])
            if today <= due <= horizon:
                items.append({"kind": "projet", "id": p["id"],
                              "due": p["due_date"], "label": p["name"],
                              "who": p["customer_name"] or ""})
        for t in self.db.query(
                "SELECT t.*, p.name AS project_name FROM project_tasks t JOIN "
                "projects p ON p.id=t.project_id WHERE t.due_date != '' AND "
                "t.status IN ('a_faire','en_cours')"):
            due = date.fromisoformat(t["due_date"])
            if today <= due <= horizon:
                items.append({"kind": "tache", "id": t["id"],
                              "due": t["due_date"], "label": t["title"],
                              "who": t["project_name"]})
        return items

    def pending_reminders(self, days_before: int = REMINDER_DAYS_BEFORE) -> list[dict]:
        """Rappels à générer : échéances dans les N jours sans rappel envoyé."""
        horizon = date.today() + timedelta(days=days_before)
        result = []
        for item in self._due_items(horizon):
            already = self.db.query_one(
                "SELECT id FROM reminders WHERE kind=? AND ref_id=?",
                (item["kind"], item["id"]))
            if not already:
                result.append(item)
        return result

    def send_reminders(self, days_before: int = REMINDER_DAYS_BEFORE,
                       recipients: str = "") -> dict:
        """Génère et envoie les rappels email. Retourne un rapport."""
        cfg_ok = bool(self.settings.get("smtp_host"))
        recipient_list = [r.strip() for r in
                          (recipients or self.settings.get("reminder_emails")
                       or "").split(",") if r.strip()]
        report = {"sent": 0, "skipped": 0, "errors": []}
        for item in self.pending_reminders(days_before):
            self.db.execute(
                "INSERT OR IGNORE INTO reminders (kind, ref_id, due_date, "
                "label, recipients) VALUES (?,?,?,?,?)",
                (item["kind"], item["id"], item["due"], item["label"],
                 ", ".join(recipient_list)))
            if not cfg_ok or not recipient_list:
                report["skipped"] += 1
                continue
            subject = (f"[ERP] Rappel : {item['label']} — échéance "
                       f"{item['due']}")
            body = (f"Bonjour,\n\nRappel automatique de l'ERP :\n\n"
                    f"  {item['label']}\n"
                    f"  Échéance : {item['due']}\n"
                    f"  {'Client : ' + item['who'] if item['who'] else ''}\n\n"
                    f"Cordialement,\nL'ERP")
            try:
                self.send_email(", ".join(recipient_list), subject, body)
                self.db.execute(
                    "UPDATE reminders SET sent=1, "
                    "sent_at=datetime('now','localtime') WHERE kind=? AND "
                    "ref_id=?", (item["kind"], item["id"]))
                report["sent"] += 1
            except Exception as e:
                report["errors"].append(f"{item['label']} : {e}")
        self.settings.audit("reminders.send", "reminders", "",
                            f"{report['sent']} envoyé(s)")
        return report
