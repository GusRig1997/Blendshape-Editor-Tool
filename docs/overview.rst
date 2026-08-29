Overview
========

The **Blendshape Editor** (v.05.55) is a dockable Maya panel designed to
speed up blendShape target creation and editing workflows for facial rigging
and character deformation.

Window Layout
-------------

The interface is divided into a fixed top shelf and a scrollable body.

.. list-table::
   :widths: 30 70
   :header-rows: 1

   * - Area
     - Description
   * - **Top status line** *(fixed)*
     - Displays the active blendShape node, selected target name and count,
       and a phantom-slot warning when empty slots are detected.
   * - **Tool Settings** *(collapsible, above shelf)*
     - Falloff, Symmetry, Strength, and Radius options shared by all sculpt
       brushes.
   * - **Maya Tools Shelf** *(fixed — 2 rows)*
     - Row 1: Grab / Flatten / Bulge / Smooth Target | Shape Editor /
       Clean BS Node / Reset All Targets | Exit Delta View.
       Row 2: Smooth / Relax / Pinch / Erase | Add Target /
       Create Opposite Target / Connect A→B | Delta View.
   * - **Nomenclature** *(collapsible, closed by default)*
     - Naming convention setup, target rename utilities, and Check Shapes.
   * - **Split** *(collapsible, open by default)*
     - Radial / 1D split of targets using spatial locators; Edge Loop Split.
   * - **Modify Deltas** *(collapsible, compact by default)*
     - Post-sculpt delta operations: multiply, nullify, add/sub/transfer,
       normal push, smooth, relax, hammer, average, copy/paste, prune,
       apply moves, bake deformers, delta cluster and delta joint helpers.
   * - **Tools** *(collapsible, closed by default)*
     - Wrap Setup — extract wrap targets onto secondary meshes.
       Wire Setup — curve-based lip/mouth deformation rig.
       Joints Setup — joint-based lip rig from an edge loop.
   * - **Status bar** *(bottom, fixed)*
     - Real-time feedback for every operation (✓ = success, ✗ = error).

Key Features
------------

- **Locator-based spatial split** — divide any target into N weighted regions
  using 1-D projection or 3-D radial falloff with four curve shapes.
- **Edge Loop Split** — split any target into upper/lower halves along a
  stored edge loop, with persistent setup fields filled once and reused
  across multiple targets.
- **Symmetric naming** — auto-generates paired ``L_`` / ``C_`` / ``R_``
  targets from a single split; side tokens are fully configurable.
- **Check Shapes** — compare existing targets against an external JSON
  reference list, with a *Match Existing to List* tool that suggests
  token-based renames and a new *Add to List* dialog to register missing
  shapes directly into the reference.
- **Secondary mesh pipeline** — extract wrap targets onto one or more
  receiver meshes in a single pass; auto-creates blendShape nodes; connects
  matching target weights automatically. Accessible from the Tools panel.
- **Full delta editing suite** — multiply, nullify, add/sub/transfer, normal
  push, Laplacian smooth, relax, hammer, average, copy/paste, prune, apply
  moves, bake deformers, cluster and joint helpers.
- **Wire Setup** — build a curve-based lip rig from an edge loop selection,
  sculpt each shape curve, and bake results back as blendShape targets.
- **Joints Setup** — build a joint-based lip rig from a middle edge; outputs
  a skinned joint chain ready to drive blendShape targets.
- **Rig Connector** — map FK controller attributes to blendShape target
  weights with auto-normalisation, optional gate nodes, and auto-stagger
  (normal, mirror, symmetric modes). Accessible from the **Rig Connector**
  menu in the menu bar.
- **Undo safety** — every operation is wrapped in a single Maya undo chunk
  (one Ctrl+Z reverts the entire action).
- **Persistent preferences** — naming convention pairs, Check Shapes file
  path, and UI position are saved across sessions.

Menu Bar
--------

Edit Menu
^^^^^^^^^

**Reset Default Options**
   Restores all Split, Falloff, and Modify Deltas controls to their
   factory defaults.

   .. note::
      User-defined naming convention pairs are **preserved** by Reset.

**Documentation**
   Opens the online documentation in the default browser.

Check Shapes Menu
^^^^^^^^^^^^^^^^^

**Open Check Shapes…**
   Opens the Check Shapes dialog to compare existing blendShape targets
   against a reference JSON list.

Rig Connector Menu
^^^^^^^^^^^^^^^^^^

**Open Rig Connector…**
   Opens the Rig Connector dialog to map FK controller attributes to
   blendShape target weights. See :doc:`sections/rig_connector` for details.
