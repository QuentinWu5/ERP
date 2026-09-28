"""Bloc Nomenclature : BOM multi-niveaux, versions, variants, coût standard,
explosion et 'où utilisé ?'."""
from __future__ import annotations

import csv
import io
from typing import Optional

from erp.db.connection import Database
from erp.services.inventory_service import InventoryService
from erp.services.settings_service import SettingsService


class BomError(Exception):
    pass


class BomService:
    def __init__(self, db: Database, inventory: InventoryService | None = None,
                 settings: SettingsService | None = None):
        self.db = db
        self.inventory = inventory or InventoryService(db)
        self.settings = settings or SettingsService(db)

    # ------------------------------------------------------------------ CRUD
    def create_bom(self, parent_id: int, version: str = "A",
                   variant: str = "") -> int:
        parent = self.inventory.get_article(parent_id)
        if not parent:
            raise BomError("Produit parent introuvable")
        with self.db.transaction():
            self.db.execute("UPDATE boms SET active=0 WHERE parent_id=?",
                            (parent_id,))
            bom_id = self.db.execute(
                "INSERT INTO boms (parent_id, version, variant) VALUES (?,?,?)",
                (parent_id, version, variant))
        self.settings.audit("bom.create", "boms", bom_id,
                            f"{parent['sku']} v{version}")
        return bom_id

    def get_active_bom(self, parent_id: int, version: str = "",
                       variant: str = "") -> Optional[dict]:
        sql = "SELECT * FROM boms WHERE parent_id=? AND active=1"
        params: list = [parent_id]
        if version:
            sql += " AND version=?"
            params.append(version)
        if variant:
            sql += " AND variant=?"
            params.append(variant)
        sql += " ORDER BY id DESC LIMIT 1"
        return self.db.query_one(sql, params)

    def set_active_version(self, bom_id: int) -> None:
        bom = self.db.query_one("SELECT * FROM boms WHERE id=?", (bom_id,))
        with self.db.transaction():
            self.db.execute("UPDATE boms SET active=0 WHERE parent_id=?",
                            (bom["parent_id"],))
            self.db.execute("UPDATE boms SET active=1 WHERE id=?", (bom_id,))

    def list_boms(self, parent_id: Optional[int] = None) -> list[dict]:
        sql = ("SELECT b.*, a.sku, a.designation, "
               "(SELECT COUNT(*) FROM bom_lines bl WHERE bl.bom_id=b.id) AS n_lines "
               "FROM boms b JOIN articles a ON a.id=b.parent_id")
        params: list = []
        if parent_id:
            sql += " WHERE b.parent_id=?"
            params.append(parent_id)
        sql += " ORDER BY a.sku, b.version"
        return self.db.query(sql, params)

    def lines(self, bom_id: int) -> list[dict]:
        return self.db.query(
            "SELECT bl.*, a.sku, a.designation, a.type, a.unit, "
                   "a.purchase_price, a.stock_qty, a.reserved_qty "
            "FROM bom_lines bl JOIN articles a ON a.id=bl.component_id "
            "WHERE bl.bom_id=? ORDER BY a.sku", (bom_id,))

    def add_line(self, bom_id: int, component_id: int, qty_per: float,
                 scrap_pct: float = 0.0) -> int:
        if qty_per <= 0:
            raise BomError("Quantité doit être > 0")
        bom = self.db.query_one("SELECT * FROM boms WHERE id=?", (bom_id,))
        if not bom:
            raise BomError("Nomenclature introuvable")
        if bom["parent_id"] == component_id:
            raise BomError("Un produit ne peut être son propre composant")
        self._check_cycle(bom["parent_id"], component_id)
        return self.db.execute(
            "INSERT INTO bom_lines (bom_id, component_id, qty_per, scrap_pct) "
            "VALUES (?,?,?,?) "
            "ON CONFLICT(bom_id, component_id) DO UPDATE SET "
            "qty_per=excluded.qty_per, scrap_pct=excluded.scrap_pct",
            (bom_id, component_id, qty_per, scrap_pct))

    def remove_line(self, line_id: int) -> None:
        self.db.execute("DELETE FROM bom_lines WHERE id=?", (line_id,))

    def _check_cycle(self, parent_id: int, component_id: int) -> None:
        """Empêche les cycles : parcourt les composants en aval du composant
        ajouté ; si le parent y figure, il y a cycle."""
        seen = set()
        stack = [component_id]
        while stack:
            cid = stack.pop()
            if cid == parent_id:
                raise BomError("Cycle détecté dans la nomenclature")
            if cid in seen:
                continue
            seen.add(cid)
            for r in self.db.query(
                "SELECT bl.component_id FROM bom_lines bl "
                "JOIN boms b ON b.id=bl.bom_id "
                "WHERE b.parent_id=?", (cid,)):
                stack.append(r["component_id"])

    # ------------------------------------------------------------------ calculs
    def standard_cost(self, parent_id: int, version: str = "",
                      variant: str = "") -> float:
        total = 0.0
        for line in self.lines(self.get_active_bom(parent_id, version, variant)["id"]):
            needed = line["qty_per"] * (1 + line["scrap_pct"] / 100)
            total += needed * line["purchase_price"]
        return round(total, 4)

    def explode(self, parent_id: int, qty: float = 1.0, version: str = "",
                variant: str = "", _level: int = 0,
                _seen: Optional[set] = None) -> list[dict]:
        """Déroulé complet multi-niveaux avec quantités totales."""
        _seen = _seen or set()
        if parent_id in _seen:
            raise BomError("Cycle détecté dans la nomenclature")
        _seen = _seen | {parent_id}
        result: list[dict] = []
        bom = self.get_active_bom(parent_id, version, variant)
        if not bom:
            return result
        for line in self.lines(bom["id"]):
            need = line["qty_per"] * (1 + line["scrap_pct"] / 100) * qty
            result.append({
                "level": _level, "sku": line["sku"],
                "designation": line["designation"],
                "article_id": line["component_id"], "qty_total": round(need, 4),
                "unit": line["unit"],
                "stock_qty": line["stock_qty"],
                "available_qty": line["stock_qty"] - line["reserved_qty"],
            })
            result.extend(self.explode(line["component_id"], need,
                                       _level=_level + 1, _seen=_seen))
        return result

    def explode_flat(self, parent_id: int, qty: float = 1.0) -> dict[int, float]:
        """Besoin net agrégé par composant (feuilles et intermédiaires)."""
        flat: dict[int, float] = {}
        for row in self.explode(parent_id, qty):
            flat[row["article_id"]] = round(
                flat.get(row["article_id"], 0) + row["qty_total"], 4)
        return flat

    def where_used(self, component_id: int) -> list[dict]:
        """Liste des parents (tous niveaux) utilisant ce composant."""
        parents: dict[int, dict] = {}
        stack = [component_id]
        while stack:
            cid = stack.pop()
            for r in self.db.query(
                "SELECT DISTINCT b.parent_id AS pid, a.sku, a.designation "
                "FROM bom_lines bl JOIN boms b ON b.id=bl.bom_id "
                "JOIN articles a ON a.id=b.parent_id "
                "WHERE bl.component_id=? AND b.active=1", (cid,)):
                if r["pid"] not in parents:
                    parents[r["pid"]] = r
                    stack.append(r["pid"])
        return list(parents.values())

    # ------------------------------------------------------------------ import CSV
    def import_bom_csv(self, csv_text: str) -> dict:
        """Colonnes : produit;version;composant;quantite;rebuts_pct"""
        rows = list(csv.DictReader(io.StringIO(csv_text), delimiter=";"))
        if not rows:
            raise BomError("Fichier CSV vide ou en-têtes manquantes")
        report = {"ok": 0, "errors": []}
        cache: dict[str, int] = {}
        for i, row in enumerate(rows, start=2):
            try:
                p_sku = (row.get("produit") or "").strip()
                c_sku = (row.get("composant") or "").strip()
                for sku in (p_sku, c_sku):
                    if sku not in cache:
                        art = self.inventory.get_by_sku(sku)
                        if not art:
                            raise ValueError(f"SKU inconnu : {sku}")
                        cache[sku] = art["id"]
                version = (row.get("version") or "A").strip()
                bom = self.db.query_one(
                    "SELECT * FROM boms WHERE parent_id=? AND version=? AND variant=''",
                    (cache[p_sku], version))
                if bom:
                    bom_id = bom["id"]
                else:
                    bom_id = self.create_bom(cache[p_sku], version)
                self.add_line(bom_id, cache[c_sku],
                              float(row.get("quantite") or 0),
                              float(row.get("rebuts_pct") or 0))
                report["ok"] += 1
            except Exception as e:
                report["errors"].append(f"Ligne {i} : {e}")
        self.settings.audit("bom.import", "boms", "", f"{report['ok']} lignes")
        return report
