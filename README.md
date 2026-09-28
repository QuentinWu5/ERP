# ERP modulaire — V1 Tkinter (Phases 1 à 6 + RH + Projets)

ERP simple et efficace en Python + SQLite pour suivre un produit de bout en bout :
commande client → AR → fabrication (OF) → livraison (BL) → facturation → paiement.
Blocs RH (salariés, congés, paie) et Projets & Calendrier (livrables, calendrier
partagé, rappels email SMTP) inclus.

- **Manuel utilisateur : [`docs/MANUEL.md`](docs/MANUEL.md)**

## Installation

- Python 3.10+ (tkinter inclus, aucune dépendance externe)

```bash
python main.py            # crée/ouvre erp.db + migrations + démo, ouvre l'UI
python main.py mabase.db  # base personnalisée
```

## Tests

```bash
python3 tests/test_phase1.py   # cœur, paramètres, inventaire, nomenclature, CSV
python3 tests/test_e2e.py      # flux complet commande → paiement + cas limites
python3 tests/test_hr.py       # RH : salariés, congés, paie
python3 tests/test_projects.py # projets, import commandes, calendrier, rappels
```

## Structure

```
main.py                    Lancement (migrations + seed + UI Tkinter)
erp/
  db/                      Couche data : TOUT le SQL (SQLite WAL, migrations v1/v2)
  services/                Logique métier — 100 % indépendante de l'UI (réutilisable en P7 web)
    settings_service.py    Société, numérotation, modules, audit
    inventory_service.py   Articles, mouvements, CMUP, emplacements, inventaire, CSV
    bom_service.py        BOM multi-niveaux, versions, coût, explosion
    sales_service.py      Clients, commandes, AR, réservations
    manufacturing_service.py  OF, contrôle composants, consommation, gammes, plans
    purchasing_service.py  Fournisseurs, besoin net, PO, réceptions
    warehouse_service.py   Picking, BL, livraisons partielles
    invoicing_service.py   Factures, paiements, avoirs, documents HTML
    dashboard_service.py   Vue d'ensemble + compteurs
  ui/                      Tkinter à onglets (aucune logique métier)
erp/seed.py                Jeu de démonstration
csv_samples/               Modèles CSV (articles, BOM, clients, fournisseurs)
tests/                     Tests services + end-to-end
docs/MANUEL.md             Manuel utilisateur
```

## Flux couvert (testé de bout en bout)

commande → AR → manque stock → suggestion d'achat → commande fournisseur →
réception (entrée stock + CMUP) → OF → lancement (réservation composants) →
production (consommation + écarts + entrée produit fini) → picking → BL →
livraison → facture (partielle possible) → encaissements partiels → avoir.

## Exigences transverses

- **Traçabilité** : chaque mouvement de stock lié à un document source ; journal d'audit.
- **Cohérence** : transactions SQLite (savepoints imbriquables), stock négatif bloqué, réservations atomiques.
- **Recherche** : articles par SKU/désignation ; filtres par statut sur commandes/OF/factures.
- **Données** : export CSV de l'état de stock ; imports CSV articles/BOM/clients/fournisseurs avec rapport d'erreurs ligne par ligne.
- **Robustesse** : WAL, sauvegarde datée (menu Fichier).
- **Simplicité** : un script `main.py`, aucune configuration.

## Multi-poste

V1 mono-poste : SQLite local. 2-3 utilisateurs max (transitoire) : base sur un
poste partagé. Vrai multi-PC : P7 FastAPI + PostgreSQL/MySQL — `erp/db/` et
`erp/ui/` seulement seront adaptées, `erp/services/` est conservé tel quel.

## Phase 7 (à venir)

Interface web FastAPI multi-utilisateurs avec la même couche métier.
