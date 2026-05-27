# Changelog — Blendshape Editor Tool

## v.04.02

**Rig Connector — Simplification UI majeure**

- Table passe de 10 à 8 colonnes : suppression des colonnes `Dir` et `Custom Attr`
- Direction encodée dans le signe de `In Max` (valeur négative = activation côté négatif du controller)
- Colonne `Attr` devient une QComboBox éditable : attributs standards en suggestion, frappe libre pour les attrs custom
- `In Max` accepte des valeurs négatives (range -9999..9999)
- Load mapping : compat ascendante — anciens JSONs avec `"direction"/"custom_attr"` convertis automatiquement

**Rig Connector — Auto-stagger repensé**

- Remplacement des deux checkboxes `Mirror` / `Symmetric` (mutuellement exclusives) par un combo `Mode` : `Linear / Mirror / Symmetric`
- `Symmetric` : les shapes extérieures activent en dernier, le centre reste proche de 0 (brows, cheekbones)
- `Mirror` : le centre active en dernier, les extérieures en premier (zip lips)
- Checkbox `+/−` activée seulement en mode Symmetric
- Falloff renommé `Smooth`
- In Max/Min du stagger Symmetric produit des valeurs signées directement

**Rig Connector — Combo Driver (ex-Gate)**

- Nouvelle colonne `Combo Driver` : un ou plusieurs targets bs_node séparés par des virgules
- Insère des nœuds `multDoubleLinear` (gate_{shape}_{i}) en série entre `clamp.outputR` et `bs_node.w[idx]`
- La shape ne s'active que si tous les combo drivers sont également actifs

**Modify Deltas — Bouton Nullify**

- Bouton `Nullify` à droite de `Multiply Deltas` : met tous les deltas à 0 en un clic

## v.04.01

**Rig Connector — Save/Load Mapping : support des proxy rows**

- `_collect_rows` enregistre maintenant un flag `"proxy": true/false` dans le JSON.
- `_load_mapping` recrée correctement les proxy rows (passe 1 : rows primaires, passe 2 : proxy rows via `_add_proxy_row`).
- La renumérotation dans `_remove_rows` ne remplace plus le marqueur `↳` par un numéro.

**Rig Connector — Auto-stagger repensé**

- L'Auto-stagger opère désormais sur les **rows sélectionnées** dans le tableau, pas sur un pattern de nom.
- Crée automatiquement des proxy rows avec le master controller et un stagger symétrique :
  extrêmes (premier/dernier) → `in_max = in_max_ref`, centre → `in_max = 0` (désactivé).
- Si une proxy row pour ce controller existe déjà, elle est mise à jour sans doublon.
- La création automatique de proxy rows dans Apply Stagger a été supprimée (créait des doublons).

**Rig Connector — Spinboxes InMin / InMax**

- Locale forcée en anglais : le point est utilisé comme séparateur décimal.
- InMax accepte désormais `0` (driver désactivé) ; la valeur minimale passe de `0.001` à `0.0`.

**Build & Connect — Robustesse au rebuild**

- Suppression du loop `disconnectAttr` qui pouvait faire échouer le build sur des connexions locked (combination targets, attrs verrouillés).
- Si tous les drivers d'une shape sont désactivés (`in_max=0`), le réseau n'est pas créé (status `skip`).
- Les drivers désactivés ne contribuent plus à `pending_limits` (évitait de bloquer le controller à 0).
- `pending_conds` utilise le nom réel du nœud retourné par `cmds.createNode` (évite les erreurs de renommage automatique Maya).

---

## v.04.00

**Edge Loop Split — persistent setup fields**

- The Edge Loop Split no longer reads from the live viewport selection.
  Three dedicated persistent fields must be filled once per session:
  *Upper Vtx*, *Lower Vtx*, and *Edgeloop* (captured via **Get** buttons).
- The three fields are grouped under a collapsible **Edge Loop Options**
  disclosure row.
- The `seed_upper` / `seed_lower` naming is now used throughout the
  core function, replacing the ambiguous `seed_a` / `seed_b`.

**Center side renamed M_ → C_**

- All auto-generated center/bilateral targets now use `C_` as the side token.
- Default side tokens in the Naming Convention dialog: `R` / `C` / `L`.
- The built-in Check Shapes default list updated accordingly.

**Check Shapes — external JSON**

- Reference list stored in `resources/check_shapes_default.json` (shipped).
- A **File** menu (Load… / Save… / Reset to Default) replaces the old toolbar.
- Last loaded file path remembered across sessions.
- Current file name shown in the dialog title bar.

**Check Shapes — Match Existing to List**

- New **Match existing to List** button opens a *Rename Suggestions* dialog.
- Matches targets using token sets (order-independent).
- Detects targets missing a side prefix (`C_`, `L_`, `R_`) and proposes adding it.
- Ambiguous matches highlighted in orange *(Not sure)* and unchecked by default.

**Naming Convention — side token fields**

- Three new fields: **Left**, **Center**, **Right** (defaults: `L`, `C`, `R`).
- Split and symmetric operations read these values at runtime.

**Actions section — topology edge field moved to top**

- The **Edge** field (topology-symmetry centered edge) is now the first
  control in the Actions section, above all operation buttons.

**Maya Tools Shelf — second row**

- Row 2 adds three sculpt brushes: **Relax**, **Pinch**, **Amplify**
  (all with double-click to open Tool Settings).
- **Add Target**, **Clean Blendshape Node**, and **Reset All Targets to 0**
  grouped together in Row 2.

**New buttons**

- **Reset All Targets to 0** (shelf row 2) — sets every target weight to 0.
- **Bake Deformers** (Actions section) — bakes deformer stack contribution
  into selected targets in one pass.

**Actions section — open by default**

**Bug fixes**

- Fixed scroll jump to top when toggling any collapsible section.
- Fixed flickering double-open when dragging the installer a second time.
- UI position and dock state preserved between sessions (`retain=True`).
- Removed `importlib.reload` from the shelf button command.

---

## v.03.003

- Naming Convention dialog (token order, prefix, custom opposite-target pairs)
- Rename Targets: Set Prefix / Suffix + Search & Replace directly from the UI
- Swap Target Names (2-target exchange, deltas untouched)
- Nomenclature section — compact state removed
- Add Target button: right-click menu (Add Empty / Add Selection / Corrective)
- Create Locator now adds the new locator to the table automatically
- Remove Locator supports multi-row selection; side/suffix refresh on remove
- Tools section added (open by default)
- Wire Setup: curve-based lip/mouth rig (Create Wire Setup + Bake Wire to Mesh)
  - Configurable Dropoff, Rotation, Spans, Flat Curve
  - Pre-bake empty-shape warning with user confirmation
  - *Delete Wire Setup after Bake* option

---

## v.03.002

- Dockable UI via `MayaQWidgetDockableMixin` (`maya.app.general.mayaMixin`)
- New **Secondary Meshes** section: Extract Wrap Targets, Extract Only, Connect Targets A→B
- Maya Tools Shelf (Grab, Flatten, Bulge | ShapeEditor, SmoothTarget, Erase)
- Delta View / Exit Delta View moved to shelf
- Removed "Selected Targets" section
- Nomenclature moved above Locators, starts collapsed
- Compact-default sections (Nomenclature, Secondary Meshes, Modify Deltas) with bounce cycle
- Version label in footer, removed from window title

---

## v1.0.0

- Initial release
- Radial split using 1 to N locators with quintic smootherstep falloff
- Multi-axis support: XZ / X / Z / Y / YZ
- Symmetric L_ / R_ generation (single locator)
- Adaptive naming: descriptive (1-3 locators) / alphabetical (4+)
- Mirror Target, Flip Target, Create Opposite Target
- PySide6 interface with custom icons
- Status label at the bottom of the window
- Tooltips on all controls
- Undo chunk wrapping on all operations
- Multiply Deltas: scales all vertex deltas by a given factor
