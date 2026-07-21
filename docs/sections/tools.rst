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
     - Same as above but skips the weight connection step regardless of
       the checkbox state. Use this when you want targets on the secondary
       mesh but prefer to set up the connections manually.

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
