"""Onglets UI des blocs métier (Ventes, Fabrication, Achats, Magasin,
Facturation, Tableau de bord). Les callbacks ne font qu'appeler les services."""
from __future__ import annotations

import subprocess
import sys
import tempfile
import webbrowser
from pathlib import Path
from tkinter import messagebox, ttk

from erp.services.dashboard_service import DashboardService
from erp.services.invoicing_service import InvoicingService
from erp.services.inventory_service import InventoryService
from erp.services.manufacturing_service import ManufacturingService
from erp.services.purchasing_service import PurchasingService
from erp.services.sales_service import SalesService
from erp.services.settings_service import SettingsService
from erp.services.warehouse_service import WarehouseService

STATUS_LABELS = {
    "brouillon": "Brouillon", "confirmee": "Confirmée",
    "en_production": "En production", "prete": "Prête",
    "livree": "Livrée", "facturee": "Facturée", "annulee": "Annulée",
    "planifie": "Planifié", "lance": "Lancé", "en_cours": "En cours",
    "termine": "Terminé", "cloture": "Clôturé",
    "envoyee": "Envoyée", "recue_partiel": "Reçue partielle", "recue": "Reçue",
    "en_attente": "En attente", "partiellement_payee": "Partiellement payée",
    "payee": "Payée", "livre": "Livré", "preparation": "Préparation",
}


def label(status: str) -> str:
    return STATUS_LABELS.get(status, status)


def _tree(parent, cols: list[tuple[str, str, int]]) -> ttk.Treeview:
    tree = ttk.Treeview(parent, columns=[c[0] for c in cols], show="headings",
                        selectmode="browse")
    for key, text, width in cols:
        tree.heading(key, text=text)
        tree.column(key, width=width)
    return tree


def _show_html(html: str, title: str) -> None:
    """Sauvegarde le document HTML et l'ouvre dans le navigateur par défaut."""
    path = Path(tempfile.gettempdir()) / f"erp_{title.replace(' ', '_')}.html"
    path.write_text(html, encoding="utf-8")
    webbrowser.open(path.as_uri())


class SalesTab:
    def __init__(self, app, frame: ttk.Frame, sales: SalesService,
                 inventory: InventoryService):
        self.app, self.sales, self.inv = app, sales, inventory
        top = ttk.Frame(frame)
        top.pack(fill="x")
        ttk.Button(top, text="Nouveau client", command=self.new_customer).pack(side="left", padx=3)
        ttk.Button(top, text="Nouvelle commande", command=self.new_order).pack(side="left", padx=3)
        ttk.Button(top, text="Ajouter ligne", command=self.add_line).pack(side="left", padx=3)
        ttk.Button(top, text="Confirmer (AR)", command=self.confirm).pack(side="left", padx=3)
        ttk.Button(top, text="Voir AR (HTML)", command=self.view_ar).pack(side="left", padx=3)
        ttk.Button(top, text="Annuler commande", command=self.cancel).pack(side="left", padx=3)

        self.tree = _tree(frame, [("id", "ID", 30), ("number", "N°", 110),
                                  ("customer_name", "Client", 180),
                                  ("status", "Statut", 110),
                                  ("desired_date", "Livraison", 90),
                                  ("total_ttc", "Total TTC", 90)])
        self.tree.pack(fill="both", expand=True, pady=4)
        self.tree.bind("<<TreeviewSelect>>", self.load_lines)
        self.lines_tree = _tree(frame, [("sku", "Réf.", 100), ("designation", "Désignation", 220),
                                        ("qty", "Qté", 60), ("delivered_qty", "Livré", 60),
                                        ("price", "PU", 80), ("vat_rate", "TVA", 50),
                                        ("total", "Total", 80)])
        self.lines_tree.pack(fill="both", expand=True)
        self.load()

    def load(self) -> None:
        self.tree.delete(*self.tree.get_children())
        for o in self.sales.list_orders():
            self.tree.insert("", "end", values=(
                o["id"], o["number"], o["customer_name"], label(o["status"]),
                o["desired_date"], f"{o['total_ttc']:.2f}"))

    def _selected(self) -> dict | None:
        sel = self.tree.selection()
        if not sel:
            messagebox.showwarning("Sélection", "Sélectionnez une commande.")
            return None
        return self.sales.get_order(self.tree.item(sel[0])["values"][0])

    def load_lines(self, _e=None) -> None:
        order = self._selected()
        if not order:
            return
        self.lines_tree.delete(*self.lines_tree.get_children())
        for l in self.sales.order_lines(order["id"]):
            total = l["qty"] * l["price"] * (1 - l["discount_pct"] / 100)
            self.lines_tree.insert("", "end", values=(
                l["sku"], l["designation"], l["qty"], l["delivered_qty"],
                l["price"], l["vat_rate"], f"{total:.2f}"))

    def new_customer(self) -> None:
        win = self.app._form_window("Nouveau client")
        fields = [("name", "Nom *"), ("address", "Adresse"), ("email", "Email"),
                  ("phone", "Téléphone"), ("payment_terms", "Conditions de paiement")]
        values = self.app._form_fields(win, fields)
        if values and values.get("name"):
            self.sales.create_customer(dict(values, payment_terms=values.get("payment_terms") or "30 jours"))
            messagebox.showinfo("Client", "Client créé.")

    def new_order(self) -> None:
        customers = self.sales.list_customers()
        if not customers:
            messagebox.showwarning("Ventes", "Créez d'abord un client.")
            return
        win = self.app._form_window("Nouvelle commande")
        cb = ttk.Combobox(win, values=[f"{c['id']} — {c['name']}" for c in customers],
                          state="readonly", width=40)
        cb.grid(row=0, column=1)
        ttk.Label(win, text="Client").grid(row=0, column=0)
        date_e = self.app._form_fields(win, [("desired_date", "Livraison souhaitée (AAAA-MM-JJ)")],
                                       start_row=1)
        if date_e is None or not cb.get():
            return
        cid = int(cb.get().split(" — ")[0])
        order = self.sales.create_order(cid, desired_date=date_e.get("desired_date", ""))
        messagebox.showinfo("Commande", f"Commande {order['number']} créée (brouillon).")
        self.load()

    def add_line(self) -> None:
        order = self._selected()
        if not order:
            return
        art = self.app._pick_article("Article à vendre",
                                     ["produit_fini", "marchandise", "service"])
        if not art:
            return
        values = self.app._prompt_fields([
            ("qty", f"Quantité {art['sku']}"), ("price", f"Prix (défaut {art['sale_price']})")])
        if not values or not values.get("qty"):
            return
        try:
            self.sales.add_line(order["id"], art["id"], float(values["qty"]),
                                float(values["price"]) if values.get("price") else None)
        except Exception as e:
            messagebox.showerror("Erreur", str(e))
            return
        self.load_lines()

    def confirm(self) -> None:
        order = self._selected()
        if not order:
            return
        try:
            ar = self.sales.confirm_order(order["id"])
        except Exception as e:
            messagebox.showerror("Confirmation", str(e))
            return
        messagebox.showinfo("AR", f"Commande confirmée. AR {ar} généré.")
        self.load()

    def view_ar(self) -> None:
        order = self._selected()
        if not order:
            return
        _show_html(self.sales.ar_html(order["id"]), f"AR_{order['number']}")

    def cancel(self) -> None:
        order = self._selected()
        if not order:
            return
        if not messagebox.askyesno("Annuler", f"Annuler la commande {order['number']} ?"):
            return
        try:
            self.sales.cancel_order(order["id"])
        except Exception as e:
            messagebox.showerror("Erreur", str(e))
        self.load()


class ManufacturingTab:
    def __init__(self, app, frame: ttk.Frame, mfg: ManufacturingService):
        self.app, self.mfg = app, mfg
        top = ttk.Frame(frame)
        top.pack(fill="x")
        ttk.Button(top, text="Nouvel OF", command=self.new_wo).pack(side="left", padx=3)
        ttk.Button(top, text="Lancer (contrôle stock)", command=self.launch).pack(side="left", padx=3)
        ttk.Button(top, text="Déclarer production", command=self.report).pack(side="left", padx=3)
        ttk.Button(top, text="Voir écarts", command=self.deviations).pack(side="left", padx=3)
        ttk.Button(top, text="Gamme", command=self.routing).pack(side="left", padx=3)
        self.tree = _tree(frame, [("id", "ID", 30), ("number", "N°", 80),
                                  ("sku", "Produit", 110), ("designation", "", 180),
                                  ("qty_to_produce", "À produire", 80),
                                  ("qty_produced", "Produit", 70),
                                  ("status", "Statut", 90),
                                  ("order_number", "Commande", 110)])
        self.tree.pack(fill="both", expand=True, pady=4)
        self.load()

    def load(self) -> None:
        self.tree.delete(*self.tree.get_children())
        for w in self.mfg.list_wos():
            self.tree.insert("", "end", values=(
                w["id"], w["number"], w["sku"], w["designation"],
                w["qty_to_produce"], w["qty_produced"], label(w["status"]),
                w["order_number"] or ""))

    def _selected(self) -> dict | None:
        sel = self.tree.selection()
        if not sel:
            messagebox.showwarning("Sélection", "Sélectionnez un OF.")
            return None
        return self.mfg.get_wo(self.tree.item(sel[0])["values"][0])

    def new_wo(self) -> None:
        art = self.app._pick_article("Produit à fabriquer", ["produit_fini"])
        if not art:
            return
        orders = self.sales_open_orders() if hasattr(self, "sales_open_orders") else []
        values = self.app._prompt_fields([("qty", f"Quantité de {art['sku']}")])
        if not values or not values.get("qty"):
            return
        try:
            wo = self.mfg.create_wo(art["id"], float(values["qty"]))
            messagebox.showinfo("OF", f"OF {wo['number']} créé (planifié).")
        except Exception as e:
            messagebox.showerror("Erreur", str(e))
        self.load()

    def sales_open_orders(self) -> list[dict]:
        return []

    def launch(self) -> None:
        wo = self._selected()
        if not wo:
            return
        try:
            res = self.mfg.check_and_launch(wo["id"])
        except Exception as e:
            messagebox.showerror("Lancement", str(e))
            return
        if res["missing"]:
            detail = "\n".join(
                f"{m['sku']} : manque {m['qty_missing']:g}" for m in res["missing"])
            if messagebox.askyesno(
                    "Composants manquants",
                    f"OF lancé avec avertissement.\n{detail}\n\n"
                    "Créer une commande fournisseur depuis les suggestions d'achat ?"):
                self.app.create_po_from_suggestions()
        else:
            messagebox.showinfo("Lancement", "OF lancé, composants réservés.")
        self.load()

    def report(self) -> None:
        wo = self._selected()
        if not wo:
            return
        values = self.app._prompt_fields([
            ("qty", f"Quantité produite (à produire : {wo['qty_to_produce']})"),
            ("scrap", "Rebuts (défaut 0)")])
        if not values or not values.get("qty"):
            return
        try:
            self.mfg.report_production(wo["id"], float(values["qty"]),
                                       float(values.get("scrap") or 0))
            messagebox.showinfo("Production", "Production déclarée, stock mis à jour.")
        except Exception as e:
            messagebox.showerror("Erreur", str(e))
        self.load()

    def deviations(self) -> None:
        wo = self._selected()
        if not wo:
            return
        devs = self.mfg.deviations(wo["id"])
        if not devs:
            messagebox.showinfo("Écarts", "Aucune consommation enregistrée.")
            return
        text = "\n".join(
            f"{d['sku']} : théorique {d['qty_theoretical']:g} / "
            f"consommé {d['qty_consumed']:g}" for d in devs)
        messagebox.showinfo(f"Écarts OF {wo['number']}", text)

    def routing(self) -> None:
        art = self.app._pick_article("Produit", ["produit_fini", "marchandise"])
        if not art:
            return
        steps = self.mfg.get_routing(art["id"])
        plans = self.mfg.plans(art["id"])
        text = "GAMME OPÉRATOIRE\n" + "\n".join(
            f"{s['step_no']}. {s['description']} ({s['estimated_time'] or '—'}"
            f" — outillage : {s['tooling'] or '—'})" for s in steps) or "Aucune gamme."
        text += "\n\nPLANS\n" + ("\n".join(
            f"{p['number']} v{p['version']} : {p['file_path'] or '—'}" for p in plans)
            or "Aucun plan.")
        messagebox.showinfo(f"Protocole {art['sku']}", text)


class PurchasingTab:
    def __init__(self, app, frame: ttk.Frame, pur: PurchasingService):
        self.app, self.pur = app, pur
        top = ttk.Frame(frame)
        top.pack(fill="x")
        ttk.Button(top, text="Nouveau fournisseur", command=self.new_supplier).pack(side="left", padx=3)
        ttk.Button(top, text="Suggestions d'achat", command=self.show_suggestions).pack(side="left", padx=3)
        ttk.Button(top, text="Nouvelle commande", command=self.new_po).pack(side="left", padx=3)
        ttk.Button(top, text="Ajouter ligne", command=self.add_line).pack(side="left", padx=3)
        ttk.Button(top, text="Envoyer", command=self.send).pack(side="left", padx=3)
        ttk.Button(top, text="Réceptionner", command=self.receive).pack(side="left", padx=3)
        self.tree = _tree(frame, [("id", "ID", 30), ("number", "N°", 110),
                                  ("supplier_name", "Fournisseur", 170),
                                  ("status", "Statut", 110),
                                  ("total_ht", "Total HT", 90)])
        self.tree.pack(fill="both", expand=True, pady=4)
        self.lines_tree = _tree(frame, [("sku", "Réf.", 100),
                                        ("designation", "Désignation", 220),
                                        ("qty", "Qté", 60), ("received_qty", "Reçu", 60),
                                        ("price", "PU", 80)])
        self.lines_tree.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self.load_lines)
        self.load()

    def load(self) -> None:
        self.tree.delete(*self.tree.get_children())
        for p in self.pur.list_pos():
            self.tree.insert("", "end", values=(
                p["id"], p["number"], p["supplier_name"], label(p["status"]),
                f"{p['total_ht']:.2f}"))

    def _selected(self) -> dict | None:
        sel = self.tree.selection()
        if not sel:
            messagebox.showwarning("Sélection", "Sélectionnez une commande fournisseur.")
            return None
        return self.pur.get_po(self.tree.item(sel[0])["values"][0])

    def load_lines(self, _e=None) -> None:
        po = self._selected()
        if not po:
            return
        self.lines_tree.delete(*self.lines_tree.get_children())
        for l in self.pur.po_lines(po["id"]):
            self.lines_tree.insert("", "end", values=(
                l["sku"], l["designation"], l["qty"], l["received_qty"], l["price"]))

    def new_supplier(self) -> None:
        win = self.app._form_window("Nouveau fournisseur")
        values = self.app._form_fields(win, [
            ("name", "Nom *"), ("address", "Adresse"), ("email", "Email"),
            ("phone", "Téléphone"), ("payment_terms", "Conditions de paiement")])
        if values and values.get("name"):
            self.pur.create_supplier(dict(values, payment_terms=values.get("payment_terms") or "30 jours"))
            messagebox.showinfo("Fournisseur", "Fournisseur créé.")

    def show_suggestions(self) -> None:
        sugg = self.pur.suggestions()
        if not sugg:
            messagebox.showinfo("Suggestions", "Aucun besoin net : stocks suffisants.")
            return
        text = "\n".join(
            f"{s['sku']} : besoin {s['qty_needed']:g}, dispo {s['qty_available']:g}, "
            f"en commande {s['qty_on_order']:g} → commander {s['qty_suggested']:g}"
            for s in sugg)
        messagebox.showinfo("Suggestions d'achat (besoin net)", text)

    def new_po(self) -> None:
        suppliers = self.pur.list_suppliers()
        if not suppliers:
            messagebox.showwarning("Achats", "Créez d'abord un fournisseur.")
            return
        win = self.app._form_window("Nouvelle commande fournisseur")
        cb = ttk.Combobox(win, values=[f"{s['id']} — {s['name']}" for s in suppliers],
                          state="readonly", width=40)
        cb.grid(row=0, column=1)
        ttk.Label(win, text="Fournisseur").grid(row=0, column=0)
        self.app.wait_window(win)
        if not cb.get():
            return
        po = self.pur.create_po(int(cb.get().split(" — ")[0]))
        messagebox.showinfo("Achats", f"Commande {po['number']} créée (brouillon).")
        self.load()

    def add_line(self) -> None:
        po = self._selected()
        if not po:
            return
        art = self.app._pick_article("Article", None)
        if not art:
            return
        values = self.app._prompt_fields([
            ("qty", f"Quantité {art['sku']}"), ("price", "Prix (vide = meilleur prix connu)")])
        if not values or not values.get("qty"):
            return
        try:
            self.pur.add_line(po["id"], art["id"], float(values["qty"]),
                              float(values["price"]) if values.get("price") else None)
        except Exception as e:
            messagebox.showerror("Erreur", str(e))
        self.load_lines()

    def send(self) -> None:
        po = self._selected()
        if not po:
            return
        try:
            self.pur.send_po(po["id"])
            messagebox.showinfo("Achats", f"Commande {po['number']} envoyée.")
        except Exception as e:
            messagebox.showerror("Erreur", str(e))
        self.load()

    def receive(self) -> None:
        po = self._selected()
        if not po:
            return
        lines = self.pur.po_lines(po["id"])
        if not lines:
            return
        win = self.app._form_window(f"Réception {po['number']}")
        rows = []
        for i, l in enumerate(lines):
            ttk.Label(win, text=f"{l['sku']} (restant {l['qty'] - l['received_qty']:g})").grid(
                row=i, column=0)
            e = ttk.Entry(win, width=10)
            e.insert(0, str(l["qty"] - l["received_qty"]))
            e.grid(row=i, column=1)
            rows.append((l, e))
        ttk.Button(win, text="Réceptionner", command=win.destroy).grid(
            row=len(lines), column=0, columnspan=2, pady=6)
        self.app.wait_window(win)
        for l, e in rows:
            if e.get().strip():
                try:
                    self.pur.receive(po["id"], l["article_id"], float(e.get()))
                except Exception as ex:
                    messagebox.showerror("Réception", f"{l['sku']} : {ex}")
        self.load()
        self.load_lines()


class WarehouseTab:
    def __init__(self, app, frame: ttk.Frame, wh: WarehouseService,
                 sales: SalesService):
        self.app, self.wh, self.sales = app, wh, sales
        top = ttk.Frame(frame)
        top.pack(fill="x")
        ttk.Button(top, text="Picking list", command=self.picking).pack(side="left", padx=3)
        ttk.Button(top, text="Créer BL", command=self.create_bl).pack(side="left", padx=3)
        ttk.Button(top, text="Marquer livré", command=self.deliver).pack(side="left", padx=3)
        ttk.Button(top, text="Voir BL (HTML)", command=self.view_bl).pack(side="left", padx=3)
        self.tree = _tree(frame, [("id", "ID", 30), ("number", "N° BL", 100),
                                  ("order_number", "Commande", 110),
                                  ("customer_name", "Client", 170),
                                  ("status", "Statut", 100),
                                  ("delivered_at", "Livré le", 130)])
        self.tree.pack(fill="both", expand=True)
        self.load()

    def load(self) -> None:
        self.tree.delete(*self.tree.get_children())
        for n in self.wh.list_notes():
            self.tree.insert("", "end", values=(
                n["id"], n["number"], n["order_number"], n["customer_name"],
                label(n["status"]), n["delivered_at"] or ""))

    def _selected(self) -> dict | None:
        sel = self.tree.selection()
        if not sel:
            messagebox.showwarning("Sélection", "Sélectionnez un BL.")
            return None
        return self.wh.get_note(self.tree.item(sel[0])["values"][0])

    def _pick_order(self) -> dict | None:
        orders = [o for o in self.sales.list_orders()
                  if o["status"] in ("confirmee", "en_production", "prete")]
        if not orders:
            messagebox.showwarning("Magasin", "Aucune commande préparable.")
            return None
        win = self.app._form_window("Choisir une commande")
        cb = ttk.Combobox(win, values=[
            f"{o['id']} — {o['number']} ({o['customer_name']}, {label(o['status'])})"
            for o in orders], state="readonly", width=55)
        cb.grid(row=0, column=1)
        cb.current(0)
        ttk.Label(win, text="Commande").grid(row=0, column=0)
        self.app.wait_window(win)
        if not cb.get():
            return None
        return self.sales.get_order(int(cb.get().split(" — ")[0]))

    def picking(self) -> None:
        order = self._pick_order()
        if not order:
            return
        pick = self.wh.picking_list(order["id"])
        if not pick:
            messagebox.showinfo("Picking", "Rien à préparer.")
            return
        text = "\n".join(
            f"{p['sku']} : prendre {p['qty_to_pick']:g} → {p['locations']}"
            for p in pick)
        messagebox.showinfo(f"Picking — commande {order['number']}", text)

    def create_bl(self) -> None:
        order = self._pick_order()
        if not order:
            return
        try:
            bl = self.wh.create_delivery_note(order["id"])
            messagebox.showinfo("BL", f"BL {bl['number']} créé (préparation).")
        except Exception as e:
            messagebox.showerror("Erreur", str(e))
        self.load()

    def deliver(self) -> None:
        note = self._selected()
        if not note:
            return
        try:
            self.wh.deliver(note["id"])
            messagebox.showinfo("Livraison", "BL livré, stock et commande mis à jour.")
        except Exception as e:
            messagebox.showerror("Erreur", str(e))
        self.load()

    def view_bl(self) -> None:
        note = self._selected()
        if not note:
            return
        _show_html(self.wh.bl_html(note["id"]), f"BL_{note['number']}")


class InvoicingTab:
    def __init__(self, app, frame: ttk.Frame, bill: InvoicingService,
                 sales: SalesService):
        self.app, self.bill, self.sales = app, bill, sales
        top = ttk.Frame(frame)
        top.pack(fill="x")
        ttk.Button(top, text="Facturer une commande", command=self.invoice_order).pack(side="left", padx=3)
        ttk.Button(top, text="Encaisser", command=self.pay).pack(side="left", padx=3)
        ttk.Button(top, text="Avoir", command=self.credit).pack(side="left", padx=3)
        ttk.Button(top, text="Voir facture (HTML)", command=self.view).pack(side="left", padx=3)
        self.tree = _tree(frame, [("id", "ID", 30), ("number", "N°", 110),
                                  ("kind", "Type", 60), ("customer_name", "Client", 170),
                                  ("total_ttc", "TTC", 90), ("paid_amount", "Payé", 80),
                                  ("remaining", "Restant", 80), ("status", "Statut", 120)])
        self.tree.pack(fill="both", expand=True)
        self.load()

    def load(self) -> None:
        self.tree.delete(*self.tree.get_children())
        for kind in ("facture", "avoir"):
            for i in self.bill.list_invoices(kind):
                status = self.bill.invoice_status(i["id"])
                self.tree.insert("", "end", values=(
                    i["id"], i["number"],
                    "Avoir" if kind == "avoir" else "Facture",
                    i["customer_name"], f"{i['total_ttc']:.2f}",
                    f"{i['paid_amount']:.2f}", f"{i['remaining']:.2f}", status))

    def _selected(self) -> dict | None:
        sel = self.tree.selection()
        if not sel:
            messagebox.showwarning("Sélection", "Sélectionnez une facture.")
            return None
        return self.bill.get_invoice(self.tree.item(sel[0])["values"][0])

    def invoice_order(self) -> None:
        orders = [o for o in self.sales.list_orders()
                  if o["status"] in ("prete", "livree")]
        if not orders:
            messagebox.showwarning("Facturation", "Aucune commande prête/livrée à facturer.")
            return
        win = self.app._form_window("Facturer une commande")
        cb = ttk.Combobox(win, values=[
            f"{o['id']} — {o['number']} ({o['customer_name']})" for o in orders],
            state="readonly", width=50)
        cb.grid(row=0, column=1)
        cb.current(0)
        ttk.Label(win, text="Commande").grid(row=0, column=0)
        self.app.wait_window(win)
        if not cb.get():
            return
        try:
            inv = self.bill.create_invoice_from_order(int(cb.get().split(" — ")[0]))
            messagebox.showinfo("Facture", f"Facture {inv['number']} créée.")
        except Exception as e:
            messagebox.showerror("Erreur", str(e))
        self.load()

    def pay(self) -> None:
        inv = self._selected()
        if not inv:
            return
        values = self.app._prompt_fields([
            ("amount", f"Montant (restant dû {inv['total_ttc'] - inv['paid_amount']:.2f})"),
            ("method", "Moyen (virement, chèque, espèces)")])
        if not values or not values.get("amount"):
            return
        try:
            self.bill.register_payment(inv["id"], float(values["amount"]),
                                        values.get("method") or "virement")
        except Exception as e:
            messagebox.showerror("Erreur", str(e))
        self.load()

    def credit(self) -> None:
        inv = self._selected()
        if not inv or inv["kind"] != "facture":
            messagebox.showwarning("Avoir", "Sélectionnez une facture.")
            return
        values = self.app._prompt_fields([("amount", "Montant de l'avoir"),
                                          ("reason", "Motif")])
        if not values or not values.get("amount"):
            return
        try:
            av = self.bill.create_credit_note(inv["id"], float(values["amount"]),
                                              values.get("reason") or "")
            messagebox.showinfo("Avoir", f"Avoir {av['number']} créé.")
        except Exception as e:
            messagebox.showerror("Erreur", str(e))
        self.load()

    def view(self) -> None:
        inv = self._selected()
        if not inv:
            return
        _show_html(self.bill.invoice_html(inv["id"]),
                   f"{inv['kind']}_{inv['number']}")


class DashboardTab:
    def __init__(self, app, frame: ttk.Frame, dash: DashboardService):
        self.app, self.dash = app, dash
        ttk.Label(frame, text="Tableau de bord", font=("", 14, "bold")).pack(pady=6)
        self.counters_frame = ttk.Frame(frame)
        self.counters_frame.pack(fill="x")
        self.list_frame = ttk.Frame(frame)
        self.list_frame.pack(fill="both", expand=True)
        self.refresh()

    def refresh(self) -> None:
        for w in (self.counters_frame, self.list_frame):
            for child in w.winfo_children():
                child.destroy()
        data = self.dash.overview()
        counts = [
            ("Commandes en cours", len(data["orders_in_progress"])),
            ("OF ouverts", len(data["wos_open"])),
            ("Factures en retard", len(data["late_invoices"])),
            ("Factures impayées", len(data["unpaid_invoices"])),
            ("Alertes de stock", len(data["stock_alerts"])),
            ("Achats en attente", len([p for p in data["pending_pos"]
                                       if p["status"] != "recue"])),
            ("Valeur du stock", f"{data['stock_value']:.2f} €"),
        ]
        for i, (name, val) in enumerate(counts):
            box = ttk.LabelFrame(self.counters_frame, text=name, padding=10)
            box.grid(row=0, column=i, padx=4, sticky="n")
            ttk.Label(box, text=str(val), font=("", 16, "bold")).pack()
        tree = _tree(self.list_frame, [
            ("type", "Type", 130), ("ref", "Référence", 100),
            ("who", "Client/Article", 170), ("detail", "Détail", 260)])
        tree.pack(fill="both", expand=True)
        for o in data["orders_in_progress"]:
            tree.insert("", "end", values=("Commande", o["number"], o["customer_name"],
                                           label(o["status"])))
        for w in data["wos_open"]:
            tree.insert("", "end", values=("OF", w["number"], w["sku"],
                                           f"{w['qty_produced']:g}/{w['qty_to_produce']:g} — {label(w['status'])}"))
        for i in data["unpaid_invoices"]:
            tree.insert("", "end", values=("Facture", i["number"], i["customer_name"],
                                           f"restant {i['remaining']:.2f} — {self.dash.invoicing.invoice_status(i['id'])}"))
        for a in data["stock_alerts"]:
            tree.insert("", "end", values=("Alerte stock", a["sku"], a["designation"],
                                           f"dispo {a['available_qty']:g} / mini {a['min_stock']:g}"))
        for p in data["pending_pos"]:
            if p["status"] != "recue":
                tree.insert("", "end", values=("Achat", p["number"], p["supplier_name"],
                                               label(p["status"])))
