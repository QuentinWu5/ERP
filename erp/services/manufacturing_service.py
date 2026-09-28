"""Bloc Fabrication : OF, contrôle composants, réservation/consommation,
gammes opératoires, plans."""
from __future__ import annotations

from typing import Optional

from erp.db.connection import Database
from erp.services.bom_service import BomService
from erp.services.inventory_service import InventoryService, StockError
from erp.services.settings_service import SettingsService

WO_STATUSES = ["planifie", "lance", "en_cours", "termine", "cloture"]


class ManufacturingError(Exception):
    pass


class ManufacturingService:
    def __init__(self, db: Database, inventory: InventoryService | None = None,
                 bom: BomService | None = None,
                 settings: SettingsService | None = None):
        self.db = db
        self.inventory = inventory or InventoryService(db)
        self.bom = bom or BomService(db, self.inventory)
        self.settings = settings or SettingsService(db)

    # ------------------------------------------------------------------ OF CRUD
    def create_wo(self, article_id: int, qty: float,
                  sales_order_id: Optional[int] = None,
                  notes: str = "") -> dict:
        art = self.inventory.get_article(article_id)
        if not art:
            raise ManufacturingError("Article introuvable")
        if qty <= 0:
            raise ManufacturingError("Quantité doit être > 0")
        bom = self.bom.get_active_bom(article_id)
        if not bom:
            raise ManufacturingError(
                f"Pas de nomenclature active pour {art['sku']} : "
                "créez une BOM avant de lancer un OF")
        number = self.settings.next_number("OF", with_year=False)
        wo_id = self.db.execute(
            "INSERT INTO work_orders (number, article_id, bom_id, qty_to_produce, "
            "sales_order_id, notes) VALUES (?,?,?,?,?,?)",
            (number, article_id, bom["id"], qty, sales_order_id, notes))
        self.settings.audit("wo.create", "work_orders", wo_id, number)
        return {"id": wo_id, "number": number}

    def get_wo(self, wo_id: int) -> Optional[dict]:
        return self.db.query_one(
            "SELECT wo.*, a.sku, a.designation FROM work_orders wo "
            "JOIN articles a ON a.id=wo.article_id WHERE wo.id=?", (wo_id,))

    def list_wos(self, status: str = "") -> list[dict]:
        sql = ("SELECT wo.*, a.sku, a.designation, "
               "(SELECT so.number FROM sales_orders so "
               " WHERE so.id=wo.sales_order_id) AS order_number "
               "FROM work_orders wo JOIN articles a ON a.id=wo.article_id WHERE 1=1")
        params: list = []
        if status:
            sql += " AND wo.status=?"
            params.append(status)
        return self.db.query(sql + " ORDER BY wo.id DESC", params)

    # ------------------------------------------------------------------ besoins
    def requirements(self, wo_id: int) -> list[dict]:
        """Besoins nets de l'OF : explosion BOM × qté, moins le stock disponible
        (en masse, via explode_flat pour agréger les sous-niveaux)."""
        wo = self.get_wo(wo_id)
        if not wo:
            raise ManufacturingError("OF introuvable")
        flat = self._wo_flat(wo)
        result = []
        for aid, need in flat.items():
            art = self.inventory.get_article(aid)
            avail = art["stock_qty"] - art["reserved_qty"]
            result.append({
                "article_id": aid, "sku": art["sku"],
                "designation": art["designation"], "unit": art["unit"],
                "qty_needed": need, "qty_available": avail,
                "qty_missing": round(max(0, need - avail), 4),
            })
        return result

    # ------------------------------------------------------------------ lancement
    def check_and_launch(self, wo_id: int) -> dict:
        """Contrôle de disponibilité puis lancement :
        - réserve les composants disponibles ;
        - selon of_missing_policy : 'warn' → avertissement + suggestion d'achat,
          'block' → lève une erreur.
        Retourne {'missing': [...], 'suggested_po': {...} ou None}."""
        wo = self.get_wo(wo_id)
        if not wo:
            raise ManufacturingError("OF introuvable")
        if wo["status"] != "planifie":
            raise ManufacturingError(f"OF déjà lancé ({wo['status']})")
        reqs = self.requirements(wo_id)
        missing = [r for r in reqs if r["qty_missing"] > 1e-9]
        policy = self.settings.get("of_missing_policy") or "warn"
        if missing and policy == "block":
            raise ManufacturingError(
                "Composants manquants (politique bloquante) : "
                + ", ".join(f"{m['sku']} (-{m['qty_missing']})" for m in missing))
        with self.db.transaction():
            for r in reqs:
                to_reserve = min(r["qty_needed"], r["qty_available"])
                if to_reserve > 1e-9:
                    self.inventory.reserve(r["article_id"], to_reserve)
            self.db.execute(
                "UPDATE work_orders SET status='lance', "
                "launched_at=datetime('now','localtime') WHERE id=?", (wo_id,))
            if wo["sales_order_id"]:
                self.db.execute(
                    "UPDATE sales_orders SET status='en_production' "
                    "WHERE id=? AND status IN ('confirmee')",
                    (wo["sales_order_id"],))
            self.settings.audit("wo.launch", "work_orders", wo_id, wo["number"])
        return {"missing": missing,
                "suggestion": [(m["article_id"], m["qty_missing"]) for m in missing]}

    # ------------------------------------------------------------------ fin d'OF
    def report_production(self, wo_id: int, qty_produced: float,
                          qty_scrap: float = 0.0,
                          actual_consumption: Optional[dict[int, float]] = None
                          ) -> dict:
        """Termine l'OF : consomme les composants (réel vs théorique),
        libère les réservations, entre le produit fini en stock.
        Retourne les écarts théorique/réel."""
        wo = self.get_wo(wo_id)
        if not wo:
            raise ManufacturingError("OF introuvable")
        if wo["status"] in ("termine", "cloture"):
            raise ManufacturingError("OF déjà terminé")
        if qty_produced <= 0:
            raise ManufacturingError("Quantité produite doit être > 0")
        reqs = self.requirements(wo_id)
        ratio = qty_produced / wo["qty_to_produce"]
        consumption: dict[int, float] = {}
        for r in reqs:
            actual = (actual_consumption or {}).get(
                r["article_id"], r["qty_needed"] * ratio)
            consumption[r["article_id"]] = actual
        with self.db.transaction():
            for aid, actual in consumption.items():
                art = self.inventory.get_article(aid)
                actual_capped = min(actual, art["stock_qty"])
                if actual_capped > 1e-9:
                    self.inventory.exit_(
                        aid, actual_capped, reason=f"Consommation OF {wo['number']}",
                        source_doc=wo["number"])
                self.inventory.unreserve(aid, actual)
                self.db.execute(
                    "INSERT INTO work_order_consumptions "
                    "(wo_id, article_id, qty_theoretical, qty_consumed) "
                    "VALUES (?,?,?,?) ON CONFLICT(wo_id, article_id) DO UPDATE "
                    "SET qty_consumed=excluded.qty_consumed",
                    (wo_id, aid, r_for(reqs, aid) * ratio, actual))
            # entrée du produit fini (coût = CMUP pondéré des composants consommés)
            cost = self._finished_cost(wo, consumption, qty_produced, qty_scrap)
            self.inventory.entry(
                wo["article_id"], qty_produced, cost,
                reason=f"Production OF {wo['number']}", source_doc=wo["number"])
            self.db.execute(
                "UPDATE work_orders SET status='termine', qty_produced=?, "
                "qty_scrap=?, finished_at=datetime('now','localtime') "
                "WHERE id=?", (qty_produced, qty_scrap, wo_id))
            if wo["sales_order_id"]:
                self.db.execute(
                    "UPDATE sales_orders SET status='prete' "
                    "WHERE id=? AND status='en_production'",
                    (wo["sales_order_id"],))
            self.settings.audit("wo.finish", "work_orders", wo_id, wo["number"])
        return self.deviations(wo_id)

    def _wo_flat(self, wo: dict) -> dict[int, float]:
        """Besoins agrégés selon la BOM enregistrée sur l'OF."""
        flat: dict[int, float] = {}
        stack = [(wo["article_id"], wo["qty_to_produce"], wo["bom_id"])]
        while stack:
            aid, qty, bom_id = stack.pop()
            if bom_id is None:
                bom = self.bom.get_active_bom(aid)
            else:
                bom = self.db.query_one("SELECT * FROM boms WHERE id=?", (bom_id,))
            if not bom:
                continue
            for line in self.db.query(
                    "SELECT * FROM bom_lines WHERE bom_id=?", (bom["id"],)):
                need = line["qty_per"] * (1 + line["scrap_pct"] / 100) * qty
                flat[line["component_id"]] = round(
                    flat.get(line["component_id"], 0) + need, 4)
                sub = self.db.query_one(
                    "SELECT id FROM boms WHERE parent_id=? AND active=1 "
                    "ORDER BY id DESC", (line["component_id"],))
                if sub:
                    stack.append((line["component_id"], need, sub["id"]))
        return flat

    def _finished_cost(self, wo: dict, consumption: dict[int, float],
                       qty_produced: float, qty_scrap: float) -> float:
        total = 0.0
        for aid, qty in consumption.items():
            art = self.inventory.get_article(aid)
            total += qty * (art["cmup"] or art["purchase_price"])
        out = qty_produced + qty_scrap
        return round(total / out, 4) if out > 0 else 0.0

    def deviations(self, wo_id: int) -> list[dict]:
        return self.db.query(
            "SELECT woc.*, a.sku, a.designation FROM work_order_consumptions woc "
            "JOIN articles a ON a.id=woc.article_id WHERE woc.wo_id=? "
            "ORDER BY a.sku", (wo_id,))

    def close_wo(self, wo_id: int) -> None:
        self.db.execute(
            "UPDATE work_orders SET status='cloture' WHERE id=? AND "
            "status='termine'", (wo_id,))
        self.settings.audit("wo.close", "work_orders", wo_id)

    # ------------------------------------------------------------------ gammes
    def set_routing(self, article_id: int, steps: list[dict]) -> None:
        with self.db.transaction():
            self.db.execute("DELETE FROM routings WHERE article_id=?", (article_id,))
            for i, s in enumerate(steps, start=1):
                self.db.execute(
                    "INSERT INTO routings (article_id, step_no, description, "
                    "estimated_time, tooling, plan_ref) VALUES (?,?,?,?,?,?)",
                    (article_id, s.get("step_no", i), s.get("description", ""),
                     s.get("estimated_time", ""), s.get("tooling", ""),
                     s.get("plan_ref", "")))

    def get_routing(self, article_id: int) -> list[dict]:
        return self.db.query(
            "SELECT * FROM routings WHERE article_id=? ORDER BY step_no",
            (article_id,))

    # ------------------------------------------------------------------ plans
    def add_plan(self, article_id: int, number: str, version: str = "A",
                 file_path: str = "", notes: str = "") -> int:
        pid = self.db.execute(
            "INSERT INTO plans (article_id, number, version, file_path, notes) "
            "VALUES (?,?,?,?,?) "
            "ON CONFLICT(article_id, number, version) DO UPDATE SET "
            "file_path=excluded.file_path, notes=excluded.notes",
            (article_id, number, version, file_path, notes))
        self.settings.audit("plan.add", "plans", pid, number)
        return pid

    def plans(self, article_id: int) -> list[dict]:
        return self.db.query(
            "SELECT * FROM plans WHERE article_id=? ORDER BY number, version",
            (article_id,))


def r_for(reqs: list[dict], aid: int) -> float:
    for r in reqs:
        if r["article_id"] == aid:
            return r["qty_needed"]
    return 0.0
