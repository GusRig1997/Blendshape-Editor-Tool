Changelog
=========

v.05.55
-------

**Bake Deformers — fixes and improvements**

- Fixed rest-pose reference: now queries ``bs_node.originalGeometry`` via ``plugs=True``
  to get the exact intermediate shape the blendShape uses, avoiding false deltas caused
  by leftover orig shapes with residual deformation.
- Double-click on **Bake Defs** button: toggles the ``envelope`` of all deformers
  downstream of the blendShape (→ 0 if any are active, → 1 if all already at 0).
  New core function ``get_deformers_above_bs()``.

**Zip Locator Presets — color export/import**

- Export now saves each locator's display color override (``overrideEnabled``,
  index or RGB mode) into the JSON.
- Import restores ``overrideEnabled`` and the saved color on the locator shape.
- New right-click entry on the Import button: **Load default presets** — loads
  ``resources/split_locs_presets.json`` bundled with the tool (no file dialog).
- Right-click menu order swapped: Load default presets first, Browse from JSON file second.
- Tooltips updated on Import and Save buttons to advertise right-click availability.

**Rig Connector**

- Header tooltips added on **Controller**, **Min**, and **Max** columns
  explaining right-click actions.
- Min/Max tooltips clarify these are driver attribute values remapped to the
  [0, 1] target weight range.
- Help menu added to the Rig Connector window with a direct link to the
  dedicated documentation page.

**Main UI — Help menu**

- New **Help** menu in the main menu bar: Documentation link (moved from Edit menu)
  + About dialog.
- About dialog shows tool name, version, copyright, and full MIT License text.

----

v.05.54
-------

**Wire Setup — Hammer Wire Weights**

- New shelf button: Hammer Wire Weights (``hammer_wire.png``).
- Pure Laplacian smooth on selected vertices: each vertex converges to the uniform
  average of its edge-connected neighbours' wire deformer weights.
- Wire node auto-detected from mesh history (``listHistory`` → ``type="wire"``).
- Adjacency built once per call; all weights snapshotted before any write —
  no propagation artefacts.

**Cluster to Joint — Hammer Cluster Weights**

- New shelf button: Hammer Cluster Weights (``hammer_cluster.png``).
- Same Laplacian approach as Wire hammer; weights read/written via ``cmds.percent``.
- Cluster auto-detected from mesh history if the combo is empty.

**Cluster to Joint — Auto-detect existing setup on UI reopen**

- ``combo_ctj_cluster.currentTextChanged`` now triggers ``_ctj_try_restore_setup``.
- If ``{cluster}_ctj_skin``, ``{cluster}_jnt`` and ``{cluster}_zero_jnt`` all exist
  in scene, the CTJ state variables are restored automatically — Bake is available
  without re-running Setup.
- Status bar confirms detection: "existing setup detected — {djnt} | Bake when ready".

**Wire Setup / Cluster to Joint — Copy/Paste Weight icons**

- Copy Weight and Paste Weight buttons in Wire Setup and Cluster to Joint now use
  ``copy_weight.png`` / ``paste_weight.png``.
- Modify Deltas copy/paste buttons unchanged (``copy_delta.png`` / ``paste_delta.png``).

----

v.05.53
-------

**Rig Connector — skinning_ctrl: renamed from no_limits, full behaviour overhaul**

- Controller flag renamed from ``no_limits`` to ``skinning_ctrl`` (Is Skinning Controller).
- Old ``no_limits`` key silently ignored on load, never written.
- Controller combobox tinted dark teal (``#002a3a``) instead of orange-brown.
- Header context menu: two separate Enable/Disable actions replaced by a single
  checkable ``QAction`` that reads the current state of selected rows.
- ``skinning_ctrl`` controllers: no ``hasLimits`` attr, no ``transformLimits``,
  all 12 limit enables forced to False, all transform + scale attrs explicitly
  unlocked and set keyable.
- ``_snapshot_row`` now captures ``skinning_ctrl`` — flag was lost after any row
  move, reorder, or undo/redo.

**Rig Connector — Build & Connect: single normalization path**

- Extracted ``_normalize_connection_rows(raw_data)`` as a module-level function —
  single source of truth for the JSON → row-dict conversion.
- ``_run_connect_from_file`` now calls this function; was previously a duplicate
  inline loop missing ``skinning_ctrl``.

**Rig Connector — Build & Connect: stagger detection fix for bidirectional shapes**

- ``_ctrl_attr_count`` now keys by ``(ctrl, resolved_attr, sign)`` where
  sign = +1 if ``in_max >= 0``, -1 otherwise.
- Bidirectional shapes on the same ctrl+attr with opposite-sign ``in_max``
  are no longer misidentified as stagger.

**Rig Connector — Build & Connect: cycleRelative fix on animCurveUU**

- ``_set_curve_infinity(curve, pre, post)`` helper: uses ``cmds.selectKey``
  then ``cmds.setInfinity`` — fixes silent failures on non-time-based curves.

**Rig Connector — Active target label clickable**

- Clicking the active target label re-selects that target in Maya's Shape Editor.
- Cursor changes to ``PointingHandCursor`` when a target is active.

**Rig Connector — Stagger: smooth interpolation for InMax distribution**

- "Exp." field replaced by ``_combo_stagger_curve``: **Uniform** (linear),
  **Ease In** (t²), **Ease Out** (1−(1−t)²), **Smooth** (3t²−2t³ smoothstep).

**Rig Connector — Scale factor tooltip**

- Added concrete usage examples: ``2.0 → double``, ``0.5 → halve``,
  ``−1.0 → flip sign``, ``1.5 → scale up by 50%``.

----

v.05.52
-------

**Rig Connector — Per-row "Disable Has Limits" option**

- Right-click on the **Controller** column header → two new actions:
  *Disable Has Limits — selected rows* and *Re-enable Has Limits — selected rows*.
- When disabled, the controller combobox is tinted orange-brown as a visual indicator.
- The ``no_limits`` flag is persisted in the mapping JSON and restored on load.

**Build & Connect — Has Limits now created first in the Channel Box**

- ``hasLimits`` is now created in a new Phase 0.5, immediately after Phase 0
  wipes all user attrs, landing right below the native ``visibility`` attribute.
- Phase 0 now also wipes ``hasLimits`` (previously preserved).

**Version label fix**

- ``BlendshapeEditorUI.VERSION`` constant was stuck at ``v.05.20`` — corrected.

----

v.05.51
-------

**Rig Connector — Bug fix: status dot grayed on primary rows with proxies**

- After Build & Connect, primary rows whose shape also had proxy rows showed
  a grey (no-info) status dot — the last row sharing the same shape name (the proxy)
  was winning in the ``row_by_shape`` dict.
- Fixed by adding a ``_table_row`` index to each row dict in ``_collect_rows``.

**Rig Connector — Min / Max display format**

- ``_fmt_inval`` now formats values with exactly 2 decimal places (``:.2f``).
- ``QDoubleValidator`` locale forced to C so ``.`` is always the decimal separator
  regardless of the OS locale (fixes ``,`` being accepted on French Windows).

**Rig Connector — Confirmation dialogs on Autofill when table is not empty**

- **Autofill from BS Node** and **Autofill from JSON**: if the table already
  contains rows, a Yes/No dialog asks before overwriting.

**Rig Connector — Table header rename**

- "Shape" column header renamed to **Target**.

----

v.05.50
-------

**Core — Create Opposite: per-target disconnect**

- ``create_opposite_shape`` now disconnects only the current source target's
  weight attribute before calling ``duplicate_target``, instead of all targets.
- Reconnect happens in a ``finally`` block to guarantee cleanup on error.

**UI — Shelf: Delta Mush Cleaner**

- **Clean BS Node** removed from shelf Row 1, moved to the **Edit** menu.
- **Delta Mush Cleaner** added to Row 1 in its place.

**UI — Edit menu: new actions**

- **Clean Blendshape Node** — moved from shelf to Edit menu.
- **Clean Deformed Mesh** — removes residual sculpt color sets and leftover unnamed
  sets, then runs ``doBakeNonDefHistory`` on selected meshes.

**UI — Split section: layout overhaul**

- **Split Target** and **Edge Loop Split** share a single row.
- Both wrapped in a titled ``QGroupBox``.
- All icon buttons standardised to 36×36 / icon 34×34.

**UI — Split section: preset locator rename proposal**

- When saving a preset, a **Rename Locators** dialog proposes renaming each locator
  to ``{side}_{preset_name}_loc_{suffix}``.

**UI — Split section: preset visibility**

- When switching presets, all sibling preset sub-groups are hidden automatically;
  only the selected preset's group is made visible.

**UI — Wire Setup: shelf overhaul**

- **Copy Wire Weight** and **Paste Wire Weight** buttons added to the wire shelf.
- **Bake Wire to Mesh** moved to the right side of the wire shelf (icon-only).
- Right-click opens a context menu with a checkable **Delete Wire at Bake** option.

**UI — Nomenclature section: layout changes**

- "Set Prfx" → **Add**, "Sufx" → **_target_**; ``→`` label replaced by a **⇄ button**
  that swaps Search and Replace fields.
- **Swap Target Names** converted to icon-only 36×36.

**Installer**

- ``dragDropInstaller.py`` renamed to ``dragDropInstaller_BSE.py``.

----

v.05.20
-------

**Core — Split / Create Opposite: regen mesh handling**

- Both operations now detect blocking regen meshes before running and show
  a confirmation popup listing each affected target.
- Orphaned regen meshes (name collision, not connected) are deleted on confirmation.
- Live regen meshes (target in sculpt mode) are reused directly without being deleted.
- ``find_blocking_regen_meshes(targets)`` added to ``blendshape_core``.
- ``_confirm_delete_regen_meshes(blockers, command_name)`` added to the UI.
- Clear ``RuntimeError`` messages added in ``duplicate_target``,
  ``create_split_target`` and ``_write_weighted_target``.

----

v.05.19
-------

**UI — Modify Deltas: major layout and workflow overhaul**

- **Deltas Exchange** expanded to a 2 × 3 grid: Add / Subtract / Bake Moves (row 0),
  Transfer / Swap / Bake Defs (row 1).
- **Wrap Extract** sub-section sits next to **Deltas to Rig**.
- **Modify Deltas** section opens in expanded state by default.
- *Select Delta Vertices* renamed **Select Vertices with Deltas**.
- **Transfer Deltas**: first selected target is the donor (B), second is the receiver (A).

**UI — Deltas Scale: Normal Push mode toggle**

- New checkable ``QToolButton`` (icon: ``normal_push.png``) in the XYZ row.
- **Normal mode ON**: XYZ fields set to ``0.20``, all three fields linked, Multiply
  and Invert operate via ``push_normals_deltas``.
- **Normal mode OFF**: XYZ fields reset to ``1.20``, object-space operations restored.

**UI — Rig Connector: Soft Blend Pairs**

- Blend Graph always visible, displayed to the right of the pairs table (70/30 split).
- Opening the section releases the window minimum height constraint.

**Bug fix — bake_deformers_to_targets (core)**

- ``cmds.listRelatives`` now called with ``fullPath=True`` for both the output shape
  and the intermediate shape — fixes ``RuntimeError`` when short shape names are ambiguous.

----

v.05.18
-------

**Hammer Deltas: convergence-based loop + opacity as blend factor**

- IDW loop now runs until convergence (max 200 passes, ``tol=1e-4``) instead of
  a fixed pass count.
- **Opacity** slider blends between original deltas and the fully-converged result.
- Status bar shows opacity percentage instead of pass count.

----

v.05.17
-------

**UI — Modify Deltas: layout refinements**

- ``btn_push_sign`` (Normal Push): replaced by a text-only ``QToolButton`` displaying
  ``+`` / ``−``.
- "Laplacian" field renamed **Smooth Iterations**.
- **Delta Clipboard**: Prune Small Deltas and Select Delta Vertices positions swapped.
- *Split Target* and *Edge Loop Split*: reverted to ``_icon_btn``.

----

v.05.16
-------

**UI — Modify Deltas: full redesign**

- Section restructured into 6 titled ``QGroupBox``:
  Deltas Scale, Deltas Exchange, Smooth & Average, Deltas Clipboard,
  Deltas Bake, Deltas to Rig.

**Compact state — multi-row grid**

- ``add_compact_row_break()`` functional in grid mode via a ``_ROW_BREAK`` sentinel.
- 3 rows: Scale + Smooth & Average / Exchange / Clipboard + Bake + Rig.

----

v.05.10
-------

**Bug fixes — Split**

- ``zero_all_bs_weights`` now skips ``combinationShape`` connections. Disconnecting
  them could silently destroy the target slot in Maya.
- Re-splitting an existing split target no longer loses its incoming SDK / expression
  connections.
- ``create_opposite_shape`` now passes ``force_reorder=True`` to ``duplicate_target``
  so the opposite is always inserted directly after the source.

**Clean BS Node — empty named slots**

- ``purge_empty_bs_slots`` now runs a second pass that removes named slots that
  have no ``inputTargetGroup`` or an empty ``inputTargetItem`` array.
- ``Clean BS Node`` in the shelf now falls back to the currently loaded BS node
  when the Shape Editor has no selection.

**Rig Connector — hasLimits attribute ordering**

- On rebuild, ``hasLimits`` is now repositioned as the last attribute on the
  controller using a delete + undo technique.

**Check Shapes — Add unmatched shapes to list**

- After the *Rename Suggestions* step, a new *Add to List* dialog lists all
  blendShape targets not in the reference JSON.

----

v.05.02
-------

**UI — Top status line**

- New persistent status line above Tool Settings, always visible.
- Displays current blendShape node, selected target name, and selection count.
- Phantom slots warning in orange.
- Event-driven refresh via global ``MouseButtonRelease`` filter — no polling.

**UI — Layout**

- Status bar footer: version label and operation status on the same row.
- Section spacing increased from 6 px to 10 px.

----

v.05.01
-------

**UI — Shelf restructure**

- Sculpt brushes reorganized into a 4×2 grid.
- Row 1: 4 sculpt | Shape Editor / Clean BS Node / Reset All Targets | Exit Delta View.
- Row 2: 4 sculpt | Add Target / Create Opposite / Connect A→B | Delta View.

**UI — Actions section removed**

- Create Opposite moved to shelf row 2 (right-click to pick axis).
- Apply Moves and Bake Deformers moved into the Modify Deltas section.

**UI — Secondary Meshes section removed**

- Extract Wrap and Extract Only moved into a "Wrap Setup" ``QGroupBox``.
- Connect A→B moved to shelf row 2.

----

v.05.00
-------

**Wrap Extract — multi-mesh support**

- First selected mesh is the master; all subsequent meshes are receivers.
- If a receiver has no blendShape node, a dialog proposes to create one automatically.
- Status bar reports per-receiver: targets kept, added, replaced, pruned.

**Modify Deltas — Add / Sub / Swap**

- ``Add Delta``: adds the delta of a source target on top of the current target.
- ``Sub Delta``: subtracts the delta of a source target from the current target.
- ``Swap Delta``: swaps delta data between two targets in one operation.

**Modify Deltas — Opacity slider extended**

- Slider now controls Hammer and Average operations in addition to Smooth/Relax.

**Bug fixes**

- Multiply / Nullify: now correctly applies to all selected targets.
- Bake Deformers: corrected delta accumulation formula.

----

v.04.05
-------

- Wire Setup: default rotation value set to ``0.15``.
- Partial mirror on vertex selection: Mirror and Flip buttons operate only on
  selected vertices when a vertex selection is active.

----

v.04.04
-------

- Opacity slider for Smooth/Relax: 1–10 passes.
- Bake Deformers: fix — deformer contribution now computed as ``(deformed − base)``
  delta added on top of existing target deltas.
- BS weight disconnect/reconnect unified across Split, Wrap and Bake.

----

v.04.03
-------

**Rig Connector — Combo Driver: extended syntaxes**

- Support for ``node.attr`` form: any Maya attribute can be used as a combo driver.
- ``rev:`` prefix: inserts a ``reverse`` node (1 − input) before the gate.

**Rig Connector — Add Row from Shape Editor**

- ``Add Row`` button reads the current Shape Editor selection.

----

v.04.02
-------

**Rig Connector — Major UI simplification**

- Table reduced from 10 to 8 columns: removed ``Dir`` and ``Custom Attr`` columns.
- Direction encoded in the sign of ``In Max``.
- ``Attr`` column becomes an editable QComboBox.
- Load mapping: backward compatibility with old JSONs preserved.

**Rig Connector — Auto-stagger redesigned**

- ``Mode`` combo: **Linear / Mirror / Symmetric**.
- Symmetric stagger In Max/Min produces signed values directly.

**Rig Connector — Combo Driver (formerly Gate)**

- New ``Combo Driver`` column: inserts ``multDoubleLinear`` nodes in series.

**Modify Deltas — Nullify button**

- ``Nullify`` button next to ``Multiply Deltas``: zeros all deltas in one click.

----

v.04.01
-------

**Rig Connector — Save/Load Mapping: proxy row support**

- ``_collect_rows`` now saves a ``"proxy": true/false`` flag in the JSON.
- ``_load_mapping`` correctly recreates proxy rows.

**Rig Connector — Auto-stagger redesigned**

- Auto-stagger now operates on the **selected rows** in the table.
- Automatically creates proxy rows with a symmetric stagger.

**Rig Connector — InMin / InMax spinboxes**

- Locale forced to English; InMax now accepts ``0`` (driver disabled).

**Build & Connect — Rebuild robustness**

- Removed ``disconnectAttr`` loop that could fail on locked connections.
- If all drivers for a shape are disabled (``in_max=0``), the network is not created.

----

v.04.00
-------

**Edge Loop Split — persistent setup fields**

- The Edge Loop Split no longer reads from the live viewport selection.
  Three dedicated persistent fields must be filled once per session:
  *Upper Vtx*, *Lower Vtx*, and *Edgeloop* (captured via **Get** buttons).
- The three fields are grouped under a collapsible **Edge Loop Options**
  disclosure row. Click it to expand or collapse the setup panel.
- The ``seed_upper`` / ``seed_lower`` naming is now used throughout the
  core function, replacing the ambiguous ``seed_a`` / ``seed_b``.

**Center side renamed M_ → C_**

- All auto-generated center/bilateral targets now use ``C_`` as the side
  token instead of ``M_``.
- The default side tokens in the Naming Convention dialog are now
  ``R`` / ``C`` / ``L``.
- The built-in Check Shapes default list has been updated accordingly.

**Check Shapes — external JSON**

- The reference list is now stored in
  ``resources/check_shapes_default.json`` (shipped with the tool) instead
  of being hard-coded in the source.
- A **File** menu (Load… / Save… / Reset to Default) replaces the old
  button toolbar. The last loaded file path is remembered across sessions.
- The current file name is shown in the dialog title bar.

**Check Shapes — Match Existing to List**

- New **Match existing to List** button opens a *Rename Suggestions* dialog.
- Matches targets to the reference list using token sets (order-independent).
- Also detects targets missing a side prefix (``C_``, ``L_``, or ``R_``)
  and proposes adding it.
- Ambiguous matches are highlighted in orange *(Not sure)* and unchecked by
  default.

**Naming Convention — side token fields**

- Three new fields in the Naming Convention dialog: **Left**, **Center**,
  **Right** (defaults: ``L``, ``C``, ``R``).
- The Split and symmetric operations read these values at runtime, so
  non-standard side conventions work without code changes.

**Actions section — topology edge field moved to top**

- The **Edge** field (topology-symmetry centered edge) is now the first
  control in the Actions section, above all operation buttons.

**Maya Tools Shelf — second row**

- The top shelf now has two rows.
- Row 2 adds three sculpt brushes: **Relax**, **Pinch**, **Amplify**
  (all with double-click to open Tool Settings).
- **Add Target**, **Clean Blendshape Node**, and **Reset All Targets to 0**
  are now grouped together in Row 2, separated from the sculpt tools.

**New buttons**

- **Reset All Targets to 0** (shelf row 2) — sets every target weight on the
  blendShape node(s) to 0.
- **Bake Deformers** (Actions section) — bakes the contribution of all
  deformers above the blendShape into the selected targets.

**Actions section — open by default**

**Bug fixes**

- Fixed scroll jump to top when toggling any collapsible section.
- Fixed flickering double-open when dragging the installer a second time.
- UI position and dock state preserved between sessions (``retain=True``).
- Removed ``importlib.reload`` from the shelf button command.

----

v.03.003
--------

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

----

v.03.002
--------

- Dockable UI via ``MayaQWidgetDockableMixin`` (``maya.app.general.mayaMixin``)
- New **Secondary Meshes** section: Extract Wrap Targets, Extract Only,
  Connect Targets A→B
- Maya Tools Shelf (Grab, Flatten, Bulge | ShapeEditor, SmoothTarget, Erase)
- Delta View / Exit Delta View moved to shelf
- Removed *Selected Targets* section
- Nomenclature moved above Locators, starts collapsed
- Compact-default sections (Nomenclature, Secondary Meshes, Modify Deltas)
  with bounce cycle
- Version label in footer, removed from window title

----

v1.0.0
------

- Initial release
- Radial split using 1 to N locators with quintic smootherstep falloff
- Multi-axis support: XZ / X / Z / Y / YZ
- Symmetric L\_ / R\_ generation (single locator)
- Adaptive naming: descriptive (1–3 locators) / alphabetical (4+)
- Mirror Target, Flip Target, Create Opposite Target
- PySide6 interface with custom icons
- Status label at the bottom of the window
- Tooltips on all controls
- Undo chunk wrapping on all operations
- Multiply Deltas: scales all vertex deltas by a given factor
