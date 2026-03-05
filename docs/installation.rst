Installation
============

Requirements
------------

- Autodesk Maya **2022 or later** (PySide6 / shiboken6 required)
- No external Python packages — uses Maya's bundled interpreter

Drag & Drop Installer
---------------------

The tool ships with a one-click installer.

1. Open Maya.
2. Locate ``dragDropInstaller.py`` in the tool folder.
3. **Drag and drop** the file directly into the Maya viewport.
4. The installer will:

   - Copy ``blendshape_ui.py`` and ``blendshape_core.py`` to ``MAYA_APP_DIR/scripts/``
   - Copy all icon PNGs to ``MAYA_APP_DIR/prefs/icons/``
   - Create a shelf button labelled **BSEdtr** in the current shelf

5. Restart Maya when prompted to ensure all modules are loaded correctly.

Opening the Tool
----------------

Click the **BSEdtr** shelf button.
The tool opens as a floating, dockable panel (376 × 900 px by default).
It can be docked to any panel area or left floating.

.. note::
   If the tool is already open when you click the shelf button again,
   it will be brought to the front rather than opening a second instance.
