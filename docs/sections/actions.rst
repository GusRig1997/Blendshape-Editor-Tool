Actions
=======

The **Actions** section groups the most common target-level operations:
duplicate, mirror, flip, and create an opposite target.
All operations are undoable as a single step and support multi-target
selection in the Shape Editor.

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
   * - **Axis** *(combo: Object X / Y / Z)*
     - Symmetry axis used for the flip.
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
   * - **Axis** *(combo: Object X / Y / Z)*
     - Symmetry axis for the flip and the naming pair lookup.
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
