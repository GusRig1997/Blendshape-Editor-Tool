Modify Deltas
=============

The **Modify Deltas** section provides post-sculpt operations that act
directly on the vertex delta vectors of a blendShape target.
Most tools respect an active vertex selection — if vertices are selected,
only those are affected; otherwise the entire target is processed.

All operations are undoable as a single step.

The section starts in **compact (grid) mode**. Click the section header to
expand it to the full layout.

----

Multiply / Invert / Nullify
---------------------------

**Multiply Deltas**

Scales the X, Y, and/or Z components of every delta vector individually.

.. list-table::
   :widths: 30 70
   :header-rows: 1

   * - Control
     - Description
   * - **X / Y / Z fields** *(default 1.2)*
     - Click a field to edit that axis. The value is mirrored to all other
       fields that are also selected (Shift-click to multi-select).
   * - **Multiply Deltas** button
     - Applies the per-axis scale (full-width button above Invert/Nullify).

Common use cases:

- ``1.0`` — no change
- ``0.0`` — zero out an axis (e.g. remove all vertical movement)
- ``-1.0`` — invert the axis
- ``1.2`` — amplify by 20 %

**Invert**

Multiplies all delta components by ``-1`` (hardcoded — ignores the X/Y/Z
fields). Equivalent to flipping the entire shape.

**Nullify**

Sets all vertex deltas to zero — equivalent to sculpting everything back to
rest pose. Hardcoded to ``×0`` (ignores the X/Y/Z fields).
Use with caution: a confirmation dialog is shown before clearing the target.

.. note::
   If you need a precise negative or zero value on a single axis, type it
   directly into the X, Y, or Z fields and use **Multiply Deltas** instead.

----

Add / Sub / Transfer Delta
--------------------------

These operations work on two targets selected in the Shape Editor.

.. list-table::
   :widths: 20 80
   :header-rows: 1

   * - Button
     - Description
   * - **Add**
     - Adds the deltas of target B to target A (A += B).
   * - **Sub**
     - Subtracts the deltas of target B from target A (A −= B).
   * - **Transfer Delta**
     - Copies the deltas of target A to target B, replacing B's deltas
       entirely. The selection order matters: first selected = source (A),
       last selected = destination (B).

----

Normal Push
-----------

Adds displacement along each vertex's outward normal, weighted by the
existing delta magnitude — vertices with larger deltas receive a larger push.

.. list-table::
   :widths: 30 70
   :header-rows: 1

   * - Control
     - Description
   * - **Push Factor** *(default 0.20)*
     - Magnitude of the push relative to the existing delta length.
   * - **+** / **−** radio buttons
     - ``+`` pushes outward (positive normal direction).
       ``−`` pushes inward.
   * - **Normal Push** button
     - Applies the operation.

Only vertices that already have non-zero deltas are affected.
Useful for adding volume (puff) or collapsing (sink) to an existing shape.

----

Opacity
-------

A shared **Opacity** slider (1 – 100, default 50) controls the intensity of
the four operations below it: Smooth, Relax, Hammer, and Average.

.. list-table::
   :widths: 30 70
   :header-rows: 1

   * - Operation
     - How opacity is used
   * - **Smooth Deltas**
     - ``n_passes = max(1, round(opacity × 0.1))`` → 1–10 passes.
   * - **Relax Deltas**
     - Same pass calculation as Smooth.
   * - **Hammer Deltas**
     - ``n_passes = max(1, round(opacity × 0.2))`` → 1–20 passes.
   * - **Average Deltas**
     - ``opacity / 100`` is used directly as a lerp factor between the
       original delta and the averaged value.

----

Smooth Deltas
-------------

Applies Laplacian smoothing to the delta *vector field* — each vertex's
delta is replaced by a weighted average of its neighbours' deltas.

Use this to soften pinching or noisy sculpts without changing the
overall shape direction.

----

Relax Deltas
------------

Laplacian relaxation in *position space* — averages actual 3-D vertex
positions in the deformed state rather than smoothing the delta vectors
directly. The result is closer to a mesh relax, applied only to the
blendShape target.

----

Hammer Deltas
-------------

Replaces each selected vertex's delta with the IDW-weighted average of its
spatial neighbours' deltas. Useful for aggressively evening out irregular
sculpts while preserving the overall shape envelope.

Unlike Smooth, Hammer uses up to **20 passes** at full opacity (vs. 10 for
Smooth), making it more effective on heavily noisy regions.

A **vertex selection is required** — Hammer does not operate on the full
target.

**Space**

Controls the coordinate space used to compute neighbour distances:

.. list-table::
   :widths: 20 80
   :header-rows: 1

   * - Option
     - Behaviour
   * - **Neutral** *(default)*
     - Distances are measured on the rest mesh (no deltas applied).
       Vertices that are close at rest influence each other, regardless of
       the current deformation.
   * - **Deformed**
     - Distances are measured on the mesh with deltas applied.
       Neighbours are determined by proximity in the deformed pose.

*Example — open mouth shape:*

- **Neutral** — upper and lower lip vertices are geometrically close at
  rest, so the hammer may blend deltas across the mouth gap.
- **Deformed** — lips are spread apart in the open-mouth pose; each lip is
  hammered independently without cross-influence.

**Laplacian**

Number of additional topological Laplacian smoothing passes applied
*after* the Hammer iterations (0–10, default 1). Acts as a post-process
to clean up residual high-frequency noise left by the spatial pass.
Set to ``0`` to skip.

----

Average Deltas
--------------

Blends each vertex's current delta toward the arithmetic mean of *all
selected vertices'* deltas, controlled by the Opacity lerp factor.
At opacity 100 each vertex is set exactly to the group average;
at opacity 1 only a 1 % blend is applied.

Useful for levelling a cluster of vertices to a common displacement value.

A **vertex selection is required**.

----

Copy / Paste Delta
------------------

.. list-table::
   :widths: 30 70
   :header-rows: 1

   * - Button
     - Description
   * - **Copy Delta**
     - Stores the delta vector of the **single selected vertex** on the
       active target. Held in memory until the next copy or tool restart.
   * - **Paste Delta**
     - Writes the stored delta onto all currently selected vertices.
       (Disabled until a delta has been copied.)

----

Select Delta Verts
------------------

Selects all vertices on the active target that have a non-zero delta.
Useful for isolating the sculpted region before applying other operations.

----

Prune Small Deltas
------------------

Zeros out any delta whose Euclidean magnitude falls below a tolerance
threshold. Removes noise from accidental micro-sculpts.

.. list-table::
   :widths: 30 70
   :header-rows: 1

   * - Control
     - Description
   * - **Tolerance** *(0.001 – 10.0, default 0.001)*
     - Any delta smaller than this value is set to zero.
   * - **Prune Small Deltas** button
     - Applies the prune.

----

Bake Moves
----------

Bakes the current vertex tweaks (``pnts[]`` offsets) from the mesh into the
selected blendShape target's deltas, then zeroes them out on the mesh.

Use this when you sculpted or moved vertices directly on the mesh (outside
of sculpt/edit mode) and want those moves to become part of the target.

- Works on **1 selected target** only.
- The moves are *added* to the existing deltas (non-destructive).

----

Bake Deformers
--------------

Bakes the contribution of all deformers upstream of the blendShape (e.g.
lattices, clusters, wrap deformers) into the selected targets, then the
upstream deformer can be safely deleted.

- Works on multiple targets in one pass.
- Each target is activated to weight 1.0, the deformer stack is evaluated,
  and the resulting positions are baked as new deltas.

----

Create Delta Cluster
--------------------

Converts the active target into a cluster deformer for viewport feedback.

- Regenerates the target as a posed static mesh.
- Creates a cluster with weights proportional to the delta magnitudes.

Useful for inspecting influence regions or painting corrective weights.

**Neutral checkbox**
  When checked, the cluster is created at rest pose with normalised weights
  rather than at the posed position.

**Multi checkbox**
  When checked, creates one cluster per selected target instead of merging
  all selected targets into a single cluster.

----

Create Delta Joint
------------------

Binds two joints to the target's deformation region:

- ``{target}_jnt`` — skinned with weights equal to the normalised delta
  magnitudes (most-deformed vertices have weight 1.0).
- ``{target}_zero_jnt`` — receives the complementary weights
  (``1 − w``), acting as a stable anchor.

Both joints are placed under a ``{target}_grp`` group.
This helper is a starting point for joint-driven blendShape setups.
