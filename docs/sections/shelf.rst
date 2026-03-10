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
   * - *(separator)*
     -
   * - **Shape Editor**
     - Opens Maya's native Shape Editor window.
   * - **Smooth Target**
     - Activates Maya's *Smooth* sculpt brush in target mode.
       Double-click opens Tool Settings.
   * - **Erase**
     - Activates Maya's *Erase* sculpt brush.
       Double-click opens Tool Settings.
   * - *(separator)*
     -
   * - **Delta View**
     - Colorises mesh vertices by cumulative delta magnitude. See below.
   * - **Exit Delta View**
     - Restores original vertex colours and disables Delta View.

----

Row 2 — Extra Sculpt Tools & Node Utilities
--------------------------------------------

.. list-table::
   :widths: 20 80
   :header-rows: 1

   * - Button
     - Description
   * - **Relax**
     - Activates Maya's *Relax* mesh sculpt brush.
       Double-click opens Tool Settings.
   * - **Pinch**
     - Activates Maya's *Pinch* mesh sculpt brush.
       Double-click opens Tool Settings.
   * - **Amplify**
     - Activates Maya's *Amplify* mesh sculpt brush.
       Double-click opens Tool Settings.
   * - *(separator)*
     -
   * - **Add Target**
     - See below — dual left/right-click behaviour.
   * - **Clean Blendshape Node**
     - Removes phantom (empty/unaliased) target slots from the blendShape
       node(s) of the targets selected in the Shape Editor.
   * - **Reset All Targets to 0**
     - Sets every target weight on the blendShape node(s) to 0.
       Useful to return to neutral pose after previewing shapes.
   * - *(separator)*
     -
   * - *(reserved)*
     - Space reserved for future tools.

----

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

Click **Exit Delta View** to restore original vertex colours.
