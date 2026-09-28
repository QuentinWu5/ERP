"""Jeu de démonstration + données initiales (société, modules, compteur)."""
from __future__ import annotations

from erp.db.connection import Database
from erp.services.inventory_service import InventoryService
from erp.services.bom_service import BomService
from erp.services.settings_service import SettingsService, seed


def seed_demo(db: Database) -> None:
    seed(db)
    settings = SettingsService(db)
    inv = InventoryService(db, settings)
    bom = BomService(db, inv, settings)

    settings.set("company_name", "Atelier Exemple SARL")
    settings.set("company_address", "12 rue des Fabrics, 75000 Paris")
    settings.set("company_siret", "12345678900012")

    if inv.get_by_sku("VIS-M5"):
        return  # déjà initialisé

    # Composants et consommables
    articles = [
        ("VIS-M5", "Vis M5 inox", "composant", "pce", 0.08, 0, 1000, "MAG/A1", 200),
        ("BOITIER-ALU", "Boîtier aluminium", "composant", "pce", 12.50, 0, 50, "MAG/B2", 20),
        ("PCB-CTRL", "Carte contrôleur", "composant", "pce", 22.00, 0, 30, "MAG/B1", 10),
        ("CABLE-2M", "Câble 2 m", "composant", "pce", 3.20, 0, 200, "MAG/A2", 50),
        ("EMBAL-K7", "Emballage carton", "consommable", "pce", 1.10, 0, 400, "MAG/C1", 100),
        ("ETIQ-BAR", "Étiquette code-barres", "consommable", "pce", 0.15, 0, 500, "MAG/C1", 100),
        ("CAP-STD", "Capot standard", "marchandise", "pce", 8.00, 12.00, 80, "MAG/D1", 20),
        ("MAINT-1H", "Heure de maintenance", "service", "h", 0, 45.00, 0, "", 0),
        ("CTRL-100", "Boîtier de contrôle 100", "produit_fini", "pce", 0, 149.00, 5, "MAG/PF", 0),
    ]
    ids: dict[str, int] = {}
    for sku, des, typ, unit, pa, pv, qty, loc, mini in articles:
        aid = inv.create_article({
            "sku": sku, "designation": des, "type": typ, "unit": unit,
            "purchase_price": pa, "sale_price": pv, "min_stock": mini,
        })
        ids[sku] = aid
        if qty:
            inv.entry(aid, qty, pa, move_type="reprise_initiale",
                      reason="Stock initial de démonstration",
                      location_to=loc, source_doc="DEMO")

    # Nomenclatures
    b_ctrl = bom.create_bom(ids["CTRL-100"], "A")
    bom.add_line(b_ctrl, ids["BOITIER-ALU"], 1, 2)
    bom.add_line(b_ctrl, ids["PCB-CTRL"], 1, 0)
    bom.add_line(b_ctrl, ids["VIS-M5"], 8, 5)
    bom.add_line(b_ctrl, ids["CABLE-2M"], 1, 0)
    bom.add_line(b_ctrl, ids["EMBAL-K7"], 1, 0)
    bom.add_line(b_ctrl, ids["ETIQ-BAR"], 2, 0)

    # Postes de travail + gabarit vanne de démonstration (configurateur)
    from erp.services.manufacturing_service import ManufacturingService
    from erp.services.configurator_service import ConfiguratorService
    mfg = ManufacturingService(db, inv, bom, settings)
    for code, name in (("SOUD", "Soudure"), ("USIN", "Usinage"),
                       ("ASSE", "Assemblage"), ("TEST", "Test & contrôle")):
        try:
            mfg.create_work_center(code, name)
        except Exception:
            pass
    if not db.query_one("SELECT id FROM product_templates "
                        "WHERE sku_base='VANNE-BALL'"):
        cfg = ConfiguratorService(db, inv, bom, settings)
        tid = cfg.create_template("VANNE-BALL", "Vanne à boisselet", 200)
        cfg.add_base_component(tid, ids["VIS-M5"], 8, 5)
        dn = cfg.add_option(tid, "Diamètre nominal", "DN", 1)
        cfg.add_option_value(dn, "025", "DN25")
        cfg.add_option_value(dn, "050", "DN50", price_extra=20)
        cfg.add_option_value(dn, "100", "DN100", price_extra=55)
        corps = {}
        for sku, des, prix in (
                ("CORPS-INOX", "Corps inox 316", 45),
                ("CORPS-BRONZE", "Corps bronze", 38)):
            if sku not in ids:
                ids[sku] = inv.create_article(
                    {"sku": sku, "designation": des, "type": "composant",
                     "purchase_price": prix})
                inv.entry(ids[sku], 20, prix, move_type="reprise_initiale",
                          reason="Stock initial de démonstration",
                          source_doc="DEMO")
            corps[sku] = ids[sku]
        mat = cfg.add_option(tid, "Matière corps", "MAT", 2)
        cfg.add_option_value(mat, "INOX", "Inox 316",
                             component_id=corps["CORPS-INOX"])
        cfg.add_option_value(mat, "BRONZE", "Bronze",
                             component_id=corps["CORPS-BRONZE"])
        act = cfg.add_option(tid, "Actionneur", "ACT", 3)
        cfg.add_option_value(act, "MAN", "Manuel", price_extra=12)
        cfg.add_option_value(act, "PNEU", "Pneumatique", price_extra=60)
