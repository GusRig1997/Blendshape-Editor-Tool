Actions
=======

The **Actions** section groups the most common target-level operations:
duplicate, mirror, flip, create an opposite target, apply mesh moves,
and bake deformers.
All operations are undoable as a single step and support multi-target
selection in the Shape Editor.

The section is **open by default**.

----

Topology Edge
-------------

The **Edge** field at the top of the section stores the centered edge used
for topology-symmetry operations (Mirror, Flip, and Create Opposite Target
when **Topology** axis is selected).

.. list-table::
   :widths: 30 70
   :header-rows: 1

   * - Control
     - Description
   * - **Edge** *(read-only field)*
     - Stores one centered edge on the symmetry seam. Select the edge in the
       viewport and click **Get** to capture it.

The edge is required only when the **Topology** axis is active. For
object-space axes (X / Y / Z) the field is ignored.

----

Duplicate Target
----------------

Creates one or more copies of each selected target.

.. list-table::
   :widths: 30 70
   :header-rows: 1

   * - Control
     - Description
   * - **Passes** *(spinner, 1 – 20)*
     - Number of copies to create per target.
   * - **Duplicate Target** button
     - Runs the duplication.

Generated names follow the pattern:
``original`` → ``original_Copy`` → ``original_Copy2`` → …

----

Mirror Target
-------------

Copies the active target to its opposite side.

.. list-table::
   :widths: 30 70
   :header-rows: 1

   * - Control
     - Description
   * - **Axis** *(combo: X / Y / Z / Topology)*
     - Symmetry axis. **Topology** uses the edge stored in the **Edge** field
       above (topology-symmetry, mesh-independent).
   * - **Direction** *(combo: +, −)*
     - ``+`` copies from the positive side to the negative side.
       ``−`` copies from negative to positive.
   * - **Mirror Target** button
     - Runs the mirror.

----

Flip Target
-----------

Flips the active target onto itself across a symmetry axis — useful for
correcting asymmetric sculpts or creating a mirrored version in-place.

.. list-table::
   :widths: 30 70
   :header-rows: 1

   * - Control
     - Description
   * - **Axis** *(combo: X / Y / Z / Topology)*
     - Symmetry axis. **Topology** uses the edge stored in the **Edge** field
       above.
   * - **Flip Target** button
     - Runs the flip. The target name is preserved.

----

Create Opposite Target
----------------------

Duplicates the selected target, flips it across the chosen axis, and
renames it automatically using the naming pair conventions defined in
**Nomenclature → Naming Convention**.

.. list-table::
   :widths: 30 70
   :header-rows: 1

   * - Control
     - Description
   * - **Axis** *(combo: X / Y / Z / Topology)*
     - Symmetry axis for the flip and the naming pair lookup.
       **Topology** uses the edge stored in the **Edge** field above.
   * - **Create Opposite Target** button
     - Runs the operation on all targets selected in the Shape Editor.

**Renaming logic**

The tool scans the target name for a token that matches one of the
registered pairs for the selected axis (built-in + user-defined).
The matched token is swapped for its counterpart:

.. code-block:: text

   R_cheekbone_up  →  L_cheekbone_up   (axis X, pair L/R)
   lip_up          →  lip_dn           (axis Y, pair up/dn)
   brow_lft_raise  →  brow_rgt_raise   (axis X, pair lft/rgt)

A warning is shown if no matching pair is found for the selected axis.

.. note::
   Custom naming pairs added in the **Naming Convention** dialog are
   included in the lookup automatically and persist between sessions.

----

Apply Moves
-----------

Transfers the current mesh deformation (``pnts[]`` offsets applied directly
on the mesh in the viewport) into the selected blendShape target, then
resets the mesh back to neutral.

Use this when you sculpt or move vertices directly on the base mesh and want
to store those moves as a blendShape target delta without entering the
normal sculpt workflow.

.. list-table::
   :widths: 30 70
   :header-rows: 1

   * - Control
     - Description
   * - **Apply Moves** button
     - Reads ``pnts[]`` from the base mesh, bakes the values into the selected
       target's deltas, and zeroes the mesh deformation.
       Works on **1 selected target** only.

----

Bake Deformers
--------------

Bakes the contribution of all deformers stacked above the blendShape into
the selected targets. For each target the tool activates it at weight 1.0,
samples the fully evaluated mesh (deformers active), and stores the result
as the new delta set.

**Typical workflow:**

1. Add a Delta Mush (or any deformer) on the base mesh and tune it.
2. Select the targets to improve in the Shape Editor.
3. Click **Bake Deformers**.
4. Delete the deformer.

**How the bake is computed:**

- *Neutral sample* — all targets set to 0, deformers active → reference positions.
- *Per-target sample* — one target at 1.0, deformers active → baked positions.
- *New delta* = baked positions − neutral positions.

Only the deformer's *additional* contribution when the target is active is
captured; any constant effect at the neutral pose is factored out.

.. list-table::
   :widths: 30 70
   :header-rows: 1

   * - Control
     - Description
   * - **Bake Deformers** button
     - Processes all targets selected in the Shape Editor.
       Original blendShape weights are saved and restored after the operation.
