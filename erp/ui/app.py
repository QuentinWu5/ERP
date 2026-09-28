"""UI Tkinter : fenêtre à onglets par bloc.

Aucune logique métier ici : les callbacks appellent les services
et affichent le résultat (préparation bascule web P7).
"""
from __future__ import annotations

import csv
import io
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter import simpledialog

from erp.db.connection import Database
from erp.services.bom_service import BomService
from erp.services.dashboard_service import DashboardService
from erp.services.inventory_service import InventoryService
from erp.services.invoicing_service import InvoicingService
from erp.services.manufacturing_service import ManufacturingService
from erp.services.purchasing_service import PurchasingService
from erp.services.sales_service import SalesService
from erp.services.settings_service import SettingsService
from erp.services.hr_service import HrService
from erp.services.configurator_service import ConfiguratorService
from erp.services.project_service import ProjectService
from erp.services.warehouse_service import WarehouseService
from erp.ui.app_modules import MODULES, MODULE_LABELS
from erp.ui.flux_views import (DashboardTab, InvoicingTab, ManufacturingTab,
                               PurchasingTab, SalesTab, WarehouseTab)
from erp.ui.hr_view import HrTab
from erp.ui.production_view import ProductionTab
from erp.ui.project_view import ProjectsTab


class App(tk.Tk):
    def __init__(self, db: Database):
        super().__init__()
        self.title("ERP modulaire — Phase 1")
        self.geometry("1100x650")
        self.db = db
        self.settings = SettingsService(db)
        self.inventory = InventoryService(db, self.settings)
        self.bom = BomService(db, self.inventory, self.settings)
        self.sales = SalesService(db, self.inventory, self.settings)
        self.mfg = ManufacturingService(db, self.inventory, self.bom, self.settings)
        self.purchasing = PurchasingService(db, self.inventory, self.settings)
        self.warehouse = WarehouseService(db, self.inventory, self.sales, self.settings)
        self.invoicing = InvoicingService(db, self.sales, self.settings)
        self.hr = HrService(db, self.settings)
        self.projects = ProjectService(db, self.settings)
        self.configurator = ConfiguratorService(db, self.inventory, self.bom,
                                                 self.settings)
        self.dashboard = DashboardService(db, self.settings, self.inventory,
                                           self.sales, self.mfg, self.purchasing,
                                           self.invoicing, self.bom)

        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True)
        self.tabs: dict[str, ttk.Frame] = {}
        self.tab_widgets: dict[str, object] = {}
        for m in MODULES:
            if self.settings.module_enabled(m):
                self.add_module(m)

        self._add_menu()

    def add_module(self, module: str) -> None:
        frame = ttk.Frame(self.notebook, padding=8)
        self.tabs[module] = frame
        self.notebook.add(frame, text=MODULE_LABELS[module])
        if module == "settings":
            self._build_settings(frame)
        elif module == "inventory":
            self._build_inventory(frame)
        elif module == "bom":
            self._build_bom(frame)
        elif module == "sales":
            self.tab_widgets[module] = SalesTab(self, frame, self.sales, self.inventory)
        elif module == "manufacturing":
            self.tab_widgets[module] = ManufacturingTab(self, frame, self.mfg)
        elif module == "purchasing":
            self.tab_widgets[module] = PurchasingTab(self, frame, self.purchasing)
        elif module == "warehouse":
            self.tab_widgets[module] = WarehouseTab(self, frame, self.warehouse, self.sales)
        elif module == "invoicing":
            self.tab_widgets[module] = InvoicingTab(self, frame, self.invoicing, self.sales)
        elif module == "hr":
            self.tab_widgets[module] = HrTab(self, frame, self.hr)
        elif module == "projects":
            self.tab_widgets[module] = ProjectsTab(self, frame, self.projects,
                                                   self.sales)
        elif module == "production":
            self.tab_widgets[module] = ProductionTab(self, frame,
                                                     self.configurator,
                                                     self.mfg)
        elif module == "dashboard":
            self.tab_widgets[module] = DashboardTab(self, frame, self.dashboard)

    def refresh_tabs(self) -> None:
        current = self.notebook.index(self.notebook.select()) \
            if self.notebook.tabs() else 0
        self.notebook.pack_forget()
        self.notebook.destroy()
        self.notebook = ttk.Notebook(self)
        self.tabs = {}
        self.tab_widgets = {}
        for m in MODULES:
            if self.settings.module_enabled(m):
                self.add_module(m)
        self.notebook.pack(fill="both", expand=True)
        if self.notebook.tabs():
            self.notebook.select(min(current, len(self.notebook.tabs()) - 1))

    def _add_menu(self) -> None:
        bar = tk.Menu(self)
        menu_f = tk.Menu(bar, tearoff=0)
        menu_f.add_command(label="Sauvegarde de la base…", command=self._backup)
        menu_f.add_separator()
        menu_f.add_command(label="Quitter", command=self.destroy)
        bar.add_cascade(label="Fichier", menu=menu_f)
        menu_i = tk.Menu(bar, tearoff=0)
        menu_i.add_command(label="Importer articles (CSV)…", command=self._import_articles)
        menu_i.add_command(label="Importer nomenclatures (CSV)…", command=self._import_bom)
        menu_i.add_command(label="Exporter état de stock (CSV)…", command=self._export_stock)
        bar.add_cascade(label="Import / Export", menu=menu_i)
        self.config(menu=bar)

    def _backup(self) -> None:
        from datetime import datetime
        from erp.db.connection import get_db_path
        name = f"erp_backup_{datetime.now():%Y%m%d_%H%M%S}.db"
        path = filedialog.asksaveasfilename(defaultextension=".db",
                                             initialfile=name)
        if not path:
            return
        src = str(get_db_path())
        import sqlite3
        s = sqlite3.connect(src)
        d = sqlite3.connect(path)
        with d:
            s.backup(d)
        s.close()
        d.close()
        messagebox.showinfo("Sauvegarde", f"Base sauvegardée :\n{path}")

    # ------------------------------------------------------------------ Paramètres
    def _build_settings(self, parent: ttk.Frame) -> None:
        top = ttk.LabelFrame(parent, text="Société", padding=8)
        top.pack(fill="x")
        self._settings_vars: dict[str, tk.StringVar] = {}
        for i, (key, label) in enumerate([
            ("company_name", "Nom"), ("company_address", "Adresse"),
            ("company_siret", "SIRET"), ("company_vat", "N° TVA"),
            ("company_logo", "Logo (chemin)"), ("currency", "Devise"),
            ("default_vat_rate", "TVA par défaut (%)"),
            ("of_missing_policy", "OF composant manquant (warn/block)"),
        ]):
            ttk.Label(top, text=label).grid(row=i // 2, column=(i % 2) * 2, sticky="w")
            v = tk.StringVar(value=self.settings.get(key) or "")
            self._settings_vars[key] = v
            ttk.Entry(top, textvariable=v, width=40).grid(
                row=i // 2, column=(i % 2) * 2 + 1, sticky="w", padx=6, pady=2)

        def save_company() -> None:
            for k, v in self._settings_vars.items():
                self.settings.set(k, v.get())
            messagebox.showinfo("Paramètres", "Société enregistrée.")

        ttk.Button(top, text="Enregistrer", command=save_company).grid(
            row=4, column=1, sticky="w", pady=4)

        mid = ttk.LabelFrame(parent, text="Modules actifs", padding=8)
        mid.pack(fill="x", pady=8)
        self._module_vars: dict[str, tk.BooleanVar] = {}
        for m in MODULES:
            v = tk.BooleanVar(value=self.settings.module_enabled(m))
            self._module_vars[m] = v
            ttk.Checkbutton(mid, text=MODULE_LABELS.get(m, m), variable=v).pack(
                side="left", padx=8)

        def save_modules() -> None:
            for m, v in self._module_vars.items():
                self.settings.set_module(m, v.get())
            self.refresh_tabs()

        ttk.Button(mid, text="Appliquer", command=save_modules).pack(side="left", padx=8)

        bottom = ttk.LabelFrame(parent, text="Journal d'audit", padding=8)
        bottom.pack(fill="both", expand=True)
        cols = ("id", "at", "action", "entity", "entity_id", "details")
        tree = ttk.Treeview(bottom, columns=cols, show="headings", height=8)
        for c, w in zip(cols, (40, 130, 120, 90, 60, 220)):
            tree.heading(c, text=c)
            tree.column(c, width=w)
        tree.pack(fill="both", expand=True)
        for row in self.settings.audit_list():
            tree.insert("", "end", values=tuple(row[c] for c in cols))

    # ------------------------------------------------------------------ Inventaire
    def _build_inventory(self, parent: ttk.Frame) -> None:
        toolbar = ttk.Frame(parent)
        toolbar.pack(fill="x")
        self._inv_search = tk.StringVar()
        ttk.Label(toolbar, text="Recherche :").pack(side="left")
        ttk.Entry(toolbar, textvariable=self._inv_search, width=25).pack(
            side="left", padx=4)
        ttk.Button(toolbar, text="Chercher",
                   command=self._load_articles).pack(side="left")
        for label, cmd in (
            ("Nouvel article", self._new_article),
            ("Entrée", self._stock_entry),
            ("Sortie", self._stock_exit),
            ("Ajustement", self._stock_adjust),
            ("Transfert", self._stock_transfer),
            ("Inventaire physique", self._stock_count),
        ):
            ttk.Button(toolbar, text=label, command=cmd).pack(side="left", padx=3)

        main = ttk.PanedWindow(parent, orient="horizontal")
        main.pack(fill="both", expand=True, pady=6)

        left = ttk.Frame(main)
        main.add(left, weight=3)
        cols = ("id", "sku", "designation", "type", "stock_qty",
                "reserved_qty", "available_qty", "cmup", "stock_value")
        headers = ("ID", "Référence", "Désignation", "Type", "Physique",
                   "Réservé", "Disponible", "CMUP", "Valeur")
        self._art_tree = ttk.Treeview(left, columns=cols, show="headings")
        for c, h, w in zip(cols, headers, (30, 90, 200, 90, 70, 70, 80, 70, 80)):
            self._art_tree.heading(c, text=h)
            self._art_tree.column(c, width=w)
        self._art_tree.pack(fill="both", expand=True)

        right = ttk.Frame(main)
        main.add(right, weight=2)
        ttk.Label(right, text="Mouvements de l'article sélectionné").pack()
        mcols = ("moved_at", "move_type", "qty", "cmup_after", "source_doc", "reason")
        self._move_tree = ttk.Treeview(right, columns=mcols, show="headings", height=15)
        for c, h in zip(mcols, ("Date", "Type", "Qté", "CMUP", "Doc", "Motif")):
            self._move_tree.heading(c, text=h)
            self._move_tree.column(c, width=90)
        self._move_tree.pack(fill="both", expand=True)
        self._art_tree.bind("<<TreeviewSelect>>", self._on_article_select)
        self._load_articles()

    def _load_articles(self) -> None:
        self._art_tree.delete(*self._art_tree.get_children())
        search = self._inv_search.get().strip()
        for a in self.inventory.list_articles(search):
            avail = a["stock_qty"] - a["reserved_qty"]
            value = a["stock_qty"] * a["cmup"]
            self._art_tree.insert("", "end", values=(
                a["id"], a["sku"], a["designation"], a["type"], a["stock_qty"],
                a["reserved_qty"], avail, round(a["cmup"], 2), round(value, 2)))

    def _selected_article(self) -> dict | None:
        sel = self._art_tree.selection()
        if not sel:
            messagebox.showwarning("Sélection", "Sélectionnez un article.")
            return None
        aid = self._art_tree.item(sel[0])["values"][0]
        return self.inventory.get_article(aid)

    def _on_article_select(self, _e=None) -> None:
        a = self._selected_article()
        if not a:
            return
        self._move_tree.delete(*self._move_tree.get_children())
        for m in self.inventory.moves(a["id"], limit=100):
            self._move_tree.insert("", "end", values=(
                m["moved_at"], m["move_type"], m["qty"], m["cmup_after"],
                m["source_doc"], m["reason"]))

    def _article_form(self, a: dict | None = None) -> dict | None:
        win = tk.Toplevel(self)
        win.title("Article" + (f" — {a['sku']}" if a else ""))
        win.grab_set()
        vars_ = {
            "sku": tk.StringVar(value=a["sku"] if a else ""),
            "designation": tk.StringVar(value=a["designation"] if a else ""),
            "type": tk.StringVar(value=a["type"] if a else "composant"),
            "unit": tk.StringVar(value=a["unit"] if a else "pce"),
            "purchase_price": tk.StringVar(value=a["purchase_price"] if a else "0"),
            "sale_price": tk.StringVar(value=a["sale_price"] if a else "0"),
            "vat_rate": tk.StringVar(value=a["vat_rate"] if a else "20.0"),
            "min_stock": tk.StringVar(value=a["min_stock"] if a else "0"),
            "track_lots": tk.BooleanVar(value=bool(a and a["track_lots"])),
        }
        fields = [("sku", "Référence (SKU)"), ("designation", "Désignation"),
                  ("unit", "Unité"), ("purchase_price", "Prix d'achat"),
                  ("sale_price", "Prix de vente"), ("vat_rate", "TVA (%)"),
                  ("min_stock", "Stock mini")]
        for i, (k, label) in enumerate(fields):
            ttk.Label(win, text=label).grid(row=i, column=0, sticky="w", padx=6, pady=2)
            ttk.Entry(win, textvariable=vars_[k]).grid(row=i, column=1, padx=6)
        ttk.Label(win, text="Type").grid(row=7, column=0, sticky="w", padx=6)
        ttk.Combobox(win, textvariable=vars_["type"], state="readonly",
                     values=["produit_fini", "composant", "consommable",
                             "marchandise", "service"]).grid(row=7, column=1, sticky="w")
        ttk.Checkbutton(win, text="Suivi par lots", variable=vars_["track_lots"]).grid(
            row=8, column=0, columnspan=2, sticky="w", padx=6)
        result: dict = {}

        def ok() -> None:
            result.update({k: v.get() for k, v in vars_.items()})
            win.destroy()

        ttk.Button(win, text="Enregistrer", command=ok).grid(
            row=9, column=0, columnspan=2, pady=6)
        self.wait_window(win)
        return result or None

    def _new_article(self) -> None:
        data = self._article_form()
        if not data:
            return
        try:
            self.inventory.create_article(data)
        except Exception as e:
            messagebox.showerror("Erreur", str(e))
        self._load_articles()

    def _stock_entry(self) -> None:
        a = self._selected_article()
        if not a:
            return
        qty = simpledialog.askfloat("Entrée", f"Quantité {a['sku']} :")
        if qty is None:
            return
        cost = simpledialog.askfloat("Coût unitaire", "Coût unitaire :", parent=self)
        loc = simpledialog.askstring("Emplacement", "Emplacement (MAG/rayon) :", parent=self)
        try:
            self.inventory.entry(a["id"], qty, cost or 0,
                                 reason="Saisie manuelle", location_to=loc or "")
        except Exception as e:
            messagebox.showerror("Erreur", str(e))
        self._load_articles()

    def _stock_exit(self) -> None:
        a = self._selected_article()
        if not a:
            return
        qty = simpledialog.askfloat("Sortie", f"Quantité {a['sku']} :")
        if qty is None:
            return
        reason = simpledialog.askstring("Motif", "Motif :", parent=self) or ""
        try:
            self.inventory.exit_(a["id"], qty, reason=reason)
        except Exception as e:
            messagebox.showerror("Erreur", str(e))
        self._load_articles()

    def _stock_adjust(self) -> None:
        a = self._selected_article()
        if not a:
            return
        qty = simpledialog.askfloat("Ajustement", f"Nouvelle quantité {a['sku']} :")
        if qty is None:
            return
        try:
            self.inventory.adjust(a["id"], qty, reason="Ajustement manuel")
        except Exception as e:
            messagebox.showerror("Erreur", str(e))
        self._load_articles()

    def _stock_transfer(self) -> None:
        a = self._selected_article()
        if not a:
            return
        qty = simpledialog.askfloat("Transfert", f"Quantité {a['sku']} :")
        if qty is None:
            return
        src = simpledialog.askstring("De", "Emplacement source :") or ""
        dst = simpledialog.askstring("Vers", "Emplacement destination :") or ""
        try:
            self.inventory.transfer(a["id"], qty, src, dst)
        except Exception as e:
            messagebox.showerror("Erreur", str(e))
        self._load_articles()

    def _stock_count(self) -> None:
        win = tk.Toplevel(self)
        win.title("Inventaire physique")
        win.grab_set()
        cid = self.inventory.open_count()
        count_ref = self.db.query_one(
            "SELECT reference FROM stock_counts WHERE id=?", (cid,))["reference"]
        ttk.Label(win, text=f"Inventaire {count_ref}").pack(padx=8, pady=4)
        cols = ("id", "sku", "designation", "system_qty", "counted_qty")
        tree = ttk.Treeview(win, columns=cols, show="headings", height=12)
        for c, h in zip(cols, ("ID", "Référence", "Désignation", "Système", "Compté")):
            tree.heading(c, text=h)
            tree.column(c, width=90)
        tree.pack(padx=8, pady=4)
        for a in self.inventory.list_articles(active_only=True):
            if a["type"] != "service":
                tree.insert("", "end", values=(
                    a["id"], a["sku"], a["designation"], a["stock_qty"], ""))

        def save_line() -> None:
            sel = tree.selection()
            if not sel:
                return
            vals = tree.item(sel[0])["values"]
            qty = simpledialog.askfloat("Comptage", f"Quantité comptée {vals[1]} :", parent=win)
            if qty is not None:
                self.inventory.set_counted(cid, vals[0], qty)
                tree.item(sel[0], values=(vals[0], vals[1], vals[2], vals[3], qty))

        def validate() -> None:
            for line in self.inventory.count_lines(cid):
                if messagebox.askyesno(
                        "Écart",
                        f"{line['sku']} : système {line['system_qty']} → "
                        f"compté {line['counted_qty']}. Ajuster ?"):
                    self.inventory.set_counted(cid, line["article_id"],
                                               line["counted_qty"])
            self.inventory.validate_count(cid)
            win.destroy()
            self._load_articles()

        btns = ttk.Frame(win)
        btns.pack(pady=4)
        ttk.Button(btns, text="Saisir comptage", command=save_line).pack(side="left", padx=4)
        ttk.Button(btns, text="Valider", command=validate).pack(side="left", padx=4)

    # ------------------------------------------------------------------ Nomenclature
    def _build_bom(self, parent: ttk.Frame) -> None:
        toolbar = ttk.Frame(parent)
        toolbar.pack(fill="x")
        ttk.Button(toolbar, text="Nouvelle nomenclature",
                   command=self._new_bom).pack(side="left", padx=3)
        ttk.Button(toolbar, text="Ajouter composant",
                   command=self._add_bom_line).pack(side="left", padx=3)
        ttk.Button(toolbar, text="Retirer composant",
                   command=self._remove_bom_line).pack(side="left", padx=3)
        ttk.Button(toolbar, text="Activer cette version",
                   command=self._activate_bom).pack(side="left", padx=3)
        ttk.Button(toolbar, text="Explosion / Coût",
                   command=self._explode_bom).pack(side="left", padx=3)
        ttk.Button(toolbar, text="Où utilisé ?",
                   command=self._where_used).pack(side="left", padx=3)

        cols = ("id", "sku", "designation", "version", "variant",
                "active", "n_lines")
        self._bom_tree = ttk.Treeview(parent, columns=cols, show="headings",
                                      selectmode="browse")
        for c, h, w in zip(cols, ("BOM ID", "Produit", "Désignation", "Version",
                                  "Variant", "Active", "Lignes"),
                           (60, 110, 220, 60, 60, 50, 50)):
            self._bom_tree.heading(c, text=h)
            self._bom_tree.column(c, width=w)
        self._bom_tree.pack(fill="x", pady=4)
        self._bom_tree.bind("<<TreeviewSelect>>", self._load_bom_lines)

        ttk.Label(parent, text="Composants").pack()
        lcols = ("id", "sku", "designation", "qty_per", "scrap_pct", "unit_cost")
        self._bomline_tree = ttk.Treeview(parent, columns=lcols, show="headings",
                                          selectmode="browse", height=8)
        for c, h, w in zip(lcols, ("ID", "Référence", "Désignation", "Qté/produit",
                                   "Rebuts %", "Coût matière"),
                           (60, 110, 220, 90, 70, 90)):
            self._bomline_tree.heading(c, text=h)
            self._bomline_tree.column(c, width=w)
        self._bomline_tree.pack(fill="both", expand=True)
        self._load_boms()

    def _load_boms(self) -> None:
        self._bom_tree.delete(*self._bom_tree.get_children())
        for b in self.bom.list_boms():
            self._bom_tree.insert("", "end", values=(
                b["id"], b["sku"], b["designation"], b["version"],
                b["variant"] or "-", "oui" if b["active"] else "non", b["n_lines"]))

    def _selected_bom_id(self) -> int | None:
        sel = self._bom_tree.selection()
        if not sel:
            messagebox.showwarning("Sélection", "Sélectionnez une nomenclature.")
            return None
        return self._bom_tree.item(sel[0])["values"][0]

    def _load_bom_lines(self, _e=None) -> None:
        bom_id = self._selected_bom_id()
        if not bom_id:
            return
        self._bomline_tree.delete(*self._bomline_tree.get_children())
        for l in self.bom.lines(bom_id):
            cost = l["qty_per"] * (1 + l["scrap_pct"] / 100) * l["purchase_price"]
            self._bomline_tree.insert("", "end", values=(
                l["id"], l["sku"], l["designation"], l["qty_per"],
                l["scrap_pct"], round(cost, 2)))

    def _pick_article(self, title: str, types: list[str] | None = None) -> dict | None:
        articles = self.inventory.list_articles()
        if types:
            articles = [a for a in articles if a["type"] in types]
        win = tk.Toplevel(self)
        win.title(title)
        win.grab_set()
        tree = ttk.Treeview(win, columns=("id", "sku", "designation", "type"),
                            show="headings", height=10)
        for c, h in zip(("id", "sku", "designation", "type"),
                        ("ID", "Référence", "Désignation", "Type")):
            tree.heading(c, text=h)
            tree.column(c, width=110)
        tree.pack(padx=8, pady=8)
        for a in articles:
            tree.insert("", "end", values=(a["id"], a["sku"], a["designation"], a["type"]))
        picked: dict = {}

        def ok() -> None:
            sel = tree.selection()
            if sel:
                picked["id"] = tree.item(sel[0])["values"][0]
            win.destroy()

        ttk.Button(win, text="Choisir", command=ok).pack(pady=4)
        tree.bind("<Double-1>", lambda _e: ok())
        self.wait_window(win)
        if not picked:
            return None
        return self.inventory.get_article(picked["id"])

    def _new_bom(self) -> None:
        parent = self._pick_article("Produit parent", ["produit_fini", "marchandise"])
        if not parent:
            return
        version = simpledialog.askstring("Version", "Version (A, B…) :", parent=self,
                                         initialvalue="A") or "A"
        variant = simpledialog.askstring("Variant", "Variant (optionnel) :", parent=self) or ""
        try:
            self.bom.create_bom(parent["id"], version, variant)
        except Exception as e:
            messagebox.showerror("Erreur", str(e))
        self._load_boms()

    def _add_bom_line(self) -> None:
        bom_id = self._selected_bom_id()
        if not bom_id:
            return
        comp = self._pick_article("Composant", ["composant", "consommable",
                                                "produit_fini", "marchandise"])
        if not comp:
            return
        qty = simpledialog.askfloat("Quantité", f"Qté par produit de {comp['sku']} :", parent=self)
        if not qty:
            return
        scrap = simpledialog.askfloat("Rebuts", "% pertes :", parent=self) or 0
        try:
            self.bom.add_line(bom_id, comp["id"], qty, scrap)
        except Exception as e:
            messagebox.showerror("Erreur", str(e))
        self._load_bom_lines()

    def _remove_bom_line(self) -> None:
        sel = self._bomline_tree.selection()
        if not sel:
            messagebox.showwarning("Sélection", "Sélectionnez une ligne.")
            return
        line_id = self._bomline_tree.item(sel[0])["values"][0]
        self.bom.remove_line(line_id)
        self._load_bom_lines()

    def _activate_bom(self) -> None:
        bom_id = self._selected_bom_id()
        if bom_id:
            self.bom.set_active_version(bom_id)
            self._load_boms()

    def _explode_bom(self) -> None:
        bom_id = self._selected_bom_id()
        if not bom_id:
            return
        bom = self.db.query_one("SELECT * FROM boms WHERE id=?", (bom_id,))
        qty = simpledialog.askfloat("Quantité", "Quantité à produire :", parent=self,
                                   initialvalue=1.0) or 1
        rows = self.bom.explode(bom["parent_id"], qty,
                                version=bom["version"], variant=bom["variant"])
        cost = self.bom.standard_cost(bom["parent_id"])
        text = (f"Coût matière standard : {cost:.2f}\n\n"
                + "\n".join(
                    "  " * r["level"] + f"{r['sku']} × {r['qty_total']} "
                    f"(stock {r['available_qty']})" for r in rows))
        messagebox.showinfo("Explosion de nomenclature", text or "BOM vide")

    def _where_used(self) -> None:
        comp = self._pick_article("Composant à rechercher")
        if not comp:
            return
        parents = self.bom.where_used(comp["id"])
        if not parents:
            messagebox.showinfo("Où utilisé ?", f"{comp['sku']} n'est utilisé nulle part.")
            return
        messagebox.showinfo("Où utilisé ?", "\n".join(
            f"{p['sku']} — {p['designation']}" for p in parents))

    # ------------------------------------------------------------------ Import/Export
    # ------------------------------------------------------------------ helpers formulaires
    def _form_window(self, title: str) -> tk.Toplevel:
        win = tk.Toplevel(self)
        win.title(title)
        win.grab_set()
        win.transient(self)
        return win

    def _form_fields(self, win: tk.Toplevel, fields: list[tuple[str, str]],
                     start_row: int = 0) -> dict[str, str] | None:
        """Champs simples ; retourne les valeurs, None si fermé sans valider."""
        entries: dict[str, tk.Entry] = {}
        result: dict[str, str] = {}
        for i, (key, label) in enumerate(fields, start=start_row):
            ttk.Label(win, text=label).grid(row=i, column=0, sticky="w", padx=6, pady=2)
            e = ttk.Entry(win, width=35)
            e.grid(row=i, column=1, padx=6, pady=2)
            entries[key] = e

        def ok() -> None:
            for k, e in entries.items():
                result[k] = e.get().strip()
            win.destroy()

        ttk.Button(win, text="Valider", command=ok).grid(
            row=start_row + len(fields), column=0, columnspan=2, pady=6)
        self.wait_window(win)
        return result or None

    def _prompt_fields(self, fields: list[tuple[str, str]]) -> dict[str, str] | None:
        win = self._form_window("Saisie")
        return self._form_fields(win, fields)

    def create_po_from_suggestions(self) -> None:
        """Assistant : générer une commande fournisseur depuis les suggestions."""
        sugg = self.purchasing.suggestions()
        if not sugg:
            messagebox.showinfo("Suggestions", "Aucun besoin net.")
            return
        suppliers = self.purchasing.list_suppliers()
        if not suppliers:
            messagebox.showwarning("Achats", "Créez d'abord un fournisseur.")
            return
        win = self._form_window("Commande fournisseur depuis suggestions")
        ttk.Label(win, text="Fournisseur").grid(row=0, column=0)
        cb = ttk.Combobox(win, values=[f"{s['id']} — {s['name']}" for s in suppliers],
                          state="readonly", width=35)
        cb.grid(row=0, column=1)
        cb.current(0)
        for i, s in enumerate(sugg, start=1):
            var = tk.BooleanVar(value=True)
            ttk.Checkbutton(
                win, text=f"{s['sku']} — commander {s['qty_suggested']:g} "
                          f"@ {s['best_price']:.2f}", variable=var).grid(
                row=i, column=0, columnspan=2, sticky="w", padx=6)
            s["_var"] = var
        ttk.Button(win, text="Créer la commande", command=win.destroy).grid(
            row=len(sugg) + 1, column=0, columnspan=2, pady=6)
        self.wait_window(win)
        selected = [s["article_id"] for s in sugg if s["_var"].get()]
        if not selected:
            return
        po = self.purchasing.create_po_from_suggestions(
            int(cb.get().split(" — ")[0]), selected)
        messagebox.showinfo(
            "Achats",
            f"Commande {po['number']} créée — à envoyer depuis l'onglet Achats.")

    def _read_csv(self, title: str) -> str | None:
        path = filedialog.askopenfilename(title=title,
                                          filetypes=[("CSV", "*.csv"), ("Tous", "*.*")])
        if not path:
            return None
        with open(path, encoding="utf-8-sig", newline="") as f:
            return f.read()

    def _import_articles(self) -> None:
        text = self._read_csv("Importer articles")
        if not text:
            return
        try:
            report = self.inventory.import_articles_csv(text)
        except Exception as e:
            messagebox.showerror("Import", str(e))
            return
        msg = f"{report['ok']} article(s) importé(s)."
        if report["errors"]:
            msg += "\n\nErreurs :\n" + "\n".join(report["errors"])
        messagebox.showinfo("Import articles", msg)
        self._load_articles()

    def _import_bom(self) -> None:
        text = self._read_csv("Importer nomenclatures")
        if not text:
            return
        try:
            report = self.bom.import_bom_csv(text)
        except Exception as e:
            messagebox.showerror("Import", str(e))
            return
        msg = f"{report['ok']} ligne(s) importée(s)."
        if report["errors"]:
            msg += "\n\nErreurs :\n" + "\n".join(report["errors"])
        messagebox.showinfo("Import nomenclatures", msg)
        self._load_boms()

    def _export_stock(self) -> None:
        path = filedialog.asksaveasfilename(defaultextension=".csv",
                                             initialfile="etat_stock.csv")
        if not path:
            return
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            f.write(self.inventory.export_stock_csv())
        messagebox.showinfo("Export", f"État de stock exporté :\n{path}")
