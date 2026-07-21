Maya Tools Shelf
================

The top shelf is always visible regardless of scroll position.
It provides instant access to the most frequent Maya sculpting, Shape Editor,
and blendShape node commands without leaving the tool.

The shelf is split into **two rows**.

----

Row 1 — Sculpt & Visualisation
-------------------------------

.. list-table::
   :widths: 20 80
   :header-rows: 1

   * - Button
     - Description
   * - **Grab**
     - Activates Maya's *Grab* mesh sculpt brush.
       Double-click opens Tool Settings.
   * - **Flatten**
     - Activates Maya's *Flatten* mesh sculpt brush.
       Double-click opens Tool Settings.
   * - **Bulge**
     - Activates Maya's *Bulge* mesh sculpt brush.
       Double-click opens Tool Settings.
   * - **Smooth Target**
     - Activates Maya's *Smooth Target* sculpt brush.
       Double-click opens Tool Settings.
   * - *(separator)*
     -
   * - **Shape Editor**
     - Opens Maya's native Shape Editor window.
   * - **Clean BS Node**
     - Removes empty target slots from the blendShape node.
       See :ref:`clean-bs-node` below.
   * - **Reset All Targets to 0**
     - Sets every target weight on the active blendShape node(s) to 0.
       Useful to return to neutral pose after previewing shapes.
   * - *(separator)*
     -
   * - **Exit Delta View**
     - Restores original vertex colours and disables Delta View.

----

Row 2 — Extra Sculpt & Target Utilities
-----------------------------------------

.. list-table::
   :widths: 20 80
   :header-rows: 1

   * - Button
     - Description
   * - **Smooth**
     - Activates Maya's *Smooth* mesh sculpt brush.
       Double-click opens Tool Settings.
   * - **Relax**
     - Activates Maya's *Relax* mesh sculpt brush.
       Double-click opens Tool Settings.
   * - **Pinch**
     - Activates Maya's *Pinch* mesh sculpt brush.
       Double-click opens Tool Settings.
   * - **Erase**
     - Activates Maya's *Erase* sculpt brush.
       Double-click opens Tool Settings.
   * - *(separator)*
     -
   * - **Add Target**
     - See :ref:`add-target` below — dual left/right-click behaviour.
   * - **Create Opposite Target**
     - See :ref:`create-opposite` below.
   * - **Connect A→B**
     - Connects matching blendShape target weights from a source mesh (A)
       to a destination mesh (B). Select A then B in the viewport before
       clicking.
   * - *(separator)*
     -
   * - **Delta View**
     - Colorises mesh vertices by cumulative delta magnitude. See :ref:`delta-view`.

----

.. _clean-bs-node:

Clean BS Node
-------------

Removes empty or phantom target slots from the blendShape node.

**Sources checked:**

1. **Phantom slots** — weight indices that have no alias attribute (no name).
2. **Empty named slots** — named targets that have no ``inputTargetGroup``
   data or an empty ``inputTargetItem`` array. These are typically created
   by Maya's ``.shp`` import/export round-trip.

**Node resolution:**

- If one or more targets are selected in the **Shape Editor**, the operation
  runs on the blendShape node(s) associated with those targets.
- If nothing is selected in the Shape Editor, the operation falls back to the
  blendShape node currently loaded in the tool (displayed in the top status
  line).

The total number of slots removed is reported in the status bar.

----

.. _add-target:

Add Target
----------

The **Add Target** button has two behaviours depending on which mouse button
you use.

Left-click — Add Empty Target
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Creates a new zero-delta target on the blendShape node of the active mesh,
then immediately enters sculpt mode so you can start painting.

Right-click — Context Menu
^^^^^^^^^^^^^^^^^^^^^^^^^^

.. list-table::
   :widths: 35 65
   :header-rows: 1

   * - Menu Item
     - Description
   * - *Add Empty Target*
     - Same as left-click.
   * - *Add Selection as New Target*
     - Select one or more **source meshes** and a **target mesh** (last in
       selection). The source meshes are imported as new targets at rest pose
       directly.
   * - *Add Selection as New Corrective Target*
     - Same selection rule. Uses ``invertShape()`` to bake a *posed* sculpt
       back into rest-pose delta space — the correct workflow for corrective
       shapes on a deformation stack.
   * - *Delete source mesh after import*
     - Persistent checkbox. When enabled, source meshes are deleted from the
       scene after a successful import.

----

.. _create-opposite:

Create Opposite Target
----------------------

Creates a mirrored copy of the selected target, automatically negating the
appropriate vertex delta components and renaming it using the configured
opposite-token pairs (e.g. ``L_`` ↔ ``R_``, ``up`` ↔ ``dn``).

The new target is inserted directly after the source in the Shape Editor,
preserving its group placement.

**Right-click — Axis Menu**

Right-clicking the button opens an axis selection menu:

.. list-table::
   :widths: 30 70
   :header-rows: 1

   * - Option
     - Description
   * - *Object X* *(default)*
     - Mirror across the object's local X axis.
   * - *Object Y*
     - Mirror across the object's local Y axis.
   * - *Object Z*
     - Mirror across the object's local Z axis.
   * - *Topology*
     - Mirror using a topology-based symmetry map. Requires the
       **Edge** field (topology edge) to be set in the Split section.

----

.. _delta-view:

Delta View
----------

Visualises the magnitude of vertex deltas as a colour gradient directly on
the mesh surface.

**Colour scale** (low → high):

.. code-block:: text

   Black → Blue → Cyan → Green → Yellow → Red → White

**Behaviour:**

- Sums delta magnitudes across all targets selected in the Shape Editor.
- When multiple meshes are selected, a single global scale is applied so
  magnitudes are comparable across meshes.
- A Laplacian diffusion pass is applied for a smooth halo effect around
  high-delta areas.

Click **Exit Delta View** (Row 1) to restore original vertex colours.

----

Tool Settings
-------------

The **Tool Settings** panel sits above the shelf and is toggled open/closed
by clicking the **Tool Settings** disclosure button.

.. list-table::
   :widths: 25 75
   :header-rows: 1

   * - Control
     - Description
   * - **Falloff**
     - Falloff type used by sculpt brushes: *Surface* (default) or *Volume*.
   * - **Symmetry**
     - Enables Maya's symmetry sculpting mode.
   * - **Strength**
     - Brush strength (0.000 – 1.000, default 0.500). No spinner arrows;
       type directly or drag the field.
   * - **Radius**
     - Brush radius in scene units. No spinner arrows; type directly or drag
       the field.

Double-clicking any sculpt brush button in the shelf opens Maya's native
**Tool Settings** window for that brush.
