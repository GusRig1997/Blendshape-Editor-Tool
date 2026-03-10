Nomenclature
============

The **Nomenclature** section (collapsed by default) contains two independent
tools:

- **Naming Convention** — configure how the tool auto-names generated targets.
- **Rename Targets** — directly rename existing targets in the Shape Editor.

----

Naming Convention Dialog
------------------------

Click **Naming Convention…** to open the dialog.

Tool's Auto-naming
^^^^^^^^^^^^^^^^^^

Controls the token order used when the Split and Create Opposite Target
operations generate new names automatically.

**Preset**

Six built-in token order presets are available:

.. list-table::
   :widths: 50 50
   :header-rows: 1

   * - Preset
     - Example output
   * - ``{side}_{target}_{suffix}`` *(default)*
     - ``R_cheekbone_up``
   * - ``{target}_{side}_{suffix}``
     - ``cheekbone_R_up``
   * - ``{target}_{suffix}_{side}``
     - ``cheekbone_up_R``
   * - ``{prefix}_{side}_{target}_{suffix}``
     - ``Facial_R_cheekbone_up``
   * - ``{prefix}_{target}_{side}_{suffix}``
     - ``Facial_cheekbone_R_up``
   * - ``{prefix}_{target}_{suffix}_{side}``
     - ``Facial_cheekbone_up_R``

**Token drag-and-drop area**

The four tokens — ``{prefix}``, ``{side}``, ``{target}``, ``{suffix}`` — can
be dragged into any order. The live **Preview** label updates instantly to
reflect the result (e.g. *Preview → e.g. R_cheekbone_up*).

.. note::
   The **Prefix** text field is greyed out automatically when the
   ``{prefix}`` token is not present in the current order.

**Side tokens**

Three single-character fields define the side labels used in generated names:

.. list-table::
   :widths: 20 20 60
   :header-rows: 1

   * - Field
     - Default
     - Description
   * - **Left**
     - ``L``
     - Token written for the left side.
   * - **Center**
     - ``C``
     - Token written for the center / bilateral side.
   * - **Right**
     - ``R``
     - Token written for the right side.

Change these if your project uses a different side convention (e.g.
``lft`` / ``ctr`` / ``rgt``). The Split section and symmetric operations
read these values at runtime.

----

Opposite Target — Naming Pairs
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Defines which tokens are treated as opposites when using
**Create Opposite Target**. Organised in three tabs — one per symmetry axis.

**Built-in pairs** are shown in grey (read-only).

.. list-table::
   :widths: 20 80
   :header-rows: 1

   * - Axis
     - Default pairs
   * - **X**
     - L/R · l/r · lft/rgt · left/right · in/out · pos/neg · p/n
   * - **Y**
     - up/dn · up/down · up/lo · u/d · u/l · upper/lower · top/bot ·
       top/bottom · hi/lo · high/low · higher/lower · pos/neg · p/n ·
       raise/depress
   * - **Z**
     - fwd/bwd · front/back · frt/bck · f/b · ant/post · pos/neg · p/n

**Custom pairs** can be added in any tab:

1. Type the two tokens in the pair text fields.
2. Click **Add**.
3. Remove a pair with the **✕** button next to it.

Custom pairs are saved persistently to a JSON file in your Maya preferences
folder (``blendshape_editor_naming.json``).

----

Rename Targets
--------------

Operates on the targets currently selected in the **Shape Editor**.

Prefix / Suffix
^^^^^^^^^^^^^^^

.. list-table::
   :widths: 20 80
   :header-rows: 1

   * - Field
     - Description
   * - **Set Prfx**
     - Text to prepend to each selected target name.
   * - **Sufx**
     - Text to append to each selected target name.

Click **Apply** to rename. Both fields are applied simultaneously.

Search & Replace
^^^^^^^^^^^^^^^^

.. list-table::
   :widths: 20 80
   :header-rows: 1

   * - Field
     - Description
   * - **S&R**
     - String to search for within existing target names.
   * - **→**
     - Replacement string. Leave empty to delete the matched substring.

Click **Apply** to process all selected targets.

Swap Target Names
^^^^^^^^^^^^^^^^^

Select **exactly two** targets in the Shape Editor, then click
**Swap Names** to exchange their names.
The vertex deltas are untouched — only the name attributes are swapped.
The operation is fully undoable.

----

Check Shapes
------------

Click **Check Shapes…** to open the dialog. It compares the targets present
on the selected blendShape node against a reference list of expected shape
names.

Reference List
^^^^^^^^^^^^^^

The reference list is stored as a JSON file. By default the tool ships with
``resources/check_shapes_default.json``.

Use the **File** menu inside the dialog to manage the list:

.. list-table::
   :widths: 25 75
   :header-rows: 1

   * - Menu item
     - Action
   * - **Load…**
     - Opens a file browser to load any ``.json`` file as the reference list.
       The last loaded path is remembered across sessions.
   * - **Save…**
     - Saves the current list (including any edits made in the dialog) to a
       ``.json`` file of your choice.
   * - **Reset to Default**
     - Reloads the built-in ``check_shapes_default.json`` and discards the
       remembered path.

The current file name is shown in the dialog's title bar.

Check Results
^^^^^^^^^^^^^

Targets are grouped into categories defined in the JSON file. Each target
name is displayed with a status indicator:

- **Green** — target exists on the blendShape node.
- **Red** — target is missing.

Match Existing to List
^^^^^^^^^^^^^^^^^^^^^^

Click **Match existing to List** to automatically suggest renames for targets
whose names do not match the reference list exactly but are close enough to
identify.

The tool compares targets using token sets (order-independent), so
``up_brow_L`` would match ``L_brow_up`` in the reference list.
It also detects targets that are missing only a side prefix (``C_``, ``L_``,
or ``R_``) and proposes adding it.

A **Rename Suggestions** dialog opens with a table showing:

.. list-table::
   :widths: 30 30 40
   :header-rows: 1

   * - Column
     - Description
     - Notes
   * - **Current Name**
     - Existing target name on the blendShape node
     -
   * - **Proposed Name**
     - Suggested rename from the reference list
     - Shown in orange as *(Not sure)* when the match is ambiguous
   * - **Apply?**
     - Checkbox to include this rename in the batch
     - Ambiguous matches are unchecked by default

Use **Check All** / **Uncheck All** to select or deselect all rows, then
**Apply Checked** to execute the renames. Renames are performed via
``aliasAttr`` and are fully undoable.
