"""Onglet Production : configurateur de variantes + écran atelier."""
from __future__ import annotations

from tkinter import messagebox, ttk

from erp.services.configurator_service import ConfiguratorService
from erp.services.manufacturing_service import ManufacturingService

STEP_LABELS = {"a_faire": "À faire", "en_cours": "En cours",
               "termine": "Terminé"}


class ProductionTab:
    def __init__(self, app, frame: ttk.Frame, cfg: ConfiguratorService,
                 mfg: ManufacturingService):
        self.app, self.cfg, self.mfg = app, cfg, mfg

        # ------------------------------------------------------------ configurateur
        ttk.Label(frame, text="Configurateur de variantes",
                  font=("", 11, "bold")).pack(anchor="w")
        top = ttk.Frame(frame)
        top.pack(fill="x")
        ttk.Button(top, text="Charger gabarit",
                   command=self.load_template).pack(side="left", padx=3)
        ttk.Button(top, text="Configurer → article + BOM",
                   command=self.configure).pack(side="left", padx=3)
        ttk.Button(top, text="Créer l'OF",
                   command=self.create_wo).pack(side="left", padx=3)
        self.tpl_label = ttk.Label(frame, text="Aucun gabarit chargé.")
        self.tpl_label.pack(anchor="w")
        self.opts_frame = ttk.Frame(frame)
        self.opts_frame.pack(fill="x", pady=3)
        self._template_id: int | None = None
        self._opt_vars: dict[str, object] = {}

        self.variants_tree = ttk.Treeview(
            frame, columns=("id", "sku", "designation", "price"),
            show="headings", height=4, selectmode="browse")
        for c, h, w in zip(("id", "sku", "designation", "price"),
                           ("ID", "SKU configuré", "Désignation", "Prix"),
                           (30, 200, 260, 70)):
            self.variants_tree.heading(c, text=h)
            self.variants_tree.column(c, width=w)
        self.variants_tree.pack(fill="x", pady=3)
        self.load_variants()

        # ------------------------------------------------------------ atelier
        ttk.Label(frame, text="Écran atelier — file par poste",
                  font=("", 11, "bold")).pack(anchor="w", pady=(8, 0))
        mid = ttk.Frame(frame)
        mid.pack(fill="x")
        ttk.Label(mid, text="Poste :").pack(side="left")
        self.wc_var = ttk.Combobox(mid, state="readonly", width=25)
        self.wc_var.pack(side="left", padx=4)
        self.wc_var.bind("<<ComboboxSelected>>", self.load_queue)
        ttk.Button(mid, text="Démarrer l'étape",
                   command=self.start_step).pack(side="left", padx=3)
        ttk.Button(mid, text="Terminer l'étape",
                   command=self.finish_step).pack(side="left", padx=3)
        self.refresh_wcs()

        self.queue_tree = ttk.Treeview(
            frame, columns=("wo_id", "wo", "step", "description", "article",
                            "status"), show="headings", height=6,
            selectmode="browse")
        for c, h, w in zip(
                ("wo_id", "wo", "step", "description", "article", "status"),
                ("OF ID", "N° OF", "Étape", "Description", "Article",
                 "Statut"),
                (50, 90, 50, 180, 160, 80)):
            self.queue_tree.heading(c, text=h)
            self.queue_tree.column(c, width=w)
        self.queue_tree.pack(fill="both", expand=True, pady=3)

    # ------------------------------------------------------------------ configurateur
    def load_variants(self) -> None:
        self.variants_tree.delete(*self.variants_tree.get_children())
        for tpl in self.cfg.list_templates():
            for art in self.cfg.inventory.list_articles():
                if art["sku"].startswith(tpl["sku_base"] + "-"):
                    self.variants_tree.insert("", "end", values=(
                        art["id"], art["sku"], art["designation"],
                        f"{art['sale_price']:.2f}"))

    def load_template(self) -> None:
        templates = self.cfg.list_templates()
        if not templates:
            messagebox.showinfo(
                "Configurateur",
                "Aucun gabarit. Créez-en un via le service "
                "(create_template) ou les données de démo.")
            return
        win = self.app._form_window("Choisir un gabarit")
        cb = ttk.Combobox(win, values=[f"{t['id']} — {t['sku_base']} "
                                       f"({t['designation']})"
                                       for t in templates],
                          state="readonly", width=45)
        cb.grid(row=0, column=1)
        cb.current(0)
        ttk.Label(win, text="Gabarit").grid(row=0, column=0)
        self.app.wait_window(win)
        if not cb.get():
            return
        self._template_id = int(cb.get().split(" — ")[0])
        tpl = self.cfg.get_template(self._template_id)
        self.tpl_label.config(text=f"Gabarit : {tpl['sku_base']} — "
                                   f"{tpl['designation']}")
        for child in self.opts_frame.winfo_children():
            child.destroy()
        self._opt_vars = {}
        for i, opt in enumerate(self.cfg.options(self._template_id)):
            ttk.Label(self.opts_frame, text=opt["name"]).grid(
                row=i, column=0, sticky="w", padx=4)
            var = ttk.Combobox(
                self.opts_frame, state="readonly", width=30,
                values=[f"{v['id']} — {v['label']}"
                        + (f" (+{v['price_extra']:.2f})"
                           if v["price_extra"] else "")
                        for v in opt["values"]])
            var.grid(row=i, column=1, padx=4, pady=2)
            if opt["values"]:
                var.current(0)
            self._opt_vars[opt["code"]] = var

    def configure(self) -> None:
        if not self._template_id:
            messagebox.showwarning("Configurateur", "Chargez un gabarit.")
            return
        choices = {}
        for code, var in self._opt_vars.items():
            if var.get():
                choices[code] = int(var.get().split(" — ")[0])
        try:
            res = self.cfg.configure(self._template_id, choices)
            messagebox.showinfo(
                "Configurateur",
                f"Article {res['sku']} prêt (prix {res['sale_price']:.2f}), "
                f"BOM générée.")
        except Exception as e:
            messagebox.showerror("Erreur", str(e))
        self.load_variants()

    def create_wo(self) -> None:
        sel = self.variants_tree.selection()
        if not sel:
            messagebox.showwarning("Configurateur",
                                  "Sélectionnez une variante configurée.")
            return
        values = self.app._prompt_fields([("qty", "Quantité à produire")])
        if not values or not values.get("qty"):
            return
        try:
            wo = self.cfg.create_wo_for(
                int(self.variants_tree.item(sel[0])["values"][0]),
                float(values["qty"]))
            messagebox.showinfo("OF", f"OF {wo['number']} créé (planifié).")
        except Exception as e:
            messagebox.showerror("Erreur", str(e))

    # ------------------------------------------------------------------ atelier
    def refresh_wcs(self) -> None:
        wcs = self.mfg.list_work_centers()
        self.wc_var.config(values=[f"{w['id']} — {w['code']} ({w['name']})"
                                   for w in wcs])
        if wcs:
            self.wc_var.current(0)
        self.load_queue()

    def _selected_queue_row(self) -> dict | None:
        sel = self.queue_tree.selection()
        if not sel:
            messagebox.showwarning("Sélection",
                                   "Sélectionnez une étape dans la file.")
            return None
        vals = self.queue_tree.item(sel[0])["values"]
        return {"wo_id": vals[0], "step_no": vals[2]}

    def load_queue(self, _e=None) -> None:
        self.queue_tree.delete(*self.queue_tree.get_children())
        if not self.wc_var.get():
            return
        wc_id = int(self.wc_var.get().split(" — ")[0])
        for row in self.mfg.queue_by_work_center(wc_id):
            self.queue_tree.insert("", "end", values=(
                row["work_order_id"], row["wo_number"], row["step_no"],
                row["description"],
                f"{row['sku']} — {row['designation']}",
                STEP_LABELS.get(row["status"], row["status"])))

    def start_step(self) -> None:
        row = self._selected_queue_row()
        if not row:
            return
        try:
            self.mfg.start_step(row["wo_id"], row["step_no"])
        except Exception as e:
            messagebox.showerror("Erreur", str(e))
        self.load_queue()

    def finish_step(self) -> None:
        row = self._selected_queue_row()
        if not row:
            return
        values = self.app._prompt_fields(
            [("time", "Temps passé (min, défaut 0)")])
        try:
            self.mfg.finish_step(row["wo_id"], row["step_no"],
                                 float(values.get("time") or 0)
                                 if values else 0)
        except Exception as e:
            messagebox.showerror("Erreur", str(e))
        self.load_queue()
