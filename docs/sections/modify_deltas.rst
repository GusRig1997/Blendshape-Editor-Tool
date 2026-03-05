Modify Deltas
=============

The **Modify Deltas** section provides post-sculpt operations that act
directly on the vertex delta vectors of a blendShape target.
Most tools respect an active vertex selection — if vertices are selected,
only those are affected; otherwise the entire target is processed.

All operations are undoable as a single step.

----

Multiply Deltas
---------------

Scales the X, Y, and/or Z components of every delta vector individually.

**Axis buttons (X / Y / Z)**
  Click to select an axis field. Shift-click to add it to the selection.
  The value typed in any selected field is instantly mirrored to all other
  selected fields.

**Value fields**
  Default value: ``1.2``.
  Common use cases:

  - ``1.0`` — no change
  - ``0.0`` — zero out an axis (e.g. remove all vertical movement)
  - ``-1.0`` — invert the axis
  - ``1.2`` — amplify by 20 %

Click **Multiply Deltas** to apply.

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

Smooth Deltas
-------------

Applies Laplacian smoothing to the delta *vector field* — each vertex's
delta is replaced by a weighted average of its neighbours' deltas.

.. list-table::
   :widths: 30 70
   :header-rows: 1

   * - Control
     - Description
   * - **Opacity slider** *(1 – 100)*
     - Maps to 1 – 10 smoothing passes.
       ``50`` = 5 passes, ``100`` = 10 passes (very powerful).
   * - **Smooth Deltas** button
     - Applies the smoothing.

Use this to soften pinching or noisy sculpts without changing the
overall shape direction.

----

Relax Deltas
------------

Laplacian relaxation in *position space* — averages actual 3-D vertex
positions in the deformed state rather than smoothing the delta vectors
directly. The result is closer to a mesh relax, applied only to the
blendShape target.

Same opacity control as Smooth Deltas.

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

Create Delta Cluster
--------------------

Converts the active target into a cluster deformer for viewport feedback.

- Regenerates the target as a posed static mesh.
- Creates a cluster with weights proportional to the delta magnitudes.

Useful for inspecting influence regions or painting corrective weights.

----

Create Delta Joint
------------------

Binds two joints to the target's deformation region:

- ``{target}_jnt`` — skinned with weights equal to the normalised delta
  magnitudes (most-deformed vertices have weight 1.0).
- ``{target}_zero_jnt`` — receives the complementary weights
  (``1 - w``), acting as a stable anchor.

Both joints are placed under a ``{target}_grp`` group.
This helper is a starting point for joint-driven blendShape setups.
