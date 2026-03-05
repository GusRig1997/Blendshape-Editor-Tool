Wire Setup
==========

The **Wire Setup** tool (inside the **Tools** section) builds a curve-based
deformation rig for lip and mouth shapes, then bakes the results as
blendShape targets on the original mesh.

.. tip::
   Hover over the **Wire Setup** group title for a quick workflow reminder.

----

Concept
-------

A wire deformer driven by a blendShape curve (``wire_bs``) controls a
duplicate of the base mesh (``wire_setup_msh``). Each shape is stored as
a blendShape target on ``wire_crv`` — sculpting the curve directly in the
viewport deforms the mesh in real time. When satisfied, **Bake Wire to Mesh**
transfers each posed state as a standard blendShape target on the original mesh.

----

Step-by-step Workflow
---------------------

1. **Set the base mesh** — select a mesh transform and click **Get**.
2. **Capture the edge loop** — select a symmetrical edge loop
   (upper *or* lower lip line) and click **Get** next to the Edges field.
3. **Review the shape list** — add, remove, or rename entries as needed.
4. Click **Create Wire Setup** — the rig is built in the scene.
5. **Sculpt** each shape by moving CVs of the target curves in the viewport
   (set the corresponding ``wire_bs`` weight to 1.0 to see the result live).
6. Click **Bake Wire to Mesh** to transfer all shapes to the base mesh's
   blendShape node.

----

Controls
--------

Base Mesh
^^^^^^^^^

.. list-table::
   :widths: 25 75
   :header-rows: 1

   * - Control
     - Description
   * - **Text field**
     - Displays the captured mesh transform name.
   * - **Get**
     - Sets the field to the currently selected object.

Edges
^^^^^

.. list-table::
   :widths: 25 75
   :header-rows: 1

   * - Control
     - Description
   * - **Text field** *(read-only)*
     - Displays the captured edge selection as an index list.
   * - **Get**
     - Captures the currently selected edges.

.. important::
   Select only **one continuous edge loop** — upper lip line *or* lower
   lip line, not both. The loop must be symmetrical left-to-right.

Shape Curves List
^^^^^^^^^^^^^^^^^

The list defines which curve-based shapes will be created.

Default entries:

.. code-block:: text

   lip_up · lip_dn · lip_out · lip_in
   mouth_corner_out · mouth_corner_in
   mouth_corner_up · mouth_corner_dn

.. list-table::
   :widths: 25 75
   :header-rows: 1

   * - Control
     - Description
   * - **List widget**
     - Shows all registered shape names. Double-click an entry to rename it.
   * - **Name field + Add**
     - Type a new shape name and click **Add** to append it to the list.
   * - **Remove**
     - Deletes the selected entry from the list.

Parameters
^^^^^^^^^^

.. list-table::
   :widths: 25 75
   :header-rows: 1

   * - Parameter
     - Description
   * - **Dropoff** *(default 100.0)*
     - Wire deformer dropoff distance. A large value (100+) means the
       deformation reaches far from the curve — appropriate for lip rigs.
   * - **Rotation** *(default 0.0)*
     - Wire deformer rotation attribute (0.0 – 1.0).
   * - **Spans** *(default 4)*
     - Number of spans when the extracted curve is rebuilt.
       More spans = more CVs = finer sculpting control.
   * - **Flat Curve** *(checkbox, default ON)*
     - Flattens all CVs to the Y position of the first CV after rebuild.
       Keep ON for horizontal lip/mouth edge loops.
       Disable for curved surfaces such as cheeks or eyelids.

----

Create Wire Setup
-----------------

Click **Create Wire Setup** to build the rig.

**Nodes created in the scene:**

.. code-block:: text

   wire_setup_grp
   ├── wire_setup_msh     ← deforming duplicate of the base mesh
   ├── wire_crv           ← master wire curve (rebuilt, driven by wire_bs)
   ├── <shape>_crv …      ← one target curve per shape (hidden)
   └── wire_setup_wire    ← wire deformer node

- ``wire_bs`` is a blendShape on ``wire_crv`` with one target per shape name.
- Setting ``wire_bs.<shape>`` to ``1.0`` poses ``wire_setup_msh`` via the
  wire deformer.

----

Bake Wire to Mesh
-----------------

Transfers each shaped state of ``wire_setup_msh`` as a blendShape target
on the base mesh's blendShape node.

**Pre-bake check — no deltas warning**

Before baking, the tool checks whether each shape curve has any stored
delta. If one or more shapes have no displacement:

- A warning dialog lists the empty shapes.
- You can choose to **proceed anyway** (the target will be baked empty)
  or **cancel** the operation.

**Overwrite behaviour**

If a target with the same name already exists on the blendShape node,
it is overwritten and a warning is printed to the Script Editor.

**Delete Wire Setup after Bake** *(checkbox, default OFF)*

When checked, ``wire_setup_grp`` (and all its children) is deleted from
the scene automatically after a successful bake.
