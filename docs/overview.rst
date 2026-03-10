Overview
========

The **Blendshape Editor Tool** is a dockable Maya panel designed to speed up
blendShape target creation and editing workflows for facial rigging and
character deformation.

Window Layout
-------------

The interface is divided into a fixed top shelf and a scrollable body.

.. list-table::
   :widths: 30 70
   :header-rows: 1

   * - Area
     - Description
   * - **Maya Tools Shelf** *(top, fixed)*
     - Quick-access sculpt tools, Shape Editor, Add Target, Delta View
   * - **Nomenclature** *(collapsible)*
     - Naming convention setup, target rename utilities, and Check Shapes
   * - **Split** *(collapsible, open by default)*
     - Radial / 1D split of targets using spatial locators; Edge Loop Split
   * - **Secondary Meshes** *(collapsible)*
     - Extract and connect targets on secondary meshes via wrap deformers
   * - **Actions** *(collapsible, compact by default)*
     - Duplicate, Mirror, Flip, Create Opposite Target, Apply Moves
   * - **Modify Deltas** *(collapsible)*
     - Post-sculpt delta operations (multiply, push, smooth, prune, etc.)
   * - **Tools** *(collapsible)*
     - Wire Setup — curve-based lip/mouth deformation rig
   * - **Status bar** *(bottom, fixed)*
     - Real-time feedback for every operation (green = success, red = error)

Key Features
------------

- **Locator-based spatial split** — divide any target into N weighted regions
  using 1-D projection or 3-D radial falloff with four curve shapes.
- **Edge Loop Split** — split any target into upper/lower halves along a
  stored edge loop, with persistent setup fields (Upper Vtx, Lower Vtx,
  Edgeloop) filled once and reused across multiple targets.
- **Symmetric naming** — auto-generates paired ``L_`` / ``C_`` / ``R_``
  targets from a single split; side tokens are fully configurable.
- **Check Shapes** — compare existing targets against an external JSON
  reference list, with a *Match Existing to List* tool that suggests
  token-based renames including missing side prefixes.
- **Secondary mesh pipeline** — extract wrap targets, extract-only, and
  connect matching targets between two meshes.
- **Full delta editing suite** — multiply, normal push, Laplacian smooth,
  relax, copy/paste, prune, cluster, and joint helpers.
- **Wire Setup** — build a curve-based lip rig from an edge loop selection,
  sculpt each shape curve, and bake results back as blendShape targets.
- **Undo safety** — every operation is wrapped in a single Maya undo chunk
  (one Ctrl+Z reverts the entire action).
- **Persistent preferences** — naming convention pairs, Check Shapes file
  path, and UI position are saved across sessions.

Edit Menu
---------

**Reset Default Options**
   Restores all Split, Falloff, and Modify Deltas controls to their
   factory defaults.

   .. note::
      User-defined naming convention pairs are **preserved** by Reset.
