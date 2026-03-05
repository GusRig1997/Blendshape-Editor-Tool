Secondary Meshes
================

The **Secondary Meshes** section automates the transfer of blendShape targets
from a primary mesh onto secondary meshes (e.g. teeth, gums, tongue, eyeballs)
using wrap-deformer-based extraction.

----

Connect Extracted Targets *(checkbox, default ON)*
--------------------------------------------------

When this option is enabled, the tool automatically connects the weight
attributes of matching targets after extraction.

Pattern: ``source_bs.<name>  →  mesh_bs.<name>``

Any target whose name exists on both the source and destination blendShape
nodes will be driven by the same weight.

----

Extract Wrap Targets
--------------------

Creates a wrap deformer between the secondary mesh and the blendShape base
mesh, then extracts every selected target through that wrap.

**Workflow**

1. Select one or more **secondary meshes** in the scene.
2. Select the **targets** to extract in the Shape Editor.
3. Click **Extract Wrap Targets**.

**What happens**

- A wrap deformer is created on each secondary mesh, driven by the
  blendShape base mesh.
- Each selected target is posed and extracted as a static mesh through the
  wrap.
- The extracted shapes are added as new targets on the secondary mesh's
  blendShape (which is created if it does not already exist).
- If *Connect Extracted Targets* is ON, target weights are connected.

----

Extract Only
------------

Uses an **existing** deformer already present on the secondary mesh
(wrap, proximity wrap, delta mush, etc.) to extract targets. Does not
create or remove any deformers.

**Workflow**

1. Ensure the secondary mesh already has a deformer driven by the base mesh.
2. Select the secondary mesh in the scene.
3. Select the targets to extract in the Shape Editor.
4. Click **Extract Only**.

----

Connect Targets A → B
---------------------

Connects all matching weight attributes between two meshes that already
have their own blendShape nodes.

**Workflow**

1. Select **mesh A** (source) first.
2. Shift-select **mesh B** (destination) second.
3. Click **Connect Targets A→B**.

Every target name present on both blendShape nodes will be connected:
``bs_A.<name>  →  bs_B.<name>``.
