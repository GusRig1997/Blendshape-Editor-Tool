Changelog
=========

v.05.03
-------

**Bug fixes — Split**

- ``zero_all_bs_weights`` now skips ``combinationShape`` connections. Disconnecting
  them could silently destroy the target slot in Maya.
- Re-splitting an existing split target (e.g. ``L_shape`` already present) no longer
  loses its incoming SDK / expression connections. ``_run_split`` saves those
  connections before the operation and reconnects them to the new slot index.
- ``create_opposite_shape`` now passes ``force_reorder=True`` to
  ``duplicate_target`` so the opposite is always inserted directly after the
  source, even when the source lives inside a Shape Editor sub-group.

**Clean BS Node — empty named slots**

- ``purge_empty_bs_slots`` now runs a second pass that removes named slots that
  have no ``inputTargetGroup`` or an empty ``inputTargetItem`` array. These
  phantom-named slots are created by Maya's ``.shp`` import/export round-trip.
- ``Clean BS Node`` in the shelf now falls back to the currently loaded BS node
  when the Shape Editor has no selection.

**Rig Connector — hasLimits attribute ordering**

- On rebuild, ``hasLimits`` is now repositioned as the last attribute on the
  controller using a delete + undo technique, which also preserves all existing
  custom connections on the attribute.

**Check Shapes — Add unmatched shapes to list**

- After the *Rename Suggestions* step, a new *Add to List* dialog lists all
  blendShape targets that are not in the reference JSON. The user can select
  which shapes to add and choose an existing group or create a new one.

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
- Space after the separator is reserved for future tools.

**New buttons**

- **Reset All Targets to 0** (shelf row 2) — sets every target weight on the
  blendShape node(s) to 0; useful to return to neutral after previewing shapes.
- **Bake Deformers** (Actions section) — bakes the contribution of all
  deformers above the blendShape into the selected targets, then the deformer
  can be deleted. Works on multiple targets in one pass.

**Actions section — open by default**

- The Actions section now starts fully open instead of compact.

**Bug fix — scroll jump on section toggle**

- Fixed a bug where opening or closing any collapsible section caused the
  scroll area to jump to the top of the panel.

**UI stability**

- Fixed flickering double-open when dragging the installer into Maya a
  second time.
- UI position and dock state are now preserved between sessions
  (``retain=True``).
- Removed ``importlib.reload`` from the shelf button command; the installer
  now re-opens the tool cleanly after installation.

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
