"""
╔══════════════════════════════════════════════════════════════╗
║          Blendshape Editor Tool — Drag & Drop Installer      ║
║                                                              ║
║  Drag and drop this file into the Maya viewport to           ║
║  install the tool automatically.                             ║
╚══════════════════════════════════════════════════════════════╝
"""

import maya.cmds as cmds
import maya.mel as mel
import os
import sys
import shutil


# ─── Configuration ────────────────────────────────────────────────────────────

TOOL_NAME   = "blendshape_ui"
TOOL_LABEL  = "Blendshape Editor Tool"
SHELF_LABEL = "BSEdtr"
ICON_NAME   = "split.png"

# ─── Maya paths ───────────────────────────────────────────────────────────────

MAYA_APP_DIR = cmds.internalVar(userAppDir=True)
SCRIPTS_DIR  = os.path.join(MAYA_APP_DIR, "scripts")
PREFS_ICONS  = os.path.join(MAYA_APP_DIR, "prefs", "icons")


# ─── Functions ────────────────────────────────────────────────────────────────

def _log(msg):
    print(f"[{TOOL_LABEL}] {msg}")


def _resolve_installer_dir(*args):
    """
    Tries every known method to find the directory of this installer file.
    Maya's drag & drop passes the file path differently depending on the version.
    """
    candidates = []

    # Method 1: args[0] if it looks like a file path
    if args:
        candidates.append(str(args[0]))

    # Method 2: inspect the call stack for a frame referencing this file
    import inspect
    for frame_info in inspect.stack():
        fname = frame_info[1]
        if "dragDropInstaller" in fname:
            candidates.append(fname)

    # Method 3: __file__ global
    try:
        candidates.append(__file__)
    except NameError:
        pass

    # Method 4: sys.argv
    for arg in sys.argv:
        if "dragDropInstaller" in arg:
            candidates.append(arg)

    _log(f"  Path candidates : {candidates}")

    for c in candidates:
        c = os.path.normpath(c)
        if os.path.isabs(c):
            d = os.path.dirname(c) if not os.path.isdir(c) else c
            if os.path.isdir(d):
                _log(f"  Resolved installer dir : {d}")
                return d

    raise RuntimeError(
        f"Could not resolve installer directory.\n"
        f"Candidates tried: {candidates}\n\n"
        f"Please run manually from the Script Editor:\n"
        f"  import sys; sys.path.insert(0, r'YOUR_FOLDER'); "
        f"import dragDropInstaller; dragDropInstaller.install(r'YOUR_FOLDER')"
    )


def _copy_scripts(installer_dir):
    """Copies Python scripts to the Maya scripts folder."""
    source_dir = os.path.join(installer_dir, "source")
    os.makedirs(SCRIPTS_DIR, exist_ok=True)
    count = 0
    for fname in os.listdir(source_dir):
        if fname.endswith(".py"):
            src = os.path.join(source_dir, fname)
            dst = os.path.join(SCRIPTS_DIR, fname)
            shutil.copy2(src, dst)
            _log(f"  Script copied : {fname}")
            count += 1
    return count


def _copy_icons(installer_dir):
    """Copies icons to the Maya prefs/icons folder."""
    icons_dir = os.path.join(installer_dir, "resources", "icons")
    os.makedirs(PREFS_ICONS, exist_ok=True)
    count = 0
    for fname in os.listdir(icons_dir):
        if fname.lower().endswith(".png"):
            src = os.path.join(icons_dir, fname)
            dst = os.path.join(PREFS_ICONS, fname)
            shutil.copy2(src, dst)
            _log(f"  Icon copied : {fname}")
            count += 1
    return count


def _add_to_shelf():
    """Adds a button to the active shelf."""
    mel.eval('refreshEditorTemplates')

    shelf_top     = mel.eval("$tmpVar=$gShelfTopLevel")
    current_shelf = cmds.tabLayout(shelf_top, query=True, selectTab=True)

    icon_path = os.path.join(PREFS_ICONS, ICON_NAME)
    icon = ICON_NAME if os.path.exists(icon_path) else "commandButton.png"

    command = (
        f"import {TOOL_NAME}\n"
        f"import importlib\n"
        f"importlib.reload({TOOL_NAME})\n"
        f"{TOOL_NAME}.show()"
    )

    existing = cmds.shelfLayout(current_shelf, query=True, childArray=True) or []
    for btn in existing:
        if cmds.shelfButton(btn, query=True, exists=True):
            if cmds.shelfButton(btn, query=True, label=True) == SHELF_LABEL:
                cmds.deleteUI(btn)
                _log("  Existing shelf button removed.")

    cmds.shelfButton(
        label             = SHELF_LABEL,
        command           = command,
        image             = icon,
        imageOverlayLabel = "",
        sourceType        = "python",
        annotation        = TOOL_LABEL,
        parent            = current_shelf,
    )
    _log(f"  Button added to shelf : {current_shelf}")


def _add_to_usersetup():
    """Adds the import marker to userSetup.py if not already present."""
    usersetup = os.path.join(SCRIPTS_DIR, "userSetup.py")
    marker    = f"# {TOOL_NAME} autostart"
    line      = (
        f"\n{marker}\n"
        f"import maya.utils\n"
        f"maya.utils.executeDeferred(lambda: None)  # placeholder\n"
    )
    if os.path.exists(usersetup):
        with open(usersetup, "r") as f:
            content = f.read()
        if marker in content:
            _log("  userSetup.py already configured.")
            return
        with open(usersetup, "a") as f:
            f.write(line)
    else:
        with open(usersetup, "w") as f:
            f.write(line)
    _log("  userSetup.py updated.")


def _show_result(scripts_count, icons_count):
    """Displays a confirmation popup."""
    msg = (
        f"{TOOL_LABEL} installed successfully!\n\n"
        f"  • {scripts_count} script(s) copied\n"
        f"  • {icons_count} icon(s) copied\n\n"
        f"A button has been added to your active shelf.\n"
        f"Restart Maya to fully validate the installation."
    )
    cmds.confirmDialog(
        title         = f"{TOOL_LABEL} — Installation",
        message       = msg,
        button        = ["OK"],
        defaultButton = "OK",
    )


def _close_workspace_controls():
    """Closes the existing tool window and removes workspaceControl entries."""
    # 1. Close via the module's _win reference — chemin le plus propre
    if "blendshape_ui" in sys.modules:
        try:
            mod = sys.modules["blendshape_ui"]
            win = getattr(mod, "_win", None)
            if win is not None:
                win.close()
                mod._win = None
                _log("  Existing window closed.")
        except Exception as e:
            _log(f"  Could not close via _win: {e}")

    # 2. Supprimer les workspace controls (fallback)
    for ctrl in ("BlendshapeEditorUIWorkspaceControl",
                 "BlendshapeEditorToolWorkspaceControl"):
        try:
            if cmds.workspaceControl(ctrl, query=True, exists=True):
                cmds.deleteUI(ctrl, deleteHistory=True)
                _log(f"  WorkspaceControl removed : {ctrl}")
        except Exception:
            pass
        try:
            cmds.workspaceControlState(ctrl, remove=True)
        except Exception:
            pass


def _purge_modules():
    """Removes all cached blendshape modules from sys.modules."""
    purged = []
    for key in list(sys.modules.keys()):
        if key in ("blendshape_ui", "blendshape_core"):
            del sys.modules[key]
            purged.append(key)
    if purged:
        _log(f"  Modules purged : {', '.join(purged)}")


def _fix_sys_path():
    """Ensures SCRIPTS_DIR is first in sys.path so the fresh copy is loaded."""
    norm = os.path.normpath(SCRIPTS_DIR)
    sys.path = [p for p in sys.path if os.path.normpath(p) != norm]
    sys.path.insert(0, SCRIPTS_DIR)
    _log(f"  sys.path[0] = {sys.path[0]}")


def _launch_fresh_ui():
    """Opens the tool after the installer has fully completed."""
    import blendshape_ui as ui
    ui.show()


def install(installer_dir=None):
    """Can also be called manually: install(r'C:\path\to\blendshape_editor_tool')"""
    if installer_dir is None:
        installer_dir = _resolve_installer_dir()

    _log(f"  Installer directory : {installer_dir}")

    # 1. Close old window before copying (while old module is still in memory)
    _close_workspace_controls()

    # 2. Copy files
    n_scripts = _copy_scripts(installer_dir)
    n_icons   = _copy_icons(installer_dir)
    _add_to_shelf()
    _add_to_usersetup()

    # 3. Purge stale modules and fix import path
    _purge_modules()
    _fix_sys_path()

    _log(f"Installation complete ({n_scripts} scripts, {n_icons} icons).")
    _show_result(n_scripts, n_icons)

    # Deferred so Maya finishes processing the close before opening the new window
    import maya.utils
    maya.utils.executeDeferred(_launch_fresh_ui)


# ─── Entry point (called by Maya on drag & drop) ──────────────────────────────

def onMayaDroppedPythonFile(*args):
    _log("Starting installation...")
    _log(f"  args received : {args}")

    try:
        installer_dir = _resolve_installer_dir(*args)
        install(installer_dir)

    except Exception as e:
        cmds.confirmDialog(
            title   = f"{TOOL_LABEL} — Error",
            message = f"An error occurred during installation:\n\n{e}",
            button  = ["OK"],
        )
        raise