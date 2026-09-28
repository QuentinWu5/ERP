"""Configurateur de variantes (inspiré Odoo product.template) :un produit générique + des options → l'ERP génère automatiquementl'article, la BOM et l'OF de chaque configuration.Exemple vanne : VANNE-BALL + DN (025/050/100) + corps (INOX/BRONZE)+ actionneur (MAN/PNEU) → SKU VANNE-BALL-050-INOX-PNEU."""
from __future__ import annotations

from typing import Optional

from erp.db.connection import Database
from erp.services.bom_service import BomService
from erp.services.inventory_service import InventoryService
from erp.services.settings_service import SettingsService


class ConfiguratorError(Exception):
    pass


class ConfiguratorService:
    def __init__(self, db: Database, inventory: InventoryService | None = None,
                 bom: BomService | None = None,
                 settings: SettingsService | None = None):
        self.db = db
        self.inventory = inventory or InventoryService(db)
        self.bom = bom or BomService(db, self.inventory)
        self.settings = settings or SettingsService(db)

    # ------------------------------------------------------------------ templates
    def create_template(self, sku_base: str, designation: str,
                        sale_base_price: float = 0) -> int:
        sku_base = sku_base.strip().upper()
        if not sku_base or not designation.strip():
            raise ConfiguratorError("SKU de base et désignation obligatoires")
        try:
            tid = self.db.execute(
                "INSERT INTO product_templates (sku_base, designation, "
                "sale_base_price) VALUES (?,?,?)",
                (sku_base, designation, sale_base_price))
        except Exception as e:
            raise ConfiguratorError(f"Gabarit impossible à créer : {e}")
        self.settings.audit("template.create", "product_templates", tid,
                            sku_base)
        return tid

    def get_template(self, tid: int) -> Optional[dict]:
        return self.db.query_one("SELECT * FROM product_templates WHERE id=?",
                                (tid,))

    def list_templates(self) -> list[dict]:
        return self.db.query("SELECT * FROM product_templates "
                             "WHERE active=1 ORDER BY sku_base")

    def add_base_component(self, tid: int, component_id: int,
                           qty_per: float = 1, scrap_pct: float = 0) -> None:
        if qty_per <= 0:
            raise ConfiguratorError("Quantité doit être > 0")
        try:
            self.db.execute(
                "INSERT INTO template_components (template_id, component_id, "
                "qty_per, scrap_pct) VALUES (?,?,?,?) "
                "ON CONFLICT(template_id, component_id) DO UPDATE SET "
                "qty_per=excluded.qty_per, scrap_pct=excluded.scrap_pct",
                (tid, component_id, qty_per, scrap_pct))
        except Exception as e:
            raise ConfiguratorError(f"Composant de base invalide : {e}")

    def add_option(self, tid: int, name: str, code: str,
                   position: int = 0) -> int:
        code = code.strip().upper()
        if not name.strip() or not code:
            raise ConfiguratorError("Nom et code d'option obligatoires")
        try:
            oid = self.db.execute(
                "INSERT INTO template_options (template_id, name, code, "
                "position) VALUES (?,?,?,?)", (tid, name, code, position))
        except Exception as e:
            raise ConfiguratorError(f"Option impossible : {e}")
        return oid

    def add_option_value(self, option_id: int, code: str, label: str,
                         component_id: Optional[int] = None,
                         qty_per: float = 1, price_extra: float = 0) -> int:
        code = code.strip().upper()
        if not code or not label.strip():
            raise ConfiguratorError("Code et libellé obligatoires")
        try:
            vid = self.db.execute(
                "INSERT INTO option_values (option_id, code, label, "
                "component_id, qty_per, price_extra) VALUES (?,?,?,?,?,?)",
                (option_id, code, label, component_id, qty_per, price_extra))
        except Exception as e:
            raise ConfiguratorError(f"Valeur d'option impossible : {e}")
        return vid

    def options(self, tid: int) -> list[dict]:
        opts = self.db.query(
            "SELECT * FROM template_options WHERE template_id=? "
            "ORDER BY position, id", (tid,))
        for o in opts:
            o["values"] = self.db.query(
                "SELECT ov.*, a.sku AS component_sku FROM option_values ov "
                "LEFT JOIN articles a ON a.id=ov.component_id "
                "WHERE ov.option_id=? ORDER BY ov.code", (o["id"],))
        return opts

    def base_components(self, tid: int) -> list[dict]:
        return self.db.query(
            "SELECT tc.*, a.sku, a.designation, a.purchase_price "
            "FROM template_components tc JOIN articles a "
            "ON a.id=tc.component_id WHERE tc.template_id=?", (tid,))

    # ------------------------------------------------------------------ configuration
    def configure(self, tid: int, choices: dict[str, int],
                  create: bool = True) -> dict:
        """Applique des choix {code_option: id_valeur} et crée l'article
        configuré + sa BOM + (option) l'OF. Retourne les détails."""
        tpl = self.get_template(tid)
        if not tpl:
            raise ConfiguratorError("Gabarit introuvable")
        opts = self.options(tid)
        by_code = {o["code"]: o for o in opts}
        unknown = [c for c in choices if c not in by_code]
        if unknown:
            raise ConfiguratorError(f"Options inconnues : {unknown}")
        missing_required = [o["code"] for o in opts
                            if o["id"] not in [v["id"] for v in o["values"]
                                               if v["id"] in choices.values()]
                            and o["code"] not in choices]
        sku_parts = [tpl["sku_base"]]
        label_parts: list[str] = []
        price = tpl["sale_base_price"]
        components: dict[int, tuple[float, float]] = {}
        for o in opts:
            vid = choices.get(o["code"])
            if not vid:
                continue
            value = next((v for v in o["values"] if v["id"] == vid), None)
            if not value:
                raise ConfiguratorError(
                    f"Valeur {vid} invalide pour l'option {o['code']}")
            sku_parts.append(value["code"])
            label_parts.append(f"{o['name']}={value['label']}")
            price += value["price_extra"]
            if value["component_id"]:
                comp = components.setdefault(
                    (value["component_id"], 1), [0, 0])
                comp[0] += value["qty_per"]
                comp[1] = max(comp[1], 0)
        for bc in self.base_components(tid):
            entry = components.setdefault((bc["component_id"], 0), [0, 0])
            entry[0] += bc["qty_per"]
            entry[1] = bc["scrap_pct"]
        sku = "-".join(sku_parts)
        designation = f"{tpl['designation']} ({', '.join(label_parts)})" \
            if label_parts else tpl["designation"]
        if not create:
            return {"sku": sku, "designation": designation,
                    "sale_price": round(price, 2),
                    "components": {aid: q for (aid, _), (q, _) in
                                   components.items()}}
        # article (réutilise s'il existe déjà)
        art = self.inventory.get_by_sku(sku)
        if art:
            article_id = art["id"]
            self.inventory.update_article(article_id, {
                "designation": designation, "sale_price": round(price, 2)})
        else:
            article_id = self.inventory.create_article({
                "sku": sku, "designation": designation,
                "type": "produit_fini", "sale_price": round(price, 2)})
        # BOM version A (une seule par configuration)
        bom_id = self.db.query_one(
            "SELECT id FROM boms WHERE parent_id=? AND active=1", (article_id,))
        if not bom_id:
            bom_id = self.bom.create_bom(article_id, "A")
        else:
            bom_id = bom_id["id"]
            for line in self.bom.lines(bom_id):
                self.bom.remove_line(line["id"])
        for (aid, _is_option), (qty, scrap) in components.items():
            self.bom.add_line(bom_id, aid, qty, scrap)
        self.bom.set_active_version(bom_id)
        self.settings.audit("configurator.configure", "articles",
                            article_id, sku)
        return {"article_id": article_id, "sku": sku,
                "designation": designation, "bom_id": bom_id,
                "sale_price": round(price, 2)}

    def create_wo_for(self, article_id: int, qty: float) -> dict:
        """Crée l'OF de la configuration (via le service Fabrication)."""
        from erp.services.manufacturing_service import ManufacturingService
        mfg = ManufacturingService(self.db, self.inventory, self.bom,
                                   self.settings)
        return mfg.create_wo(article_id, qty)
