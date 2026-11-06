Tools
=====

The **Tools** section (closed by default) groups three independent rigging
helpers inside collapsible QGroupBox panels.

----

Wrap Setup
----------

Extracts blendShape targets onto one or more secondary meshes using a wrap
deformer pipeline.

**Workflow**

1. Select the targets you want to extract in the **Shape Editor**.
2. Select the **secondary mesh(es)** you want to drive in the viewport
   (can be one or many).
3. Choose whether to connect extracted targets (see checkbox below).
4. Click **Extract Wrap Targets** or **Extract Only**.

.. list-table::
   :widths: 35 65
   :header-rows: 1

   * - Button
     - Description
   * - **Extract Wrap Targets**
     - Wraps each secondary mesh to the base mesh, bakes the selected
       targets as new blendShape targets on each secondary mesh, then
       deletes the temporary wrap deformer. If *Connect extracted targets*
       is enabled, the new target weights are connected to the original
       blendShape weights so they drive in sync.
   * - **Extract Only**
     - Uses the deformer setup you already built on the mesh target (wrap,
       proximity wrap, etc.): activates each target alone and duplicates the
       mesh target. The shapes are grouped under
       ``<mesh>_extractedShapes_grp``; no blendShape or deformer is created.
       Selection, any of: targets in the Shape Editor + mesh target; a
       blendShape node + mesh target (all its targets); a source mesh then the
       mesh target (all targets of the source's blendShape).

The options below are in **Extract Settings**, a collapsible block under the
two buttons (closed by default, click it to open), like the shelf's
**Tool Settings**.

**Overwrite** *(checkbox, default ON)*
  When a target with the same name already exists on the receiver, only its
  deltas are replaced: its connections, Shape Editor group, index, painted
  target weights and combination are kept. Its in-betweens are kept too (a
  warning lists them). OFF: the new target gets a numeric suffix.

**Skip empty** *(checkbox, default ON)*
  For both extractions: a target that moves no vertex of the mesh target by
  the threshold or more (default 0.001) is not extracted. With Overwrite, an
  existing target of the same name is then left as is. The threshold is set
  in the field next to the checkbox.

**Keep groups** *(checkbox, default ON)*
  Rebuilds the Shape Editor groups of the source blendShape: on the receiver
  blendShape for **Extract Wrap Targets**, as Outliner groups under the
  extracted shapes group for **Extract Only**. Groups that already exist with
  the same name are reused, empty groups are not created, and the receiver's
  own targets are not moved.

Wrap settings are fixed (Maya factory defaults: smooth bind, max distance 1,
volume falloff), so the result does not depend on each user's Wrap options.
If a receiver has active targets of its own, they are set to 0 during the
extraction (with a warning).

**Connect extracted targets** *(checkbox, default ON)*
  When enabled, **Extract Wrap Targets** automatically connects each
  extracted target weight on the secondary mesh to the corresponding
  weight on the original blendShape node.

----

Wire Setup
----------

Builds a curve-based deformation rig for lip and mouth shapes, then bakes
the results as blendShape targets on the original mesh.

For full documentation see the dedicated :doc:`wire_setup` page.

----

Joints Setup
------------

Builds a joint-based lip rig from a single middle edge, producing a skinned
joint chain that can be used to drive blendShape targets or corrective shapes.

**Middle Edge**

.. list-table::
   :widths: 25 75
   :header-rows: 1

   * - Control
     - Description
   * - **Text field** *(read-only)*
     - Displays the captured middle edge (e.g. ``pSphere1.e[42]``).
   * - **Get**
     - Captures the single edge currently selected in the viewport.
       This edge should sit at the centre of the lip seam and define the
       symmetry axis of the joint chain.

**Build Rig**

Click **Build Rig** to generate the joint-based rig from the captured middle
edge. The rig is placed in the scene ready for weight painting and connection
to the blendShape node.

.. note::
   **Joints Setup** is currently disabled and will be re-enabled in a future version.
