# Changelog — Blendshape Editor Tool

## v.05.01

**UI — Shelf restructure**

- Sculpt brushes reorganized into a 4×2 grid: Grab / Flatten / Bulge / SmoothTarget (row 1), Smooth / Relax / Pinch / Erase (row 2)
- Smooth replaces Amplify as the fourth row-2 sculpt brush
- Visual separators with symmetric spacing (`spacing=8`) between button groups
- Row 1: 4 sculpt | Shape Editor / Clean BS Node / Reset All Targets | Exit Delta View
- Row 2: 4 sculpt | Add Target / Create Opposite / Connect A→B | Delta View

**UI — Actions section removed**

- Create Opposite moved to shelf row 2 — right-click to pick axis (Object X/Y/Z, Topology)
- Apply Moves and Bake Deformers moved into the Modify Deltas section
- Duplicate, Mirror and Flip buttons removed from the main UI

**UI — Secondary Meshes section removed**

- Extract Wrap and Extract Only moved into a "Wrap Setup" QGroupBox at the top of the Tools panel
- Connect A→B moved to shelf row 2

**UI — Tool Settings**

- New inline collapsible block (Edge Loop Options style: arrow QToolButton + hidden widget) wrapped in a QGroupBox, placed above the shelf
- Groups Strength, Radius, Falloff and Symmetry controls

**UI — Falloff**

- Label shortened: "Falloff Type" → "Falloff"
- Default mode: Surface (index 1)

**UI — Strength**

- Default value: 50
- Decimals: 3
- Spin buttons removed (`NoButtons`)

**UI — Radius**

- Spin buttons removed (`NoButtons`)

**Safety — Confirmation dialogs**

- Nullify: warning dialog when no vertices are selected, listing affected targets, with Continue / Cancel buttons (Cancel as default)
- Swap: same dialog when no vertices are selected

---

## v.05.00

**Wrap Extract — multi-mesh support**

- First selected mesh is the master (source of targets); all subsequent meshes are receivers
- Works with any number of receivers in a single operation
- If a receiver has no blendShape node, a dialog proposes to create `{meshName}_bs` automatically
- If no targets are selected in the Shape Editor, all targets are wrapped and near-zero results (magnitude < 0.001) are pruned automatically
- Status bar reports per-receiver: targets kept, added, replaced, pruned

**Joints Setup tool**

- New `Joints Setup` section (under Wire Setup in the Tools panel)
- `build_lip_rig`: builds a joint-based lip rig from an edge loop — motionPath orientation baked into rest pose, joints skin-bound to the mesh

**Modify Deltas — Add / Sub / Swap**

- `Add Delta`: adds the delta of a source target on top of the current target
- `Sub Delta`: subtracts the delta of a source target from the current target
- `Swap Delta`: swaps delta data between two targets in one operation

**Modify Deltas — Opacity slider extended**

- Slider now controls Hammer and Average operations in addition to Smooth/Relax
- Hammer: `n_passes = max(1, int(round(opacity × 20)))` → 1–20 passes
- Average: `opacity` used directly as lerp factor between original and averaged value

**Tools Shelf — settings bar**

- New persistent bar above the sculpt shelf: `Strength` slider, `Falloff Type` combo, `Symmetry` toggle
- Values are read by all sculpt-brush buttons at execution time

**Bug fixes**

- Multiply / Nullify: now correctly applies to all selected targets (was only acting on the first)
- Split group reorder: fixed index collision when reordering locator groups
- Bake Deformers: corrected delta accumulation formula (was double-counting the base pose)
- Wrap Extract (no-selection mode): weights with incoming connections now properly disconnected/reconnected

---

## v.04.05

- Wire Setup: default rotation value set to `0.15`
- Partial mirror on vertex selection: Mirror and Flip buttons operate only on selected vertices when a vertex selection is active

---

## v.04.04

- Opacity slider for Smooth/Relax: `n_passes = max(1, int(round(opacity × 10)))` → 1–10 passes
- Bake Deformers: fix — deformer contribution now computed as `(deformed − base)` delta added on top of existing target deltas
- BS weight disconnect/reconnect unified across Split, Wrap and Bake to handle combination (driven) targets cleanly

---

## v.04.03

**Rig Connector — Combo Driver: extended syntaxes**

- Support for `node.attr` form: any Maya attribute can be used as a combo driver (e.g. `FKJaw_ctrl.zip`)
- `rev:` prefix: inserts a `reverse` node (1 − input) before the gate — the shape is blocked when the attribute is active (e.g. `rev:FKJaw_ctrl.retain`)
- `rev_{shape}_{i}` nodes included in rebuild cleanup and `disconnect_rig_shapes`
- Combo Driver column tooltip updated to document all three syntaxes

**Rig Connector — Add Row from Shape Editor**

- `Add Row` button reads the current Shape Editor selection (`getShapeEditorTreeviewSelection`)
- Adds one row per selected target, with the name pre-filled
- Falls back to one empty row if nothing is selected in the Shape Editor

---

## v.04.02

**Rig Connector — Major UI simplification**

- Table reduced from 10 to 8 columns: removed `Dir` and `Custom Attr` columns
- Direction encoded in the sign of `In Max` (negative value = activation on the negative side of the controller)
- `Attr` column becomes an editable QComboBox: standard attributes as suggestions, free typing for custom attrs
- `In Max` accepts negative values (range -9999..9999)
- Load mapping: backward compatibility — old JSONs with `"direction"/"custom_attr"` keys converted automatically

**Rig Connector — Auto-stagger redesigned**

- Replaced the two mutually-exclusive `Mirror` / `Symmetric` checkboxes with a `Mode` combo: `Linear / Mirror / Symmetric`
- `Symmetric`: outer shapes activate last, center stays near 0 (brows, cheekbones)
- `Mirror`: center activates last, outer shapes first (zip lips)
- `+/−` checkbox enabled only in Symmetric mode
- Falloff renamed `Smooth`
- Symmetric stagger In Max/Min produces signed values directly

**Rig Connector — Combo Driver (formerly Gate)**

- New `Combo Driver` column: one or more bs_node targets separated by commas
- Inserts `multDoubleLinear` nodes (`gate_{shape}_{i}`) in series between `clamp.outputR` and `bs_node.w[idx]`
- The shape only activates when all combo drivers are also active

**Modify Deltas — Nullify button**

- `Nullify` button next to `Multiply Deltas`: zeros all deltas in one click

## v.04.01

**Rig Connector — Save/Load Mapping: proxy row support**

- `_collect_rows` now saves a `"proxy": true/false` flag in the JSON.
- `_load_mapping` correctly recreates proxy rows (pass 1: primary rows, pass 2: proxy rows via `_add_proxy_row`).
- Renumbering in `_remove_rows` no longer replaces the `↳` marker with a number.

**Rig Connector — Auto-stagger redesigned**

- Auto-stagger now operates on the **selected rows** in the table, not on a name pattern.
- Automatically creates proxy rows with the master controller and a symmetric stagger:
  extremes (first/last) → `in_max = in_max_ref`, center → `in_max = 0` (disabled).
- If a proxy row for that controller already exists, it is updated without duplication.
- Automatic proxy row creation in Apply Stagger has been removed (was causing duplicates).

**Rig Connector — InMin / InMax spinboxes**

- Locale forced to English: dot used as decimal separator.
- InMax now accepts `0` (driver disabled); minimum value changed from `0.001` to `0.0`.

**Build & Connect — Rebuild robustness**

- Removed the `disconnectAttr` loop that could fail the build on locked connections (combination targets, locked attrs).
- If all drivers for a shape are disabled (`in_max=0`), the network is not created (status `skip`).
- Disabled drivers no longer contribute to `pending_limits` (was blocking the controller at 0).
- `pending_conds` uses the real node name returned by `cmds.createNode` (avoids Maya auto-rename errors).

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
