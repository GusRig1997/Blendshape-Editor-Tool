"""
BET_reload.py
=============
Script de reload BÉTON pour le Blendshape Editor Tool.
Colle ce script dans le Script Editor Maya et lance-le à chaque mise à jour du .py

Ce script :
  1. Copie le .py depuis SOURCE_DIR vers le dossier scripts Maya (écrase l'ancien)
  2. Ferme et supprime le workspaceControl existant
  3. Ferme la fenêtre flottante si présente
  4. Purge TOUS les modules liés du cache Python (sys.modules)
  5. Importe le fichier frais depuis le disque
  6. Lance le tool proprement
"""

import sys
import shutil
import os
import maya.cmds as cmds

# ── SEULE LIGNE À MODIFIER : dossier source (là où tu télécharges/modifies) ──
SOURCE_DIR = r"C:\Users\auror\Downloads\blendshape_editor_tool_v02.001\source"

TOOL_FILE  = "Blendshape_Editor_Tool.py"

# Dossier Maya scripts (destination automatique)
MAYA_SCRIPTS = cmds.internalVar(userScriptDir=True)

# ── 1. Copie source → Maya scripts ───────────────────────────────────────────
src = os.path.join(SOURCE_DIR, TOOL_FILE)
dst = os.path.join(MAYA_SCRIPTS, TOOL_FILE)

if not os.path.exists(src):
    raise FileNotFoundError(f"[BET] Fichier source introuvable : {src}")

# ── Diagnostic : version dans le fichier SOURCE avant copie ──────────────────
with open(src, encoding="utf-8") as _f:
    _src_content = _f.read()
_ver_line = next((l.strip() for l in _src_content.splitlines()
                  if "VERSION" in l and "=" in l), "VERSION non trouvée")
print(f"[BET] Source    : {src}")
print(f"[BET] Version   : {_ver_line}")
print(f"[BET] Taille    : {os.path.getsize(src)} octets / modifié le {__import__('datetime').datetime.fromtimestamp(os.path.getmtime(src))}")

shutil.copy2(src, dst)
print(f"[BET] Copié  →  : {dst}")

# ── 2. Fermer le workspaceControl dockable ────────────────────────────────────
ctrl = "BlendshapeEditorToolWorkspaceControl"
if cmds.workspaceControl(ctrl, query=True, exists=True):
    cmds.deleteUI(ctrl, deleteHistory=True)
    print(f"[BET] WorkspaceControl supprimé : {ctrl}")
# Supprimer aussi les prefs sauvegardées du workspace
try:
    cmds.workspaceControlState(ctrl, remove=True)
    print(f"[BET] WorkspaceControl prefs purgées")
except Exception:
    pass

# ── 3. Fermer la fenêtre flottante si elle existe encore ─────────────────────
try:
    import Blendshape_Editor_Tool as _old
    if hasattr(_old, "_win") and _old._win is not None:
        try:
            _old._win.close()
            _old._win.deleteLater()
            print("[BET] Ancienne fenêtre fermée")
        except Exception:
            pass
except Exception:
    pass

# ── 4. Purger sys.modules ────────────────────────────────────────────────────
_to_purge = [k for k in sys.modules if "Blendshape_Editor_Tool" in k
                                     or "blendshape_editor_tool" in k]
for k in _to_purge:
    del sys.modules[k]
    print(f"[BET] Purgé : {k}")

# ── 5. Nettoyer sys.path — retirer le dossier source, mettre Maya scripts en 1er ──
# Le dossier source ne doit PAS être dans sys.path au moment de l'import,
# sinon Python charge l'ancien fichier depuis Downloads au lieu de Maya/scripts.
sys.path = [p for p in sys.path if os.path.normpath(p) != os.path.normpath(SOURCE_DIR)]

if MAYA_SCRIPTS not in sys.path:
    sys.path.insert(0, MAYA_SCRIPTS)
else:
    # S'assurer qu'il est EN PREMIER
    sys.path.remove(MAYA_SCRIPTS)
    sys.path.insert(0, MAYA_SCRIPTS)

print(f"[BET] sys.path[0] = {sys.path[0]}")

# ── 6. Importer et lancer ─────────────────────────────────────────────────────
import Blendshape_Editor_Tool as BET
print(f"[BET] Chargé depuis : {BET.__file__}")
BET.show()