# Manuel utilisateur — ERP modulaire (V1 Tkinter)

*Version 1.0 — Phases 1 à 6*

---

## 1. Démarrage

```bash
python main.py
```

Au premier lancement, l'application :
1. crée la base `erp.db` (à côté du projet),
2. applique les migrations (schéma versionné),
3. charge le jeu de démonstration (articles, stock initial, nomenclature),
4. ouvre la fenêtre principale à onglets.

Pour utiliser une autre base : `python main.py mabase.db`.

**Onglets par défaut** : Tableau de bord, Paramètres, Inventaire, Nomenclature, Ventes, Fabrication, Achats, Magasin, Facturation. Chaque bloc peut être désactivé dans **Paramètres → Modules actifs** (les données sont conservées).

**Sauvegarde** : menu `Fichier → Sauvegarde de la base…` → copie datée de `erp.db`. À faire régulièrement (par ex. copie sur clé USB chaque soir).

---

## 2. Le flux métier complet

```
Commande client → AR → stock suffisant ? ── Oui → Réservation
                                    └─ Non → Commande fournisseur → Réception
                                              → Entrée en stock → OF → Fabrication
   Produit fini → Picking list → BL → Livraison → Facture → Paiement
```

**Recette de démonstration** (à suivre dans l'ordre, avec le jeu de données fourni) :

1. **Ventes** → *Nouvelle commande* : client `Client Test SARL`, ajouter la ligne `CTRL-100` × 10 @ 149 €.
2. **Ventes** → *Confirmer (AR)* : la commande passe en *Confirmée*, l'AR `AR-2026-0001` est généré. Le produit fini n'a pas assez de stock → pas de réservation, il faut fabriquer.
3. **Fabrication** → *Nouvel OF* : produit `CTRL-100`, quantité 10. Puis *Lancer (contrôle stock)* : les composants (boîtier, PCB, vis, câble, emballage) sont réservés.
4. **Fabrication** → *Déclarer production* : quantité produite 10. Les composants sortent de stock, 10 produits finis entrent en stock, la commande passe en *Prête*. Les écarts théorique/réel sont visibles via *Voir écarts*.
5. **Magasin** → *Picking list* (emplacements), puis *Créer BL*, puis *Marquer livré* : sortie de stock, commande *Livrée*.
6. **Facturation** → *Facturer une commande* : facture `FAC-2026-0001` (TVA 20 %). Puis *Encaisser* : saisir le montant → statut *Partiellement payée* / *Payée*.

Tous les documents (AR, BL, facture, avoir) sont consultables en HTML via les boutons *Voir … (HTML)* — imprimables ou copiables dans Word.

---

## 3. Tableau de bord

Vue d'ensemble en temps réel :
- compteurs : commandes en cours, OF ouverts, factures en retard/impayées, alertes de stock, achats en attente, valeur totale du stock (valorisation CMUP) ;
- liste détaillée en dessous (commandes, OF, factures impayées avec restant dû, ruptures de stock avec dispo/mini, commandes fournisseurs).

---

## 4. Paramètres

- **Société** : nom, adresse, SIRET, n° TVA, logo, devise, TVA par défaut (20 %). Ces informations apparaissent sur les documents.
- **Politique OF** : `warn` (défaut) = avertissement + proposition de commande fournisseur si un composant manque au lancement ; `block` = lancement interdit.
- **Modules actifs** : cochez/décochez les blocs, puis *Appliquer* (les onglets apparaissent/disparaissent).
- **Journal d'audit** : toutes les actions tracées (qui, quoi, quand).

---

## 5. Inventaire

- **Fiche article** : référence (SKU), désignation, type (produit fini / composant / consommable / marchandise / service), unité, prix d'achat, prix de vente, TVA, stock mini, suivi par lots.
- **Stocks** : physique / réservé / disponible ; **CMUP** recalculé à chaque entrée ; valeur du stock par article.
- **Mouvements tracés** : entrée, sortie, ajustement, transfert — chaque mouvement porte un motif, un document source, la date et le CMUP résultant. L'historique de l'article sélectionné s'affiche en dessous de la liste.
- **Emplacements** : magasin/rayon (ex. `MAG/A1`), plusieurs emplacements par article.
- **Inventaire physique** : ouverture d'un comptage, saisie des quantités réelles, affichage des écarts, validation → ajustements tracés.
- **Alertes** : un article est en alerte quand disponible ≤ stock mini (visible au Tableau de bord).
- **Import/Export** (menu `Import / Export`) : importer articles + stock initial depuis CSV (mouvement « reprise initiale » tracé), exporter l'état de stock en CSV.

**Formats CSV** (séparateur `;`, UTF-8, modèles dans `csv_samples/`) :

`articles.csv` : `reference;designation;type;unite;prix_achat;prix_vente;tva;stock_initial;emplacement;cout_init;stock_mini`

`boms.csv` : `produit;version;composant;quantite;rebuts_pct`

---

## 6. Nomenclature

- **BOM multi-niveaux** : chaque composant peut avoir sa propre nomenclature ; les cycles sont détectés et bloqués.
- **Versions** (A, B…) et **variants** (couleur, taille…) : la création d'une nouvelle version la rend active ; on peut réactiver une ancienne version via *Activer cette version*.
- **Lignes** : quantité par produit + % de rebuts/pertes.
- **Explosion / Coût** : déroulé complet multi-niveaux avec quantités réelles (rebuts inclus) et coût matière standard.
- **Où utilisé ?** : liste de tous les produits parents d'un composant.

---

## 7. Ventes

- **Clients** : coordonnées, conditions de paiement, TVA applicable. (Import CSV possible.)
- **Commandes** : lignes produits/services, quantités, prix, remises (par ligne), date de livraison souhaitée. Numérotation `CMD-2026-0001`.
- **Confirmation** : réserve le stock disponible (marchandises), génère l'AR. Un produit fini en stock insuffisant ne bloque pas : il passe par un OF. Un composant/composant manquant bloque la confirmation.
- **Statuts** : Brouillon → Confirmée → En production → Prête → Livrée → Facturée (ou Annulée). L'annulation libère les réservations.
- **AR** : document HTML imprimable.

---

## 8. Fabrication

- **OF** (Ordre de Fabrication) : `OF-0001`, lié à une commande client ou en stock, basé sur la nomenclature active.
- **Lancement** : contrôle de disponibilité de tous les composants (explosion de la BOM × quantité) ; réservation de ce qui est disponible ; selon la politique : avertissement + proposition de commande fournisseur, ou blocage.
- **Production** : saisie de la quantité produite (et des rebuts). Consommation réelle des composants (modifiable ligne à ligne par le service), entrée du produit fini valorisée au coût des composants consommés. La commande cliente repasse en *Prête*.
- **Écarts** : comparaison théorique/consommé par composant, conservée pour traçabilité.
- **Gammes** : étapes numérotées (description, temps estimé, outillage, plan) consultables via *Gamme*.
- **Plans** : numéro de plan, version, chemin de fichier, rattachés au produit.

---

## 9. Achats

- **Fournisseurs** : fiche + prix d'achat par article (historique des prix, meilleur prix proposé automatiquement).
- **Suggestions d'achat** : calcul du besoin net = besoin (OF ouverts + commandes clients confirmées + stock mini) − stock disponible − quantités déjà en commande.
- **Commande fournisseur** (`CDF-2026-0001`) : brouillon → envoyée → reçue (partielle ou totale).
- **Réception** : entrée en stock automatique avec CMUP, mise à jour du prix fournisseur. La quantité reçue ne peut pas dépasser le restant dû.

---

## 10. Magasin

- **Picking list** : quantités restant à livrer par commande, avec emplacements.
- **BL** (`BL-2026-0001`) : préparation → livré (sortie de stock, libération des réservations, mise à jour de la commande).
- **Livraisons partielles** : le BL ne porte que les lignes livrables ; la commande reste *Prête* jusqu'à livraison complète.

---

## 11. Facturation

- **Facture** (`FAC-2026-0001`) : depuis une commande prête/livrée ; ne facture que les quantités livrées non encore facturées (facturation partielle possible). TVA par ligne, conditions de paiement, échéance calculée.
- **Paiements** : encaissements partiels multiples ; statut En attente / Partiellement payée / Payée / **En retard** (échéance dépassée, visible au Tableau de bord).
- **Avoirs** (`AVC-2026-0001`) : montant libre sur une facture, motif obligatoire.
- **Documents** : facture HTML imprimable avec en-tête société, totaux HT/TVA/TTC, payé et restant dû.

---

## 12. RH (ressources humaines)

- **Salariés** : fiche complète (matricule auto `EMP001`, nom, poste, service, contrat CDI/CDD/stage, date d'embauche, salaire de base). Le module s'active/désactive comme les autres dans *Paramètres*.
- **Congés** : demande avec type (congé payé, RTT, maladie, sans solde), dates ; le compte des **jours ouvrés** est automatique (week-ends exclus). Workflow *Soumise → Approuvée / Refusée*, annulation possible d'une demande approuvée (solde restitué).
- **Soldes** : acquisition de **2,5 j/mois** depuis l'embauche ; solde = acquis − approuvés (congés payés + RTT). L'approbation au-delà du solde est bloquée.
- **Paie** : bulletin individuel ou **génération du mois** en un clic (un bulletin par salarié actif, doublons interdits). Brut = base + primes + heures sup ; cotisations salariales (22 % par défaut) et patronales (42 %) paramétrables dans *Paramètres* (`hr_cotisation_salariale` / `hr_cotisation_patronale`) ; net = brut − cotisations salariales. Statut *À payer / Payé*.
- **Bulletin HTML** : imprimable, en-tête société, détail base/primes/brut/cotisations/net.

---

## 13. Projets & Calendrier

- **Projets** (`PRJ-2026-0001`) : manuels ou **importés d'une commande client** en un clic — les lignes de commande deviennent des livrables (tâches), l'échéance du projet = date de livraison souhaitée.
- **Tâches / livrables** : titre, échéance, assignée à un salarié, statut (À faire / En cours / Terminé / Annulé).
- **Calendrier partagé** : vue mensuelle (navigation ◀ ▶) affichant sur chaque jour :
  - 🌴 les congés approuvés (plage de dates),
  - 📦 les échéances de projets,
  - ✅ les tâches à livrer,
  - 🚚 les livraisons clients (date souhaitée des commandes),
  - ⚙️ les OF en cours.
- **Rappels email** : « Rappels email… » détecte les échéances des N prochains jours (défaut 7) et envoie un email récapitulatif. Anti-doublon : un rappel n'est envoyé qu'une fois par échéance.
- **Configuration SMTP** (onglet Paramètres) : `smtp_host`, `smtp_port` (587), `smtp_user`, `smtp_password`, `smtp_from`, et `reminder_emails` (destinataires par défaut, séparés par des virgules). Sans SMTP configuré, les rappels sont enregistrés et l'envoi reprendra une fois les Paramètres renseignés.

---

## 14. Multi-poste (transition avant le web)

- **V1 mono-poste** (recommandé) : SQLite local, aucun réglage.
- **2-3 postes, faible trafic** : placer `erp.db` sur un poste partagé (lecteur mappé) ; le mode WAL est déjà activé. Risque de verrouillage si écritures simultanées fréquentes.
- **Vrai multi-PC** : prévu en phase 7 (interface web FastAPI + migration PostgreSQL/MySQL). La couche métier (`erp/services/`) et la couche data (`erp/db/`) seront réutilisées sans réécriture.

---

## 15. Questions fréquentes

**« Stock négatif interdit »** — Le système bloque toute sortie qui rendrait le stock négatif. Vérifiez les réceptions en attente ou ajustez via *Inventaire physique*.

**La commande ne passe pas en « Livrée »** — Des lignes restent à livrer (livraison partielle) ou une ligne service n'est pas soldée : les lignes service sont soldées au moment de la livraison.

**Je ne peux pas modifier une ligne de commande** — La commande est confirmée : annulez-la (libère les réservations) ou créez une nouvelle commande.

**Réinitialiser la démo** — Supprimez `erp.db` et relancez `python main.py`.
