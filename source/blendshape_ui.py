from maya import cmds, mel
import traceback
from maya.app.general.mayaMixin import MayaQWidgetDockableMixin

from PySide6 import QtWidgets, QtCore, QtGui

from blendshape_core import *
from blendshape_core import (_save_shape_editor_selection, _restore_shape_editor_selection,
                             _collect_magnitudes, _find_blendshape_on_mesh)

import json, os, copy

# Module-level slot called from the MEL wrapper of setSculptTargetIndex
_sculpt_idx_callback = None

def _on_sculpt_target_index_changed(bs_node, idx):
    """Invoked by the MEL wrapper whenever setSculptTargetIndex fires."""
    if _sculpt_idx_callback is not None:
        _sculpt_idx_callback(bs_node, int(idx))


def _get_vertex_selection():
    """Return a list of selected vertex indices, or None if no vertices are selected."""
    import re as _re
    raw = cmds.filterExpand(cmds.ls(sl=True), selectionMask=31, expand=True) or []
    if not raw:
        return None
    indices = []
    for v in raw:
        m = _re.search(r'\[(\d+)\]', v)
        if m:
            indices.append(int(m.group(1)))
    return indices if indices else None


class _DblClickFilter(QtCore.QObject):
    """Event filter that fires a callback on mouse double-click."""
    def __init__(self, callback, parent=None):
        super().__init__(parent)
        self._cb = callback

    def eventFilter(self, obj, event):
        if event.type() == QtCore.QEvent.MouseButtonDblClick:
            self._cb()
            return True
        return super().eventFilter(obj, event)


class _GlobalMouseReleaseFilter(QtCore.QObject):
    """App-level event filter that fires a callback shortly after any mouse release.
    Used to detect Shape Editor selection changes, which have no dedicated Maya event."""
    def __init__(self, callback, parent=None):
        super().__init__(parent)
        self._cb = callback
        self._timer = QtCore.QTimer()
        self._timer.setSingleShot(True)
        self._timer.setInterval(100)
        self._timer.timeout.connect(self._cb)

    def eventFilter(self, obj, event):
        if event.type() == QtCore.QEvent.MouseButtonRelease:
            if not self._timer.isActive():
                self._timer.start()
        return False


def _user_naming_prefs_path():
    return os.path.join(cmds.internalVar(userPrefDir=True),
                        "blendshape_editor_naming.json")


def _rig_mapping_prefs_path():
    return os.path.join(cmds.internalVar(userPrefDir=True),
                        "blendshape_editor_rig_mapping.json")


def _smart_mapping_default(current_path=""):
    """Return the best default path/directory for mapping file dialogs.

    Priority:
      1. Directory of the currently loaded mapping file (if any).
      2. Directory of the current Maya scene file (if one is open).
      3. Maya user prefs directory (fallback).
    """
    if current_path:
        return current_path
    try:
        scene = cmds.file(q=True, sceneName=True) or ""
        scene_dir = os.path.dirname(scene)
        if scene_dir and os.path.isdir(scene_dir):
            return scene_dir
    except Exception:
        pass
    return _rig_mapping_prefs_path()


class _NoScrollCombo(QtWidgets.QComboBox):
    """QComboBox that ignores wheel scroll unless the widget has keyboard focus."""
    def wheelEvent(self, event):
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()


class _ClickableLabel(QtWidgets.QLabel):
    """QLabel that emits clicked() on left mouse press."""
    clicked = QtCore.Signal()

    def mousePressEvent(self, ev):
        if ev.button() == QtCore.Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(ev)


def _fmt_inval(v):
    """Format a float for InMin/InMax QLineEdit display (2 decimal places, dot separator)."""
    try:
        return f"{float(v):.2f}"
    except (TypeError, ValueError):
        return "0.00"


def _read_inval(le, default=0.0):
    """Read a float from an InMin/InMax QLineEdit, returning default on error."""
    try:
        return float(le.text())
    except (ValueError, AttributeError):
        return default


def _make_inval_le(value, tooltip=None):
    """Create a QLineEdit for InMin/InMax with double validator (dot separator, C locale)."""
    le = QtWidgets.QLineEdit()
    le.setAlignment(QtCore.Qt.AlignCenter)
    validator = QtGui.QDoubleValidator(-9999.0, 9999.0, 2, le)
    validator.setLocale(QtCore.QLocale.c())
    le.setValidator(validator)
    le.setText(_fmt_inval(value))
    if tooltip:
        le.setToolTip(tooltip)
    return le


def _apply_ctrl_skinning_style(cb, skinning_ctrl):
    """Tint the controller combobox to indicate this is also a skinning controller."""
    if skinning_ctrl:
        cb.setStyleSheet("QComboBox { background-color: #002a3a; }")
    else:
        cb.setStyleSheet("")


def _check_shapes_default_json_path():
    """Returns the path to the default check_shapes JSON shipped with the tool."""
    src_dir  = os.path.dirname(os.path.abspath(__file__))
    tool_dir = os.path.dirname(src_dir)
    return os.path.join(tool_dir, "resources", "check_shapes_default.json")


_CHECK_SHAPES_OPTIONVAR = "blendshapeEditor_checkShapesLastFile"

# ── Rig Connector constants ────────────────────────────────────────────────────
_RC_PART_MAP = {
    "LipUp":     "upper_lip",
    "LipDn":     "lower_lip",
    "Mouth":     "mouth",
    "Jaw":       "jaw",
    "Cheek":     "cheek",
    "Cheekbone": "cheekbone",
    "Eyebrow":   "brow",
    "LipCorner": "mouth_corner",
    "Nostril":   "nostril",
}
_RC_SIDE_MAP = {"L": "L", "R": "R", "M": "C"}
_RC_DIR_ATTR = {
    "up":      ("ty", "+"),
    "dn":      ("ty", "−"),
    "lft":     ("tx", "−"),
    "rgt":     ("tx", "+"),
    "in":      ("tx", "−"),
    "out":     ("tx", "+"),
    "rot_pos": ("rz", "+"),
    "rot_neg": ("rz", "−"),
}
_RC_CUSTOM_DIRS = {"puff_in", "puff_out", "puff", "curl_in", "curl_out", "curl"}
_FK_CONTROLLERS = [
    'FKLipUpB_L', 'FKLipUpA_L', 'FKLipUp_M', 'FKLipUpA_R', 'FKLipUpB_R',
    'FKLipDnB_R', 'FKLipDnA_R', 'FKLipDn_M', 'FKLipDnA_L', 'FKLipDnB_L',
    'FKMouth_M', 'FKLipCorner_R', 'FKLipCorner_L', 'FKJaw_M',
    'FKCheek_L', 'FKCheek_R',
    'FKCheekbone_L', 'FKCheekboneA_L', 'FKCheekboneB_L', 'FKCheekboneC_L',
    'FKCheekbone_R', 'FKCheekboneA_R', 'FKCheekboneB_R', 'FKCheekboneC_R',
    'FKNostril_L', 'FKNostril_R',
    'FKEyebrow_L', 'FKEyebrowE_L', 'FKEyebrowD_L', 'FKEyebrowC_L',
    'FKEyebrowB_L', 'FKEyebrowA_L',
    'FKEyebrow_R', 'FKEyebrowA_R', 'FKEyebrowB_R', 'FKEyebrowC_R',
    'FKEyebrowD_R', 'FKEyebrowE_R',
]


def _load_user_duos():
    path = _user_naming_prefs_path()
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_user_duos(data):
    with open(_user_naming_prefs_path(), "w") as f:
        json.dump(data, f, indent=2)


_BASE_DUOS = [
    ["L", "R"], ["l", "r"], ["lft", "rgt"], ["left", "right"],
    ["in", "out"], ["pos", "neg"], ["p", "n"],
    ["up", "dn"], ["up", "down"], ["upper", "lower"], ["top", "bot"], ["hi", "lo"],
    ["fwd", "bwd"], ["front", "back"], ["frt", "bck"],
]


def _swap_opposite_name(name):
    """
    Swaps the first recognizable symmetric token in `name` and returns the opposite.
    Returns None if no matching token is found.
    Uses _BASE_DUOS plus any user-defined pairs.
    """
    all_duos = list(_BASE_DUOS)
    for pairs in _load_user_duos().values():
        for p in pairs:
            if p not in all_duos:
                all_duos.append(p)

    tokens = name.split("_")
    for duo in all_duos:
        for i, tok in enumerate(tokens):
            if tok == duo[0]:
                new = tokens[:]
                new[i] = duo[1]
                return "_".join(new)
            elif tok == duo[1]:
                new = tokens[:]
                new[i] = duo[0]
                return "_".join(new)
    return None


def create_opposite_shape(symmetry_axis="Topology", topo_edge=None):
    """
    Supports multiple selection in the Shape Editor.
    Duplicates each selected target, flips it, and renames it with the opposite naming convention.
    symmetry_axis : one of "Object X", "Object Y", "Object Z", "Topology" — matches FLIP_AXIS_MAP.
    topo_edge     : required when symmetry_axis == "Topology" (e.g. "head_msh.e[3077]").
    """
    targets = get_selected_targets()
    if not targets:
        cmds.warning("Please select at least one blend shape target in the Shape Editor.")
        return

    # Pairs filtered by symmetry axis — each entry is [token_a, token_b]
    AXIS_DUOS = {
        "Object X": [
            ["L",    "R"    ],
            ["l",    "r"    ],
            ["lft",  "rgt"  ],
            ["left", "right"],
            ["in",   "out"  ],
            ["pos",  "neg"  ],
            ["p",    "n"    ],
        ],
        "Object Y": [
            ["up",     "dn"     ],
            ["up",     "down"   ],
            ["up",     "lo"     ],
            ["u",      "d"      ],
            ["u",      "l"      ],
            ["upper",  "lower"  ],
            ["top",    "bot"    ],
            ["top",    "bottom" ],
            ["hi",     "lo"     ],
            ["high",   "low"    ],
            ["higher", "lower"  ],
            ["pos",    "neg"    ],
            ["p",      "n"      ],
            ["raise",  "depress"],
        ],
        "Object Z": [
            ["fwd",   "bwd" ],
            ["front", "back"],
            ["frt",   "bck" ],
            ["f",     "b"   ],
            ["ant",   "post"],
            ["pos",   "neg" ],
            ["p",     "n"   ],
        ],
    }
    # Merge user-defined pairs (persistent, saved via NamingConventionDialog)
    for _ax, _pairs in _load_user_duos().items():
        if _ax in AXIS_DUOS:
            _existing = [tuple(p) for p in AXIS_DUOS[_ax]]
            for _p in _pairs:
                if tuple(_p) not in _existing:
                    AXIS_DUOS[_ax].append(_p)

    # Try axis-specific pairs first, then fall back to all pairs
    primary_duos  = AXIS_DUOS.get(symmetry_axis, [])
    fallback_duos = [d for ax, duos in AXIS_DUOS.items() for d in duos
                     if ax != symmetry_axis and d not in primary_duos]

    for bs_name, index, shape in targets:
        # Find opposite name from naming convention
        names          = shape.split("_")
        opposite_shape = None
        matched_duo    = None
        matched_in_primary = False
        flip_axis_name = symmetry_axis  # may be overridden by fallback dialog

        for duos in primary_duos:
            fix = [x for x in names if x in duos]
            if fix:
                matched_duo        = duos
                matched_in_primary = True
                opposite_shape     = shape.replace(duos[0], fix[0]) if fix[0] == duos[0]                                      else shape.replace(duos[1], duos[0]) if fix[0] == duos[1]                                      else None
                if fix[0] == duos[0]:
                    opposite_shape = shape.replace(duos[0], duos[1])
                else:
                    opposite_shape = shape.replace(duos[1], duos[0])
                break

        # If not found in primary, check fallback (no axis mismatch warning)
        if not matched_in_primary:
            for duos in fallback_duos:
                fix = [x for x in names if x in duos]
                if fix:
                    matched_duo = duos
                    if fix[0] == duos[0]:
                        opposite_shape = shape.replace(duos[0], duos[1])
                    else:
                        opposite_shape = shape.replace(duos[1], duos[0])
                    break

        if opposite_shape is None:
            axis_label = symmetry_axis.replace("Object ", "")
            cmds.warning(
                f"Skipping '{shape}': no matching naming convention for axis {axis_label}. "
                f"Expected tokens like: {', '.join('/'.join(d) for d in primary_duos[:4])}..."
            )

        if opposite_shape is None:
            continue

        # Duplicate via duplicate_target — works correctly with multiple selections
        base_mesh        = get_base_mesh(bs_name)
        dup_idx          = None
        duplicated_shape = f"{shape}_Copy"

        # Read combination info before touching any connections
        source_combo_info = get_combination_info(bs_name, index)

        # Temporarily disconnect only the source target's incoming connections so
        # sculptTarget -regenerate can set the weight freely.  Other targets are
        # left untouched — disconnecting all of them on every iteration is
        # unnecessarily expensive when many targets are selected.
        src_attr  = f"{bs_name}.w[{index}]"
        src_conns = cmds.listConnections(src_attr, source=True, destination=False, plugs=True) or []
        src_conns = [c for c in src_conns
                     if cmds.nodeType(c.split(".")[0]) != "combinationShape"]
        for c in src_conns:
            cmds.disconnectAttr(c, src_attr)

        try:
            dup_idx = duplicate_target(bs_name, base_mesh, index, duplicated_shape, force_reorder=True)

            # Flip the duplicate — flip_axis_name set at loop start, may be overridden by fallback dialog
            do_flip_target(bs_name, dup_idx, None, 0, flip_axis_name, topo_edge)
            cmds.setAttr(f"{bs_name}.{duplicated_shape}", 0)

            # Replace the existing opposite target if it already exists
            existing_shapes = cmds.listAttr(f'{bs_name}.w', m=True) or []
            was_fresh = opposite_shape not in existing_shapes
            if not was_fresh:
                existing_index = get_bs_weight_attribute_logical_index(bs_name, opposite_shape)
                old_shape   = f"{bs_name}.weight[{existing_index}]"
                new_shape   = f"{bs_name}.weight[{dup_idx}]"
                shape_value = cmds.getAttr(old_shape)

                out_conns = cmds.listConnections(old_shape, plugs=True, destination=True, s=False) or []
                for conn in out_conns:
                    cmds.connectAttr(new_shape, conn, force=True)

                in_conns = cmds.listConnections(old_shape, plugs=True, s=True, d=False) or []
                for conn in in_conns:
                    cmds.connectAttr(conn, new_shape, force=True)
                    cmds.disconnectAttr(conn, old_shape)

                mel.eval(f"blendShapeDeleteTargetGroup {bs_name} {existing_index};")
                if not in_conns:
                    cmds.setAttr(f"{bs_name}.{duplicated_shape}", shape_value)

            # Rename the flipped duplicate to the opposite name
            cmds.aliasAttr(opposite_shape, f"{bs_name}.{duplicated_shape}")
            print(f"Opposite created : {opposite_shape}")

            # Mirror driver connection — only when freshly created
            # src_conns holds the source target's original drivers (saved + disconnected above)
            if was_fresh:
                for driver_plug in src_conns:
                    dot = driver_plug.rfind(".")
                    if dot == -1:
                        continue
                    driver_node = driver_plug[:dot]
                    driver_attr = driver_plug[dot + 1:]

                    # Separate namespace from base node name
                    ns_sep    = driver_node.rfind(":")
                    ns_prefix = driver_node[:ns_sep + 1] if ns_sep != -1 else ""
                    base_node = driver_node[ns_sep + 1:] if ns_sep != -1 else driver_node

                    # Find a matching token in the node name and swap it
                    node_tokens = base_node.split("_")
                    opp_node = None
                    for duo in (primary_duos + fallback_duos):
                        for i, tok in enumerate(node_tokens):
                            if tok == duo[0]:
                                opp_tokens    = node_tokens[:]
                                opp_tokens[i] = duo[1]
                                opp_node      = ns_prefix + "_".join(opp_tokens)
                                break
                            elif tok == duo[1]:
                                opp_tokens    = node_tokens[:]
                                opp_tokens[i] = duo[0]
                                opp_node      = ns_prefix + "_".join(opp_tokens)
                                break
                        if opp_node:
                            break

                    if opp_node is None or opp_node == driver_node:
                        cmds.warning(
                            f"Create Opposite: could not find opposite driver for '{driver_node}'. "
                            f"Connect '{opposite_shape}' manually."
                        )
                        continue

                    opp_plug = f"{opp_node}.{driver_attr}"
                    if not cmds.objExists(opp_node):
                        cmds.warning(
                            f"Create Opposite: opposite node '{opp_node}' not found. "
                            f"Connect '{opposite_shape}' manually."
                        )
                        continue
                    if not cmds.objExists(opp_plug):
                        cmds.warning(
                            f"Create Opposite: attribute '{opp_plug}' not found. "
                            f"Connect '{opposite_shape}' manually."
                        )
                        continue

                    new_weight = f"{bs_name}.weight[{dup_idx}]"
                    cmds.connectAttr(opp_plug, new_weight, force=True)
                    print(f"Driver mirrored : {opp_plug} ->{new_weight}")

            # Mirror combination drive — only for freshly created targets.
            # Overwrite path already restored the old slot's incoming connections above.
            if was_fresh and source_combo_info:
                mirrored_drivers = []
                for driver_name in source_combo_info["driver_names"]:
                    tokens   = driver_name.split("_")
                    opp_name = None
                    for duo in (primary_duos + fallback_duos):
                        for i, tok in enumerate(tokens):
                            if tok in duo:
                                opp_tokens    = tokens[:]
                                opp_tokens[i] = duo[1] if tok == duo[0] else duo[0]
                                opp_name      = "_".join(opp_tokens)
                                break
                        if opp_name:
                            break
                    mirrored_drivers.append(opp_name or driver_name)
                mirrored_combo = {"driver_names": mirrored_drivers,
                                  "method": source_combo_info["method"]}
                apply_combination(bs_name, opposite_shape, mirrored_combo)

        except Exception:
            # Clean up the _Copy slot so it doesn't become a phantom target
            if dup_idx is not None:
                try:
                    mel.eval(f"blendShapeDeleteTargetGroup {bs_name} {dup_idx};")
                    print(f"Cleaned up temp slot [{dup_idx}] after error on '{shape}'")
                except Exception:
                    pass
            raise
        finally:
            # Reconnect the source target's connections regardless of success or failure
            for c in src_conns:
                if cmds.objExists(c.split(".")[0]):
                    cmds.connectAttr(c, src_attr, force=True)


class _ProgressCtx:
    """
    Context manager wrapping cmds.progressWindow.
    Displays an interruptable Maya progress window for long operations.

    Usage:
        with _ProgressCtx("Baking deformers", max_value=n_targets) as pb:
            for i, target in enumerate(targets):
                if pb.cancelled:
                    break
                pb.set(i, f"Baking {target}…")
                bake(target)
            pb.set(n_targets, "Done")
    """
    def __init__(self, title, max_value=100, interruptable=True, min_items=2):
        self._title         = title
        self._max           = max_value
        self._interruptable = interruptable
        self._active        = max_value >= min_items

    def __enter__(self):
        if self._active:
            cmds.progressWindow(
                title=self._title,
                minValue=0,
                maxValue=self._max,
                progress=0,
                isInterruptable=self._interruptable,
                status="Please wait…"
            )
        return self

    def __exit__(self, *_):
        if self._active:
            cmds.progressWindow(endProgress=True)

    def set(self, value, status=""):
        if self._active:
            cmds.progressWindow(e=True, progress=value, status=status)

    def advance(self, step=1, status=""):
        if self._active:
            cmds.progressWindow(e=True, step=step, status=status)

    @property
    def cancelled(self):
        return self._active and self._interruptable and cmds.progressWindow(q=True, isCancelled=True)


class _AddToListDialog(QtWidgets.QDialog):
    """
    Shows blendShape targets that are not in the Check Shapes JSON list and
    lets the user select which ones to add and to which group.
    """

    def __init__(self, unmatched, group_names, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add to List")
        self.setMinimumWidth(380)
        lay = QtWidgets.QVBoxLayout(self)

        lbl = QtWidgets.QLabel(
            f"{len(unmatched)} target(s) found in the blendShape node are not in the list.\n"
            "Select which ones to add:")
        lbl.setWordWrap(True)
        lay.addWidget(lbl)

        self._list = QtWidgets.QListWidget()
        self._list.setAlternatingRowColors(True)
        for name in sorted(unmatched):
            item = QtWidgets.QListWidgetItem(name)
            item.setFlags(item.flags() | QtCore.Qt.ItemIsUserCheckable)
            item.setCheckState(QtCore.Qt.Checked)
            self._list.addItem(item)
        lay.addWidget(self._list)

        sel_row = QtWidgets.QHBoxLayout()
        btn_all  = QtWidgets.QPushButton("All")
        btn_none = QtWidgets.QPushButton("None")
        btn_all.setFixedHeight(22)
        btn_none.setFixedHeight(22)
        btn_all.clicked.connect(lambda: self._set_all(QtCore.Qt.Checked))
        btn_none.clicked.connect(lambda: self._set_all(QtCore.Qt.Unchecked))
        sel_row.addWidget(btn_all)
        sel_row.addWidget(btn_none)
        sel_row.addStretch()
        lay.addLayout(sel_row)

        grp_row = QtWidgets.QHBoxLayout()
        grp_row.addWidget(QtWidgets.QLabel("Add to group:"))
        self._combo = QtWidgets.QComboBox()
        self._combo.addItems(group_names if group_names else ["misc"])
        grp_row.addWidget(self._combo, 1)
        btn_new = QtWidgets.QPushButton("New…")
        btn_new.setFixedWidth(52)
        btn_new.clicked.connect(self._new_group)
        grp_row.addWidget(btn_new)
        lay.addLayout(grp_row)

        bb = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _set_all(self, state):
        for i in range(self._list.count()):
            self._list.item(i).setCheckState(state)

    def _new_group(self):
        name, ok = QtWidgets.QInputDialog.getText(self, "New Group", "Group name:")
        if ok and name.strip():
            name = name.strip()
            self._combo.addItem(name)
            self._combo.setCurrentIndex(self._combo.count() - 1)

    def selected_names(self):
        return [self._list.item(i).text()
                for i in range(self._list.count())
                if self._list.item(i).checkState() == QtCore.Qt.Checked]

    def selected_group(self):
        return self._combo.currentText()


class RenameMatchDialog(QtWidgets.QDialog):
    """
    Shows a list of suggested renames (token-equivalent matches between existing
    blendShape targets and the JSON list). The user checks which ones to apply.
    """

    def __init__(self, bs_node, suggestions, parent=None):
        """
        suggestions : list of (current_alias, logical_index, proposed_name, is_ambiguous)
        """
        super().__init__(parent)
        self.bs_node     = bs_node
        self.suggestions = suggestions
        self.setWindowTitle("Match existing to List")
        self.setMinimumWidth(520)
        self.resize(520, 400)
        self._build_ui()

    def _build_ui(self):
        lay = QtWidgets.QVBoxLayout(self)
        lay.setSpacing(6)

        info = QtWidgets.QLabel(
            f"Found <b>{len(self.suggestions)}</b> target(s) with token-equivalent names "
            f"in the list. Check the ones you want to rename.")
        info.setWordWrap(True)
        lay.addWidget(info)

        # Table
        self.table = QtWidgets.QTableWidget(len(self.suggestions), 3)
        self.table.setHorizontalHeaderLabels(["Current name", "Proposed name", ""])
        self.table.horizontalHeader().setStretchLastSection(False)
        self.table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeToContents)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.NoSelection)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)

        COLOR_AMBIGUOUS = QtGui.QColor("#FF9800")

        for row, (current, _idx, proposed, is_ambiguous) in enumerate(self.suggestions):
            item_current  = QtWidgets.QTableWidgetItem(current)
            item_proposed = QtWidgets.QTableWidgetItem(proposed)
            item_current.setFlags(QtCore.Qt.ItemIsEnabled)
            item_proposed.setFlags(QtCore.Qt.ItemIsEnabled)

            item_check = QtWidgets.QTableWidgetItem()
            item_check.setFlags(QtCore.Qt.ItemIsUserCheckable | QtCore.Qt.ItemIsEnabled)
            item_check.setCheckState(QtCore.Qt.Checked if not is_ambiguous else QtCore.Qt.Unchecked)

            if is_ambiguous:
                item_proposed.setText(f"{proposed}  (Not sure)")
                item_proposed.setForeground(COLOR_AMBIGUOUS)
                item_check.setToolTip("Multiple matches found — verify before applying")

            self.table.setItem(row, 0, item_current)
            self.table.setItem(row, 1, item_proposed)
            self.table.setItem(row, 2, item_check)

        lay.addWidget(self.table)

        # Buttons row
        btn_row = QtWidgets.QHBoxLayout()
        btn_check_all   = QtWidgets.QPushButton("Check All")
        btn_uncheck_all = QtWidgets.QPushButton("Uncheck All")
        btn_apply       = QtWidgets.QPushButton("Apply Checked")
        btn_cancel      = QtWidgets.QPushButton("Cancel")
        btn_apply.setFixedHeight(28)
        btn_cancel.setFixedHeight(28)
        btn_row.addWidget(btn_check_all)
        btn_row.addWidget(btn_uncheck_all)
        btn_row.addStretch()
        btn_row.addWidget(btn_apply)
        btn_row.addWidget(btn_cancel)
        lay.addLayout(btn_row)

        btn_check_all.clicked.connect(lambda: self._set_all_checks(QtCore.Qt.Checked))
        btn_uncheck_all.clicked.connect(lambda: self._set_all_checks(QtCore.Qt.Unchecked))
        btn_apply.clicked.connect(self._apply)
        btn_cancel.clicked.connect(self.reject)

    def _set_all_checks(self, state):
        for row in range(self.table.rowCount()):
            self.table.item(row, 2).setCheckState(state)

    def _apply(self):
        renamed = []
        errors  = []
        for row, (current, idx, proposed, _ambig) in enumerate(self.suggestions):
            if self.table.item(row, 2).checkState() != QtCore.Qt.Checked:
                continue
            # Strip "(Not sure)" suffix that may have been added visually
            clean_proposed = proposed
            try:
                cmds.aliasAttr(clean_proposed, f"{self.bs_node}.w[{idx}]")
                renamed.append(f"{current} ->{clean_proposed}")
            except Exception as e:
                errors.append(f"{current}: {e}")

        msg = []
        if renamed:
            msg.append(f"Renamed {len(renamed)} target(s):\n" + "\n".join(f"  {r}" for r in renamed))
        if errors:
            msg.append(f"\nErrors ({len(errors)}):\n" + "\n".join(f"  {e}" for e in errors))
        QtWidgets.QMessageBox.information(self, "Match existing to List", "\n".join(msg) or "Nothing applied.")
        self.accept()


class CheckShapesDialog(QtWidgets.QDialog):
    """
    Dialog to check whether a list of expected blendShape targets exist
    on the bs_node of the selected mesh or Shape Editor targets.
    Shapes are organized in groups, editable and toggleable.
    """

    DEFAULT_SHAPES = {
        "lips_splits": [
            "C_upper_lip_up", "C_upper_lip_dn", "C_upper_lip_in", "C_upper_lip_out",
            "C_lower_lip_up", "C_lower_lip_dn", "C_lower_lip_in", "C_lower_lip_out",
            "L_upper_lip_up_a", "L_upper_lip_dn_a", "L_upper_lip_in_a", "L_upper_lip_out_a",
            "L_upper_lip_up_b", "L_upper_lip_dn_b", "L_upper_lip_in_b", "L_upper_lip_out_b",
            "R_upper_lip_up_a", "R_upper_lip_dn_a", "R_upper_lip_in_a", "R_upper_lip_out_a",
            "R_upper_lip_up_b", "R_upper_lip_dn_b", "R_upper_lip_in_b", "R_upper_lip_out_b",
            "L_lower_lip_up_a", "L_lower_lip_dn_a", "L_lower_lip_in_a", "L_lower_lip_out_a",
            "L_lower_lip_up_b", "L_lower_lip_dn_b", "L_lower_lip_in_b", "L_lower_lip_out_b",
            "R_lower_lip_up_a", "R_lower_lip_dn_a", "R_lower_lip_in_a", "R_lower_lip_out_a",
            "R_lower_lip_up_b", "R_lower_lip_dn_b", "R_lower_lip_in_b", "R_lower_lip_out_b",
        ],
        "lips_puffs": [
            "L_upper_lip_puff_in", "L_upper_lip_puff_out",
            "R_upper_lip_puff_in", "R_upper_lip_puff_out",
            "L_lower_lip_puff_in", "L_lower_lip_puff_out",
            "R_lower_lip_puff_in", "R_lower_lip_puff_out",
        ],
        "lips_curls": [
            "L_upper_lip_curl_in", "R_upper_lip_curl_in",
            "L_upper_lip_curl_out", "R_upper_lip_curl_out",
            "L_lower_lip_curl_in", "R_lower_lip_curl_in",
            "L_lower_lip_curl_out", "R_lower_lip_curl_out",
        ],
        "jaw": [
            "C_jaw_in", "C_jaw_out", "C_jaw_up", "C_jaw_dn", "C_jaw_lft", "C_jaw_rgt",
        ],
        "mouth": [
            "C_mouth_up", "C_mouth_dn", "C_mouth_lft", "C_mouth_rgt",
            "C_mouth_rot_pos", "C_mouth_rot_neg",
        ],
        "mouth_corners": [
            "L_mouth_corner_in", "L_mouth_corner_out",
            "L_mouth_corner_up", "L_mouth_corner_dn",
            "R_mouth_corner_in", "R_mouth_corner_out",
            "R_mouth_corner_up", "R_mouth_corner_dn",
        ],
        "cheekbones": [
            "L_cheekbone_up", "R_cheekbone_up",
            "L_cheekbone_up_a", "L_cheekbone_up_b", "L_cheekbone_up_c",
            "R_cheekbone_up_a", "R_cheekbone_up_b", "R_cheekbone_up_c",
            "L_cheekbone_dn", "R_cheekbone_dn",
            "L_cheekbone_dn_a", "L_cheekbone_dn_b", "L_cheekbone_dn_c",
            "R_cheekbone_dn_a", "R_cheekbone_dn_b", "R_cheekbone_dn_c",
            "L_cheekbone_out", "R_cheekbone_out",
        ],
        "cheeks": [
            "L_cheek_in", "L_cheek_out", "R_cheek_in", "R_cheek_out",
        ],
        "brows": [
            "L_brow_up", "L_brow_dn", "L_brow_in",
            "R_brow_up", "R_brow_dn", "R_brow_in",
        ],
        "brows_splits": [
            "L_brow_up_a", "L_brow_up_b", "L_brow_up_c", "L_brow_up_d", "L_brow_up_e",
            "R_brow_up_a", "R_brow_up_b", "R_brow_up_c", "R_brow_up_d", "R_brow_up_e",
            "L_brow_dn_a", "L_brow_dn_b", "L_brow_dn_c", "L_brow_dn_d", "L_brow_dn_e",
            "R_brow_dn_a", "R_brow_dn_b", "R_brow_dn_c", "R_brow_dn_d", "R_brow_dn_e",
        ],
        "neck": [
            "L_neck_a", "R_neck_a", "L_neck_b", "R_neck_b",
        ],
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Check Shapes")
        self.setMinimumWidth(400)
        self.resize(400, 640)
        self._build_ui()
        # Load last used file, or fall back to default JSON, or fall back to DEFAULT_SHAPES
        last = cmds.optionVar(q=_CHECK_SHAPES_OPTIONVAR) \
               if cmds.optionVar(exists=_CHECK_SHAPES_OPTIONVAR) else ""
        if last and os.path.isfile(last):
            self._load_shapes_from_path(last)
        else:
            self._load_shapes_from_path(_check_shapes_default_json_path())

    def _build_ui(self):
        lay = QtWidgets.QVBoxLayout(self)
        lay.setSpacing(6)

        # ── Menu bar ──────────────────────────────────────────────────────
        menu_bar  = QtWidgets.QMenuBar(self)
        _mb_font = menu_bar.font()
        _mb_font.setPointSize(8)
        menu_bar.setFont(_mb_font)
        _ico_dir  = cmds.internalVar(userAppDir=True) + "prefs/icons"
        menu_file = menu_bar.addMenu("File")
        act_load  = menu_file.addAction(QtGui.QIcon(f"{_ico_dir}/path.png"),  "Load…")
        act_load.setToolTip("Load a JSON shapes list from disk")
        act_save  = menu_file.addAction(QtGui.QIcon(f"{_ico_dir}/save.png"),  "Save…")
        act_save.setToolTip("Save the current list to a JSON file")
        menu_file.addSeparator()
        act_reset = menu_file.addAction("Reset to Default")
        act_reset.setToolTip("Reload the default shapes list shipped with the tool")
        lay.setMenuBar(menu_bar)

        # Tree
        self.tree = QtWidgets.QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setColumnCount(1)
        self.tree.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        lay.addWidget(self.tree)

        # Tree buttons
        _SB = 26  # small square button size

        btn_add_shp = QtWidgets.QPushButton("+")
        btn_add_shp.setToolTip("Add a shape entry")

        btn_remove = QtWidgets.QPushButton("−")
        btn_remove.setToolTip("Remove selected entry")

        btn_add_grp = QtWidgets.QToolButton()
        btn_add_grp.setToolTip("Add a group")
        _grp_px = QtGui.QPixmap(cmds.internalVar(userAppDir=True) + "prefs/icons/group.png")
        if not _grp_px.isNull():
            btn_add_grp.setIcon(QtGui.QIcon(
                _grp_px.scaled(_SB - 4, _SB - 4,
                               QtCore.Qt.KeepAspectRatio,
                               QtCore.Qt.SmoothTransformation)))
            btn_add_grp.setIconSize(QtCore.QSize(_SB - 4, _SB - 4))
        else:
            btn_add_grp.setText("G+")

        btn_up = QtWidgets.QPushButton("↑")
        btn_dn = QtWidgets.QPushButton("↓")

        btn_opp = QtWidgets.QPushButton("Create Opposite")
        btn_opp.setToolTip("Insert the opposite name of the selected shape into the same group")

        for b in (btn_add_shp, btn_remove, btn_add_grp, btn_up, btn_dn):
            b.setFocusPolicy(QtCore.Qt.NoFocus)

        row1 = QtWidgets.QHBoxLayout()
        row1.setSpacing(3)
        row1.addWidget(btn_add_shp, 1)
        row1.addWidget(btn_remove, 1)
        row1.addWidget(btn_add_grp, 1)

        row2 = QtWidgets.QHBoxLayout()
        row2.setSpacing(3)
        row2.addWidget(btn_opp, 1)
        row2.addWidget(btn_up, 1)
        row2.addWidget(btn_dn, 1)

        lay.addLayout(row1)
        lay.addLayout(row2)

        # Separator
        sep = QtWidgets.QFrame()
        sep.setFrameShape(QtWidgets.QFrame.HLine)
        sep.setStyleSheet("color: rgba(255,255,255,30);")
        lay.addWidget(sep)

        # Check + Match buttons
        btn_check = QtWidgets.QPushButton("Check")
        btn_check.setFixedHeight(28)
        lay.addWidget(btn_check)

        btn_match = QtWidgets.QPushButton("Match existing to List")
        btn_match.setFixedHeight(28)
        btn_match.setToolTip(
            "Scans the blendShape node for targets whose name tokens match a JSON list entry\n"
            "but are in the wrong order (e.g. L_brow_a_up ->L_brow_up_a, M_ ->C_).\n"
            "Opens a dialog showing all suggestions — you choose which ones to apply.")
        lay.addWidget(btn_match)

        # Results
        self.txt_results = QtWidgets.QTextEdit()
        self.txt_results.setReadOnly(True)
        self.txt_results.setFixedHeight(110)
        self.txt_results.setPlaceholderText(
            "Select targets in the Shape Editor or a mesh in the scene, then click Check.")
        lay.addWidget(self.txt_results)

        act_load.triggered.connect(self._load_json)
        act_save.triggered.connect(self._save_json)
        act_reset.triggered.connect(self._reset_default)
        btn_add_grp.clicked.connect(self._add_group)
        btn_add_shp.clicked.connect(self._add_shape)
        btn_remove.clicked.connect(self._remove_selected)
        btn_up.clicked.connect(self._move_item_up)
        btn_dn.clicked.connect(self._move_item_down)
        btn_opp.clicked.connect(self._create_opposite_item)
        btn_check.clicked.connect(self._run_check)
        btn_match.clicked.connect(self._run_match_to_list)

    def _populate_tree(self, data):
        self.tree.clear()
        for grp, shapes in data.items():
            self._add_group_item(grp, shapes)

    # ── JSON helpers ──────────────────────────────────────────────────────

    def _tree_to_dict(self):
        data = {}
        for i in range(self.tree.topLevelItemCount()):
            grp  = self.tree.topLevelItem(i)
            name = grp.text(0)
            data[name] = [grp.child(j).text(0) for j in range(grp.childCount())]
        return data

    def _load_shapes_from_path(self, path):
        default_path = _check_shapes_default_json_path()
        if path and os.path.isfile(path):
            try:
                with open(path, "r") as f:
                    data = json.load(f)
                self._populate_tree(data)
                label = "default" if os.path.abspath(path) == os.path.abspath(default_path) \
                        else os.path.basename(path)
                self.setWindowTitle(f"Check Shapes  —  {label}")
                return
            except Exception as e:
                QtWidgets.QMessageBox.warning(self, "Load Error", f"Could not read file:\n{e}")
        # Fallback to hard-coded DEFAULT_SHAPES
        self._populate_tree(self.DEFAULT_SHAPES)
        self.setWindowTitle("Check Shapes  —  default (built-in)")

    def _load_json(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Load Check Shapes", "", "JSON files (*.json)")
        if not path:
            return
        self._load_shapes_from_path(path)
        cmds.optionVar(sv=(_CHECK_SHAPES_OPTIONVAR, path))

    def _save_json(self):
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Save Check Shapes", "", "JSON files (*.json)")
        if not path:
            return
        if not path.endswith(".json"):
            path += ".json"
        try:
            with open(path, "w") as f:
                json.dump(self._tree_to_dict(), f, indent=4)
            cmds.optionVar(sv=(_CHECK_SHAPES_OPTIONVAR, path))
            self.setWindowTitle(f"Check Shapes  —  {os.path.basename(path)}")
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "Save Error", f"Could not save file:\n{e}")

    def _reset_default(self):
        default = _check_shapes_default_json_path()
        self._load_shapes_from_path(default)
        cmds.optionVar(sv=(_CHECK_SHAPES_OPTIONVAR, default))

    def _add_group_item(self, name, shapes=None):
        item = QtWidgets.QTreeWidgetItem([name])
        item.setCheckState(0, QtCore.Qt.Checked)
        item.setFlags(item.flags() | QtCore.Qt.ItemIsEditable)
        font = item.font(0)
        font.setBold(True)
        item.setFont(0, font)
        self.tree.addTopLevelItem(item)
        for shp in (shapes or []):
            child = QtWidgets.QTreeWidgetItem([shp])
            child.setFlags(child.flags() | QtCore.Qt.ItemIsEditable)
            item.addChild(child)
        item.setExpanded(True)
        return item

    def _add_group(self):
        item = self._add_group_item("new_group")
        self.tree.editItem(item, 0)

    def _add_shape(self):
        sel = self.tree.selectedItems()
        if not sel:
            return
        item   = sel[0]
        parent = item if item.parent() is None else item.parent()
        child  = QtWidgets.QTreeWidgetItem(["new_shape"])
        child.setFlags(child.flags() | QtCore.Qt.ItemIsEditable)
        parent.addChild(child)
        parent.setExpanded(True)
        self.tree.editItem(child, 0)

    def _remove_selected(self):
        for item in self.tree.selectedItems():
            parent = item.parent()
            if parent is None:
                self.tree.takeTopLevelItem(self.tree.indexOfTopLevelItem(item))
            else:
                parent.removeChild(item)

    def _create_opposite_item(self):
        item = self.tree.currentItem()
        if item is None:
            return
        parent = item.parent()
        if parent is None:
            return  # groups don't have an obvious opposite
        name = item.text(0)
        opp = _swap_opposite_name(name)
        if opp is None or opp == name:
            QtWidgets.QMessageBox.information(
                self, "Create Opposite",
                f"Could not find an opposite name for '{name}'.")
            return
        new_item = QtWidgets.QTreeWidgetItem([opp])
        new_item.setFlags(new_item.flags() | QtCore.Qt.ItemIsEditable)
        idx = parent.indexOfChild(item)
        parent.insertChild(idx + 1, new_item)
        self.tree.setCurrentItem(new_item)

    def _move_item_up(self):
        item = self.tree.currentItem()
        if item is None:
            return
        parent = item.parent()
        if parent is None:
            idx = self.tree.indexOfTopLevelItem(item)
            if idx <= 0:
                return
            self.tree.takeTopLevelItem(idx)
            self.tree.insertTopLevelItem(idx - 1, item)
        else:
            idx = parent.indexOfChild(item)
            if idx <= 0:
                return
            parent.removeChild(item)
            parent.insertChild(idx - 1, item)
        self.tree.setCurrentItem(item)

    def _move_item_down(self):
        item = self.tree.currentItem()
        if item is None:
            return
        parent = item.parent()
        if parent is None:
            idx = self.tree.indexOfTopLevelItem(item)
            if idx >= self.tree.topLevelItemCount() - 1:
                return
            self.tree.takeTopLevelItem(idx)
            self.tree.insertTopLevelItem(idx + 1, item)
        else:
            idx = parent.indexOfChild(item)
            if idx >= parent.childCount() - 1:
                return
            parent.removeChild(item)
            parent.insertChild(idx + 1, item)
        self.tree.setCurrentItem(item)

    def _resolve_bs_node(self):
        targets = get_selected_targets()
        if targets:
            return targets[0][0]
        sel = cmds.ls(sl=True, transforms=True)
        if sel:
            return _find_blendshape_on_mesh(sel[0])
        return None

    def _run_check(self):
        bs_node = self._resolve_bs_node()
        if not bs_node:
            self.txt_results.setPlainText(
                "No blendShape node found.\n"
                "Select targets in the Shape Editor or a mesh in the scene.")
            return

        existing = set(cmds.listAttr(bs_node + ".w", multi=True) or [])

        COLOR_OK      = QtGui.QColor("#4CAF50")
        COLOR_MISSING = QtGui.QColor("#F44336")
        COLOR_DEFAULT = QtGui.QColor(self.palette().color(QtGui.QPalette.Text))

        missing = []
        present = 0

        for i in range(self.tree.topLevelItemCount()):
            grp_item = self.tree.topLevelItem(i)
            enabled  = grp_item.checkState(0) == QtCore.Qt.Checked
            for j in range(grp_item.childCount()):
                shp_item = grp_item.child(j)
                shp_name = shp_item.text(0)
                if not enabled:
                    shp_item.setForeground(0, COLOR_DEFAULT)
                    continue
                if shp_name in existing:
                    shp_item.setForeground(0, COLOR_OK)
                    present += 1
                else:
                    shp_item.setForeground(0, COLOR_MISSING)
                    missing.append(shp_name)

        total = present + len(missing)
        if missing:
            lines = [f"bs_node : {bs_node}",
                     f"{present}/{total} present  —  {len(missing)} missing:\n"]
            lines += [f"  • {s}" for s in missing]
            self.txt_results.setPlainText("\n".join(lines))
        else:
            self.txt_results.setPlainText(
                f"✓  All {total} shapes present in '{bs_node}'.")

    def _run_match_to_list(self):
        bs_node = self._resolve_bs_node()
        if not bs_node:
            QtWidgets.QMessageBox.warning(
                self, "Match existing to List",
                "No blendShape node found.\n"
                "Select targets in the Shape Editor or a mesh in the scene.")
            return

        # Collect existing aliases
        alias_pairs = cmds.aliasAttr(bs_node, q=True) or []
        # aliasAttr returns a flat list: [alias, attr, alias, attr, ...]
        existing = {}  # alias ->logical_index
        for i in range(0, len(alias_pairs) - 1, 2):
            alias = alias_pairs[i]
            attr  = alias_pairs[i + 1]  # e.g. "weight[12]"
            try:
                idx = int(attr.split("[")[1].rstrip("]"))
                existing[alias] = idx
            except (IndexError, ValueError):
                pass

        # Collect all JSON names (flat set)
        json_names = []
        for grp in range(self.tree.topLevelItemCount()):
            grp_item = self.tree.topLevelItem(grp)
            for j in range(grp_item.childCount()):
                json_names.append(grp_item.child(j).text(0))
        json_name_set = set(json_names)

        # Build token lookup: frozenset(tokens) ->[json_names]
        # Normalize M ->C when tokenizing
        def _norm_tokens(name):
            toks = name.split("_")
            return frozenset("C" if t == "M" else t for t in toks)

        token_map = {}
        for jname in json_names:
            key = _norm_tokens(jname)
            token_map.setdefault(key, []).append(jname)

        # Find suggestions
        suggestions = []  # list of (current_alias, idx, proposed_name, is_ambiguous)
        for alias, idx in sorted(existing.items()):
            if alias in json_name_set:
                continue  # already correct

            # Pass 1 — token reorder (same tokens, wrong order, handles M→C)
            key = _norm_tokens(alias)
            matches = token_map.get(key, [])

            # Pass 2 — missing side prefix (e.g. jaw_up ->C_jaw_up)
            if not matches:
                side_matches = [f"{s}_{alias}" for s in ("C", "L", "R")
                                if f"{s}_{alias}" in json_name_set]
                # Also try token-reorder with each prefixed candidate
                if not side_matches:
                    for s in ("C", "L", "R"):
                        key_prefixed = _norm_tokens(f"{s}_{alias}")
                        side_matches += token_map.get(key_prefixed, [])
                matches = side_matches

            if not matches:
                continue
            is_ambiguous = len(matches) > 1
            proposed = matches[0]
            suggestions.append((alias, idx, proposed, is_ambiguous))

        # Targets that are in the BS node but not in the JSON and have no token match
        matched_aliases = {alias for alias, _, _, _ in suggestions}
        matched_aliases |= json_name_set & set(existing)
        unmatched = [alias for alias in sorted(existing.keys())
                     if alias not in matched_aliases]

        if not suggestions and not unmatched:
            QtWidgets.QMessageBox.information(
                self, "Match existing to List",
                "Nothing to do.\n"
                "All existing targets already match the list.")
            return

        # Step 1 — rename suggestions (token-equivalent matches)
        if suggestions:
            dlg = RenameMatchDialog(bs_node, suggestions, parent=self)
            dlg.exec_()

        # Step 2 — add unmatched targets to the JSON list
        if unmatched:
            group_names = [self.tree.topLevelItem(i).text(0)
                           for i in range(self.tree.topLevelItemCount())]
            add_dlg = _AddToListDialog(unmatched, group_names, parent=self)
            if add_dlg.exec_() == QtWidgets.QDialog.Accepted:
                names     = add_dlg.selected_names()
                grp_name  = add_dlg.selected_group()
                if names:
                    # Find or create the target group in the tree
                    target_grp = None
                    for i in range(self.tree.topLevelItemCount()):
                        if self.tree.topLevelItem(i).text(0) == grp_name:
                            target_grp = self.tree.topLevelItem(i)
                            break
                    if target_grp is None:
                        target_grp = self._add_group_item(grp_name)
                    for name in names:
                        child = QtWidgets.QTreeWidgetItem([name])
                        child.setFlags(child.flags() | QtCore.Qt.ItemIsEditable)
                        target_grp.addChild(child)
                    target_grp.setExpanded(True)



class _SoftBlendGraphWidget(QtWidgets.QWidget):
    """
    Mini graph editor for the global soft blend activation curve.
    Normalized space: U ∈ [-1, 1] (driver), V ∈ [-1, 1] (weight).
    Mapping at build time: u_actual = u_norm × in_max  (works for ± shapes).

    Key roles:
      idx 0  (-1,  0, linear) — fixed: partner full extent, dead zone
      idx 1  (-0.5, 0, linear) — fixed: partner half extent, dead zone
      idx 2  ( 0,  0, smooth) — U/V locked, tangent editable ->controls undershoot
      idx 3  ( 0.5, 0.5, auto) — fully editable: the "midpoint" key
      idx 4  ( 1,  1, linear) — fixed: full activation endpoint
    """

    keys_changed = QtCore.Signal()

    _KEY_R  = 5
    _MARGIN = 20
    # Visible viewport range (data space) — keys outside are drawn clipped
    _U_MIN, _U_MAX = -0.5, 1.0
    _V_MIN, _V_MAX = -0.5, 1.0

    DEFAULT_KEYS = [
        {"u": -1.0, "v": 0.0, "tangent": "linear"},
        {"u": -0.5, "v": 0.0, "tangent": "linear"},
        {"u":  0.0, "v": 0.0, "tangent": "smooth"},
        {"u":  0.5, "v": 0.5, "tangent": "auto"},
        {"u":  1.0, "v": 1.0, "tangent": "linear"},
    ]

    _U_LOCKED       = {0, 1, 2, 4}   # only midpoint (idx 3) can move horizontally
    _V_LOCKED       = {0, 1, 2, 4}   # only midpoint can move vertically
    _TANGENT_LOCKED = {0, 1, 4}      # indices 2 and 3 have editable tangents

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(200, 150)
        self.setFixedHeight(155)
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding,
                           QtWidgets.QSizePolicy.Fixed)
        self.setFocusPolicy(QtCore.Qt.ClickFocus)
        import copy
        self._keys = copy.deepcopy(self.DEFAULT_KEYS)
        self._sel  = None   # selected key index
        self._drag = None   # key index being dragged

    # ── Public API ─────────────────────────────────────────────────────────────

    def set_keys(self, keys):
        import copy
        self._keys = copy.deepcopy(keys)
        self._sel  = None
        self.update()
        self.keys_changed.emit()

    def get_keys(self):
        import copy
        return copy.deepcopy(self._keys)

    def selected_index(self):
        return self._sel

    def selected_key(self):
        return dict(self._keys[self._sel]) if self._sel is not None else None

    def set_selected_u(self, u):
        if self._sel is None or self._sel in self._U_LOCKED:
            return
        keys = self._keys
        lo = keys[self._sel - 1]["u"] if self._sel > 0 else -1.0
        hi = keys[self._sel + 1]["u"] if self._sel < len(keys) - 1 else 1.0
        keys[self._sel]["u"] = max(lo + 0.001, min(hi - 0.001, round(u, 3)))
        self.update()
        self.keys_changed.emit()

    def set_selected_v(self, v):
        if self._sel is None or self._sel in self._V_LOCKED:
            return
        self._keys[self._sel]["v"] = max(-1.0, min(1.0, round(v, 3)))
        self.update()
        self.keys_changed.emit()

    def set_selected_tangent(self, tangent):
        if self._sel is None or self._sel in self._TANGENT_LOCKED:
            return
        self._keys[self._sel]["tangent"] = tangent
        self.update()
        self.keys_changed.emit()

    # ── Coordinate helpers ────────────────────────────────────────────────────

    def _u2x(self, u):
        m = self._MARGIN
        w = self.width() - 2 * m
        return m + (u - self._U_MIN) / (self._U_MAX - self._U_MIN) * w

    def _v2y(self, v):
        m = self._MARGIN
        h = self.height() - 2 * m
        return m + (self._V_MAX - v) / (self._V_MAX - self._V_MIN) * h

    def _x2u(self, x):
        m = self._MARGIN
        w = max(self.width() - 2 * m, 1)
        return self._U_MIN + (x - m) / w * (self._U_MAX - self._U_MIN)

    def _y2v(self, y):
        m = self._MARGIN
        h = max(self.height() - 2 * m, 1)
        return self._V_MAX - (y - m) / h * (self._V_MAX - self._V_MIN)

    # ── Tangent slope (Catmull-Rom for smooth/auto) ───────────────────────────

    def _slope(self, idx, direction):
        keys = self._keys
        k    = keys[idx]
        tang = k["tangent"]
        if tang == "linear":
            if direction == "out" and idx < len(keys) - 1:
                nk = keys[idx + 1]
                du = nk["u"] - k["u"]
                return (nk["v"] - k["v"]) / du if abs(du) > 1e-9 else 0.0
            if direction == "in"  and idx > 0:
                pk = keys[idx - 1]
                du = k["u"] - pk["u"]
                return (k["v"] - pk["v"]) / du if abs(du) > 1e-9 else 0.0
            return 0.0
        # smooth / auto ->Catmull-Rom
        if 0 < idx < len(keys) - 1:
            pk = keys[idx - 1]; nk = keys[idx + 1]
            du = nk["u"] - pk["u"]
            return (nk["v"] - pk["v"]) / du if abs(du) > 1e-9 else 0.0
        if idx == 0 and len(keys) > 1:
            nk = keys[1]; du = nk["u"] - k["u"]
            return (nk["v"] - k["v"]) / du if abs(du) > 1e-9 else 0.0
        if idx == len(keys) - 1 and len(keys) > 1:
            pk = keys[-2]; du = k["u"] - pk["u"]
            return (k["v"] - pk["v"]) / du if abs(du) > 1e-9 else 0.0
        return 0.0

    # ── Paint ─────────────────────────────────────────────────────────────────

    def paintEvent(self, event):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        p.fillRect(self.rect(), QtGui.QColor(38, 38, 38))

        m = self._MARGIN
        grid_pen = QtGui.QPen(QtGui.QColor(60, 60, 60), 1, QtCore.Qt.DotLine)
        axis_pen = QtGui.QPen(QtGui.QColor(85, 85, 85), 1)
        font     = QtGui.QFont("Arial", 7)
        p.setFont(font)
        fm       = QtGui.QFontMetrics(font)

        # Grid + labels (only values inside the viewport range)
        lbl_col = QtGui.QPen(QtGui.QColor(100, 100, 100))
        for val in (-0.5, 0.0, 0.5, 1.0):
            lbl = f"{val:g}"
            lbl_w = fm.horizontalAdvance(lbl)
            # horizontal grid line (V axis)
            if self._V_MIN <= val <= self._V_MAX:
                p.setPen(grid_pen)
                y = int(self._v2y(val))
                p.drawLine(m, y, self.width() - m, y)
                p.setPen(lbl_col)
                p.drawText(2, y + fm.ascent() // 2, lbl)
            # vertical grid line (U axis)
            if self._U_MIN <= val <= self._U_MAX:
                p.setPen(grid_pen)
                x = int(self._u2x(val))
                p.drawLine(x, m, x, self.height() - m)
                p.setPen(lbl_col)
                p.drawText(x - lbl_w // 2, self.height() - 3, lbl)

        # Axes at 0
        p.setPen(axis_pen)
        y0 = int(self._v2y(0.0)); x0 = int(self._u2x(0.0))
        p.drawLine(m, y0, self.width() - m, y0)
        p.drawLine(x0, m, x0, self.height() - m)

        # Curve
        self._paint_curve(p)

        # Key dots
        for i, k in enumerate(self._keys):
            x = int(self._u2x(k["u"]))
            y = int(self._v2y(k["v"]))
            r = self._KEY_R
            fixed = i in self._U_LOCKED and i in self._V_LOCKED
            col = QtGui.QColor(110, 110, 110) if fixed else QtGui.QColor(220, 160, 40)
            p.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255) if i == self._sel
                                else col.darker(160), 2 if i == self._sel else 1))
            p.setBrush(col)
            p.drawEllipse(QtCore.QPoint(x, y), r, r)

        p.end()

    def _paint_curve(self, painter):
        if len(self._keys) < 2:
            return
        painter.setPen(QtGui.QPen(QtGui.QColor(80, 180, 255), 1.5))
        path  = QtGui.QPainterPath()
        first = True
        for i in range(len(self._keys) - 1):
            k0 = self._keys[i]; k1 = self._keys[i + 1]
            x0 = self._u2x(k0["u"]); y0 = self._v2y(k0["v"])
            x1 = self._u2x(k1["u"]); y1 = self._v2y(k1["v"])
            if first:
                path.moveTo(x0, y0)
                first = False
            if k0["tangent"] == "linear" and k1["tangent"] == "linear":
                path.lineTo(x1, y1)
            else:
                s0 = self._slope(i,     "out")
                s1 = self._slope(i + 1, "in")
                du = k1["u"] - k0["u"]
                path.cubicTo(
                    self._u2x(k0["u"] + du / 3.0), self._v2y(k0["v"] + s0 * du / 3.0),
                    self._u2x(k1["u"] - du / 3.0), self._v2y(k1["v"] - s1 * du / 3.0),
                    x1, y1)
        painter.drawPath(path)

    # ── Mouse ─────────────────────────────────────────────────────────────────

    def _key_at(self, pos):
        for i, k in enumerate(self._keys):
            dx = pos.x() - self._u2x(k["u"])
            dy = pos.y() - self._v2y(k["v"])
            if dx * dx + dy * dy <= (self._KEY_R + 3) ** 2:
                return i
        return None

    def mousePressEvent(self, event):
        if event.button() != QtCore.Qt.LeftButton:
            return
        prev  = self._sel
        idx   = self._key_at(event.pos())
        self._sel = idx
        if idx is not None:
            movable = idx not in self._U_LOCKED or idx not in self._V_LOCKED
            if movable:
                self._drag = idx
        self.update()
        if self._sel != prev:
            self.keys_changed.emit()

    def mouseMoveEvent(self, event):
        if self._drag is None:
            return
        k    = self._keys[self._drag]
        keys = self._keys
        if self._drag not in self._U_LOCKED:
            u  = self._x2u(event.pos().x())
            lo = keys[self._drag - 1]["u"] if self._drag > 0 else -1.0
            hi = keys[self._drag + 1]["u"] if self._drag < len(keys) - 1 else 1.0
            k["u"] = round(max(lo + 0.001, min(hi - 0.001, u)), 3)
        if self._drag not in self._V_LOCKED:
            v  = self._y2v(event.pos().y())
            k["v"] = round(max(-1.0, min(1.0, v)), 3)
        self.update()
        self.keys_changed.emit()

    def mouseReleaseEvent(self, event):
        self._drag = None


class _AddSoftBlendPairDialog(QtWidgets.QDialog):
    """Small dialog to pick two shapes and define a soft blend pair."""

    def __init__(self, shapes, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add Soft Blend Pair")
        self.setFixedWidth(380)
        lay = QtWidgets.QVBoxLayout(self)
        lay.setSpacing(8)

        row = QtWidgets.QHBoxLayout()
        row.setSpacing(6)
        self._cb_a = QtWidgets.QComboBox()
        self._cb_a.setEditable(True)
        self._cb_a.addItems(shapes)
        self._cb_b = QtWidgets.QComboBox()
        self._cb_b.setEditable(True)
        self._cb_b.addItems(shapes)
        row.addWidget(self._cb_a)
        row.addWidget(QtWidgets.QLabel("↔"))
        row.addWidget(self._cb_b)
        lay.addLayout(row)

        btns = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

    def get_pair(self):
        return self._cb_a.currentText().strip(), self._cb_b.currentText().strip()


def _normalize_connection_rows(raw_data):
    """Convert raw JSON connection data to the canonical row-dict format expected
    by build_and_connect_rig.  This is the single source of truth for that
    conversion — every code path that builds rows from JSON must go through here.

    Canonical keys (mirrors what RigConnectorDialog._collect_rows produces):
        shape, proxy, controller, attr, min, max, gate, skinning_ctrl
    """
    rows = []
    for rd in raw_data:
        if not isinstance(rd, dict):
            continue
        attr   = rd.get("attr", "ty")
        in_min = float(rd.get("min", rd.get("in_min", 0.0)))
        in_max = float(rd.get("max", rd.get("in_max", 1.0)))
        # Old format: attr=="custom" + custom_attr field
        custom_attr = rd.get("custom_attr", "")
        if attr == "custom" and custom_attr:
            attr = custom_attr
        # Old format: explicit direction field (− = negative)
        if rd.get("direction", "+") == "\u2212":
            in_max = -abs(in_max)
            if in_min != 0.0:
                in_min = -abs(in_min)
        rows.append({
            "shape":         rd.get("shape", ""),
            "proxy":         bool(rd.get("proxy", False)),
            "controller":    rd.get("controller", ""),
            "attr":          attr,
            "min":           in_min,
            "max":           in_max,
            "gate":          rd.get("gate", ""),
            "skinning_ctrl": bool(rd.get("skinning_ctrl", False)),
        })
    return rows


class RigConnectorDialog(QtWidgets.QDialog):
    """
    Maps FK rig controllers to blendShape targets.
    Phase 1: table UI, auto-fill, save/load mapping.
    Phase 2 (future): Build & Connect nodes.
    """

    _COL_NUM   = 0
    _COL_SHAPE = 1
    _COL_CTRL  = 2
    _COL_ATTR  = 3
    _COL_MIN = 4
    _COL_MAX = 5
    _COL_GATE  = 6
    _COL_STAT  = 7

    _ATTR_ITEMS = ["tx", "ty", "tz", "rx", "ry", "rz", "sx", "sy", "sz"]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Rig Connector")
        self.setMinimumSize(900, 600)
        self.setWindowFlags(self.windowFlags() | QtCore.Qt.Window)
        self._shapes = []   # list of shape name strings currently in table
        self._min_h_locked = False
        self._min_w_locked = False
        self._undo_stack = []      # list of state dicts (rows, pairs, curve)
        self._redo_stack = []
        self._undo_restoring = False  # suppress pushes during restore
        self._build_ui()
        self._populate_from_default_json()
        self._populate_mapping_from_parent()
        if self._table.rowCount() == 0:
            self._add_placeholder_row()

    def showEvent(self, event):
        super().showEvent(event)
        if not self._min_h_locked:
            self._min_h_locked = True
            QtCore.QTimer.singleShot(50, self._lock_min_height)
        QtCore.QTimer.singleShot(0, self._update_scale_position)
        if not self._min_w_locked:
            self._min_w_locked = True
            QtCore.QTimer.singleShot(50, self._lock_min_width)

    def keyPressEvent(self, event):
        """Ctrl+Z = undo, Ctrl+Y = redo. Blocked from reaching Maya."""
        if event.modifiers() == QtCore.Qt.ControlModifier:
            if event.key() == QtCore.Qt.Key_Z:
                self._undo()
                event.accept()
                return
            if event.key() == QtCore.Qt.Key_Y:
                self._redo()
                event.accept()
                return
        super().keyPressEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_scale_position()

    def _lock_min_height(self):
        h = self.height()
        if h > 0:
            self.setMinimumHeight(h)

    def _lock_min_width(self):
        w = self.width()
        if w > 0:
            self.setMinimumWidth(w)

    def _update_scale_position(self):
        """Align search field to Shape column right edge; center Scale Factor over InMin+InMax."""
        hh   = self._table.horizontalHeader()
        fw   = self._table.frameWidth()
        sb   = self._table.verticalScrollBar()
        sb_w = sb.width() if sb.isVisible() else 0
        sp   = self._search_row.spacing()   # 4 px between every item
        _Fixed = QtWidgets.QSizePolicy.Fixed

        # ── Filter left spacer: flush left (label+field start at left edge)
        self._filter_left_spacer.changeSize(0, 0, _Fixed, _Fixed)
        left_spacer_w = 0

        # ── Filter field: width ends at Shape column right edge, flush against label
        lbl_w       = self._lbl_filter.sizeHint().width()
        shape_right = fw + hh.sectionViewportPosition(self._COL_SHAPE) + hh.sectionSize(self._COL_SHAPE)
        le_left     = sp + lbl_w + sp   # left_spacer_w=0, so lbl_w + 2*sp
        self._le_search.setFixedWidth(max(40, shape_right - le_left))
        le_search_w = self._le_search.width()

        # ── Scale Factor block [lbl_scale + (sp+4px+sp) + le_scale]: center over InMin+InMax
        lbl_scale_w = self._lbl_scale.sizeHint().width()
        le_scale_w  = self._le_scale.width()         # 44 px
        inner_gap   = sp + 4 + sp                    # gap between lbl_scale and le_scale
        block_w     = lbl_scale_w + inner_gap + le_scale_w

        inmin_left  = fw + hh.sectionViewportPosition(self._COL_MIN)
        inmax_right = fw + hh.sectionViewportPosition(self._COL_MAX) + hh.sectionSize(self._COL_MAX)
        combined_w  = inmax_right - inmin_left
        block_left_target = inmin_left + (combined_w - block_w) // 2 - 10

        # x(lbl_scale) = left_spacer_w + sp + lbl_w + sp + le_search_w + sp
        #                + scale_left_spacer_w + sp
        #              = left_spacer_w + lbl_w + le_search_w + scale_left_w + 4*sp
        x_if_zero     = left_spacer_w + lbl_w + le_search_w + 4 * sp
        scale_left_w  = max(0, block_left_target - x_if_zero)
        self._scale_left_spacer.changeSize(scale_left_w, 0, _Fixed, _Fixed)

        # ── Scale right spacer: fills remaining space so save buttons sit at right edge
        # Row items (10 items → 9 gaps of sp):
        # [left_spacer][lbl_filter][le_search][scale_left][lbl_scale][4px][le_scale][scale_right][save_map][save_plus]
        save_map_w  = self._btn_save_map.width()
        save_plus_w = self._btn_save_plus.width()
        row_w       = hh.width() + 2 * fw + sb_w
        fixed_sum   = (left_spacer_w + lbl_w + le_search_w + scale_left_w
                       + lbl_scale_w + 4 + le_scale_w + save_map_w + save_plus_w)
        scale_right_w = max(0, row_w - fixed_sum - 9 * sp)
        self._scale_right_spacer.changeSize(scale_right_w, 0, _Fixed, _Fixed)

        self._search_row.invalidate()

    # ── UI build ──────────────────────────────────────────────────────────────

    def _build_ui(self):
        import maya.cmds as _cmds
        _idir = _cmds.internalVar(userAppDir=True) + "prefs/icons"

        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(6)

        # ── Files Mapping ─────────────────────────────────────────────────────
        grp_files = QtWidgets.QGroupBox("Files Mapping")
        grp_files.setStyleSheet("QGroupBox { font-size: 11px; }")
        _fm_ss = (
            "QToolButton { background-color: transparent; border: none;"
            " border-radius: 3px; padding: 2px; }"
            "QToolButton:hover   { background-color: rgba(255,255,255,30); }"
            "QToolButton:pressed { background-color: rgba(0,0,0,40); }"
        )
        files_row = QtWidgets.QHBoxLayout(grp_files)
        files_row.setContentsMargins(8, 4, 8, 4)
        files_row.setSpacing(0)

        _FS = 6  # uniform spacing between label / field / button

        # Mapping json
        files_row.addWidget(QtWidgets.QLabel("Mapping .json"))
        files_row.addSpacing(_FS)
        _fm_ss_nopad = _fm_ss.replace("padding: 2px;", "padding: 0px;")
        btn_save_map = QtWidgets.QToolButton()
        btn_save_map.setAutoRaise(True)
        btn_save_map.setStyleSheet(_fm_ss_nopad)
        _pix_save_map = QtGui.QPixmap(f"{_idir}/save.png")
        if not _pix_save_map.isNull():
            btn_save_map.setIcon(QtGui.QIcon(_pix_save_map))
            btn_save_map.setIconSize(QtCore.QSize(38, 38))
        btn_save_map.setFixedSize(36, 36)
        btn_save_map.setToolTip(
            "Save — overwrite the current file directly.\n"
            "Opens a browser only if no file is loaded yet.")
        btn_save_map.clicked.connect(self._save_mapping)
        btn_save_plus = QtWidgets.QToolButton()
        btn_save_plus.setAutoRaise(True)
        btn_save_plus.setStyleSheet(_fm_ss_nopad)
        _pix_save_plus = QtGui.QPixmap(f"{_idir}/save_plus.png")
        if not _pix_save_plus.isNull():
            btn_save_plus.setIcon(QtGui.QIcon(_pix_save_plus))
            btn_save_plus.setIconSize(QtCore.QSize(38, 38))
        btn_save_plus.setFixedSize(36, 36)
        btn_save_plus.setToolTip(
            "Save As — increment version.\n"
            "Proposes a new filename with an incremented version number\n"
            "(e.g. mapping_v01.json ->mapping_v02.json).")
        btn_save_plus.clicked.connect(self._save_mapping_increment)
        btn_load_map = QtWidgets.QToolButton()
        btn_load_map.setAutoRaise(True)
        btn_load_map.setStyleSheet(_fm_ss)
        _pix_load_map = QtGui.QPixmap(f"{_idir}/path.png")
        if not _pix_load_map.isNull():
            btn_load_map.setIcon(QtGui.QIcon(_pix_load_map))
            btn_load_map.setIconSize(QtCore.QSize(34, 34))
        btn_load_map.setFixedSize(36, 36)
        btn_load_map.setToolTip("Load a previously saved table mapping from a JSON file")
        btn_load_map.clicked.connect(self._load_mapping)
        files_row.addWidget(btn_load_map)
        files_row.addSpacing(_FS)
        self._le_mapping_path = QtWidgets.QLineEdit()
        self._le_mapping_path.setReadOnly(True)
        self._le_mapping_path.setMinimumWidth(260)
        self._le_mapping_path.setFixedHeight(23)
        self._le_mapping_path.setPlaceholderText("C:/path/to/rig_mapping.json")
        files_row.addWidget(self._le_mapping_path)
        files_row.addSpacing(_FS)
        btn_json_autofill = QtWidgets.QToolButton()
        btn_json_autofill.setAutoRaise(True)
        btn_json_autofill.setStyleSheet(_fm_ss)
        _pix_json_af = QtGui.QPixmap(f"{_idir}/json_autofill.png")
        if not _pix_json_af.isNull():
            btn_json_autofill.setIcon(QtGui.QIcon(_pix_json_af))
            btn_json_autofill.setIconSize(QtCore.QSize(34, 34))
        btn_json_autofill.setFixedSize(36, 36)
        btn_json_autofill.setToolTip(
            "Auto Fill from JSON — reload the table from the currently loaded mapping file.")
        btn_json_autofill.clicked.connect(self._autofill_from_json)
        files_row.addWidget(btn_json_autofill)

        files_row.addStretch(1)

        # BS Node
        files_row.addWidget(QtWidgets.QLabel("BlendShape Node"))
        files_row.addSpacing(_FS)
        btn_get_bs = QtWidgets.QToolButton()
        btn_get_bs.setAutoRaise(True)
        btn_get_bs.setStyleSheet(_fm_ss)
        _pix_get_bs = QtGui.QPixmap(f"{_idir}/get_bsnode.png")
        if not _pix_get_bs.isNull():
            btn_get_bs.setIcon(QtGui.QIcon(_pix_get_bs))
            btn_get_bs.setIconSize(QtCore.QSize(34, 34))
        btn_get_bs.setFixedSize(36, 36)
        btn_get_bs.setToolTip("Get the blendShape node from the current mesh selection or selected blendshape node")
        btn_get_bs.clicked.connect(self._get_bs_node)
        files_row.addWidget(btn_get_bs)
        files_row.addSpacing(_FS)
        self._le_bs_node = QtWidgets.QLineEdit()
        self._le_bs_node.setReadOnly(True)
        self._le_bs_node.setFixedWidth(160)
        self._le_bs_node.setFixedHeight(23)
        self._le_bs_node.setPlaceholderText("blendShape node")
        files_row.addWidget(self._le_bs_node)
        files_row.addSpacing(_FS)
        btn_fill_bs = QtWidgets.QToolButton()
        btn_fill_bs.setAutoRaise(True)
        btn_fill_bs.setStyleSheet(_fm_ss)
        _pix_bs_af = QtGui.QPixmap(f"{_idir}/bs_auto-fill.png")
        if _pix_bs_af.isNull():
            _pix_bs_af = QtGui.QPixmap(f"{_idir}/json_autofill.png")
        btn_fill_bs.setIcon(QtGui.QIcon(_pix_bs_af))
        btn_fill_bs.setIconSize(QtCore.QSize(34, 34))
        btn_fill_bs.setFixedSize(36, 36)
        btn_fill_bs.setToolTip(
            "Auto Fill from BS Node — populate the table with all targets\n"
            "from the current BS node (auto-detects if none is set).")
        btn_fill_bs.clicked.connect(self._fill_from_bs_node)
        files_row.addWidget(btn_fill_bs)

        # backing store for _load_json_file / _populate_from_default_json (not displayed)
        self._le_json = QtWidgets.QLineEdit()
        self._le_json.setReadOnly(True)

        outer.addWidget(grp_files)

        # ── Search + Scale Ranges ─────────────────────────────────────────────
        self._search_row = QtWidgets.QHBoxLayout()
        search_row = self._search_row
        search_row.setSpacing(4)
        self._filter_left_spacer = QtWidgets.QSpacerItem(
            0, 0, QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)
        search_row.addItem(self._filter_left_spacer)
        self._lbl_filter = QtWidgets.QLabel("Filter")
        search_row.addWidget(self._lbl_filter)
        self._le_search = QtWidgets.QLineEdit()
        self._le_search.setPlaceholderText("Search shapes…")
        self._le_search.setClearButtonEnabled(True)
        self._le_search.textChanged.connect(self._filter_rows)
        search_row.addWidget(self._le_search)

        self._scale_left_spacer = QtWidgets.QSpacerItem(
            12, 0, QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)
        search_row.addItem(self._scale_left_spacer)
        self._lbl_scale = QtWidgets.QLabel("Scale Factor")
        search_row.addWidget(self._lbl_scale)
        search_row.addSpacing(4)
        self._le_scale = QtWidgets.QLineEdit("2.00")
        self._le_scale.setAlignment(QtCore.Qt.AlignCenter)
        self._le_scale.setFixedWidth(44)
        self._le_scale.setFixedHeight(22)
        self._le_scale.setToolTip(
            "Multiplier applied when right-clicking In Min / In Max column headers.\n"
            "Examples:\n"
            "  2.0  →  double\n"
            "  0.5  →  halve\n"
            "  -1.0 →  flip sign\n"
            "  1.5  →  scale up by 50%"
        )
        self._le_scale.editingFinished.connect(self._format_scale_factor)
        search_row.addWidget(self._le_scale)
        self._scale_right_spacer = QtWidgets.QSpacerItem(
            0, 0, QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)
        search_row.addItem(self._scale_right_spacer)
        self._btn_save_map  = btn_save_map
        self._btn_save_plus = btn_save_plus
        search_row.addWidget(btn_save_plus)
        search_row.addWidget(btn_save_map)

        # search_row added below, after sidebar width is known

        # ── Table ─────────────────────────────────────────────────────────────
        self._table = QtWidgets.QTableWidget(0, 8)
        self._table.setHorizontalHeaderLabels(
            ["#", "Target", "Controller", "Attr",
             "Min", "Max", "Combo Driver", "Status"])
        self._table.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._show_row_context_menu)
        self._table.cellDoubleClicked.connect(
            lambda r, c: self._push_undo() if c == self._COL_SHAPE else None)
        self._table.verticalHeader().setVisible(False)
        self._table.setAlternatingRowColors(True)
        self._table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self._table.verticalHeader().setDefaultSectionSize(24)

        hh = self._table.horizontalHeader()
        hh.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        hh.customContextMenuRequested.connect(self._show_header_context_menu)
        hh.setSectionResizeMode(self._COL_NUM,   QtWidgets.QHeaderView.Fixed)
        hh.setSectionResizeMode(self._COL_SHAPE, QtWidgets.QHeaderView.Stretch)
        hh.setSectionResizeMode(self._COL_CTRL,  QtWidgets.QHeaderView.Fixed)
        hh.setSectionResizeMode(self._COL_ATTR,  QtWidgets.QHeaderView.Fixed)
        hh.setSectionResizeMode(self._COL_MIN, QtWidgets.QHeaderView.Fixed)
        hh.setSectionResizeMode(self._COL_MAX, QtWidgets.QHeaderView.Fixed)
        hh.setSectionResizeMode(self._COL_GATE,  QtWidgets.QHeaderView.Fixed)
        hh.setSectionResizeMode(self._COL_STAT,  QtWidgets.QHeaderView.Fixed)

        self._table.setColumnWidth(self._COL_NUM,   28)
        self._table.setColumnWidth(self._COL_CTRL,  140)
        self._table.setColumnWidth(self._COL_ATTR,  90)
        self._table.setColumnWidth(self._COL_MIN, 65)
        self._table.setColumnWidth(self._COL_MAX, 65)
        self._table.setColumnWidth(self._COL_GATE,  100)
        self._table.setColumnWidth(self._COL_STAT,  50)

        # ── Table + sidebar ────────────────────────────────────────────────────
        _RC_BW = 28
        _RC_SP = 2
        _RC_W  = _RC_BW * 2 + _RC_SP   # 58 px — uniform column width
        _rc_icon_ss = (
            "QToolButton { background-color: transparent; border: none;"
            " border-radius: 3px; padding: 2px; }"
            "QToolButton:hover   { background-color: rgba(255,255,255,30); }"
            "QToolButton:pressed { background-color: rgba(0,0,0,40); }"
        )

        def _rc_side_btn(label, tooltip, callback):
            b = QtWidgets.QPushButton(label)
            b.setFixedSize(40, 40)
            b.setFocusPolicy(QtCore.Qt.NoFocus)
            b.setStyleSheet("font-size: 14px; font-weight: bold;")
            b.setToolTip(tooltip)
            b.clicked.connect(callback)
            return b

        def _rc_icon_btn(icon_path, tooltip, callback, icon_size=34):
            b = QtWidgets.QToolButton()
            b.setFixedSize(40, 40)
            b.setAutoRaise(True)
            b.setStyleSheet(_rc_icon_ss)
            b.setToolTip(tooltip)
            px = QtGui.QPixmap(icon_path)
            if not px.isNull():
                b.setIcon(QtGui.QIcon(px))
                b.setIconSize(QtCore.QSize(icon_size, icon_size))
            b.clicked.connect(callback)
            return b

        btn_add = _rc_side_btn("+",
            "Add one row per target selected in the Shape Editor.\n"
            "Falls back to one empty row if nothing is selected.",
            self._add_row)
        btn_rm  = _rc_side_btn("−", "Delete the selected rows from the table",
                               self._remove_rows)
        btn_up  = _rc_side_btn("↑", "Move selected rows up by one position",
                               self._move_up)
        btn_dn  = _rc_side_btn("↓", "Move selected rows down by one position",
                               self._move_down)

        btn_opp = _rc_side_btn(
            "L→R",
            "Duplicate the selected row with opposite names:\n"
            "swaps L/R (and other symmetric tokens) in the Shape and Controller fields.\n"
            "The new row is inserted directly below the source row.",
            self._create_opposite_row)
        btn_opp.setStyleSheet("font-size: 11px; font-weight: bold;")

        btn_connect_sel = _rc_icon_btn(
            f"{_idir}/link_locs.png",
            "Connect Selected — Build the rig network for the selected rows only.\n"
            "Useful for testing a single setup without rebuilding the full rig.",
            self._connect_selected_rows, icon_size=26)

        btn_disc_sel = _rc_icon_btn(
            f"{_idir}/unlink_locs.png",
            "Disconnect Selected — Remove the rig network for the selected shapes\n"
            "(utility nodes deleted, blendShape weight disconnected).",
            self._disconnect_selected, icon_size=26)
        btn_disc_all = _rc_icon_btn(
            f"{_idir}/disconnect_all.png",
            "Disconnect All — Remove the rig network for all shapes in the table\n"
            "(utility nodes deleted, blendShape weights disconnected).",
            self._disconnect_all)

        _build_ss = (
            "QToolButton { background-color: transparent; border: none;"
            " border-radius: 3px; padding: 2px; }"
            "QToolButton:hover   { background-color: rgba(255,255,255,30); }"
            "QToolButton:pressed { background-color: rgba(0,0,0,40); }"
        )
        self._btn_build = QtWidgets.QToolButton()
        self._btn_build.setToolButtonStyle(QtCore.Qt.ToolButtonIconOnly)
        self._btn_build.setToolTip(
            "Build the rig network for all valid rows:\n"
            "  • Creates offset / normalize / clamp utility nodes per shape\n"
            "  • Applies transform limits and locks unused axes on each controller\n"
            "  • Optionally generates scale shapes (if Auto Scale Shapes is checked)")
        self._btn_build.setStyleSheet(_build_ss)
        _pix_connect = QtGui.QPixmap(f"{_idir}/connect_rig.png")
        if not _pix_connect.isNull():
            self._btn_build.setIcon(QtGui.QIcon(_pix_connect))
            self._btn_build.setIconSize(QtCore.QSize(30, 30))
        self._btn_build.setFixedSize(40, 40)
        self._btn_build.clicked.connect(self._on_build_connect)

        _tbl_row = QtWidgets.QHBoxLayout()
        _tbl_row.setSpacing(_RC_SP)
        _tbl_row.setContentsMargins(0, 0, 0, 0)
        _tbl_row.addWidget(self._table, 1)
        _side_vlay = QtWidgets.QVBoxLayout()
        _side_vlay.setSpacing(_RC_SP)
        _side_vlay.setContentsMargins(0, 0, 0, 0)
        _side_vlay.addStretch(1)
        for _b in (btn_connect_sel, btn_disc_sel, btn_disc_all):
            _side_vlay.addWidget(_b)
        _side_vlay.addSpacing(8)
        _side_vlay.addWidget(self._btn_build)
        _side_vlay.addStretch(1)
        _tbl_row.addLayout(_side_vlay)

        # search_row constrained to table width: sidebar (40px) + spacing on the right
        _sidebar_w = 40 + _RC_SP  # btn fixed size + tbl_row spacing
        _search_outer = QtWidgets.QHBoxLayout()
        _search_outer.setContentsMargins(0, 0, 0, 0)
        _search_outer.setSpacing(0)
        _search_outer.addLayout(search_row, 1)
        _search_outer.addSpacing(_sidebar_w)
        outer.addLayout(_search_outer)

        outer.addLayout(_tbl_row, 1)

        grp_row_opts = QtWidgets.QGroupBox("Row Options")
        grp_row_opts.setStyleSheet("QGroupBox { font-size: 11px; }")
        side_row = QtWidgets.QHBoxLayout(grp_row_opts)
        side_row.setSpacing(_RC_SP)
        side_row.setContentsMargins(8, 4, 8, 4)
        for _b in (btn_add, btn_rm, btn_up, btn_dn, btn_opp):
            side_row.addWidget(_b)
        side_row.addStretch(1)

        # ── Auto-stagger ──────────────────────────────────────────────────────
        grp_stagger = QtWidgets.QGroupBox("Auto-stagger")
        grp_stagger.setStyleSheet("QGroupBox { font-size: 11px; }")
        _stagger_vbox = QtWidgets.QVBoxLayout(grp_stagger)
        _stagger_vbox.setContentsMargins(8, 4, 8, 4)
        _stagger_vbox.setSpacing(2)

        stagger_row = QtWidgets.QHBoxLayout()
        stagger_row.setContentsMargins(0, 0, 0, 0)
        stagger_row.setSpacing(4)
        _stagger_vbox.addLayout(stagger_row)

        self._le_stagger_ctrl = QtWidgets.QLineEdit()
        self._le_stagger_ctrl.setPlaceholderText("controller")
        self._le_stagger_ctrl.setFixedWidth(140)
        stagger_row.addWidget(self._le_stagger_ctrl)

        stagger_row.addStretch(1)

        self._combo_stagger_axis = QtWidgets.QComboBox()
        self._combo_stagger_axis.setEditable(True)
        self._combo_stagger_axis.addItems(self._ATTR_ITEMS)
        self._combo_stagger_axis.setCurrentIndex(-1)
        self._combo_stagger_axis.lineEdit().setPlaceholderText("attribute")
        self._combo_stagger_axis.setFixedWidth(75)
        stagger_row.addWidget(self._combo_stagger_axis)

        stagger_row.addStretch(1)

        self._stagger_sign = 1.0
        self.btn_stagger_sign = QtWidgets.QToolButton()
        self.btn_stagger_sign.setFixedSize(26, 34)
        self.btn_stagger_sign.setToolButtonStyle(QtCore.Qt.ToolButtonTextOnly)
        self.btn_stagger_sign.setText("+")
        self.btn_stagger_sign.setEnabled(False)
        self.btn_stagger_sign.setAutoRaise(True)
        self.btn_stagger_sign.setStyleSheet("""
            QToolButton {
                background-color: transparent;
                border: none;
                border-radius: 3px;
                font-size: 14px;
                font-weight: bold;
            }
            QToolButton:hover   { background-color: rgba(255,255,255,30); }
            QToolButton:pressed { background-color: rgba(0,0,0,40); }
        """)
        self.btn_stagger_sign.setToolTip(
            "Symmetric only — controls which side is positive.\n"
            "+: left half ->'+', right half ->'\u2212'.\n"
            "\u2212: left half ->'\u2212', right half ->'+'.")
        def _on_stagger_sign_clicked():
            self._stagger_sign *= -1.0
            self.btn_stagger_sign.setText("\u2212" if self._stagger_sign < 0 else "+")
        self.btn_stagger_sign.clicked.connect(_on_stagger_sign_clicked)
        stagger_row.addWidget(self.btn_stagger_sign)

        self._combo_stagger_mode = QtWidgets.QComboBox()
        self._combo_stagger_mode.addItems(["Linear", "Symmetric", "Mirror"])
        self._combo_stagger_mode.setFixedWidth(90)
        self._combo_stagger_mode.setToolTip(
            "Linear   : sequential slots [0→1] for each shape.\n"
            "Symmetric: outer shapes activate last, centre shape first.\n"
            "           Left half and right half get opposite directions.\n"
            "           Ideal for brows and cheekbones.\n"
            "Mirror   : centre shape activates last, outer shapes first.\n"
            "           Same direction for all. Ideal for zip lips.")
        self._combo_stagger_mode.currentTextChanged.connect(self._on_stagger_mode_changed)
        stagger_row.addWidget(self._combo_stagger_mode)

        stagger_row.addStretch(1)

        stagger_row.addWidget(QtWidgets.QLabel("In Max"))
        self._sb_stagger_inmax = QtWidgets.QLineEdit("0.000")
        self._sb_stagger_inmax.setFixedWidth(70)
        self._sb_stagger_inmax.setAlignment(QtCore.Qt.AlignCenter)
        _inmax_v = QtGui.QDoubleValidator(0.0, 9999.0, 3, self._sb_stagger_inmax)
        _inmax_v.setLocale(QtCore.QLocale(QtCore.QLocale.English))
        self._sb_stagger_inmax.setValidator(_inmax_v)
        stagger_row.addWidget(self._sb_stagger_inmax)

        stagger_row.addStretch(1)

        self._combo_stagger_curve = QtWidgets.QComboBox()
        self._combo_stagger_curve.addItems(["Uniform", "Ease In", "Ease Out", "Smooth"])
        self._combo_stagger_curve.setFixedWidth(82)
        self._combo_stagger_curve.setToolTip(
            "Interpolation curve applied to the slot positions.\n"
            "Uniform  : uniform spacing (default).\n"
            "Ease In  : slots cluster at the start, spread toward In Max  (t²).\n"
            "Ease Out : slots spread quickly then compress toward In Max  (1−(1−t)²).\n"
            "Smooth   : S-curve, slow at both ends, fast in the middle  (3t²−2t³).")
        stagger_row.addWidget(self._combo_stagger_curve)

        stagger_row.addStretch(1)

        stagger_row.addWidget(QtWidgets.QLabel("Blend"))
        self._sb_stagger_falloff = QtWidgets.QLineEdit("0.000")
        self._sb_stagger_falloff.setFixedWidth(70)
        self._sb_stagger_falloff.setAlignment(QtCore.Qt.AlignCenter)
        _falloff_v = QtGui.QDoubleValidator(0.0, 9999.0, 3, self._sb_stagger_falloff)
        _falloff_v.setLocale(QtCore.QLocale(QtCore.QLocale.English))
        self._sb_stagger_falloff.setValidator(_falloff_v)
        self._sb_stagger_falloff.setToolTip(
            "Overlap amount added on each side of a shape's activation slot.\n"
            "Example: slot [0.2, 0.4] with blend 0.01 ->In Min=0.19, In Max=0.41.\n"
            "First shape never goes below 0; last shape never exceeds In Max.")
        stagger_row.addWidget(self._sb_stagger_falloff)

        stagger_row.addStretch(1)

        self._chk_stagger_proxies = QtWidgets.QCheckBox("As Proxies")
        self._chk_stagger_proxies.setChecked(False)
        self._chk_stagger_proxies.setToolTip(
            "ON: creates a new proxy row (sub-driver) for each selected row,\n"
            "    driven by the stagger controller with the computed In Min / In Max.\n"
            "OFF: applies the controller, axis, In Min and In Max directly\n"
            "    to the selected rows, without adding proxy rows.")
        stagger_row.addWidget(self._chk_stagger_proxies)

        stagger_row.addStretch(1)

        _lbl_create_stagger = QtWidgets.QLabel("Create Stagger")
        stagger_row.addWidget(_lbl_create_stagger)
        stagger_row.setAlignment(_lbl_create_stagger, QtCore.Qt.AlignVCenter)
        stagger_row.setAlignment(self._chk_stagger_proxies, QtCore.Qt.AlignVCenter)
        btn_stagger = QtWidgets.QToolButton()
        btn_stagger.setAutoRaise(True)
        btn_stagger.setStyleSheet("""
            QToolButton { background-color: transparent; border: none; border-radius: 3px; padding: 2px; }
            QToolButton:hover   { background-color: rgba(255,255,255,30); }
            QToolButton:pressed { background-color: rgba(0,0,0,40); }
        """)
        _pix_stagger = QtGui.QPixmap(f"{_idir}/stagger.png")
        if not _pix_stagger.isNull():
            btn_stagger.setIcon(QtGui.QIcon(_pix_stagger))
            btn_stagger.setIconSize(QtCore.QSize(34, 34))
        btn_stagger.setFixedSize(36, 36)
        btn_stagger.setToolTip(
            "Apply stagger In Min / In Max to the selected rows.\n"
            "As Proxies ON: creates a proxy sub-row per shape driven by the stagger controller.\n"
            "As Proxies OFF: writes the values directly onto the selected rows.\n"
            "Each shape gets a sequential activation slot within [0, In Max].\n"
            "Blend extends each slot by ±blend (clamped to bounds).\n"
            "Mirror/Symmetric: outer shapes share slot 0, centre shape activates last.")
        btn_stagger.clicked.connect(self._apply_stagger)
        stagger_row.addWidget(btn_stagger)

        # ── Center row (Mirror / Symmetric only) ──────────────────────────────
        self._center_row_widget = QtWidgets.QWidget()
        _cr = QtWidgets.QHBoxLayout(self._center_row_widget)
        _cr.setContentsMargins(0, 0, 0, 0)
        _cr.setSpacing(6)

        _cr.addStretch(1)
        _cr.addWidget(QtWidgets.QLabel("Center"))

        self._stagger_center_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self._stagger_center_slider.setRange(0, 100)
        self._stagger_center_slider.setValue(50)
        self._stagger_center_slider.setFixedWidth(120)
        self._stagger_center_slider.setToolTip(
            "Shift the peak of the stagger distribution.\n"
            "0.5 = middle row (default). 0.8 = peak at 80% of the row range.\n"
            "Applies to Mirror and Symmetric modes only.")
        _cr.addWidget(self._stagger_center_slider)

        self._stagger_center_le = QtWidgets.QLineEdit("0.50")
        self._stagger_center_le.setFixedWidth(48)
        self._stagger_center_le.setAlignment(QtCore.Qt.AlignCenter)
        _cv = QtGui.QDoubleValidator(0.0, 1.0, 2, self._stagger_center_le)
        _cv.setLocale(QtCore.QLocale(QtCore.QLocale.English))
        self._stagger_center_le.setValidator(_cv)
        self._stagger_center_le.setToolTip(
            "Center of the stagger distribution (0.0 – 1.0).\n"
            "0.5 = middle row. 0.8 = peak biased toward the last rows.")
        _cr.addWidget(self._stagger_center_le)
        _cr.addStretch(1)

        # Sync slider ↔ line edit
        def _center_slider_changed(val):
            self._stagger_center_le.setText(f"{val / 100:.2f}")
        def _center_le_changed():
            try:
                v = max(0.0, min(1.0, float(self._stagger_center_le.text())))
                self._stagger_center_slider.blockSignals(True)
                self._stagger_center_slider.setValue(int(round(v * 100)))
                self._stagger_center_slider.blockSignals(False)
            except ValueError:
                pass
        self._stagger_center_slider.valueChanged.connect(_center_slider_changed)
        self._stagger_center_le.editingFinished.connect(_center_le_changed)

        self._center_row_widget.setEnabled(False)  # disabled until Mirror/Symmetric selected
        _stagger_vbox.addWidget(self._center_row_widget)

        opts_row = QtWidgets.QHBoxLayout()
        opts_row.setSpacing(_RC_SP)
        opts_row.setContentsMargins(0, 0, 0, 0)
        opts_row.addWidget(grp_row_opts)
        opts_row.addWidget(grp_stagger)
        outer.addLayout(opts_row)

        # ── Soft Blend Pairs (collapsible, closed by default) ─────────────────
        _HDR_STYLE = (
            "QPushButton { background-color: rgba(255,255,255,28); border: none;"
            " border-radius: 2px; font-weight: bold; text-align: left;"
            " padding-left: 6px; }"
            "QPushButton:hover { background-color: rgba(255,255,255,38); }"
        )
        sb_header = QtWidgets.QPushButton()
        sb_header.setStyleSheet(_HDR_STYLE)
        sb_header.setSizePolicy(QtWidgets.QSizePolicy.Expanding,
                                QtWidgets.QSizePolicy.Fixed)
        sb_header.setFixedHeight(22)

        sb_body = QtWidgets.QWidget()
        sb_body.setVisible(False)           # closed by default
        lay_sb = QtWidgets.QVBoxLayout(sb_body)
        lay_sb.setContentsMargins(4, 4, 4, 4)
        lay_sb.setSpacing(3)

        _sb_open = [False]

        def _toggle_sb():
            _sb_open[0] = not _sb_open[0]
            if _sb_open[0]:
                self.setMinimumHeight(0)
            else:
                QtCore.QTimer.singleShot(50, lambda: self.setMinimumHeight(self.height()))
            sb_body.setVisible(_sb_open[0])
            sb_header.setText(("▼  Soft Blend Pairs") if _sb_open[0]
                               else ("▶  Soft Blend Pairs"))

        sb_header.setText("▶  Soft Blend Pairs")
        sb_header.clicked.connect(_toggle_sb)

        outer.addWidget(sb_header)
        outer.addWidget(sb_body)

        # ── Pairs table (full width) ──────────────────────────────────────────
        self._tbl_pairs = QtWidgets.QTableWidget(0, 3)
        self._tbl_pairs.setHorizontalHeaderLabels(["Shape A", "Shape B", ""])
        self._tbl_pairs.verticalHeader().setVisible(False)
        self._tbl_pairs.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self._tbl_pairs.setAlternatingRowColors(True)
        self._tbl_pairs.verticalHeader().setDefaultSectionSize(22)
        hh_sb = self._tbl_pairs.horizontalHeader()
        hh_sb.setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        hh_sb.setSectionResizeMode(1, QtWidgets.QHeaderView.Stretch)
        hh_sb.setSectionResizeMode(2, QtWidgets.QHeaderView.Fixed)
        self._tbl_pairs.setColumnWidth(2, 24)

        pair_btn_row = QtWidgets.QHBoxLayout()
        pair_btn_row.setSpacing(4)
        btn_add_pair = QtWidgets.QPushButton("+ Add Pair")
        btn_add_pair.setFixedWidth(80)
        btn_add_pair.setToolTip(
            "Define a Soft Blend pair: two opposite shapes (e.g. mouth_lft / mouth_rgt)\n"
            "will be driven by an animCurveUU with smooth tangents at neutral\n"
            "instead of the standard linear norm/clamp network.")
        btn_add_pair.clicked.connect(self._add_soft_blend_pair)
        pair_btn_row.addWidget(btn_add_pair)
        pair_btn_row.addStretch()

        _left_col = QtWidgets.QVBoxLayout()
        _left_col.setSpacing(3)
        _left_col.addWidget(self._tbl_pairs)
        _left_col.addLayout(pair_btn_row)

        # ── Blend Graph (right panel) ──────────────────────────────────────────
        self._graph = _SoftBlendGraphWidget()
        self._graph.keys_changed.connect(self._on_graph_keys_changed)

        # Graph controls: Reset Curve | U | V | Tangent
        graph_ctrl_row = QtWidgets.QHBoxLayout()
        graph_ctrl_row.setSpacing(4)

        btn_reset_curve = QtWidgets.QPushButton("Reset Curve")
        btn_reset_curve.setFixedWidth(80)
        btn_reset_curve.setToolTip("Reset the soft blend curve to the default 5-key preset.")
        btn_reset_curve.clicked.connect(self._reset_soft_blend_curve)
        graph_ctrl_row.addWidget(btn_reset_curve)

        graph_ctrl_row.addStretch()
        graph_ctrl_row.addSpacing(8)
        graph_ctrl_row.addWidget(QtWidgets.QLabel("U:"))
        self._sb_key_u = QtWidgets.QDoubleSpinBox()
        self._sb_key_u.setRange(-1.0, 1.0)
        self._sb_key_u.setDecimals(3)
        self._sb_key_u.setSingleStep(0.01)
        self._sb_key_u.setFixedWidth(65)
        self._sb_key_u.setLocale(QtCore.QLocale(QtCore.QLocale.English))
        self._sb_key_u.valueChanged.connect(self._on_key_u_changed)
        graph_ctrl_row.addWidget(self._sb_key_u)

        graph_ctrl_row.addWidget(QtWidgets.QLabel("V:"))
        self._sb_key_v = QtWidgets.QDoubleSpinBox()
        self._sb_key_v.setRange(-1.0, 1.0)
        self._sb_key_v.setDecimals(3)
        self._sb_key_v.setSingleStep(0.01)
        self._sb_key_v.setFixedWidth(65)
        self._sb_key_v.setLocale(QtCore.QLocale(QtCore.QLocale.English))
        self._sb_key_v.valueChanged.connect(self._on_key_v_changed)
        graph_ctrl_row.addWidget(self._sb_key_v)

        graph_ctrl_row.addWidget(QtWidgets.QLabel("Tangent:"))
        self._combo_key_tang = QtWidgets.QComboBox()
        self._combo_key_tang.addItems(["smooth", "auto", "linear"])
        self._combo_key_tang.setFixedWidth(76)
        self._combo_key_tang.currentTextChanged.connect(self._on_key_tangent_changed)
        graph_ctrl_row.addWidget(self._combo_key_tang)

        _right_col = QtWidgets.QVBoxLayout()
        _right_col.setSpacing(3)
        _right_col.addWidget(self._graph)
        _right_col.addLayout(graph_ctrl_row)

        _sb_split = QtWidgets.QHBoxLayout()
        _sb_split.setSpacing(6)
        _sb_split.addLayout(_left_col, 7)
        _sb_split.addLayout(_right_col, 3)
        lay_sb.addLayout(_sb_split)

        self._updating_controls = False
        self._refresh_key_controls()

        self.setStyleSheet("QLineEdit { border: none; border-radius: 3px; padding: 0px 4px; }")
        # Restore native look on the editable combo's internal QLineEdit
        self._combo_stagger_axis.lineEdit().setStyleSheet("")

    # ── Populate ──────────────────────────────────────────────────────────────

    def _populate_from_default_json(self):
        path = _check_shapes_default_json_path()
        if os.path.exists(path):
            self._le_json.setText(path)
            self._load_shapes_from_json(path)

    def _populate_mapping_from_parent(self):
        """Pre-fill the mapping path from the parent UI, without loading the table."""
        parent_ui = self.parent()
        if parent_ui is None or not hasattr(parent_ui, "line_rig_json"):
            return
        path = parent_ui.line_rig_json.text().strip()
        if path and os.path.exists(path):
            self._le_mapping_path.setText(path)

    # ── Undo / Redo ───────────────────────────────────────────────────────────

    def _current_state(self):
        """Capture the full current state of the dialog (rows, pairs, curve)."""
        rows = []
        if not self._is_placeholder():
            for r in range(self._table.rowCount()):
                rows.append(self._snapshot_row(r))
        return {
            "rows":  rows,
            "pairs": self._collect_soft_blend_pairs(),
            "curve": copy.deepcopy(self._graph.get_keys()),
        }

    def _push_undo(self):
        """Push current state onto the undo stack (called before a change)."""
        if self._undo_restoring:
            return
        state = self._current_state()
        if self._undo_stack and self._undo_stack[-1] == state:
            return  # deduplicate identical consecutive states
        self._undo_stack.append(state)
        if len(self._undo_stack) > 50:
            self._undo_stack.pop(0)
        self._redo_stack.clear()

    def _restore_state(self, state):
        """Rebuild the dialog from a captured state dict."""
        self._undo_restoring = True
        try:
            self._table.setRowCount(0)
            for snap in state["rows"]:
                self._insert_row_data_at(self._table.rowCount(), snap)
            self._renumber_rows()
            if self._table.rowCount() == 0:
                self._add_placeholder_row()
            self._tbl_pairs.setRowCount(0)
            for pair in state["pairs"]:
                if len(pair) == 2:
                    self._insert_pair_row(pair[0], pair[1])
            if state.get("curve"):
                self._graph.set_keys(copy.deepcopy(state["curve"]))
            self._refresh_key_controls()
        finally:
            self._undo_restoring = False

    def _undo(self):
        if not self._undo_stack:
            return
        self._redo_stack.append(self._current_state())
        self._restore_state(self._undo_stack.pop())

    def _redo(self):
        if not self._redo_stack:
            return
        self._undo_stack.append(self._current_state())
        self._restore_state(self._redo_stack.pop())

    def eventFilter(self, obj, event):
        """Push undo when a cell widget gains focus (before user edits it)."""
        if event.type() == QtCore.QEvent.FocusIn:
            if isinstance(obj, (QtWidgets.QLineEdit, QtWidgets.QComboBox)):
                self._push_undo()
        return False

    def _load_shapes_from_json(self, path):
        try:
            with open(path, "r") as f:
                data = json.load(f)
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "Load Error", str(e))
            return
        if isinstance(data, list):
            shapes = data
        elif isinstance(data, dict):
            shapes = []
            for v in data.values():
                if isinstance(v, list):
                    shapes.extend(v)
        else:
            shapes = []
        self._shapes = [s for s in shapes if isinstance(s, str)]
        self._rebuild_table()

    def _rebuild_table(self):
        """Re-create all rows from self._shapes, preserving controller scene list."""
        controllers = self._scene_controllers()
        self._table.setRowCount(0)
        for i, shape in enumerate(self._shapes):
            self._append_table_row(i + 1, shape, controllers)
        if self._table.rowCount() == 0:
            self._add_placeholder_row()

    def _append_table_row(self, row_num, shape_name, controllers,
                          ctrl="", attr="ty",
                          in_min=0.0, in_max=1.0, gate="",
                          skinning_ctrl=False):
        self._remove_placeholder()
        row = self._table.rowCount()
        self._table.insertRow(row)

        # Col 0 — #
        num_item = QtWidgets.QTableWidgetItem(str(row_num))
        num_item.setFlags(QtCore.Qt.ItemIsEnabled)
        self._table.setItem(row, self._COL_NUM, num_item)

        # Col 1 — Shape
        shape_item = QtWidgets.QTableWidgetItem(shape_name)
        shape_item.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable | QtCore.Qt.ItemIsEditable)
        self._table.setItem(row, self._COL_SHAPE, shape_item)

        # Col 2 — Controller (editable QComboBox)
        cb_ctrl = _NoScrollCombo()
        cb_ctrl.setEditable(True)
        cb_ctrl.addItem("")
        cb_ctrl.addItems(controllers)
        if ctrl:
            idx = cb_ctrl.findText(ctrl)
            if idx >= 0:
                cb_ctrl.setCurrentIndex(idx)
            else:
                cb_ctrl.setEditText(ctrl)
        if skinning_ctrl:
            cb_ctrl.setProperty("skinning_ctrl", True)
        _apply_ctrl_skinning_style(cb_ctrl, skinning_ctrl)
        self._table.setCellWidget(row, self._COL_CTRL, cb_ctrl)

        # Col 3 — Attr (editable combo: pick standard or type custom)
        cb_attr = _NoScrollCombo()
        cb_attr.setEditable(True)
        cb_attr.addItems(self._ATTR_ITEMS)
        cb_attr.setCurrentText(attr)
        cb_attr.setToolTip("Standard attribute or type a custom attribute name directly.")
        self._table.setCellWidget(row, self._COL_ATTR, cb_attr)

        # Col 4 — In Min
        le_min = _make_inval_le(in_min)
        self._table.setCellWidget(row, self._COL_MIN, le_min)

        # Col 5 — In Max (negative = negative direction)
        le_max = _make_inval_le(
            in_max,
            tooltip="Positive = shape activates as controller goes positive.\n"
                    "Negative = shape activates as controller goes negative.")
        self._table.setCellWidget(row, self._COL_MAX, le_max)

        # Col 8 — Cond.
        le_gate = QtWidgets.QLineEdit()
        le_gate.setPlaceholderText("shape name")
        le_gate.setText(gate)
        le_gate.setToolTip("Combo driver(s): one or more entries, comma-separated.\n"
                           "Each entry multiplies the shape weight in series.\n"
                           "  - blendShape target name   ->jaw_dn\n"
                           "  - node.attr plug           ->FKJaw_ctrl.zip\n"
                           "  - rev: prefix (inverted)   ->rev:FKJaw_ctrl.retain\n"
                           "The shape only activates when all combo drivers are active.")
        self._table.setCellWidget(row, self._COL_GATE, le_gate)

        # Col 9 — Status
        lbl_status = QtWidgets.QLabel("●")
        lbl_status.setAlignment(QtCore.Qt.AlignCenter)
        lbl_status.setStyleSheet("color: grey;")
        self._table.setCellWidget(row, self._COL_STAT, lbl_status)

        # Install undo event filter on all editable cell widgets
        for _w in (cb_ctrl, cb_attr, le_min, le_max, le_gate):
            _w.installEventFilter(self)

    # ── Toolbar handlers ──────────────────────────────────────────────────────

    def _get_bs_node(self):
        sel = cmds.ls(sl=True, type="blendShape") or []
        if not sel:
            # try from selected mesh
            meshes = cmds.ls(sl=True, dag=True, type="mesh") or []
            for m in meshes:
                hist = cmds.listHistory(m, pdo=True) or []
                bs_nodes = [n for n in hist if cmds.nodeType(n) == "blendShape"]
                if bs_nodes:
                    sel = bs_nodes[:1]
                    break
        if sel:
            self._le_bs_node.setText(sel[0])
            self._btn_build.setEnabled(True)
        else:
            QtWidgets.QMessageBox.information(
                self, "Get BS Node", "No blendShape node found in selection.")

    def _load_json_file(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Load Shapes JSON", "", "JSON files (*.json)")
        if path:
            self._le_json.setText(path)
            self._load_shapes_from_json(path)

    def _fill_from_bs_node(self):
        self._push_undo()
        bs_node = self._le_bs_node.text().strip()
        if not bs_node:
            # Auto-detect from selection
            self._get_bs_node()
            bs_node = self._le_bs_node.text().strip()
        if not bs_node:
            return
        try:
            targets = cmds.listAttr(f"{bs_node}.w", m=True) or []
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "Fill from BS", str(e))
            return
        if not targets:
            QtWidgets.QMessageBox.information(
                self, "Fill from BS", f"No targets found on '{bs_node}'.")
            return
        if not self._is_placeholder():
            n_rows = sum(
                1 for r in range(self._table.rowCount())
                if not (self._table.item(r, self._COL_NUM) and
                        self._table.item(r, self._COL_NUM).text() == "\u21b3"))
            answer = QtWidgets.QMessageBox.question(
                self, "Autofill from BS Node",
                f"The table already contains {n_rows} row(s).\n"
                f"Replace with {len(targets)} targets from '{bs_node}'?",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No)
            if answer != QtWidgets.QMessageBox.Yes:
                return
        self._le_json.clear()
        self._shapes = targets
        overlay = QtWidgets.QWidget(self._table)
        overlay.setGeometry(self._table.rect())
        overlay.setStyleSheet("background: rgba(0, 0, 0, 80);")
        overlay.setCursor(QtCore.Qt.WaitCursor)
        overlay.show()
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        QtWidgets.QApplication.processEvents()
        self._rebuild_table()
        overlay.hide()
        overlay.deleteLater()
        QtWidgets.QApplication.restoreOverrideCursor()

    # ── Auto-fill ─────────────────────────────────────────────────────────────

    def _scene_controllers(self):
        import re
        try:
            all_xforms = cmds.ls(type="transform") or []
            pattern = re.compile(r"^FK\w+_[LRM]$")
            scene_ctrls = [x for x in all_xforms if pattern.match(x)]
        except Exception:
            scene_ctrls = []
        return scene_ctrls if scene_ctrls else list(_FK_CONTROLLERS)

    @staticmethod
    def _parse_shape(shape_name):
        """
        Returns (side, part_tokens_joined, direction, split) or None on failure.
        shape: {Side}_{part_tokens}_{dir}[_{split}]
        """
        tokens = shape_name.split("_")
        if len(tokens) < 3:
            return None
        side = tokens[0]
        # Last token: optional single letter a-e = split
        split = ""
        if tokens[-1].lower() in ("a", "b", "c", "d", "e"):
            split = tokens[-1].lower()
            tokens = tokens[:-1]
        # Second-to-last is direction, middle tokens are part
        direction = tokens[-1]
        part_tokens = tokens[1:-1]
        part = "_".join(part_tokens)
        return side, part, direction, split

    @staticmethod
    def _parse_controller(ctrl_name):
        """
        Returns (part, split, side) or None.
        pattern: FK{Part}{Split}_{Side}   e.g. FKLipUpA_L
        """
        import re
        m = re.match(r"^FK([A-Za-z]+?)([A-E]?)_([LRM])$", ctrl_name)
        if not m:
            return None
        return m.group(1), m.group(2).upper(), m.group(3)

    def _auto_fill(self):
        import re
        controllers = self._scene_controllers()
        # Pre-parse all controllers
        parsed_ctrls = {}  # ctrl_name -> (part, split, side)
        for c in controllers:
            p = self._parse_controller(c)
            if p:
                parsed_ctrls[c] = p

        for row in range(self._table.rowCount()):
            shape_item = self._table.item(row, self._COL_SHAPE)
            if not shape_item:
                continue
            shape_name = shape_item.text()
            parsed = self._parse_shape(shape_name)
            if not parsed:
                continue
            shape_side, shape_part, direction, split = parsed

            # Map direction token ->attr name + sign
            dir_lower = direction.lower()
            if dir_lower in _RC_CUSTOM_DIRS:
                attr     = direction  # keep the raw token as custom attr name
                negative = False
            else:
                mapping = _RC_DIR_ATTR.get(dir_lower)
                if not mapping:
                    continue
                attr, dir_sign = mapping
                negative = (dir_sign == "\u2212")

            # Find matching controller
            matched_ctrl = ""
            for ctrl_name, (ctrl_part, ctrl_split, ctrl_side_raw) in parsed_ctrls.items():
                mapped_side = _RC_SIDE_MAP.get(ctrl_side_raw, "")
                mapped_part = _RC_PART_MAP.get(ctrl_part, "")
                ctrl_split_lower = ctrl_split.lower()
                if (mapped_side == shape_side and
                        mapped_part == shape_part and
                        ctrl_split_lower == split):
                    matched_ctrl = ctrl_name
                    break

            # Fill cells
            cb_ctrl  = self._table.cellWidget(row, self._COL_CTRL)
            cb_attr  = self._table.cellWidget(row, self._COL_ATTR)
            le_max = self._table.cellWidget(row, self._COL_MAX)
            if not (cb_ctrl and cb_attr):
                continue

            if matched_ctrl:
                idx = cb_ctrl.findText(matched_ctrl)
                if idx >= 0:
                    cb_ctrl.setCurrentIndex(idx)
                else:
                    cb_ctrl.setEditText(matched_ctrl)

            cb_attr.setCurrentText(attr)

            # Encode direction in sign of in_max
            if le_max and negative:
                le_max.setValue(-abs(le_max.value()))

    # ── Row management ────────────────────────────────────────────────────────

    def _shape_editor_selection(self):
        """Return target names currently selected in Maya's Shape Editor."""
        try:
            bs = self._le_bs_node.text().strip()
            targets = get_selected_targets()   # (bs_node, idx, name) — from blendshape_core
            if bs:
                return [name for node, _idx, name in targets if node == bs]
            return [name for _node, _idx, name in targets]
        except Exception:
            return []

    # ── Placeholder row (empty table) ─────────────────────────────────────────

    def _is_placeholder(self):
        """True when the table contains only the grayed-out example row."""
        if self._table.rowCount() != 1:
            return False
        item = self._table.item(0, self._COL_NUM)
        return item is not None and item.data(QtCore.Qt.UserRole) == "__placeholder__"

    def _add_placeholder_row(self):
        """Insert a single non-interactive example row when the table is empty."""
        if self._table.rowCount() > 0:
            return
        grey = QtGui.QColor(105, 105, 105)
        self._table.insertRow(0)
        for col, text in (
            (self._COL_NUM,   "1"),
            (self._COL_SHAPE, "L_shapeName_sel"),
            (self._COL_CTRL,  "L_controlName_ctrl"),
            (self._COL_ATTR,  "ty"),
            (self._COL_GATE,  "R_comboControl_ctrl"),
            (self._COL_STAT,  "●"),
        ):
            item = QtWidgets.QTableWidgetItem(text)
            item.setFlags(QtCore.Qt.NoItemFlags)
            item.setForeground(grey)
            if col == self._COL_NUM:
                item.setData(QtCore.Qt.UserRole, "__placeholder__")
            self._table.setItem(0, col, item)
        for col, val in ((self._COL_MIN, 0.0), (self._COL_MAX, 1.0)):
            le = _make_inval_le(val)
            le.setEnabled(False)
            le.setStyleSheet("color: rgb(105, 105, 105);")
            self._table.setCellWidget(0, col, le)

    def _remove_placeholder(self):
        """Remove the placeholder row if it is the only row in the table."""
        if self._is_placeholder():
            self._table.removeRow(0)

    def _add_row(self):
        controllers = self._scene_controllers()
        sel_rows = sorted(set(idx.row() for idx in self._table.selectedIndexes()))
        insert_at = (sel_rows[-1] + 1) if sel_rows else self._table.rowCount()

        blank = {"is_proxy": False, "shape": "", "ctrl": "", "attr": "ty",
                 "min":0.0, "max":1.0, "gate": "",
                 "stat_text": "\u25cf", "stat_style": "color: grey;", "stat_tip": ""}

        names = self._shape_editor_selection() or [""]
        for i, name in enumerate(names):
            snap = dict(blank, shape=name)
            self._insert_row_data_at(insert_at + i, snap)
        self._renumber_rows()

    def _remove_rows(self):
        self._push_undo()
        selected = sorted(
            set(idx.row() for idx in self._table.selectedIndexes()), reverse=True)
        for r in selected:
            self._table.removeRow(r)
        self._renumber_rows()
        if self._table.rowCount() == 0:
            self._add_placeholder_row()

    # ── Row reordering ────────────────────────────────────────────────────────

    def _renumber_rows(self):
        n = 0
        for r in range(self._table.rowCount()):
            item = self._table.item(r, self._COL_NUM)
            if not item or item.text() == "\u21b3":
                continue
            n += 1
            item.setText(str(n))

    def _snapshot_row(self, r):
        """Return a dict capturing all data for row r (items + widget values)."""
        num_item   = self._table.item(r, self._COL_NUM)
        shape_item = self._table.item(r, self._COL_SHAPE)
        cb_ctrl    = self._table.cellWidget(r, self._COL_CTRL)
        cb_attr    = self._table.cellWidget(r, self._COL_ATTR)
        le_min   = self._table.cellWidget(r, self._COL_MIN)
        le_max   = self._table.cellWidget(r, self._COL_MAX)
        le_gate    = self._table.cellWidget(r, self._COL_GATE)
        lbl_stat   = self._table.cellWidget(r, self._COL_STAT)
        is_proxy   = bool(num_item and num_item.text() == "\u21b3")
        return {
            "is_proxy":      is_proxy,
            "shape":         shape_item.text()      if shape_item else "",
            "ctrl":          cb_ctrl.currentText()  if cb_ctrl    else "",
            "attr":          cb_attr.currentText()  if cb_attr    else "ty",
            "min":           _read_inval(le_min, 0.0) if le_min else 0.0,
            "max":           _read_inval(le_max, 1.0) if le_max else 1.0,
            "gate":          le_gate.text().strip() if le_gate    else "",
            "skinning_ctrl": bool(cb_ctrl.property("skinning_ctrl")) if cb_ctrl else False,
            "stat_text":     lbl_stat.text()        if lbl_stat   else "\u25cf",
            "stat_style":    lbl_stat.styleSheet()  if lbl_stat   else "color: grey;",
            "stat_tip":      lbl_stat.toolTip()     if lbl_stat   else "",
        }

    def _insert_row_data_at(self, pos, d):
        """Insert a fully populated row at position pos from a snapshot dict."""
        self._remove_placeholder()
        controllers = self._scene_controllers()
        self._table.insertRow(pos)

        # Col 0 — #
        num_text = "\u21b3" if d["is_proxy"] else ""  # number assigned by _renumber_rows
        num_item = QtWidgets.QTableWidgetItem(num_text)
        num_item.setFlags(QtCore.Qt.ItemIsEnabled)
        if d["is_proxy"]:
            num_item.setForeground(QtGui.QColor("#888888"))
        self._table.setItem(pos, self._COL_NUM, num_item)

        # Col 1 — Shape
        shape_item = QtWidgets.QTableWidgetItem(d["shape"])
        if d["is_proxy"]:
            shape_item.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable)
            shape_item.setForeground(QtGui.QColor("#888888"))
        else:
            shape_item.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable
                                | QtCore.Qt.ItemIsEditable)
        self._table.setItem(pos, self._COL_SHAPE, shape_item)

        # Col 2 — Controller
        cb_ctrl = _NoScrollCombo()
        cb_ctrl.setEditable(True)
        cb_ctrl.addItem("")
        cb_ctrl.addItems(controllers)
        if d["ctrl"]:
            idx = cb_ctrl.findText(d["ctrl"])
            cb_ctrl.setCurrentIndex(idx) if idx >= 0 else cb_ctrl.setEditText(d["ctrl"])
        skin_c = d.get("skinning_ctrl", False)
        if skin_c:
            cb_ctrl.setProperty("skinning_ctrl", True)
        _apply_ctrl_skinning_style(cb_ctrl, skin_c)
        self._table.setCellWidget(pos, self._COL_CTRL, cb_ctrl)

        # Col 3 — Attr (editable combo)
        cb_attr = _NoScrollCombo()
        cb_attr.setEditable(True)
        cb_attr.addItems(self._ATTR_ITEMS)
        cb_attr.setCurrentText(d["attr"])
        cb_attr.setToolTip("Standard attribute or type a custom attribute name directly.")
        self._table.setCellWidget(pos, self._COL_ATTR, cb_attr)

        # Col 4 — In Min
        le_min = _make_inval_le(d["min"])
        self._table.setCellWidget(pos, self._COL_MIN, le_min)

        # Col 5 — In Max (negative = negative direction)
        le_max = _make_inval_le(
            d["max"],
            tooltip="Positive = shape activates as controller goes positive.\n"
                    "Negative = shape activates as controller goes negative.")
        self._table.setCellWidget(pos, self._COL_MAX, le_max)

        # Col 8 — Cond.
        le_gate = QtWidgets.QLineEdit()
        le_gate.setPlaceholderText("shape name")
        le_gate.setText(d.get("gate", ""))
        le_gate.setToolTip("Combo driver(s): one or more entries, comma-separated.\n"
                           "Each entry multiplies the shape weight in series.\n"
                           "  - blendShape target name   ->jaw_dn\n"
                           "  - node.attr plug           ->FKJaw_ctrl.zip\n"
                           "  - rev: prefix (inverted)   ->rev:FKJaw_ctrl.retain\n"
                           "The shape only activates when all combo drivers are active.")
        self._table.setCellWidget(pos, self._COL_GATE, le_gate)

        # Col 9 — Status
        lbl_stat = QtWidgets.QLabel(d["stat_text"])
        lbl_stat.setAlignment(QtCore.Qt.AlignCenter)
        lbl_stat.setStyleSheet(d["stat_style"])
        lbl_stat.setToolTip(d["stat_tip"])
        self._table.setCellWidget(pos, self._COL_STAT, lbl_stat)

        for _w in (cb_ctrl, cb_attr, le_min, le_max, le_gate):
            _w.installEventFilter(self)

    def _move_block(self, src_rows, target_row):
        """Move src_rows (sorted list of row indices) to before target_row."""
        if not src_rows:
            return
        self._push_undo()
        src_rows = sorted(src_rows)

        # Snapshot source rows
        snapshots = [self._snapshot_row(r) for r in src_rows]

        # Compute adjusted insertion point after deletion
        n_before = sum(1 for r in src_rows if r < target_row)
        adj_target = max(0, min(target_row - n_before,
                                self._table.rowCount() - len(src_rows)))

        # Remove source rows (highest first to keep indices valid)
        for r in reversed(src_rows):
            self._table.removeRow(r)

        # Insert snapshots at adjusted position
        for i, snap in enumerate(snapshots):
            self._insert_row_data_at(adj_target + i, snap)

        self._renumber_rows()

        # Restore selection on all moved rows
        sel_model = self._table.selectionModel()
        sel_model.clearSelection()
        col_count = self._table.columnCount()
        for i in range(len(snapshots)):
            top_left     = self._table.model().index(adj_target + i, 0)
            bottom_right = self._table.model().index(adj_target + i, col_count - 1)
            sel_model.select(
                QtCore.QItemSelection(top_left, bottom_right),
                QtCore.QItemSelectionModel.Select)

    def _create_opposite_row(self):
        self._push_undo()
        sel_rows = sorted(set(idx.row() for idx in self._table.selectedIndexes()))
        if not sel_rows:
            return

        # Build a name→row index map of the current table
        def _shape_row_map():
            m = {}
            for r in range(self._table.rowCount()):
                it = self._table.item(r, self._COL_SHAPE)
                if it and it.text():
                    m[it.text()] = r
            return m

        overwrite_all = None  # None = not decided, True/False = decided for all

        # Process in reverse so inserted rows don't shift subsequent indices
        for src in reversed(sel_rows):
            snap = self._snapshot_row(src)
            opp_shape = _swap_opposite_name(snap["shape"]) or snap["shape"]
            opp_ctrl  = _swap_opposite_name(snap["ctrl"])  or snap["ctrl"]
            opp_gate  = _swap_opposite_name(snap["gate"])  or snap["gate"]
            new_snap  = dict(snap,
                             shape=opp_shape,
                             ctrl=opp_ctrl,
                             gate=opp_gate,
                             stat_text="\u25cf",
                             stat_style="color: grey;",
                             stat_tip="")

            existing = _shape_row_map().get(opp_shape, -1)
            if existing >= 0:
                if overwrite_all is None:
                    box = QtWidgets.QMessageBox(self)
                    box.setWindowTitle("Overwrite?")
                    box.setText(f"A row named <b>{opp_shape}</b> already exists.<br>Overwrite it?")
                    btn_yes     = box.addButton("Yes",        QtWidgets.QMessageBox.YesRole)
                    btn_yes_all = box.addButton("Yes to All", QtWidgets.QMessageBox.YesRole)
                    btn_no      = box.addButton("No",         QtWidgets.QMessageBox.NoRole)
                    btn_no_all  = box.addButton("No to All",  QtWidgets.QMessageBox.NoRole)
                    box.exec_()
                    clicked = box.clickedButton()
                    if clicked is btn_yes_all:
                        overwrite_all = True
                    elif clicked is btn_no_all:
                        overwrite_all = False
                    do_overwrite = clicked in (btn_yes, btn_yes_all)
                else:
                    do_overwrite = overwrite_all

                if not do_overwrite:
                    continue

                # Overwrite: update widgets in the existing row in place
                self._table.item(existing, self._COL_SHAPE).setText(opp_shape)
                cb_ctrl  = self._table.cellWidget(existing, self._COL_CTRL)
                cb_attr  = self._table.cellWidget(existing, self._COL_ATTR)
                le_min = self._table.cellWidget(existing, self._COL_MIN)
                le_max = self._table.cellWidget(existing, self._COL_MAX)
                le_gate  = self._table.cellWidget(existing, self._COL_GATE)
                if cb_ctrl:
                    idx = cb_ctrl.findText(opp_ctrl)
                    cb_ctrl.setCurrentIndex(idx) if idx >= 0 else cb_ctrl.setEditText(opp_ctrl)
                if cb_attr:  cb_attr.setCurrentText(new_snap["attr"])
                if le_min: le_min.setText(_fmt_inval(new_snap["min"]))
                if le_max: le_max.setText(_fmt_inval(new_snap["max"]))
                if le_gate:  le_gate.setText(opp_gate)
            else:
                self._insert_row_data_at(src + 1, new_snap)

        self._renumber_rows()

    def _move_up(self):
        src_rows = sorted(set(idx.row() for idx in self._table.selectedIndexes()))
        if not src_rows or src_rows[0] == 0:
            return
        self._move_block(src_rows, src_rows[0] - 1)

    def _move_down(self):
        src_rows = sorted(set(idx.row() for idx in self._table.selectedIndexes()))
        if not src_rows or src_rows[-1] >= self._table.rowCount() - 1:
            return
        self._move_block(src_rows, src_rows[-1] + 2)

    # ── Save / Load mapping ───────────────────────────────────────────────────

    def _collect_rows(self):
        rows = []
        for r in range(self._table.rowCount()):
            num_item   = self._table.item(r, self._COL_NUM)
            shape_item = self._table.item(r, self._COL_SHAPE)
            shape    = shape_item.text() if shape_item else ""
            is_proxy = bool(num_item and num_item.text() == "\u21b3")
            cb_ctrl  = self._table.cellWidget(r, self._COL_CTRL)
            cb_attr  = self._table.cellWidget(r, self._COL_ATTR)
            le_min = self._table.cellWidget(r, self._COL_MIN)
            le_max = self._table.cellWidget(r, self._COL_MAX)
            le_gate  = self._table.cellWidget(r, self._COL_GATE)
            rows.append({
                "_table_row": r,
                "shape":      shape,
                "proxy":      is_proxy,
                "controller": cb_ctrl.currentText() if cb_ctrl  else "",
                "attr":       cb_attr.currentText() if cb_attr  else "ty",
                "min":    _read_inval(le_min, 0.0) if le_min else 0.0,
                "max":    _read_inval(le_max, 1.0) if le_max else 1.0,
                "gate":       le_gate.text().strip() if le_gate  else "",
                "skinning_ctrl": bool(cb_ctrl.property("skinning_ctrl")) if cb_ctrl else False,
            })
        return rows

    # ── Soft Blend Pairs ──────────────────────────────────────────────────────

    def _collect_soft_blend_pairs(self):
        pairs = []
        for r in range(self._tbl_pairs.rowCount()):
            a = self._tbl_pairs.item(r, 0)
            b = self._tbl_pairs.item(r, 1)
            if a and b and a.text() and b.text():
                pairs.append([a.text(), b.text()])
        return pairs

    def _insert_pair_row(self, shape_a, shape_b):
        r = self._tbl_pairs.rowCount()
        self._tbl_pairs.insertRow(r)
        self._tbl_pairs.setItem(r, 0, QtWidgets.QTableWidgetItem(shape_a))
        self._tbl_pairs.setItem(r, 1, QtWidgets.QTableWidgetItem(shape_b))
        btn_rm = QtWidgets.QPushButton("\u00d7")
        btn_rm.setFixedWidth(22)
        btn_rm.clicked.connect(lambda checked=False, b=btn_rm: self._remove_pair_row(b))
        self._tbl_pairs.setCellWidget(r, 2, btn_rm)

    def _remove_pair_row(self, btn):
        self._push_undo()
        for r in range(self._tbl_pairs.rowCount()):
            if self._tbl_pairs.cellWidget(r, 2) is btn:
                self._tbl_pairs.removeRow(r)
                break

    def _add_soft_blend_pair(self):
        self._push_undo()
        # Collect selected rows in order (skip placeholder)
        sel_rows = sorted(set(idx.row() for idx in self._table.selectedIndexes()))
        if self._is_placeholder():
            sel_rows = []

        sel_shapes = []
        for r in sel_rows:
            item = self._table.item(r, self._COL_SHAPE)
            if item and item.text():
                sel_shapes.append(item.text())

        if sel_shapes:
            if len(sel_shapes) % 2 != 0:
                self._set_status(
                    f"Select an even number of rows to create pairs ({len(sel_shapes)} selected).",
                    error=True)
                return
            for i in range(0, len(sel_shapes), 2):
                self._insert_pair_row(sel_shapes[i], sel_shapes[i + 1])
            return

        # No selection → open popup
        all_shapes = []
        for r in range(self._table.rowCount()):
            item = self._table.item(r, self._COL_SHAPE)
            if item and item.text():
                all_shapes.append(item.text())
        dlg = _AddSoftBlendPairDialog(all_shapes, self)
        if dlg.exec_() == QtWidgets.QDialog.Accepted:
            a, b = dlg.get_pair()
            if a and b and a != b:
                self._insert_pair_row(a, b)

    def _reset_soft_blend_curve(self):
        import copy
        self._graph.set_keys(copy.deepcopy(_SoftBlendGraphWidget.DEFAULT_KEYS))
        self._refresh_key_controls()

    # ── Graph / key controls ──────────────────────────────────────────────────

    def _refresh_key_controls(self):
        """Sync U/V spinboxes and tangent combo to the currently selected graph key."""
        self._updating_controls = True
        k   = self._graph.selected_key()
        idx = self._graph.selected_index()
        if k is None:
            self._sb_key_u.setValue(0.0)
            self._sb_key_v.setValue(0.0)
            self._combo_key_tang.setCurrentText("smooth")
            self._sb_key_u.setEnabled(False)
            self._sb_key_v.setEnabled(False)
            self._combo_key_tang.setEnabled(False)
        else:
            self._sb_key_u.setValue(k["u"])
            self._sb_key_v.setValue(k["v"])
            self._combo_key_tang.setCurrentText(k.get("tangent", "smooth"))
            self._sb_key_u.setEnabled(idx not in _SoftBlendGraphWidget._U_LOCKED)
            self._sb_key_v.setEnabled(idx not in _SoftBlendGraphWidget._V_LOCKED)
            self._combo_key_tang.setEnabled(
                idx not in _SoftBlendGraphWidget._TANGENT_LOCKED)
        self._updating_controls = False

    def _on_graph_keys_changed(self):
        if not self._updating_controls:
            self._refresh_key_controls()

    def _on_key_u_changed(self, val):
        if self._updating_controls:
            return
        self._graph.set_selected_u(val)

    def _on_key_v_changed(self, val):
        if self._updating_controls:
            return
        self._graph.set_selected_v(val)

    def _on_key_tangent_changed(self, text):
        if self._updating_controls:
            return
        self._graph.set_selected_tangent(text)

    def _save_mapping(self):
        loaded = self._le_mapping_path.text().strip()
        if loaded and os.path.exists(loaded):
            ans = QtWidgets.QMessageBox.question(
                self, "Overwrite?",
                f"Overwrite the existing file?\n\n{loaded}",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No)
            if ans != QtWidgets.QMessageBox.Yes:
                return
            path = loaded
        else:
            default = _smart_mapping_default(loaded)
            path, _ = QtWidgets.QFileDialog.getSaveFileName(
                self, "Save Mapping", default, "JSON files (*.json)")
            if not path:
                return
            self._le_mapping_path.setText(path)
            self._sync_path_to_parent(path)
        data = {
            "connections":       self._collect_rows(),
            "soft_blend_pairs":  self._collect_soft_blend_pairs(),
            "soft_blend_curve":  self._graph.get_keys(),
        }
        try:
            with open(path, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "Save Error", str(e))

    def _save_mapping_increment(self):
        """Save As with auto-incremented version number in the filename."""
        import re
        loaded = self._le_mapping_path.text().strip()
        if loaded:
            directory = os.path.dirname(loaded)
            basename  = os.path.basename(loaded)
            stem, ext = os.path.splitext(basename)
            # Try to find an existing vNN pattern and increment it
            m = re.search(r'(.*[_\-])v(\d+)$', stem, re.IGNORECASE)
            if m:
                prefix  = m.group(1)
                num     = int(m.group(2))
                width   = len(m.group(2))
                new_stem = f"{prefix}v{num + 1:0{width}d}"
            else:
                new_stem = f"{stem}_v02"
            default = os.path.join(directory, new_stem + ext)
        else:
            default = _smart_mapping_default()
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Save Mapping As", default, "JSON files (*.json)")
        if not path:
            return
        self._le_mapping_path.setText(path)
        self._sync_path_to_parent(path)
        data = {
            "connections":       self._collect_rows(),
            "soft_blend_pairs":  self._collect_soft_blend_pairs(),
            "soft_blend_curve":  self._graph.get_keys(),
        }
        try:
            with open(path, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "Save Error", str(e))

    def _autofill_from_json(self):
        """Reload the table from the currently loaded mapping path (no browser)."""
        path = self._le_mapping_path.text().strip()
        if not path or not os.path.exists(path):
            QtWidgets.QMessageBox.warning(
                self, "No file loaded",
                "No mapping file is currently loaded.\nUse the browse button to load one first.")
            return
        self._load_mapping_from_path(path)

    def _sync_path_to_parent(self, path):
        """Propagate the mapping path to the parent BlendshapeEditorUI field."""
        parent_ui = self.parent()
        if parent_ui is not None and hasattr(parent_ui, "line_rig_json"):
            parent_ui.line_rig_json.setText(path)

    def _load_mapping(self):
        default = _smart_mapping_default(self._le_mapping_path.text().strip())
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Load Mapping", default, "JSON files (*.json)")
        if not path or not os.path.exists(path):
            return
        self._le_mapping_path.setText(path)
        self._sync_path_to_parent(path)
        self._load_mapping_from_path(path)

    def _load_mapping_from_path(self, path):
        if not self._is_placeholder():
            n_rows = sum(
                1 for r in range(self._table.rowCount())
                if not (self._table.item(r, self._COL_NUM) and
                        self._table.item(r, self._COL_NUM).text() == "\u21b3"))
            answer = QtWidgets.QMessageBox.question(
                self, "Autofill from JSON",
                f"The table already contains {n_rows} row(s).\n"
                f"Replace with the content of the JSON file?",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No)
            if answer != QtWidgets.QMessageBox.Yes:
                return

        self._push_undo()
        # ── Loading overlay ───────────────────────────────────────────────────
        overlay = QtWidgets.QWidget(self._table)
        overlay.setGeometry(self._table.rect())
        overlay.setStyleSheet("background: rgba(0, 0, 0, 80);")
        overlay.setCursor(QtCore.Qt.WaitCursor)
        overlay.show()
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        QtWidgets.QApplication.processEvents()

        try:
            with open(path, "r") as f:
                data = json.load(f)
        except Exception as e:
            overlay.hide(); overlay.deleteLater()
            QtWidgets.QApplication.restoreOverrideCursor()
            QtWidgets.QMessageBox.warning(self, "Load Error", str(e))
            return

        # Support both old format (list) and new format (dict with "connections" key)
        soft_blend_pairs = []
        soft_blend_curve = None
        if isinstance(data, dict):
            soft_blend_pairs = data.get("soft_blend_pairs", [])
            soft_blend_curve = data.get("soft_blend_curve")
            data = data.get("connections", [])

        # Full rebuild — clear existing table then insert rows in JSON order
        self._table.setRowCount(0)
        controllers = self._scene_controllers()
        row_num = 0

        def _resolve_rd(rd):
            """Normalise a row dict — handles old JSON format (direction + custom_attr)."""
            attr    = rd.get("attr", "ty")
            in_min  = float(rd.get("min", rd.get("in_min", 0.0)))
            in_max  = float(rd.get("max", rd.get("in_max", 1.0)))
            custom_attr = rd.get("custom_attr", "")
            if attr == "custom" and custom_attr:
                attr = custom_attr
            if rd.get("direction", "+") == "\u2212":
                in_max = -abs(in_max)
                if in_min != 0.0:
                    in_min = -abs(in_min)
            return attr, in_min, in_max

        for rd in data:
            if not isinstance(rd, dict):
                continue
            sname = rd.get("shape", "")
            if not sname:
                continue
            is_proxy = rd.get("proxy", False)
            attr, in_min, in_max = _resolve_rd(rd)
            if is_proxy:
                # Find the last row for this shape to insert the proxy after
                last_row = -1
                for check_r in range(self._table.rowCount()):
                    s_item = self._table.item(check_r, self._COL_SHAPE)
                    if s_item and s_item.text() == sname:
                        last_row = check_r
                if last_row < 0:
                    continue
                self._add_proxy_row(last_row, ctrl=rd.get("controller", ""))
                new_row = last_row + 1
                cb_ctrl_w = self._table.cellWidget(new_row, self._COL_CTRL)
                cb_attr_w = self._table.cellWidget(new_row, self._COL_ATTR)
                le_min_w  = self._table.cellWidget(new_row, self._COL_MIN)
                le_max_w  = self._table.cellWidget(new_row, self._COL_MAX)
                le_gate_w = self._table.cellWidget(new_row, self._COL_GATE)
                if cb_attr_w: cb_attr_w.setCurrentText(attr)
                if le_min_w:  le_min_w.setText(_fmt_inval(in_min))
                if le_max_w:  le_max_w.setText(_fmt_inval(in_max))
                if le_gate_w: le_gate_w.setText(rd.get("gate", ""))
                if cb_ctrl_w:
                    skin_c = rd.get("skinning_ctrl", False)
                    if skin_c:
                        cb_ctrl_w.setProperty("skinning_ctrl", True)
                    _apply_ctrl_skinning_style(cb_ctrl_w, skin_c)
            else:
                row_num += 1
                self._append_table_row(
                    row_num, sname, controllers,
                    ctrl=rd.get("controller", ""),
                    attr=attr,
                    in_min=in_min,
                    in_max=in_max,
                    gate=rd.get("gate", ""),
                    skinning_ctrl=rd.get("skinning_ctrl", False),
                )

        # ── Soft blend pairs ──────────────────────────────────────────────────
        self._tbl_pairs.setRowCount(0)
        for pair in soft_blend_pairs:
            if len(pair) == 2:
                self._insert_pair_row(pair[0], pair[1])

        # ── Soft blend curve ──────────────────────────────────────────────────
        if soft_blend_curve:
            self._graph.set_keys(soft_blend_curve)
        else:
            self._graph.set_keys(copy.deepcopy(_SoftBlendGraphWidget.DEFAULT_KEYS))
        self._refresh_key_controls()

        overlay.hide()
        overlay.deleteLater()
        QtWidgets.QApplication.restoreOverrideCursor()
        if self._table.rowCount() == 0:
            self._add_placeholder_row()

    # ── Search / filter ───────────────────────────────────────────────────────

    def _filter_rows(self, text):
        text = text.strip().lower()
        for row in range(self._table.rowCount()):
            item = self._table.item(row, self._COL_SHAPE)
            shape_name = item.text().lower() if item else ""
            self._table.setRowHidden(row, bool(text) and text not in shape_name)

    def _format_scale_factor(self):
        try:
            self._le_scale.setText(f"{float(self._le_scale.text()):.2f}")
        except ValueError:
            self._le_scale.setText("2.00")

    def _set_skinning_ctrl_selection(self, enabled):
        """Enable or disable the skinning controller flag for selected rows."""
        sel_rows = list({idx.row() for idx in self._table.selectedIndexes()})
        if not sel_rows:
            return
        self._push_undo()
        for r in sel_rows:
            cb_ctrl = self._table.cellWidget(r, self._COL_CTRL)
            if cb_ctrl is not None:
                cb_ctrl.setProperty("skinning_ctrl", enabled)
                _apply_ctrl_skinning_style(cb_ctrl, enabled)

    def _scale_ranges(self, col):
        selected_rows = list({idx.row() for idx in self._table.selectedIndexes()})
        if not selected_rows:
            return
        try:
            factor = float(self._le_scale.text())
        except ValueError:
            return
        for row in selected_rows:
            sb = self._table.cellWidget(row, col)
            if sb is not None:
                sb.setText(_fmt_inval(_read_inval(sb) * factor))

    # ── Context menus ─────────────────────────────────────────────────────────

    def _show_header_context_menu(self, pos):
        col = self._table.horizontalHeader().logicalIndexAt(pos)
        menu = QtWidgets.QMenu(self)
        if col == self._COL_NUM:
            act = menu.addAction("Reorder From Shape Editor")
            act.setToolTip(
                "Reorder the table rows to match the visual order shown in Maya's Shape Editor.\n"
                "Reads the targetDirectory structure of the BS node to respect custom groupings\n"
                "and manual reordering done inside the Shape Editor.\n"
                "Proxy rows follow their primary row. Unmatched rows are appended at the end.")
            act.triggered.connect(self._reorder_from_bs_node)
        elif col == self._COL_CTRL:
            sel_rows = list({idx.row() for idx in self._table.selectedIndexes()})
            all_on = bool(sel_rows) and all(
                bool(self._table.cellWidget(r, self._COL_CTRL) and
                     self._table.cellWidget(r, self._COL_CTRL).property("skinning_ctrl"))
                for r in sel_rows
            )
            act_skin = menu.addAction("Is Skinning Controller  —  selected rows")
            act_skin.setCheckable(True)
            act_skin.setChecked(all_on)
            act_skin.triggered.connect(self._set_skinning_ctrl_selection)
        elif col in (self._COL_MIN, self._COL_MAX):
            act = menu.addAction(f"Multiply selected rows by Scale Factor  ({self._le_scale.text()})")
            act.triggered.connect(lambda: self._scale_ranges(col))
        else:
            return
        menu.exec_(self._table.horizontalHeader().mapToGlobal(pos))

    def _reorder_from_bs_node(self):
        """Reorder table rows to match the visual order shown in Maya's Shape Editor."""
        self._push_undo()
        bs_node = self._le_bs_node.text().strip()
        if not bs_node:
            self._get_bs_node()
            bs_node = self._le_bs_node.text().strip()
        if not bs_node:
            self._set_status("No BlendShape node set.", error=True)
            return
        try:
            targets = get_shape_editor_order(bs_node)
        except Exception as e:
            self._set_status(f"Reorder failed: {e}", error=True)
            return
        if not targets:
            self._set_status(f"No targets found on '{bs_node}'.", error=True)
            return

        order = {name: i for i, name in enumerate(targets)}

        # Snapshot all rows, skip placeholder
        if self._is_placeholder():
            return
        snapshots = [self._snapshot_row(r) for r in range(self._table.rowCount())]

        # Group into blocks: each primary row followed by its proxy rows
        blocks = []
        for snap in snapshots:
            if snap.get("is_proxy", False):
                if blocks:
                    blocks[-1].append(snap)
                else:
                    blocks.append([snap])
            else:
                blocks.append([snap])

        # Sort blocks by primary row's position in BS node order;
        # shapes not found in BS node go to the end, preserving their relative order
        not_found = [b for b in blocks if b[0].get("shape", "") not in order]
        found     = [b for b in blocks if b[0].get("shape", "") in order]
        found.sort(key=lambda b: order[b[0]["shape"]])
        sorted_blocks = found + not_found

        # Rebuild table with loading overlay
        overlay = QtWidgets.QWidget(self._table)
        overlay.setGeometry(self._table.rect())
        overlay.setStyleSheet("background: rgba(0, 0, 0, 80);")
        overlay.setCursor(QtCore.Qt.WaitCursor)
        overlay.show()
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        QtWidgets.QApplication.processEvents()
        self._table.setRowCount(0)
        for block in sorted_blocks:
            for snap in block:
                self._insert_row_data_at(self._table.rowCount(), snap)
        self._renumber_rows()
        overlay.hide()
        overlay.deleteLater()
        QtWidgets.QApplication.restoreOverrideCursor()
        self._set_status(
            f"Rows reordered to Shape Editor order of '{bs_node}' "
            f"({len(found)} matched, {len(not_found)} unmatched appended).")

    def _show_row_context_menu(self, pos):
        row = self._table.rowAt(pos.y())
        if row < 0:
            return
        menu = QtWidgets.QMenu(self)
        act_proxy = menu.addAction("Add Proxy Row")
        act = menu.exec_(self._table.viewport().mapToGlobal(pos))
        if act == act_proxy:
            self._add_proxy_row(row)

    def _add_proxy_row(self, source_row, ctrl="", in_max_override=None):
        """Insert a row after source_row with the same shape and an empty (or pre-filled) controller."""
        shape_item = self._table.item(source_row, self._COL_SHAPE)
        if not shape_item:
            return
        shape_name = shape_item.text()

        # Read source row parameters (attr, in_max)
        cb_attr  = self._table.cellWidget(source_row, self._COL_ATTR)
        le_max = self._table.cellWidget(source_row, self._COL_MAX)
        attr   = cb_attr.currentText()  if cb_attr  else "ty"
        in_max = in_max_override if in_max_override is not None else (_read_inval(le_max, 1.0) if le_max else 1.0)

        controllers = self._scene_controllers()
        insert_row  = source_row + 1
        self._table.insertRow(insert_row)

        row_num_item = QtWidgets.QTableWidgetItem(f"↳")
        row_num_item.setFlags(QtCore.Qt.ItemIsEnabled)
        row_num_item.setForeground(QtGui.QColor("#888888"))
        self._table.setItem(insert_row, self._COL_NUM, row_num_item)

        shape_proxy = QtWidgets.QTableWidgetItem(shape_name)
        shape_proxy.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable)
        shape_proxy.setForeground(QtGui.QColor("#888888"))
        self._table.setItem(insert_row, self._COL_SHAPE, shape_proxy)

        cb_ctrl_new = _NoScrollCombo()
        cb_ctrl_new.setEditable(True)
        cb_ctrl_new.addItem("")
        cb_ctrl_new.addItems(controllers)
        if ctrl:
            cb_ctrl_new.setCurrentText(ctrl)
        self._table.setCellWidget(insert_row, self._COL_CTRL, cb_ctrl_new)

        cb_attr_new = _NoScrollCombo()
        cb_attr_new.setEditable(True)
        cb_attr_new.addItems(self._ATTR_ITEMS)
        cb_attr_new.setCurrentText(attr)
        cb_attr_new.setToolTip("Standard attribute or type a custom attribute name directly.")
        self._table.setCellWidget(insert_row, self._COL_ATTR, cb_attr_new)

        le_min_new = _make_inval_le(0.0)
        self._table.setCellWidget(insert_row, self._COL_MIN, le_min_new)

        le_max_new = _make_inval_le(
            in_max,
            tooltip="Positive = shape activates as controller goes positive.\n"
                    "Negative = shape activates as controller goes negative.")
        self._table.setCellWidget(insert_row, self._COL_MAX, le_max_new)

        le_gate_new = QtWidgets.QLineEdit()
        le_gate_new.setPlaceholderText("shape name")
        le_gate_new.setToolTip("Combo driver(s): one or more entries, comma-separated.\n"
                               "Each entry multiplies the shape weight in series.\n"
                               "  - blendShape target name   ->jaw_dn\n"
                               "  - node.attr plug           ->FKJaw_ctrl.zip\n"
                               "  - rev: prefix (inverted)   ->rev:FKJaw_ctrl.retain\n"
                               "The shape only activates when all combo drivers are active.")
        self._table.setCellWidget(insert_row, self._COL_GATE, le_gate_new)

        lbl_status = QtWidgets.QLabel("●")
        lbl_status.setAlignment(QtCore.Qt.AlignCenter)
        lbl_status.setStyleSheet("color: grey;")
        self._table.setCellWidget(insert_row, self._COL_STAT, lbl_status)

        for _w in (cb_ctrl_new, cb_attr_new, le_min_new, le_max_new, le_gate_new):
            _w.installEventFilter(self)

    # ── Auto-stagger ──────────────────────────────────────────────────────────

    def _on_stagger_mode_changed(self, text):
        self.btn_stagger_sign.setEnabled(text == "Symmetric")
        self._center_row_widget.setEnabled(text == "Symmetric")

    def _apply_stagger(self):
        """Apply stagger In Min / In Max to the selected primary rows.

        Linear    : shape k gets slot [k/N, (k+1)/N] × in_max_ref.
        Symmetric : outer peak — outer shapes activate last, centre shape first.
                    Left half gets negative Max, right half positive (or reversed via +/−).
        Mirror    : centre peak — centre shape activates last, outer shapes first.
                    All shapes same direction. Ideal for zip lips.
                    Ideal for brows and cheekbones.
        Smooth    : each slot extended by ±smooth (clamped to [0, in_max_ref]).
        Proxies ON : creates / updates a proxy sub-row per shape.
        Proxies OFF: writes values directly on the selected rows.
        """
        self._push_undo()
        master_ctrl   = self._le_stagger_ctrl.text().strip()
        axis          = self._combo_stagger_axis.currentText()
        in_max_ref    = float(self._sb_stagger_inmax.text() or "0")
        smooth        = float(self._sb_stagger_falloff.text() or "0")
        curve = self._combo_stagger_curve.currentText()
        mode          = self._combo_stagger_mode.currentText()   # "Linear"/"Mirror"/"Symmetric"
        use_mirror    = (mode == "Mirror")
        use_symmetric = (mode == "Symmetric")
        try:
            center_bias = max(0.0, min(1.0, float(self._stagger_center_le.text())))
        except ValueError:
            center_bias = 0.5
        positive_left = self._stagger_sign > 0
        use_proxies   = self._chk_stagger_proxies.isChecked()

        if use_proxies and not master_ctrl:
            QtWidgets.QMessageBox.warning(
                self, "Auto-stagger", "Enter the stagger controller name.")
            return

        # Collect selected rows
        selected = sorted(set(idx.row() for idx in self._table.selectedIndexes()))

        def _is_proxy(r):
            it = self._table.item(r, self._COL_NUM)
            return bool(it and it.text() == "\u21b3")

        # If the entire selection is proxy rows, operate on them directly
        proxy_only = bool(selected) and all(_is_proxy(r) for r in selected)

        if proxy_only:
            primary_selected = selected  # proxy rows ARE the targets
        else:
            primary_selected = [r for r in selected if not _is_proxy(r)]

        if not primary_selected:
            QtWidgets.QMessageBox.information(
                self, "Auto-stagger", "Select rows in the table first.")
            return

        n = len(primary_selected)

        # ── Slot formula ──────────────────────────────────────────────────────
        if use_mirror:
            # Centre shape gets highest slot; outer shapes share lower slots (zip lips)
            c = (n - 1) / 2.0
            num_slots  = (n + 1) // 2
            slot_width = in_max_ref / num_slots if num_slots > 0 else 0.0
            def _slot(k):
                dist = abs(k - c)
                return int(c - dist + 1e-9)
        elif use_symmetric:
            # Outer shapes get highest slot; centre shape gets lowest slot (brows/cheekbones)
            c = center_bias * (n - 1)
            num_slots  = int(max(c, (n - 1) - c) + 1e-9) + 1
            slot_width = in_max_ref / num_slots if num_slots > 0 else 0.0
            def _slot(k):
                return int(abs(k - c) + 1e-9)
        else:
            num_slots  = n
            slot_width = in_max_ref / n if n > 0 else 0.0
            def _slot(k):
                return k

        # ── Direction formula (Symmetric only) ────────────────────────────────
        def _direction(k):
            """Return '+' or '−' based on left/right position; None if not Symmetric."""
            if not use_symmetric:
                return None
            is_left = k < (n - 1) / 2.0   # strictly left of centre
            if positive_left:
                return "+" if is_left else "\u2212"
            else:
                return "\u2212" if is_left else "+"

        # Process bottom-up to avoid index shifts on proxy insertion
        for k, r in reversed(list(enumerate(primary_selected))):
            shape_item = self._table.item(r, self._COL_SHAPE)
            if not shape_item:
                continue
            shape = shape_item.text()

            slot      = _slot(k)
            direction = _direction(k)   # "+", "−", or None

            # Compute signed in_min / in_max (Symmetric ->negative side gets negated values)
            def _crv(t):
                if curve == "Ease In":  return t * t
                if curve == "Ease Out": return 1.0 - (1.0 - t) * (1.0 - t)
                if curve == "Smooth":   return t * t * (3.0 - 2.0 * t)
                return t  # Uniform
            t_min = _crv(slot / num_slots) if num_slots > 0 else 0.0
            t_max = _crv((slot + 1) / num_slots) if num_slots > 0 else 1.0
            base_min = max(0.0, t_min * in_max_ref - smooth)
            base_max = min(in_max_ref, t_max * in_max_ref + smooth)
            if direction == "\u2212":
                in_min_val = -base_min
                in_max_val = -base_max
            else:
                in_min_val = base_min
                in_max_val = base_max

            if proxy_only:
                # Selected row is already the proxy target — write directly
                target_row = r
            elif use_proxies:
                # Find or create a proxy row for this shape + master ctrl
                target_row = -1
                for check_r in range(self._table.rowCount()):
                    n_item = self._table.item(check_r, self._COL_NUM)
                    s_item = self._table.item(check_r, self._COL_SHAPE)
                    cb_chk = self._table.cellWidget(check_r, self._COL_CTRL)
                    if (n_item and n_item.text() == "\u21b3"
                            and s_item and s_item.text() == shape
                            and cb_chk and cb_chk.currentText() == master_ctrl):
                        target_row = check_r
                        break
                if target_row < 0:
                    self._add_proxy_row(r, ctrl=master_ctrl, in_max_override=in_max_val)
                    target_row = r + 1
            else:
                # Write directly on the selected primary row
                target_row = r
                cb_ctrl_w = self._table.cellWidget(r, self._COL_CTRL)
                if cb_ctrl_w and master_ctrl:
                    cb_ctrl_w.setCurrentText(master_ctrl)

            # Apply axis / in_min / in_max (direction is encoded in sign of in_max)
            cb_attr_w  = self._table.cellWidget(target_row, self._COL_ATTR)
            le_min_w = self._table.cellWidget(target_row, self._COL_MIN)
            le_max_w = self._table.cellWidget(target_row, self._COL_MAX)
            if cb_attr_w:
                cb_attr_w.setCurrentText(axis)
            if le_min_w:
                le_min_w.setText(_fmt_inval(in_min_val))
            if le_max_w:
                le_max_w.setText(_fmt_inval(in_max_val))

    # ── Build & Connect ───────────────────────────────────────────────────────

    @undo_chunk
    def _connect_selected_rows(self):
        bs_node = self._le_bs_node.text().strip()
        if not bs_node or not cmds.objExists(bs_node):
            QtWidgets.QMessageBox.warning(
                self, "Connect Selected",
                "No valid blendShape node set.\nUse 'Get' to pick one.")
            return
        sel_rows = sorted(set(idx.row() for idx in self._table.selectedIndexes()))
        if not sel_rows:
            QtWidgets.QMessageBox.warning(self, "Connect Selected", "No rows selected.")
            return
        # Expand to include proxy rows (↳) that belong to selected primaries
        all_rows = self._collect_rows()
        n = len(all_rows)
        expanded = set(sel_rows)
        for r in sel_rows:
            for nr in range(r + 1, n):
                num_item = self._table.item(nr, self._COL_NUM)
                if num_item and num_item.text() == "\u21b3":
                    expanded.add(nr)
                else:
                    break
        rows = [all_rows[r] for r in sorted(expanded) if r < n]
        pairs  = self._collect_soft_blend_pairs()
        curve  = self._graph.get_keys()
        results = build_and_connect_rig(
            bs_node, rows, soft_blend_pairs=pairs, soft_blend_curve=curve)
        row_by_shape = {}
        for r in range(self._table.rowCount()):
            item = self._table.item(r, self._COL_SHAPE)
            if item:
                row_by_shape[item.text()] = r
        ok_count = err_count = skip_count = 0
        for res in results:
            shape  = res["shape"]
            status = res["status"]
            r = res.get("_table_row")
            if r is None:
                r = row_by_shape.get(shape)
            if r is not None:
                lbl = self._table.cellWidget(r, self._COL_STAT)
                if lbl:
                    if status in ("ok", "ok:direct"):
                        color = "#00cc00"; ok_count += 1
                    elif status == "skip":
                        color = "grey"; skip_count += 1
                    else:
                        color = "#ff4444"; err_count += 1
                    lbl.setStyleSheet(f"color: {color};")
                    lbl.setToolTip(status)
        self._set_status(
            f"Connect Selected: {ok_count} ok, {skip_count} skipped, {err_count} error(s).")

    @undo_chunk
    def _on_build_connect(self):
        bs_node = self._le_bs_node.text().strip()
        if not bs_node or not cmds.objExists(bs_node):
            QtWidgets.QMessageBox.warning(
                self, "Build & Connect", "No valid blendShape node set.\nUse 'Get' to pick one.")
            return

        rows = self._collect_rows()
        if not rows:
            QtWidgets.QMessageBox.information(
                self, "Build & Connect",
                "The table is empty.\nPlease load a mapping JSON or use Auto Fill first.")
            return
        pairs  = self._collect_soft_blend_pairs()
        curve  = self._graph.get_keys()
        results = build_and_connect_rig(
            bs_node, rows, soft_blend_pairs=pairs, soft_blend_curve=curve)

        # Build shape ->table row lookup
        row_by_shape = {}
        for r in range(self._table.rowCount()):
            item = self._table.item(r, self._COL_SHAPE)
            if item:
                row_by_shape[item.text()] = r

        ok_count = err_count = skip_count = 0
        for res in results:
            shape  = res["shape"]
            status = res["status"]
            r = res.get("_table_row")
            if r is None:
                r = row_by_shape.get(shape)
            if r is not None:
                lbl = self._table.cellWidget(r, self._COL_STAT)
                if lbl:
                    if status in ("ok", "ok:direct"):
                        color = "#00cc00"
                        ok_count += 1
                    elif status == "skip":
                        color = "grey"
                        skip_count += 1
                    else:
                        color = "#ff4444"
                        err_count += 1
                    lbl.setStyleSheet(f"color: {color};")
                    lbl.setToolTip(status)

        QtWidgets.QMessageBox.information(
            self, "Build & Connect",
            f"Done.\n"
            f"  \u2713 Connected : {ok_count}\n"
            f"  \u2715 Errors    : {err_count}\n"
            f"  \u2014 Skipped   : {skip_count}"
        )


    @undo_chunk
    def _disconnect_selected(self):
        bs_node = self._le_bs_node.text().strip()
        if not bs_node or not cmds.objExists(bs_node):
            return
        selected_rows = sorted(set(idx.row() for idx in self._table.selectedIndexes()))
        shape_names = []
        for r in selected_rows:
            num_item = self._table.item(r, self._COL_NUM)
            if num_item and num_item.text() == "\u21b3":
                continue
            item = self._table.item(r, self._COL_SHAPE)
            if item and item.text():
                shape_names.append(item.text())
        if not shape_names:
            return
        count = disconnect_rig_shapes(bs_node, shape_names)
        for r in selected_rows:
            lbl = self._table.cellWidget(r, self._COL_STAT)
            if lbl:
                lbl.setStyleSheet("color: grey;")
                lbl.setToolTip("")
        print(f"[Rig Connector] \u2713 Disconnected {count} shape(s)")

    @undo_chunk
    def _disconnect_all(self):
        bs_node = self._le_bs_node.text().strip()
        if not bs_node or not cmds.objExists(bs_node):
            return
        shape_names = []
        for r in range(self._table.rowCount()):
            num_item = self._table.item(r, self._COL_NUM)
            if num_item and num_item.text() == "\u21b3":
                continue
            item = self._table.item(r, self._COL_SHAPE)
            if item and item.text():
                shape_names.append(item.text())
        if not shape_names:
            return
        count = disconnect_rig_shapes(bs_node, shape_names)
        for r in range(self._table.rowCount()):
            lbl = self._table.cellWidget(r, self._COL_STAT)
            if lbl:
                lbl.setStyleSheet("color: grey;")
                lbl.setToolTip("")
        print(f"[Rig Connector] \u2713 Disconnected all ({count} shape(s))")


class NamingConventionDialog(QtWidgets.QDialog):
    """
    Full naming convention editor:
      - Tool's Auto-naming : preset, token order, prefix
      - Opposite Target Pairs : custom L/R, up/dn… pairs per axis
    On Save, writes state back to parent_ui and persists pairs to JSON.
    """

    _AXES    = ["Object X", "Object Y", "Object Z"]
    _PRESETS = [
        "{side}_{target}_{suffix}",
        "{target}_{side}_{suffix}",
        "{target}_{suffix}_{side}",
        "{prefix}_{side}_{target}_{suffix}",
        "{prefix}_{target}_{side}_{suffix}",
        "{prefix}_{target}_{suffix}_{side}",
    ]
    _BUILTIN = {
        "Object X": [
            ["L",    "R"    ],
            ["l",    "r"    ],
            ["lft",  "rgt"  ],
            ["left", "right"],
            ["in",   "out"  ],
            ["pos",  "neg"  ],
            ["p",    "n"    ],
        ],
        "Object Y": [
            ["up",     "dn"     ],
            ["up",     "down"   ],
            ["up",     "lo"     ],
            ["u",      "d"      ],
            ["u",      "l"      ],
            ["upper",  "lower"  ],
            ["top",    "bot"    ],
            ["top",    "bottom" ],
            ["hi",     "lo"     ],
            ["high",   "low"    ],
            ["higher", "lower"  ],
            ["pos",    "neg"    ],
            ["p",      "n"      ],
            ["raise",  "depress"],
        ],
        "Object Z": [
            ["fwd",   "bwd" ],
            ["front", "back"],
            ["frt",   "bck" ],
            ["f",     "b"   ],
            ["ant",   "post"],
            ["pos",   "neg" ],
            ["p",     "n"   ],
        ],
    }
    _TOKEN_SS = """
        QListWidget {
            background: rgba(255,255,255,8);
            border: 1px solid rgba(255,255,255,20);
            border-radius: 3px;
        }
        QListWidget::item {
            background: rgba(255,255,255,18);
            border: 1px solid rgba(255,255,255,25);
            border-radius: 3px;
            padding: 2px 8px;
            color: #cccccc;
            font-size: 11px;
        }
        QListWidget::item:selected {
            background: rgba(100,160,255,60);
            border: 1px solid rgba(100,160,255,120);
        }
        QListWidget::item:hover { background: rgba(255,255,255,28); }
    """

    def __init__(self, parent_ui):
        super().__init__(parent_ui)
        self._parent_ui = parent_ui
        self.setWindowTitle("Naming Convention")
        self.setMinimumWidth(440)
        self._user_data = _load_user_duos()
        self._build_ui()
        self._init_from_parent()

    # ── Build ──────────────────────────────────────────────────────────────
    def _build_ui(self):
        root = QtWidgets.QVBoxLayout(self)
        root.setSpacing(10)
        root.setContentsMargins(10, 10, 10, 10)

        # ── Tool's Auto-naming ─────────────────────────────────────────────
        grp_auto = QtWidgets.QGroupBox("Tool's Auto-naming")
        grp_auto.setStyleSheet("QGroupBox { font-size: 11px; }")
        lay_auto = QtWidgets.QVBoxLayout(grp_auto)
        lay_auto.setContentsMargins(8, 8, 8, 8)
        lay_auto.setSpacing(6)

        # Preset combo
        row_preset = QtWidgets.QHBoxLayout()
        row_preset.addWidget(QtWidgets.QLabel("Preset"))
        self._combo = QtWidgets.QComboBox()
        self._combo.addItems(self._PRESETS)
        self._combo.setToolTip("Load a preset token order")
        row_preset.addWidget(self._combo, 1)
        lay_auto.addLayout(row_preset)

        # Token drag-and-drop list
        self._token_list = QtWidgets.QListWidget()
        self._token_list.setFlow(QtWidgets.QListWidget.LeftToRight)
        self._token_list.setDragDropMode(QtWidgets.QAbstractItemView.InternalMove)
        self._token_list.setDefaultDropAction(QtCore.Qt.MoveAction)
        self._token_list.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self._token_list.setFixedHeight(36)
        self._token_list.setWrapping(False)
        self._token_list.setSpacing(3)
        self._token_list.setStyleSheet(self._TOKEN_SS)
        lay_auto.addWidget(self._token_list)

        # Live preview
        self._lbl_preview = QtWidgets.QLabel("")
        self._lbl_preview.setStyleSheet(
            "color: #888888; font-size: 11px; font-style: italic; padding: 0 2px;")
        lay_auto.addWidget(self._lbl_preview)

        # Prefix field
        row_pfx = QtWidgets.QHBoxLayout()
        self._lbl_prefix = QtWidgets.QLabel("Prefix")
        row_pfx.addWidget(self._lbl_prefix)
        self._edit_prefix = QtWidgets.QLineEdit()
        self._edit_prefix.setPlaceholderText("e.g. facial  (optional)")
        self._edit_prefix.setToolTip("Global prefix added to all generated names ({prefix} token)")
        row_pfx.addWidget(self._edit_prefix, 1)
        lay_auto.addLayout(row_pfx)

        # Side tokens
        row_sides = QtWidgets.QHBoxLayout()
        row_sides.addWidget(QtWidgets.QLabel("Sides"))
        for attr, label, tip in (
            ("_edit_side_left",   "Left",   "Token used for the left side  (e.g. L)"),
            ("_edit_side_center", "Center", "Token used for the center side (e.g. C)"),
            ("_edit_side_right",  "Right",  "Token used for the right side  (e.g. R)"),
        ):
            lbl = QtWidgets.QLabel(label)
            lbl.setStyleSheet("color: #888888; font-size: 10px;")
            edit = QtWidgets.QLineEdit()
            edit.setFixedWidth(40)
            edit.setToolTip(tip)
            row_sides.addWidget(lbl)
            row_sides.addWidget(edit)
            setattr(self, attr, edit)
        row_sides.addStretch()
        lay_auto.addLayout(row_sides)

        # Wire signals
        self._combo.currentTextChanged.connect(self._populate_tokens_from_preset)
        self._token_list.model().rowsMoved.connect(lambda *_: self._refresh_preview())
        self._edit_prefix.textChanged.connect(lambda _: self._refresh_prefix_state())
        self._edit_prefix.textChanged.connect(lambda _: self._refresh_preview())

        root.addWidget(grp_auto)

        # ── Opposite Target — Naming Pairs ─────────────────────────────────
        grp_pairs = QtWidgets.QGroupBox("Opposite Target — Naming Pairs")
        grp_pairs.setStyleSheet("QGroupBox { font-size: 11px; }")
        lay_pairs = QtWidgets.QVBoxLayout(grp_pairs)
        lay_pairs.setContentsMargins(8, 8, 8, 8)
        lay_pairs.setSpacing(6)

        lbl_info = QtWidgets.QLabel(
            "Built-in pairs are shown in grey (read-only). "
            "Custom pairs extend Create Opposite Target recognition.")
        lbl_info.setStyleSheet("color: #888888; font-size: 10px; font-style: italic;")
        lbl_info.setWordWrap(True)
        lay_pairs.addWidget(lbl_info)

        self._tabs   = QtWidgets.QTabWidget()
        self._tables = {}
        self._add_fields = {}

        for axis in self._AXES:
            tab = QtWidgets.QWidget()
            tab_lay = QtWidgets.QVBoxLayout(tab)
            tab_lay.setSpacing(6)
            tab_lay.setContentsMargins(6, 8, 6, 6)

            tbl = QtWidgets.QTableWidget(0, 3)
            tbl.setHorizontalHeaderLabels(["Token A", "Token B", ""])
            tbl.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
            tbl.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.Stretch)
            tbl.horizontalHeader().setSectionResizeMode(2, QtWidgets.QHeaderView.Fixed)
            tbl.setColumnWidth(2, 30)
            tbl.verticalHeader().setVisible(False)
            tbl.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
            tbl.setSelectionMode(QtWidgets.QAbstractItemView.NoSelection)
            tbl.setAlternatingRowColors(True)
            self._tables[axis] = tbl

            for pair in self._BUILTIN.get(axis, []):
                self._add_row(tbl, pair[0], pair[1], builtin=True)
            for pair in self._user_data.get(axis, []):
                self._add_row(tbl, pair[0], pair[1], builtin=False)

            tab_lay.addWidget(tbl)

            add_row = QtWidgets.QHBoxLayout()
            edit_a = QtWidgets.QLineEdit()
            edit_a.setPlaceholderText("Token A  (e.g. brow_up)")
            edit_b = QtWidgets.QLineEdit()
            edit_b.setPlaceholderText("Token B  (e.g. brow_dn)")
            btn_add = QtWidgets.QPushButton("Add")
            btn_add.setFixedWidth(50)
            btn_add.setToolTip("Add this pair")
            self._add_fields[axis] = (edit_a, edit_b)
            btn_add.clicked.connect(lambda _=False, ax=axis: self._on_add(ax))
            edit_a.returnPressed.connect(lambda ax=axis: self._on_add(ax))
            edit_b.returnPressed.connect(lambda ax=axis: self._on_add(ax))
            add_row.addWidget(edit_a, 1)
            add_row.addWidget(edit_b, 1)
            add_row.addWidget(btn_add)
            tab_lay.addLayout(add_row)

            self._tabs.addTab(tab, axis.replace("Object ", "Axis "))

        lay_pairs.addWidget(self._tabs)
        root.addWidget(grp_pairs)

        # ── Save / Cancel ──────────────────────────────────────────────────
        btn_row = QtWidgets.QHBoxLayout()
        btn_row.addStretch()
        btn_save = QtWidgets.QPushButton("Save")
        btn_save.setFixedWidth(70)
        btn_save.setToolTip("Apply and save")
        btn_save.clicked.connect(self._on_save)
        btn_cancel = QtWidgets.QPushButton("Cancel")
        btn_cancel.setFixedWidth(70)
        btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(btn_save)
        btn_row.addWidget(btn_cancel)
        root.addLayout(btn_row)

    # ── Init from parent state ─────────────────────────────────────────────
    def _init_from_parent(self):
        self._edit_prefix.setText(self._parent_ui._nom_prefix)
        self._populate_tokens_direct(self._parent_ui._nom_token_order)
        self._edit_side_left.setText(self._parent_ui._nom_side_left)
        self._edit_side_center.setText(self._parent_ui._nom_side_center)
        self._edit_side_right.setText(self._parent_ui._nom_side_right)
        self._refresh_prefix_state()
        self._refresh_preview()

    def _populate_tokens_from_preset(self, pattern):
        tokens = [t for t in pattern.split("_") if t.startswith("{")]
        self._populate_tokens_direct(tokens)

    def _populate_tokens_direct(self, token_order):
        self._token_list.clear()
        for tok in token_order:
            item = QtWidgets.QListWidgetItem(tok)
            item.setTextAlignment(QtCore.Qt.AlignCenter)
            self._token_list.addItem(item)
        self._refresh_prefix_state()
        self._refresh_preview()

    def _refresh_prefix_state(self):
        has = any(
            self._token_list.item(i).text() == "{prefix}"
            for i in range(self._token_list.count())
        )
        self._edit_prefix.setEnabled(has)
        self._lbl_prefix.setEnabled(has)

    def _refresh_preview(self):
        tokens = [self._token_list.item(i).text()
                  for i in range(self._token_list.count())]
        pfx = self._edit_prefix.text().strip()
        example = {
            "{prefix}": pfx or "Facial",
            "{side}":   "R",
            "{target}": "cheekbone",
            "{suffix}": "up",
        }
        parts = [example[tok] for tok in tokens if example.get(tok)]
        self._lbl_preview.setText("Preview  -> e.g. " + "_".join(parts))

    # ── Pairs table helpers ────────────────────────────────────────────────
    def _add_row(self, tbl, token_a, token_b, builtin=False):
        row = tbl.rowCount()
        tbl.insertRow(row)
        item_a = QtWidgets.QTableWidgetItem(token_a)
        item_b = QtWidgets.QTableWidgetItem(token_b)
        if builtin:
            grey = QtGui.QColor("#606060")
            item_a.setForeground(grey)
            item_b.setForeground(grey)
            item_a.setToolTip("Built-in — read only")
            item_b.setToolTip("Built-in — read only")
            tbl.setItem(row, 0, item_a)
            tbl.setItem(row, 1, item_b)
            tbl.setItem(row, 2, QtWidgets.QTableWidgetItem(""))
        else:
            tbl.setItem(row, 0, item_a)
            tbl.setItem(row, 1, item_b)
            btn_del = QtWidgets.QPushButton("✕")
            btn_del.setFixedSize(22, 20)
            btn_del.setStyleSheet(
                "QPushButton { color: #cc5555; font-size: 10px; border: none; }"
                "QPushButton:hover { color: #ff4444; }"
            )
            btn_del.setToolTip("Remove this pair")
            btn_del.clicked.connect(lambda _=False, t=tbl, b=btn_del: self._on_delete(t, b))
            cell_w = QtWidgets.QWidget()
            cell_lay = QtWidgets.QHBoxLayout(cell_w)
            cell_lay.setContentsMargins(3, 0, 3, 0)
            cell_lay.addWidget(btn_del)
            tbl.setCellWidget(row, 2, cell_w)

    def _on_delete(self, tbl, btn):
        for row in range(tbl.rowCount()):
            w = tbl.cellWidget(row, 2)
            if w and btn in w.findChildren(QtWidgets.QPushButton):
                tbl.removeRow(row)
                return

    def _on_add(self, axis):
        edit_a, edit_b = self._add_fields[axis]
        token_a = edit_a.text().strip()
        token_b = edit_b.text().strip()
        if not token_a or not token_b:
            return
        self._add_row(self._tables[axis], token_a, token_b, builtin=False)
        edit_a.clear()
        edit_b.clear()
        edit_a.setFocus()

    # ── Save ───────────────────────────────────────────────────────────────
    def _on_save(self):
        # Write auto-naming state back to parent
        self._parent_ui._nom_token_order = [
            self._token_list.item(i).text()
            for i in range(self._token_list.count())
        ]
        self._parent_ui._nom_prefix = self._edit_prefix.text().strip()
        self._parent_ui._nom_side_left   = self._edit_side_left.text().strip()   or "L"
        self._parent_ui._nom_side_center = self._edit_side_center.text().strip() or "C"
        self._parent_ui._nom_side_right  = self._edit_side_right.text().strip()  or "R"

        # Save naming pairs
        result = {}
        for axis in self._AXES:
            tbl = self._tables[axis]
            builtin_count = len(self._BUILTIN.get(axis, []))
            user_pairs = []
            for row in range(builtin_count, tbl.rowCount()):
                a = tbl.item(row, 0)
                b = tbl.item(row, 1)
                if a and b and a.text() and b.text():
                    user_pairs.append([a.text(), b.text()])
            if user_pairs:
                result[axis] = user_pairs
        _save_user_duos(result)
        self.accept()


class BlendshapeEditorUI(MayaQWidgetDockableMixin, QtWidgets.QWidget):

    TOOL_NAME = "BlendshapeEditorUI"
    VERSION   = "v.05.54"

    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.setObjectName(self.TOOL_NAME)
        self.setWindowTitle("Blendshape Editor Tool")
        # Shelf width: 9 btns×36 + 2 seps×6 + 10 gaps×2 + margins 4+4
        # + scrollbar width (~12 px) so scroll content aligns with shelf buttons
        _SHELF_W = 9 * 36 + 2 * 6 + 10 * 2 + 8 + 12  # 376 px
        _DEFAULT_H = 900
        self.setMinimumWidth(_SHELF_W)
        self._corrective_delete_mesh = False
        # Create Opposite axis state (set via right-click context menu on the shelf button)
        self._opp_axis = "Object X"
        self._opp_topo_edge = None
        # Naming convention state — edited via Naming Convention dialog
        self._nom_token_order = ["{side}", "{target}", "{suffix}"]
        self._nom_prefix = ""
        self._nom_side_left   = "L"
        self._nom_side_center = "C"
        self._nom_side_right  = "R"
        self._sel_changed_job = None
        self._cached_phantom_count = 0
        self._edit_poll_state = None
        self._last_sculpt_hook = None
        self._sculpt_hook_mel_path = None
        self._build_ui()
        self.resize(_SHELF_W, _DEFAULT_H)
        self._mouse_filter = _GlobalMouseReleaseFilter(self._refresh_top_status, self)
        QtWidgets.QApplication.instance().installEventFilter(self._mouse_filter)
        self._refresh_top_status(check_phantoms=True)
        import blendshape_ui as _bse_mod
        _bse_mod._sculpt_idx_callback = self._on_sculpt_idx_changed
        self._install_sculpt_hook()


    def keyPressEvent(self, event):
        """Route Ctrl+Z / Ctrl+Y to focused QLineEdit; block Maya undo."""
        focused = QtWidgets.QApplication.focusWidget()
        if event.modifiers() == QtCore.Qt.ControlModifier:
            if event.key() == QtCore.Qt.Key_Z:
                if isinstance(focused, QtWidgets.QLineEdit):
                    focused.undo()
                event.accept()
                return
            if event.key() == QtCore.Qt.Key_Y:
                if isinstance(focused, QtWidgets.QLineEdit):
                    focused.redo()
                event.accept()
                return
        super().keyPressEvent(event)

    def closeEvent(self, event):
        self._uninstall_sculpt_hook()
        import blendshape_ui as _bse_mod
        _bse_mod._sculpt_idx_callback = None
        QtWidgets.QApplication.instance().removeEventFilter(self._mouse_filter)
        super().closeEvent(event)

    # ── setSculptTargetIndex hook ──────────────────────────────────────────────

    def _install_sculpt_hook(self):
        try:
            import re
            result = mel.eval('whatIs "setSculptTargetIndex"')
            if 'found in:' not in str(result):
                return
            mel_path = result.split('found in:')[-1].strip()
            self._sculpt_hook_mel_path = mel_path
            with open(mel_path, 'r') as f:
                src = f.read()
            # Parse exact signature and parameter names from the source file
            m = re.search(r'global\s+proc\s+setSculptTargetIndex\s*(\([^)]*\))', src)
            if not m:
                return
            sig = m.group(1)                          # "(string $node, int $idx, ...)"
            params = re.findall(r'\$\w+', sig)        # ['$node', '$idx', ...]
            call_args = ', '.join(params)
            node_p, idx_p = params[0], params[1]
            # Define renamed original with exact signature
            renamed = src.replace('proc setSculptTargetIndex',
                                  'proc _bse_orig_setSculptTargetIndex', 1)
            mel.eval(renamed)
            # Define wrapper with the same signature — single-line to avoid MEL multiline issues
            mel.eval(
                f'global proc setSculptTargetIndex{sig} {{'
                f' python("import blendshape_ui; blendshape_ui._on_sculpt_target_index_changed(\'" + {node_p} + "\', " + string({idx_p}) + ")");'
                f' _bse_orig_setSculptTargetIndex({call_args});'
                f' }}'
            )
        except Exception:
            pass

    def _uninstall_sculpt_hook(self):
        try:
            if self._sculpt_hook_mel_path:
                mel.eval(f'source "{self._sculpt_hook_mel_path}"')
                self._sculpt_hook_mel_path = None
        except Exception:
            pass

    def _on_sculpt_idx_changed(self, bs_node, idx):
        """Fired by the MEL hook. idx == -1 means edit mode off."""
        # Always record the raw hook state so _refresh_top_status can read it
        # even when _edit_poll_state is not yet initialised.
        self._last_sculpt_hook = (bs_node, idx)
        current = self._edit_poll_state
        if not current or current[0] != bs_node:
            return
        edit_on = (idx == current[1])
        self._apply_edit_btn_style(edit_on)
        self._edit_poll_state = (current[0], current[1], edit_on)

    def _apply_edit_btn_style(self, edit_on):
        if edit_on:
            self._btn_edit_target.setStyleSheet(
                "QPushButton { background-color: #ff0000; color: white; border: none; }"
                "QPushButton:hover { background-color: #ff3333; }"
                "QPushButton:pressed { background-color: #cc0000; }"
            )
        else:
            self._btn_edit_target.setStyleSheet(
                "QPushButton { background-color: #5a5a5a; color: #cccccc; border: none; }"
                "QPushButton:hover { background-color: #6a6a6a; }"
                "QPushButton:pressed { background-color: #4a4a4a; }"
                "QPushButton:disabled { background-color: #3a3a3a; color: #666666; border: none; }"
            )

    def _set_weight_widgets(self, enabled, value, tip):
        """Sync weight slider without triggering Maya writes."""
        self._slider_target_weight.blockSignals(True)
        self._slider_target_weight.setValue(int(round(value * 1000)))
        self._slider_target_weight.setEnabled(enabled and not self._vis_is_hidden())
        self._slider_target_weight.setToolTip(tip)
        self._slider_target_weight.blockSignals(False)

    def _on_target_weight_slider(self, int_val):
        v = int_val / 1000.0
        state = self._edit_poll_state
        if state:
            try:
                cmds.setAttr(f"{state[0]}.weight[{state[1]}]", v)
            except Exception:
                pass

    def _vis_is_hidden(self):
        b = getattr(self, '_btn_target_vis', None)
        return b is not None and b.isChecked()

    def _on_target_vis_toggled(self, checked):
        """Toggle blendShape target visibility. checked=True ->hidden (black dot)."""
        state = getattr(self, '_edit_poll_state', None)
        if state:
            import maya.mel as mel
            mel.eval(f'blendShapeToggleVisibility {state[0]} {state[1]} ""')
        hidden = checked
        pix = self._pix_tgt_off if hidden else self._pix_tgt_on
        if not pix.isNull():
            self._icon_target_lbl.setPixmap(pix)
        self._lbl_active_target.setStyleSheet(
            "color: #555555;" if hidden else "color: #aaaaaa;")
        self._slider_target_weight.setEnabled(not hidden and state is not None)
        self._btn_edit_target.setEnabled(not hidden and state is not None)

    def _toggle_edit_mode(self):
        state = getattr(self, '_edit_poll_state', None)
        if not state:
            return
        bs_node, idx, edit_on = state
        if edit_on:
            mel.eval(f'catchQuiet(`sculptTarget -e -target -1 "{bs_node}"`)')
        else:
            mel.eval(f'catchQuiet(`blendShape -e -w {idx} 1.0 "{bs_node}"`)')
            mel.eval(f'catchQuiet(`sculptTarget -e -target {idx} "{bs_node}"`)')

    # ── end setSculptTargetIndex hook ─────────────────────────────────────────

    def _icon_btn(self, icon_path, label, tooltip=""):
        """
        Maya-style icon+button: icon in a QLabel on the left, QPushButton text-only on the right.
        Both share the same height and border so they look like one unified control.
        """
        ICON_SIZE  = 32
        BTN_HEIGHT = 34

        container = QtWidgets.QWidget()
        container.setFixedHeight(BTN_HEIGHT)
        layout = QtWidgets.QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        lbl = QtWidgets.QLabel()
        pixmap = QtGui.QPixmap(icon_path)
        if not pixmap.isNull():
            lbl.setPixmap(pixmap.scaled(
                ICON_SIZE, ICON_SIZE,
                QtCore.Qt.KeepAspectRatio,
                QtCore.Qt.SmoothTransformation
            ))
        lbl.setFixedSize(ICON_SIZE + 8, BTN_HEIGHT)
        lbl.setAlignment(QtCore.Qt.AlignCenter)
        lbl.setStyleSheet("""
            QLabel {
                background-color: transparent;
                border: none;
            }
        """)
        if tooltip:
            lbl.setToolTip(tooltip)

        btn = QtWidgets.QPushButton(label)
        btn.setFixedHeight(BTN_HEIGHT)
        if tooltip:
            btn.setToolTip(tooltip)

        layout.addWidget(lbl)
        layout.addWidget(btn, 1)
        return container, btn

    def _align_label_icon_btns(self, containers):
        """Set all text labels inside _label_icon_btn containers to the same width (widest)
        so their icon buttons align vertically within a section."""
        labels = [c.layout().itemAt(0).widget() for c in containers]
        fm = labels[0].fontMetrics()
        max_w = max(fm.horizontalAdvance(lbl.text()) for lbl in labels)
        for lbl in labels:
            lbl.setFixedWidth(max_w + 6)

    def _label_icon_btn(self, icon_path, label, tooltip=""):
        """
        Inverted icon+button: QLabel text on the left (expanding), QToolButton icon on the right (shelf-style).
        Same return signature as _icon_btn: (container, btn).
        """
        BTN_HEIGHT = 34
        ICON_SIZE  = 32

        container = QtWidgets.QWidget()
        container.setFixedHeight(BTN_HEIGHT)
        layout = QtWidgets.QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        lbl = QtWidgets.QLabel(label)
        lbl.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
        layout.addWidget(lbl)

        btn = QtWidgets.QToolButton()
        btn.setFixedSize(BTN_HEIGHT + 2, BTN_HEIGHT)
        btn.setIconSize(QtCore.QSize(ICON_SIZE, ICON_SIZE))
        btn.setAutoRaise(True)
        btn.setStyleSheet("""
            QToolButton {
                background-color: transparent;
                border: none;
                border-radius: 3px;
                padding: 1px;
            }
            QToolButton:hover   { background-color: rgba(255,255,255,30); }
            QToolButton:pressed { background-color: rgba(0,0,0,40); }
        """)
        pix = QtGui.QPixmap(icon_path)
        if not pix.isNull():
            btn.setIcon(QtGui.QIcon(pix))
        if tooltip:
            btn.setToolTip(tooltip)

        layout.addWidget(btn)
        layout.addStretch()
        return container, btn

    def _collapsible_section(self, title, expanded=True, two_state=False, initial_state=None, compact_rows=1):
        """
        3-state collapsible section:
          State 2 — Full    : header ▼  + full body content
          State 1 — Compact : header ▼· + compact shelf (icon-only buttons)
          State 0 — Closed  : header ▶  (nothing visible)

        Left-click cycles 2→1→0→2.
        Returns (outer_widget, body_widget, body_layout).
        Call section.add_compact_action(icon_path, tooltip, callback) after building
        content to register compact shelf buttons.
        """
        HEADER_STYLE = """
            QToolButton {
                background-color: rgba(255,255,255,28);
                border: none;
                border-radius: 2px;
                font-weight: bold;
                text-align: left;
                padding-left: 4px;
            }
            QToolButton:hover { background-color: rgba(255,255,255,38); }
        """
        SHELF_BTN_STYLE = """
            QToolButton {
                background-color: transparent;
                border: none;
                border-radius: 3px;
                padding: 2px;
            }
            QToolButton:hover { background-color: rgba(255,255,255,30); }
            QToolButton:pressed { background-color: rgba(0,0,0,40); }
        """

        outer     = QtWidgets.QWidget()
        outer_lay = QtWidgets.QVBoxLayout(outer)
        outer_lay.setContentsMargins(0, 0, 0, 0)
        outer_lay.setSpacing(0)

        # ── Header ────────────────────────────────────────────────────────
        header = QtWidgets.QToolButton()
        header.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)
        header.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        header.setFixedHeight(22)
        header.setStyleSheet(HEADER_STYLE)

        # ── Compact shelf ─────────────────────────────────────────────────
        shelf_widget = QtWidgets.QWidget()

        if compact_rows >= 2:
            # Grid mode: buttons placed in zigzag order (row = idx%compact_rows, col = idx//compact_rows)
            shelf_grid = QtWidgets.QGridLayout(shelf_widget)
            shelf_grid.setContentsMargins(4, 2, 4, 2)
            shelf_grid.setSpacing(4)
            shelf_btn_idx = [0]
        else:
            # Single/multi-row mode with manual row breaks
            shelf_vlay = QtWidgets.QVBoxLayout(shelf_widget)
            shelf_vlay.setContentsMargins(4, 2, 4, 2)
            shelf_vlay.setSpacing(2)

            def _new_shelf_row():
                row = QtWidgets.QHBoxLayout()
                row.setSpacing(4)
                row.addStretch()
                shelf_vlay.addLayout(row)
                return row

            shelf_cur_row = [_new_shelf_row()]

        # ── Full body ─────────────────────────────────────────────────────
        body     = QtWidgets.QWidget()
        body_lay = QtWidgets.QVBoxLayout(body)
        body_lay.setContentsMargins(0, 4, 0, 4)
        body_lay.setSpacing(4)

        # State: 2=full, 1=compact, 0=closed
        state      = [initial_state if initial_state is not None else (2 if expanded else 0)]
        prev_state = [None]

        ARROWS = {2: QtCore.Qt.DownArrow, 1: QtCore.Qt.DownArrow, 0: QtCore.Qt.RightArrow}
        LABELS = {2: f"  {title}", 1: f"  {title}", 0: f"  {title}"}

        persistent_widgets = []  # shown in states 1+2, hidden in state 0

        def _apply_state(s):
            header.setArrowType(ARROWS[s])
            header.setText(LABELS[s])
            shelf_widget.setVisible(s == 1)
            body.setVisible(s == 2)
            for w in persistent_widgets:
                w.setVisible(s in (1, 2))

        def _on_click():
            cur = state[0]
            if two_state:
                nxt = 2 if cur == 0 else 0
            elif initial_state == 1:
                # Bounce cycle: 1→2→1→0→1→2→...
                # 2 and 0 always return to compact (1).
                # From compact, direction depends on where we came from:
                #   came from open (2) ->go to closed (0)
                #   otherwise         ->go to open (2)
                if cur in (2, 0):
                    nxt = 1
                else:
                    nxt = 0 if prev_state[0] == 2 else 2
            else:
                nxt = (cur - 1) % 3
            prev_state[0] = cur
            state[0] = nxt
            _apply_state(nxt)

        header.clicked.connect(_on_click)
        _apply_state(state[0])

        outer_lay.addWidget(header)
        outer_lay.addWidget(shelf_widget)
        outer_lay.addWidget(body)

        # ── Helpers to register compact actions after UI build ────────────
        def _make_compact_btn(icon_path, tooltip, callback):
            btn = QtWidgets.QToolButton()
            btn.setFixedSize(40, 40)
            btn.setToolButtonStyle(QtCore.Qt.ToolButtonIconOnly)
            btn.setToolTip(tooltip)
            btn.setStyleSheet(SHELF_BTN_STYLE)
            px = QtGui.QPixmap(icon_path)
            if not px.isNull():
                scaled = px.scaled(32, 32,
                                   QtCore.Qt.KeepAspectRatio,
                                   QtCore.Qt.SmoothTransformation)
                btn.setIcon(QtGui.QIcon(scaled))
                btn.setIconSize(QtCore.QSize(32, 32))
            btn.clicked.connect(callback)
            return btn

        if compact_rows >= 2:
            _pending_compact = []

            def add_compact_action(icon_path, tooltip, callback):
                _pending_compact.append(_make_compact_btn(icon_path, tooltip, callback))

            def add_compact_text_btn(label, tooltip, callback):
                btn = QtWidgets.QToolButton()
                btn.setFixedHeight(40)
                btn.setToolButtonStyle(QtCore.Qt.ToolButtonTextOnly)
                btn.setText(label)
                btn.setToolTip(tooltip)
                btn.setStyleSheet(SHELF_BTN_STYLE)
                btn.clicked.connect(callback)
                _pending_compact.append(btn)

            def add_compact_spacer():
                _pending_compact.append(None)

            _ROW_BREAK = object()

            def add_compact_row_break():
                _pending_compact.append(_ROW_BREAK)

            def finalize_compact():
                if not _pending_compact:
                    return
                row, col, max_col = 0, 0, 0
                for item in _pending_compact:
                    if item is _ROW_BREAK:
                        row += 1
                        col = 0
                    elif item is not None:
                        shelf_grid.addWidget(item, row, col)
                        col += 1
                        if col > max_col:
                            max_col = col
                shelf_grid.setColumnStretch(max_col, 1)
        else:
            def add_compact_action(icon_path, tooltip, callback):
                btn = _make_compact_btn(icon_path, tooltip, callback)
                row = shelf_cur_row[0]
                row.insertWidget(row.count() - 1, btn)

            def add_compact_text_btn(label, tooltip, callback):
                btn = QtWidgets.QToolButton()
                btn.setFixedHeight(40)
                btn.setToolButtonStyle(QtCore.Qt.ToolButtonTextOnly)
                btn.setText(label)
                btn.setToolTip(tooltip)
                btn.setStyleSheet(SHELF_BTN_STYLE)
                btn.clicked.connect(callback)
                row = shelf_cur_row[0]
                row.insertWidget(row.count() - 1, btn)

            def add_compact_spacer():
                pass  # no-op in single-row mode

            def add_compact_row_break():
                shelf_cur_row[0] = _new_shelf_row()

            def finalize_compact():
                pass  # no-op for single-row mode

        def add_persistent_widget(widget):
            """Insert a widget just below the header, visible in states 1 and 2, hidden in 0."""
            # position 1 = after header, before shelf_widget
            outer_lay.insertWidget(1, widget)
            persistent_widgets.append(widget)
            widget.setVisible(state[0] in (1, 2))

        outer.add_compact_action    = add_compact_action
        outer.add_compact_text_btn  = add_compact_text_btn
        outer.add_compact_spacer    = add_compact_spacer
        outer.add_compact_row_break = add_compact_row_break
        outer.finalize_compact      = finalize_compact
        outer.compact_shelf         = shelf_widget
        outer.add_persistent_widget = add_persistent_widget

        return outer, body, body_lay

    def _build_ui(self):
        import maya.cmds as _cmds
        _icons_dir = _cmds.internalVar(userAppDir=True) + "prefs/icons"


        # ── Outer layout + menu bar (fixes, hors scroll) ──────────────────
        outer_layout = QtWidgets.QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(0)

        # ── Menu bar ──────────────────────────────────────────────────────
        menu_bar = QtWidgets.QMenuBar(self)
        _mb_font = menu_bar.font()
        _mb_font.setPointSize(8)
        menu_bar.setFont(_mb_font)
        menu_edit = menu_bar.addMenu("Edit")
        act_reset = menu_edit.addAction("Reset Default Options")
        act_reset.setToolTip("Restore all split options to their default values")
        act_reset.triggered.connect(self._reset_default_options)
        menu_edit.addSeparator()
        act_naming = menu_edit.addAction("Naming Convention…")
        act_naming.setToolTip(
            "Configure token order, prefix, and custom naming pairs\n"
            "used by the tool when generating target names.")
        act_naming.triggered.connect(self._open_naming_convention)
        menu_edit.addSeparator()
        act_clean_bs = menu_edit.addAction("Clean Blendshape Node")
        act_clean_bs.setToolTip(
            "Removes phantom (empty/unaliased) target slots from the blendShape node(s)\n"
            "of the selected targets in the Shape Editor.")
        act_clean_bs.triggered.connect(self._run_clean_bs)
        act_clean_mesh = menu_edit.addAction("Clean Deformed Mesh")
        act_clean_mesh.setToolTip(
            "Removes residual sculpt color sets (SculptFreezeColorTemp, SculptMaskColorTemp)\n"
            "and any leftover unnamed sets, then bakes non-deformer history\n"
            "on the selected mesh(es).")
        act_clean_mesh.triggered.connect(self._run_clean_deformed_mesh)
        menu_edit.addSeparator()
        act_doc = menu_edit.addAction("Documentation")
        act_doc.setToolTip("Open the online documentation in your web browser")
        act_doc.triggered.connect(lambda: QtGui.QDesktopServices.openUrl(
            QtCore.QUrl("https://blendshape-editor-tool.readthedocs.io")))
        menu_check = menu_bar.addMenu("Check Shapes")
        act_check = menu_check.addAction("Open Check Shapes…")
        act_check.setToolTip("Open the Check Shapes dialog to verify expected targets on a blendShape node")
        act_check.triggered.connect(self._open_check_shapes)
        menu_rig = menu_bar.addMenu("Rig Connector")
        act_rig = menu_rig.addAction("Open Rig Connector…")
        act_rig.setToolTip("Open the Rig Connector to map FK controllers to blendShape targets")
        act_rig.triggered.connect(self._open_rig_connector)
        outer_layout.setMenuBar(menu_bar)

        # ── Scroll area ───────────────────────────────────────────────────
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)

        inner = QtWidgets.QWidget()
        _app_pal   = QtWidgets.QApplication.palette()
        _inner_pal = QtGui.QPalette(_app_pal)
        _inner_pal.setColor(QtGui.QPalette.Window, QtGui.QColor("#404040"))
        inner.setPalette(_inner_pal)
        inner.setAutoFillBackground(True)
        root = QtWidgets.QVBoxLayout(inner)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(10)

        # ── Maya Tools Shelf ──────────────────────────────────────────────
        shelf_frame = QtWidgets.QFrame()
        shelf_frame.setFrameShape(QtWidgets.QFrame.NoFrame)
        shelf_frame.setPalette(_app_pal)
        shelf_frame.setAutoFillBackground(True)
        shelf_frame.setFixedHeight(80)
        shelf_vlay = QtWidgets.QVBoxLayout(shelf_frame)
        shelf_vlay.setContentsMargins(4, 3, 4, 3)
        shelf_vlay.setSpacing(2)

        shelf_lay = QtWidgets.QHBoxLayout()   # row 1 — sculpt / shape tools
        shelf_lay.setContentsMargins(0, 0, 0, 0)
        shelf_lay.setSpacing(2)
        shelf_vlay.addLayout(shelf_lay)

        row2_lay = QtWidgets.QHBoxLayout()    # row 2 — node-level tools
        row2_lay.setContentsMargins(0, 0, 0, 0)
        row2_lay.setSpacing(2)
        shelf_vlay.addLayout(row2_lay)

        def _shelf_btn(icon_path, tooltip, mel_cmd=None, callback=None, dbl_click=None):
            btn = QtWidgets.QToolButton()
            btn.setFixedSize(36, 36)
            btn.setIconSize(QtCore.QSize(34, 34))
            btn.setAutoRaise(True)
            btn.setStyleSheet("""
                QToolButton {
                    background-color: transparent;
                    border: none;
                    border-radius: 3px;
                    padding: 2px;
                }
                QToolButton:hover   { background-color: rgba(255,255,255,30); }
                QToolButton:pressed { background-color: rgba(0,0,0,40); }
            """)
            btn.setToolTip(tooltip)
            pix = QtGui.QPixmap(icon_path)
            if not pix.isNull():
                btn.setIcon(QtGui.QIcon(pix))
            if mel_cmd:
                btn.clicked.connect(lambda _=False, cmd=mel_cmd: mel.eval(cmd))
            elif callback:
                btn.clicked.connect(callback)
            if dbl_click:
                _f = _DblClickFilter(dbl_click, btn)
                btn.installEventFilter(_f)
            return btn

        def _vsep(layout):
            layout.addStretch(1)
            sep = QtWidgets.QFrame()
            sep.setFrameShape(QtWidgets.QFrame.VLine)
            sep.setFrameShadow(QtWidgets.QFrame.Sunken)
            sep.setFixedWidth(6)
            layout.addWidget(sep)
            layout.addStretch(1)

        # ── Row 1 ─────────────────────────────────────────────────────────
        shelf_lay.addStretch(1)
        # Sculpt tools (4)
        for _ic, _tt, _cmd in [
            (f"{_icons_dir}/Grab.png",         "Sculpt Grab\nDouble-click: Tool Settings",         "SetMeshGrabTool"),
            (f"{_icons_dir}/Flatten.png",       "Sculpt Flatten\nDouble-click: Tool Settings",       "SetMeshFlattenTool"),
            (f"{_icons_dir}/Bulge.png",         "Sculpt Bulge\nDouble-click: Tool Settings",         "SetMeshBulgeTool"),
            (f"{_icons_dir}/SmoothTarget.png",  "Smooth Target\nDouble-click: Tool Settings",        "SetMeshSmoothTargetTool"),
        ]:
            shelf_lay.addWidget(_shelf_btn(_ic, _tt,
                callback=lambda _=False, c=_cmd: self._activate_sculpt_tool(c),
                dbl_click=self._open_tool_settings))
            shelf_lay.addStretch(1)

        _vsep(shelf_lay)

        # Node tools (3)
        shelf_lay.addWidget(_shelf_btn(
            f"{_icons_dir}/blendShapeEditor.png", "Shape Editor", mel_cmd="ShapeEditor"))
        shelf_lay.addStretch(1)
        self.btn_delta_mush = _shelf_btn(
            f"{_icons_dir}/deltaMush_cleaner.png",
            "Delta Mush Cleaner\n"
            "Creates a cleaning Delta Mush to remove residual deltas and relax your shapes\n"
            "in the best possible way.\n"
            "Especially useful on complex meshes with heavy bevels, where keeping consistent\n"
            "vertex distances is difficult.\n"
            "Primarily used alongside the Bake Deformers tool.\n"
            "It is recommended to delete this node once your targets are cleaned,\n"
            "before publishing your rig.",
            callback=self._run_delta_mush_cleaner)
        shelf_lay.addWidget(self.btn_delta_mush)
        shelf_lay.addStretch(1)
        self.btn_reset_weights = _shelf_btn(
            f"{_icons_dir}/reset_bsnode.png",
            "Reset All Targets to 0\n"
            "Sets every target weight on the blendShape node(s) to 0.\n"
            "Requires at least one target selected in the Shape Editor.",
            callback=self._run_reset_all_weights)
        shelf_lay.addWidget(self.btn_reset_weights)

        _vsep(shelf_lay)

        # Delta tools (1)
        self.btn_exit_delta_view = _shelf_btn(
            f"{_icons_dir}/exit_delta.png",
            "Exit Delta View — restore original vertex colors",
            callback=self._exit_delta_view)
        self.btn_exit_delta_view.setEnabled(False)
        shelf_lay.addWidget(self.btn_exit_delta_view)
        shelf_lay.addStretch(1)

        # ── Row 2 ─────────────────────────────────────────────────────────
        row2_lay.addStretch(1)
        # Sculpt tools (4)
        for _ic, _tt, _cmd in [
            (f"{_icons_dir}/Smooth.png",  "Smooth\nDouble-click: Tool Settings",  "SetMeshSmoothTool"),
            (f"{_icons_dir}/Relax.png",   "Relax\nDouble-click: Tool Settings",   "SetMeshRelaxTool"),
            (f"{_icons_dir}/Pinch.png",   "Pinch\nDouble-click: Tool Settings",   "SetMeshPinchTool"),
            (f"{_icons_dir}/Erase.png",   "Erase Target\nDouble-click: Tool Settings", "SetMeshEraseTool"),
        ]:
            row2_lay.addWidget(_shelf_btn(_ic, _tt,
                callback=lambda _=False, c=_cmd: self._activate_sculpt_tool(c),
                dbl_click=self._open_tool_settings))
            row2_lay.addStretch(1)

        _vsep(row2_lay)

        # Node tools (3)
        self.btn_add_target = _shelf_btn(
            f"{_icons_dir}/add_target.png",
            "Add Target  [left-click]\n"
            "  Add an empty target to the blendshape(s) of selected targets in the Shape Editor.\n"
            "\n"
            "Right-click for more options:\n"
            "  • Add Empty Target — same as left-click\n"
            "  • Add Selection as New Target — select source mesh(es) + target mesh (last)\n"
            "  • Add Selection as New Corrective Target — select corrective mesh(es) + target mesh (last),\n"
            "    inverts the deformation stack via invertShape()",
            callback=self._run_add_target)
        self.btn_add_target.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.btn_add_target.customContextMenuRequested.connect(self._show_add_target_context_menu)
        row2_lay.addWidget(self.btn_add_target)
        row2_lay.addStretch(1)
        self.btn_create_opposite = _shelf_btn(
            f"{_icons_dir}/create_opposite.png",
            "Create Opposite Target\n"
            "Duplicates the target, flips it and renames it with the opposite\n"
            "naming convention (L_/R_, lft/rgt, up/dn, fwd/bwd …).\n"
            "Right-click to choose the symmetry axis.\n"
            f"Current axis: {self._opp_axis}",
            callback=self._run_opposite)
        self.btn_create_opposite.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.btn_create_opposite.customContextMenuRequested.connect(self._opp_axis_menu)
        row2_lay.addWidget(self.btn_create_opposite)
        row2_lay.addStretch(1)
        self.btn_connect_ab = _shelf_btn(
            f"{_icons_dir}/connect_a_b.png",
            "Connect Targets A to B\n"
            "Select two meshes (source first, target second).\n"
            "Finds the blendShape on each and connects every weight attribute\n"
            "that shares the same target name:  bs_A.name  -> bs_B.name",
            callback=self._run_connect_targets_A_to_B)
        row2_lay.addWidget(self.btn_connect_ab)

        _vsep(row2_lay)

        # Delta tools (1)
        self.btn_delta_view = _shelf_btn(
            f"{_icons_dir}/delta_view.png",
            "Delta View — colorize vertices by delta magnitude (black→red→yellow)",
            callback=self._run_delta_view)
        row2_lay.addWidget(self.btn_delta_view)
        row2_lay.addStretch(1)

        # Shelf pinned above the scroll — always visible
        shelf_wrapper = QtWidgets.QWidget()
        shelf_wrapper_lay = QtWidgets.QVBoxLayout(shelf_wrapper)
        shelf_wrapper_lay.setContentsMargins(8, 4, 8, 2)
        shelf_wrapper_lay.setSpacing(4)

        # ── Tool Settings (inline collapsible like Edge Loop Options) ──
        self._ts_toggle = QtWidgets.QToolButton()
        self._ts_toggle.setText("  Tool Settings")
        self._ts_toggle.setArrowType(QtCore.Qt.RightArrow)
        self._ts_toggle.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)
        self._ts_toggle.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        self._ts_toggle.setFixedHeight(18)
        self._ts_toggle.setStyleSheet(
            "QToolButton { background: transparent; border: none; "
            "font-size: 10px; color: #888888; text-align: center; }"
            "QToolButton:hover { color: #aaaaaa; }")

        ts_widget = QtWidgets.QWidget()
        ts_widget.setVisible(False)
        lay_ts = QtWidgets.QVBoxLayout(ts_widget)
        lay_ts.setContentsMargins(12, 2, 0, 0)
        lay_ts.setSpacing(6)

        # Surface/Volume + Symmetry combos
        combos_row = QtWidgets.QHBoxLayout()
        combos_row.setSpacing(4)
        combos_row.addSpacing(4)
        combos_row.addWidget(QtWidgets.QLabel("Falloff"))
        self.combo_tool_surf_vol = QtWidgets.QComboBox()
        for _lbl, _val in [("Surface/Volume", 0), ("Surface", 1), ("Volume", 2)]:
            self.combo_tool_surf_vol.addItem(_lbl, _val)
        self.combo_tool_surf_vol.setCurrentIndex(1)
        self.combo_tool_surf_vol.currentIndexChanged.connect(self._on_tool_surf_vol_changed)
        combos_row.addWidget(self.combo_tool_surf_vol, 1)
        combos_row.addSpacing(16)
        combos_row.addWidget(QtWidgets.QLabel("Symmetry"))
        self.combo_tool_symmetry = QtWidgets.QComboBox()
        for _lbl, _val in [
            ("Off",      0), ("Object X", 4), ("Object Y", 5), ("Object Z", 6),
            ("World X",  1), ("World Y",  2), ("World Z",  3), ("Topology", 7),
        ]:
            self.combo_tool_symmetry.addItem(_lbl, _val)
        self.combo_tool_symmetry.currentIndexChanged.connect(self._on_tool_symmetry_changed)
        combos_row.addWidget(self.combo_tool_symmetry, 1)
        lay_ts.addLayout(combos_row)

        # Strength slider + spinbox
        strength_row = QtWidgets.QHBoxLayout()
        strength_row.setSpacing(4)
        strength_row.addSpacing(4)
        strength_row.addWidget(QtWidgets.QLabel("Strength"))
        self.spin_tool_strength = QtWidgets.QDoubleSpinBox()
        self.spin_tool_strength.setRange(0.0, 100.0)
        self.spin_tool_strength.setDecimals(3)
        self.spin_tool_strength.setValue(50.0)
        self.spin_tool_strength.setFixedWidth(68)
        self.spin_tool_strength.setButtonSymbols(QtWidgets.QAbstractSpinBox.NoButtons)
        self.spin_tool_strength.setLocale(QtCore.QLocale(QtCore.QLocale.English))
        self.spin_tool_strength.valueChanged.connect(self._on_tool_strength_spin)
        strength_row.addWidget(self.spin_tool_strength)
        self.slider_tool_strength = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.slider_tool_strength.setRange(0, 1000)
        self.slider_tool_strength.setValue(500)
        self.slider_tool_strength.valueChanged.connect(self._on_tool_strength_slider)
        strength_row.addWidget(self.slider_tool_strength, 1)
        lay_ts.addLayout(strength_row)

        # ── Connect From File ──────────────────────────────────────────────
        cff_row = QtWidgets.QHBoxLayout()
        cff_row.setSpacing(4)
        btn_rig_browse = QtWidgets.QToolButton()
        btn_rig_browse.setAutoRaise(True)
        btn_rig_browse.setStyleSheet("""
            QToolButton {
                background-color: transparent;
                border: none;
                border-radius: 3px;
                padding: 2px;
            }
            QToolButton:hover   { background-color: rgba(255,255,255,30); }
            QToolButton:pressed { background-color: rgba(0,0,0,40); }
        """)
        _pix_browse = QtGui.QPixmap(f"{_icons_dir}/path.png")
        if not _pix_browse.isNull():
            btn_rig_browse.setIcon(QtGui.QIcon(_pix_browse))
            btn_rig_browse.setIconSize(QtCore.QSize(34, 34))
        btn_rig_browse.setFixedSize(36, 36)
        btn_rig_browse.setToolTip("Browse for a rig mapping JSON file")
        self.line_rig_json = QtWidgets.QLineEdit()
        self.line_rig_json.setReadOnly(True)
        self.line_rig_json.setFixedHeight(23)
        self.line_rig_json.setPlaceholderText("C:/path/to/rig_mapping.json")
        self.btn_rig_connect = QtWidgets.QToolButton()
        btn_rig_connect = self.btn_rig_connect
        btn_rig_connect.setToolButtonStyle(QtCore.Qt.ToolButtonIconOnly)
        btn_rig_connect.setToolTip(
            "Build and connect the rig for all targets defined in the referenced JSON mapping file.\n"
            "The blendShape node is automatically detected from the current mesh selection.")
        btn_rig_connect.setStyleSheet("""
            QToolButton {
                background-color: transparent;
                border: none;
                border-radius: 3px;
                padding: 2px;
            }
            QToolButton:hover   { background-color: rgba(255,255,255,30); }
            QToolButton:pressed { background-color: rgba(0,0,0,40); }
        """)
        _pix_connect = QtGui.QPixmap(f"{_icons_dir}/connect_rig.png")
        if not _pix_connect.isNull():
            btn_rig_connect.setIcon(QtGui.QIcon(_pix_connect))
            btn_rig_connect.setIconSize(QtCore.QSize(34, 34))
        btn_rig_connect.setFixedSize(36, 36)
        cff_row.addWidget(btn_rig_browse)
        cff_row.addWidget(self.line_rig_json, 1)
        cff_row.addWidget(btn_rig_connect)
        shelf_wrapper_lay.addLayout(cff_row)

        # ── Active Target indicator row ────────────────────────────────────────
        active_row = QtWidgets.QHBoxLayout()
        active_row.setSpacing(4)
        active_row.setContentsMargins(0, 0, 0, 0)
        self._icon_target_lbl = QtWidgets.QLabel()
        self._pix_tgt_on  = QtGui.QPixmap(f"{_icons_dir}/target_object_space.png").scaled(
            20, 20, QtCore.Qt.IgnoreAspectRatio, QtCore.Qt.SmoothTransformation)
        # Generate OFF version from ON — guarantees identical pixel dimensions
        _pm_off = QtGui.QPixmap(self._pix_tgt_on.size())
        _pm_off.fill(QtCore.Qt.transparent)
        _p = QtGui.QPainter(_pm_off)
        _p.setOpacity(0.3)
        _p.drawPixmap(0, 0, self._pix_tgt_on)
        _p.end()
        self._pix_tgt_off = _pm_off
        if not self._pix_tgt_on.isNull():
            self._icon_target_lbl.setPixmap(self._pix_tgt_on)
        self._icon_target_lbl.setFixedSize(22, 22)
        self._icon_target_lbl.setAlignment(QtCore.Qt.AlignCenter)
        _icon_lbl = self._icon_target_lbl
        self._lbl_active_target = _ClickableLabel("—")
        self._lbl_active_target.setStyleSheet("color: #aaaaaa;")
        self._lbl_active_target.setSizePolicy(
            QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Fixed)
        self._lbl_active_target.setToolTip(
            "Currently selected target in the Shape Editor.\n"
            "Click to re-select it in the Shape Editor.\n"
            "Updates automatically when the selection changes.")
        self._lbl_active_target.clicked.connect(self._select_active_target_in_shape_editor)
        self._slider_target_weight = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self._slider_target_weight.setMinimum(0)
        self._slider_target_weight.setMaximum(1000)
        self._slider_target_weight.setValue(0)
        self._slider_target_weight.setEnabled(False)
        self._slider_target_weight.setToolTip("Target weight")
        self._slider_target_weight.valueChanged.connect(self._on_target_weight_slider)
        self._btn_edit_target = QtWidgets.QPushButton("Edit")
        self._btn_edit_target.setToolTip(
            "Toggle sculpt edit mode on the active target.\n"
            "Red: edit mode ON — the target is live and ready to sculpt.\n"
            "Gray: edit mode OFF.\n"
            "Stays in sync with the native Shape Editor edit mode.")
        self._btn_edit_target.setFixedWidth(40)
        self._btn_edit_target.setFixedHeight(22)
        self._btn_edit_target.setEnabled(False)
        self._btn_edit_target.setStyleSheet(
            "QPushButton { background-color: #5a5a5a; color: #cccccc; border: none; }"
            "QPushButton:hover { background-color: #6a6a6a; }"
            "QPushButton:pressed { background-color: #4a4a4a; }"
            "QPushButton:disabled { background-color: #3a3a3a; color: #666666; border: none; }"
        )
        self._btn_edit_target.clicked.connect(self._toggle_edit_mode)
        self._btn_target_vis = QtWidgets.QPushButton()
        self._btn_target_vis.setCheckable(True)
        self._btn_target_vis.setChecked(False)
        self._btn_target_vis.setFixedSize(10, 10)
        self._btn_target_vis.setEnabled(False)
        self._btn_target_vis.setToolTip(
            "Toggle target visibility in the Shape Editor.\n"
            "White = visible  ·  Black = hidden")
        self._btn_target_vis.setStyleSheet(
            "QPushButton { background-color: #cccccc; border-radius: 5px; border: none; }"
            "QPushButton:hover:!checked { background-color: #ffffff; }"
            "QPushButton:checked { background-color: #1a1a1a; border-radius: 5px; border: none; }"
            "QPushButton:hover:checked { background-color: #333333; }"
            "QPushButton:disabled { background-color: #505050; border-radius: 5px; border: none; }"
        )
        self._btn_target_vis.toggled.connect(self._on_target_vis_toggled)
        active_row.addWidget(self._btn_target_vis)
        active_row.addWidget(_icon_lbl)
        active_row.addWidget(self._lbl_active_target)
        active_row.addSpacing(5)
        active_row.addWidget(self._slider_target_weight)
        active_row.addWidget(self._btn_edit_target)
        active_inner = QtWidgets.QFrame()
        active_inner.setStyleSheet("QFrame { background-color: #383838; border-radius: 2px; }")
        active_inner_lay = QtWidgets.QHBoxLayout(active_inner)
        active_inner_lay.setContentsMargins(4, 3, 4, 3)
        active_inner_lay.setSpacing(0)
        active_inner_lay.addLayout(active_row)
        # ── end Active Target ──────────────────────────────────────────────────

        btn_rig_connect.setEnabled(False)
        self.line_rig_json.textChanged.connect(
            lambda text: self.btn_rig_connect.setEnabled(bool(text.strip())))
        btn_rig_browse.clicked.connect(self._browse_rig_json)
        btn_rig_connect.clicked.connect(self._run_connect_from_file)

        shelf_wrapper_lay.addWidget(active_inner)
        shelf_wrapper_lay.addWidget(shelf_frame)
        shelf_wrapper_lay.addWidget(self._ts_toggle)
        shelf_wrapper_lay.addWidget(ts_widget)

        def _toggle_ts():
            visible = not ts_widget.isVisible()
            ts_widget.setVisible(visible)
            self._ts_toggle.setArrowType(
                QtCore.Qt.DownArrow if visible else QtCore.Qt.RightArrow)

        self._ts_toggle.clicked.connect(_toggle_ts)
        outer_layout.addWidget(shelf_wrapper)
        outer_layout.addWidget(scroll, 1)

        # ── Auto-resize when vertical scrollbar appears / disappears ──────────
        self._vscroll_visible = False

        def _on_vscroll_range(min_val, max_val):
            if not self.isVisible():
                return
            sb_w    = scroll.verticalScrollBar().sizeHint().width()
            visible = max_val > min_val
            if visible == self._vscroll_visible:
                return
            self._vscroll_visible = visible
            self.resize(self.width() + (sb_w if visible else -sb_w), self.height())

        scroll.verticalScrollBar().rangeChanged.connect(_on_vscroll_range)

        # ── Nomenclature ──────────────────────────────────────────────────────
        grp_nom, _body_nom, lay_nom = self._collapsible_section("Nomenclature", two_state=True, initial_state=0)
        lay_nom.setSpacing(6)

        # ── Rename Targets group ───────────────────────────────────────────
        grp_rename = QtWidgets.QGroupBox("Rename Targets")
        grp_rename.setStyleSheet("QGroupBox { font-size: 11px; }")
        lay_rename = QtWidgets.QVBoxLayout(grp_rename)
        lay_rename.setContentsMargins(8, 6, 8, 6)
        lay_rename.setSpacing(4)

        # ── Rename Tools (Prefix / Suffix / Search & Replace) ─────────────
        _REN_BTN_W = 44
        ren_grid = QtWidgets.QGridLayout()
        ren_grid.setSpacing(4)
        ren_grid.setColumnStretch(1, 1)
        ren_grid.setColumnStretch(3, 1)

        # Row 0 : Pfx / Sfx
        lbl_pfx = QtWidgets.QLabel("Add")
        lbl_pfx.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
        self.edit_rename_pfx = QtWidgets.QLineEdit()
        self.edit_rename_pfx.setPlaceholderText("Prefix_")
        self.edit_rename_pfx.setToolTip("Add a prefix to each selected target name")
        lbl_sfx = QtWidgets.QLabel("_target_")
        lbl_sfx.setAlignment(QtCore.Qt.AlignCenter)
        self.edit_rename_sfx = QtWidgets.QLineEdit()
        self.edit_rename_sfx.setPlaceholderText("_Suffix")
        self.edit_rename_sfx.setToolTip("Add a suffix to each selected target name")
        btn_apply_ps = QtWidgets.QPushButton("Apply")
        btn_apply_ps.setFixedWidth(_REN_BTN_W)
        btn_apply_ps.setToolTip("Apply prefix / suffix to all selected targets")
        btn_apply_ps.clicked.connect(self._run_add_prefix_suffix)
        ren_grid.addWidget(lbl_pfx,             0, 0)
        ren_grid.addWidget(self.edit_rename_pfx, 0, 1)
        ren_grid.addWidget(lbl_sfx,             0, 2)
        ren_grid.addWidget(self.edit_rename_sfx, 0, 3)
        ren_grid.addWidget(btn_apply_ps,         0, 4)

        # Row 1 : Search / Replace
        lbl_search = QtWidgets.QLabel("S&R")
        lbl_search.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
        self.edit_search = QtWidgets.QLineEdit()
        self.edit_search.setPlaceholderText("search")
        self.edit_search.setToolTip("String to find in target names")
        btn_swap_sr = QtWidgets.QPushButton("⇄")
        btn_swap_sr.setFixedSize(30, 30)
        btn_swap_sr.setStyleSheet("font-size: 16px;")
        btn_swap_sr.setToolTip("Swap search and replace texts")
        def _swap_sr():
            a, b = self.edit_search.text(), self.edit_replace.text()
            self.edit_search.setText(b)
            self.edit_replace.setText(a)
        btn_swap_sr.clicked.connect(_swap_sr)
        self.edit_replace = QtWidgets.QLineEdit()
        self.edit_replace.setPlaceholderText("replace")
        self.edit_replace.setToolTip("Replacement string (leave empty to delete)")
        btn_apply_sr = QtWidgets.QPushButton("Apply")
        btn_apply_sr.setFixedWidth(_REN_BTN_W)
        btn_apply_sr.setToolTip("Apply search & replace to all selected target names")
        btn_apply_sr.clicked.connect(self._run_search_replace)
        ren_grid.addWidget(lbl_search,       1, 0)
        ren_grid.addWidget(self.edit_search,  1, 1)
        ren_grid.addWidget(btn_swap_sr,       1, 2)
        ren_grid.addWidget(self.edit_replace, 1, 3)
        ren_grid.addWidget(btn_apply_sr,      1, 4)

        lay_rename.addLayout(ren_grid)

        # ── Swap Target Names ─────────────────────────────────────────────
        # ── Swap Target Names ─────────────────────────────────────────────
        row_swap = QtWidgets.QHBoxLayout()
        row_swap.addStretch(1)
        self.btn_swap_names = QtWidgets.QToolButton()
        self.btn_swap_names.setFixedSize(36, 36)
        self.btn_swap_names.setToolButtonStyle(QtCore.Qt.ToolButtonIconOnly)
        self.btn_swap_names.setToolTip(
            "Swap Target Names\n"
            "Swaps the names of exactly 2 selected targets in the Shape Editor.\n"
            "Select 2 targets, then click — their names are exchanged instantly.")
        self.btn_swap_names.setStyleSheet("""
            QToolButton { background-color: transparent; border: none; border-radius: 3px; padding: 2px; }
            QToolButton:hover { background-color: rgba(255,255,255,30); }
            QToolButton:pressed { background-color: rgba(0,0,0,40); }
        """)
        self.btn_swap_names.setIcon(QtGui.QIcon(f"{_icons_dir}/swap_names.png"))
        self.btn_swap_names.setIconSize(QtCore.QSize(34, 34))
        self.btn_swap_names.clicked.connect(self._run_swap_names)
        row_swap.addWidget(self.btn_swap_names)
        row_swap.addStretch(1)
        lay_rename.addLayout(row_swap)

        lay_nom.addWidget(grp_rename)


        root.addWidget(grp_nom)

        # ── Split (includes Locator controls) ─────────────────────────────
        grp_split, _body_split, lay_split = self._collapsible_section("Split", two_state=True, initial_state=2)
        lay_split.setSpacing(6)
        self._suffix_template = "abc"  # "abc" or "positional"

        # ── Locators ──────────────────────────────────────────────────────
        #
        # Right column layout:
        #   [   Create Locator (colspan 2)   ]
        #   [+][−]
        #   [↑][↓]
        #   [🔗][⛓]
        #
        _BW = 32  # button size (square)
        _SP = 2   # grid spacing
        _grid_h = 4 * _BW + 3 * _SP  # drives table height too (4 rows)
        self._loc_grid_h = _grid_h   # minimum table height (= button block height)

        _ICON_BTN_STYLE = """
            QToolButton {
                background-color: rgba(255,255,255,18);
                border: none;
                border-radius: 3px;
                padding: 2px;
            }
            QToolButton:hover   { background-color: rgba(255,255,255,30); }
            QToolButton:pressed { background-color: rgba(0,0,0,40); }
        """

        def _side_btn(label, tooltip, callback):
            b = QtWidgets.QPushButton(label)
            b.setFixedSize(_BW, _BW)
            b.setFocusPolicy(QtCore.Qt.NoFocus)
            b.setStyleSheet(
                "QPushButton { font-size: 16px; font-weight: bold;"
                " background-color: rgba(255,255,255,18); border: none; border-radius: 3px; }"
                "QPushButton:hover { background-color: rgba(255,255,255,30); }"
                "QPushButton:pressed { background-color: rgba(0,0,0,40); }"
            )
            b.setToolTip(tooltip)
            b.clicked.connect(callback)
            return b

        def _side_icon_btn(icon_path, tooltip, callback):
            b = QtWidgets.QToolButton()
            b.setFixedSize(_BW, _BW)
            b.setAutoRaise(True)
            b.setToolTip(tooltip)
            px = QtGui.QPixmap(icon_path)
            if not px.isNull():
                b.setIcon(QtGui.QIcon(px.scaled(24, 24,
                    QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)))
                b.setIconSize(QtCore.QSize(24, 24))
            b.clicked.connect(callback)
            return b

        btn_get = _side_btn("+",
            "Select locators from the character's right side to left\n"
            "(i.e. from your left to right when facing the character).\n"
            "The selection order maps directly to zone naming:\n"
            "  1 locator  -> symmetric L_ / R_ pair\n"
            "  3 locators -> R_ / C_ / L_\n"
            "  4+ locators ->alphabetical  (a, b, c…)",
            self._get_locators_from_selection)
        btn_rm     = _side_btn("−", "Remove selected row",    self._remove_row)
        btn_up     = _side_btn("↑", "Move selected row up",   self._move_row_up)
        btn_dn     = _side_btn("↓", "Move selected row down", self._move_row_down)
        btn_link   = _side_icon_btn(
            f"{_icons_dir}/link_locs.png",
            "Connect L locators to R locators via multiplyDivide nodes (X-axis mirror).\n"
            "Requires Symmetric L/R ON with L and R sides assigned.",
            self._run_link_mirrors)
        btn_link.setStyleSheet(_ICON_BTN_STYLE)
        btn_unlink = _side_icon_btn(
            f"{_icons_dir}/unlink_locs.png",
            "Remove mirror connections. R locators keep their current position.",
            self._run_unlink_mirrors)
        btn_unlink.setStyleSheet(_ICON_BTN_STYLE)

        btn_create_loc = QtWidgets.QToolButton()
        btn_create_loc.setFixedSize(_BW, _BW)
        btn_create_loc.setAutoRaise(True)
        btn_create_loc.setStyleSheet(_ICON_BTN_STYLE)
        btn_create_loc.setToolTip("Create a locator at the origin and add it to the table")
        _px_loc = QtGui.QPixmap(f"{_icons_dir}/locator.png")
        if not _px_loc.isNull():
            btn_create_loc.setIcon(QtGui.QIcon(_px_loc.scaled(
                _BW, _BW, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)))
            btn_create_loc.setIconSize(QtCore.QSize(_BW, _BW))
        btn_create_loc.clicked.connect(self._create_locator)

        btn_clear_table = QtWidgets.QToolButton()
        btn_clear_table.setFixedSize(_BW, _BW)
        btn_clear_table.setAutoRaise(True)
        btn_clear_table.setStyleSheet(_ICON_BTN_STYLE)
        btn_clear_table.setToolTip("Remove all locators from the table")
        _px_clear = QtGui.QPixmap(f"{_icons_dir}/clear_table.png")
        if not _px_clear.isNull():
            btn_clear_table.setIcon(QtGui.QIcon(_px_clear.scaled(
                _BW, _BW, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)))
            btn_clear_table.setIconSize(QtCore.QSize(_BW, _BW))
        btn_clear_table.clicked.connect(self._clear_table)

        side_grid = QtWidgets.QGridLayout()
        side_grid.setSpacing(_SP)
        side_grid.setContentsMargins(_SP, _SP, _SP, _SP)
        side_grid.addWidget(btn_create_loc,  0, 0)        # row 0 col 0
        side_grid.addWidget(btn_clear_table, 0, 1)        # row 0 col 1
        side_grid.addWidget(btn_get,         1, 0)
        side_grid.addWidget(btn_up,          1, 1)
        side_grid.addWidget(btn_rm,          2, 0)
        side_grid.addWidget(btn_dn,          2, 1)
        side_grid.addWidget(btn_link,        3, 0)
        side_grid.addWidget(btn_unlink,      3, 1)

        self.table = QtWidgets.QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Locator", "Side", "Suffix"])
        self.table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeToContents)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.table.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.table.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        self.table.setFixedHeight(_grid_h)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.DoubleClicked)
        self.table.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._on_table_context_menu)
        self.table.itemChanged.connect(self._on_locator_name_edited)
        _hdr = self.table.horizontalHeader()
        _hdr.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        _hdr.customContextMenuRequested.connect(self._on_table_header_context_menu)

        # ── Preset row ────────────────────────────────────────────────────
        # [load_btn] [combo, stretch — aligns with table] | [save_btn — aligns with side_grid]
        self._current_locs_grp = None
        _side_w = 2 * _BW + 3 * _SP          # total width of side_grid incl. margins

        preset_row = QtWidgets.QHBoxLayout()
        preset_row.setSpacing(4)
        preset_row.setContentsMargins(0, 0, 0, 0)

        btn_load_preset = QtWidgets.QToolButton()
        btn_load_preset.setFixedSize(32, 32)
        btn_load_preset.setAutoRaise(True)
        btn_load_preset.setStyleSheet(_ICON_BTN_STYLE)
        btn_load_preset.setToolTip(
            "Select a locator group in the scene and click to load its presets into the list.")
        _px_lp = QtGui.QPixmap(f"{_icons_dir}/path.png")
        if not _px_lp.isNull():
            btn_load_preset.setIcon(QtGui.QIcon(_px_lp.scaled(
                30, 30, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)))
            btn_load_preset.setIconSize(QtCore.QSize(30, 30))
        else:
            btn_load_preset.setText("Load")
        btn_load_preset.clicked.connect(self._on_load_split_preset_grp)

        self._combo_split_preset = QtWidgets.QComboBox()
        self._combo_split_preset.addItem("— presets —")
        self._combo_split_preset.setToolTip(
            "Select a preset to load its locators and split settings.")
        self._combo_split_preset.activated.connect(self._on_split_preset_activated)

        btn_save_preset = QtWidgets.QToolButton()
        btn_save_preset.setFixedSize(_side_w, 32)
        btn_save_preset.setAutoRaise(True)
        btn_save_preset.setStyleSheet(_ICON_BTN_STYLE)
        btn_save_preset.setToolTip(
            "Save the current locators and settings into the selected preset group\n"
            "(or create a new preset group if none is selected).\n"
            "Locators are parented to the preset sub-group inside the locs_grp.")
        _px_sp = QtGui.QPixmap(f"{_icons_dir}/save.png")
        if not _px_sp.isNull():
            btn_save_preset.setIcon(QtGui.QIcon(_px_sp.scaled(
                30, 30, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)))
            btn_save_preset.setIconSize(QtCore.QSize(30, 30))
        else:
            btn_save_preset.setText("Save")
        btn_save_preset.clicked.connect(self._on_save_split_preset)

        btn_load_preset.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        btn_load_preset.customContextMenuRequested.connect(
            lambda pos: self._on_load_preset_context_menu(btn_load_preset, pos))
        btn_save_preset.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        btn_save_preset.customContextMenuRequested.connect(
            lambda pos: self._on_save_preset_context_menu(btn_save_preset, pos))

        preset_row.addWidget(btn_load_preset)
        preset_row.addWidget(self._combo_split_preset, 1)
        preset_row.addWidget(btn_save_preset)
        lay_split.addLayout(preset_row)

        loc_row = QtWidgets.QHBoxLayout()
        loc_row.setSpacing(4)
        loc_row.setContentsMargins(0, 0, 0, 0)
        loc_row.addWidget(self.table, 1)
        loc_row.addLayout(side_grid)
        lay_split.addLayout(loc_row)

        # ── Axis group ────────────────────────────────────────────────
        grp_axis = QtWidgets.QGroupBox("Axis Options")
        grp_axis.setStyleSheet("QGroupBox { font-size: 11px; }")
        lay_axis = QtWidgets.QHBoxLayout(grp_axis)
        lay_axis.setContentsMargins(8, 4, 8, 4)
        lay_axis.setSpacing(8)

        self.chk_x = QtWidgets.QCheckBox("X")
        self.chk_y = QtWidgets.QCheckBox("Y")
        self.chk_z = QtWidgets.QCheckBox("Z")
        self.chk_x.setChecked(True)
        self.chk_x.setToolTip("Radial OFF: radio mode, one axis at a time.\nRadial ON: free multi-selection.")
        self.chk_y.setToolTip("Radial OFF: radio mode, one axis at a time.\nRadial ON: free multi-selection.")
        self.chk_z.setToolTip("Radial OFF: radio mode, one axis at a time.\nRadial ON: free multi-selection.")
        self.chk_x.stateChanged.connect(lambda s: self._on_axis_exclusive(self.chk_x, s))
        self.chk_y.stateChanged.connect(lambda s: self._on_axis_exclusive(self.chk_y, s))
        self.chk_z.stateChanged.connect(lambda s: self._on_axis_exclusive(self.chk_z, s))

        self.chk_invert_axis = QtWidgets.QCheckBox("Invert")
        self.chk_invert_axis.setChecked(False)
        self.chk_invert_axis.setToolTip(
            "Inverts the split axis direction.\n"
            "Use when the selected axis points opposite to the expected split direction."
        )

        self.chk_local_axes = QtWidgets.QCheckBox("Local")
        self.chk_local_axes.setChecked(True)
        self.chk_local_axes.setToolTip(
            "Checked  — Local space: projection uses the local axes of the locators.\n"
            "           Rotate your locators to match the surface for curved meshes.\n"
            "Unchecked — World space: projection uses the world X/Y/Z axes."
        )

        self.chk_symmetric = QtWidgets.QCheckBox("Symmetric L / R")
        self.chk_symmetric.setToolTip(
            "Auto-fills suffixes for symmetric L/R splits.\n"
            "Odd locators: R_b R_a C_ L_a L_b\n"
            "Even locators: R_b R_a L_a L_b"
        )
        self.chk_symmetric.stateChanged.connect(self._on_symmetric_changed)

        lay_axis.addWidget(self.chk_x)
        lay_axis.addWidget(self.chk_y)
        lay_axis.addWidget(self.chk_z)
        lay_axis.addStretch()
        lay_axis.addWidget(self.chk_invert_axis)
        lay_axis.addStretch()
        lay_axis.addWidget(self.chk_local_axes)
        lay_axis.addStretch()
        lay_axis.addWidget(self.chk_symmetric)

        lay_split.addWidget(grp_axis)

        # ── Falloff options group (same style as Axis groupbox, no title) ──────
        grp_falloff = QtWidgets.QGroupBox("Falloff Options")
        grp_falloff.setStyleSheet("QGroupBox { font-size: 11px; }")
        lay_falloff = QtWidgets.QVBoxLayout(grp_falloff)
        lay_falloff.setContentsMargins(8, 6, 8, 6)
        lay_falloff.setSpacing(4)

        # Row: Radial falloff + Curve type
        row_curve = QtWidgets.QHBoxLayout()
        self.chk_radial = QtWidgets.QCheckBox("Radial falloff")
        self.chk_radial.setToolTip(
            "Uses euclidean distance from each locator instead of\n"
            "1D projection. Works with any axis combination.\n"
            "All 3 axes = full 3D radial falloff."
        )
        def _on_radial_toggled(state):
            if state:
                self.chk_radius.setChecked(True)
            else:
                # Radial turned OFF: if multiple axes were checked, keep only the first
                checked = [c for c in (self.chk_x, self.chk_y, self.chk_z) if c.isChecked()]
                if len(checked) != 1:
                    for c in checked[1:]:
                        c.blockSignals(True)
                        c.setChecked(False)
                        c.blockSignals(False)
                    if not checked:
                        self.chk_x.blockSignals(True)
                        self.chk_x.setChecked(True)
                        self.chk_x.blockSignals(False)
        self.chk_radial.stateChanged.connect(_on_radial_toggled)
        row_curve.addWidget(self.chk_radial)
        row_curve.addStretch()
        row_curve.addWidget(QtWidgets.QLabel("Curve type"))
        self.combo_curve = QtWidgets.QComboBox()
        self.combo_curve.addItems(list(CURVE_FUNCTIONS.keys()))
        self.combo_curve.setToolTip("Falloff function applied between locators")
        row_curve.addWidget(self.combo_curve)
        lay_falloff.addLayout(row_curve)

        # Row: Radius checkbox + slider + spinbox on same line
        row_rad = QtWidgets.QHBoxLayout()
        self.chk_radius = QtWidgets.QCheckBox("Radius")
        self.chk_radius.setChecked(False)
        self.chk_radius.setToolTip(
            "Enable radius.\n"
            "1 locator : transition zone around the locator.\n"
            "N locators : overlap beyond each locator — 0 = hard transition, >0 = soft blend.")
        self.chk_radius.stateChanged.connect(self._on_radius_enabled)
        row_rad.addWidget(self.chk_radius)
        self.radius_label = QtWidgets.QLabel("")

        self.spin_radius = QtWidgets.QLineEdit("1.00")
        self.spin_radius.setFixedWidth(38)
        self.spin_radius.setAlignment(QtCore.Qt.AlignCenter)
        _rv = QtGui.QDoubleValidator(0.0, 15.0, 2, self.spin_radius)
        _rv.setLocale(QtCore.QLocale(QtCore.QLocale.English))
        self.spin_radius.setValidator(_rv)
        self.spin_radius.setEnabled(False)
        self.spin_radius.editingFinished.connect(self._on_radius_spin)
        row_rad.addWidget(self.spin_radius)

        self.slider_radius = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.slider_radius.setMinimum(0)
        self.slider_radius.setMaximum(150)
        self.slider_radius.setValue(10)
        self.slider_radius.setToolTip(
            "1 locator : transition zone around the locator.\n"
            "N locators : overlap beyond each locator.")
        self.slider_radius.setEnabled(False)
        self.slider_radius.valueChanged.connect(self._on_radius_slider)
        row_rad.addWidget(self.slider_radius)

        lay_falloff.addLayout(row_rad)
        lay_split.addWidget(grp_falloff)

        # Split Target + Edge Loop Split — same row
        _split_btn_ss = """
            QToolButton { background-color: transparent; border: none; border-radius: 3px; padding: 2px; }
            QToolButton:hover { background-color: rgba(255,255,255,30); }
            QToolButton:pressed { background-color: rgba(0,0,0,40); }
        """
        row_split_actions = QtWidgets.QHBoxLayout()

        _half_split = QtWidgets.QHBoxLayout()
        _half_split.setSpacing(4)
        _lbl_split = QtWidgets.QLabel("Split Target")
        _half_split.addWidget(_lbl_split)
        self.btn_split = QtWidgets.QToolButton()
        self.btn_split.setFixedSize(36, 36)
        self.btn_split.setToolButtonStyle(QtCore.Qt.ToolButtonIconOnly)
        self.btn_split.setToolTip("Creates split targets in the blendShape node")
        self.btn_split.setStyleSheet(_split_btn_ss)
        self.btn_split.setIcon(QtGui.QIcon(f"{_icons_dir}/split.png"))
        self.btn_split.setIconSize(QtCore.QSize(34, 34))
        self.btn_split.clicked.connect(self._run_split)
        _half_split.addWidget(self.btn_split)
        _half_split.addStretch(1)

        _half_els = QtWidgets.QHBoxLayout()
        _half_els.setSpacing(4)
        _lbl_els = QtWidgets.QLabel("Edge Loop Split")
        _half_els.addWidget(_lbl_els)
        self.btn_edge_loop_split = QtWidgets.QToolButton()
        self.btn_edge_loop_split.setFixedSize(36, 36)
        self.btn_edge_loop_split.setToolButtonStyle(QtCore.Qt.ToolButtonIconOnly)
        self.btn_edge_loop_split.setToolTip(
            "Splits selected targets along the stored edge loop.\n"
            "Set Vertices and Edgeloop via the Setup section below.\n"
            "The Radius setting controls the falloff blend at the seam (default: 1).\n"
            "Enable Radius and increase the value for a softer transition.")
        self.btn_edge_loop_split.setStyleSheet(_split_btn_ss)
        self.btn_edge_loop_split.setIcon(QtGui.QIcon(f"{_icons_dir}/edge_split.png"))
        self.btn_edge_loop_split.setIconSize(QtCore.QSize(34, 34))
        self.btn_edge_loop_split.clicked.connect(self._run_edge_loop_split)
        _half_els.addWidget(self.btn_edge_loop_split)
        _half_els.addStretch(1)

        row_split_actions.addLayout(_half_split, 1)
        row_split_actions.addLayout(_half_els, 1)

        grp_split_actions = QtWidgets.QGroupBox()
        grp_split_actions.setStyleSheet("QGroupBox { font-size: 11px; }")
        lay_split_actions_grp = QtWidgets.QVBoxLayout(grp_split_actions)
        lay_split_actions_grp.setContentsMargins(8, 6, 8, 6)
        lay_split_actions_grp.setSpacing(0)
        lay_split_actions_grp.addLayout(row_split_actions)
        lay_split.addWidget(grp_split_actions)

        # ── Edge Loop Split setup — collapsible ───────────────────────────
        self._els_setup_toggle = QtWidgets.QToolButton()
        self._els_setup_toggle.setText("  Edge Loop Options")
        self._els_setup_toggle.setArrowType(QtCore.Qt.RightArrow)
        self._els_setup_toggle.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)
        self._els_setup_toggle.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        self._els_setup_toggle.setFixedHeight(18)
        self._els_setup_toggle.setStyleSheet(
            "QToolButton { background: transparent; border: none; "
            "font-size: 10px; color: #888888; "
            "text-align: center; }"
            "QToolButton:hover { color: #aaaaaa; }")
        lay_split.addWidget(self._els_setup_toggle)

        els_setup_widget = QtWidgets.QWidget()
        els_setup_widget.setVisible(False)
        els_setup_lay = QtWidgets.QVBoxLayout(els_setup_widget)
        els_setup_lay.setContentsMargins(12, 2, 0, 2)
        els_setup_lay.setSpacing(4)

        def _els_field(edit_attr, placeholder, get_handler):
            row = QtWidgets.QHBoxLayout()
            edit = QtWidgets.QLineEdit()
            edit.setReadOnly(True)
            edit.setPlaceholderText(placeholder)
            btn_get = QtWidgets.QPushButton("Get")
            btn_get.setFixedWidth(40)
            btn_get.clicked.connect(get_handler)
            row.addWidget(edit)
            row.addWidget(btn_get)
            setattr(self, edit_attr, edit)
            return row

        lbl_edges = QtWidgets.QLabel("Edge Loop")
        lbl_seeds = QtWidgets.QLabel("Vertices")
        _lbl_w = max(lbl_edges.sizeHint().width(), lbl_seeds.sizeHint().width())
        lbl_edges.setFixedWidth(_lbl_w)
        lbl_seeds.setFixedWidth(_lbl_w)

        row_seeds = QtWidgets.QHBoxLayout()
        row_seeds.addWidget(lbl_seeds)
        row_seeds.addLayout(_els_field(
            "edit_els_upper_vtx", "upper vertex", self._els_get_upper_vtx))
        row_seeds.addLayout(_els_field(
            "edit_els_lower_vtx", "lower vertex", self._els_get_lower_vtx))
        els_setup_lay.addLayout(row_seeds)

        row_edges = QtWidgets.QHBoxLayout()
        row_edges.addWidget(lbl_edges)
        row_edges.addLayout(_els_field(
            "edit_els_edges", "split edge loop", self._els_get_edges))
        els_setup_lay.addLayout(row_edges)

        lay_split.addWidget(els_setup_widget)

        def _toggle_els_setup():
            visible = not els_setup_widget.isVisible()
            els_setup_widget.setVisible(visible)
            self._els_setup_toggle.setArrowType(
                QtCore.Qt.DownArrow if visible else QtCore.Qt.RightArrow)

        self._els_setup_toggle.clicked.connect(_toggle_els_setup)

        grp_split.add_compact_action(
            f"{_icons_dir}/locator.png", "Create Locator", self._create_locator)
        grp_split.add_compact_text_btn(
            "Get Selection",
            "Select locators from the character's right side to left\n"
            "(i.e. from your left to right when facing the character).\n"
            "The selection order maps directly to zone naming:\n"
            "  1 locator  -> symmetric L_ / R_ pair\n"
            "  3 locators -> R_ / C_ / L_\n"
            "  4+ locators ->alphabetical  (a, b, c…)",
            self._get_locators_from_selection)
        grp_split.add_compact_action(
            f"{_icons_dir}/split.png", "Split Target", self._run_split)
        grp_split.add_compact_action(
            f"{_icons_dir}/edge_split.png", "Edge Loop Split", self._run_edge_loop_split)
        # ── Modify ────────────────────────────────────────────────────────
        grp_mod, _body_mod, lay_mod = self._collapsible_section("Modify Deltas", initial_state=2, compact_rows=1)
        lay_mod.setSpacing(4)

        _GRP_STYLE = "QGroupBox { font-size: 11px; }"
        _GRP_MARGINS = (8, 6, 8, 6)

        def _make_factor_field(default="1.0"):
            field = QtWidgets.QLineEdit(default)
            field.setFixedWidth(52)
            field.setAlignment(QtCore.Qt.AlignCenter)
            validator = QtGui.QRegularExpressionValidator(
                QtCore.QRegularExpression(r"-?\d*\.?\d*"), field)
            field.setValidator(validator)
            return field

        # ── Scalar ────────────────────────────────────────────────────────────
        grp_scalar = QtWidgets.QGroupBox("Deltas Scale")
        grp_scalar.setStyleSheet(_GRP_STYLE)
        lay_scalar = QtWidgets.QVBoxLayout(grp_scalar)
        lay_scalar.setContentsMargins(*_GRP_MARGINS)
        lay_scalar.setSpacing(4)

        self._mult_labels = []
        self._mult_fields = []
        for idx, axis in enumerate(('X', 'Y', 'Z')):
            lbl = QtWidgets.QPushButton(axis)
            lbl.setCheckable(True)
            lbl.setFixedSize(22, 22)
            lbl.setToolTip("Click to select — Shift+click to multi-select.\n"
                           "Typing in a selected field updates all selected fields.")
            fld = _make_factor_field("1.2")
            self._mult_labels.append(lbl)
            self._mult_fields.append(fld)

        for idx, lbl in enumerate(self._mult_labels):
            lbl.clicked.connect(lambda *args, i=idx: self._on_mult_label_click(i))
        for idx, fld in enumerate(self._mult_fields):
            fld.editingFinished.connect(lambda i=idx: self._on_mult_field_edited(i))

        _tt_multiply = ("Multiply X/Y/Z delta components directly (object space).\n"
                        "1.0 = unchanged   0.0 = zero   -1.0 = invert\n"
                        "Click X/Y/Z labels to select axes — Shift+click to multi-select.")
        _tt_invert   = ("Invert all delta components (multiply X, Y, Z by -1).\n"
                        "Works on selected vertices or the full target.")
        _tt_nullify  = ("Zero out all delta components (X=0, Y=0, Z=0).\n"
                        "Equivalent to Multiply Deltas with all factors set to 0.\n"
                        "Works on selected vertices or the full target.")

        # X/Y/Z labels + fields
        row_xyz = QtWidgets.QHBoxLayout()
        row_xyz.setSpacing(4)
        self._mult_sign = 1.0
        self.btn_mult_sign = QtWidgets.QToolButton()
        self.btn_mult_sign.setFixedSize(40, 34)
        self.btn_mult_sign.setIconSize(QtCore.QSize(34, 34))
        self.btn_mult_sign.setAutoRaise(True)
        self.btn_mult_sign.setStyleSheet("""
            QToolButton {
                background-color: transparent;
                border: none;
                border-radius: 3px;
                padding: 2px;
            }
            QToolButton:hover   { background-color: rgba(255,255,255,30); }
            QToolButton:pressed { background-color: rgba(0,0,0,40); }
        """)
        self.btn_mult_sign.setToolTip("Toggle sign: click to switch between + and −.\n− negates all X/Y/Z factors before applying.")
        _ico_plus  = QtGui.QIcon(f"{_icons_dir}/plus.png")
        _ico_minus = QtGui.QIcon(f"{_icons_dir}/minus.png")
        self.btn_mult_sign.setIcon(_ico_plus)
        def _on_mult_sign_clicked():
            self._mult_sign *= -1.0
            _ico = _ico_minus if self._mult_sign < 0 else _ico_plus
            self.btn_mult_sign.setIcon(_ico)
            _cb = getattr(self, '_compact_sign_btn', None)
            if _cb:
                _cb.setIcon(_ico)
        self.btn_mult_sign.clicked.connect(_on_mult_sign_clicked)
        row_xyz.addWidget(self.btn_mult_sign)
        for _lbl, _fld in zip(self._mult_labels, self._mult_fields):
            _fld.setFixedWidth(16777215)
            _fld.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
            row_xyz.addWidget(_lbl)
            row_xyz.addWidget(_fld, 1)

        self._xyz_linked = True
        self._btn_normal_mode = QtWidgets.QToolButton()
        self._btn_normal_mode.setCheckable(True)
        self._btn_normal_mode.setChecked(False)
        self._btn_normal_mode.setFixedSize(36, 34)
        self._btn_normal_mode.setIconSize(QtCore.QSize(32, 32))
        self._btn_normal_mode.setAutoRaise(True)
        self._btn_normal_mode.setStyleSheet("""
            QToolButton {
                background-color: transparent;
                border: none;
                border-radius: 3px;
                padding: 1px;
            }
            QToolButton:hover   { background-color: rgba(255,255,255,30); }
            QToolButton:pressed { background-color: rgba(0,0,0,40); }
            QToolButton:checked { background-color: rgba(80,120,200,60);
                                  border: 1px solid rgba(100,150,255,150); }
        """)
        _px_normal = QtGui.QPixmap(f"{_icons_dir}/normal_push.png")
        if not _px_normal.isNull():
            self._btn_normal_mode.setIcon(QtGui.QIcon(_px_normal))
        self._btn_normal_mode.setToolTip(
            "Normal Push mode — when active, Multiply and Invert operate\n"
            "along vertex normals instead of object-space XYZ axes.\n"
            "XYZ fields are linked and default to 0.20.")
        row_xyz.addWidget(self._btn_normal_mode)
        lay_scalar.addLayout(row_xyz)

        _w_mult, self._btn_mult = self._label_icon_btn(f"{_icons_dir}/multiply_delta.png", "Multiply", _tt_multiply)
        self._btn_mult.clicked.connect(self._run_multiply)

        def _on_normal_mode_toggled(checked):
            self._xyz_linked = checked
            val = "0.20" if checked else "1.20"
            for fld in self._mult_fields:
                fld.setText(val)
            _cf = getattr(self, '_compact_mult_fld', None)
            if _cf:
                _cf.setText(val)
            _cn = getattr(self, '_compact_normal_btn', None)
            if _cn and _cn.isChecked() != checked:
                _cn.setChecked(checked)
        self._btn_normal_mode.toggled.connect(_on_normal_mode_toggled)
        _w_inv,  _b_inv  = self._label_icon_btn(f"{_icons_dir}/invert_delta.png",   "Invert",   _tt_invert)
        _b_inv.clicked.connect(self._run_invert_deltas)
        _w_nul,  _b_nul  = self._label_icon_btn(f"{_icons_dir}/nullify_delta.png",  "Nullify",  _tt_nullify)
        _b_nul.clicked.connect(self._run_nullify)
        self._align_label_icon_btns([_w_mult, _w_inv, _w_nul])
        row_min = QtWidgets.QHBoxLayout()
        row_min.setSpacing(0)
        row_min.addWidget(_w_mult)
        row_min.addStretch(1)
        row_min.addWidget(_w_inv)
        row_min.addStretch(1)
        row_min.addWidget(_w_nul)
        lay_scalar.addLayout(row_min)

        lay_mod.addWidget(grp_scalar)

        # ── Between 2 Targets ─────────────────────────────────────────────────
        grp_2tgt = QtWidgets.QGroupBox("Deltas Exchange")
        grp_2tgt.setStyleSheet(_GRP_STYLE)
        lay_2tgt = QtWidgets.QVBoxLayout(grp_2tgt)
        lay_2tgt.setContentsMargins(*_GRP_MARGINS)
        lay_2tgt.setSpacing(4)

        _tt_add = (
            "Add — Adds donor deltas onto the receiver target.\n"
            "Select target A first, then add select the other targets.\n"
            "Vertex selection: restricts the operation to selected verts.\n"
            "No vertex selection: operates on the full target.")
        _tt_sub = (
            "Sub — Subtracts donor deltas from the receiver target.\n"
            "Select target A first, then add select the other targets.\n"
            "Vertex selection: restricts the operation to selected verts.\n"
            "No vertex selection: operates on the full target.")
        _tt_xfer = (
            "Transfer — Moves deltas from B to A (adds to A, zeros B).\n"
            "Select target B first (donor), then add select target A (receiver).\n"
            "Vertex selection: transfers only selected verts.\n"
            "No vertex selection: transfers all delta verts on B.")
        _tt_swap = (
            "Swap — Replaces A's deltas with B's and B's with A's (A \u2194 B).\n"
            "Select target A first, then add select target B.\n"
            "Vertex selection: swaps only selected verts.\n"
            "No vertex selection: swaps full delta sets (confirmation required).")
        _tt_repl = (
            "Replace — Copies B's deltas onto A, overwriting A. B is left intact.\n"
            "Select target A first, then add select target B.\n"
            "Vertex selection: replaces only selected verts on A.\n"
            "No vertex selection: replaces full delta set of A with B's.")
        _tt_mult_sh = (
            "Mult A\u00d7B — Multiplies A's deltas component-wise by B's.\n"
            "A[vi] = (Ax*Bx, Ay*By, Az*Bz). Verts in A not in B are zeroed.\n"
            "Select target A first, then add select target B.")

        _w_add,  self.btn_delta_add         = self._label_icon_btn(f"{_icons_dir}/add_delta.png",      "Add",      _tt_add)
        _w_sub,  self.btn_delta_sub         = self._label_icon_btn(f"{_icons_dir}/sub_delta.png",      "Subtract", _tt_sub)
        _w_xfer, self.btn_delta_transfer        = self._label_icon_btn(f"{_icons_dir}/transfer_delta.png", "Transfer", _tt_xfer)
        _w_swap, self.btn_delta_swap_pure   = self._label_icon_btn(f"{_icons_dir}/swap_delta.png",     "Swap",     _tt_swap)
        self.btn_delta_add.clicked.connect(self._run_delta_add)
        self.btn_delta_sub.clicked.connect(self._run_delta_sub)
        self.btn_delta_transfer.clicked.connect(self._run_delta_transfer)
        self.btn_delta_swap_pure.clicked.connect(self._run_delta_swap_pure)

        _w_apply, self.btn_apply_moves = self._label_icon_btn(
            f"{_icons_dir}/bake_moves.png", "Bake Moves",
            "Transfers vertex tweaks (pnts[]) from the mesh to the selected target.\n"
            "Use when you sculpted the mesh directly without entering edit mode first.\n"
            "The vertex moves are added to the target's existing deltas,\n"
            "then zeroed out on the mesh.\n"
            "Works on 1 selected target only.\n\n"
            "Vertex selection: if vertices are selected on the mesh, only those vertices\n"
            "are baked — the rest of the target and the remaining tweaks are left untouched.")
        self.btn_apply_moves.clicked.connect(self._run_apply_moves)

        _w_bake, self.btn_bake_deformers = self._label_icon_btn(
            f"{_icons_dir}/bake_deformer.png", "Bake Defs",
            "Bakes the contribution of all deformers stacked above the blendShape into the\n"
            "selected targets. For each target the tool activates it at weight 1.0, samples\n"
            "the mesh with all deformers evaluated, and stores the result as the new delta set.\n\n"
            "Typical workflow:\n"
            "  1. Add a Delta Mush (or any deformer) on the base mesh and adjust it.\n"
            "  2. Select the targets to improve in the Shape Editor.\n"
            "  3. Click Bake Defs.\n"
            "  4. Delete the deformer.\n\n"
            "Works on all targets selected in the Shape Editor.\n\n"
            "Vertex selection: if vertices are selected on the mesh, only those vertices\n"
            "receive the baked delta — the remaining vertices keep their existing deltas.")
        self.btn_bake_deformers.clicked.connect(self._run_bake_deformers)

        self._align_label_icon_btns([_w_add, _w_sub, _w_xfer, _w_swap])
        self._align_label_icon_btns([_w_apply, _w_bake])
        grid_2tgt = QtWidgets.QGridLayout()
        grid_2tgt.setSpacing(4)
        grid_2tgt.addWidget(_w_add,   0, 0)
        grid_2tgt.addWidget(_w_sub,   0, 1)
        grid_2tgt.addWidget(_w_apply, 0, 2)
        grid_2tgt.addWidget(_w_xfer,  1, 0)
        grid_2tgt.addWidget(_w_swap,  1, 1)
        grid_2tgt.addWidget(_w_bake,  1, 2)
        grid_2tgt.setColumnStretch(0, 1)
        grid_2tgt.setColumnStretch(1, 1)
        grid_2tgt.setColumnStretch(2, 1)
        lay_2tgt.addLayout(grid_2tgt)

        # ── Smooth & Relax ────────────────────────────────────────────────────
        grp_smooth = QtWidgets.QGroupBox("Deltas Distribution")
        grp_smooth.setStyleSheet(_GRP_STYLE)
        lay_smooth = QtWidgets.QVBoxLayout(grp_smooth)
        lay_smooth.setContentsMargins(*_GRP_MARGINS)
        lay_smooth.setSpacing(4)

        row_opacity = QtWidgets.QHBoxLayout()
        row_opacity.setSpacing(4)
        lbl_opacity = QtWidgets.QLabel("Opacity")
        lbl_opacity.setFixedWidth(52)
        _opacity_tip = (
            "Strength for Smooth, Relax, Hammer and Average.\n"
            "Smooth / Relax: maps to 1–10 iterative passes.\n"
            "Hammer: blends between original and fully-converged result (100% = full hammer).\n"
            "Average: blend weight between original and averaged value.")
        self.spin_smooth_opacity = QtWidgets.QLineEdit("1.00")
        self.spin_smooth_opacity.setFixedWidth(55)
        self.spin_smooth_opacity.setAlignment(QtCore.Qt.AlignCenter)
        _ov = QtGui.QDoubleValidator(0.01, 1.0, 2, self.spin_smooth_opacity)
        _ov.setLocale(QtCore.QLocale(QtCore.QLocale.English))
        self.spin_smooth_opacity.setValidator(_ov)
        self.spin_smooth_opacity.setToolTip(_opacity_tip)
        self.slider_smooth_opacity = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.slider_smooth_opacity.setRange(1, 100)
        self.slider_smooth_opacity.setValue(100)
        self.slider_smooth_opacity.setToolTip(_opacity_tip)
        self.slider_smooth_opacity.valueChanged.connect(
            lambda v: (self.spin_smooth_opacity.blockSignals(True),
                       self.spin_smooth_opacity.setText(f"{v / 100.0:.2f}"),
                       self.spin_smooth_opacity.blockSignals(False)))
        self.spin_smooth_opacity.editingFinished.connect(self._on_opacity_spin_edited)
        row_opacity.addWidget(lbl_opacity)
        row_opacity.addWidget(self.spin_smooth_opacity)
        row_opacity.addWidget(self.slider_smooth_opacity)
        lay_smooth.addLayout(row_opacity)

        row_sr = QtWidgets.QHBoxLayout()
        row_sr.setSpacing(2)
        _w_smt, self.btn_smooth = self._label_icon_btn(
            f"{_icons_dir}/smooth_delta.png", "Smooth Deltas",
            "Laplacian smoothing of the delta field.\n"
            "Each vertex is replaced by the average of its neighbors' deltas.\n"
            "Works on vertex selection or full target (no selection).\n"
            "Opacity maps to 1–10 iterative passes.")
        self.btn_smooth.clicked.connect(self._run_smooth_deltas)
        _w_rlx, self.btn_relax = self._label_icon_btn(
            f"{_icons_dir}/relax_delta.png", "Relax Deltas",
            "Relaxes the delta field by averaging 3D positions in deformed space.\n"
            "Like a mesh relax, but applied only to the blendShape target.\n"
            "Works on vertex selection or full target (no selection).\n"
            "Opacity maps to 1–10 iterative passes.")
        self.btn_relax.clicked.connect(self._run_relax_deltas)
        row_sr.addWidget(_w_smt)
        row_sr.addWidget(_w_rlx)
        lay_smooth.addLayout(row_sr)

        row_ha = QtWidgets.QHBoxLayout()
        row_ha.setSpacing(2)
        _w_hammer, self.btn_hammer = self._label_icon_btn(
            f"{_icons_dir}/hammer_delta.png", "Hammer Deltas",
            "Replaces each selected vertex's delta with the IDW-weighted average\n"
            "of its topological neighbors' deltas (1-ring, Euclidean distance).\n"
            "Like Maya's Hammer Weights — selection required.\n"
            "Opacity maps to 1–20 iterative passes (50% = 10, default).")
        self.btn_hammer.clicked.connect(self._run_hammer_deltas)
        _w_average, self.btn_average = self._label_icon_btn(
            f"{_icons_dir}/average_delta.png", "Average Deltas",
            "Replaces all selected vertices' deltas with their arithmetic mean.\n"
            "Levels a cluster to a common displacement value.\n"
            "Selection required.\n"
            "Opacity blends between the original delta and the averaged value.")
        self.btn_average.clicked.connect(self._run_average_deltas)
        self._align_label_icon_btns([_w_smt, _w_rlx, _w_hammer, _w_average])
        row_ha.addWidget(_w_hammer)
        row_ha.addWidget(_w_average)
        lay_smooth.addLayout(row_ha)

        row_neighbors = QtWidgets.QHBoxLayout()
        row_neighbors.setSpacing(2)
        lbl_smooth_neighbors = QtWidgets.QLabel("Mode")
        lbl_smooth_neighbors.setFixedWidth(40)
        self.combo_smooth_falloff = QtWidgets.QComboBox()
        self.combo_smooth_falloff.addItem("Surface", "surface")
        self.combo_smooth_falloff.addItem("Volume",  "volume")
        self.combo_smooth_falloff.setCurrentIndex(0)
        self.combo_smooth_falloff.setToolTip(
            "Hammer mode — how neighbors are determined (Hammer only).\n"
            "\n"
            "Surface — topological Laplacian along mesh edges (1-ring).\n"
            "          Spreads deltas strictly through edge connectivity.\n"
            "          Never bleeds across disconnected regions or open seams.\n"
            "          Classic surface-aware smoothing.\n"
            "\n"
            "Volume  — spatial IDW in neutral-space (rest mesh positions).\n"
            "          k-nearest Euclidean neighbors regardless of topology.\n"
            "          Good for volume corrections where proximity in rest pose\n"
            "          matters more than edge connectivity.")
        lbl_lap = QtWidgets.QLabel("Smooth Iterations")
        lbl_lap.setToolTip("Number of topological Laplacian smoothing passes\n"
                           "applied after the Hammer iterations.\n"
                           "0 = no smoothing.")
        self.spin_hammer_lap = QtWidgets.QLineEdit("1")
        self.spin_hammer_lap.setFixedWidth(36)
        self.spin_hammer_lap.setAlignment(QtCore.Qt.AlignCenter)
        self.spin_hammer_lap.setValidator(QtGui.QIntValidator(0, 10, self.spin_hammer_lap))
        self.spin_hammer_lap.setToolTip(lbl_lap.toolTip())
        # Left half: Space + combo — mirrors left column of row_ha
        _w_nb_left = QtWidgets.QWidget()
        _lay_nb_left = QtWidgets.QHBoxLayout(_w_nb_left)
        _lay_nb_left.setContentsMargins(0, 0, 0, 0)
        _lay_nb_left.setSpacing(4)
        _lay_nb_left.addWidget(lbl_smooth_neighbors)
        _lay_nb_left.addWidget(self.combo_smooth_falloff)
        _lay_nb_left.addStretch()
        # Right half: Smooth Iterations + field — aligned with Average/Relax column
        _w_nb_right = QtWidgets.QWidget()
        _lay_nb_right = QtWidgets.QHBoxLayout(_w_nb_right)
        _lay_nb_right.setContentsMargins(0, 0, 0, 0)
        _lay_nb_right.setSpacing(4)
        _lay_nb_right.addWidget(lbl_lap)
        _lay_nb_right.addWidget(self.spin_hammer_lap)
        _lay_nb_right.addStretch()
        row_neighbors.addWidget(_w_nb_left)
        row_neighbors.addWidget(_w_nb_right)
        lay_smooth.addLayout(row_neighbors)
        lay_mod.addWidget(grp_smooth)
        lay_mod.addWidget(grp_2tgt)

        # ── Selection ─────────────────────────────────────────────────────────
        grp_sel = QtWidgets.QGroupBox("Deltas Clipboard")
        grp_sel.setStyleSheet(_GRP_STYLE)
        lay_sel = QtWidgets.QVBoxLayout(grp_sel)
        lay_sel.setContentsMargins(*_GRP_MARGINS)
        lay_sel.setSpacing(4)

        _w_copy_delta, self.btn_copy_delta = self._label_icon_btn(
            f"{_icons_dir}/copy_delta.png", "Copy Delta",
            "Copies the delta of the single selected vertex on the active target.\n"
            "The value is stored until a new Copy or tool restart.")
        self.btn_copy_delta.clicked.connect(self._run_copy_delta)

        _w_paste_delta, self.btn_paste_delta = self._label_icon_btn(
            f"{_icons_dir}/paste_delta.png", "Paste Delta",
            "Pastes the copied delta onto all selected vertices on the active target.\n"
            "Undoable.")
        self.btn_paste_delta.setEnabled(False)
        self.btn_paste_delta.clicked.connect(self._run_paste_delta)

        self.spin_prune_tol = QtWidgets.QLineEdit("0.001")
        self.spin_prune_tol.setFixedWidth(52)
        self.spin_prune_tol.setAlignment(QtCore.Qt.AlignCenter)
        self.spin_prune_tol.setValidator(QtGui.QRegularExpressionValidator(
            QtCore.QRegularExpression(r"\d*\.?\d*"), self.spin_prune_tol))
        self.spin_prune_tol.setToolTip("Tolerance — deltas with magnitude below this value are zeroed out.")

        _prune_style = """
            QToolButton {
                background-color: transparent; border: none;
                border-radius: 3px; padding: 1px;
            }
            QToolButton:hover   { background-color: rgba(255,255,255,30); }
            QToolButton:pressed { background-color: rgba(0,0,0,40); }
        """
        self.btn_prune = QtWidgets.QToolButton()
        self.btn_prune.setFixedSize(34, 34)
        self.btn_prune.setIconSize(QtCore.QSize(32, 32))
        self.btn_prune.setAutoRaise(True)
        self.btn_prune.setStyleSheet(_prune_style)
        _px_prune = QtGui.QPixmap(f"{_icons_dir}/prune_delta.png")
        if not _px_prune.isNull():
            self.btn_prune.setIcon(QtGui.QIcon(_px_prune))
        self.btn_prune.setToolTip("Zeros out deltas whose magnitude is below the tolerance threshold.")
        self.btn_prune.clicked.connect(self._run_prune_deltas)

        _lbl_prune = QtWidgets.QLabel("Prune Small Deltas")
        _w_prune = QtWidgets.QWidget()
        _w_prune.setFixedHeight(34)
        _lay_prune = QtWidgets.QHBoxLayout(_w_prune)
        _lay_prune.setContentsMargins(0, 0, 0, 0)
        _lay_prune.setSpacing(4)
        _lay_prune.addWidget(_lbl_prune)
        _lay_prune.addWidget(self.spin_prune_tol)
        _lay_prune.addWidget(self.btn_prune)
        _lay_prune.addStretch()

        _w_sel_delta, self.btn_sel_delta = self._label_icon_btn(
            f"{_icons_dir}/select_delta.png", "Select Vertices with Deltas",
            "Selects all vertices that have non-zero deltas on the active target.")
        self.btn_sel_delta.clicked.connect(self._run_select_delta_vertices)

        grid_cps = QtWidgets.QGridLayout()
        grid_cps.setSpacing(4)
        grid_cps.addWidget(_w_copy_delta,  0, 0)
        grid_cps.addWidget(_w_sel_delta,   0, 1)
        grid_cps.addWidget(_w_paste_delta, 1, 0)
        grid_cps.addWidget(_w_prune,       1, 1)
        self._align_label_icon_btns([_w_copy_delta, _w_paste_delta])
        self._align_label_icon_btns([_w_sel_delta])
        grid_cps.setColumnStretch(0, 1)
        grid_cps.setColumnStretch(1, 1)
        lay_sel.addLayout(grid_cps)
        lay_mod.addWidget(grp_sel)

        # ── Rig Extraction ────────────────────────────────────────────────────
        grp_rig = QtWidgets.QGroupBox("Deltas to Rig")
        grp_rig.setStyleSheet(_GRP_STYLE)
        lay_rig = QtWidgets.QVBoxLayout(grp_rig)
        lay_rig.setContentsMargins(*_GRP_MARGINS)
        lay_rig.setSpacing(4)

        _w_dc, self.btn_delta_cluster = self._label_icon_btn(
            f"{_icons_dir}/delta_cluster.png", "Create Delta Cluster",
            "Duplicates the target as a posed mesh and creates a cluster\n"
            "with weights matching the delta magnitudes of the shape.\n"
            "Cluster handle is placed at the bbox center of delta vertices.\n"
            "Enable 'Neutral' to use the rest-pose mesh instead.")
        self.btn_delta_cluster.clicked.connect(self._run_delta_cluster)

        _w_dj, self.btn_delta_joint = self._label_icon_btn(
            f"{_icons_dir}/delta_joint.png", "Create Delta Joint",
            "Duplicates the target as a posed mesh and binds two joints:\n"
            "  - {target}_jnt       : weights = normalized delta magnitudes\n"
            "  - {target}_zero_jnt  : absorbs remaining weights\n"
            "Everything is grouped under {target}_deltaJoint_grp.\n"
            "Enable 'Neutral' to use the rest-pose mesh instead.")
        self.btn_delta_joint.clicked.connect(self._run_delta_joint)
        self._align_label_icon_btns([_w_dc, _w_dj])
        lay_rig.addWidget(_w_dc)
        lay_rig.addWidget(_w_dj)

        row_delta_opts = QtWidgets.QHBoxLayout()
        self.chk_delta_neutral = QtWidgets.QCheckBox("Neutral")
        self.chk_delta_neutral.setChecked(False)
        self.chk_delta_neutral.setToolTip(
            "Neutral — creates a new empty '{target}_Copy' target in the blendShape,\n"
            "then regenerates it as a neutral-pose live mesh.\n"
            "Weights come from the selected target's delta magnitudes.\n"
            "Delete the mesh when done to bake your sculpt back into '{target}_Copy'.")
        self.chk_delta_multi = QtWidgets.QCheckBox("Multi")
        self.chk_delta_multi.setToolTip(
            "Multi — when multiple targets are selected, combines all their delta\n"
            "magnitudes (additive sum, then normalized) into a single cluster or joint.\n"
            "Works with or without Neutral mode.\n"
            "If only one target is selected, Multi has no effect.")
        row_delta_opts.addWidget(self.chk_delta_neutral)
        row_delta_opts.addWidget(self.chk_delta_multi)
        row_delta_opts.addStretch()
        lay_rig.addLayout(row_delta_opts)
        # ── Wrap Extract ──────────────────────────────────────────────────────
        grp_wrap_setup = QtWidgets.QGroupBox("Wrap Extract")
        grp_wrap_setup.setStyleSheet(_GRP_STYLE)
        lay_wrap_setup = QtWidgets.QVBoxLayout(grp_wrap_setup)
        lay_wrap_setup.setContentsMargins(*_GRP_MARGINS)
        lay_wrap_setup.setSpacing(4)


        _w_wrap, self.btn_wrap_extract = self._label_icon_btn(
            f"{_icons_dir}/wrap_extract.png",
            "Extract Wrap Targets",
            "Select master mesh (with BS) + one or more receiver meshes.\n"
            "If targets are selected in the Shape Editor, wraps those targets.\n"
            "Otherwise wraps all targets and prunes near-zero results.\n"
            "A BS node is created on each receiver if none exists.")
        self.btn_wrap_extract.clicked.connect(self._run_wrap_extract)

        _w_extract, self.btn_extract_only = self._label_icon_btn(
            f"{_icons_dir}/extract_only.png",
            "Extract Only",
            "Extracts each selected target using the deformer setup already present\n"
            "on the selected mesh (wrap, proximity wrap, etc.).\n"
            "No deformer is created or deleted.")
        self.btn_extract_only.clicked.connect(self._run_extract_only)

        self._align_label_icon_btns([_w_wrap, _w_extract])
        lay_wrap_setup.addWidget(_w_wrap)
        lay_wrap_setup.addWidget(_w_extract)

        self.chk_connect_targets = QtWidgets.QCheckBox("Connect")
        self.chk_connect_targets.setChecked(True)
        self.chk_connect_targets.setToolTip(
            "After extraction, connects each target weight from the source blendShape\n"
            "to the matching target on the mesh's blendShape.\n"
            "source_bs.target_name  -> mesh_bs.target_name")
        self.chk_wrap_overwrite = QtWidgets.QCheckBox("Overwrite")
        self.chk_wrap_overwrite.setChecked(True)
        self.chk_wrap_overwrite.setToolTip(
            "Overwrite ON  — if a target with the same name already exists on the\n"
            "receiver, replace it with the newly extracted version.\n"
            "Overwrite OFF — keep the existing target and append a numeric suffix\n"
            "to the new one (e.g. 'jaw' ->'jaw1'), like Maya's native behaviour.")
        row_wrap_row1 = QtWidgets.QHBoxLayout()
        row_wrap_row1.setSpacing(8)
        row_wrap_row1.addWidget(self.chk_connect_targets)
        row_wrap_row1.addWidget(self.chk_wrap_overwrite)
        row_wrap_row1.addStretch()
        lay_wrap_setup.addLayout(row_wrap_row1)

        row_bake_rig = QtWidgets.QHBoxLayout()
        row_bake_rig.setSpacing(4)
        row_bake_rig.addWidget(grp_wrap_setup, 9)
        row_bake_rig.addWidget(grp_rig, 11)
        lay_mod.addLayout(row_bake_rig)

        # Row 0 — Scale & Push (4)
        grp_mod.add_compact_action(f"{_icons_dir}/multiply_delta.png", "Multiply Deltas",       self._run_multiply)
        grp_mod.add_compact_action(f"{_icons_dir}/invert_delta.png",   "Invert Deltas",         self._run_invert_deltas)
        grp_mod.add_compact_action(f"{_icons_dir}/nullify_delta.png",  "Nullify",               self._run_nullify)
        grp_mod.add_compact_row_break()
        # Row 1 — Smooth & Average (4)
        grp_mod.add_compact_action(f"{_icons_dir}/smooth_delta.png",   "Smooth Deltas",         self._run_smooth_deltas)
        grp_mod.add_compact_action(f"{_icons_dir}/relax_delta.png",    "Relax Deltas",          self._run_relax_deltas)
        grp_mod.add_compact_action(f"{_icons_dir}/hammer_delta.png",   "Hammer Deltas",         self._run_hammer_deltas)
        grp_mod.add_compact_action(f"{_icons_dir}/average_delta.png",  "Average Deltas",        self._run_average_deltas)
        grp_mod.add_compact_row_break()
        # Row 2 — Exchange + Bake (6)
        grp_mod.add_compact_action(f"{_icons_dir}/add_delta.png",      "Add",                   self._run_delta_add)
        grp_mod.add_compact_action(f"{_icons_dir}/sub_delta.png",      "Subtract",              self._run_delta_sub)
        grp_mod.add_compact_action(f"{_icons_dir}/transfer_delta.png", "Transfer",              self._run_delta_transfer)
        grp_mod.add_compact_action(f"{_icons_dir}/swap_delta.png",     "Swap",                  self._run_delta_swap_pure)
        grp_mod.add_compact_action(f"{_icons_dir}/bake_moves.png",     "Bake Moves",            self._run_apply_moves)
        grp_mod.add_compact_action(f"{_icons_dir}/bake_deformer.png",  "Bake Defs",             self._run_bake_deformers)
        grp_mod.add_compact_row_break()
        # Row 3 — Clipboard & Select (4)
        grp_mod.add_compact_action(f"{_icons_dir}/copy_delta.png",     "Copy Delta",            self._run_copy_delta)
        grp_mod.add_compact_action(f"{_icons_dir}/paste_delta.png",    "Paste Delta",           self._run_paste_delta)
        grp_mod.add_compact_action(f"{_icons_dir}/prune_delta.png",    "Prune Small Deltas",    self._run_prune_deltas)
        grp_mod.add_compact_action(f"{_icons_dir}/select_delta.png",   "Select Vertices with Deltas", self._run_select_delta_vertices)
        grp_mod.add_compact_row_break()
        # Row 4 — Extract & Rig (4)
        grp_mod.add_compact_action(f"{_icons_dir}/wrap_extract.png",   "Extract Wrap Targets",  self._run_wrap_extract)
        grp_mod.add_compact_action(f"{_icons_dir}/extract_only.png",   "Extract Only",          self._run_extract_only)
        grp_mod.add_compact_action(f"{_icons_dir}/delta_cluster.png",  "Create Delta Cluster",  self._run_delta_cluster)
        grp_mod.add_compact_action(f"{_icons_dir}/delta_joint.png",    "Create Delta Joint",    self._run_delta_joint)
        grp_mod.finalize_compact()

        # ── Compact XYZ row (prepended at top of compact shelf) ───────────────
        _cxyz_w = QtWidgets.QWidget()
        _cxyz_lay = QtWidgets.QHBoxLayout(_cxyz_w)
        _cxyz_lay.setContentsMargins(2, 2, 2, 2)
        _cxyz_lay.setSpacing(4)

        self._compact_sign_btn = QtWidgets.QToolButton()
        self._compact_sign_btn.setFixedSize(28, 28)
        self._compact_sign_btn.setIconSize(QtCore.QSize(24, 24))
        self._compact_sign_btn.setAutoRaise(True)
        self._compact_sign_btn.setStyleSheet("""
            QToolButton { background: transparent; border: none; border-radius: 3px; padding: 1px; }
            QToolButton:hover   { background: rgba(255,255,255,30); }
            QToolButton:pressed { background: rgba(0,0,0,40); }
        """)
        self._compact_sign_btn.setIcon(QtGui.QIcon(f"{_icons_dir}/plus.png"))
        def _cxyz_sign_clicked():
            self._mult_sign *= -1.0
            _ico = QtGui.QIcon(f"{_icons_dir}/minus.png") if self._mult_sign < 0 \
                   else QtGui.QIcon(f"{_icons_dir}/plus.png")
            self.btn_mult_sign.setIcon(_ico)
            self._compact_sign_btn.setIcon(_ico)
        self._compact_sign_btn.clicked.connect(_cxyz_sign_clicked)



        self._compact_mult_fld = _make_factor_field("1.20")
        self._compact_mult_fld.setToolTip("Scale factor (XYZ linked).")
        def _cxyz_fld_edited():
            val = self._compact_mult_fld.text()
            for fld in self._mult_fields:
                fld.setText(val)
        self._compact_mult_fld.editingFinished.connect(_cxyz_fld_edited)

        self._compact_normal_btn = QtWidgets.QToolButton()
        self._compact_normal_btn.setCheckable(True)
        self._compact_normal_btn.setChecked(False)
        self._compact_normal_btn.setFixedSize(28, 28)
        self._compact_normal_btn.setIconSize(QtCore.QSize(24, 24))
        self._compact_normal_btn.setAutoRaise(True)
        self._compact_normal_btn.setStyleSheet("""
            QToolButton { background: transparent; border: none; border-radius: 3px; padding: 1px; }
            QToolButton:hover   { background: rgba(255,255,255,30); }
            QToolButton:pressed { background: rgba(0,0,0,40); }
            QToolButton:checked { background: rgba(80,120,200,60);
                                  border: 1px solid rgba(100,150,255,150); }
        """)
        if not _px_normal.isNull():
            self._compact_normal_btn.setIcon(QtGui.QIcon(_px_normal))
        self._compact_normal_btn.setToolTip("Normal Push mode.")
        self._compact_normal_btn.toggled.connect(
            lambda c: self._btn_normal_mode.setChecked(c)
            if self._btn_normal_mode.isChecked() != c else None)
        self._btn_normal_mode.toggled.connect(
            lambda c: self._compact_normal_btn.setChecked(c)
            if self._compact_normal_btn.isChecked() != c else None)

        _cxyz_group = QtWidgets.QWidget()
        _cxyz_group_lay = QtWidgets.QHBoxLayout(_cxyz_group)
        _cxyz_group_lay.setContentsMargins(0, 0, 0, 0)
        _cxyz_group_lay.setSpacing(0)
        _cxyz_group_lay.addWidget(self._compact_mult_fld)
        _cxyz_group_lay.addWidget(self._compact_normal_btn)

        _cxyz_lay.addWidget(self._compact_sign_btn)
        _cxyz_lay.addWidget(_cxyz_group)
        _cxyz_lay.addStretch()
        grp_mod.compact_shelf.layout().insertWidget(0, _cxyz_w)

        root.addWidget(grp_mod)

        # ── Tools ─────────────────────────────────────────────────────────────
        grp_tools, _body_tools, lay_tools = self._collapsible_section("Tools", two_state=True, initial_state=0)
        lay_tools.setSpacing(6)

        lay_tools.addWidget(grp_split)


        grp_wire, _body_wire, lay_wire = self._collapsible_section(
            "Wire Setup", two_state=True, initial_state=0)
        lay_wire.setSpacing(6)

        # Paint Wire Weights — shelf button
        _wire_shelf_row = QtWidgets.QHBoxLayout()
        btn_paint_wire = QtWidgets.QToolButton()
        btn_paint_wire.setFixedSize(36, 36)
        btn_paint_wire.setToolButtonStyle(QtCore.Qt.ToolButtonIconOnly)
        btn_paint_wire.setToolTip("Paint Wire Weights\nOpens the Paint Attributes tool on wire_setup_wire.weights.")
        btn_paint_wire.setStyleSheet("""
            QToolButton { background-color: transparent; border: none; border-radius: 3px; padding: 2px; }
            QToolButton:hover { background-color: rgba(255,255,255,30); }
            QToolButton:pressed { background-color: rgba(0,0,0,40); }
        """)
        _pw_px = QtGui.QPixmap(f"{_icons_dir}/paint_wire.png")
        if not _pw_px.isNull():
            btn_paint_wire.setIcon(QtGui.QIcon(_pw_px))
            btn_paint_wire.setIconSize(QtCore.QSize(34, 34))
        btn_paint_wire.clicked.connect(self._run_paint_wire)
        btn_paint_wire.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        btn_paint_wire.customContextMenuRequested.connect(
            lambda _: self._open_tool_settings()
        )
        _f_pw = _DblClickFilter(self._open_tool_settings, btn_paint_wire)
        btn_paint_wire.installEventFilter(_f_pw)
        _wire_shelf_row.addWidget(btn_paint_wire)

        # Mirror Wire Weights — shelf button
        btn_mirror_wire = QtWidgets.QToolButton()
        btn_mirror_wire.setFixedSize(36, 36)
        btn_mirror_wire.setToolButtonStyle(QtCore.Qt.ToolButtonIconOnly)
        btn_mirror_wire.setToolTip(
            "Mirror Wire Weights (YZ)\n"
            "Mirrors wire deformer weights from -X to +X.\n\n"
            "WARNING: Your mesh must be symmetrical and in neutral pose,\n"
            "otherwise results will be unpredictable."
        )
        btn_mirror_wire.setStyleSheet("""
            QToolButton { background-color: transparent; border: none; border-radius: 3px; padding: 2px; }
            QToolButton:hover { background-color: rgba(255,255,255,30); }
            QToolButton:pressed { background-color: rgba(0,0,0,40); }
        """)
        _mw_px = QtGui.QPixmap(f"{_icons_dir}/mirror_wire.png")
        if not _mw_px.isNull():
            btn_mirror_wire.setIcon(QtGui.QIcon(_mw_px))
            btn_mirror_wire.setIconSize(QtCore.QSize(34, 34))
        btn_mirror_wire.clicked.connect(self._run_mirror_wire_weights)
        btn_mirror_wire.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        btn_mirror_wire.customContextMenuRequested.connect(
            lambda _: cmds.MirrorDeformerWeightsOptions()
        )
        _wire_shelf_row.addWidget(btn_mirror_wire)

        # Copy / Paste Wire Weight
        _wire_btn_ss2 = """
            QToolButton { background-color: transparent; border: none; border-radius: 3px; padding: 2px; }
            QToolButton:hover { background-color: rgba(255,255,255,30); }
            QToolButton:pressed { background-color: rgba(0,0,0,40); }
            QToolButton:disabled { opacity: 0.4; }
        """
        self.btn_copy_wire_delta = QtWidgets.QToolButton()
        self.btn_copy_wire_delta.setFixedSize(36, 36)
        self.btn_copy_wire_delta.setToolButtonStyle(QtCore.Qt.ToolButtonIconOnly)
        self.btn_copy_wire_delta.setToolTip(
            "Copy Wire Weight\n"
            "Select exactly 1 vertex on a mesh with a wire deformer.\n"
            "Copies the wire weight value of that vertex into the clipboard.")
        self.btn_copy_wire_delta.setStyleSheet(_wire_btn_ss2)
        _cpw_px = QtGui.QPixmap(f"{_icons_dir}/copy_weight.png")
        if not _cpw_px.isNull():
            self.btn_copy_wire_delta.setIcon(QtGui.QIcon(_cpw_px))
            self.btn_copy_wire_delta.setIconSize(QtCore.QSize(34, 34))
        self.btn_copy_wire_delta.clicked.connect(self._run_copy_wire_delta)
        _wire_shelf_row.addWidget(self.btn_copy_wire_delta)

        self.btn_paste_wire_delta = QtWidgets.QToolButton()
        self.btn_paste_wire_delta.setFixedSize(36, 36)
        self.btn_paste_wire_delta.setToolButtonStyle(QtCore.Qt.ToolButtonIconOnly)
        self.btn_paste_wire_delta.setToolTip(
            "Paste Wire Weight\n"
            "Select one or more vertices on a mesh with a wire deformer.\n"
            "Applies the copied weight value to all selected vertices. Undoable.")
        self.btn_paste_wire_delta.setStyleSheet(_wire_btn_ss2)
        self.btn_paste_wire_delta.setEnabled(False)
        _ppw_px = QtGui.QPixmap(f"{_icons_dir}/paste_weight.png")
        if not _ppw_px.isNull():
            self.btn_paste_wire_delta.setIcon(QtGui.QIcon(_ppw_px))
            self.btn_paste_wire_delta.setIconSize(QtCore.QSize(34, 34))
        self.btn_paste_wire_delta.clicked.connect(self._run_paste_wire_delta)
        _wire_shelf_row.addWidget(self.btn_paste_wire_delta)

        btn_hammer_wire = QtWidgets.QToolButton()
        btn_hammer_wire.setFixedSize(36, 36)
        btn_hammer_wire.setToolButtonStyle(QtCore.Qt.ToolButtonIconOnly)
        btn_hammer_wire.setToolTip(
            "Hammer Wire Weights\n"
            "Select vertices on the mesh, then click.\n"
            "Averages each selected vertex's wire weight\n"
            "with its topological 1-ring neighbours.")
        btn_hammer_wire.setStyleSheet("""
            QToolButton { background-color: transparent; border: none; border-radius: 3px; padding: 2px; }
            QToolButton:hover { background-color: rgba(255,255,255,30); }
            QToolButton:pressed { background-color: rgba(0,0,0,40); }
        """)
        _hw_px = QtGui.QPixmap(f"{_icons_dir}/hammer_wire.png")
        if not _hw_px.isNull():
            btn_hammer_wire.setIcon(QtGui.QIcon(_hw_px))
            btn_hammer_wire.setIconSize(QtCore.QSize(34, 34))
        btn_hammer_wire.clicked.connect(self._run_hammer_wire_weights)
        _wire_shelf_row.addWidget(btn_hammer_wire)

        lay_wire.addLayout(_wire_shelf_row)

        # Base Mesh
        row_wbase = QtWidgets.QHBoxLayout()
        lbl_wbase = QtWidgets.QLabel("Base Mesh")
        lbl_wbase.setFixedWidth(70)
        self.edit_wire_base = QtWidgets.QLineEdit()
        self.edit_wire_base.setPlaceholderText("mesh transform")
        self.edit_wire_base.setToolTip("Base mesh to build the wire setup on")
        btn_wire_get_base = QtWidgets.QPushButton("Get")
        btn_wire_get_base.setFixedWidth(40)
        btn_wire_get_base.setToolTip("Use currently selected object as base mesh")
        btn_wire_get_base.clicked.connect(self._wire_get_base)
        row_wbase.addWidget(lbl_wbase)
        row_wbase.addWidget(self.edit_wire_base, 1)
        row_wbase.addWidget(btn_wire_get_base)
        lay_wire.addLayout(row_wbase)

        # Edges
        row_wedge = QtWidgets.QHBoxLayout()
        lbl_wedge = QtWidgets.QLabel("Edges")
        lbl_wedge.setFixedWidth(70)
        self.edit_wire_edges = QtWidgets.QLineEdit()
        self.edit_wire_edges.setReadOnly(True)
        self.edit_wire_edges.setPlaceholderText("select an edge loop then click Get")
        self.edit_wire_edges.setToolTip("Edge loop used to extract the wire curve")
        btn_wire_get_edges = QtWidgets.QPushButton("Get")
        btn_wire_get_edges.setFixedWidth(40)
        btn_wire_get_edges.setToolTip("Capture current edge selection")
        btn_wire_get_edges.clicked.connect(self._wire_get_edges)
        row_wedge.addWidget(lbl_wedge)
        row_wedge.addWidget(self.edit_wire_edges, 1)
        row_wedge.addWidget(btn_wire_get_edges)
        lay_wire.addLayout(row_wedge)

        # Shape Curves list
        lbl_shapes = QtWidgets.QLabel("Shape Curves  (double-click to rename)")
        lbl_shapes.setStyleSheet("color: #aaaaaa; font-size: 10px;")
        lay_wire.addWidget(lbl_shapes)

        self.list_wire_shapes = QtWidgets.QListWidget()
        self.list_wire_shapes.setFixedHeight(116)
        self.list_wire_shapes.setToolTip(
            "Each entry creates one blendShape target curve.\n"
            "Double-click to rename. Use Add / Remove to edit the list.")
        for _shp in ["lip_up", "lip_dn", "lip_out", "lip_in",
                     "mouth_corner_out", "mouth_corner_in",
                     "mouth_corner_up", "mouth_corner_dn"]:
            _item = QtWidgets.QListWidgetItem(_shp)
            _item.setFlags(_item.flags() | QtCore.Qt.ItemIsEditable)
            self.list_wire_shapes.addItem(_item)
        lay_wire.addWidget(self.list_wire_shapes)

        row_shapes_ctrl = QtWidgets.QHBoxLayout()
        self.edit_wire_shape_add = QtWidgets.QLineEdit()
        self.edit_wire_shape_add.setPlaceholderText("new shape name")
        self.edit_wire_shape_add.returnPressed.connect(self._wire_add_shape)
        _wire_btn_ss = "font-size: 12px; font-weight: bold;"
        btn_wire_add_shape = QtWidgets.QPushButton("+")
        btn_wire_add_shape.setFixedSize(22, 22)
        btn_wire_add_shape.setFocusPolicy(QtCore.Qt.NoFocus)
        btn_wire_add_shape.setStyleSheet(_wire_btn_ss)
        btn_wire_add_shape.clicked.connect(self._wire_add_shape)
        btn_wire_rm_shape = QtWidgets.QPushButton("−")
        btn_wire_rm_shape.setFixedSize(22, 22)
        btn_wire_rm_shape.setFocusPolicy(QtCore.Qt.NoFocus)
        btn_wire_rm_shape.setStyleSheet(_wire_btn_ss)
        btn_wire_rm_shape.setToolTip("Remove selected shape from the list")
        btn_wire_rm_shape.clicked.connect(self._wire_remove_shape)
        row_shapes_ctrl.addWidget(self.edit_wire_shape_add, 1)
        row_shapes_ctrl.addWidget(btn_wire_add_shape)
        row_shapes_ctrl.addWidget(btn_wire_rm_shape)
        lay_wire.addLayout(row_shapes_ctrl)

        # Dropoff / Rotation / Spans / Flat Curve
        row_wparams = QtWidgets.QHBoxLayout()
        row_wparams.addWidget(QtWidgets.QLabel("Dropoff"))
        self.spin_wire_dropoff = QtWidgets.QLineEdit("100.0")
        self.spin_wire_dropoff.setFixedWidth(60)
        self.spin_wire_dropoff.setAlignment(QtCore.Qt.AlignCenter)
        _dv = QtGui.QDoubleValidator(0.1, 9999.0, 1, self.spin_wire_dropoff)
        _dv.setLocale(QtCore.QLocale(QtCore.QLocale.English))
        self.spin_wire_dropoff.setValidator(_dv)
        self.spin_wire_dropoff.setToolTip("Wire deformer dropoff distance")
        row_wparams.addWidget(self.spin_wire_dropoff)
        row_wparams.addSpacing(8)
        row_wparams.addWidget(QtWidgets.QLabel("Rotation"))
        self.spin_wire_rotation = QtWidgets.QLineEdit("0.00")
        self.spin_wire_rotation.setFixedWidth(50)
        self.spin_wire_rotation.setAlignment(QtCore.Qt.AlignCenter)
        _rv = QtGui.QDoubleValidator(0.0, 1.0, 2, self.spin_wire_rotation)
        _rv.setLocale(QtCore.QLocale(QtCore.QLocale.English))
        self.spin_wire_rotation.setValidator(_rv)
        self.spin_wire_rotation.setToolTip("Wire deformer rotation value")
        row_wparams.addWidget(self.spin_wire_rotation)
        row_wparams.addSpacing(8)
        row_wparams.addWidget(QtWidgets.QLabel("Spans"))
        self.spin_wire_spans = QtWidgets.QLineEdit("4")
        self.spin_wire_spans.setFixedWidth(44)
        self.spin_wire_spans.setAlignment(QtCore.Qt.AlignCenter)
        self.spin_wire_spans.setValidator(QtGui.QIntValidator(1, 64, self.spin_wire_spans))
        self.spin_wire_spans.setToolTip(
            "Number of spans for the rebuilt wire curve (rebuildCurve s=N).\n"
            "More spans = more CVs = finer control.")
        row_wparams.addWidget(self.spin_wire_spans)
        row_wparams.addStretch()
        lay_wire.addLayout(row_wparams)

        self.chk_wire_flat = QtWidgets.QCheckBox("Flat Curve")
        self.chk_wire_flat.setChecked(True)
        self.chk_wire_flat.setToolTip(
            "Flatten all CVs to the Y position of the first CV.\n"
            "Useful for lips or any edge loop that should remain planar.\n"
            "Disable for curved surfaces (cheeks, eyelids…).")
        lay_wire.addWidget(self.chk_wire_flat)

        # Create Wire Setup
        _wire_action_ss = """
            QToolButton { background-color: transparent; border: none; border-radius: 3px; padding: 2px; }
            QToolButton:hover { background-color: rgba(255,255,255,30); }
            QToolButton:pressed { background-color: rgba(0,0,0,40); }
        """
        row_create_wire = QtWidgets.QHBoxLayout()
        row_create_wire.setSpacing(4)
        row_create_wire.addWidget(QtWidgets.QLabel("Create Wire Setup"))
        btn_create_wire = QtWidgets.QToolButton()
        btn_create_wire.setFixedSize(36, 36)
        btn_create_wire.setToolButtonStyle(QtCore.Qt.ToolButtonIconOnly)
        btn_create_wire.setToolTip(
            "Creates wire_setup_msh, wire_crv, wire_bs and the wire deformer\n"
            "from the base mesh and edge selection above.")
        btn_create_wire.setStyleSheet(_wire_action_ss)
        btn_create_wire.setIcon(QtGui.QIcon(f"{_icons_dir}/create_wire_setup.png"))
        btn_create_wire.setIconSize(QtCore.QSize(34, 34))
        btn_create_wire.clicked.connect(self._run_create_wire_setup)
        row_create_wire.addWidget(btn_create_wire)
        row_create_wire.addStretch(1)
        row_create_wire.addWidget(QtWidgets.QLabel("Bake Wire to Mesh"))
        self._wire_delete_after_bake = False
        btn_bake_wire = QtWidgets.QToolButton()
        btn_bake_wire.setFixedSize(36, 36)
        btn_bake_wire.setToolButtonStyle(QtCore.Qt.ToolButtonIconOnly)
        btn_bake_wire.setToolTip(
            "Bake Wire to Mesh\n"
            "For each shape curve, poses wire_setup_msh and adds the result\n"
            "as a blendShape target on the base mesh's bs_node.\n"
            "Existing targets with the same name are overwritten.\n\n"
            "Right-click to toggle Delete Wire at Bake.")
        btn_bake_wire.setStyleSheet(_wire_action_ss)
        _bw_px = QtGui.QPixmap(f"{_icons_dir}/bake_wire.png")
        if not _bw_px.isNull():
            btn_bake_wire.setIcon(QtGui.QIcon(_bw_px))
            btn_bake_wire.setIconSize(QtCore.QSize(34, 34))
        btn_bake_wire.clicked.connect(self._run_bake_wire)
        btn_bake_wire.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        def _bake_wire_ctx_menu(pos):
            menu = QtWidgets.QMenu(btn_bake_wire)
            act_del = menu.addAction("Delete Wire at Bake")
            act_del.setCheckable(True)
            act_del.setChecked(self._wire_delete_after_bake)
            act_del.toggled.connect(lambda v: setattr(self, "_wire_delete_after_bake", v))
            menu.exec_(btn_bake_wire.mapToGlobal(pos))
        btn_bake_wire.customContextMenuRequested.connect(_bake_wire_ctx_menu)
        row_create_wire.addWidget(btn_bake_wire)
        lay_wire.addLayout(row_create_wire)

        lay_tools.addWidget(grp_wire)

        # ── Cluster to Joint ───────────────────────────────────────────────
        grp_ctj, _body_ctj, lay_ctj = self._collapsible_section(
            "Cluster to Joint", two_state=True, initial_state=0)
        lay_ctj.setSpacing(6)

        # Shelf row — Paint / Mirror / Copy / Paste cluster weights
        _ctj_shelf_row = QtWidgets.QHBoxLayout()
        _ctj_shelf_ss = """
            QToolButton { background-color: transparent; border: none; border-radius: 3px; padding: 2px; }
            QToolButton:hover { background-color: rgba(255,255,255,30); }
            QToolButton:pressed { background-color: rgba(0,0,0,40); }
        """
        _ctj_shelf_ss_dis = _ctj_shelf_ss + "QToolButton:disabled { opacity: 0.4; }"

        btn_paint_ctj = QtWidgets.QToolButton()
        btn_paint_ctj.setFixedSize(36, 36)
        btn_paint_ctj.setToolButtonStyle(QtCore.Qt.ToolButtonIconOnly)
        btn_paint_ctj.setToolTip(
            "Paint Cluster Weights\n"
            "Selects the mesh and opens the Paint Attributes tool on cluster.weights.\n"
            "Right-click or double-click to open Tool Settings.")
        btn_paint_ctj.setStyleSheet(_ctj_shelf_ss)
        _pctj_px = QtGui.QPixmap(f"{_icons_dir}/paint_cluster.png")
        if not _pctj_px.isNull():
            btn_paint_ctj.setIcon(QtGui.QIcon(_pctj_px))
            btn_paint_ctj.setIconSize(QtCore.QSize(34, 34))
        btn_paint_ctj.clicked.connect(self._run_paint_ctj)
        btn_paint_ctj.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        btn_paint_ctj.customContextMenuRequested.connect(lambda _: self._open_tool_settings())
        _f_pctj = _DblClickFilter(self._open_tool_settings, btn_paint_ctj)
        btn_paint_ctj.installEventFilter(_f_pctj)
        _ctj_shelf_row.addWidget(btn_paint_ctj)

        btn_mirror_ctj = QtWidgets.QToolButton()
        btn_mirror_ctj.setFixedSize(36, 36)
        btn_mirror_ctj.setToolButtonStyle(QtCore.Qt.ToolButtonIconOnly)
        btn_mirror_ctj.setToolTip(
            "Mirror Cluster Weights (YZ)\n"
            "Mirrors cluster weights from -X to +X.\n\n"
            "WARNING: Mesh must be symmetrical and in neutral pose.\n"
            "Right-click to open Mirror Deformer Weights options.")
        btn_mirror_ctj.setStyleSheet(_ctj_shelf_ss)
        _mctj_px = QtGui.QPixmap(f"{_icons_dir}/mirror_wire.png")
        if not _mctj_px.isNull():
            btn_mirror_ctj.setIcon(QtGui.QIcon(_mctj_px))
            btn_mirror_ctj.setIconSize(QtCore.QSize(34, 34))
        btn_mirror_ctj.clicked.connect(self._run_mirror_ctj)
        btn_mirror_ctj.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        btn_mirror_ctj.customContextMenuRequested.connect(
            lambda _: cmds.MirrorDeformerWeightsOptions())
        _ctj_shelf_row.addWidget(btn_mirror_ctj)

        self.btn_copy_ctj_weight = QtWidgets.QToolButton()
        self.btn_copy_ctj_weight.setFixedSize(36, 36)
        self.btn_copy_ctj_weight.setToolButtonStyle(QtCore.Qt.ToolButtonIconOnly)
        self.btn_copy_ctj_weight.setToolTip(
            "Copy Cluster Weight\n"
            "Select exactly 1 vertex on the mesh.\n"
            "Copies its cluster weight value into the clipboard.")
        self.btn_copy_ctj_weight.setStyleSheet(_ctj_shelf_ss_dis)
        _cpctj_px = QtGui.QPixmap(f"{_icons_dir}/copy_weight.png")
        if not _cpctj_px.isNull():
            self.btn_copy_ctj_weight.setIcon(QtGui.QIcon(_cpctj_px))
            self.btn_copy_ctj_weight.setIconSize(QtCore.QSize(34, 34))
        self.btn_copy_ctj_weight.clicked.connect(self._run_copy_ctj_weight)
        _ctj_shelf_row.addWidget(self.btn_copy_ctj_weight)

        self.btn_paste_ctj_weight = QtWidgets.QToolButton()
        self.btn_paste_ctj_weight.setFixedSize(36, 36)
        self.btn_paste_ctj_weight.setToolButtonStyle(QtCore.Qt.ToolButtonIconOnly)
        self.btn_paste_ctj_weight.setToolTip(
            "Paste Cluster Weight\n"
            "Select one or more vertices.\n"
            "Applies the copied weight to all selected vertices. Undoable.")
        self.btn_paste_ctj_weight.setStyleSheet(_ctj_shelf_ss_dis)
        self.btn_paste_ctj_weight.setEnabled(False)
        _ppctj_px = QtGui.QPixmap(f"{_icons_dir}/paste_weight.png")
        if not _ppctj_px.isNull():
            self.btn_paste_ctj_weight.setIcon(QtGui.QIcon(_ppctj_px))
            self.btn_paste_ctj_weight.setIconSize(QtCore.QSize(34, 34))
        self.btn_paste_ctj_weight.clicked.connect(self._run_paste_ctj_weight)
        _ctj_shelf_row.addWidget(self.btn_paste_ctj_weight)

        btn_hammer_ctj = QtWidgets.QToolButton()
        btn_hammer_ctj.setFixedSize(36, 36)
        btn_hammer_ctj.setToolButtonStyle(QtCore.Qt.ToolButtonIconOnly)
        btn_hammer_ctj.setToolTip(
            "Hammer Cluster Weights\n"
            "Select vertices on the mesh, then click.\n"
            "Averages each selected vertex's cluster weight\n"
            "with its topological 1-ring neighbours.")
        btn_hammer_ctj.setStyleSheet(_ctj_shelf_ss)
        _hctj_px = QtGui.QPixmap(f"{_icons_dir}/hammer_cluster.png")
        if not _hctj_px.isNull():
            btn_hammer_ctj.setIcon(QtGui.QIcon(_hctj_px))
            btn_hammer_ctj.setIconSize(QtCore.QSize(34, 34))
        btn_hammer_ctj.clicked.connect(self._run_hammer_ctj_weights)
        _ctj_shelf_row.addWidget(btn_hammer_ctj)

        lay_ctj.addLayout(_ctj_shelf_row)

        # Mesh
        row_ctj_mesh = QtWidgets.QHBoxLayout()
        lbl_ctj_mesh = QtWidgets.QLabel("Mesh")
        lbl_ctj_mesh.setFixedWidth(60)
        self.edit_ctj_mesh = QtWidgets.QLineEdit()
        self.edit_ctj_mesh.setPlaceholderText("mesh transform")
        self.edit_ctj_mesh.setToolTip("Mesh that has the cluster deformer")
        self.edit_ctj_mesh.textChanged.connect(self._ctj_refresh_clusters)
        btn_ctj_pick = QtWidgets.QPushButton("Pick")
        btn_ctj_pick.setFixedWidth(40)
        btn_ctj_pick.setToolTip("Use currently selected object as mesh")
        btn_ctj_pick.clicked.connect(self._ctj_pick_mesh)
        row_ctj_mesh.addWidget(lbl_ctj_mesh)
        row_ctj_mesh.addWidget(self.edit_ctj_mesh, 1)
        row_ctj_mesh.addWidget(btn_ctj_pick)
        lay_ctj.addLayout(row_ctj_mesh)

        # Cluster
        row_ctj_cls = QtWidgets.QHBoxLayout()
        lbl_ctj_cls = QtWidgets.QLabel("Cluster")
        lbl_ctj_cls.setFixedWidth(60)
        self.combo_ctj_cluster = QtWidgets.QComboBox()
        self.combo_ctj_cluster.setToolTip("Cluster deformer on the mesh above")
        self.combo_ctj_cluster.currentTextChanged.connect(self._ctj_try_restore_setup)
        row_ctj_cls.addWidget(lbl_ctj_cls)
        row_ctj_cls.addWidget(self.combo_ctj_cluster, 1)
        lay_ctj.addLayout(row_ctj_cls)

        # Setup button
        _ctj_action_ss = """
            QToolButton { background-color: transparent; border: none; border-radius: 3px; padding: 2px; }
            QToolButton:hover { background-color: rgba(255,255,255,30); }
            QToolButton:pressed { background-color: rgba(0,0,0,40); }
        """
        row_ctj_setup = QtWidgets.QHBoxLayout()
        row_ctj_setup.setSpacing(4)
        row_ctj_setup.addWidget(QtWidgets.QLabel("Cluster to Joint Setup"))
        btn_ctj_setup = QtWidgets.QToolButton()
        btn_ctj_setup.setFixedSize(36, 36)
        btn_ctj_setup.setToolButtonStyle(QtCore.Qt.ToolButtonIconOnly)
        btn_ctj_setup.setToolTip(
            "Copy cluster weights to a new joint+skinCluster.\n"
            "The cluster is disabled so you can paint skin weights freely.")
        btn_ctj_setup.setStyleSheet(_ctj_action_ss)
        _ctj_px = QtGui.QPixmap(f"{_icons_dir}/cluster_to_joint.png")
        if not _ctj_px.isNull():
            btn_ctj_setup.setIcon(QtGui.QIcon(_ctj_px))
            btn_ctj_setup.setIconSize(QtCore.QSize(34, 34))
        btn_ctj_setup.clicked.connect(self._run_ctj_setup)
        row_ctj_setup.addWidget(btn_ctj_setup)
        row_ctj_setup.addStretch(1)
        row_ctj_setup.addWidget(QtWidgets.QLabel("Bake to Cluster"))
        btn_ctj_bake = QtWidgets.QToolButton()
        btn_ctj_bake.setFixedSize(36, 36)
        btn_ctj_bake.setToolButtonStyle(QtCore.Qt.ToolButtonIconOnly)
        btn_ctj_bake.setToolTip(
            "Write the current skinCluster joint weights back into the cluster\n"
            "and re-enable the cluster deformer.")
        btn_ctj_bake.setStyleSheet(_ctj_action_ss)
        _ctj_bake_px = QtGui.QPixmap(f"{_icons_dir}/bake_to_cluster.png")
        if not _ctj_bake_px.isNull():
            btn_ctj_bake.setIcon(QtGui.QIcon(_ctj_bake_px))
            btn_ctj_bake.setIconSize(QtCore.QSize(34, 34))
        btn_ctj_bake.clicked.connect(self._run_ctj_bake)
        row_ctj_setup.addWidget(btn_ctj_bake)
        lay_ctj.addLayout(row_ctj_setup)

        # Delete Joint Setup at Bake toggle
        row_ctj_opts = QtWidgets.QHBoxLayout()
        self.chk_ctj_delete_joints = QtWidgets.QCheckBox("Delete Joint Setup at Bake")
        self.chk_ctj_delete_joints.setToolTip(
            "When checked, the skinCluster, joints and their group are deleted after baking.\n"
            "When unchecked, they are kept.")
        row_ctj_opts.addWidget(self.chk_ctj_delete_joints)
        row_ctj_opts.addStretch(1)
        lay_ctj.addLayout(row_ctj_opts)

        lay_tools.addWidget(grp_ctj)

        # ── Copy Deformer Weights ──────────────────────────────────────────
        grp_cdw, _body_cdw, lay_cdw = self._collapsible_section(
            "Copy Deformer Weights", two_state=True, initial_state=0)
        lay_cdw.setSpacing(6)

        # Source label
        lbl_cdw_src_hdr = QtWidgets.QLabel("Source")
        lbl_cdw_src_hdr.setStyleSheet("color: #aaaaaa; font-size: 10px;")
        lay_cdw.addWidget(lbl_cdw_src_hdr)

        # Source Mesh
        row_cdw_src_mesh = QtWidgets.QHBoxLayout()
        lbl_cdw_sm = QtWidgets.QLabel("Mesh")
        lbl_cdw_sm.setFixedWidth(60)
        self.edit_cdw_src_mesh = QtWidgets.QLineEdit()
        self.edit_cdw_src_mesh.setPlaceholderText("source mesh")
        self.edit_cdw_src_mesh.setToolTip("Mesh that holds the source deformer")
        self.edit_cdw_src_mesh.textChanged.connect(
            lambda: self._cdw_refresh_deformers(src=True))
        btn_cdw_pick_src = QtWidgets.QPushButton("Pick")
        btn_cdw_pick_src.setFixedWidth(40)
        btn_cdw_pick_src.setToolTip("Use sel[0] as source mesh")
        btn_cdw_pick_src.clicked.connect(self._cdw_pick_source)
        row_cdw_src_mesh.addWidget(lbl_cdw_sm)
        row_cdw_src_mesh.addWidget(self.edit_cdw_src_mesh, 1)
        row_cdw_src_mesh.addWidget(btn_cdw_pick_src)
        lay_cdw.addLayout(row_cdw_src_mesh)

        # Source Deformer
        row_cdw_src_def = QtWidgets.QHBoxLayout()
        lbl_cdw_sd = QtWidgets.QLabel("Deformer")
        lbl_cdw_sd.setFixedWidth(60)
        self.combo_cdw_src_deformer = QtWidgets.QComboBox()
        self.combo_cdw_src_deformer.setToolTip("Source deformer (auto-populated from mesh above)")
        row_cdw_src_def.addWidget(lbl_cdw_sd)
        row_cdw_src_def.addWidget(self.combo_cdw_src_deformer, 1)
        lay_cdw.addLayout(row_cdw_src_def)

        _cdw_sep1 = QtWidgets.QFrame()
        _cdw_sep1.setFrameShape(QtWidgets.QFrame.HLine)
        lay_cdw.addWidget(_cdw_sep1)

        # Target label
        lbl_cdw_tgt_hdr = QtWidgets.QLabel("Target")
        lbl_cdw_tgt_hdr.setStyleSheet("color: #aaaaaa; font-size: 10px;")
        lay_cdw.addWidget(lbl_cdw_tgt_hdr)

        # Target Mesh
        row_cdw_tgt_mesh = QtWidgets.QHBoxLayout()
        lbl_cdw_tm = QtWidgets.QLabel("Mesh")
        lbl_cdw_tm.setFixedWidth(60)
        self.edit_cdw_tgt_mesh = QtWidgets.QLineEdit()
        self.edit_cdw_tgt_mesh.setReadOnly(True)
        self.edit_cdw_tgt_mesh.setPlaceholderText("target mesh(es)")
        self.edit_cdw_tgt_mesh.setToolTip("One or more target meshes")
        btn_cdw_pick_tgt = QtWidgets.QPushButton("Pick")
        btn_cdw_pick_tgt.setFixedWidth(40)
        btn_cdw_pick_tgt.setToolTip(
            "Pick target mesh(es) from selection.\n"
            "If source is already set and is first in selection, uses sel[1:].")
        btn_cdw_pick_tgt.clicked.connect(self._cdw_pick_targets)
        row_cdw_tgt_mesh.addWidget(lbl_cdw_tm)
        row_cdw_tgt_mesh.addWidget(self.edit_cdw_tgt_mesh, 1)
        row_cdw_tgt_mesh.addWidget(btn_cdw_pick_tgt)
        lay_cdw.addLayout(row_cdw_tgt_mesh)

        # Target Deformer
        row_cdw_tgt_def = QtWidgets.QHBoxLayout()
        lbl_cdw_td = QtWidgets.QLabel("Deformer")
        lbl_cdw_td.setFixedWidth(60)
        self.combo_cdw_tgt_deformer = QtWidgets.QComboBox()
        self.combo_cdw_tgt_deformer.setToolTip(
            "Target deformer (auto-populated from first target mesh).\n"
            "Can be the same node as the source (e.g. one cluster on multiple meshes).")
        row_cdw_tgt_def.addWidget(lbl_cdw_td)
        row_cdw_tgt_def.addWidget(self.combo_cdw_tgt_deformer, 1)
        lay_cdw.addLayout(row_cdw_tgt_def)

        # Copy button
        btn_cdw_copy = QtWidgets.QPushButton("Copy Weights")
        btn_cdw_copy.setToolTip(
            "Copy per-vertex weights from source deformer to target deformer.\n"
            "Same vertex count → direct 1:1 copy.\n"
            "Different topology → closest-surface-point spatial transfer.\n\n"
            "Note: skinCluster and blendShape are not supported.")
        btn_cdw_copy.clicked.connect(self._run_copy_deformer_weights)
        lay_cdw.addWidget(btn_cdw_copy)

        lay_tools.addWidget(grp_cdw)

        # ── Joints Setup ──────────────────────────────────────────────────
        grp_joints, _body_joints, lay_joints = self._collapsible_section(
            "Joints Setup", two_state=True, initial_state=0)
        lay_joints.setSpacing(6)

        # Middle Edge row
        row_mid = QtWidgets.QHBoxLayout()
        lbl_mid = QtWidgets.QLabel("Middle Edge")
        lbl_mid.setFixedWidth(80)
        self.line_joints_middle = QtWidgets.QLineEdit()
        self.line_joints_middle.setReadOnly(True)
        self.line_joints_middle.setPlaceholderText("— select an edge —")
        self.line_joints_middle.setToolTip(
            "Edge on the edge loop that defines bone/controller positions.\n"
            "The full edge loop is extracted and split into upper/lower arcs.")
        btn_mid_get = QtWidgets.QPushButton("Get")
        btn_mid_get.setFixedWidth(40)
        btn_mid_get.clicked.connect(self._joints_get_middle)
        row_mid.addWidget(lbl_mid)
        row_mid.addWidget(self.line_joints_middle)
        row_mid.addWidget(btn_mid_get)
        lay_joints.addLayout(row_mid)


        btn_build_rig = QtWidgets.QPushButton("Build Rig")
        btn_build_rig.setToolTip(
            "Extracts both edge loops, splits them at the lip corners,\n"
            "builds NURBS curves, duplicates the mesh, and creates\n"
            "the full joint hierarchy (13 influences + zero_out).")
        btn_build_rig.clicked.connect(self._run_build_lip_rig)
        lay_joints.addWidget(btn_build_rig)


        grp_joints.setEnabled(False)
        lay_tools.addWidget(grp_joints)

        root.addWidget(grp_tools)

        root.addStretch(1)

        scroll.setWidget(inner)

        # ── Progress bar + Status + Version pinned below scroll ─────────────
        self.progress_bar = QtWidgets.QProgressBar()
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setVisible(False)

        self.lbl_top_status = QtWidgets.QLabel("— no selection —")
        self.lbl_top_status.setAlignment(QtCore.Qt.AlignCenter)
        self.lbl_top_status.setStyleSheet("color: #666666; font-size: 11px; padding-top: 4px; padding-bottom: 4px;")

        self.lbl_status = QtWidgets.QLabel("")
        self.lbl_status.setAlignment(QtCore.Qt.AlignCenter | QtCore.Qt.AlignVCenter)

        lbl_version = QtWidgets.QLabel(self.VERSION)
        lbl_version.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        lbl_version.setStyleSheet("color: #7a7a7a; font-size: 10px;")

        bottom_wrapper = QtWidgets.QWidget()
        bottom_lay = QtWidgets.QVBoxLayout(bottom_wrapper)
        bottom_lay.setContentsMargins(8, 2, 8, 4)
        bottom_lay.setSpacing(0)
        bottom_lay.addWidget(self.progress_bar)
        bottom_lay.addWidget(self.lbl_top_status)
        bottom_lay.addWidget(self.lbl_status)
        bottom_lay.addWidget(lbl_version)
        outer_layout.addWidget(bottom_wrapper)

        self.setStyleSheet("QLineEdit { border: none; border-radius: 3px; padding: 0px 4px; }")

        self._update_single_loc_state()

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _auto_suffixes(self, n):
        if n == 0:
            return []
        if getattr(self, "_suffix_template", "abc") == "positional" and n <= 3:
            return {1: ["in"], 2: ["in", "out"], 3: ["in", "mid", "out"]}[n]
        return [chr(ord('a') + i) for i in range(n)]

    def _resize_table_to_content(self):
        header_h   = self.table.horizontalHeader().height()
        rows_h     = sum(self.table.rowHeight(i) for i in range(self.table.rowCount()))
        content_h  = header_h + rows_h + 2
        final_h    = max(content_h, self._loc_grid_h)
        self.table.setMinimumHeight(final_h)
        self.table.setMaximumHeight(final_h)

    # ── Split presets (stored on Maya nodes via bse_split_data attribute) ────────

    _BSE_ATTR      = "bse_split_data"
    _LOCS_GRP_NAME = "Splits_locs_grp"

    def _ensure_locs_grp(self):
        """
        Return the main locs_grp node (long name), creating it if necessary.
        Priority:
          1. self._current_locs_grp if it still exists in the scene
          2. First node named _LOCS_GRP_NAME found in the scene
          3. A new empty group created at world root
        Also refreshes the preset combo when a new group is found or created.
        """
        # 1. Already set and still alive
        if self._current_locs_grp and cmds.objExists(self._current_locs_grp):
            return self._current_locs_grp

        # 2. Search the scene by name
        matches = cmds.ls(self._LOCS_GRP_NAME, long=True, type="transform")
        if matches:
            self._current_locs_grp = matches[0]
            self._refresh_preset_combo()
            return self._current_locs_grp

        # 3. Create at world root
        result = cmds.group(empty=True, name=self._LOCS_GRP_NAME, world=True)
        self._current_locs_grp = cmds.ls(result, long=True)[0]
        self._refresh_preset_combo()
        return self._current_locs_grp

    def _refresh_preset_combo(self):
        """Re-populate the preset combo from _current_locs_grp's children."""
        if not self._current_locs_grp or not cmds.objExists(self._current_locs_grp):
            return
        children = cmds.listRelatives(self._current_locs_grp, children=True,
                                      type="transform", fullPath=True) or []
        presets  = [c for c in children
                    if cmds.attributeQuery(self._BSE_ATTR, node=c, exists=True)]
        _sfx = "_splitsLocs_grp"
        self._combo_split_preset.blockSignals(True)
        current_text = self._combo_split_preset.currentText()
        self._combo_split_preset.clear()
        self._combo_split_preset.addItem("— presets —")
        for p in presets:
            short   = p.split("|")[-1]
            display = short[:-len(_sfx)] if short.endswith(_sfx) else short
            self._combo_split_preset.addItem(display)
        # Restore previous selection if still present
        idx = self._combo_split_preset.findText(current_text)
        self._combo_split_preset.setCurrentIndex(idx if idx >= 0 else 0)
        self._combo_split_preset.blockSignals(False)

    def _preset_write(self, node, data):
        """Write preset dict as JSON string on node.bse_split_data."""
        js = json.dumps(data)
        if not cmds.attributeQuery(self._BSE_ATTR, node=node, exists=True):
            cmds.addAttr(node, longName=self._BSE_ATTR, dataType="string")
        cmds.setAttr(f"{node}.{self._BSE_ATTR}", js, type="string")

    def _preset_read(self, node):
        """Return parsed dict from node.bse_split_data, or None."""
        if not cmds.attributeQuery(self._BSE_ATTR, node=node, exists=True):
            return None
        try:
            return json.loads(cmds.getAttr(f"{node}.{self._BSE_ATTR}") or "")
        except Exception:
            return None

    def _on_load_split_preset_grp(self):
        """Find Splits_locs_grp in the scene and populate the preset combo."""
        matches = cmds.ls(self._LOCS_GRP_NAME, long=True, type="transform")
        if not matches:
            self._set_status(
                f"✗ '{self._LOCS_GRP_NAME}' not found in scene. "
                "It will be created automatically on first Save.", error=True)
            return
        self._current_locs_grp = matches[0]
        self._refresh_preset_combo()
        children = cmds.listRelatives(self._current_locs_grp, children=True,
                                      type="transform", fullPath=True) or []
        n = sum(1 for c in children
                if cmds.attributeQuery(self._BSE_ATTR, node=c, exists=True))
        self._set_status(f"✓ {n} preset(s) loaded from '{self._LOCS_GRP_NAME}'")

    def _on_split_preset_activated(self, index):
        """Load the selected preset group's locators and settings into the table."""
        if index == 0 or not self._current_locs_grp:
            return
        preset_name  = self._combo_split_preset.currentText()
        node_name    = f"{preset_name}_splitsLocs_grp"
        children     = cmds.listRelatives(self._current_locs_grp, children=True,
                                          type="transform", fullPath=True) or []
        preset_node  = next(
            (c for c in children if c.split("|")[-1] == node_name), None)
        if not preset_node:
            self._set_status(f"✗ Preset group '{node_name}' not found.", error=True)
            return
        # Hide all sibling preset groups, show only the selected one
        for child in children:
            if cmds.attributeQuery(self._BSE_ATTR, node=child, exists=True):
                cmds.setAttr(f"{child}.visibility", child == preset_node)
        data = self._preset_read(preset_node)
        if not data:
            self._set_status(f"✗ No data on preset '{preset_name}'.", error=True)
            return
        # Locators are children of preset_node
        loc_children = cmds.listRelatives(
            preset_node, children=True, type="transform", fullPath=True) or []
        loc_children = [l for l in loc_children
                        if cmds.listRelatives(l, shapes=True, type="locator")]
        stored = {ld["name"]: ld for ld in data.get("locs", [])}
        data["locators"] = [
            {"name": l.split("|")[-1], "long": l,
             "side":   stored.get(l.split("|")[-1], {}).get("side", ""),
             "suffix": stored.get(l.split("|")[-1], {}).get("suffix", "")}
            for l in loc_children
        ]
        self._apply_split_preset(data)
        self._set_status(f"✓ Preset loaded: '{preset_name}'")

    def _propose_locator_renames(self, preset_name):
        """
        Show a dialog proposing to rename locators based on preset_name + table side/suffix.
        Proposed format: {side}_{preset_name}_loc_{suffix}  (parts omitted if empty).
        The user can edit proposed names and check/uncheck individual rows.
        Accepted renames are applied in Maya and the table is updated.
        """
        # Build proposals from table
        rows = []
        for row in range(self.table.rowCount()):
            item     = self.table.item(row, 0)
            side_itm = self.table.item(row, 1)
            sfx_itm  = self.table.item(row, 2)
            if not item:
                continue
            long_name  = item.data(QtCore.Qt.UserRole) or ""
            short_name = item.text()
            if not long_name or not cmds.objExists(long_name):
                continue
            side   = (side_itm.text().strip()  if side_itm  else "").upper()
            suffix = sfx_itm.text().strip()    if sfx_itm   else ""
            # Build proposed name
            parts = []
            if side:
                parts.append(side)
            parts.append(preset_name)
            parts.append("loc")
            if suffix:
                parts.append(suffix)
            proposed = "_".join(parts)
            rows.append((row, long_name, short_name, proposed))

        if not rows:
            return

        # Dialog
        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle("Rename Locators")
        dlg.setMinimumWidth(420)
        lay = QtWidgets.QVBoxLayout(dlg)
        lay.setSpacing(6)

        lbl = QtWidgets.QLabel(
            f"Proposed renames for preset <b>{preset_name}</b>.<br>"
            "Uncheck rows to skip. Proposed names are editable.")
        lbl.setWordWrap(True)
        lay.addWidget(lbl)

        tbl = QtWidgets.QTableWidget(len(rows), 2)
        tbl.setHorizontalHeaderLabels(["Current", "Proposed"])
        tbl.horizontalHeader().setStretchLastSection(True)
        tbl.verticalHeader().setVisible(False)
        tbl.setSelectionMode(QtWidgets.QAbstractItemView.NoSelection)
        for i, (_, _, short_name, proposed) in enumerate(rows):
            cur_item = QtWidgets.QTableWidgetItem(short_name)
            cur_item.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsUserCheckable)
            cur_item.setCheckState(QtCore.Qt.Checked)
            tbl.setItem(i, 0, cur_item)
            tbl.setItem(i, 1, QtWidgets.QTableWidgetItem(proposed))
        tbl.resizeColumnToContents(0)
        lay.addWidget(tbl)

        btn_row = QtWidgets.QHBoxLayout()
        btn_ok   = QtWidgets.QPushButton("Rename Checked")
        btn_skip = QtWidgets.QPushButton("Skip")
        btn_row.addWidget(btn_ok)
        btn_row.addWidget(btn_skip)
        lay.addLayout(btn_row)

        btn_ok.clicked.connect(dlg.accept)
        btn_skip.clicked.connect(dlg.reject)

        if dlg.exec_() != QtWidgets.QDialog.Accepted:
            return

        # Apply checked renames
        for i, (row, long_name, short_name, _) in enumerate(rows):
            cur_item = tbl.item(i, 0)
            if cur_item.checkState() != QtCore.Qt.Checked:
                continue
            new_name = tbl.item(i, 1).text().strip()
            if not new_name or new_name == short_name:
                continue
            try:
                result = cmds.rename(long_name, new_name)
                new_long = cmds.ls(result, long=True)[0]
                # Update table
                self.table.blockSignals(True)
                name_item = self.table.item(row, 0)
                if name_item:
                    name_item.setText(result)
                    name_item.setData(QtCore.Qt.UserRole, new_long)
                self.table.blockSignals(False)
            except Exception as e:
                cmds.warning(f"Could not rename '{short_name}' -> '{new_name}': {e}")

    def _on_save_split_preset(self):
        """Save current table state into the selected (or new) preset sub-group."""
        locs_grp = self._ensure_locs_grp()
        idx = self._combo_split_preset.currentIndex()
        if idx == 0:
            name, ok = QtWidgets.QInputDialog.getText(
                self, "New Split Preset", "Preset name:")
            if not ok or not name.strip():
                return
            name = name.strip()
            # Check if a preset with this name already exists
            if self._combo_split_preset.findText(name) >= 0:
                answer = QtWidgets.QMessageBox.question(
                    self, "Overwrite Preset",
                    f"A preset named '{name}' already exists.\nOverwrite it?",
                    QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                    QtWidgets.QMessageBox.No)
                if answer != QtWidgets.QMessageBox.Yes:
                    return
        else:
            name = self._combo_split_preset.currentText()

        # Propose locator renames based on preset name + table side/suffix
        self._propose_locator_renames(name)

        # Find or create the preset sub-group  ({name}_splitsLocs_grp)
        node_name   = f"{name}_splitsLocs_grp"
        children    = cmds.listRelatives(locs_grp, children=True,
                                         type="transform", fullPath=True) or []
        preset_node = next(
            (c for c in children if c.split("|")[-1] == node_name), None)
        if preset_node is None:
            result      = cmds.group(empty=True, name=node_name, parent=locs_grp)
            preset_node = cmds.ls(result, long=True)[0]

        # Collect locators from table + parent them to preset_node
        locs_data = []
        for row in range(self.table.rowCount()):
            item     = self.table.item(row, 0)
            side_itm = self.table.item(row, 1)
            sfx_itm  = self.table.item(row, 2)
            if not item:
                continue
            long_name  = item.data(QtCore.Qt.UserRole) or ""
            short_name = item.text()
            if not long_name or not cmds.objExists(long_name):
                continue
            # Re-parent to preset_node if needed
            current_parent = (cmds.listRelatives(long_name, parent=True,
                                                 fullPath=True) or [None])[0]
            if current_parent != preset_node:
                cmds.parent(long_name, preset_node)
            # Update stored long name after potential re-parent
            new_long = cmds.ls(short_name, long=True)
            if new_long:
                self.table.blockSignals(True)
                item.setData(QtCore.Qt.UserRole, new_long[0])
                self.table.blockSignals(False)
            locs_data.append({
                "name":   short_name,
                "side":   side_itm.text() if side_itm else "",
                "suffix": sfx_itm.text()  if sfx_itm  else "",
            })

        # Build and write settings dict
        state = self._capture_split_state()
        payload = {k: v for k, v in state.items() if k != "locators"}
        payload["locs"] = locs_data
        self._preset_write(preset_node, payload)

        # Refresh combo
        self._combo_split_preset.blockSignals(True)
        if self._combo_split_preset.findText(name) < 0:
            self._combo_split_preset.addItem(name)
        self._combo_split_preset.setCurrentIndex(
            self._combo_split_preset.findText(name))
        self._combo_split_preset.blockSignals(False)
        self._set_status(f"✓ Split preset saved: '{name}'")

    def _on_load_preset_context_menu(self, btn, pos):
        menu = QtWidgets.QMenu(self)
        act = menu.addAction("Browse from JSON file…")
        act.setToolTip("Import split presets from a JSON file previously exported.\n"
                       "Recreates Splits_locs_grp, preset sub-groups and locators.")
        act.triggered.connect(self._on_import_splits_from_json)
        menu.exec_(btn.mapToGlobal(pos))

    def _on_save_preset_context_menu(self, btn, pos):
        menu = QtWidgets.QMenu(self)
        act = menu.addAction("Save to JSON file…")
        act.setToolTip("Export all split presets and locator positions to a JSON file\n"
                       "so they can be recreated in another scene.")
        act.triggered.connect(self._on_export_splits_to_json)
        menu.exec_(btn.mapToGlobal(pos))

    def _on_delete_split_preset(self):
        """Remove the selected preset from the combo (clears bse_split_data, keeps group)."""
        idx = self._combo_split_preset.currentIndex()
        if idx == 0 or not self._current_locs_grp:
            return
        name      = self._combo_split_preset.currentText()
        node_name = f"{name}_splitsLocs_grp"
        # Find the node and remove the attribute (marks it as non-preset)
        children    = cmds.listRelatives(self._current_locs_grp, children=True,
                                         type="transform", fullPath=True) or []
        preset_node = next(
            (c for c in children if c.split("|")[-1] == node_name), None)
        if preset_node and cmds.attributeQuery(self._BSE_ATTR, node=preset_node,
                                               exists=True):
            cmds.deleteAttr(f"{preset_node}.{self._BSE_ATTR}")
        self._combo_split_preset.blockSignals(True)
        self._combo_split_preset.removeItem(idx)
        self._combo_split_preset.setCurrentIndex(0)
        self._combo_split_preset.blockSignals(False)
        self._set_status(f"Preset '{name}' removed (group kept in scene)")

    # ── Export / Import presets to/from JSON file ─────────────────────────────

    def _on_export_splits_to_json(self):
        """Export all presets in Splits_locs_grp to a JSON file with locator transforms."""
        locs_grp = None
        if self._current_locs_grp and cmds.objExists(self._current_locs_grp):
            locs_grp = self._current_locs_grp
        else:
            matches = cmds.ls(self._LOCS_GRP_NAME, long=True, type="transform")
            if matches:
                locs_grp = matches[0]
        if not locs_grp:
            QtWidgets.QMessageBox.warning(
                self, "Export Split Presets",
                f"'{self._LOCS_GRP_NAME}' not found in scene.\n"
                "Create and save at least one preset first.")
            return

        children = cmds.listRelatives(
            locs_grp, children=True, type="transform", fullPath=True) or []
        preset_nodes = [c for c in children
                        if cmds.attributeQuery(self._BSE_ATTR, node=c, exists=True)]
        if not preset_nodes:
            QtWidgets.QMessageBox.warning(
                self, "Export Split Presets", "No presets found in the scene.")
            return

        _sfx = "_splitsLocs_grp"
        presets_out = []
        for preset_node in preset_nodes:
            short = preset_node.split("|")[-1]
            name  = short[:-len(_sfx)] if short.endswith(_sfx) else short
            settings = self._preset_read(preset_node) or {}

            loc_children = cmds.listRelatives(
                preset_node, children=True, type="transform", fullPath=True) or []
            loc_children = [l for l in loc_children
                            if cmds.listRelatives(l, shapes=True, type="locator")]
            locs_out = []
            for loc in loc_children:
                t = cmds.getAttr(f"{loc}.translate")[0]
                r = cmds.getAttr(f"{loc}.rotate")[0]
                s = cmds.getAttr(f"{loc}.scale")[0]
                locs_out.append({
                    "name": loc.split("|")[-1],
                    "t": [round(v, 6) for v in t],
                    "r": [round(v, 6) for v in r],
                    "s": [round(v, 6) for v in s],
                })

            presets_out.append({
                "name":      name,
                "settings":  settings,
                "locators":  locs_out,
            })

        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Export Split Presets", "", "JSON files (*.json)")
        if not path:
            return
        if not path.endswith(".json"):
            path += ".json"

        try:
            with open(path, "w") as fh:
                json.dump({"bse_version": 1, "presets": presets_out}, fh, indent=2)
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "Export Error", str(e))
            return

        self._set_status(
            f"✓ {len(presets_out)} preset(s) exported to {os.path.basename(path)}")

    def _on_import_splits_from_json(self):
        """Import split presets from a JSON file, recreating groups and locators."""
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Import Split Presets", "", "JSON files (*.json)")
        if not path or not os.path.exists(path):
            return

        try:
            with open(path, "r") as fh:
                data = json.load(fh)
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "Import Error", str(e))
            return

        if not isinstance(data, dict) or "presets" not in data:
            QtWidgets.QMessageBox.warning(
                self, "Import Error", "Invalid file: missing 'presets' key.")
            return

        locs_grp  = self._ensure_locs_grp()
        n_created = 0

        for preset in data["presets"]:
            name = preset.get("name", "").strip()
            if not name:
                continue

            node_name = f"{name}_splitsLocs_grp"
            children  = cmds.listRelatives(
                locs_grp, children=True, type="transform", fullPath=True) or []
            preset_node = next(
                (c for c in children if c.split("|")[-1] == node_name), None)
            if preset_node is None:
                result      = cmds.group(empty=True, name=node_name, parent=locs_grp)
                preset_node = cmds.ls(result, long=True)[0]

            # Recreate / reposition locators
            for ld in preset.get("locators", []):
                loc_name = ld.get("name", "").strip()
                if not loc_name:
                    continue
                existing = cmds.ls(loc_name, long=True)
                if existing:
                    loc_long = existing[0]
                    cur_parent = (cmds.listRelatives(
                        loc_long, parent=True, fullPath=True) or [None])[0]
                    if cur_parent != preset_node:
                        cmds.parent(loc_long, preset_node)
                        loc_long = cmds.ls(loc_name, long=True)[0]
                else:
                    new_loc  = cmds.spaceLocator(name=loc_name)[0]
                    cmds.parent(new_loc, preset_node)
                    loc_long = cmds.ls(loc_name, long=True)[0]

                if "t" in ld:
                    cmds.setAttr(f"{loc_long}.translate", *ld["t"], type="double3")
                if "r" in ld:
                    cmds.setAttr(f"{loc_long}.rotate", *ld["r"], type="double3")
                if "s" in ld:
                    cmds.setAttr(f"{loc_long}.scale", *ld["s"], type="double3")

            settings = preset.get("settings", {})
            if settings:
                self._preset_write(preset_node, settings)

            n_created += 1

        self._refresh_preset_combo()
        self._set_status(
            f"✓ {n_created} preset(s) imported from {os.path.basename(path)}")

    def _capture_split_state(self):
        """Snapshot the current locator table and split settings into a dict."""
        locs = []
        for row in range(self.table.rowCount()):
            item     = self.table.item(row, 0)
            side_itm = self.table.item(row, 1)
            sfx_itm  = self.table.item(row, 2)
            if item:
                locs.append({
                    "name":   item.text(),
                    "long":   item.data(QtCore.Qt.UserRole) or "",
                    "side":   side_itm.text() if side_itm else "",
                    "suffix": sfx_itm.text()  if sfx_itm  else "",
                })
        return {
            "locators":  locs,
            "axis_x":    self.chk_x.isChecked(),
            "axis_y":    self.chk_y.isChecked(),
            "axis_z":    self.chk_z.isChecked(),
            "invert":    self.chk_invert_axis.isChecked(),
            "local":     self.chk_local_axes.isChecked(),
            "symmetric": self.chk_symmetric.isChecked(),
            "radial":    self.chk_radial.isChecked(),
            "radius_on": self.chk_radius.isChecked(),
            "radius":    self.spin_radius.text(),
            "curve":     self.combo_curve.currentText(),
        }

    def _apply_split_preset(self, data):
        """Restore locator table and split settings from a preset dict."""
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        for ld in data.get("locators", []):
            long_name  = ld.get("long", "")
            short_name = ld.get("name", "")
            if long_name and cmds.objExists(long_name):
                resolved_long = long_name
            elif short_name and cmds.objExists(short_name):
                resolved_long = cmds.ls(short_name, long=True)[0]
            else:
                cmds.warning(f"Split preset: locator '{short_name}' not found — skipped")
                continue
            resolved_short = resolved_long.split("|")[-1]
            r = self.table.rowCount()
            self.table.insertRow(r)
            item_loc = QtWidgets.QTableWidgetItem(resolved_short)
            item_loc.setFlags(item_loc.flags() | QtCore.Qt.ItemIsEditable)
            item_loc.setData(QtCore.Qt.UserRole, resolved_long)
            self.table.setItem(r, 0, item_loc)
            self.table.setItem(r, 1, QtWidgets.QTableWidgetItem(ld.get("side", "")))
            self.table.setItem(r, 2, QtWidgets.QTableWidgetItem(ld.get("suffix", "")))
        self.table.blockSignals(False)

        for chk, key in [
            (self.chk_x,           "axis_x"),
            (self.chk_y,           "axis_y"),
            (self.chk_z,           "axis_z"),
            (self.chk_invert_axis, "invert"),
            (self.chk_local_axes,  "local"),
            (self.chk_symmetric,   "symmetric"),
            (self.chk_radial,      "radial"),
            (self.chk_radius,      "radius_on"),
        ]:
            if key in data:
                chk.blockSignals(True)
                chk.setChecked(data[key])
                chk.blockSignals(False)

        if "radius" in data:
            self.spin_radius.setText(data["radius"])
        if "curve" in data:
            idx = self.combo_curve.findText(data["curve"])
            if idx >= 0:
                self.combo_curve.setCurrentIndex(idx)

        self._on_radius_enabled(self.chk_radius.isChecked())
        self._update_single_loc_state()
        self._resize_table_to_content()

    def _on_radius_enabled(self, state):
        """Enable/disable radius slider+spin when checkbox is toggled."""
        enabled = bool(state)
        self.slider_radius.setEnabled(enabled)
        self.spin_radius.setEnabled(enabled)

    def _update_single_loc_state(self):
        """Auto-enable radius checkbox when there is exactly 1 locator."""
        single = (self.table.rowCount() == 1)
        if single and not self.chk_radius.isChecked():
            self.chk_radius.setChecked(True)
        elif not single and self.table.rowCount() > 1 and self.chk_radius.isChecked():
            # Only auto-disable if it was auto-enabled (rowCount just went above 1)
            pass  # user may have enabled it manually — don't force-disable
        # Refresh symmetric suffixes if active
        if self.chk_symmetric.isChecked():
            self._apply_symmetric_suffixes()

    def _apply_symmetric_suffixes(self):
        """
        Auto-fills the suffix column based on symmetric L/R logic.

        n=1 : (no suffix — handled as classic 1-loc symmetric)
        n=2 : _R  _L
        n=3 : _R  _M  _L
        n=4 : _R_b  _R_a  _L_a  _L_b
        n=5 : _R_b  _R_a  _M  _L_a  _L_b
        n=6+: letters continue (a, b, c...)

        Suffixes are read-only while symmetric is active.
        """
        n = self.table.rowCount()
        if n == 0:
            return

        letters = "abcdefghijklmnopqrstuvwxyz"
        suffixes = []

        # suffixes store only the letter part — prefix (R_/L_/C_) is handled in _run_split
        # n=1 : [""]          ->R_name   L_name
        # n=2 : ["", ""]      ->R_name   L_name
        # n=3 : ["", "", ""]  ->R_name   C_name  L_name
        # n=4 : ["_b","_a","_a","_b"] ->R_name_b  R_name_a  L_name_a  L_name_b
        # n=5 : ["_b","_a","","_a","_b"] ->R_name_b  R_name_a  C_name  L_name_a  L_name_b

        if n == 1:
            suffixes = [""]
        elif n == 2:
            suffixes = ["", ""]
        elif n == 3:
            suffixes = ["", "", ""]
        elif n % 2 == 1:
            # Odd >= 5
            half = n // 2
            for i in range(half, 0, -1):
                suffixes.append(letters[i-1])
            suffixes.append("")   # middle — no letter suffix
            for i in range(1, half + 1):
                suffixes.append(letters[i-1])
        else:
            # Even >= 4
            half = n // 2
            for i in range(half, 0, -1):
                suffixes.append(letters[i-1])
            for i in range(1, half + 1):
                suffixes.append(letters[i-1])

        # Sides per row
        _SL = self._nom_side_left
        _SC = self._nom_side_center
        _SR = self._nom_side_right
        if n == 1:
            sides = [""]
        elif n == 2:
            sides = [_SR, _SL]
        elif n == 3:
            sides = [_SR, _SC, _SL]
        elif n % 2 == 1:
            half = n // 2
            sides = ([_SR] * half) + [_SC] + ([_SL] * half)
        else:
            half = n // 2
            sides = ([_SR] * half) + ([_SL] * half)

        for row in range(n):
            # Side column (col 1)
            side_item = self.table.item(row, 1)
            if side_item is None:
                side_item = QtWidgets.QTableWidgetItem("")
                self.table.setItem(row, 1, side_item)
            side_item.setText(sides[row] if row < len(sides) else "")
            side_item.setFlags(side_item.flags() & ~QtCore.Qt.ItemIsEditable)

            # Suffix column (col 2)
            sfx_item = self.table.item(row, 2)
            if sfx_item is None:
                sfx_item = QtWidgets.QTableWidgetItem("")
                self.table.setItem(row, 2, sfx_item)
            sfx_item.setText(suffixes[row] if row < len(suffixes) else "")
            sfx_item.setFlags(sfx_item.flags() & ~QtCore.Qt.ItemIsEditable)

    def _restore_suffix_editable(self):
        """Restores editable Side + Suffix columns when symmetric mode is turned off."""
        n        = self.table.rowCount()
        suffixes = self._auto_suffixes(n)
        for row in range(n):
            # Clear Side
            side_item = self.table.item(row, 1)
            if side_item is None:
                side_item = QtWidgets.QTableWidgetItem("")
                self.table.setItem(row, 1, side_item)
            side_item.setText("")
            side_item.setFlags(side_item.flags() | QtCore.Qt.ItemIsEditable)

            # Restore default Suffix
            sfx_item = self.table.item(row, 2)
            if sfx_item is None:
                sfx_item = QtWidgets.QTableWidgetItem("")
                self.table.setItem(row, 2, sfx_item)
            sfx_item.setText(suffixes[row] if row < len(suffixes) else "")
            sfx_item.setFlags(sfx_item.flags() | QtCore.Qt.ItemIsEditable)

    def _on_symmetric_changed(self, state):
        if state:
            self._apply_symmetric_suffixes()
        else:
            self._restore_suffix_editable()

    def _on_table_header_context_menu(self, pos):
        """Right-click on the locator table header — show suffix template menu on col 2."""
        col = self.table.horizontalHeader().logicalIndexAt(pos)
        if col != 2:
            return
        menu = QtWidgets.QMenu(self)
        act_abc = menu.addAction("a, b, c")
        act_abc.setCheckable(True)
        act_abc.setChecked(self._suffix_template == "abc")
        act_pos = menu.addAction("in / mid / out")
        act_pos.setCheckable(True)
        act_pos.setChecked(self._suffix_template == "positional")
        act_abc.triggered.connect(lambda: self._set_suffix_template("abc"))
        act_pos.triggered.connect(lambda: self._set_suffix_template("positional"))
        menu.exec_(self.table.horizontalHeader().mapToGlobal(pos))

    def _set_suffix_template(self, template):
        """Apply a suffix template and refresh suffix cells."""
        self._suffix_template = template
        if self.chk_symmetric.isChecked():
            return  # symmetric mode manages suffixes itself
        n = self.table.rowCount()
        suffixes = self._auto_suffixes(n)
        for row in range(n):
            sfx_item = self.table.item(row, 2)
            if sfx_item is None:
                sfx_item = QtWidgets.QTableWidgetItem("")
                self.table.setItem(row, 2, sfx_item)
            sfx_item.setText(suffixes[row] if row < len(suffixes) else "")

    def _select_locs_in_maya(self):
        """Select the Maya locators that correspond to the currently selected table rows."""
        rows = sorted({idx.row() for idx in self.table.selectedIndexes()})
        locs = []
        for r in rows:
            item = self.table.item(r, 0)
            if item:
                ln = item.data(QtCore.Qt.UserRole)
                if ln and cmds.objExists(ln):
                    locs.append(ln)
        if locs:
            cmds.select(locs, replace=True)

    def _on_table_context_menu(self, pos):
        """Right-click context menu on the locator table."""
        # Right-click doesn't change Qt selection, so select the clicked row if
        # it isn't already part of the current selection.
        clicked_row = self.table.rowAt(pos.y())
        if clicked_row >= 0:
            selected_rows = {idx.row() for idx in self.table.selectedIndexes()}
            if clicked_row not in selected_rows:
                self.table.selectRow(clicked_row)

        menu = QtWidgets.QMenu(self)
        rows = sorted({idx.row() for idx in self.table.selectedIndexes()})
        if rows:
            act_sel = menu.addAction("Select in Maya")
            act_sel.triggered.connect(self._select_locs_in_maya)

        if not menu.isEmpty():
            menu.exec_(self.table.viewport().mapToGlobal(pos))

    def _on_locator_name_edited(self, item):
        """Inline rename: called when col 0 cell text is committed by the user."""
        if self.table.column(item) != 0:
            return
        long_name = item.data(QtCore.Qt.UserRole)
        if not long_name:
            return
        new_name = item.text().strip()
        old_short = long_name.split("|")[-1]
        if not new_name:
            self.table.blockSignals(True)
            item.setText(old_short)
            self.table.blockSignals(False)
            return
        if new_name == old_short:
            return
        if not cmds.objExists(long_name):
            self.table.blockSignals(True)
            item.setText(old_short)
            self.table.blockSignals(False)
            cmds.warning("Locator no longer exists in scene.")
            return
        try:
            result    = cmds.rename(long_name, new_name)
            new_long  = cmds.ls(result, long=True)[0]
            new_short = new_long.split("|")[-1]
            self.table.blockSignals(True)
            item.setText(new_short)
            item.setData(QtCore.Qt.UserRole, new_long)
            self.table.blockSignals(False)
            self._set_status(f"Renamed: {old_short} ->{new_short}")
        except Exception as e:
            self.table.blockSignals(True)
            item.setText(old_short)
            self.table.blockSignals(False)
            cmds.warning(f"Rename failed: {e}")

    def _on_axis_exclusive(self, toggled_chk, state):
        """
        Radial OFF: radio mode — exactly one axis active at a time.
          - Checking a box unchecks the other two.
          - Trying to uncheck the last active box: snaps it back ON.
        Radial ON: no constraint, all combinations valid.
        """
        if self.chk_radial.isChecked():
            return  # free multi-selection in radial mode

        if not state:
            # Prevent unchecking the last active box
            others = [c for c in (self.chk_x, self.chk_y, self.chk_z) if c is not toggled_chk]
            if not any(c.isChecked() for c in others):
                toggled_chk.blockSignals(True)
                toggled_chk.setChecked(True)
                toggled_chk.blockSignals(False)
        else:
            # Uncheck the other two
            for chk in (self.chk_x, self.chk_y, self.chk_z):
                if chk is not toggled_chk:
                    chk.blockSignals(True)
                    chk.setChecked(False)
                    chk.blockSignals(False)

    def _on_radius_slider(self, value):
        # Slider range 1-150 maps to value 0.1-15.0
        self.spin_radius.blockSignals(True)
        self.spin_radius.setText(f"{value / 10.0:.2f}")
        self.spin_radius.blockSignals(False)

    def _on_radius_spin(self):
        try:
            value = float(self.spin_radius.text())
        except ValueError:
            return
        self.spin_radius.blockSignals(True)
        self.spin_radius.setText(f"{value:.2f}")
        self.spin_radius.blockSignals(False)
        self.slider_radius.blockSignals(True)
        self.slider_radius.setValue(int(value * 10))
        self.slider_radius.blockSignals(False)

    def _on_opacity_spin_edited(self):
        try:
            value = max(0.01, min(1.0, float(self.spin_smooth_opacity.text())))
        except ValueError:
            return
        self.slider_smooth_opacity.blockSignals(True)
        self.slider_smooth_opacity.setValue(int(round(value * 100)))
        self.slider_smooth_opacity.blockSignals(False)

    def _get_axes(self):
        """Returns (use_x, use_z, use_y)."""
        return (self.chk_x.isChecked(), self.chk_z.isChecked(), self.chk_y.isChecked())

    # ── Table slots ───────────────────────────────────────────────────────────

    def _reset_default_options(self):
        """
        Resets all options to their default values (state at first open).
        User-defined naming convention pairs (JSON) are preserved.
        Signals are blocked during reset to avoid cascading side-effects.
        """
        # ── Axes ──────────────────────────────────────────────────────────────
        for chk in (self.chk_x, self.chk_y, self.chk_z,
                    self.chk_invert_axis, self.chk_local_axes, self.chk_symmetric):
            chk.blockSignals(True)
        self.chk_x.setChecked(True)
        self.chk_y.setChecked(False)
        self.chk_z.setChecked(False)
        self.chk_invert_axis.setChecked(False)
        self.chk_local_axes.setChecked(True)
        self.chk_symmetric.setChecked(False)
        for chk in (self.chk_x, self.chk_y, self.chk_z,
                    self.chk_invert_axis, self.chk_local_axes, self.chk_symmetric):
            chk.blockSignals(False)

        # ── Falloff ───────────────────────────────────────────────────────────
        self.chk_radial.blockSignals(True)
        self.chk_radial.setChecked(False)
        self.chk_radial.blockSignals(False)

        self.chk_radius.blockSignals(True)
        self.chk_radius.setChecked(False)
        self.chk_radius.blockSignals(False)
        self._on_radius_enabled(False)

        self.combo_curve.blockSignals(True)
        self.combo_curve.setCurrentIndex(0)  # Linear
        self.combo_curve.blockSignals(False)

        self.slider_radius.blockSignals(True)
        self.slider_radius.setValue(10)
        self.slider_radius.blockSignals(False)

        self.spin_radius.blockSignals(True)
        self.spin_radius.setText("1.00")
        self.spin_radius.blockSignals(False)

        # ── Locators table ────────────────────────────────────────────────────
        self.table.setRowCount(0)

        # ── Secondary Meshes ──────────────────────────────────────────────────
        self.chk_connect_targets.setChecked(True)

        # ── Create Opposite ───────────────────────────────────────────────────
        self._opp_axis = "Object X"
        self._opp_topo_edge = None
        self.btn_create_opposite.setToolTip(
            "Create Opposite Target\n"
            "Duplicates the target, flips it and renames it with the opposite\n"
            "naming convention (L_/R_, lft/rgt, up/dn, fwd/bwd …).\n"
            "Right-click to choose the symmetry axis.\n"
            f"Current axis: {self._opp_axis}")

        # ── Modify Deltas ─────────────────────────────────────────────────────
        self.chk_delta_neutral.setChecked(True)
        self.chk_delta_multi.setChecked(False)
        self.slider_smooth_opacity.blockSignals(True)
        self.slider_smooth_opacity.setValue(50)
        self.slider_smooth_opacity.blockSignals(False)
        self.spin_smooth_opacity.blockSignals(True)
        self.spin_smooth_opacity.setText("0.50")
        self.spin_smooth_opacity.blockSignals(False)

        self.spin_prune_tol.setText("0.001")

        for lbl, fld in zip(self._mult_labels, self._mult_fields):
            lbl.setChecked(False)
            fld.setText("1.2")

        # ── Nomenclature — Rename Targets ─────────────────────────────────────
        self.edit_rename_pfx.clear()
        self.edit_rename_sfx.clear()
        self.edit_search.clear()
        self.edit_replace.clear()

        # ── Nomenclature — Tool's Auto-naming (pairs JSON preserved) ──────────
        self._nom_token_order = ["{side}", "{target}", "{suffix}"]
        self._nom_prefix = ""
        self._nom_side_left   = "L"
        self._nom_side_center = "C"
        self._nom_side_right  = "R"

    def _run_delta_view(self):
        """
        Colorizes base meshes by summed delta magnitude across all selected targets.

        Multi-target / multi-mesh logic:
          - Targets are grouped by bs_node (= base mesh).
          - For each mesh, magnitudes are SUMMED across all its selected targets
            vertex by vertex — a vertex active in several targets accumulates.
          - max_mag is computed globally across ALL meshes, so different meshes
            share the same color scale (useful for comparing two bs_nodes).
          - Each mesh is colorized independently with that global scale.

        Gradient: black(0) ->red(0.5) ->yellow(1)
        """
        targets = self._get_targets_or_warn()
        if not targets:
            return

        def _heatmap_rgb(t):
            """
            HLS hue-sweep colormap via colorsys.hls_to_rgb.
            Interpolating in HLS space (L=0.5, S=1) keeps luminosity constant
            across all transitions — no perceptual dead zones, no white artefacts.

            Hue stops match exact target colors:
              t=0.000–0.001 ->noir pur  (0,    0,    0  )
              t=0.001       ->bleu      hue=0.667
              t=0.125       ->cyan      hue=0.500
              t=0.250       ->vert      hue=0.333
              t=0.500       ->jaune     hue=0.167
              t=0.625       ->jaune-org hue=0.100
              t=0.750       ->orange    hue=0.050
              t=0.999       ->rouge     hue=0.000
              t=0.999–1.000 ->blanc pur (1,    1,    1  )
            """
            import colorsys
            if t < 0.001:
                return (0.0, 0.0, 0.0)
            if t > 0.999:
                return (1.0, 1.0, 1.0)
            stops_hue = [
                (0.001, 0.667),   # blue
                (0.080, 0.500),   # leaving cyan  — narrow peak around hue=0.5
                (0.100, 0.333),   # green
                (0.500, 0.167),   # yellow
                (0.625, 0.100),   # yellow-orange
                (0.9500, 0.050),   # orange — wider range
                (0.999, 0.000),   # red
            ]
            for i in range(len(stops_hue) - 1):
                t0, h0 = stops_hue[i]
                t1, h1 = stops_hue[i + 1]
                if t0 <= t <= t1:
                    s   = (t - t0) / (t1 - t0)
                    hue = h0 + s * (h1 - h0)
                    return colorsys.hls_to_rgb(hue, 0.5, 1.0)
            return (1.0, 0.0, 0.0)

        # ── Step 1: group targets by bs_node, accumulate magnitudes per mesh ──
        # mesh_data = { base_mesh: { vi: summed_magnitude } }
        mesh_data = {}
        for bs_node, logical_index, target_name in targets:
            base_mesh = get_base_mesh(bs_node)
            if not base_mesh:
                cmds.warning(f"Could not find base mesh for '{bs_node}', skipping.")
                continue
            deltas = get_target_deltas(bs_node, logical_index)
            if not deltas:
                cmds.warning(f"No deltas on '{target_name}', skipping.")
                continue
            if base_mesh not in mesh_data:
                mesh_data[base_mesh] = {}
            for vi, (dx, dy, dz) in deltas.items():
                mag = abs(dx) + abs(dy) + abs(dz)
                mesh_data[base_mesh][vi] = mesh_data[base_mesh].get(vi, 0.0) + mag

        if not mesh_data:
            cmds.warning("No valid deltas found across selected targets.")
            return

        # ── Step 2: global max across all meshes ──────────────────────────────
        max_mag = max(
            mag
            for magnitudes in mesh_data.values()
            for mag in magnitudes.values()
        )
        if max_mag < 1e-7:
            max_mag = 1.0

        # ── Step 3: Laplacian diffusion — propagate magnitudes to neighbours ──
        # Builds vertex adjacency from edges, then runs N passes of weighted
        # averaging. Each vertex receives a blend of its own value and its
        # neighbours' average, creating the smooth halo Maya shows on skin weights.
        # Original peaks are preserved (values never decrease below raw value).
        from maya.api import OpenMaya as om
        DIFFUSE_PASSES  = 3     # number of diffusion iterations
        DIFFUSE_WEIGHT  = 0.5   # neighbour contribution per pass (0=none, 1=full)

        def _build_adjacency(fn_mesh, n_verts):
            """Returns dict {vi: [neighbour_vi, ...]} from edge connectivity."""
            adj = {i: [] for i in range(n_verts)}
            edge_iter = om.MItMeshEdge(fn_mesh.object())
            while not edge_iter.isDone():
                a = edge_iter.vertexId(0)
                b = edge_iter.vertexId(1)
                adj[a].append(b)
                adj[b].append(a)
                edge_iter.next()
            return adj

        def _diffuse(magnitudes, adj, n_verts, passes, weight):
            """
            Laplacian diffusion: each pass spreads values to neighbours.
            Values can only increase — peaks are never diluted.
            """
            vals = [magnitudes.get(i, 0.0) for i in range(n_verts)]
            for _ in range(passes):
                new_vals = vals[:]
                for vi, neighbours in adj.items():
                    if not neighbours:
                        continue
                    nb_avg = sum(vals[nb] for nb in neighbours) / len(neighbours)
                    blended = vals[vi] * (1.0 - weight) + nb_avg * weight
                    new_vals[vi] = max(vals[vi], blended)  # never reduce peaks
                vals = new_vals
            return vals

        # ── Step 4: colorize each mesh with the global scale ──────────────────
        self._dv_meshes = []  # list of (mesh, prev_display_state)

        for base_mesh, magnitudes in mesh_data.items():
            n_verts   = cmds.polyEvaluate(base_mesh, vertex=True)
            prev_disp = cmds.getAttr(f"{base_mesh}.displayColors")
            self._dv_meshes.append((base_mesh, prev_disp))

            sel     = om.MSelectionList()
            sel.add(base_mesh)
            dag     = sel.getDagPath(0)
            fn_mesh = om.MFnMesh(dag)

            # Build adjacency and diffuse
            adj        = _build_adjacency(fn_mesh, n_verts)
            diffused   = _diffuse(magnitudes, adj, n_verts, DIFFUSE_PASSES, DIFFUSE_WEIGHT)

            colors     = om.MColorArray()
            vertex_ids = om.MIntArray()
            for vi in range(n_verts):
                t       = diffused[vi] / max_mag
                t       = min(t, 1.0)   # diffusion can exceed original max
                r, g, b = _heatmap_rgb(t)
                colors.append(om.MColor([r, g, b, 1.0]))
                vertex_ids.append(vi)

            fn_mesh.setVertexColors(colors, vertex_ids)
            fn_mesh.updateSurface()
            cmds.setAttr(f"{base_mesh}.displayColors", 1)

        n_targets = len(targets)
        n_meshes  = len(mesh_data)
        self.btn_delta_view.setEnabled(False)
        self.btn_exit_delta_view.setEnabled(True)
        self._set_status(
            f"Delta View : {n_targets} target{'s' if n_targets > 1 else ''} "
            f"on {n_meshes} mesh{'es' if n_meshes > 1 else ''}  —  "
            f"max mag = {max_mag:.4f}"
        )

    def _exit_delta_view(self):
        """
        Removes delta colorization from all colorized meshes and restores
        their original vertex color display state.
        """
        if not hasattr(self, "_dv_meshes") or not self._dv_meshes:
            return
        for base_mesh, prev_disp in self._dv_meshes:
            try:
                if cmds.objExists(base_mesh):
                    pvc_nodes = cmds.ls(
                        cmds.listHistory(base_mesh) or [],
                        type="polyColorPerVertex")
                    if pvc_nodes:
                        cmds.delete(pvc_nodes)
                    cmds.setAttr(f"{base_mesh}.displayColors", 1 if prev_disp else 0)
            except Exception:
                pass
        self._dv_meshes = []
        self.btn_delta_view.setEnabled(True)
        self.btn_exit_delta_view.setEnabled(False)
        self._set_status("Delta View exited.")

    def _run_delta_mush_cleaner(self):
        """Create a Delta Mush deformer on the selected mesh with clean default values."""
        sel = cmds.ls(sl=True, type="transform")
        if not sel:
            self._set_status("Select a mesh first.", error=True)
            return
        mesh = sel[0]
        # Verify it has a shape
        shapes = cmds.listRelatives(mesh, shapes=True, type="mesh") or []
        if not shapes:
            self._set_status(f"'{mesh}' has no mesh shape.", error=True)
            return
        try:
            dm_nodes = cmds.deltaMush(mesh)
            dm = dm_nodes[0]
            mesh_base = mesh.split(":")[-1]
            dm = cmds.rename(dm, f"{mesh_base}_cleaner_dm")
            cmds.setAttr(f"{dm}.smoothingIterations", 3)
            cmds.setAttr(f"{dm}.inwardConstraint",    0.5)
            cmds.setAttr(f"{dm}.outwardConstraint",   0.5)
            cmds.setAttr(f"{dm}.distanceWeight",      1.0)
            self._set_status(f"Delta Mush '{dm}' created on '{mesh}'.")
        except Exception as e:
            self._set_status(f"Delta Mush failed: {e}", error=True)

    def _create_locator(self):
        loc = cmds.spaceLocator(name="split_locator#")[0]
        # Snap to the current selection if an object is selected
        sel = cmds.ls(sl=True)
        if sel:
            pos = cmds.xform(sel[0], q=True, ws=True, t=True)
            cmds.xform(loc, ws=True, t=pos)

        # Add to the locators table
        long_name  = cmds.ls(loc, long=True)[0]
        short_name = long_name.split("|")[-1]
        new_row    = self.table.rowCount()
        self.table.insertRow(new_row)
        item_loc = QtWidgets.QTableWidgetItem(short_name)
        item_loc.setFlags(item_loc.flags() | QtCore.Qt.ItemIsEditable)
        item_loc.setData(QtCore.Qt.UserRole, long_name)
        self.table.setItem(new_row, 0, item_loc)
        self.table.setItem(new_row, 1, QtWidgets.QTableWidgetItem(""))

        # Refresh all suffixes now that row count changed
        suffixes = self._auto_suffixes(self.table.rowCount())
        for row in range(self.table.rowCount()):
            self.table.setItem(row, 2, QtWidgets.QTableWidgetItem(suffixes[row]))

        self._update_single_loc_state()
        self._resize_table_to_content()
        self._combo_split_preset.setCurrentIndex(0)
        self._set_status(f"✓ Locator created : {short_name}")

    def _get_locators_from_selection(self):
        sel = cmds.ls(sl=True, long=True)
        locators = [
            s for s in sel
            if cmds.nodeType(s) == "transform"
            and cmds.listRelatives(s, shapes=True, type="locator")
        ]
        if not locators:
            cmds.warning("No locator selected.")
            return

        # Collect long names already in the table to avoid duplicates
        existing = set()
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item:
                existing.add(item.data(QtCore.Qt.UserRole) or "")

        new_locs = [loc for loc in locators if loc not in existing]
        if not new_locs:
            return

        for loc in new_locs:
            short_name = loc.split("|")[-1]
            new_row = self.table.rowCount()
            self.table.insertRow(new_row)
            item_loc = QtWidgets.QTableWidgetItem(short_name)
            item_loc.setFlags(item_loc.flags() | QtCore.Qt.ItemIsEditable)
            item_loc.setData(QtCore.Qt.UserRole, loc)
            self.table.setItem(new_row, 0, item_loc)
            self.table.setItem(new_row, 1, QtWidgets.QTableWidgetItem(""))
            self.table.setItem(new_row, 2, QtWidgets.QTableWidgetItem(""))

        # Recalculate suffixes for the whole table now that row count changed
        suffixes = self._auto_suffixes(self.table.rowCount())
        for row in range(self.table.rowCount()):
            self.table.setItem(row, 2, QtWidgets.QTableWidgetItem(suffixes[row]))

        self._update_single_loc_state()
        self._resize_table_to_content()
        self._combo_split_preset.setCurrentIndex(0)

    def _move_row_up(self):
        rows = sorted({idx.row() for idx in self.table.selectedIndexes()})
        if not rows or rows[0] <= 0:
            return
        for row in rows:
            self._swap_rows(row, row - 1)
        self._reselect_rows([r - 1 for r in rows])
        self._combo_split_preset.setCurrentIndex(0)

    def _move_row_down(self):
        rows = sorted({idx.row() for idx in self.table.selectedIndexes()}, reverse=True)
        if not rows or rows[0] >= self.table.rowCount() - 1:
            return
        for row in rows:
            self._swap_rows(row, row + 1)
        self._reselect_rows([r + 1 for r in rows])
        self._combo_split_preset.setCurrentIndex(0)

    def _reselect_rows(self, rows):
        self.table.clearSelection()
        last_col = self.table.columnCount() - 1
        for r in rows:
            rng = QtWidgets.QTableWidgetSelectionRange(r, 0, r, last_col)
            self.table.setRangeSelected(rng, True)

    def _swap_rows(self, r1, r2):
        for col in range(self.table.columnCount()):
            item1 = self.table.takeItem(r1, col)
            item2 = self.table.takeItem(r2, col)
            self.table.setItem(r1, col, item2)
            self.table.setItem(r2, col, item1)

    def _remove_row(self):
        rows = sorted(
            {idx.row() for idx in self.table.selectedIndexes()},
            reverse=True
        )
        if not rows:
            return
        for row in rows:
            self.table.removeRow(row)

        # Refresh suffixes for the new count
        suffixes = self._auto_suffixes(self.table.rowCount())
        for row in range(self.table.rowCount()):
            self.table.setItem(row, 2, QtWidgets.QTableWidgetItem(suffixes[row]))

        self._update_single_loc_state()
        self._resize_table_to_content()
        self._combo_split_preset.setCurrentIndex(0)

    def _clear_table(self):
        """Remove all locators from the table."""
        if self.table.rowCount() == 0:
            return
        self.table.setRowCount(0)
        self._update_single_loc_state()
        self._resize_table_to_content()
        self._combo_split_preset.setCurrentIndex(0)

    @undo_chunk
    def _run_link_mirrors(self):
        n = self.table.rowCount()
        if n == 0:
            self._set_status("No locators in table", error=True)
            return

        r_rows = sorted([r for r in range(n)
                         if (self.table.item(r, 1) or QtWidgets.QTableWidgetItem()).text() == 'R'])
        l_rows = sorted([r for r in range(n)
                         if (self.table.item(r, 1) or QtWidgets.QTableWidgetItem()).text() == 'L'])

        if not r_rows or not l_rows:
            self._set_status("Link Mirrors requires L and R sides — enable Symmetric L/R first", error=True)
            return
        if len(r_rows) != len(l_rows):
            self._set_status(f"Unequal L/R count ({len(l_rows)}L / {len(r_rows)}R)", error=True)
            return

        # Collect R locators from table to clean any existing links first
        r_locs = [self.table.item(r, 0).data(QtCore.Qt.UserRole) for r in r_rows]
        unlink_mirror_locators(r_locs)

        # Pair : R[0]<->L[-1], R[1]<->L[-2], ...  (symmetric match)
        pairs = list(zip(r_rows, reversed(l_rows)))

        try:
            for r_row, l_row in pairs:
                R_loc = self.table.item(r_row, 0).data(QtCore.Qt.UserRole)
                L_loc = self.table.item(l_row, 0).data(QtCore.Qt.UserRole)
                link_mirror_locators(L_loc, R_loc)
            self._set_status(f"{len(pairs)} mirror pair{'s' if len(pairs) > 1 else ''} linked")
        except Exception as e:
            traceback.print_exc()
            self._set_status(f"{e}", error=True)

    def _run_unlink_mirrors(self):
        n = self.table.rowCount()
        r_locs = [
            self.table.item(r, 0).data(QtCore.Qt.UserRole)
            for r in range(n)
            if (self.table.item(r, 1) or QtWidgets.QTableWidgetItem()).text() == 'R'
        ]
        if not r_locs:
            self._set_status("No R locators in table", error=True)
            return
        try:
            removed = unlink_mirror_locators(r_locs)
            self._set_status(f"Mirror links removed" if removed else "No mirror links found")
        except Exception as e:
            traceback.print_exc()
            self._set_status(f"{e}", error=True)

    # ── Action slots ──────────────────────────────────────────────────────────

    def _set_status(self, msg, error=False, neutral=False):
        self.lbl_status.setText(msg)
        color = "#e05252" if error else ("#ffffff" if neutral else "#7ec87e")
        self.lbl_status.setStyleSheet(f"color: {color};")
        self._refresh_top_status(check_phantoms=True)

    def _progress_begin(self, total):
        self.progress_bar.setMaximum(total)
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(True)

    def _progress_step(self, value, msg=""):
        self.progress_bar.setValue(value)
        if msg:
            self.lbl_status.setText(msg)
            self.lbl_status.setStyleSheet("color: #aaaaaa;")
        QtWidgets.QApplication.processEvents()

    def _progress_end(self):
        self.progress_bar.setVisible(False)
        self.progress_bar.setValue(0)

    def _refresh_top_status(self, check_phantoms=False):
        try:
            results = get_selected_targets()  # [(bs_node, idx, name), ...]
            if not results:
                # If a target is in edit mode, show it even without a Shape Editor selection
                last = getattr(self, '_last_sculpt_hook', None)
                if last and last[1] >= 0 and cmds.objExists(last[0]):
                    bs_node_e, idx_e = last[0], last[1]
                    try:
                        name_e = cmds.aliasAttr(f"{bs_node_e}.weight[{idx_e}]", query=True) or "?"
                        self.lbl_top_status.setText(f"{bs_node_e}  ·  {name_e}  (edit)")
                        self.lbl_top_status.setStyleSheet(
                            "color: #ff6666; font-size: 11px; padding-top: 4px; padding-bottom: 4px;")
                        self._cached_phantom_count = 0
                        self._lbl_active_target.setText(name_e)
                        self._lbl_active_target.setCursor(QtCore.Qt.PointingHandCursor)
                        self._btn_edit_target.setEnabled(True)
                        self._apply_edit_btn_style(True)
                        self._edit_poll_state = (bs_node_e, idx_e, True)
                        attr_w = f"{bs_node_e}.weight[{idx_e}]"
                        w_val  = cmds.getAttr(attr_w)
                        locked = cmds.getAttr(attr_w, lock=True)
                        driven = bool(cmds.listConnections(
                            attr_w, source=True, plugs=True, destination=False))
                        tip = "Locked" if locked else (
                            "Driven by a connection — cannot be set manually." if driven
                            else "Target weight")
                        self._set_weight_widgets(
                            enabled=not locked and not driven, value=w_val, tip=tip)
                        return
                    except Exception:
                        pass
                self.lbl_top_status.setText("— no selection —")
                self.lbl_top_status.setStyleSheet("color: #666666; font-size: 11px; padding-top: 4px; padding-bottom: 4px;")
                self._cached_phantom_count = 0
                self._lbl_active_target.setText("—")
                self._lbl_active_target.setStyleSheet("color: #aaaaaa;")
                self._lbl_active_target.setCursor(QtCore.Qt.ArrowCursor)
                self._btn_edit_target.setEnabled(False)
                self._apply_edit_btn_style(False)
                self._edit_poll_state = None
                self._set_weight_widgets(enabled=False, value=0.0, tip="")
                self._btn_target_vis.blockSignals(True)
                self._btn_target_vis.setChecked(False)
                self._btn_target_vis.blockSignals(False)
                self._btn_target_vis.setEnabled(False)
                if not self._pix_tgt_on.isNull():
                    self._icon_target_lbl.setPixmap(self._pix_tgt_on)
                return

            bs_node = results[0][0]
            first_idx = results[0][1]
            n_selected = len(results)

            indices = cmds.getAttr(f"{bs_node}.weight", multiIndices=True) or []
            n_total = len(indices)

            if check_phantoms:
                self._cached_phantom_count = sum(
                    1 for i in indices
                    if cmds.aliasAttr(f"{bs_node}.weight[{i}]", query=True) is None
                )

            first_name = cmds.aliasAttr(f"{bs_node}.weight[{first_idx}]", query=True) or "?"
            if n_selected == 1:
                sel_str = f"{bs_node}  ·  {first_name}"
            else:
                sel_str = f"{bs_node}  ·  {first_name}  [...]  {n_selected} of {n_total} selected"

            parts = [sel_str]
            if self._cached_phantom_count:
                parts.append(
                    f"{self._cached_phantom_count} phantom slot"
                    f"{'s' if self._cached_phantom_count > 1 else ''}"
                )

            self.lbl_top_status.setText("  ·  ".join(parts))
            color = "#c8a030" if self._cached_phantom_count else "#aaaaaa"
            self.lbl_top_status.setStyleSheet(f"color: {color}; font-size: 11px; padding-top: 4px; padding-bottom: 4px;")

            # Update active target label; reset edit state only on target change
            bs_node_r, idx_r, name_r = results[0]
            self._lbl_active_target.setText(name_r)
            self._lbl_active_target.setCursor(QtCore.Qt.PointingHandCursor)
            self._btn_target_vis.setEnabled(True)
            self._btn_edit_target.setEnabled(not self._vis_is_hidden())

            # Weight slider — refresh every time (value may have changed externally)
            attr_w = f"{bs_node_r}.weight[{idx_r}]"
            try:
                w_val  = cmds.getAttr(attr_w)
                locked = cmds.getAttr(attr_w, lock=True)
                driven = bool(cmds.listConnections(attr_w, source=True, plugs=True, destination=False))
                if locked:
                    tip = "Locked"
                elif driven:
                    tip = "Driven by a connection — cannot be set manually."
                else:
                    tip = "Target weight"
                self._set_weight_widgets(enabled=not locked and not driven, value=w_val, tip=tip)
            except Exception:
                self._set_weight_widgets(enabled=False, value=0.0, tip="")

            current = self._edit_poll_state
            if not current or current[0] != bs_node_r or current[1] != idx_r:
                # New target selected — reset visibility toggle
                self._btn_target_vis.blockSignals(True)
                self._btn_target_vis.setChecked(False)
                self._btn_target_vis.blockSignals(False)
                self._lbl_active_target.setStyleSheet("color: #aaaaaa;")
                if not self._pix_tgt_on.isNull():
                    self._icon_target_lbl.setPixmap(self._pix_tgt_on)
                # Consult the last hook event — the hook may have fired before
                # _edit_poll_state was initialised (e.g. edit activated via native
                # Shape Editor before the user clicked to trigger _refresh_top_status).
                last = getattr(self, '_last_sculpt_hook', None)
                edit_on = bool(last and last[0] == bs_node_r and last[1] == idx_r)
                self._apply_edit_btn_style(edit_on)
                self._edit_poll_state = (bs_node_r, idx_r, edit_on)
        except Exception:
            pass

    def _get_targets_or_warn(self):
        results = get_selected_targets()
        if not results:
            cmds.warning("Please select at least one target in the Shape Editor.")
            return []
        return results

    def _select_active_target_in_shape_editor(self):
        """Re-select the displayed active target in the Shape Editor."""
        state = self._edit_poll_state
        if not state:
            return
        bs_node, idx = state[0], state[1]
        try:
            mel.eval(f'shapeEditorTreeviewSelect "{bs_node}.{idx}";')
        except Exception as e:
            cmds.warning(f"Could not select target in Shape Editor: {e}")

    def _update_nom_preview(self):
        pass  # Preview is now shown inside the Naming Convention dialog

    def _build_target_name(self, base_name, side, suffix):
        """
        Assembles the final target name from self._nom_token_order and self._nom_prefix.
        Empty tokens are dropped — no double underscores produced.
          {prefix} ->self._nom_prefix
          {side}   ->e.g. "R", "L", "C"  (empty string = token skipped)
          {target} ->base_name
          {suffix} ->e.g. "a", "b", "up"  (empty string = token skipped)
        """
        token_map = {
            "{prefix}": self._nom_prefix,
            "{side}":   side,
            "{target}": base_name,
            "{suffix}": suffix,
        }
        parts = []
        for tok in self._nom_token_order:
            val = token_map.get(tok, tok.strip("{}"))
            if val:
                parts.append(val)
        return "_".join(parts)

    # ── Wire Setup callbacks ───────────────────────────────────────────────────

    def _wire_get_base(self):
        sel = cmds.ls(sl=True, transforms=True)
        if not sel:
            self._set_status("✗ Wire Setup: select a mesh transform first", error=True)
            return
        self.edit_wire_base.setText(sel[0])

    def _wire_get_edges(self):
        sel = cmds.ls(sl=True, flatten=True)
        edges = [s for s in sel if ".e[" in s]
        if not edges:
            self._set_status("✗ Wire Setup: select edges first", error=True)
            return
        self.edit_wire_edges.setText(str(edges))
        self._set_status(f"✓ {len(edges)} edge(s) captured")

    def _wire_add_shape(self):
        name = self.edit_wire_shape_add.text().strip()
        if not name:
            return
        item = QtWidgets.QListWidgetItem(name)
        item.setFlags(item.flags() | QtCore.Qt.ItemIsEditable)
        self.list_wire_shapes.addItem(item)
        self.edit_wire_shape_add.clear()

    def _wire_remove_shape(self):
        for item in self.list_wire_shapes.selectedItems():
            self.list_wire_shapes.takeItem(self.list_wire_shapes.row(item))

    def _wire_shape_names(self):
        return [self.list_wire_shapes.item(i).text().strip()
                for i in range(self.list_wire_shapes.count())
                if self.list_wire_shapes.item(i).text().strip()]

    def _run_paint_wire(self):
        wire_node = "wire_setup_wire"
        mesh = "wire_setup_msh"
        if not cmds.objExists(wire_node):
            self._set_status("✗ Paint Wire: wire_setup_wire not found in scene", error=True)
            return
        if not cmds.objExists(mesh):
            self._set_status("✗ Paint Wire: wire_setup_msh not found in scene", error=True)
            return
        sel = cmds.ls(sl=True, transforms=True)
        if mesh not in sel:
            cmds.select(mesh, replace=True)
        mel.eval(f'artSetToolAndSelectAttr "artAttrCtx" "wire.{wire_node}.weights"')
        mel.eval('artAttrInitPaintableAttr')
        mel.eval('toolPropertyShow')
        self._set_status("✓ Paint Wire tool opened")

    def _activate_sculpt_tool(self, mel_cmd):
        mel.eval(mel_cmd)
        self._sync_tool_strength()

    def _sync_tool_strength(self):
        try:
            val = cmds.sculptMeshCacheCtx("sculptMeshCacheContext", q=True, strength=True)
            self.spin_tool_strength.blockSignals(True)
            self.slider_tool_strength.blockSignals(True)
            self.spin_tool_strength.setValue(val)
            self.slider_tool_strength.setValue(int(round(val * 10)))
            self.spin_tool_strength.blockSignals(False)
            self.slider_tool_strength.blockSignals(False)
        except Exception:
            pass

    def _apply_tool_strength(self, value):
        try:
            cmds.sculptMeshCacheCtx("sculptMeshCacheContext", edit=True, strength=value)
        except Exception:
            pass

    def _on_tool_strength_slider(self, int_val):
        val = int_val / 10.0
        self.spin_tool_strength.blockSignals(True)
        self.spin_tool_strength.setValue(val)
        self.spin_tool_strength.blockSignals(False)
        self._apply_tool_strength(val)

    def _on_tool_strength_spin(self, val):
        self.slider_tool_strength.blockSignals(True)
        self.slider_tool_strength.setValue(int(round(val * 10)))
        self.slider_tool_strength.blockSignals(False)
        self._apply_tool_strength(val)

    def _on_tool_surf_vol_changed(self, index):
        value = self.combo_tool_surf_vol.itemData(index)
        try:
            cmds.sculptMeshCacheCtx("sculptMeshCacheContext", edit=True, falloffType=value)
        except Exception:
            pass

    def _on_tool_symmetry_changed(self, index):
        value = self.combo_tool_symmetry.itemData(index)
        try:
            cmds.sculptMeshCacheCtx("sculptMeshCacheContext", edit=True, mirror=value)
        except Exception:
            pass

    def _open_tool_settings(self):
        """Open Maya's Tool Settings window for the currently active tool."""
        for cmd in ("toolPropertyWindow;", 'toolPropertyWindow1 ("");',
                    "artAttrValues artAttrContext;", "toolPropertyShow;",
                    "dR_updateToolSettings;"):
            try:
                mel.eval(cmd)
            except Exception:
                pass
        self._set_status("✓ Tool Settings opened")

    def _run_paint_wire_settings(self):
        self._open_tool_settings()

    def _run_mirror_wire_weights(self):
        wire_node = "wire_setup_wire"
        mesh = "wire_setup_msh"
        if not cmds.objExists(wire_node):
            self._set_status("✗ Mirror Wire: wire_setup_wire not found in scene", error=True)
            return
        if not cmds.objExists(mesh):
            self._set_status("✗ Mirror Wire: wire_setup_msh not found in scene", error=True)
            return
        mel.eval(
            f'copyDeformerWeights -sd {wire_node} -ss {mesh} -ds {mesh}'
            f' -mirrorMode YZ -surfaceAssociation closestPoint'
        )
        self._set_status("✓ Wire weights mirrored (YZ)")

    @undo_chunk
    def _run_create_wire_setup(self):
        mesh = self.edit_wire_base.text().strip()
        if not mesh:
            self._set_status("✗ Wire Setup: no base mesh set", error=True)
            return
        if not cmds.objExists(mesh):
            self._set_status(f"✗ Wire Setup: '{mesh}' not found in scene", error=True)
            return
        edges_raw = self.edit_wire_edges.text().strip()
        if not edges_raw:
            self._set_status("✗ Wire Setup: no edges captured", error=True)
            return
        try:
            edges = eval(edges_raw)
        except Exception:
            self._set_status("✗ Wire Setup: invalid edge data", error=True)
            return
        shape_names = self._wire_shape_names()
        if not shape_names:
            self._set_status("✗ Wire Setup: shape list is empty", error=True)
            return
        try:
            create_wire_setup(
                mesh, edges, shape_names,
                dropoff=float(self.spin_wire_dropoff.text() or "100.0"),
                rotation=float(self.spin_wire_rotation.text() or "0.0"),
                spans=int(self.spin_wire_spans.text() or "4"),
                flat_curve=self.chk_wire_flat.isChecked()
            )
            self._set_status(f"✓ Wire setup created — {len(shape_names)} shape(s)")
        except Exception as e:
            import traceback; traceback.print_exc()
            self._set_status(f"✗ Wire Setup: {e}", error=True)

    def _run_copy_wire_delta(self):
        """Copy the wire weight of the single selected vertex into the clipboard."""
        raw_sel = cmds.ls(sl=True, flatten=True) or []
        vtx_sel = [s for s in raw_sel if ".vtx[" in s]
        if len(vtx_sel) != 1:
            self._set_status(
                f"Copy Wire Weight: select exactly 1 vertex "
                f"({'none' if not vtx_sel else len(vtx_sel)} selected).",
                error=True)
            return
        vtx_str = vtx_sel[0]
        mesh = vtx_str.split(".vtx[")[0]
        vi   = int(vtx_str.split(".vtx[")[1].rstrip("]"))
        wire_nodes = cmds.ls(cmds.listHistory(mesh) or [], type="wire")
        if not wire_nodes:
            self._set_status(
                f"Copy Wire Weight: no wire deformer found on '{mesh}'.",
                error=True)
            return
        wire_node = wire_nodes[0]
        weight = cmds.percent(wire_node, vtx_str, q=True, v=True)[0]
        self._wire_weight_clipboard = {"wire": wire_node, "vi": vi, "weight": weight}
        self.btn_paste_wire_delta.setEnabled(True)
        self._set_status(
            f"Wire weight copied from vtx[{vi}] on '{wire_node}': {weight:.4f}")

    @undo_chunk
    def _run_paste_wire_delta(self):
        """Paste the copied wire weight onto all selected vertices."""
        if not getattr(self, "_wire_weight_clipboard", None):
            self._set_status(
                "Paste Wire Weight: clipboard is empty — Copy Wire Weight first.",
                error=True)
            return
        raw_sel = cmds.ls(sl=True, flatten=True) or []
        vtx_sel = [s for s in raw_sel if ".vtx[" in s]
        if not vtx_sel:
            self._set_status("Paste Wire Weight: no vertices selected.", error=True)
            return
        mesh = vtx_sel[0].split(".vtx[")[0]
        wire_nodes = cmds.ls(cmds.listHistory(mesh) or [], type="wire")
        if not wire_nodes:
            self._set_status(
                f"Paste Wire Weight: no wire deformer found on '{mesh}'.",
                error=True)
            return
        wire_node = wire_nodes[0]
        weight = self._wire_weight_clipboard["weight"]
        cmds.percent(wire_node, *vtx_sel, v=weight)
        self._set_status(
            f"Wire weight {weight:.4f} pasted onto {len(vtx_sel)} vert(s) "
            f"on '{wire_node}'.")

    @undo_chunk
    def _run_hammer_wire_weights(self):
        """Pure Laplacian smooth of wire weights on selected vertices.
        Each selected vertex converges to the uniform average of its edge-connected
        neighbours' weights (same principle as the blendshape surface hammer)."""
        raw_sel = cmds.ls(sl=True, flatten=True) or []
        vtx_sel = [s for s in raw_sel if ".vtx[" in s]
        if not vtx_sel:
            self._set_status("✗ Hammer Wire Weights: select vertices first", error=True)
            return
        mesh = vtx_sel[0].split(".vtx[")[0]
        wire_nodes = cmds.ls(cmds.listHistory(mesh) or [], type="wire")
        if not wire_nodes:
            self._set_status(
                f"✗ Hammer Wire Weights: no wire deformer found on '{mesh}'", error=True)
            return
        wire_node = wire_nodes[0]
        sel_indices = set(int(v.split(".vtx[")[1].rstrip("]")) for v in vtx_sel)

        # Build adjacency map once for all selected vertices
        adj = {}
        for vi in sel_indices:
            edges = cmds.polyListComponentConversion(
                f"{mesh}.vtx[{vi}]", fromVertex=True, toEdge=True)
            ring = cmds.ls(
                cmds.filterExpand(
                    cmds.polyListComponentConversion(edges, fromEdge=True, toVertex=True),
                    selectionMask=31) or [],
                flatten=True)
            adj[vi] = [int(v.split(".vtx[")[1].rstrip("]")) for v in ring if v != f"{mesh}.vtx[{vi}]"]

        # Read all weights needed (selected + their neighbours) before writing
        needed = sel_indices | {nb for nbs in adj.values() for nb in nbs}
        w_snap = {vi: cmds.percent(wire_node, f"{mesh}.vtx[{vi}]", q=True, v=True)[0]
                  for vi in needed}

        # Pure Laplacian: replace each selected vertex with average of neighbours only
        for vi in sel_indices:
            nbrs = adj.get(vi, [])
            if not nbrs:
                continue
            cmds.percent(wire_node, f"{mesh}.vtx[{vi}]",
                         v=sum(w_snap[nb] for nb in nbrs) / len(nbrs))
        self._set_status(
            f"✓ Hammer Wire Weights — {len(sel_indices)} vtx on '{wire_node}'")

    @undo_chunk
    def _run_bake_wire(self):
        mesh = self.edit_wire_base.text().strip()
        if not mesh:
            self._set_status("✗ Bake Wire: no base mesh set", error=True)
            return
        if not cmds.objExists(mesh):
            self._set_status(f"✗ Bake Wire: '{mesh}' not found in scene", error=True)
            return
        shape_names = self._wire_shape_names()
        if not shape_names:
            self._set_status("✗ Bake Wire: shape list is empty", error=True)
            return
        # Check for shapes with no deltas before baking
        empty_shapes = check_wire_shapes_have_deltas(shape_names)
        if empty_shapes:
            msg = "\n".join(f"  • {s}" for s in empty_shapes)
            result = QtWidgets.QMessageBox.warning(
                self,
                "No Deltas Detected",
                f"The following shapes have no vertex displacement:\n\n{msg}\n\n"
                "These targets will be baked empty.\nProceed anyway?",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.Cancel
            )
            if result != QtWidgets.QMessageBox.Yes:
                self._set_status("Bake Wire cancelled.")
                return

        try:
            bs_node, baked = bake_wire_to_mesh(mesh, shape_names)
            if self._wire_delete_after_bake:
                if cmds.objExists("wire_setup_grp"):
                    cmds.delete("wire_setup_grp")
            self._set_status(f"✓ Baked {len(baked)} shape(s) ->{bs_node}")
        except Exception as e:
            import traceback; traceback.print_exc()
            self._set_status(f"✗ Bake Wire: {e}", error=True)

    # ── Joints Setup callbacks ─────────────────────────────────────────────────

    def _joints_get_middle(self):
        sel = cmds.ls(sl=True, fl=True) or []
        edges = [s for s in sel if ".e[" in s]
        if len(edges) != 1:
            self._set_status(
                f"✗ Select exactly 1 edge for Middle Edge ({len(edges)} selected)",
                error=True)
            return
        self.line_joints_middle.setText(edges[0])

    @undo_chunk
    def _run_build_lip_rig(self):
        middle = self.line_joints_middle.text().strip()
        if not middle:
            self._set_status("✗ Joints Setup: set Middle Edge first", error=True)
            return
        mesh = middle.split(".")[0]
        try:
            rig_grp, skin_joints = build_lip_rig(mesh, middle)
            self._set_status(
                f"✓ Lip rig built — {len(skin_joints)} influence(s), all weights on zero_out")
        except Exception as e:
            import traceback; traceback.print_exc()
            self._set_status(f"✗ Build Rig: {e}", error=True)

    # ── Cluster to Joint callbacks ─────────────────────────────────────────

    def _ctj_pick_mesh(self):
        sel = cmds.ls(selection=True, transforms=True)
        if not sel:
            self._set_status("✗ Cluster to Joint: select a mesh transform first", error=True)
            return
        self.edit_ctj_mesh.setText(sel[0])

    def _ctj_refresh_clusters(self):
        self.combo_ctj_cluster.clear()
        mesh = self.edit_ctj_mesh.text().strip()
        if not mesh or not cmds.objExists(mesh):
            return
        history = cmds.listHistory(mesh, pruneDagObjects=True) or []
        cluster_nodes = cmds.ls(history, type="cluster") or []
        for c in cluster_nodes:
            self.combo_ctj_cluster.addItem(c)
        # Auto-detection triggered via currentTextChanged signal

    def _ctj_try_restore_setup(self, cluster=""):
        """If a CTJ skinCluster already exists for this cluster, restore state variables."""
        cluster = cluster or self.combo_ctj_cluster.currentText()
        mesh    = self.edit_ctj_mesh.text().strip()
        if not cluster:
            return
        skin_name  = f"{cluster}_ctj_skin"
        djnt_name  = f"{cluster}_jnt"
        zjnt_name  = f"{cluster}_zero_jnt"
        if (cmds.objExists(skin_name) and
                cmds.objExists(djnt_name) and
                cmds.objExists(zjnt_name)):
            self._ctj_skin_node    = skin_name
            self._ctj_cluster_node = cluster
            self._ctj_mesh         = mesh
            self._ctj_deform_jnt   = djnt_name
            self._ctj_zero_jnt     = zjnt_name
            self._ctj_n_verts      = (cmds.polyEvaluate(mesh, vertex=True)
                                      if mesh and cmds.objExists(mesh) else 0)
            self._set_status(
                f"✓ Cluster to Joint: existing setup detected — {djnt_name}  |  Bake when ready")
        else:
            # Clear stale state if the cluster changed and no setup found
            if getattr(self, "_ctj_cluster_node", None) == cluster:
                return  # same cluster, setup may have just been run — don't clear
            self._ctj_skin_node    = None
            self._ctj_cluster_node = None
            self._ctj_deform_jnt   = None
            self._ctj_zero_jnt     = None
            self._ctj_n_verts      = 0

    def _run_ctj_setup(self):
        mesh = self.edit_ctj_mesh.text().strip()
        cluster = self.combo_ctj_cluster.currentText()
        if not mesh or not cmds.objExists(mesh):
            self._set_status("✗ Cluster to Joint: set Mesh first", error=True)
            return
        if not cluster or not cmds.objExists(cluster):
            self._set_status("✗ Cluster to Joint: select a cluster", error=True)
            return
        cmds.undoInfo(openChunk=True, chunkName="Cluster to Joint Setup")
        try:
            skin, djnt, zjnt, nverts = cluster_to_joint_setup(mesh, cluster)
            self._ctj_skin_node    = skin
            self._ctj_cluster_node = cluster
            self._ctj_mesh         = mesh
            self._ctj_deform_jnt   = djnt
            self._ctj_zero_jnt     = zjnt
            self._ctj_n_verts      = nverts
            self._set_status(f"✓ Cluster to Joint: {djnt}  |  Paint weights, then Bake")
        except Exception as e:
            import traceback; traceback.print_exc()
            self._set_status(f"✗ Cluster to Joint Setup: {e}", error=True)
        finally:
            cmds.undoInfo(closeChunk=True)

    def _run_ctj_bake(self):
        skin    = getattr(self, "_ctj_skin_node",    None)
        cluster = getattr(self, "_ctj_cluster_node", None)
        mesh    = getattr(self, "_ctj_mesh",         None)
        djnt    = getattr(self, "_ctj_deform_jnt",   None)
        zjnt    = getattr(self, "_ctj_zero_jnt",     None)
        nverts  = getattr(self, "_ctj_n_verts",      None)
        if not all([skin, cluster, mesh, djnt, zjnt, nverts]):
            self._set_status("✗ Bake to Cluster: run Setup first", error=True)
            return
        if not cmds.objExists(skin):
            self._set_status("✗ Bake to Cluster: skinCluster no longer exists", error=True)
            return
        keep = not self.chk_ctj_delete_joints.isChecked()
        cmds.undoInfo(openChunk=True, chunkName="Bake to Cluster")
        try:
            bake_joint_weights_to_cluster(skin, cluster, mesh, nverts, djnt, zjnt,
                                          keep_skin=keep)
            if not keep:
                self._ctj_skin_node  = None
                self._ctj_deform_jnt = None
                self._ctj_zero_jnt   = None
            self._set_status(f"✓ Baked joint weights -> '{cluster}'  (cluster re-enabled)")
        except Exception as e:
            import traceback; traceback.print_exc()
            self._set_status(f"✗ Bake to Cluster: {e}", error=True)
        finally:
            cmds.undoInfo(closeChunk=True)

    # ── Cluster to Joint shelf callbacks ──────────────────────────────────────

    def _run_paint_ctj(self):
        cluster = self.combo_ctj_cluster.currentText()
        mesh    = self.edit_ctj_mesh.text().strip()
        if not cluster or not cmds.objExists(cluster):
            self._set_status("✗ Paint Cluster: set Mesh and select a cluster first", error=True)
            return
        if not mesh or not cmds.objExists(mesh):
            self._set_status("✗ Paint Cluster: set Mesh first", error=True)
            return
        sel = cmds.ls(sl=True, transforms=True)
        if mesh not in sel:
            cmds.select(mesh, replace=True)
        mel.eval(f'artSetToolAndSelectAttr "artAttrCtx" "cluster.{cluster}.weights"')
        mel.eval('artAttrInitPaintableAttr')
        mel.eval('toolPropertyShow')
        self._set_status(f"✓ Paint Cluster tool opened — {cluster}")

    def _run_mirror_ctj(self):
        cluster = self.combo_ctj_cluster.currentText()
        mesh    = self.edit_ctj_mesh.text().strip()
        if not cluster or not cmds.objExists(cluster):
            self._set_status("✗ Mirror Cluster: set Mesh and select a cluster first", error=True)
            return
        if not mesh or not cmds.objExists(mesh):
            self._set_status("✗ Mirror Cluster: set Mesh first", error=True)
            return
        mel.eval(
            f'copyDeformerWeights -sd {cluster} -ss {mesh} -ds {mesh}'
            f' -mirrorMode YZ -surfaceAssociation closestPoint'
        )
        self._set_status(f"✓ Cluster weights mirrored (YZ) — {cluster}")

    def _run_copy_ctj_weight(self):
        raw_sel = cmds.ls(sl=True, flatten=True) or []
        vtx_sel = [s for s in raw_sel if ".vtx[" in s]
        if len(vtx_sel) != 1:
            self._set_status(
                f"Copy Cluster Weight: select exactly 1 vertex "
                f"({'none' if not vtx_sel else len(vtx_sel)} selected).",
                error=True)
            return
        vtx_str = vtx_sel[0]
        cluster = self.combo_ctj_cluster.currentText()
        if not cluster or not cmds.objExists(cluster):
            mesh = vtx_str.split(".vtx[")[0]
            found = cmds.ls(cmds.listHistory(mesh) or [], type="cluster") or []
            if not found:
                self._set_status("Copy Cluster Weight: no cluster found.", error=True)
                return
            cluster = found[0]
        vi     = int(vtx_str.split(".vtx[")[1].rstrip("]"))
        weight = cmds.percent(cluster, vtx_str, q=True, v=True)[0]
        self._ctj_weight_clipboard = {"cluster": cluster, "vi": vi, "weight": weight}
        self.btn_paste_ctj_weight.setEnabled(True)
        self._set_status(f"Cluster weight copied from vtx[{vi}] on '{cluster}': {weight:.4f}")

    @undo_chunk
    def _run_paste_ctj_weight(self):
        if not getattr(self, "_ctj_weight_clipboard", None):
            self._set_status(
                "Paste Cluster Weight: clipboard empty — Copy Cluster Weight first.",
                error=True)
            return
        raw_sel = cmds.ls(sl=True, flatten=True) or []
        vtx_sel = [s for s in raw_sel if ".vtx[" in s]
        if not vtx_sel:
            self._set_status("Paste Cluster Weight: no vertices selected.", error=True)
            return
        cluster = self._ctj_weight_clipboard["cluster"]
        weight  = self._ctj_weight_clipboard["weight"]
        cmds.percent(cluster, *vtx_sel, v=weight)
        self._set_status(
            f"Cluster weight {weight:.4f} pasted onto {len(vtx_sel)} vert(s) on '{cluster}'.")

    @undo_chunk
    def _run_hammer_ctj_weights(self):
        """Pure Laplacian smooth of cluster weights on selected vertices.
        Each selected vertex converges to the uniform average of its edge-connected
        neighbours' weights (same principle as the blendshape surface hammer)."""
        raw_sel = cmds.ls(sl=True, flatten=True) or []
        vtx_sel = [s for s in raw_sel if ".vtx[" in s]
        if not vtx_sel:
            self._set_status("✗ Hammer Cluster Weights: select vertices first", error=True)
            return
        mesh = vtx_sel[0].split(".vtx[")[0]
        cluster = self.combo_ctj_cluster.currentText()
        if not cluster or not cmds.objExists(cluster):
            found = cmds.ls(cmds.listHistory(mesh) or [], type="cluster") or []
            if not found:
                self._set_status(
                    "✗ Hammer Cluster Weights: no cluster found — set Mesh first", error=True)
                return
            cluster = found[0]
        sel_indices = set(int(v.split(".vtx[")[1].rstrip("]")) for v in vtx_sel)

        # Build adjacency map once for all selected vertices
        adj = {}
        for vi in sel_indices:
            edges = cmds.polyListComponentConversion(
                f"{mesh}.vtx[{vi}]", fromVertex=True, toEdge=True)
            ring = cmds.ls(
                cmds.filterExpand(
                    cmds.polyListComponentConversion(edges, fromEdge=True, toVertex=True),
                    selectionMask=31) or [],
                flatten=True)
            adj[vi] = [int(v.split(".vtx[")[1].rstrip("]")) for v in ring if v != f"{mesh}.vtx[{vi}]"]

        # Read all weights needed (selected + their neighbours) before writing
        needed = sel_indices | {nb for nbs in adj.values() for nb in nbs}
        w_snap = {vi: cmds.percent(cluster, f"{mesh}.vtx[{vi}]", q=True, v=True)[0]
                  for vi in needed}

        # Pure Laplacian: replace each selected vertex with average of neighbours only
        for vi in sel_indices:
            nbrs = adj.get(vi, [])
            if not nbrs:
                continue
            cmds.percent(cluster, f"{mesh}.vtx[{vi}]",
                         v=sum(w_snap[nb] for nb in nbrs) / len(nbrs))
        self._set_status(
            f"✓ Hammer Cluster Weights — {len(sel_indices)} vtx on '{cluster}'")

    # ── Copy Deformer Weights callbacks ───────────────────────────────────────

    def _cdw_pick_source(self):
        sel = cmds.ls(selection=True, transforms=True)
        if not sel:
            self._set_status("✗ Copy Deformer Weights: select a source mesh", error=True)
            return
        self.edit_cdw_src_mesh.setText(sel[0])   # textChanged triggers _cdw_refresh_deformers

    def _cdw_pick_targets(self):
        sel = cmds.ls(selection=True, transforms=True)
        src = self.edit_cdw_src_mesh.text().strip()
        # if the source mesh is the first item, skip it automatically
        if src and sel and sel[0] == src:
            sel = sel[1:]
        if not sel:
            self._set_status("✗ Copy Deformer Weights: select target mesh(es)", error=True)
            return
        self._cdw_tgt_meshes = sel
        self.edit_cdw_tgt_mesh.setText(sel[0] if len(sel) == 1 else f"{len(sel)} meshes")
        self._cdw_refresh_deformers(src=False)

    def _cdw_refresh_deformers(self, src=True):
        combo = self.combo_cdw_src_deformer if src else self.combo_cdw_tgt_deformer
        if src:
            mesh = self.edit_cdw_src_mesh.text().strip()
        else:
            mesh = (getattr(self, "_cdw_tgt_meshes", [None]) or [None])[0] or ""
        combo.clear()
        if not mesh or not cmds.objExists(mesh):
            return
        _skip = {"tweak", "skinCluster", "blendShape"}
        history = cmds.listHistory(mesh, pruneDagObjects=True) or []
        deformers = [n for n in cmds.ls(history, type="geometryFilter")
                     if cmds.nodeType(n) not in _skip]
        for d in deformers:
            combo.addItem(d)

    def _run_copy_deformer_weights(self):
        src_mesh   = self.edit_cdw_src_mesh.text().strip()
        src_def    = self.combo_cdw_src_deformer.currentText()
        tgt_meshes = getattr(self, "_cdw_tgt_meshes", [])
        tgt_def    = self.combo_cdw_tgt_deformer.currentText()

        if not src_mesh or not cmds.objExists(src_mesh):
            self._set_status("✗ Copy Deformer Weights: set source mesh", error=True)
            return
        if not src_def or not cmds.objExists(src_def):
            self._set_status("✗ Copy Deformer Weights: pick source deformer", error=True)
            return
        if not tgt_meshes:
            self._set_status("✗ Copy Deformer Weights: pick target mesh(es)", error=True)
            return
        if not tgt_def or not cmds.objExists(tgt_def):
            self._set_status("✗ Copy Deformer Weights: pick target deformer", error=True)
            return
        cmds.undoInfo(openChunk=True, chunkName="Copy Deformer Weights")
        try:
            copy_deformer_weights(src_mesh, src_def, tgt_meshes, tgt_def)
            n = len(tgt_meshes)
            self._set_status(
                f"✓ Copy Deformer Weights: {src_def} -> {tgt_def}  ({n} mesh{'es' if n > 1 else ''})")
        except Exception as e:
            import traceback; traceback.print_exc()
            self._set_status(f"✗ Copy Deformer Weights: {e}", error=True)
        finally:
            cmds.undoInfo(closeChunk=True)

    def _open_check_shapes(self):
        dlg = CheckShapesDialog(parent=self)
        dlg.show()

    def _open_rig_connector(self):
        dlg = RigConnectorDialog(parent=self)
        dlg.show()

    def _browse_rig_json(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Load Rig Mapping",
            _smart_mapping_default(self.line_rig_json.text().strip()),
            "JSON files (*.json)")
        if path:
            self.line_rig_json.setText(path)

    @undo_chunk
    def _run_connect_from_file(self):
        path = self.line_rig_json.text().strip()
        if not path or not os.path.exists(path):
            self._set_status("✗ No JSON file selected", error=True)
            return

        # Auto-detect blendShape node from scene selection
        sel = cmds.ls(selection=True) or []
        bs_node = None
        for obj in sel:
            bs_node = _find_blendshape_on_mesh(obj)
            if bs_node:
                break
        if not bs_node:
            self._set_status("✗ No blendShape node found — select the mesh first", error=True)
            return

        try:
            with open(path, "r") as f:
                data = json.load(f)
        except Exception as e:
            self._set_status(f"✗ Could not read JSON: {e}", error=True)
            return

        # Support both old format (list) and new format (dict with "connections" key)
        soft_blend_pairs = []
        soft_blend_curve = None
        if isinstance(data, dict):
            soft_blend_pairs = data.get("soft_blend_pairs", [])
            soft_blend_curve = data.get("soft_blend_curve")
            data = data.get("connections", [])

        rows = _normalize_connection_rows(data)

        results = build_and_connect_rig(
            bs_node, rows,
            soft_blend_pairs=soft_blend_pairs,
            soft_blend_curve=soft_blend_curve,
        )
        ok      = sum(1 for r in results if r["status"] in ("ok", "ok:direct"))
        missing = sum(1 for r in results if r["status"] == "no_target")
        skp     = sum(1 for r in results if r["status"] in ("skip", "no_ctrl", "no_attr"))
        errors  = sum(1 for r in results if r["status"].startswith("error:"))

        html_parts = []
        if ok:
            html_parts.append(f'<span style="color:#7ec87e;">✓ {ok} connected</span>')
        if missing:
            html_parts.append(f'<span style="color:#aaaaaa;">{missing} missing</span>')
        if skp:
            html_parts.append(f'<span style="color:#aaaaaa;">{skp} skipped</span>')
        if errors:
            html_parts.append(f'<span style="color:#e05252;">✗ {errors} errors</span>')

        sep = '<span style="color:#555555;">  ·  </span>'
        self.lbl_status.setText(sep.join(html_parts) if html_parts else
                                '<span style="color:#7ec87e;">✓ Done</span>')
        self._refresh_top_status(check_phantoms=True)

    def _open_naming_convention(self):
        dlg = NamingConventionDialog(parent_ui=self)
        dlg.show()

    def _run_swap_names(self):
        """
        Swaps the aliasAttr names of exactly 2 selected targets.
        Only names are exchanged — deltas stay untouched.
        Undoable as a single chunk.
        """
        targets = get_selected_targets()
        if len(targets) != 2:
            self._set_status(
                f"✗ Swap Names: select exactly 2 targets ({len(targets)} selected)",
                error=True)
            return

        bs_node_a, idx_a, name_a = targets[0]
        bs_node_b, idx_b, name_b = targets[1]

        if bs_node_a != bs_node_b:
            self._set_status(
                "✗ Swap Names: both targets must be on the same blendShape node",
                error=True)
            return

        if name_a == name_b:
            self._set_status("✗ Swap Names: targets already have the same name", error=True)
            return

        try:
            bs_node = bs_node_a
            # Swap via a temporary name to avoid alias collision
            tmp_name = f"__swap_tmp_{name_a}_{name_b}__"
            cmds.aliasAttr(tmp_name, f"{bs_node}.w[{idx_a}]")
            cmds.aliasAttr(name_a,   f"{bs_node}.w[{idx_b}]")
            cmds.aliasAttr(name_b,   f"{bs_node}.w[{idx_a}]")
            self._set_status(f"✓ Swapped : '{name_a}'  ↔  '{name_b}'")
        except Exception as e:
            import traceback
            traceback.print_exc()
            self._set_status(f"✗ Swap Names: {e}", error=True)

    @undo_chunk
    def _run_add_prefix_suffix(self):
        """Add a prefix and/or suffix to each selected target name."""
        pfx = self.edit_rename_pfx.text().strip()
        sfx = self.edit_rename_sfx.text().strip()
        if not pfx and not sfx:
            self._set_status("✗ Prefix/Suffix: enter at least one value", error=True)
            return
        targets = get_selected_targets()
        if not targets:
            self._set_status("✗ Prefix/Suffix: no targets selected", error=True)
            return
        try:
            for bs_node, idx, name in targets:
                new_name = f"{pfx}{name}{sfx}"
                cmds.aliasAttr(new_name, f"{bs_node}.w[{idx}]")
            self._set_status(f"✓ Renamed {len(targets)} target(s)")
        except Exception as e:
            import traceback
            traceback.print_exc()
            self._set_status(f"✗ Prefix/Suffix: {e}", error=True)

    @undo_chunk
    def _run_search_replace(self):
        """Search & replace in selected target names."""
        search = self.edit_search.text()
        replace = self.edit_replace.text()
        if not search:
            self._set_status("✗ Search & Replace: search field is empty", error=True)
            return
        targets = get_selected_targets()
        if not targets:
            self._set_status("✗ Search & Replace: no targets selected", error=True)
            return
        try:
            renamed = 0
            skipped = 0
            for bs_node, idx, name in targets:
                if search not in name:
                    skipped += 1
                    continue
                new_name = name.replace(search, replace)
                cmds.aliasAttr(new_name, f"{bs_node}.w[{idx}]")
                renamed += 1
            parts = []
            if renamed:
                parts.append(f"✓ Renamed {renamed} target(s)")
            if skipped:
                parts.append(f"{skipped} unchanged (no match)")
            self._set_status("  ".join(parts) if parts else "✗ S&R: nothing to rename")
        except Exception as e:
            import traceback
            traceback.print_exc()
            self._set_status(f"✗ Search & Replace: {e}", error=True)

    def _confirm_delete_regen_meshes(self, blockers, command_name):
        """
        Shows a dialog listing regen meshes that would affect `command_name`.

        Connected regen meshes (is_connected=True) are handled transparently by
        duplicate_target() — they are reused as-is and never deleted here, so the
        sculpt session is preserved and no phantom slot is created.

        Orphaned regen meshes (is_connected=False) collide with sculptTarget's
        naming and must be deleted before the operation can proceed.

        Returns True if the user confirms (or if only connected blockers exist),
        False if the user cancels.
        """
        connected = [(a, n) for a, n, c in blockers if c]
        orphaned  = [(a, n) for a, n, c in blockers if not c]

        lines = []
        for alias, node in connected:
            lines.append(f"  • {alias}  — live regen mesh (sculpt mode active, will be reused)")
        for alias, node in orphaned:
            lines.append(f"  • {alias}  — orphaned regen mesh (will be deleted)")

        reply = QtWidgets.QMessageBox.question(
            self,
            "Regen Mesh Exists",
            f"The following regen mesh(es) are blocking {command_name}:\n\n"
            + "\n".join(lines)
            + "\n\nContinue?",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.Yes,
        )
        if reply != QtWidgets.QMessageBox.Yes:
            return False

        # Only delete orphaned nodes — connected ones are handled by duplicate_target()
        for _alias, node in orphaned:
            if cmds.objExists(node):
                cmds.delete(node)
        return True

    @undo_chunk
    def _run_split(self):
        n_locs = self.table.rowCount()
        if n_locs < 1:
            cmds.warning("Please add at least 1 locator.")
            return

        targets = self._get_targets_or_warn()
        if not targets:
            return

        blockers = find_blocking_regen_meshes(targets)
        if blockers and not self._confirm_delete_regen_meshes(blockers, "Split"):
            return

        locators = []
        sides    = []
        suffixes = []
        for row in range(n_locs):
            locators.append(self.table.item(row, 0).data(QtCore.Qt.UserRole))
            s = self.table.item(row, 1)
            sides.append(s.text() if s else "")
            sfx = self.table.item(row, 2)
            suffixes.append(sfx.text() if sfx else "")

        falloff_func  = CURVE_FUNCTIONS[self.combo_curve.currentText()]
        radius        = (float(self.spin_radius.text()) if self.chk_radius.isChecked() else 0.0)
        axes          = self._get_axes()
        symmetric     = self.chk_symmetric.isChecked() and self.chk_symmetric.isEnabled()
        loc_positions = [cmds.xform(loc, q=True, ws=True, t=True) for loc in locators]
        loc_axes_list = [get_locator_local_axes(loc) for loc in locators]             if self.chk_local_axes.isChecked() else None

        total = 0
        bs_states = {}
        try:
            for bs_node, logical_index, target_name in targets:
                target_name = target_name.replace("Shape", "")
                base_mesh   = get_base_mesh(bs_node)
                if not base_mesh:
                    continue

                deltas        = get_target_deltas(bs_node, logical_index)
                delta_indices = list(deltas.keys())
                if not deltas:
                    cmds.warning(f"No deltas on {target_name}, skipping.")
                    continue

                vtx_positions = get_vtx_world_positions(base_mesh)
                weights       = compute_weights(vtx_positions, loc_positions, delta_indices,
                                                falloff_func, axes, radius,
                                                radial=self.chk_radial.isChecked(),
                                                loc_axes=loc_axes_list,
                                                invert_axis=self.chk_invert_axis.isChecked())

                # Zero all blendShape weights (disconnect driven attrs) — once per bs_node
                if bs_node not in bs_states:
                    bs_states[bs_node] = zero_all_bs_weights(bs_node)

                # Build the list of (loc_idx, final_name) pairs to create
                if symmetric:
                    # Strip existing side prefix so we can rebuild with the correct side
                    base_name = target_name
                    _sides_pfx = {self._nom_side_left, self._nom_side_center, self._nom_side_right}
                    for pfx in [f"{s}_" for s in _sides_pfx] + [f"{s.lower()}_" for s in _sides_pfx]:
                        if target_name.startswith(pfx):
                            base_name = target_name[len(pfx):]
                            break
                    if n_locs == 1:
                        pairs = [(i, self._build_target_name(base_name, sv, ""))
                                 for i, sv in enumerate([self._nom_side_right, self._nom_side_left])]
                    else:
                        pairs = [(i, self._build_target_name(base_name, sides[i], suffixes[i]))
                                 for i in range(n_locs)]
                else:
                    # Keep the full target name (including any side prefix), just append suffix
                    if n_locs == 1:
                        raw_pairs = [(0, suffixes[0]),
                                     (1, "out")]
                    else:
                        raw_pairs = [(i, suffixes[i]) for i in range(n_locs)]
                    pairs = [(i, self._build_target_name(target_name, "", sfx))
                             for i, sfx in raw_pairs]

                # Create all split targets and collect their new indices
                new_indices = []
                existing_shapes = cmds.listAttr(f"{bs_node}.w", m=True) or []
                for loc_idx, final_name in pairs:
                    # Pop the old slot's state so restore_all_bs_weights doesn't try to
                    # reconnect it to a dead index.  The saved connections (SDK etc.) will
                    # be transferred to the new slot after create_split_target returns.
                    old_state = None
                    if final_name in existing_shapes:
                        old_idx   = get_bs_weight_attribute_logical_index(bs_node, final_name)
                        old_state = bs_states[bs_node].pop(old_idx, None)

                    idx = create_split_target(bs_node, base_mesh, final_name,
                                              logical_index, loc_idx, weights, deltas)

                    # Transfer regular incoming connections (SDK, expressions) that were
                    # disconnected by zero_all_bs_weights and saved in old_state.
                    if old_state and old_state["connections"]:
                        new_attr = f"{bs_node}.weight[{idx}]"
                        for src in old_state["connections"]:
                            if cmds.objExists(src.split(".")[0]):
                                try:
                                    cmds.connectAttr(src, new_attr, force=True)
                                except Exception as e:
                                    print(f"  Warning: could not reconnect {src} ->{new_attr}: {e}")

                    new_indices.append(idx)
                    total += 1

            self._set_status(f"✓ {total} target{'s' if total > 1 else ''} created")
        except Exception as e:
            traceback.print_exc()
            self._set_status(f"✗ {e}", error=True)
        finally:
            for _bs, _state in bs_states.items():
                restore_all_bs_weights(_bs, _state)

    def _parse_factor(self, field):
        """Parse a QLineEdit value as float, using dot as decimal separator."""
        try:
            return float(field.text().replace(",", ".") or "1.0")
        except ValueError:
            return 1.0

    @undo_chunk
    def _on_mult_label_click(self, idx):
        """Single-click = exclusive select. Shift+click = toggle add/remove."""
        mods = QtWidgets.QApplication.keyboardModifiers()
        if not (mods & QtCore.Qt.ShiftModifier):
            for i, lbl in enumerate(self._mult_labels):
                if i != idx:
                    lbl.setChecked(False)

    def _on_mult_field_edited(self, idx):
        """Propagate the edited value to all other fields when linked, or to selected fields."""
        value = self._mult_fields[idx].text()
        if self._xyz_linked:
            for i, fld in enumerate(self._mult_fields):
                if i != idx:
                    fld.setText(value)
            _cf = getattr(self, '_compact_mult_fld', None)
            if _cf:
                _cf.setText(value)
        elif self._mult_labels[idx].isChecked():
            for i, (lbl, fld) in enumerate(zip(self._mult_labels, self._mult_fields)):
                if lbl.isChecked() and i != idx:
                    fld.setText(value)

    @undo_chunk
    def _run_delta_combine(self, operation):
        targets = self._get_targets_or_warn()
        if not targets:
            return
        if len(targets) < 2:
            self._set_status("✗ Select at least 2 targets: A (receiver) then B/C/… (donors)", error=True)
            return

        raw_sel     = cmds.ls(sl=True, flatten=True) or []
        vtx_sel     = [s for s in raw_sel if ".vtx[" in s]
        vtx_indices = None if not vtx_sel else [int(s.split(".vtx[")[1].rstrip("]")) for s in vtx_sel]

        bs_node_a, idx_a, name_a = targets[0]
        donors = [(bs, idx) for bs, idx, _ in targets[1:]]

        try:
            written  = combine_target_deltas(bs_node_a, idx_a, donors, operation=operation,
                                             vtx_indices=vtx_indices)
            op_sym   = "+" if operation == 'add' else "\u2212"
            n_donors = len(donors)
            scope    = "all verts" if vtx_indices is None else f"{len(vtx_indices)} vtx"
            self._set_status(
                f"✓ Delta {op_sym} — {n_donors} donor{'s' if n_donors > 1 else ''} \u2192 {name_a}"
                f"  ({scope}, {written} verts)")
        except Exception as e:
            traceback.print_exc()
            self._set_status(f"✗ {e}", error=True)

    def _run_delta_add(self):
        self._run_delta_combine('add')

    def _run_delta_sub(self):
        self._run_delta_combine('sub')

    @undo_chunk
    def _run_delta_transfer(self):
        targets = self._get_targets_or_warn()
        if not targets:
            return
        if len(targets) < 2:
            self._set_status("✗ Transfer: select B (donor) then A (receiver)", error=True)
            return

        raw_sel     = cmds.ls(sl=True, flatten=True) or []
        vtx_sel     = [s for s in raw_sel if ".vtx[" in s]
        vtx_indices = None if not vtx_sel else [int(s.split(".vtx[")[1].rstrip("]")) for s in vtx_sel]

        if vtx_indices is None:
            bs_node_b, idx_b, name_b = targets[0]
            bs_node_a, idx_a, name_a = targets[1]
            msg = QtWidgets.QMessageBox(self)
            msg.setIcon(QtWidgets.QMessageBox.Warning)
            msg.setWindowTitle("Swap — No vertex selection")
            msg.setText(
                f"No vertices are selected.\n\n"
                f"This will swap ALL deltas between:\n"
                f"  {name_a}  ↔  {name_b}\n\n"
                f"Continue?")
            btn_continue = msg.addButton("Continue", QtWidgets.QMessageBox.AcceptRole)
            btn_cancel   = msg.addButton("Cancel",   QtWidgets.QMessageBox.RejectRole)
            msg.setDefaultButton(btn_cancel)
            msg.adjustSize()
            center = self.geometry().center()
            msg.move(center.x() - msg.width() // 2, center.y() - msg.height() // 2)
            msg.exec_()
            if msg.clickedButton() is not btn_continue:
                return

        bs_node_b, idx_b, name_b = targets[0]
        bs_node_a, idx_a, name_a = targets[1]

        try:
            n = transfer_target_deltas(bs_node_a, idx_a, bs_node_b, idx_b,
                                       vtx_indices=vtx_indices)
            scope = "all delta verts" if vtx_indices is None else f"{len(vtx_indices)} vtx"
            self._set_status(f"✓ Transfer {name_b} \u2192 {name_a}  ({scope}, {n} verts)")
        except Exception as e:
            traceback.print_exc()
            self._set_status(f"✗ {e}", error=True)

    @undo_chunk
    def _run_delta_swap_pure(self):
        targets = self._get_targets_or_warn()
        if not targets:
            return
        if len(targets) < 2:
            self._set_status("✗ Swap: select A then B", error=True)
            return

        raw_sel     = cmds.ls(sl=True, flatten=True) or []
        vtx_sel     = [s for s in raw_sel if ".vtx[" in s]
        vtx_indices = None if not vtx_sel else [int(s.split(".vtx[")[1].rstrip("]")) for s in vtx_sel]

        bs_node_a, idx_a, name_a = targets[0]
        bs_node_b, idx_b, name_b = targets[1]

        if vtx_indices is None:
            msg = QtWidgets.QMessageBox(self)
            msg.setIcon(QtWidgets.QMessageBox.Warning)
            msg.setWindowTitle("Swap — No vertex selection")
            msg.setText(f"No vertices selected.\n\nThis will swap ALL deltas between:\n"
                        f"  {name_a}  \u2194  {name_b}\n\nContinue?")
            btn_ok  = msg.addButton("Continue", QtWidgets.QMessageBox.AcceptRole)
            btn_no  = msg.addButton("Cancel",   QtWidgets.QMessageBox.RejectRole)
            msg.setDefaultButton(btn_no)
            msg.adjustSize()
            c = self.geometry().center()
            msg.move(c.x() - msg.width() // 2, c.y() - msg.height() // 2)
            msg.exec_()
            if msg.clickedButton() is not btn_ok:
                return

        try:
            n = swap_target_deltas(bs_node_a, idx_a, bs_node_b, idx_b,
                                   vtx_indices=vtx_indices)
            scope = "all delta verts" if vtx_indices is None else f"{len(vtx_indices)} vtx"
            self._set_status(f"✓ Swap {name_a} \u2194 {name_b}  ({scope}, {n} verts)")
        except Exception as e:
            traceback.print_exc()
            self._set_status(f"✗ {e}", error=True)

    @undo_chunk
    def _run_delta_replace(self):
        targets = self._get_targets_or_warn()
        if not targets:
            return
        if len(targets) < 2:
            self._set_status("✗ Replace: select A (receiver) then B (source)", error=True)
            return

        raw_sel     = cmds.ls(sl=True, flatten=True) or []
        vtx_sel     = [s for s in raw_sel if ".vtx[" in s]
        vtx_indices = None if not vtx_sel else [int(s.split(".vtx[")[1].rstrip("]")) for s in vtx_sel]

        bs_node_a, idx_a, name_a = targets[0]
        bs_node_b, idx_b, name_b = targets[1]

        try:
            n = replace_target_deltas(bs_node_a, idx_a, bs_node_b, idx_b,
                                      vtx_indices=vtx_indices)
            scope = "all delta verts" if vtx_indices is None else f"{len(vtx_indices)} vtx"
            self._set_status(f"✓ Replace {name_a} \u2190 {name_b}  ({scope}, {n} verts)")
        except Exception as e:
            traceback.print_exc()
            self._set_status(f"✗ {e}", error=True)

    def _run_multiply_factors(self, fx, fy, fz):
        raw_sel   = cmds.ls(sl=True, flatten=True) or []
        vtx_sel   = [s for s in raw_sel if ".vtx[" in s]
        all_verts = not vtx_sel

        targets = self._get_targets_or_warn()
        if not targets:
            return

        vtx_indices = None if all_verts else [int(s.split(".vtx[")[1].rstrip("]")) for s in vtx_sel]
        scope       = "all verts" if all_verts else f"{len(vtx_indices)} vtx"

        try:
            for bs_node, logical_index, target_name in targets:
                multiply_target_deltas(bs_node, logical_index, fx, fy, fz,
                                       vtx_indices=vtx_indices)
            n_t = len(targets)
            if fx == 0.0 and fy == 0.0 and fz == 0.0:
                self._set_status(
                    f"Deltas wiped on {n_t} target{'s' if n_t > 1 else ''}  {scope}"
                    f" — Ctrl+Z to undo", neutral=True)
            else:
                self._set_status(
                    f"Multiplied {n_t} target{'s' if n_t > 1 else ''}"
                    f"  X\xd7{fx} Y\xd7{fy} Z\xd7{fz}  {scope}")
        except Exception as e:
            traceback.print_exc()
            self._set_status(f"✗ {e}", error=True)

    def _run_push_normals_mode(self, factor):
        raw_sel     = cmds.ls(sl=True, flatten=True) or []
        vtx_sel     = [s for s in raw_sel if ".vtx[" in s]
        vtx_indices = None if not vtx_sel else [int(s.split(".vtx[")[1].rstrip("]")) for s in vtx_sel]
        targets = self._get_targets_or_warn()
        if not targets:
            return
        try:
            for bs_node, logical_index, target_name in targets:
                push_normals_deltas(bs_node, logical_index, factor, vtx_indices=vtx_indices)
            scope     = "all verts" if vtx_indices is None else f"{len(vtx_indices)} vtx"
            n_t       = len(targets)
            direction = "inward" if factor < 0 else "outward"
            self._set_status(
                f"Normal Push {n_t} target{'s' if n_t > 1 else ''}"
                f"  {direction} ×{abs(factor):.2f}  {scope}")
        except Exception as e:
            traceback.print_exc()
            self._set_status(f"✗ {e}", error=True)

    @undo_chunk
    def _run_multiply(self):
        if self._btn_normal_mode.isChecked():
            factor = self._mult_sign * self._parse_factor(self._mult_fields[0])
            self._run_push_normals_mode(factor)
        else:
            fx = self._mult_sign * self._parse_factor(self._mult_fields[0])
            fy = self._mult_sign * self._parse_factor(self._mult_fields[1])
            fz = self._mult_sign * self._parse_factor(self._mult_fields[2])
            self._run_multiply_factors(fx, fy, fz)

    @undo_chunk
    def _run_nullify(self):
        raw_sel = cmds.ls(sl=True, flatten=True) or []
        vtx_sel = [s for s in raw_sel if ".vtx[" in s]
        if not vtx_sel:
            targets = self._get_targets_or_warn()
            if not targets:
                return
            n = len(targets)
            names = ", ".join(t[2] for t in targets[:3])
            if n > 3:
                names += f" … (+{n - 3})"
            msg = QtWidgets.QMessageBox(self)
            msg.setIcon(QtWidgets.QMessageBox.Warning)
            msg.setWindowTitle("Nullify — No vertex selection")
            msg.setText(
                f"No vertices are selected.\n\n"
                f"This will wipe ALL deltas on {n} target{'s' if n > 1 else ''}:\n"
                f"{names}\n\n"
                f"Continue?")
            btn_continue = msg.addButton("Continue", QtWidgets.QMessageBox.AcceptRole)
            btn_cancel   = msg.addButton("Cancel",   QtWidgets.QMessageBox.RejectRole)
            msg.setDefaultButton(btn_cancel)
            msg.adjustSize()
            center = self.geometry().center()
            msg.move(center.x() - msg.width() // 2, center.y() - msg.height() // 2)
            msg.exec_()
            if msg.clickedButton() is not btn_continue:
                return
        self._run_multiply_factors(0.0, 0.0, 0.0)

    @undo_chunk
    def _run_invert_deltas(self):
        if self._btn_normal_mode.isChecked():
            self._run_push_normals_mode(-1.0)
        else:
            self._run_multiply_factors(-1.0, -1.0, -1.0)

    @undo_chunk
    def _run_delta_mult_shapes(self):
        targets = self._get_targets_or_warn()
        if not targets:
            return
        if len(targets) < 2:
            self._set_status("✗ Mult: select A (receiver) then B (source)", error=True)
            return

        raw_sel     = cmds.ls(sl=True, flatten=True) or []
        vtx_sel     = [s for s in raw_sel if ".vtx[" in s]
        vtx_indices = None if not vtx_sel else [int(s.split(".vtx[")[1].rstrip("]")) for s in vtx_sel]

        bs_node_a, idx_a, name_a = targets[0]
        bs_node_b, idx_b, name_b = targets[1]

        try:
            n = multiply_shapes_deltas(bs_node_a, idx_a, bs_node_b, idx_b,
                                       vtx_indices=vtx_indices)
            scope = "all delta verts" if vtx_indices is None else f"{len(vtx_indices)} vtx"
            self._set_status(f"✓ Mult {name_a} \u00d7 {name_b}  ({scope}, {n} verts)")
        except Exception as e:
            traceback.print_exc()
            self._set_status(f"✗ {e}", error=True)

    @undo_chunk
    def _run_push_normals_legacy(self):
        raw_sel   = cmds.ls(sl=True, flatten=True) or []
        vtx_sel   = [s for s in raw_sel if ".vtx[" in s]
        all_verts = not vtx_sel

        targets = self._get_targets_or_warn()
        if not targets:
            return

        factor = self._push_sign * self._parse_factor(self.field_push_factor)

        vtx_indices = None if all_verts else [int(s.split(".vtx[")[1].rstrip("]")) for s in vtx_sel]

        try:
            for bs_node, logical_index, target_name in targets:
                push_normals_deltas(bs_node, logical_index, factor,
                                    vtx_indices=vtx_indices)
            scope = "all verts" if all_verts else f"{len(vtx_indices)} vtx"
            n_t   = len(targets)
            direction = "inward" if factor < 0 else "outward"
            self._set_status(
                f"Normal Push {n_t} target{'s' if n_t > 1 else ''}"
                f"  {direction} \xd7{abs(factor)}  {scope}")
        except Exception as e:
            traceback.print_exc()
            self._set_status(f"✗ {e}", error=True)
    @undo_chunk
    def _run_smooth_deltas(self):
        raw_sel   = cmds.ls(sl=True, flatten=True) or []
        vtx_sel   = [s for s in raw_sel if ".vtx[" in s]
        all_verts = not vtx_sel

        targets = self._get_targets_or_warn()
        if not targets:
            return

        opacity = self.slider_smooth_opacity.value() / 100.0
        vtx_indices = None if all_verts else [int(s.split(".vtx[")[1].rstrip("]")) for s in vtx_sel]
        n_t = len(targets)

        try:
            self._progress_begin(n_t)
            for i, (bs_node, logical_index, target_name) in enumerate(targets):
                self._progress_step(i, f"Smoothing {target_name}…")
                smooth_target_deltas(bs_node, logical_index, opacity,
                                     vtx_indices=vtx_indices)
            n_passes = max(1, int(round(opacity * 10)))
            scope = "all verts" if all_verts else f"{len(vtx_indices)} vtx"
            self._set_status(
                f"Smooth Deltas {n_t} target{'s' if n_t > 1 else ''}"
                f"  {n_passes} pass{'es' if n_passes > 1 else ''}  {scope}")
        except Exception as e:
            traceback.print_exc()
            self._set_status(f"✗ {e}", error=True)
        finally:
            self._progress_end()

    @undo_chunk
    def _run_relax_deltas(self):
        raw_sel   = cmds.ls(sl=True, flatten=True) or []
        vtx_sel   = [s for s in raw_sel if ".vtx[" in s]
        all_verts = not vtx_sel

        targets = self._get_targets_or_warn()
        if not targets:
            return

        opacity = self.slider_smooth_opacity.value() / 100.0
        vtx_indices = None if all_verts else [int(s.split(".vtx[")[1].rstrip("]")) for s in vtx_sel]
        n_t = len(targets)

        try:
            self._progress_begin(n_t)
            for i, (bs_node, logical_index, target_name) in enumerate(targets):
                self._progress_step(i, f"Relaxing {target_name}…")
                relax_target_deltas(bs_node, logical_index, opacity,
                                    vtx_indices=vtx_indices)
            n_passes = max(1, int(round(opacity * 10)))
            scope = "all verts" if all_verts else f"{len(vtx_indices)} vtx"
            self._set_status(
                f"Relax Deltas {n_t} target{'s' if n_t > 1 else ''}"
                f"  {n_passes} pass{'es' if n_passes > 1 else ''}  {scope}")
        except Exception as e:
            traceback.print_exc()
            self._set_status(f"✗ {e}", error=True)
        finally:
            self._progress_end()

    @undo_chunk
    def _run_hammer_deltas(self):
        raw_sel = cmds.ls(sl=True, flatten=True) or []
        vtx_sel = [s for s in raw_sel if ".vtx[" in s]
        if not vtx_sel:
            self._set_status("✗ Hammer: select vertices first", error=True)
            return

        targets = self._get_targets_or_warn()
        if not targets:
            return

        opacity = self.slider_smooth_opacity.value() / 100.0
        vtx_indices = [int(s.split(".vtx[")[1].rstrip("]")) for s in vtx_sel]
        hammer_mode = self.combo_smooth_falloff.currentData()
        n_laplacian = int(self.spin_hammer_lap.text() or "0")
        n_t = len(targets)
        try:
            self._progress_begin(n_t)
            for i, (bs_node, logical_index, target_name) in enumerate(targets):
                self._progress_step(i, f"Hammering {target_name}…")
                hammer_target_deltas(bs_node, logical_index, vtx_indices,
                                     opacity=opacity, progress_cb=None,
                                     n_laplacian=n_laplacian, mode=hammer_mode)
            scope = f"{len(vtx_indices)} vtx"
            self._set_status(
                f"Hammer Deltas {n_t} target{'s' if n_t > 1 else ''}"
                f"  {scope}  ({opacity:.0%})")
        except Exception as e:
            traceback.print_exc()
            self._set_status(f"✗ {e}", error=True)
        finally:
            self._progress_end()

    @undo_chunk
    def _run_average_deltas(self):
        raw_sel = cmds.ls(sl=True, flatten=True) or []
        vtx_sel = [s for s in raw_sel if ".vtx[" in s]
        if not vtx_sel:
            self._set_status("✗ Average: select vertices first", error=True)
            return

        targets = self._get_targets_or_warn()
        if not targets:
            return

        opacity = self.slider_smooth_opacity.value() / 100.0
        hammer_mode = self.combo_smooth_falloff.currentData()
        vtx_indices = [int(s.split(".vtx[")[1].rstrip("]")) for s in vtx_sel]
        n_t = len(targets)
        try:
            for bs_node, logical_index, target_name in targets:
                average_target_deltas(bs_node, logical_index, vtx_indices,
                                      opacity=opacity, mode=hammer_mode)
            self._set_status(
                f"Average Deltas {n_t} target{'s' if n_t > 1 else ''}"
                f"  {len(vtx_indices)} vtx  (opacity {opacity:.2f})")
        except Exception as e:
            traceback.print_exc()
            self._set_status(f"✗ {e}", error=True)

    def _opp_axis_menu(self, pos):
        """Right-click context menu on the Create Opposite shelf button to pick the symmetry axis."""
        menu = QtWidgets.QMenu(self)
        for axis in ("Object X", "Object Y", "Object Z", "Topology"):
            act = menu.addAction(axis)
            act.setCheckable(True)
            act.setChecked(self._opp_axis == axis)
        chosen = menu.exec_(self.btn_create_opposite.mapToGlobal(pos))
        if chosen is None:
            return
        axis = chosen.text()
        if axis == "Topology":
            edges = cmds.filterExpand(cmds.ls(sl=True), selectionMask=32) or []
            if not edges:
                self._set_status("✗ Select a central edge in the viewport first.", error=True)
                return
            self._opp_topo_edge = edges[0]
            self._set_status(f"✓ Topology axis — edge: {edges[0]}")
        else:
            self._opp_topo_edge = None
        self._opp_axis = axis
        self.btn_create_opposite.setToolTip(
            "Create Opposite Target\n"
            "Duplicates the target, flips it and renames it with the opposite\n"
            "naming convention (L_/R_, lft/rgt, up/dn, fwd/bwd …).\n"
            "Right-click to choose the symmetry axis.\n"
            f"Current axis: {self._opp_axis}")

    @undo_chunk
    def _run_opposite(self):
        targets = get_selected_targets()
        if targets:
            blockers = find_blocking_regen_meshes(targets)
            if blockers and not self._confirm_delete_regen_meshes(blockers, "Create Opposite"):
                return
        try:
            create_opposite_shape(
                symmetry_axis=self._opp_axis,
                topo_edge=self._opp_topo_edge)
            self._set_status("✓ Opposite(s) created")
        except Exception as e:
            traceback.print_exc()
            self._set_status(f"✗ {e}", error=True)

    @undo_chunk
    def _run_clean_bs(self):
        try:
            # Collect bs nodes from Shape Editor selection; fall back to the current
            # bs node so the tool works even when no target is selectable (e.g. after
            # a .shp re-import that produced only phantom / unnamed slots).
            bs_nodes = {bs for bs, _, _ in get_selected_targets()}
            if not bs_nodes and self.bs_node:
                bs_nodes = {self.bs_node}
            if not bs_nodes:
                self._set_status("No blendShape node found. Get a BS node first.", error=True)
                return
            total = 0
            for bs_node in bs_nodes:
                total += purge_empty_bs_slots(bs_node)
            nodes = ", ".join(bs_nodes)
            if total:
                self._set_status(f"✓ Cleaned {total} slot(s) on: {nodes}")
            else:
                self._set_status(f"✓ No empty slots found on: {nodes}")
        except Exception as e:
            traceback.print_exc()
            self._set_status(f"✗ Clean BS: {e}", error=True)

    def _run_clean_deformed_mesh(self):
        """Remove residual sculpt color sets and bake non-deformer history on selected meshes."""
        import maya.mel as mel
        meshes = cmds.ls(sl=True, type="transform")
        if not meshes:
            self._set_status("Select at least one mesh first.", error=True)
            return
        try:
            for mesh in meshes:
                shapes = cmds.listRelatives(mesh, shapes=True, type="mesh") or []
                if not shapes:
                    continue
                # Delete named sculpt color sets
                for cs in ("SculptFreezeColorTemp", "SculptMaskColorTemp"):
                    existing = cmds.polyColorSet(mesh, query=True, allColorSets=True) or []
                    if cs in existing:
                        cmds.polyColorSet(mesh, delete=True, colorSet=cs)
                # Delete any remaining unnamed / leftover sets (up to 4 passes)
                for _ in range(4):
                    remaining = cmds.polyColorSet(mesh, query=True, allColorSets=True) or []
                    if not remaining:
                        break
                    try:
                        cmds.polyColorSet(mesh, delete=True,
                                          colorSet=remaining[0])
                    except Exception:
                        break
            # Bake non-deformer history on the whole selection
            mel.eval('doBakeNonDefHistory(1, {"prePost"})')
            names = ", ".join(meshes)
            self._set_status(f"Clean Deformed Mesh done on: {names}")
        except Exception as e:
            traceback.print_exc()
            self._set_status(f"Clean Deformed Mesh failed: {e}", error=True)

    @undo_chunk
    def _run_reset_all_weights(self):
        targets = self._get_targets_or_warn()
        if not targets:
            return
        try:
            seen = set()
            total = 0
            for bs_node, _, _ in targets:
                if bs_node in seen:
                    continue
                seen.add(bs_node)
                total += reset_all_target_weights(bs_node)
            nodes = ", ".join(seen)
            self._set_status(f"✓ Reset {total} weight(s) to 0 on: {nodes}")
        except Exception as e:
            traceback.print_exc()
            self._set_status(f"✗ Reset Weights: {e}", error=True)

    @undo_chunk
    def _run_apply_moves(self):
        try:
            targets = get_selected_targets()
            if not targets:
                self._set_status("Select exactly one target in the Shape Editor.", error=True)
                return
            if len(targets) > 1:
                self._set_status("Bake Moves works on 1 target only — select a single target.", error=True)
                return
            bs_node, logical_index, target_name = targets[0]
            base_mesh = get_base_mesh(bs_node)
            vtx_indices = _get_vertex_selection()
            n = apply_mesh_moves_to_target(bs_node, base_mesh, logical_index,
                                           vtx_indices=vtx_indices)
            vtx_suffix = f" on {len(vtx_indices)} vert(s)" if vtx_indices else ""
            self._set_status(f"✓ Bake Moves: {n} vertex move(s) added to '{target_name}'{vtx_suffix}")
        except Exception as e:
            traceback.print_exc()
            self._set_status(f"✗ Bake Moves: {e}", error=True)

    @undo_chunk
    def _run_bake_deformers(self):
        targets = self._get_targets_or_warn()
        if not targets:
            return
        try:
            vtx_indices = _get_vertex_selection()
            seen = {}
            for bs_node, logical_index, _ in targets:
                seen.setdefault(bs_node, []).append(logical_index)
            total = 0
            n_total = len(targets)
            done = 0
            self._progress_begin(n_total)
            for bs_node, indices in seen.items():
                base_mesh = get_base_mesh(bs_node)
                if not base_mesh:
                    done += len(indices)
                    continue
                for idx in indices:
                    name = cmds.aliasAttr(f"{bs_node}.w[{idx}]", q=True) or str(idx)
                    self._progress_step(done, f"Baking {name}…")
                    total += bake_deformers_to_targets(bs_node, base_mesh, [idx],
                                                       vtx_indices=vtx_indices)
                    done += 1
            vtx_suffix = f" on {len(vtx_indices)} vert(s)" if vtx_indices else ""
            self._set_status(f"✓ Bake Deformers: {total} target(s) baked{vtx_suffix}")
        except Exception as e:
            traceback.print_exc()
            self._set_status(f"✗ Bake Deformers: {e}", error=True)
        finally:
            self._progress_end()

    @undo_chunk
    # ── Edge Loop Split — Get handlers ────────────────────────────────────
    def _els_get_upper_vtx(self):
        sel = [s for s in (cmds.ls(sl=True, flatten=True) or []) if ".vtx[" in s]
        if not sel:
            self._set_status("✗ Edge Loop Split: select a vertex first", error=True)
            return
        self.edit_els_upper_vtx.setText(sel[0])
        self._set_status(f"✓ Upper vertex: {sel[0]}")

    def _els_get_lower_vtx(self):
        sel = [s for s in (cmds.ls(sl=True, flatten=True) or []) if ".vtx[" in s]
        if not sel:
            self._set_status("✗ Edge Loop Split: select a vertex first", error=True)
            return
        self.edit_els_lower_vtx.setText(sel[0])
        self._set_status(f"✓ Lower vertex: {sel[0]}")

    def _els_get_edges(self):
        edges = [s for s in (cmds.ls(sl=True, flatten=True) or []) if ".e[" in s]
        if not edges:
            self._set_status("✗ Edge Loop Split: select edges first", error=True)
            return
        self.edit_els_edges.setText(str(edges))
        self._set_status(f"✓ {len(edges)} edge(s) captured")

    def _run_edge_loop_split(self):
        """
        UI handler for Edge Loop Split.
        Reads Upper Vtx, Lower Vtx and Edge Loop from their dedicated fields.
        Processes all targets selected in the Shape Editor.
        """
        targets = self._get_targets_or_warn()
        if not targets:
            return

        upper_str = self.edit_els_upper_vtx.text().strip()
        lower_str = self.edit_els_lower_vtx.text().strip()
        edges_raw = self.edit_els_edges.text().strip()

        if not upper_str:
            self._set_status("✗ Edge Loop Split: Upper Vtx not set — use the Get button", error=True)
            return
        if not lower_str:
            self._set_status("✗ Edge Loop Split: Lower Vtx not set — use the Get button", error=True)
            return
        if not edges_raw:
            self._set_status("✗ Edge Loop Split: Edge Loop not set — use the Get button", error=True)
            return

        if ".vtx[" not in upper_str:
            self._set_status("✗ Edge Loop Split: Upper Vtx value is not a valid vertex", error=True)
            return
        if ".vtx[" not in lower_str:
            self._set_status("✗ Edge Loop Split: Lower Vtx value is not a valid vertex", error=True)
            return

        seed_upper = int(upper_str.split(".vtx[")[1].rstrip("]"))
        seed_lower = int(lower_str.split(".vtx[")[1].rstrip("]"))

        # Parse edges list — stored as Python list repr: "['mesh.e[0]', 'mesh.e[1]']"
        try:
            import ast
            edges = ast.literal_eval(edges_raw)
            if not isinstance(edges, list):
                raise ValueError("not a list")
        except Exception:
            self._set_status("✗ Edge Loop Split: Edge Loop field is malformed — re-capture with Get", error=True)
            return

        seam_edges = set()
        seam_vis   = set()
        for e in edges:
            info = cmds.polyInfo(e, edgeToVertex=True)
            if info:
                parts = info[0].split()
                a, b  = int(parts[2]), int(parts[3])
                seam_vis.add(a);  seam_vis.add(b)
                seam_edges.add(frozenset({a, b}))

        if not seam_edges:
            self._set_status("✗ Edge Loop Split: no valid edges found — re-capture with Get", error=True)
            return

        if seed_upper in seam_vis:
            self._set_status("✗ Edge Loop Split: Upper vertex is on the seam — pick one clearly above", error=True)
            return
        if seed_lower in seam_vis:
            self._set_status("✗ Edge Loop Split: Lower vertex is on the seam — pick one clearly below", error=True)
            return

        radius     = (max(1, int(float(self.spin_radius.text()))) if self.chk_radius.isChecked() else 1)
        curve_name = self.combo_curve.currentText()
        falloff_fn = CURVE_FUNCTIONS.get(curve_name, linear)

        bs_states = {}
        try:
            # Disconnect driven attrs and zero all weights on each involved bs_node
            for bs_node in {t[0] for t in targets}:
                bs_states[bs_node] = zero_all_bs_weights(bs_node)

            done = []
            for bs_node, logical_index, target_name in targets:
                edge_loop_split_target(
                    bs_node, logical_index, target_name,
                    seam_edges, seed_upper, seed_lower,
                    falloff_radius=radius, falloff_func=falloff_fn)
                done.append(target_name)

            names_str = ", ".join(f"'{n}'" for n in done)
            self._set_status(
                f"✓ Edge Loop Split : {names_str}"
                f"  (radius={radius}, curve={curve_name}, seam={len(seam_edges)} edges)")
        except Exception as e:
            import traceback
            traceback.print_exc()
            self._set_status(f"✗ Edge Loop Split: {e}", error=True)
        finally:
            for _bs, _state in bs_states.items():
                restore_all_bs_weights(_bs, _state)
    def _run_add_target(self):
        targets = get_selected_targets()
        if not targets:
            self._set_status("Select at least one target in the Shape Editor.", error=True)
            return
        seen = set()
        added = []
        try:
            for bs_node, _, _ in targets:
                if bs_node in seen:
                    continue
                seen.add(bs_node)
                _, name = add_empty_target(bs_node)
                added.append(f"{bs_node} ->{name}")
            self._set_status(f"Added: {', '.join(added)}")
        except Exception as e:
            self._set_status(f"✗ Add Target: {e}", error=True)

    def _show_add_target_context_menu(self, pos):
        menu = QtWidgets.QMenu(self)
        menu.setToolTipsVisible(True)

        act_empty = QtGui.QAction("Add Empty Target", menu)
        act_empty.setToolTip(
            "Adds a new empty (zero-delta) target to the blendshape node(s)\n"
            "of the target(s) selected in the Shape Editor.\n"
            "Enters sculpt mode automatically.")
        act_empty.triggered.connect(self._run_add_target)
        menu.addAction(act_empty)

        menu.addSeparator()

        act_new = QtGui.QAction("Add Selection as New Target", menu)
        act_new.setToolTip(
            "Selection: source mesh(es) first, target mesh last.\n"
            "Adds each source directly as a blendshape target in rest pose (no inversion).\n"
            "Target name = source mesh name + _mprt.")
        act_new.triggered.connect(self._run_add_selection_as_target)
        menu.addAction(act_new)

        act_corrective = QtGui.QAction("Add Selection as New Corrective Target", menu)
        act_corrective.setToolTip(
            "Selection: corrective mesh(es) first, target mesh last.\n"
            "Inverts the deformation stack via invertShape() to produce\n"
            "a rest-pose corrective target from a posed sculpt.\n"
            "Target name = corrective mesh name + _mprt.")
        act_corrective.triggered.connect(self._run_create_corrective)
        menu.addAction(act_corrective)

        menu.addSeparator()

        act_delete = QtGui.QAction("Delete source mesh after import", menu)
        act_delete.setToolTip(
            "If checked, deletes the source/corrective mesh(es) from the scene\n"
            "after they have been imported as blendshape targets.")
        act_delete.setCheckable(True)
        act_delete.setChecked(self._corrective_delete_mesh)
        act_delete.toggled.connect(lambda v: setattr(self, "_corrective_delete_mesh", v))
        menu.addAction(act_delete)

        menu.exec_(self.btn_add_target.mapToGlobal(pos))

    def _run_add_selection_as_target(self):
        sel = cmds.ls(sl=True, long=True)
        if len(sel) < 2:
            self._set_status(
                "Select source mesh(es) then target mesh (last) in viewport.", error=True)
            return
        source_meshes = sel[:-1]
        target_mesh   = sel[-1]
        try:
            bs_node, results = add_mesh_as_target(
                source_meshes, target_mesh,
                delete_source=self._corrective_delete_mesh)
            names = [n for _, n in results]
            self._set_status(f"✓ {len(names)} target(s) added to {bs_node}: {', '.join(names)}")
        except Exception as e:
            traceback.print_exc()
            self._set_status(f"✗ Add Target: {e}", error=True)

    def _run_create_corrective(self):
        sel = cmds.ls(sl=True, long=True)
        if len(sel) < 2:
            self._set_status(
                "Select corrective mesh(es) then target mesh (last) in viewport.", error=True)
            return
        corrective_meshes = sel[:-1]
        target_mesh       = sel[-1]
        try:
            bs_node, results = create_corrective_shape(
                corrective_meshes, target_mesh,
                delete_corrective=self._corrective_delete_mesh)
            names = [n for _, n in results]
            self._set_status(f"✓ {len(names)} posed target(s) added to {bs_node}: {', '.join(names)}")
        except Exception as e:
            traceback.print_exc()
            self._set_status(f"✗ Create Posed Target: {e}", error=True)

    @staticmethod
    def _selected_vtx_indices(base_mesh):
        """
        Returns a list of vertex indices currently selected on base_mesh,
        or [] if no vertex components are selected on that mesh.
        Works with both short and long path names.
        """
        short = base_mesh.split("|")[-1]
        result = []
        for comp in (cmds.ls(selection=True, flatten=True) or []):
            obj, sep, rest = comp.partition(".vtx[")
            if sep and obj.split("|")[-1] == short:
                result.append(int(rest.rstrip("]")))
        return result

    # ── Copy / Paste Delta ────────────────────────────────────────────────────

    def _run_copy_delta(self):
        """
        Copies the delta (dx, dy, dz) of a SINGLE selected vertex on the active
        blendShape target — same paradigm as Maya's Copy Skin Weights.

        Workflow:
          1. Exactly 1 vertex must be selected in the viewport.
          2. Exactly 1 target must be selected in the Shape Editor.
          3. The delta of that vertex is stored in self._delta_clipboard.
          4. Paste button is enabled.

        self._delta_clipboard = {
            "target" : str,          # alias name — display only
            "vi"     : int,          # source vertex index
            "dx"     : float,
            "dy"     : float,
            "dz"     : float,
        }
        """
        # ── 1. Resolve vertex selection ───────────────────────────────────────
        raw_sel = cmds.ls(sl=True, flatten=True) or []
        vtx_sel = [s for s in raw_sel if ".vtx[" in s]
        if len(vtx_sel) != 1:
            self._set_status(
                f"✗ Copy Delta: select exactly 1 vertex "
                f"({'none' if not vtx_sel else len(vtx_sel)} selected)",
                error=True)
            return

        vtx_str = vtx_sel[0]   # e.g. "pSphere1.vtx[42]"
        vi = int(vtx_str.split(".vtx[")[1].rstrip("]"))

        # ── 2. Resolve active target ──────────────────────────────────────────
        targets = self._get_targets_or_warn()
        if not targets:
            return
        bs_node, logical_index, target_name = targets[0]
        if len(targets) > 1:
            self._set_status(
                f"Copy Delta: using first target '{target_name}' "
                f"(ignoring {len(targets)-1} other{'s' if len(targets)>2 else ''})")

        # ── 3. Read delta for that vertex ─────────────────────────────────────
        deltas = get_target_deltas(bs_node, logical_index)
        dx, dy, dz = deltas.get(vi, (0.0, 0.0, 0.0))

        self._delta_clipboard = {
            "target" : target_name,
            "vi"     : vi,
            "dx"     : dx,
            "dy"     : dy,
            "dz"     : dz,
        }
        self.btn_paste_delta.setEnabled(True)
        self._set_status(
            f"✓ Copied vtx[{vi}] from '{target_name}'  —  "
            f"Δ({dx:.4f}, {dy:.4f}, {dz:.4f})")

    @undo_chunk
    def _run_paste_delta(self):
        """
        Pastes the clipboard delta (dx, dy, dz) onto every selected vertex,
        on the active blendShape target — same paradigm as Maya's Paste Skin Weights.

        Workflow:
          1. One or more vertices must be selected in the viewport.
          2. Exactly 1 target must be selected in the Shape Editor.
          3. The target is regenerated, the delta is written onto each selected
             vertex, then the regen mesh is deleted to bake back into the slot.

        This is undoable as a single chunk.
        """
        if not hasattr(self, "_delta_clipboard") or not self._delta_clipboard:
            self._set_status("✗ Nothing in clipboard — run Copy Delta first", error=True)
            return

        # ── 1. Resolve vertex selection ───────────────────────────────────────
        raw_sel = cmds.ls(sl=True, flatten=True) or []
        vtx_sel = [s for s in raw_sel if ".vtx[" in s]
        if not vtx_sel:
            self._set_status("✗ Paste Delta: no vertices selected", error=True)
            return

        # ── 2. Resolve active target ──────────────────────────────────────────
        targets = self._get_targets_or_warn()
        if not targets:
            return
        bs_node, logical_index, target_name = targets[0]
        if len(targets) > 1:
            self._set_status(
                f"Paste Delta: using first target '{target_name}' "
                f"(ignoring {len(targets)-1} other{'s' if len(targets)>2 else ''})")

        src_name = self._delta_clipboard["target"]
        dx       = self._delta_clipboard["dx"]
        dy       = self._delta_clipboard["dy"]
        dz       = self._delta_clipboard["dz"]

        # ── 3. Write delta onto selected vertices ─────────────────────────────
        try:
            saved      = _save_shape_editor_selection()
            regen_mesh = cmds.sculptTarget(
                bs_node, e=True, target=logical_index, regenerate=True)
            regen_mesh = regen_mesh if isinstance(regen_mesh, str) else regen_mesh[0]

            for vtx_str in vtx_sel:
                vi = int(vtx_str.split(".vtx[")[1].rstrip("]"))
                cmds.setAttr(f"{regen_mesh}.pnts[{vi}].pntx", dx)
                cmds.setAttr(f"{regen_mesh}.pnts[{vi}].pnty", dy)
                cmds.setAttr(f"{regen_mesh}.pnts[{vi}].pntz", dz)

            cmds.delete(regen_mesh)
            _restore_shape_editor_selection(saved)

            self._set_status(
                f"✓ Pasted Δ({dx:.4f}, {dy:.4f}, {dz:.4f}) from '{src_name}' "
                f"onto {len(vtx_sel)} vtx ->'{target_name}'")

        except Exception as e:
            traceback.print_exc()
            self._set_status(f"✗ {e}", error=True)

    def _run_select_delta_vertices(self):
        targets = self._get_targets_or_warn()
        if not targets:
            return
        bs_node, logical_index, target_name = targets[0]
        try:
            count = select_delta_vertices(bs_node, logical_index)
            if count == 0:
                self._set_status(f"No deltas on '{target_name}'", error=True)
            else:
                self._set_status(f"✓ {count} delta vert{'s' if count > 1 else ''} selected on '{target_name}'")
        except Exception as e:
            traceback.print_exc()
            self._set_status(f"✗ {e}", error=True)

    @undo_chunk
    def _run_prune_deltas(self):
        targets = self._get_targets_or_warn()
        if not targets:
            return
        tolerance = float(self.spin_prune_tol.text() or "0.001")
        try:
            total = 0
            for bs_node, logical_index, target_name in targets:
                count = prune_small_deltas(bs_node, logical_index, tolerance)
                total += count
                print(f"  Prune '{target_name}': {count} vert(s) zeroed (tol={tolerance})")
            if total == 0:
                self._set_status(f"No deltas below tolerance {tolerance}")
            else:
                self._set_status(f"✓ {total} delta vert{'s' if total > 1 else ''} pruned (tol={tolerance})")
        except Exception as e:
            traceback.print_exc()
            self._set_status(f"✗ {e}", error=True)

    @undo_chunk
    def _run_delta_joint(self):
        targets = self._get_targets_or_warn()
        if not targets:
            return

        neutral = self.chk_delta_neutral.isChecked()
        multi   = self.chk_delta_multi.isChecked() and len(targets) > 1

        try:
            if multi:
                combined_mags, n_verts = self._combine_target_magnitudes(targets)
                combined_name = "_".join(n for _, _, n in targets)
                bs0, idx0, _ = targets[0]
                create_delta_joint(bs0, idx0, combined_name,
                                   neutral=neutral,
                                   _precomputed=(combined_mags, n_verts))
                self._set_status(f"✓ combined joint group created ({len(targets)} targets)")
            else:
                for bs_node, logical_index, target_name in targets:
                    create_delta_joint(bs_node, logical_index, target_name, neutral=neutral)
                self._set_status(f"✓ {len(targets)} joint group{'s' if len(targets) > 1 else ''} created")
        except Exception as e:
            traceback.print_exc()
            self._set_status(f"✗ {e}", error=True)

    @undo_chunk
    def _run_delta_cluster(self):
        targets = self._get_targets_or_warn()
        if not targets:
            return

        neutral = self.chk_delta_neutral.isChecked()
        multi   = self.chk_delta_multi.isChecked() and len(targets) > 1

        try:
            if multi:
                combined_mags, n_verts = self._combine_target_magnitudes(targets)
                combined_name = "_".join(n for _, _, n in targets)
                bs0, idx0, _ = targets[0]
                create_delta_cluster(bs0, idx0, combined_name,
                                     neutral=neutral,
                                     _precomputed=(combined_mags, n_verts))
                self._set_status(f"✓ combined cluster created ({len(targets)} targets)")
            else:
                for bs_node, logical_index, target_name in targets:
                    create_delta_cluster(bs_node, logical_index, target_name, neutral=neutral)
                self._set_status(f"✓ {len(targets)} cluster{'s' if len(targets) > 1 else ''} created")
        except Exception as e:
            traceback.print_exc()
            self._set_status(f"✗ {e}", error=True)

    def _combine_target_magnitudes(self, targets):
        """
        Reads delta magnitudes from each target (via regen→read→delete) and
        returns their per-vertex sum as (combined_magnitudes_dict, n_verts).
        """
        combined = {}
        n_verts  = 0
        self._progress_begin(len(targets))
        try:
            for i, (bs_node, logical_index, name) in enumerate(targets):
                self._progress_step(i, f"Reading {name}…")
                mags, nv = _collect_magnitudes(bs_node, logical_index)
                n_verts  = nv
                for vi, mag in mags.items():
                    combined[vi] = combined.get(vi, 0.0) + mag
        finally:
            self._progress_end()
        return combined, n_verts

    @undo_chunk
    def _run_wrap_extract(self):
        sel    = cmds.ls(sl=True, type='transform') or []
        meshes = [s for s in sel if cmds.listRelatives(s, shapes=True, type='mesh')]

        if len(meshes) < 2:
            self._set_status(
                "✗ Wrap Extract: select master mesh + at least 1 receiver mesh in viewport",
                error=True)
            return

        master_mesh     = meshes[0]
        receiver_meshes = meshes[1:]

        # ── Resolve BS node and targets ────────────────────────────────────
        ui_targets = get_selected_targets()
        auto_mode  = not ui_targets  # True ->wrap all targets, then prune near-zero

        if ui_targets:
            bs_nodes = list({t[0] for t in ui_targets})
            if len(bs_nodes) > 1:
                self._set_status(
                    "✗ Wrap Extract: all selected targets must be on the same blendShape node",
                    error=True)
                return
            bs_node   = bs_nodes[0]
            base_mesh = get_base_mesh(bs_node)
            targets   = ui_targets
        else:
            # Auto mode: use the BS on the master mesh
            bs_node = _find_blendshape_on_mesh(master_mesh)
            if not bs_node:
                self._set_status(
                    f"✗ Wrap Extract: no blendShape found on master mesh '{master_mesh}'",
                    error=True)
                return
            base_mesh = get_base_mesh(bs_node)
            if not base_mesh:
                self._set_status(
                    f"✗ Wrap Extract: cannot find base mesh for '{bs_node}'", error=True)
                return
            indices = cmds.getAttr(f"{bs_node}.w", multiIndices=True) or []
            targets = []
            for idx in indices:
                alias = cmds.aliasAttr(f"{bs_node}.w[{idx}]", q=True)
                if alias:
                    targets.append((bs_node, idx, alias))
            if not targets:
                self._set_status(
                    f"✗ Wrap Extract: no targets found on '{bs_node}'", error=True)
                return

        if not base_mesh:
            self._set_status(
                f"✗ Wrap Extract: cannot find base mesh for '{bs_node}'", error=True)
            return

        # ── Confirm receivers that have no BS node ────────────────────────
        for receiver in receiver_meshes:
            if _find_blendshape_on_mesh(receiver) is None:
                mesh_short = receiver.split(":")[-1].split("|")[-1]
                bs_name    = f"{mesh_short}_bs"
                msg = QtWidgets.QMessageBox(self)
                msg.setWindowTitle("Wrap Extract — No BlendShape")
                msg.setText(
                    f"'{receiver}' has no blendShape node.\n\n"
                    f"A new node '{bs_name}' will be created automatically.")
                btn_ok = msg.addButton("Create BS and Wrap", QtWidgets.QMessageBox.AcceptRole)
                msg.addButton("Cancel", QtWidgets.QMessageBox.RejectRole)
                msg.exec()
                if msg.clickedButton() != btn_ok:
                    return

        # ── Extract onto each receiver ────────────────────────────────────
        total_kept   = 0
        total_pruned = 0
        receiver_reports = []
        n_receivers = len(receiver_meshes)
        cmds.progressWindow(title="Wrap Extract", minValue=0, maxValue=n_receivers,
                            progress=0, isInterruptable=False, status="Please wait…")
        for r_idx, receiver in enumerate(receiver_meshes):
            cmds.progressWindow(e=True, progress=r_idx,
                                status=f"Extracting onto {receiver.split('|')[-1]}…")
            try:
                bs_target, log = extract_targets_via_wrap(
                    bs_node, base_mesh, receiver, targets,
                    overwrite=self.chk_wrap_overwrite.isChecked())

                # Prune near-zero targets in auto mode
                pruned = 0
                if auto_mode:
                    for _orig, actual_name, _ in log:
                        idx = get_bs_weight_attribute_logical_index(bs_target, actual_name)
                        if idx is None:
                            continue
                        deltas = get_target_deltas(bs_target, idx)
                        if not any(
                            (dx*dx + dy*dy + dz*dz) >= 1e-6
                            for dx, dy, dz in deltas.values()
                        ):
                            mel.eval(f"blendShapeDeleteTargetGroup {bs_target} {idx};")
                            pruned += 1

                kept = len(log) - pruned
                total_kept   += kept
                total_pruned += pruned

                if self.chk_connect_targets.isChecked():
                    # Connect source weight to destination weight — handles renamed targets
                    for orig_name, actual_name, _ in log:
                        src_attr = f"{bs_node}.{orig_name}"
                        dst_attr = f"{bs_target}.{actual_name}"
                        if cmds.objExists(src_attr) and cmds.objExists(dst_attr):
                            cmds.connectAttr(src_attr, dst_attr, force=True)

                rec_short = receiver.split(":")[-1].split("|")[-1]
                n_replaced = sum(1 for *_, r in log if r)
                n_added    = kept - n_replaced
                detail = f"{rec_short}: {kept} target(s)"
                if n_added and n_replaced:
                    detail += f" ({n_added} added, {n_replaced} replaced)"
                elif n_replaced:
                    detail += f" ({n_replaced} replaced)"
                receiver_reports.append(detail)

            except Exception as e:
                cmds.progressWindow(endProgress=True)
                traceback.print_exc()
                self._set_status(f"✗ Wrap Extract ({receiver}): {e}", error=True)
                return

        cmds.progressWindow(endProgress=True)
        parts = [f"✓ Wrap Extract: {', '.join(receiver_reports)}"]
        if total_pruned:
            parts.append(f"{total_pruned} pruned (near-zero)")
        if self.chk_connect_targets.isChecked():
            parts.append("connected")
        self._set_status("  ".join(parts))

    @undo_chunk
    def _run_extract_only(self):
        targets = self._get_targets_or_warn()
        if not targets:
            return

        bs_nodes = list({t[0] for t in targets})
        if len(bs_nodes) > 1:
            self._set_status(
                "✗ Extract Only: all selected targets must be on the same blendShape node",
                error=True)
            return

        bs_node = bs_nodes[0]

        sel    = cmds.ls(sl=True, type='transform') or []
        meshes = [s for s in sel if cmds.listRelatives(s, shapes=True, type='mesh')]
        if not meshes:
            self._set_status(
                "✗ Extract Only: select a mesh in the scene to extract onto", error=True)
            return
        if len(meshes) > 1:
            self._set_status(
                "✗ Extract Only: select only one mesh", error=True)
            return

        mesh_target = meshes[0]
        try:
            grp, extracted = extract_targets_only(bs_node, mesh_target, targets)
            n_total = len(extracted)
            self._set_status(
                f"✓ Extract Only: {n_total} shape{'s' if n_total > 1 else ''} ->'{grp}'")
        except Exception as e:
            traceback.print_exc()
            self._set_status(f"✗ Extract Only: {e}", error=True)

    @undo_chunk
    def _run_connect_targets_A_to_B(self):
        sel    = cmds.ls(sl=True, type='transform') or []
        meshes = [s for s in sel if cmds.listRelatives(s, shapes=True, type='mesh')]
        if len(meshes) < 2:
            self._set_status(
                "✗ Connect A→B: select source mesh first, then target mesh", error=True)
            return
        if len(meshes) > 2:
            self._set_status(
                "✗ Connect A→B: select exactly two meshes (source, then target)", error=True)
            return

        mesh_A, mesh_B = meshes[0], meshes[1]
        try:
            bs_A, bs_B, connected = connect_targets_A_to_B(mesh_A, mesh_B)
            n = len(connected)
            if n == 0:
                self._set_status(
                    f"✗ Connect A→B: no matching target names between '{bs_A}' and '{bs_B}'",
                    error=True)
            else:
                self._set_status(
                    f"✓ Connect A→B: {n} target{'s' if n > 1 else ''} connected"
                    f"  ({bs_A}  -> {bs_B})")
        except Exception as e:
            traceback.print_exc()
            self._set_status(f"✗ Connect A→B: {e}", error=True)

# ─────────────────────────────────────────────────────────────────────────────
# LAUNCH
# ─────────────────────────────────────────────────────────────────────────────

def check_compatibility():
    """
    Verifies that all dependencies required by the Blendshape Editor Tool
    are available in the current Maya environment.

    Checks:
      - Maya version  (minimum 2022 required for PySide6 / shiboken6)
      - PySide6 + shiboken6
      - maya.api.OpenMaya  (used for fast vertex color writes)
      - colorsys            (Python stdlib — should always be present)
      - Key Maya commands   (sculptTarget, blendShape, polyColorPerVertex,
                             polyEvaluate, spaceLocator, xform, getAttr, setAttr)
      - MEL: getShapeEditorTreeviewSelection  (Shape Editor integration)

    Returns True if all checks pass, False otherwise.
    A QMessageBox is shown listing any failures before the tool opens.
    """
    errors   = []
    warnings = []

    # ── Maya version ──────────────────────────────────────────────────────────
    try:
        maya_version = int(cmds.about(version=True).split()[0])
        if maya_version < 2022:
            errors.append(
                f"Maya {maya_version} detected — Maya 2022+ required "
                f"(PySide6 / shiboken6 are not available in earlier versions)."
            )
    except Exception as e:
        warnings.append(f"Could not determine Maya version: {e}")

    # ── PySide6 ───────────────────────────────────────────────────────────────
    try:
        from PySide6 import QtWidgets as _qtw, QtCore as _qtc, QtGui as _qtg
    except ImportError:
        errors.append(
            "PySide6 not found. "
            "Install it or use a Maya version that bundles PySide6 (2022+)."
        )

    # ── shiboken6 ─────────────────────────────────────────────────────────────
    try:
        from shiboken6 import wrapInstance as _wi
    except ImportError:
        errors.append(
            "shiboken6 not found. "
            "Required to embed the tool inside Maya's main window."
        )

    # ── maya.api.OpenMaya ─────────────────────────────────────────────────────
    try:
        from maya.api import OpenMaya as _om
        _ = _om.MFnMesh   # verify the class we actually use is accessible
    except Exception as e:
        errors.append(f"maya.api.OpenMaya unavailable: {e}")

    # ── colorsys (Python stdlib) ───────────────────────────────────────────────
    try:
        import colorsys as _cs
        _cs.hls_to_rgb(0.5, 0.5, 1.0)
    except Exception as e:
        errors.append(f"colorsys module unavailable: {e}")

    # ── Maya commands ─────────────────────────────────────────────────────────
    required_cmds = [
        ("sculptTarget",        "blendShape target extraction (Delta View / Split)"),
        ("blendShape",          "blendShape node creation and query"),
        ("polyColorPerVertex",  "vertex color display (Delta View)"),
        ("polyEvaluate",        "vertex count query"),
        ("spaceLocator",        "locator creation"),
        ("xform",               "transform queries"),
        ("getAttr",             "attribute reading"),
        ("setAttr",             "attribute writing"),
    ]
    for cmd_name, usage in required_cmds:
        if not hasattr(cmds, cmd_name):
            errors.append(f"Maya command '{cmd_name}' not found — used for: {usage}.")

    # ── MEL: Shape Editor integration ─────────────────────────────────────────
    try:
        mel.eval('exists("getShapeEditorTreeviewSelection")')
    except Exception as e:
        warnings.append(
            f"MEL proc 'getShapeEditorTreeviewSelection' unavailable: {e}. "
            f"Shape Editor sync may not work."
        )

    # ── Report ────────────────────────────────────────────────────────────────
    if not errors and not warnings:
        return True

    lines = []
    if errors:
        lines.append("<b>ERRORS — tool cannot run:</b>")
        for e in errors:
            lines.append(f"&nbsp;&nbsp;• {e}")
    if warnings:
        if lines:
            lines.append("")
        lines.append("<b>WARNINGS — some features may not work:</b>")
        for w in warnings:
            lines.append(f"&nbsp;&nbsp;• {w}")

    msg = QtWidgets.QMessageBox()
    msg.setWindowTitle("Blendshape Editor Tool — Compatibility Check")
    msg.setIcon(QtWidgets.QMessageBox.Critical if errors else QtWidgets.QMessageBox.Warning)
    msg.setText("<br>".join(lines))
    msg.setStandardButtons(QtWidgets.QMessageBox.Ok)
    msg.exec()

    return len(errors) == 0   # warnings don't block launch, errors do


_win = None


def show():
    global _win

    if not check_compatibility():
        return

    WS_CTRL = BlendshapeEditorUI.TOOL_NAME + "WorkspaceControl"

    if cmds.workspaceControl(WS_CTRL, q=True, exists=True):
        if _win is None:
            # Module was reloaded — stale workspaceControl, must recreate
            try:
                cmds.deleteUI(WS_CTRL)
            except Exception:
                pass
            try:
                cmds.workspaceControlState(WS_CTRL, remove=True)
            except Exception:
                pass
        else:
            # UI already exists (open or retained/closed) — just raise it
            # retain=True means Maya remembers position automatically
            cmds.workspaceControl(WS_CTRL, e=True, restore=True)
            return

    _win = BlendshapeEditorUI()
    _win.show(dockable=True, floating=True, retain=True)