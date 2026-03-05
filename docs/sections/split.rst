Split
=====

The **Split** section divides an existing blendShape target into multiple
weighted sub-targets using spatial locators. Each locator defines a region
of influence; the tool computes per-vertex blend weights and writes new
targets accordingly.

----

Locators Table
--------------

The table on the left lists every locator used in the split.

.. list-table::
   :widths: 20 20 60
   :header-rows: 1

   * - Column
     - Editable?
     - Description
   * - **Name**
     - No
     - Short name of the locator (full path stored internally).
   * - **Side**
     - When Symmetric OFF
     - Side token (``R`` / ``L`` / ``M``) used in the generated name.
       Auto-filled and locked when Symmetric is ON.
   * - **Suffix**
     - When Symmetric OFF
     - Directional suffix (e.g. ``in``, ``out``, ``mid``).
       Auto-filled and locked when Symmetric is ON.

**Naming conventions for auto-filled suffixes:**

- 1 locator → single target (no suffix)
- 2 locators → ``in`` / ``out``
- 3 locators → ``in`` / ``mid`` / ``out``
- 4+ locators → alphabetical: ``a`` / ``b`` / ``c`` / …

Locator Buttons
^^^^^^^^^^^^^^^

.. list-table::
   :widths: 25 75
   :header-rows: 1

   * - Button
     - Action
   * - **Create Locator**
     - Creates a new ``split_locator#`` in the scene at the origin (or
       snapped to the current selection), adds it to the table, and
       refreshes all side/suffix values.
   * - **+ Get**
     - Adds the locators currently selected in the scene to the table.
   * - **↑ Up**
     - Moves the selected row one position up.
   * - **↓ Down**
     - Moves the selected row one position down.
   * - **− Remove**
     - Deletes all selected rows and refreshes side/suffix values.
       Supports multi-row selection.
   * - **Link**
     - Links left/right locator pairs for mirrored motion
       (requires Symmetric ON).
   * - **Unlink**
     - Breaks an existing mirror link.

----

Axis Options
------------

Defines the projection direction used to compute influence weights.

**Projection mode**

- **Radial OFF** — weights are computed from 1-D projection along the
  selected axis (axes are radio buttons; only one active at a time).
- **Radial ON** — weights are computed from 3-D Euclidean distance
  (sphere); all three checkboxes become independent.

**Axis checkboxes / radio buttons**: X · Y · Z

**Invert Axis**
  Negates the projection direction, swapping which side is "near" and
  which is "far" relative to each locator.

**Local Axes** *(default ON)*
  Uses each locator's local coordinate frame instead of world axes.
  Keep this ON unless your locators are aligned to world space.

**Symmetric L/R** *(default OFF)*
  When enabled:

  - The **Side** and **Suffix** columns become read-only and are
    filled automatically.
  - The tool generates paired targets for both sides from a single
    split operation.
  - Locator count determines side assignment:

    - 1 locator → single bilateral target
    - 2 locators → R / L
    - 3 locators → R / M / L

----

Falloff Options
---------------

**Falloff curve**

Controls how weight transitions from 1.0 to 0.0 between locators.

.. list-table::
   :widths: 25 75
   :header-rows: 1

   * - Curve
     - Description
   * - **Smoother Step** *(default)*
     - Quintic smootherstep — smooth S-curve, no sharp edges.
   * - **Linear**
     - Straight linear interpolation.
   * - **Ease In**
     - Quadratic ease-in — slow start, fast finish.
   * - **Ease Out**
     - Quadratic ease-out — fast start, slow finish.

**Radius**

Constrains the influence of each locator to a maximum distance.

- **Enable checkbox** — turns the radius constraint on/off.
  Automatically enabled when the table contains exactly one locator,
  disabled when two or more locators are present.
- **Slider** (1 – 150) / **Spin box** (0.1 – 15.0 units) — set the
  radius value; both controls are linked.

----

Split Target
------------

**Split Target** button
  Reads the locator table, computes per-vertex weights for every locator,
  and creates one new target per locator (or per locator pair when
  Symmetric is ON). The original target is preserved unchanged.

  - Works on all targets selected in the Shape Editor.
  - Target names follow the token order configured in **Nomenclature**.

----

Edge Loop Split
---------------

**Edge Loop Split** button
  Specialised split driven by a selected edge loop rather than locators.

  1. Select an edge loop on the mesh.
  2. Select the target(s) in the Shape Editor.
  3. Click **Edge Loop Split**.

  The falloff is centred on the selected edges, with the same curve
  options as the standard split. A smoothing pass is applied to the
  resulting delta field.
