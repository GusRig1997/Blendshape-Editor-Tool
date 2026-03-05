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
     - Naming convention setup and target rename utilities
   * - **Split** *(collapsible, open by default)*
     - Radial / 1D split of targets using spatial locators
   * - **Secondary Meshes** *(collapsible)*
     - Extract and connect targets on secondary meshes via wrap deformers
   * - **Actions** *(collapsible, open by default)*
     - Duplicate, Mirror, Flip, Create Opposite Target
   * - **Modify Deltas** *(collapsible)*
     - Post-sculpt delta operations (multiply, push, smooth, prune, etc.)
   * - **Tools** *(collapsible, open by default)*
     - Wire Setup — curve-based lip/mouth deformation rig
   * - **Status bar** *(bottom, fixed)*
     - Real-time feedback for every operation (green = success, red = error)

Key Features
------------

- **Locator-based spatial split** — divide any target into N weighted regions
  using 1-D projection or 3-D radial falloff with four curve shapes.
- **Symmetric naming** — auto-generates paired L_/R_ targets from a single split.
- **Secondary mesh pipeline** — extract wrap targets, extract-only, and
  connect matching targets between two meshes.
- **Full delta editing suite** — multiply, normal push, Laplacian smooth,
  relax, copy/paste, prune, cluster, and joint helpers.
- **Wire Setup** — build a curve-based lip rig from an edge loop selection,
  sculpt each shape curve, and bake results back as blendShape targets.
- **Undo safety** — every operation is wrapped in a single Maya undo chunk
  (one Ctrl+Z reverts the entire action).
- **Persistent preferences** — naming convention pairs are saved to a JSON
  file in the Maya user preferences directory.

Edit Menu
---------

**Reset Default Options**
   Restores all Split, Falloff, and Modify Deltas controls to their
   factory defaults.

   .. note::
      User-defined naming convention pairs are **preserved** by Reset.
