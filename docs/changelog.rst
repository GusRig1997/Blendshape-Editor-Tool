Changelog
=========

v.03.003 *(in progress)*
------------------------

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
