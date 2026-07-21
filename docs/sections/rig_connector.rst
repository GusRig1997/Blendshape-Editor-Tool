Rig Connector
=============

The **Rig Connector** (accessible from the **Rig Connector** menu in the
menu bar) maps FK controller attributes to blendShape target weights.
It builds a Maya node network that normalises the controller value, applies
an optional gate, clamps the result, and drives the target weight.

----

Opening the Dialog
------------------

Go to **Rig Connector → Open Rig Connector…** in the menu bar.

Before using the table, click **Get BS Node** to load the blendShape node
you want to connect. The **Build & Connect** button is only enabled after a
node has been successfully loaded.

----

Connection Table
----------------

Each row in the table represents a mapping between one blendShape target
and one controller attribute.

.. list-table::
   :widths: 5 12 14 10 12 6 9 9 10 13
   :header-rows: 1

   * - #
     - Shape
     - Controller
     - Attr
     - Custom Attr
     - Dir
     - In Min
     - In Max
     - Cond.
     - Status
   * - Row index
     - Target name on the blendShape node
     - Maya controller transform name
     - Built-in attribute (e.g. ``ty``, ``rx``)
     - Custom attribute name (if Attr is not a standard channel)
     - Drive direction: **+** or **−**
     - Controller value at which the target weight is 0
     - Controller value at which the target weight is 1
     - Gate node toggle (see below)
     - Connection state after Build & Connect

**Direction (Dir)**
  ``+`` — the target activates as the controller moves in the positive
  direction (e.g. translateY increases).
  ``−`` (unicode minus) — the target activates as the controller moves in
  the negative direction.

**In Min / In Max**
  Define the normalisation range.
  The node network maps ``[In Min, In Max]`` → ``[0, 1]`` for the target
  weight. A non-zero In Min generates an offset node that subtracts the
  minimum before normalisation.

**Cond. (Gate node)**
  When enabled, a ``multDoubleLinear`` gate node (``gate_{shape}``) is
  inserted between the clamp output and the blendShape weight. This allows
  the connection to be toggled on/off at runtime without rebuilding the
  network.

**Status**
  After **Build & Connect** runs, each row displays the connection result:
  ✓ connected, or an error description if the connection failed.

----

Row Operations
--------------

**Right-click → Add Proxy Row**
  Adds a secondary driver row for the same shape (displayed with a ``↳``
  prefix and greyed text). Multiple proxy rows create additive drivers —
  their normalised contributions are summed before reaching the clamp.
  Use this to drive one target from several controllers simultaneously.

**Move Up / Move Down**
  Reorders selected rows. Multi-row selection is supported; the relative
  order within the selection is preserved.

----

hasLimits Attribute
-------------------

After building, a ``hasLimits`` boolean attribute is added to each
controller (always last in the attribute list).

.. list-table::
   :widths: 20 80
   :header-rows: 1

   * - Value
     - Behaviour
   * - **ON** *(default)*
     - The target weight is clamped to ``[0, 1]`` and Maya transform limits
       are activated on the controller.
   * - **OFF**
     - The clamp maximum is set to a very large value (effectively unlimited)
       so the target weight can exceed 1.0. The minimum remains 0 (no
       negative weights). Transform limits are deactivated.

``hasLimits`` is connected to a condition node (``cond_{shape}``) that
switches the clamp maximum dynamically, so toggling the attribute in the
Channel Box is enough to switch modes without rebuilding the network.

On **rebuild**, the attribute is repositioned to remain the last attribute
on the controller using a delete + undo technique that also preserves any
existing connections.

----

Node Network (per shape)
------------------------

The following nodes are created for each target:

.. code-block:: text

   ctrl.attr
     └── [offset_{shape}_{i}]   addDoubleLinear — subtracts In Min (if In Min ≠ 0)
           └── norm_{shape}_{i}  multiplyDivide  — normalises to [0, 1]
                 └── [sum_{shape}]  plusMinusAverage — sums proxy rows (if > 1 driver)
                       └── cond_{shape}  condition    — switches clamp max via hasLimits
                             └── clamp_{shape}  clamp — minR=0, maxR from cond
                                   └── [gate_{shape}]  multDoubleLinear (optional)
                                         └── bs_node.weight[N]

Nodes in brackets are only created when their condition applies.

----

Auto-stagger
------------

The **Auto-stagger** panel generates staggered In Min / In Max ranges
automatically for a set of shapes driven by the same controller.

.. list-table::
   :widths: 30 70
   :header-rows: 1

   * - Control
     - Description
   * - **Master Controller**
     - The controller whose attribute range is being staggered.
   * - **Axis**
     - The attribute on the master controller (e.g. ``ty``).
   * - **In Max Ref**
     - The total controller travel. Shapes are distributed evenly across
       ``[0, In Max Ref]``.

**Stagger Modes**

.. list-table::
   :widths: 20 80
   :header-rows: 1

   * - Mode
     - Description
   * - **Normal**
     - Each shape occupies slot ``k`` in range
       ``[k/N × In Max Ref, (k+1)/N × In Max Ref]`` with optional falloff.
   * - **Mirror**
     - Peaks are centred (``N/2`` slots). All shapes drive in the same
       direction.
   * - **Symmetric**
     - Same slot layout as Mirror, but left-side shapes drive in the
       opposite direction from right-side shapes. The **+/−** checkbox
       controls which side is positive.

Mirror and Symmetric modes are mutually exclusive.

Click **Apply Stagger** to write the computed In Min / In Max values into
the corresponding table rows.

----

Build & Connect
---------------

Click **Build & Connect** to create the node network for all rows in the
table.

- Existing ``offset_*``, ``norm_*``, ``sum_*``, ``cond_*``, ``clamp_*``,
  and ``gate_*`` nodes for the affected shapes are deleted and rebuilt from
  scratch.
- Custom attributes listed in the **Custom Attr** column are created on the
  controller if they do not already exist.
- The **Status** column updates for each row after the operation completes.
