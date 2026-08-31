# Changelog — Blendshape Editor

## v.05.58

**Wire & CTJ — Weight tools, UI overhaul, file system, Edge Loop Split fix**

- `@undo_chunk` added to all four weight methods: `_run_smooth_wire_weights`, `_run_hammer_wire_weights`, `_run_smooth_ctj_weights`, `_run_hammer_ctj_weights` — a single Ctrl+Z now undoes the entire operation
- `_run_smooth_ctj_weights` replaced `artAttrCtx` MEL approach (which deselected vertices and opened the Paint tool) with the same custom Python Laplacian flood used by the wire smooth: `polyInfo(edgeToVertex=True)` adjacency + `cmds.percent()` read/write
- Smooth and hammer (wire + CTJ) now use a relax formula `old_w + (avg_neighbors − old_w) * 1.0` instead of pure Laplacian snap — structure in place for easy opacity adjustment
- **Custom file extensions**: rig mapping → `.mapng`, split locs presets → `.splt` (`.wirepreset` already existed); all save/open dialogs updated; old `.json` files accepted as fallback in open dialogs
- **`bse_type` marker**: each file type embeds `"bse_type"` on save (`"rig_mapping"`, `"split_presets"`, `"wire_cv_presets"`); `_check_bse_type()` module-level helper validates on import and shows a clear error if the wrong file type is loaded; files without the key (old JSON) pass silently
- Rig mapping field placeholder and label updated to `.mapng`; `wirepreset` export now wraps presets under `{"bse_type": ..., "presets": {...}}`; import handles both old bare-dict and new wrapped format
- **Wire Setup UI — sidebar**: removed the "new shape name" field; `[Save] [Apply] [+] [−]` buttons moved to a vertical sidebar to the right of the Shape Curves table; `+` adds a row with a generated unique name and opens inline editing immediately; Save/Apply labels shortened (tooltips clarify CV preset purpose)
- CV preset row above the table now contains only `[Import] [Export]` icon buttons (Save/Apply moved to sidebar)
- **Wire Setup — isolate on create**: after `create_wire_setup()`, `wire_setup_grp` is isolated in the active model panel via `cmds.isolateSelect`; `_active_model_panel()` helper returns the focused panel or first visible model panel
- **Wire Setup — bake cleanup**: on bake (success or failure), exits isolate mode if active; if "Delete setup at bake" is OFF, hides `wire_setup_grp`; forces `visibility = 1` on the base mesh via `showHidden` + `setAttr`
- **Edge Loop Split — overwrite targets**: `_write_weighted_target` now saves outgoing and combo-incoming connections, deletes the old blendShape slot, recreates the target, then restores all connections — identical behaviour to the classic split overwrite

## v.05.57

**Wire Setup — AutoPaint overhaul**

- Replaced Euclidean distance with **geodesic distance** (on-surface shortest-path) in `autopaint_wire_weights()`: builds a mesh edge adjacency list via `MItMeshEdge`, then runs multi-source Dijkstra from all seed vertices simultaneously — falloff now correctly follows the lip surface topology instead of cutting through empty space
- Updated default Min/Max distances to **1.0 / 3.0** (calibrated for a 1 m 70 character in Maya scene units)
- Loop completion replaced `polySelectSp` with `cmds.polySelect(edgeLoopOrBorder=N, noSelection=True)` — purely computational, returns indices directly without touching the active selection or requiring viewport context; seeds from the first edge in the Edges field
- Removed unreliable topological closure check (valence == 2); reliability is now determined solely by whether `polySelect` returned more edges than the input
- `_WireLoopFallbackDialog` changed from blocking `exec_()` / `WindowModal` to non-blocking `show()` / `NonModal`; Continue and Cancel wired via `accepted`/`rejected` signals to a new `_apply_wire_autopaint()` helper so the user can interact with the viewport while the dialog is open
- `wire_crv` is now hidden after wire setup creation; the first shape curve in the list is shown by default

## v.05.56

**Wire Setup — Compute AutoPaint**

- New toggle "Compute AutoPaint" on the same row as Flat Curve (Flat Curve moved to end of row)
- When ON: after Create Wire Setup, the tool auto-completes the user's half edge loop into a full closed ring via `polySelectSp(loop=True)`, then paints wire deformer vertex weights on `wire_setup_msh` using euclidean distance + smooth-step IDW falloff
- Min (default 3.0) and Max (default 6.0) QLineEdits control the falloff range in world units; both are greyed out when the toggle is OFF
- Closure check: if the auto-completed loop is not topologically closed (open chain or too few added edges), a modal fallback dialog appears — user can manually capture a complete edge or vertex loop, then Continue or Cancel
- If the user continues without providing an override, the paint proceeds with the incomplete loop and a warning is shown in the status bar
- New core function `autopaint_wire_weights()` in `blendshape_core.py`: bulk vertex positions via OpenMaya `MFnMesh.getPoints()`, weights written via `setAttr` on `weightList[geomIdx].weights[vi]`
- Improved "Edges" field tooltip: explains half-loop convention and topology advice for clean auto-completion

**Wire Setup & Cluster to Joint — Delete Setup buttons**

- New "Delete Wire Setup" icon button (last on Wire Setup shelf, right-aligned): deletes `wire_setup_grp` and `wire_setup_wire`; undoable; hover tint is red
- New "Delete CTJ Setup" icon button (last on CTJ shelf, right-aligned): deletes `{cluster}_Cluster2Joint_grp` and `{cluster}_ctj_skin`, re-enables the cluster envelope; undoable; hover tint is red
- Both buttons use `delete_wire.png` / `delete_cluster.png` icons (to be added to `resources/icons/`)

**Icon cleanup**

- Removed 25 unused icons from `resources/icons/`
- Renamed `bs_autofill.png` → `bs_auto-fill.png` to match the filename referenced in code
- Improved Wire Setup "Base Mesh" field tooltip: clarifies that the tool retrieves the mesh's shapeOrig as the neutral mesh for the wire rig

## v.05.55

**Bake Deformers — fixes and improvements**

- Fixed rest-pose reference: now queries `bs_node.originalGeometry` via `plugs=True` to get the exact intermediate shape the blendShape uses, avoiding false deltas caused by leftover orig shapes with residual deformation
- Double-click on Bake Defs button: toggles the `envelope` of all deformers downstream of the blendShape (→0 if any are active, →1 if all already at 0); new core function `get_deformers_above_bs()`

**Zip Locator Presets — color export/import**

- Export now saves each locator's display color override (`overrideEnabled`, index or RGB mode) into the JSON
- Import restores `overrideEnabled` and the saved color on the locator shape
- New right-click entry on the Import button: **Load default presets** — loads `resources/split_locs_presets.json` bundled with the tool (no file dialog)
- Right-click menu order swapped: Load default presets first, Browse from JSON file second
- Tooltips updated on Import and Save buttons to advertise right-click availability

**Rig Connector**

- Header tooltips added on **Controller**, **Min**, and **Max** columns explaining right-click actions
- Min/Max tooltips clarify these are driver attribute values remapped to the [0, 1] target weight range
- Help menu added to the Rig Connector window with a direct link to the dedicated documentation page

**Main UI — Help menu**

- New **Help** menu in the main menu bar: Documentation link (moved from Edit menu) + About dialog
- About dialog shows tool name, version, copyright, and full MIT License text

## v.05.54

**Wire Setup — Hammer Wire Weights**

- New shelf button: Hammer Wire Weights (`hammer_wire.png`)
- Pure Laplacian smooth on selected vertices: each vertex converges to the uniform average of its edge-connected neighbours' wire deformer weights
- Wire node auto-detected from mesh history (`listHistory` → `type="wire"`)
- Adjacency built once per call; all weights snapshotted before any write — no propagation artefacts

**Cluster to Joint — Hammer Cluster Weights**

- New shelf button: Hammer Cluster Weights (`hammer_cluster.png`)
- Same Laplacian approach as Wire hammer; weights read/written via `cmds.percent`
- Cluster auto-detected from mesh history if the combo is empty

**Cluster to Joint — Auto-detect existing setup on UI reopen**

- `combo_ctj_cluster.currentTextChanged` now triggers `_ctj_try_restore_setup`
- If `{cluster}_ctj_skin`, `{cluster}_jnt` and `{cluster}_zero_jnt` all exist in scene, the CTJ state variables are restored automatically — Bake is available without re-running Setup
- Status bar confirms detection: "existing setup detected — {djnt} | Bake when ready"

**Wire Setup / Cluster to Joint — Copy/Paste Weight icons**

- Copy Weight and Paste Weight buttons in Wire Setup and Cluster to Joint now use `copy_weight.png` / `paste_weight.png`
- Modify Deltas copy/paste buttons unchanged (`copy_delta.png` / `paste_delta.png`)

## v.05.53

**Rig Connector — skinning_ctrl: renamed from no_limits, full behaviour overhaul**

- Controller flag renamed from `no_limits` to `skinning_ctrl` (Is Skinning Controller)
- Old `no_limits` key silently ignored on load, never written
- Controller combobox tinted dark teal (`#002a3a`) instead of orange-brown
- Header context menu: two separate Enable/Disable actions replaced by a single checkable `QAction` that reads the current state of selected rows
- `skinning_ctrl` controllers: no `hasLimits` attr, no `transformLimits`, all 12 limit enables forced to False, all transform + scale attrs explicitly unlocked and set keyable
- `_snapshot_row` now captures `skinning_ctrl` — flag was lost after any row move, reorder, or undo/redo
- `_insert_row_data_at` now restores `skinning_ctrl` property and teal tint on the reconstructed combobox

**Rig Connector — Build & Connect: single normalization path**

- Extracted `_normalize_connection_rows(raw_data)` as a module-level function — single source of truth for the JSON → row-dict conversion expected by `build_and_connect_rig`
- `_run_connect_from_file` (Run Connect from File) now calls this function; was previously a duplicate inline loop that was missing `skinning_ctrl`
- Any future field addition only requires updating `_normalize_connection_rows` + `_collect_rows` + `_snapshot_row`/`_insert_row_data_at`

**Rig Connector — Build & Connect: stagger detection fix for bidirectional shapes**

- `_ctrl_attr_count` now keys by `(ctrl, resolved_attr, sign)` where sign = +1 if `in_max >= 0`, -1 otherwise
- Previously, shapes on the same ctrl+attr with opposite-sign `in_max` (e.g. lip_up +10 / lip_dn −10 on `lip_ctrl.ty`) were counted as stagger → `floor_` set to constant → `hasLimits` had no effect on weight
- Fix: each direction is counted independently; bidirectional shapes are no longer misidentified as stagger

**Rig Connector — Build & Connect: cycleRelative fix on animCurveUU**

- `_set_curve_infinity(curve, pre, post)` helper: uses `cmds.selectKey(curve, keyframe=True, f=(0.0, 0.0))` then `cmds.setInfinity(pri=pre, poi=post)`
- Key at `f=(0.0, 0.0)` is always present regardless of `in_max` value; `add=True` removed to avoid selection pollution
- Replaces previous approaches (`setAttr postInfinity`, `mel.eval`, `setInfinity(curve, ...)`) that silently failed on non-time-based curves

**Rig Connector — Active target label clickable**

- Clicking the active target label in the main UI re-selects that target in Maya's Shape Editor via `shapeEditorTreeviewSelect`
- Cursor changes to `PointingHandCursor` when a target is active, `ArrowCursor` when showing `—`

**Rig Connector — Stagger: smooth interpolation for InMax distribution**

- "Exp." field replaced by `_combo_stagger_curve` combo: **Uniform** (linear), **Ease In** (t²), **Ease Out** (1−(1−t)²), **Smooth** (3t²−2t³ smoothstep)
- "Linear" mode name kept for the sequential naming order; curve type "Linear" renamed to "Uniform" to avoid ambiguity

**Rig Connector — Scale factor tooltip**

- Added concrete usage examples: `2.0 → double`, `0.5 → halve`, `−1.0 → flip sign`, `1.5 → scale up by 50%`

## v.05.52

**Rig Connector — Per-row "Disable Has Limits" option**

- Right-click on the **Controller** column header → two new actions: *Disable Has Limits — selected rows* and *Re-enable Has Limits — selected rows*
- When disabled, the controller combobox is tinted orange-brown as a visual indicator
- The `no_limits` flag is persisted in the mapping JSON and restored on load (both normal rows and proxy rows)
- The main UI "Build & Connect" button also reads the flag from JSON (it builds rows manually, the key was missing)

**Build & Connect — Has Limits now created first in the Channel Box**

- `hasLimits` is now created in a new **Phase 0.5**, immediately after Phase 0 wipes all user attrs, so it lands right below the native `visibility` attribute — before any custom blendshape attrs
- Removed the old delete+undo trick that was used to push it to the last position
- Phase 0 now also wipes `hasLimits` (previously preserved), since it is always recreated fresh in the correct position

**Build & Connect — no_limits controllers**

- Controllers flagged as `no_limits` are skipped entirely in Phase 0.5 (no `hasLimits` created)
- In the post-loop: `transformLimits` not applied, native attrs not locked/hidden, previously locked attrs restored, condition node `firstTerm` hardcoded to 1 (weight still capped at 1.0)

**Version label fix**

- `BlendshapeEditorUI.VERSION` constant was stuck at `v.05.20` — corrected to `v.05.52`

## v.05.51

**Rig Connector — Bug fix: status dot grayed on primary rows with proxies**

- After Build & Connect, primary rows whose shape also had proxy rows showed a grey (no-info) status dot — the last row sharing the same shape name (the proxy) was winning in the `row_by_shape` dict, overwriting the primary's update
- Fixed by adding a `_table_row` index to each row dict in `_collect_rows`; `build_and_connect_rig` now propagates `_table_row` through every `results.append` call (Phase 1 validation failures and Phase 3 network build — soft blend, skip, ok, error paths)
- Status update loops in `_on_build_connect` and `_on_connect_selected` now resolve the table row via `res.get("_table_row")` first, falling back to `row_by_shape` for callers that don't supply the field

**Rig Connector — Min / Max display format**

- `_fmt_inval` now formats values with exactly 2 decimal places (`:.2f`) — trailing zeros are no longer stripped, so `45` displays as `45.00`, `0.5` as `0.50`
- `QDoubleValidator` locale forced to C (`validator.setLocale(QtCore.QLocale.c())`) so the `.` is always the accepted decimal separator regardless of the OS locale (fixes `,` being accepted on French Windows)

**Rig Connector — Confirmation dialogs on Autofill when table is not empty**

- **Autofill from BS Node**: if the table already contains rows (detected via `_is_placeholder()` rather than the stale `self._shapes` list), a Yes/No dialog asks before overwriting — message shows the count of primary rows (proxies excluded)
- **Autofill from JSON**: same confirmation added at the top of `_load_mapping_from_path`, before the overlay and file read; `_push_undo` is called only after the user confirms, so cancelling leaves the undo stack untouched

**Rig Connector — Table header rename**

- "Shape" column header renamed to **Target**

## v.05.50

**Core — Create Opposite: per-target disconnect**

- `create_opposite_shape` now disconnects only the current source target's weight attribute (`bs_name.w[index]`) before calling `duplicate_target`, instead of all targets — avoids unnecessary disconnect/reconnect of unrelated targets when processing multiple selected targets
- The already-saved `src_conns` list is reused for the mirror-driver section (no re-read after disconnect)
- Reconnect happens in a `finally` block to guarantee cleanup even on error

**UI — Shelf: Delta Mush Cleaner**

- **Clean BS Node** removed from the shelf (Row 1) and moved to the **Edit** menu
- **Delta Mush Cleaner** added to Row 1 in its place (icon: `deltaMush_cleaner.png`)
- Creates a Delta Mush deformer on the selected mesh with clean defaults: `smoothingIterations=3`, `inwardConstraint=0.5`, `outwardConstraint=0.5`, `distanceWeight=1`
- Works on any selected mesh transform with a mesh shape

**UI — Edit menu: new actions**

- **Clean Blendshape Node** — moved from shelf to Edit menu; same behaviour as before
- **Clean Deformed Mesh** — new action; removes residual sculpt color sets (`SculptFreezeColorTemp`, `SculptMaskColorTemp`) and any leftover unnamed sets (up to 4 passes), then runs `doBakeNonDefHistory` on all selected meshes

**UI — Split section: layout overhaul**

- **Split Target** and **Edge Loop Split** buttons now share a single row, each occupying half the width (`[text][icon 36×36]` grouped left, stretch right)
- Both button pairs are wrapped in a titled QGroupBox for visual grouping
- Separator line between the locator table and Axis Options removed
- All icon buttons standardised to 36×36 / icon 34×34 (matching the main shelf)

**UI — Split section: preset locator rename proposal**

- When saving a preset, a **Rename Locators** dialog now appears proposing to rename each locator to `{side}_{preset_name}_loc_{suffix}` (parts omitted if empty, e.g. `R_Zip_loc_e`)
- Each row is individually checkable; proposed names are editable before confirming
- **Rename Checked** applies selected renames in Maya and updates the table; **Skip** saves without renaming

**UI — Split section: preset visibility**

- When switching presets from the combo, all sibling preset sub-groups (`*_splitsLocs_grp`) are hidden automatically; only the selected preset's group is made visible

**UI — Wire Setup: shelf overhaul**

- **Copy Wire Weight** and **Paste Wire Weight** buttons added to the wire shelf row (icons: `copy_delta.png`, `paste_delta.png`)
  - Copy: reads the wire deformer weight of the single selected vertex via `cmds.percent`
  - Paste: applies the stored weight to all selected vertices (undoable)
- **Bake Wire to Mesh** moved from the body to the **right side of the wire shelf** (icon-only, 36×36)
  - Right-click opens a context menu with a checkable **Delete Wire at Bake** option (replaces the old checkbox)
- **Create Wire Setup** remains in the body as `[label][icon 36×36]` left-aligned
- All wire shelf buttons standardised to 36×36 / icon 34×34
- **Create Wire Setup** and **Bake Wire to Mesh** now have custom icons (`create_wire_setup.png`, `bake_wire.png`)
- Shape list **+** / **−** buttons reduced to 22×22

**UI — Nomenclature section: layout changes**

- "Set Prfx" label renamed to **Add**, "Sufx" label renamed to **_target_** (centered), both left-aligned
- The `→` label between Search and Replace fields replaced by a **⇄ button** (30×30, font 16px) that swaps the content of both fields
- **Swap Target Names** button converted to icon-only 36×36, centered with symmetric stretch (no label)

**UI — Tools section: Joints Setup**

- **Joints Setup** section temporarily disabled (`setEnabled(False)`) — greyed out and non-interactive

**Installer**

- `dragDropInstaller.py` renamed to `dragDropInstaller_BSE.py` to avoid conflicts with identically-named installers from other tools

## v.05.20

**Core — Split / Create Opposite: regen mesh handling**

- Split and Create Opposite now detect blocking regen meshes before running and show a confirmation popup listing each affected target with its status (live sculpt mode or orphaned node)
- Orphaned regen meshes (name collision, not connected to the blendShape) are deleted on confirmation; the operation then proceeds normally
- Live regen meshes (target in sculpt mode, `inputGeomTarget` connected) are reused directly by `duplicate_target` without being deleted — the sculpt session is preserved and no phantom slot is created
- `duplicate_target` now checks `inputGeomTarget` upfront: if a regen mesh is already connected, it uses the transform (not the raw shape node) to ensure `cmds.duplicate()` behaves identically to the normal `sculptTarget` path
- `find_blocking_regen_meshes(targets)` added to `blendshape_core` — returns `(alias, node, is_connected)` tuples
- `_confirm_delete_regen_meshes(blockers, command_name)` added to the UI — modal Yes/No dialog, only deletes orphaned nodes
- `_run_split` and `_run_opposite` both run the pre-check before starting their main loop
- Clear `RuntimeError` messages added in `duplicate_target`, `create_split_target` and `_write_weighted_target` when `sculptTarget(regenerate=True)` returns `None`, replacing the cryptic `'NoneType' object is not subscriptable` error

## v.05.19

**UI — Modify Deltas: major layout and workflow overhaul**

- **Deltas Exchange** expanded to a 2 × 3 grid: *Add / Subtract / Bake Moves* (row 0), *Transfer / Swap / Bake Defs* (row 1) — Bake Moves and Bake Defs moved from the former Deltas Bake sub-section into Deltas Exchange
- *Bake Defs* (abbreviated label) prints "Bake Deformers" in the status bar
- **Wrap Extract** sub-section now sits next to **Deltas to Rig** (replacing the old Deltas Bake block in that row); explanatory label removed from Wrap Extract (tooltip is sufficient)
- **Modify Deltas** section opens in expanded state by default (`initial_state=2`)
- Neutral/Multi-target toggles in *Deltas to Rig* moved below the Create Rig buttons; Neutral mode unchecked by default
- *Select Delta Vertices* renamed **Select Vertices with Deltas**
- *Sub* button renamed **Subtract**
- **Transfer Deltas**: selection order inverted — first selected target is the donor (B), second is the receiver (A); status message and error text updated accordingly
- `_run_delta_swap` renamed `_run_delta_transfer`; `btn_delta_swap` renamed `btn_delta_transfer`
- Label alignment in Deltas Exchange split into two independent groups (exchange 4 + bake 2) to prevent label over-expansion
- Hammer combo label: *Space* → **Mode**
- *Smooth & Average* sub-section renamed **Deltas Distribution**

**UI — Deltas Scale: Normal Push mode toggle**

- New checkable `QToolButton` (icon: `normal_push.png`) in the XYZ row, right of the axis buttons
- **Normal mode ON**: XYZ fields set to `0.20`, all three fields linked together, *Multiply* and *Invert* operate via `push_normals_deltas` (component/normal space)
- **Normal mode OFF**: XYZ fields reset to `1.20`, object-space multiply/invert restored
- XYZ fields are always linked by default (even in object mode)
- `_run_push_normals` renamed `_run_push_normals_legacy` and removed from the compact state
- XYZ axis buttons fixed to 22 × 22 px (square)
- *Multiply / Invert / Nullify* row: Multiply pinned left, Invert floats centered (expandable spacing on both sides), Nullify pinned right

**UI — Compact state (Modify Deltas)**

- XYZ compact row prepended at top: sign button + value field + normal-mode toggle (no "XYZ" label)
- *Bake Moves* and *Bake Defs* merged onto the same compact row as Add / Subtract / Transfer / Swap (one 6-button row)
- **Split** section: compact state removed (`two_state=True`); opens expanded by default; *Tools* section closed by default

**UI — Rig Connector: Soft Blend Pairs**

- Blend Graph always visible, displayed to the right of the pairs table (70 / 30 width split); collapsible header removed
- Opening the Soft Blend Pairs section releases the window minimum height constraint so the window can grow; closing it re-locks minimum height after 50 ms

**UI — Delta Clipboard: Prune Small Deltas**

- Wrapped in a `QWidget` container with a trailing `addStretch()` so the spinbox and button stay anchored to the label, regardless of window width
- Label width is independent of the *Select Vertices with Deltas* label (no shared alignment)

**Bug fix — `bake_deformers_to_targets` (core)**

- `cmds.listRelatives` now called with `fullPath=True` for both the output shape and the intermediate shape
- Fixes `RuntimeError: Object does not exist` in `om2.MSelectionList().add()` when a short shape name was ambiguous (multiple nodes sharing the same short name in the scene)

---

## v.05.18

**Hammer Deltas: convergence-based loop + opacity as blend factor**

- IDW loop now runs until convergence (max 200 passes, `tol=1e-4`) instead of a fixed pass count
- **Opacity** slider now blends between original deltas and the fully-converged result — 100% always gives a complete hammer in a single click
- Removed `n_passes = max(1, round(opacity * 20))` from the UI handler
- Status bar shows opacity percentage instead of pass count
- Debug print reports `n_passes_run/200 passes` to diagnose convergence speed

---

## v.05.17

**UI — Modify Deltas: layout refinements**

- `btn_push_sign` (Normal Push): replaced by a text-only `QToolButton` displaying `+` / `−` (more compact than icons)
- "Laplacian" field renamed **Smooth Iterations**; aligned in the right column on the same row as Space/combo (50/50 split with the Space combo)
- **Delta Clipboard**: *Prune Small Deltas* and *Select Delta Vertices* positions swapped (Prune moves to bottom, Select to top)
- *Select Delta Vrtx* renamed **Select Delta Vertices** (full name)
- *Split Target* and *Edge Loop Split*: reverted to `_icon_btn` (fixed icon + full-width QPushButton text)
- *Swap Target Names*: same revert to `_icon_btn`
- **Delta Bake / Deltas to Rig**: horizontal ratio changed from 50/50 to 45/55

---

## v.05.16

**UI — Modify Deltas: full redesign**

- Section restructured into 6 titled `QGroupBox` (matching the Split section style):
  - **Deltas Scale** — Multiply, Invert, Nullify, Normal Push
  - **Deltas Exchange** — Add, Sub, Mult, Transfer, Swap, Replace
  - **Smooth & Average** — Smooth, Relax, Hammer, Average + Opacity slider
  - **Deltas Clipboard** — Copy, Paste, Prune, Select Delta Vrtx
  - **Deltas Bake** — Apply Moves, Bake Deformers
  - **Deltas to Rig** — Create Delta Cluster, Create Delta Joint

**Deltas Scale — layout**

- X/Y/Z row: `transformXYZ.png` icon (40 × 34 px) followed by 3 labels + expandable fields
- Expandable fields (`Expanding` + `stretch=1`) filling all available width
- Multiply / Invert / Nullify full-width buttons (via `_icon_btn`)
- 8 px spacing between Nullify and Normal Push to signal thematic separation

**Deltas Exchange — layout**

- 2 × 3 grid: Add / Sub / Mult (row 0), Transfer / Swap / Replace (row 1)
- `setColumnStretch` applied on all 3 columns for uniform distribution

**Deltas Clipboard — layout**

- 2 × 2 grid: Copy (0,0) / Prune + spinbox (0,1) / Paste (1,0) / Select Delta Vrtx (1,1)
- Spinbox width computed automatically: `fontMetrics().horizontalAdvance("0.0001") + 12`

**Compact state — multi-row grid**

- `add_compact_row_break()` functional in grid mode via a `_ROW_BREAK` sentinel
- `finalize_compact()` detects the sentinel and increments the current row
- `setColumnStretch(max_col, 1)`: empty space goes into the virtual column after the last button (prevents inter-button stretching)
- 3 rows: Row 0 = Scale + Smooth & Average (8 buttons), Row 1 = Exchange (6 buttons), Row 2 = Clipboard + Bake + Rig (8 buttons)

---

## v.05.02

**UI — Top status line**

- New persistent status line above Tool Settings, always visible
- Displays current blendShape node, selected target name, and selection count
- Single selection: `body_bs  ·  L_mouthCornerPull`
- Multi selection: `body_bs  ·  L_mouthCornerPull  [...]  5 of 38 selected`
- Phantom slots warning in orange: `·  2 phantom slots` (updated after each operation)
- Event-driven refresh via global `MouseButtonRelease` filter — no polling

**UI — Layout**

- Status bar footer: version label and operation status now on the same row (status left, version right)
- Section spacing increased from 6px to 10px for better readability

---

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
- Hammer: runs until convergence (max 200 passes), then blends by opacity — see v.05.18
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
