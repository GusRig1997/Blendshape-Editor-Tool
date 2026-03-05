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
