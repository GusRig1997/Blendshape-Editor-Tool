# Blendshape Editor Tool

A dockable Maya panel for creating, editing, and managing blendShape targets —
with spatial splitting, secondary mesh extraction, delta editing utilities, and a
curve-based lip/mouth wire rig.

**[Documentation](https://blendshape-editor-tool.readthedocs.io)**

---

## Requirements

- Autodesk Maya **2022 or later**
- PySide6 / shiboken6 (bundled with Maya 2022+)

---

## Installation

1. **Drag and drop** `dragDropInstaller.py` into the Maya viewport.
2. The installer copies the scripts and icons to your Maya user directory automatically.
3. A **BSEdtr** shelf button is created in your current shelf.
4. Restart Maya when prompted.

---

## Features

- **Locator-based spatial split** — divide any target into N weighted regions using 1-D projection or 3-D radial falloff (Smoother Step, Linear, Ease In, Ease Out)
- **Symmetric naming** — auto-generates paired L_/R_ targets from a single split operation
- **Secondary mesh pipeline** — Extract Wrap Targets, Extract Only, Connect Targets A→B
- **Delta editing suite** — Multiply, Normal Push, Smooth, Relax, Copy/Paste, Prune, Cluster, Joint helpers
- **Wire Setup** — build a curve-based lip rig from an edge loop, sculpt shapes, bake to blendShape targets
- **Naming Convention dialog** — configurable token order, prefix, and custom opposite-target pairs (persistent JSON)
- **Undo safety** — every operation wrapped in a single Maya undo chunk

---

## Changelog

See [versions.md](versions.md) for the full changelog.

**v.03.003** *(in progress)*
- Naming Convention dialog + Rename Targets tools
- Wire Setup (Create + Bake, configurable Spans / Flat Curve / Dropoff)
- Add Target right-click menu (Empty / From Selection / Corrective)
- Two-state collapsible sections (open ↔ closed, no intermediate compact state)
- Documentation button in Edit menu

**v.03.002**
- Dockable UI via MayaQWidgetDockableMixin
- Secondary Meshes section
- Maya Tools Shelf + Delta View

**v1.0.0**
- Initial release
