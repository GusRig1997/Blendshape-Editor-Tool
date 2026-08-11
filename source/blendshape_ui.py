from maya import cmds, mel
import traceback
from maya.app.general.mayaMixin import MayaQWidgetDockableMixin

from PySide6 import QtWidgets, QtCore, QtGui

from blendshape_core import *
from blendshape_core import (_save_shape_editor_selection, _restore_shape_editor_selection,
                             _collect_magnitudes, _find_blendshape_on_mesh)

import json, os


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
            print(f"  ✓ Opposite created : {opposite_shape}")

            # Mirror driver connection — only when freshly created
            if was_fresh:
                src_weight = f"{bs_name}.weight[{index}]"
                drivers = cmds.listConnections(src_weight, source=True, plugs=True, d=False) or []
                for driver_plug in drivers:
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
                    print(f"  ✓ Driver mirrored : {opp_plug} → {new_weight}")

        except Exception:
            # Clean up the _Copy slot so it doesn't become a phantom target
            if dup_idx is not None:
                try:
                    mel.eval(f"blendShapeDeleteTargetGroup {bs_name} {dup_idx};")
                    print(f"  ✗ Cleaned up temp slot [{dup_idx}] after error on '{shape}'")
                except Exception:
                    pass
            raise


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
                renamed.append(f"{current} → {clean_proposed}")
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
            "but are in the wrong order (e.g. L_brow_a_up → L_brow_up_a, M_ → C_).\n"
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
        existing = {}  # alias → logical_index
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

        # Build token lookup: frozenset(tokens) → [json_names]
        # Normalize M → C when tokenizing
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

            # Pass 2 — missing side prefix (e.g. jaw_up → C_jaw_up)
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
      idx 2  ( 0,  0, smooth) — U/V locked, tangent editable → controls undershoot
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
        # smooth / auto → Catmull-Rom
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
    _COL_INMIN = 4
    _COL_INMAX = 5
    _COL_GATE  = 6
    _COL_STAT  = 7

    _ATTR_ITEMS = ["tx", "ty", "tz", "rx", "ry", "rz", "sx", "sy", "sz"]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Rig Connector")
        self.setMinimumSize(900, 600)
        self.setWindowFlags(self.windowFlags() | QtCore.Qt.Window)
        self._shapes = []   # list of shape name strings currently in table
        self._build_ui()
        self._populate_from_default_json()

    # ── UI build ──────────────────────────────────────────────────────────────

    def _build_ui(self):
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(6)

        # ── Toolbar ───────────────────────────────────────────────────────────
        toolbar = QtWidgets.QHBoxLayout()
        toolbar.setSpacing(4)

        toolbar.addWidget(QtWidgets.QLabel("BS Node:"))
        self._le_bs_node = QtWidgets.QLineEdit()
        self._le_bs_node.setReadOnly(True)
        self._le_bs_node.setFixedWidth(160)
        self._le_bs_node.setPlaceholderText("blendShape node")
        toolbar.addWidget(self._le_bs_node)
        btn_get_bs = QtWidgets.QPushButton("Get")
        btn_get_bs.setFixedWidth(40)
        btn_get_bs.setToolTip("Grab the blendShape node from the current selection")
        btn_get_bs.clicked.connect(self._get_bs_node)
        toolbar.addWidget(btn_get_bs)

        toolbar.addSpacing(16)

        toolbar.addWidget(QtWidgets.QLabel("JSON:"))
        self._le_json = QtWidgets.QLineEdit()
        self._le_json.setReadOnly(True)
        self._le_json.setPlaceholderText("shapes JSON path")
        toolbar.addWidget(self._le_json)
        btn_load_json = QtWidgets.QPushButton("Load")
        btn_load_json.setFixedWidth(40)
        btn_load_json.setToolTip("Load a JSON file to populate the Shape column")
        btn_load_json.clicked.connect(self._load_json_file)
        toolbar.addWidget(btn_load_json)

        outer.addLayout(toolbar)

        # ── Search ────────────────────────────────────────────────────────────
        search_row = QtWidgets.QHBoxLayout()
        search_row.setSpacing(4)
        search_row.addWidget(QtWidgets.QLabel("Filter:"))
        self._le_search = QtWidgets.QLineEdit()
        self._le_search.setPlaceholderText("Search shapes…")
        self._le_search.setClearButtonEnabled(True)
        self._le_search.textChanged.connect(self._filter_rows)
        search_row.addWidget(self._le_search)
        outer.addLayout(search_row)

        # ── Table ─────────────────────────────────────────────────────────────
        self._table = QtWidgets.QTableWidget(0, 8)
        self._table.setHorizontalHeaderLabels(
            ["#", "Shape", "Controller", "Attr",
             "In Min", "In Max", "Combo Driver", "Status"])
        self._table.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._show_row_context_menu)
        self._table.verticalHeader().setVisible(False)
        self._table.setAlternatingRowColors(True)
        self._table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self._table.verticalHeader().setDefaultSectionSize(24)

        hh = self._table.horizontalHeader()
        hh.setSectionResizeMode(self._COL_NUM,   QtWidgets.QHeaderView.Fixed)
        hh.setSectionResizeMode(self._COL_SHAPE, QtWidgets.QHeaderView.Stretch)
        hh.setSectionResizeMode(self._COL_CTRL,  QtWidgets.QHeaderView.Fixed)
        hh.setSectionResizeMode(self._COL_ATTR,  QtWidgets.QHeaderView.Fixed)
        hh.setSectionResizeMode(self._COL_INMIN, QtWidgets.QHeaderView.Fixed)
        hh.setSectionResizeMode(self._COL_INMAX, QtWidgets.QHeaderView.Fixed)
        hh.setSectionResizeMode(self._COL_GATE,  QtWidgets.QHeaderView.Fixed)
        hh.setSectionResizeMode(self._COL_STAT,  QtWidgets.QHeaderView.Fixed)

        self._table.setColumnWidth(self._COL_NUM,   28)
        self._table.setColumnWidth(self._COL_CTRL,  140)
        self._table.setColumnWidth(self._COL_ATTR,  90)
        self._table.setColumnWidth(self._COL_INMIN, 65)
        self._table.setColumnWidth(self._COL_INMAX, 65)
        self._table.setColumnWidth(self._COL_GATE,  100)
        self._table.setColumnWidth(self._COL_STAT,  50)

        outer.addWidget(self._table)

        # ── Auto-stagger ──────────────────────────────────────────────────────
        stagger_box = QtWidgets.QVBoxLayout()
        stagger_box.setSpacing(3)

        stagger_row1 = QtWidgets.QHBoxLayout()
        stagger_row1.setSpacing(4)
        stagger_row1.addWidget(QtWidgets.QLabel("Auto-stagger:"))

        self._le_stagger_ctrl = QtWidgets.QLineEdit()
        self._le_stagger_ctrl.setPlaceholderText("controller")
        self._le_stagger_ctrl.setFixedWidth(140)
        stagger_row1.addWidget(self._le_stagger_ctrl)

        self._combo_stagger_axis = QtWidgets.QComboBox()
        self._combo_stagger_axis.setEditable(True)
        self._combo_stagger_axis.addItems(self._ATTR_ITEMS)
        self._combo_stagger_axis.setCurrentText("tx")
        self._combo_stagger_axis.setFixedWidth(75)
        stagger_row1.addWidget(self._combo_stagger_axis)

        stagger_row1.addWidget(QtWidgets.QLabel("Mode:"))
        self._combo_stagger_mode = QtWidgets.QComboBox()
        self._combo_stagger_mode.addItems(["Linear", "Mirror", "Symmetric"])
        self._combo_stagger_mode.setFixedWidth(90)
        self._combo_stagger_mode.setToolTip(
            "Linear   : sequential slots [0→1] for each shape.\n"
            "Mirror   : centre shape activates last, outer shapes first.\n"
            "           Same direction for all. Ideal for zip lips.\n"
            "Symmetric: outer shapes activate last, centre shape first.\n"
            "           Left half and right half get opposite directions.\n"
            "           Ideal for brows and cheekbones.")
        self._combo_stagger_mode.currentTextChanged.connect(self._on_stagger_mode_changed)
        stagger_row1.addWidget(self._combo_stagger_mode)

        self._chk_stagger_sign = QtWidgets.QCheckBox("+/\u2212")
        self._chk_stagger_sign.setEnabled(False)
        self._chk_stagger_sign.setToolTip(
            "Symmetric only — controls which side is positive.\n"
            "Unchecked: left half → '\u2212', right half → '+'.\n"
            "Checked:   left half → '+', right half → '\u2212'.")
        stagger_row1.addWidget(self._chk_stagger_sign)

        self._chk_stagger_proxies = QtWidgets.QCheckBox("Proxies")
        self._chk_stagger_proxies.setChecked(True)
        self._chk_stagger_proxies.setToolTip(
            "ON: creates a new proxy row (sub-driver) for each selected row,\n"
            "    driven by the stagger controller with the computed In Min / In Max.\n"
            "OFF: applies the controller, axis, In Min and In Max directly\n"
            "    to the selected rows, without adding proxy rows.")
        stagger_row1.addWidget(self._chk_stagger_proxies)
        stagger_row1.addStretch()
        stagger_box.addLayout(stagger_row1)

        stagger_row2 = QtWidgets.QHBoxLayout()
        stagger_row2.setSpacing(4)

        stagger_row2.addWidget(QtWidgets.QLabel("In Max:"))
        self._sb_stagger_inmax = QtWidgets.QDoubleSpinBox()
        self._sb_stagger_inmax.setRange(0.0, 9999.0)
        self._sb_stagger_inmax.setValue(0.0)
        self._sb_stagger_inmax.setDecimals(3)
        self._sb_stagger_inmax.setFixedWidth(70)
        self._sb_stagger_inmax.setLocale(QtCore.QLocale(QtCore.QLocale.English))
        stagger_row2.addWidget(self._sb_stagger_inmax)

        stagger_row2.addWidget(QtWidgets.QLabel("Smooth:"))
        self._sb_stagger_falloff = QtWidgets.QDoubleSpinBox()
        self._sb_stagger_falloff.setRange(0.0, 9999.0)
        self._sb_stagger_falloff.setValue(0.0)
        self._sb_stagger_falloff.setDecimals(3)
        self._sb_stagger_falloff.setFixedWidth(70)
        self._sb_stagger_falloff.setLocale(QtCore.QLocale(QtCore.QLocale.English))
        self._sb_stagger_falloff.setToolTip(
            "Overlap amount added on each side of a shape's activation slot.\n"
            "Example: slot [0.2, 0.4] with smooth 0.01 → In Min=0.19, In Max=0.41.\n"
            "First shape never goes below 0; last shape never exceeds In Max.")
        stagger_row2.addWidget(self._sb_stagger_falloff)

        btn_stagger = QtWidgets.QPushButton("Create / Set Stagger")
        btn_stagger.setToolTip(
            "Apply stagger In Min / In Max to the selected rows.\n"
            "Proxies ON: creates a proxy sub-row per shape driven by the stagger controller.\n"
            "Proxies OFF: writes the values directly onto the selected rows.\n"
            "Each shape gets a sequential activation slot within [0, In Max].\n"
            "Smooth extends each slot by ±smooth (clamped to bounds).\n"
            "Mirror/Symmetric: outer shapes share slot 0, centre shape activates last.")
        btn_stagger.clicked.connect(self._apply_stagger)
        stagger_row2.addWidget(btn_stagger)
        stagger_row2.addStretch()
        stagger_box.addLayout(stagger_row2)

        outer.addLayout(stagger_box)

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
            sb_body.setVisible(_sb_open[0])
            sb_header.setText(("▼  Soft Blend Pairs") if _sb_open[0]
                               else ("▶  Soft Blend Pairs"))

        sb_header.setText("▶  Soft Blend Pairs")
        sb_header.clicked.connect(_toggle_sb)

        outer.addWidget(sb_header)
        outer.addWidget(sb_body)

        # ── Table (left) + Graph (right) side by side ────────────────────────
        tbl_graph_row = QtWidgets.QHBoxLayout()
        tbl_graph_row.setSpacing(6)

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
        tbl_graph_row.addWidget(self._tbl_pairs, stretch=7)

        self._graph = _SoftBlendGraphWidget()
        self._graph.keys_changed.connect(self._on_graph_keys_changed)
        tbl_graph_row.addWidget(self._graph, stretch=3)

        lay_sb.addLayout(tbl_graph_row)

        # ── Bottom row: Add Pair | Reset Curve | U | V | Tangent ─────────────
        bottom_row = QtWidgets.QHBoxLayout()
        bottom_row.setSpacing(4)

        btn_add_pair = QtWidgets.QPushButton("+ Add Pair")
        btn_add_pair.setFixedWidth(80)
        btn_add_pair.setToolTip(
            "Define a Soft Blend pair: two opposite shapes (e.g. mouth_lft / mouth_rgt)\n"
            "will be driven by an animCurveUU with smooth tangents at neutral\n"
            "instead of the standard linear norm/clamp network.")
        btn_add_pair.clicked.connect(self._add_soft_blend_pair)
        bottom_row.addWidget(btn_add_pair)

        bottom_row.addStretch()

        btn_reset_curve = QtWidgets.QPushButton("Reset Curve")
        btn_reset_curve.setFixedWidth(80)
        btn_reset_curve.setToolTip("Reset the soft blend curve to the default 5-key preset.")
        btn_reset_curve.clicked.connect(self._reset_soft_blend_curve)
        bottom_row.addWidget(btn_reset_curve)

        bottom_row.addSpacing(8)
        bottom_row.addWidget(QtWidgets.QLabel("U:"))
        self._sb_key_u = QtWidgets.QDoubleSpinBox()
        self._sb_key_u.setRange(-1.0, 1.0)
        self._sb_key_u.setDecimals(3)
        self._sb_key_u.setSingleStep(0.01)
        self._sb_key_u.setFixedWidth(65)
        self._sb_key_u.setLocale(QtCore.QLocale(QtCore.QLocale.English))
        self._sb_key_u.valueChanged.connect(self._on_key_u_changed)
        bottom_row.addWidget(self._sb_key_u)

        bottom_row.addWidget(QtWidgets.QLabel("V:"))
        self._sb_key_v = QtWidgets.QDoubleSpinBox()
        self._sb_key_v.setRange(-1.0, 1.0)
        self._sb_key_v.setDecimals(3)
        self._sb_key_v.setSingleStep(0.01)
        self._sb_key_v.setFixedWidth(65)
        self._sb_key_v.setLocale(QtCore.QLocale(QtCore.QLocale.English))
        self._sb_key_v.valueChanged.connect(self._on_key_v_changed)
        bottom_row.addWidget(self._sb_key_v)

        bottom_row.addWidget(QtWidgets.QLabel("Tangent:"))
        self._combo_key_tang = QtWidgets.QComboBox()
        self._combo_key_tang.addItems(["smooth", "auto", "linear"])
        self._combo_key_tang.setFixedWidth(76)
        self._combo_key_tang.currentTextChanged.connect(self._on_key_tangent_changed)
        bottom_row.addWidget(self._combo_key_tang)

        lay_sb.addLayout(bottom_row)

        self._updating_controls = False
        self._refresh_key_controls()

        # ── Button bar ────────────────────────────────────────────────────────
        btn_bar = QtWidgets.QHBoxLayout()
        btn_bar.setSpacing(4)

        btn_autofill = QtWidgets.QPushButton("Auto-fill")
        btn_autofill.setToolTip(
            "Auto-populate the Controller and Attr columns based on naming conventions")
        btn_autofill.clicked.connect(self._auto_fill)
        btn_bar.addWidget(btn_autofill)

        btn_add = QtWidgets.QPushButton("Add Row")
        btn_add.setToolTip("Add one row per target selected in the Shape Editor.\n"
                           "Falls back to one empty row if nothing is selected.")
        btn_add.clicked.connect(self._add_row)
        btn_bar.addWidget(btn_add)

        btn_remove = QtWidgets.QPushButton("Remove Row")
        btn_remove.setToolTip("Delete the selected rows from the table")
        btn_remove.clicked.connect(self._remove_rows)
        btn_bar.addWidget(btn_remove)

        btn_opp = QtWidgets.QPushButton("Create Opposite")
        btn_opp.setToolTip(
            "Duplicate the selected row with opposite names:\n"
            "swaps L/R (and other symmetric tokens) in the Shape and Controller fields.\n"
            "The new row is inserted directly below the source row.")
        btn_opp.clicked.connect(self._create_opposite_row)
        btn_bar.addWidget(btn_opp)

        btn_up = QtWidgets.QPushButton("\u2191")
        btn_up.setFixedWidth(26)
        btn_up.setToolTip("Move selected rows up by one position")
        btn_up.clicked.connect(self._move_up)
        btn_bar.addWidget(btn_up)

        btn_dn = QtWidgets.QPushButton("\u2193")
        btn_dn.setFixedWidth(26)
        btn_dn.setToolTip("Move selected rows down by one position")
        btn_dn.clicked.connect(self._move_down)
        btn_bar.addWidget(btn_dn)

        btn_bar.addStretch()

        btn_load = QtWidgets.QPushButton("Load Mapping")
        btn_load.setToolTip("Load a previously saved table mapping from a JSON file")
        btn_load.clicked.connect(self._load_mapping)
        btn_bar.addWidget(btn_load)

        btn_save = QtWidgets.QPushButton("Save Mapping")
        btn_save.setToolTip("Save the current table mapping to a JSON file")
        btn_save.clicked.connect(self._save_mapping)
        btn_bar.addWidget(btn_save)

        btn_bar.addSpacing(8)

        btn_disc_sel = QtWidgets.QPushButton("Disconnect Selected")
        btn_disc_sel.setToolTip(
            "Remove the rig network for the selected shapes (utility nodes deleted,\n"
            "blendShape weight disconnected). Does not delete the shapes themselves.")
        btn_disc_sel.clicked.connect(self._disconnect_selected)
        btn_bar.addWidget(btn_disc_sel)

        btn_disc_all = QtWidgets.QPushButton("Disconnect All")
        btn_disc_all.setToolTip(
            "Remove the rig network for all shapes in the table (utility nodes deleted,\n"
            "blendShape weights disconnected). Does not delete the shapes themselves.")
        btn_disc_all.clicked.connect(self._disconnect_all)
        btn_bar.addWidget(btn_disc_all)

        btn_bar.addSpacing(16)

        self._btn_build = QtWidgets.QPushButton("Build && Connect ▸")
        self._btn_build.setToolTip(
            "Build the rig network for all valid rows:\n"
            "  • Creates offset / normalize / clamp utility nodes per shape\n"
            "  • Applies transform limits and locks unused axes on each controller\n"
            "  • Optionally generates scale shapes (if Auto Scale Shapes is checked)")
        self._btn_build.setEnabled(False)
        self._btn_build.clicked.connect(self._on_build_connect)
        btn_bar.addWidget(self._btn_build)

        outer.addLayout(btn_bar)

    # ── Populate ──────────────────────────────────────────────────────────────

    def _populate_from_default_json(self):
        path = _check_shapes_default_json_path()
        if os.path.exists(path):
            self._le_json.setText(path)
            self._load_shapes_from_json(path)

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

    def _append_table_row(self, row_num, shape_name, controllers,
                          ctrl="", attr="ty",
                          in_min=0.0, in_max=1.0, gate=""):
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
        cb_ctrl = QtWidgets.QComboBox()
        cb_ctrl.setEditable(True)
        cb_ctrl.addItem("")
        cb_ctrl.addItems(controllers)
        if ctrl:
            idx = cb_ctrl.findText(ctrl)
            if idx >= 0:
                cb_ctrl.setCurrentIndex(idx)
            else:
                cb_ctrl.setEditText(ctrl)
        self._table.setCellWidget(row, self._COL_CTRL, cb_ctrl)

        # Col 3 — Attr (editable combo: pick standard or type custom)
        cb_attr = QtWidgets.QComboBox()
        cb_attr.setEditable(True)
        cb_attr.addItems(self._ATTR_ITEMS)
        cb_attr.setCurrentText(attr)
        cb_attr.setToolTip("Standard attribute or type a custom attribute name directly.")
        self._table.setCellWidget(row, self._COL_ATTR, cb_attr)

        # Col 4 — In Min
        sb_inmin = QtWidgets.QDoubleSpinBox()
        sb_inmin.setRange(-9999.0, 9999.0)
        sb_inmin.setSingleStep(0.1)
        sb_inmin.setDecimals(3)
        sb_inmin.setValue(in_min)
        sb_inmin.setLocale(QtCore.QLocale(QtCore.QLocale.English))
        self._table.setCellWidget(row, self._COL_INMIN, sb_inmin)

        # Col 5 — In Max (negative = negative direction)
        sb_inmax = QtWidgets.QDoubleSpinBox()
        sb_inmax.setRange(-9999.0, 9999.0)
        sb_inmax.setSingleStep(0.1)
        sb_inmax.setDecimals(3)
        sb_inmax.setValue(in_max)
        sb_inmax.setLocale(QtCore.QLocale(QtCore.QLocale.English))
        sb_inmax.setToolTip("Positive = shape activates as controller goes positive.\n"
                            "Negative = shape activates as controller goes negative.")
        self._table.setCellWidget(row, self._COL_INMAX, sb_inmax)

        # Col 8 — Cond.
        le_gate = QtWidgets.QLineEdit()
        le_gate.setPlaceholderText("shape name")
        le_gate.setText(gate)
        le_gate.setToolTip("Combo driver(s): one or more entries, comma-separated.\n"
                           "Each entry multiplies the shape weight in series.\n"
                           "  - blendShape target name   → jaw_dn\n"
                           "  - node.attr plug           → FKJaw_ctrl.zip\n"
                           "  - rev: prefix (inverted)   → rev:FKJaw_ctrl.retain\n"
                           "The shape only activates when all combo drivers are active.")
        self._table.setCellWidget(row, self._COL_GATE, le_gate)

        # Col 9 — Status
        lbl_status = QtWidgets.QLabel("●")
        lbl_status.setAlignment(QtCore.Qt.AlignCenter)
        lbl_status.setStyleSheet("color: grey;")
        self._table.setCellWidget(row, self._COL_STAT, lbl_status)

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

            # Map direction token → attr name + sign
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
            sb_inmax = self._table.cellWidget(row, self._COL_INMAX)
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
            if sb_inmax and negative:
                sb_inmax.setValue(-abs(sb_inmax.value()))

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

    def _add_row(self):
        controllers = self._scene_controllers()
        sel = self._shape_editor_selection()
        if sel:
            for name in sel:
                row_num = self._table.rowCount() + 1
                self._append_table_row(row_num, name, controllers)
        else:
            row_num = self._table.rowCount() + 1
            self._append_table_row(row_num, "", controllers)

    def _remove_rows(self):
        selected = sorted(
            set(idx.row() for idx in self._table.selectedIndexes()), reverse=True)
        for r in selected:
            self._table.removeRow(r)
        self._renumber_rows()

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
        sb_inmin   = self._table.cellWidget(r, self._COL_INMIN)
        sb_inmax   = self._table.cellWidget(r, self._COL_INMAX)
        le_gate    = self._table.cellWidget(r, self._COL_GATE)
        lbl_stat   = self._table.cellWidget(r, self._COL_STAT)
        is_proxy   = bool(num_item and num_item.text() == "\u21b3")
        return {
            "is_proxy":   is_proxy,
            "shape":      shape_item.text()      if shape_item else "",
            "ctrl":       cb_ctrl.currentText()  if cb_ctrl    else "",
            "attr":       cb_attr.currentText()  if cb_attr    else "ty",
            "in_min":     sb_inmin.value()       if sb_inmin   else 0.0,
            "in_max":     sb_inmax.value()       if sb_inmax   else 1.0,
            "gate":       le_gate.text().strip() if le_gate    else "",
            "stat_text":  lbl_stat.text()        if lbl_stat   else "\u25cf",
            "stat_style": lbl_stat.styleSheet()  if lbl_stat   else "color: grey;",
            "stat_tip":   lbl_stat.toolTip()     if lbl_stat   else "",
        }

    def _insert_row_data_at(self, pos, d):
        """Insert a fully populated row at position pos from a snapshot dict."""
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
        cb_ctrl = QtWidgets.QComboBox()
        cb_ctrl.setEditable(True)
        cb_ctrl.addItem("")
        cb_ctrl.addItems(controllers)
        if d["ctrl"]:
            idx = cb_ctrl.findText(d["ctrl"])
            cb_ctrl.setCurrentIndex(idx) if idx >= 0 else cb_ctrl.setEditText(d["ctrl"])
        self._table.setCellWidget(pos, self._COL_CTRL, cb_ctrl)

        # Col 3 — Attr (editable combo)
        cb_attr = QtWidgets.QComboBox()
        cb_attr.setEditable(True)
        cb_attr.addItems(self._ATTR_ITEMS)
        cb_attr.setCurrentText(d["attr"])
        cb_attr.setToolTip("Standard attribute or type a custom attribute name directly.")
        self._table.setCellWidget(pos, self._COL_ATTR, cb_attr)

        # Col 4 — In Min
        sb_inmin = QtWidgets.QDoubleSpinBox()
        sb_inmin.setRange(-9999.0, 9999.0)
        sb_inmin.setSingleStep(0.1)
        sb_inmin.setDecimals(3)
        sb_inmin.setValue(d["in_min"])
        sb_inmin.setLocale(QtCore.QLocale(QtCore.QLocale.English))
        self._table.setCellWidget(pos, self._COL_INMIN, sb_inmin)

        # Col 5 — In Max (negative = negative direction)
        sb_inmax = QtWidgets.QDoubleSpinBox()
        sb_inmax.setRange(-9999.0, 9999.0)
        sb_inmax.setSingleStep(0.1)
        sb_inmax.setDecimals(3)
        sb_inmax.setValue(d["in_max"])
        sb_inmax.setLocale(QtCore.QLocale(QtCore.QLocale.English))
        self._table.setCellWidget(pos, self._COL_INMAX, sb_inmax)

        # Col 8 — Cond.
        le_gate = QtWidgets.QLineEdit()
        le_gate.setPlaceholderText("shape name")
        le_gate.setText(d.get("gate", ""))
        le_gate.setToolTip("Combo driver(s): one or more entries, comma-separated.\n"
                           "Each entry multiplies the shape weight in series.\n"
                           "  - blendShape target name   → jaw_dn\n"
                           "  - node.attr plug           → FKJaw_ctrl.zip\n"
                           "  - rev: prefix (inverted)   → rev:FKJaw_ctrl.retain\n"
                           "The shape only activates when all combo drivers are active.")
        self._table.setCellWidget(pos, self._COL_GATE, le_gate)

        # Col 9 — Status
        lbl_stat = QtWidgets.QLabel(d["stat_text"])
        lbl_stat.setAlignment(QtCore.Qt.AlignCenter)
        lbl_stat.setStyleSheet(d["stat_style"])
        lbl_stat.setToolTip(d["stat_tip"])
        self._table.setCellWidget(pos, self._COL_STAT, lbl_stat)

    def _move_block(self, src_rows, target_row):
        """Move src_rows (sorted list of row indices) to before target_row."""
        if not src_rows:
            return
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

        # Restore selection on moved rows
        self._table.clearSelection()
        for i in range(len(snapshots)):
            self._table.selectRow(adj_target + i)

    def _create_opposite_row(self):
        sel_rows = sorted(set(idx.row() for idx in self._table.selectedIndexes()))
        if not sel_rows:
            return
        # Process in reverse so inserted rows don't shift subsequent indices
        for src in reversed(sel_rows):
            snap = self._snapshot_row(src)
            opp_shape = _swap_opposite_name(snap["shape"]) or snap["shape"]
            opp_ctrl  = _swap_opposite_name(snap["ctrl"])  or snap["ctrl"]
            opp_gate  = _swap_opposite_name(snap["gate"])  or snap["gate"]
            new_snap = dict(snap,
                            shape=opp_shape,
                            ctrl=opp_ctrl,
                            gate=opp_gate,
                            stat_text="\u25cf",
                            stat_style="color: grey;",
                            stat_tip="")
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
            sb_inmin = self._table.cellWidget(r, self._COL_INMIN)
            sb_inmax = self._table.cellWidget(r, self._COL_INMAX)
            le_gate  = self._table.cellWidget(r, self._COL_GATE)
            rows.append({
                "shape":      shape,
                "proxy":      is_proxy,
                "controller": cb_ctrl.currentText() if cb_ctrl  else "",
                "attr":       cb_attr.currentText() if cb_attr  else "ty",
                "in_min":     sb_inmin.value()      if sb_inmin else 0.0,
                "in_max":     sb_inmax.value()      if sb_inmax else 1.0,
                "gate":       le_gate.text().strip() if le_gate  else "",
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
        for r in range(self._tbl_pairs.rowCount()):
            if self._tbl_pairs.cellWidget(r, 2) is btn:
                self._tbl_pairs.removeRow(r)
                break

    def _add_soft_blend_pair(self):
        shapes = []
        for r in range(self._table.rowCount()):
            item = self._table.item(r, self._COL_SHAPE)
            if item and item.text():
                shapes.append(item.text())
        dlg = _AddSoftBlendPairDialog(shapes, self)
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
        default = _rig_mapping_prefs_path()
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Save Mapping", default, "JSON files (*.json)")
        if not path:
            return
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

    def _load_mapping(self):
        default = _rig_mapping_prefs_path()
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Load Mapping", default, "JSON files (*.json)")
        if not path or not os.path.exists(path):
            return
        try:
            with open(path, "r") as f:
                data = json.load(f)
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "Load Error", str(e))
            return

        # Support both old format (list) and new format (dict with "connections" key)
        soft_blend_pairs = []
        if isinstance(data, dict):
            soft_blend_pairs = data.get("soft_blend_pairs", [])
            data = data.get("connections", [])

        # Split primary and proxy rows
        primary_data = [rd for rd in data if isinstance(rd, dict) and not rd.get("proxy", False)]
        proxy_data   = [rd for rd in data if isinstance(rd, dict) and rd.get("proxy", False)]

        # by_shape for primary rows only (no duplicate keys)
        by_shape    = {rd["shape"]: rd for rd in primary_data}
        controllers = self._scene_controllers()

        def _resolve_rd(rd):
            """Normalise a row dict — handles old JSON format (direction + custom_attr)."""
            attr    = rd.get("attr", "ty")
            in_min  = float(rd.get("in_min", 0.0))
            in_max  = float(rd.get("in_max", 1.0))
            # Old format: attr=="custom" → use custom_attr value
            custom_attr = rd.get("custom_attr", "")
            if attr == "custom" and custom_attr:
                attr = custom_attr
            # Old format: direction=="−" → negate in_max (and in_min if nonzero)
            if rd.get("direction", "+") == "\u2212":
                in_max = -abs(in_max)
                if in_min != 0.0:
                    in_min = -abs(in_min)
            return attr, in_min, in_max

        def _apply_row_data(r, rd):
            cb_ctrl  = self._table.cellWidget(r, self._COL_CTRL)
            cb_attr  = self._table.cellWidget(r, self._COL_ATTR)
            sb_inmin = self._table.cellWidget(r, self._COL_INMIN)
            sb_inmax = self._table.cellWidget(r, self._COL_INMAX)
            le_gate  = self._table.cellWidget(r, self._COL_GATE)
            attr, in_min, in_max = _resolve_rd(rd)
            if cb_ctrl:
                idx = cb_ctrl.findText(rd.get("controller", ""))
                if idx >= 0:
                    cb_ctrl.setCurrentIndex(idx)
                else:
                    cb_ctrl.setEditText(rd.get("controller", ""))
            if cb_attr:
                cb_attr.setCurrentText(attr)
            if sb_inmin:
                sb_inmin.setValue(in_min)
            if sb_inmax:
                sb_inmax.setValue(in_max)
            if le_gate:
                le_gate.setText(rd.get("gate", ""))

        # ── Pass 1: primary rows ──────────────────────────────────────────────
        existing_shapes = set()
        for r in range(self._table.rowCount()):
            num_item   = self._table.item(r, self._COL_NUM)
            shape_item = self._table.item(r, self._COL_SHAPE)
            if not shape_item:
                continue
            if num_item and num_item.text() == "\u21b3":
                continue  # existing proxy row, handled below
            sname = shape_item.text()
            existing_shapes.add(sname)
            if sname in by_shape:
                _apply_row_data(r, by_shape[sname])

        # Add primary shapes not yet in the table
        row_num = self._table.rowCount()
        for rd in primary_data:
            sname = rd.get("shape", "")
            if sname and sname not in existing_shapes:
                row_num += 1
                attr, in_min, in_max = _resolve_rd(rd)
                self._append_table_row(
                    row_num, sname, controllers,
                    ctrl=rd.get("controller", ""),
                    attr=attr,
                    in_min=in_min,
                    in_max=in_max,
                    gate=rd.get("gate", ""),
                )

        # ── Pass 2: proxy rows ────────────────────────────────────────────────
        for rd in proxy_data:
            shape        = rd.get("shape", "")
            proxy_ctrl   = rd.get("controller", "")
            if not shape:
                continue

            # Check if this proxy row already exists (same shape + same ctrl)
            already_exists = False
            for check_r in range(self._table.rowCount()):
                n_item = self._table.item(check_r, self._COL_NUM)
                s_item = self._table.item(check_r, self._COL_SHAPE)
                cb_chk = self._table.cellWidget(check_r, self._COL_CTRL)
                if (n_item and n_item.text() == "\u21b3"
                        and s_item and s_item.text() == shape
                        and cb_chk and cb_chk.currentText() == proxy_ctrl):
                    already_exists = True
                    break
            if already_exists:
                continue

            # Find the last row for this shape (primary or proxy) to insert after
            last_row = -1
            for check_r in range(self._table.rowCount()):
                s_item = self._table.item(check_r, self._COL_SHAPE)
                if s_item and s_item.text() == shape:
                    last_row = check_r
            if last_row < 0:
                continue

            self._add_proxy_row(last_row, ctrl=proxy_ctrl)
            new_row = last_row + 1
            _apply_row_data(new_row, rd)

        # ── Soft blend pairs ──────────────────────────────────────────────────
        self._tbl_pairs.setRowCount(0)
        for pair in soft_blend_pairs:
            if len(pair) == 2:
                self._insert_pair_row(pair[0], pair[1])

        # ── Soft blend curve ──────────────────────────────────────────────────
        import copy
        curve = data.get("soft_blend_curve") if isinstance(raw, dict) else None
        if curve:
            self._graph.set_keys(curve)
        else:
            self._graph.set_keys(copy.deepcopy(_SoftBlendGraphWidget.DEFAULT_KEYS))
        self._refresh_key_controls()

    # ── Search / filter ───────────────────────────────────────────────────────

    def _filter_rows(self, text):
        text = text.strip().lower()
        for row in range(self._table.rowCount()):
            item = self._table.item(row, self._COL_SHAPE)
            shape_name = item.text().lower() if item else ""
            self._table.setRowHidden(row, bool(text) and text not in shape_name)

    # ── Context menu ──────────────────────────────────────────────────────────

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
        sb_inmax = self._table.cellWidget(source_row, self._COL_INMAX)
        attr   = cb_attr.currentText()  if cb_attr  else "ty"
        in_max = in_max_override if in_max_override is not None else (sb_inmax.value() if sb_inmax else 1.0)

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

        cb_ctrl_new = QtWidgets.QComboBox()
        cb_ctrl_new.setEditable(True)
        cb_ctrl_new.addItem("")
        cb_ctrl_new.addItems(controllers)
        if ctrl:
            cb_ctrl_new.setCurrentText(ctrl)
        self._table.setCellWidget(insert_row, self._COL_CTRL, cb_ctrl_new)

        cb_attr_new = QtWidgets.QComboBox()
        cb_attr_new.setEditable(True)
        cb_attr_new.addItems(self._ATTR_ITEMS)
        cb_attr_new.setCurrentText(attr)
        cb_attr_new.setToolTip("Standard attribute or type a custom attribute name directly.")
        self._table.setCellWidget(insert_row, self._COL_ATTR, cb_attr_new)

        sb_inmin_new = QtWidgets.QDoubleSpinBox()
        sb_inmin_new.setRange(-9999.0, 9999.0)
        sb_inmin_new.setSingleStep(0.1)
        sb_inmin_new.setDecimals(3)
        sb_inmin_new.setValue(0.0)
        sb_inmin_new.setLocale(QtCore.QLocale(QtCore.QLocale.English))
        self._table.setCellWidget(insert_row, self._COL_INMIN, sb_inmin_new)

        sb_inmax_new = QtWidgets.QDoubleSpinBox()
        sb_inmax_new.setRange(-9999.0, 9999.0)
        sb_inmax_new.setSingleStep(0.1)
        sb_inmax_new.setDecimals(3)
        sb_inmax_new.setValue(in_max)
        sb_inmax_new.setLocale(QtCore.QLocale(QtCore.QLocale.English))
        sb_inmax_new.setToolTip("Positive = shape activates as controller goes positive.\n"
                                "Negative = shape activates as controller goes negative.")
        self._table.setCellWidget(insert_row, self._COL_INMAX, sb_inmax_new)

        le_gate_new = QtWidgets.QLineEdit()
        le_gate_new.setPlaceholderText("shape name")
        le_gate_new.setToolTip("Combo driver(s): one or more entries, comma-separated.\n"
                               "Each entry multiplies the shape weight in series.\n"
                               "  - blendShape target name   → jaw_dn\n"
                               "  - node.attr plug           → FKJaw_ctrl.zip\n"
                               "  - rev: prefix (inverted)   → rev:FKJaw_ctrl.retain\n"
                               "The shape only activates when all combo drivers are active.")
        self._table.setCellWidget(insert_row, self._COL_GATE, le_gate_new)

        lbl_status = QtWidgets.QLabel("●")
        lbl_status.setAlignment(QtCore.Qt.AlignCenter)
        lbl_status.setStyleSheet("color: grey;")
        self._table.setCellWidget(insert_row, self._COL_STAT, lbl_status)

    # ── Auto-stagger ──────────────────────────────────────────────────────────

    def _on_stagger_mode_changed(self, text):
        self._chk_stagger_sign.setEnabled(text == "Symmetric")

    def _apply_stagger(self):
        """Apply stagger In Min / In Max to the selected primary rows.

        Linear    : shape k gets slot [k/N, (k+1)/N] × in_max_ref.
        Mirror    : centre peak — centre shape activates last, outer shapes first.
                    All shapes same direction. Ideal for zip lips.
        Symmetric : outer peak — outer shapes activate last, centre shape first.
                    Left half gets negative In Max, right half positive (or reversed via +/−).
                    Ideal for brows and cheekbones.
        Smooth    : each slot extended by ±smooth (clamped to [0, in_max_ref]).
        Proxies ON : creates / updates a proxy sub-row per shape.
        Proxies OFF: writes values directly on the selected rows.
        """
        master_ctrl   = self._le_stagger_ctrl.text().strip()
        axis          = self._combo_stagger_axis.currentText()
        in_max_ref    = self._sb_stagger_inmax.value()
        smooth        = self._sb_stagger_falloff.value()
        mode          = self._combo_stagger_mode.currentText()   # "Linear"/"Mirror"/"Symmetric"
        use_mirror    = (mode == "Mirror")
        use_symmetric = (mode == "Symmetric")
        positive_left = self._chk_stagger_sign.isChecked()
        use_proxies   = self._chk_stagger_proxies.isChecked()

        if use_proxies and not master_ctrl:
            QtWidgets.QMessageBox.warning(
                self, "Auto-stagger", "Enter the stagger controller name.")
            return

        # Primary rows only, table order preserved
        selected = sorted(set(idx.row() for idx in self._table.selectedIndexes()))
        primary_selected = [r for r in selected
                            if not (self._table.item(r, self._COL_NUM) and
                                    self._table.item(r, self._COL_NUM).text() == "\u21b3")]
        if not primary_selected:
            QtWidgets.QMessageBox.information(
                self, "Auto-stagger", "Select rows in the table first.")
            return

        n = len(primary_selected)

        # ── Slot formula ──────────────────────────────────────────────────────
        if use_mirror:
            # Centre shape gets highest slot; outer shapes share lower slots (zip lips)
            num_slots  = (n + 1) // 2
            slot_width = in_max_ref / num_slots if num_slots > 0 else 0.0
            def _slot(k):
                dist = abs(k - (n - 1) / 2.0)
                return int((n - 1) / 2.0 - dist + 1e-9)
        elif use_symmetric:
            # Outer shapes get highest slot; centre shape gets lowest slot (brows/cheekbones)
            num_slots  = (n + 1) // 2
            slot_width = in_max_ref / num_slots if num_slots > 0 else 0.0
            def _slot(k):
                return int(abs(k - (n - 1) / 2.0) + 1e-9)
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

            # Compute signed in_min / in_max (Symmetric → negative side gets negated values)
            base_min = max(0.0, slot * slot_width - smooth)
            base_max = min(in_max_ref, (slot + 1) * slot_width + smooth)
            if direction == "\u2212":
                in_min_val = -base_min
                in_max_val = -base_max
            else:
                in_min_val = base_min
                in_max_val = base_max

            if use_proxies:
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
            sb_inmin_w = self._table.cellWidget(target_row, self._COL_INMIN)
            sb_inmax_w = self._table.cellWidget(target_row, self._COL_INMAX)
            if cb_attr_w:
                cb_attr_w.setCurrentText(axis)
            if sb_inmin_w:
                sb_inmin_w.setValue(in_min_val)
            if sb_inmax_w:
                sb_inmax_w.setValue(in_max_val)

    # ── Build & Connect ───────────────────────────────────────────────────────

    @undo_chunk
    def _on_build_connect(self):
        bs_node = self._le_bs_node.text().strip()
        if not bs_node or not cmds.objExists(bs_node):
            QtWidgets.QMessageBox.warning(
                self, "Build & Connect", "No valid blendShape node set.\nUse 'Get' to pick one.")
            return

        rows   = self._collect_rows()
        pairs  = self._collect_soft_blend_pairs()
        curve  = self._graph.get_keys()
        results = build_and_connect_rig(
            bs_node, rows, soft_blend_pairs=pairs, soft_blend_curve=curve)

        # Build shape → table row lookup
        row_by_shape = {}
        for r in range(self._table.rowCount()):
            item = self._table.item(r, self._COL_SHAPE)
            if item:
                row_by_shape[item.text()] = r

        ok_count = err_count = skip_count = 0
        for res in results:
            shape  = res["shape"]
            status = res["status"]
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
        self._lbl_preview.setText("Preview  →  e.g. " + "_".join(parts))

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
    VERSION   = "v.05.16"

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
        self._build_ui()
        self.resize(_SHELF_W, _DEFAULT_H)
        self._mouse_filter = _GlobalMouseReleaseFilter(self._refresh_top_status, self)
        QtWidgets.QApplication.instance().installEventFilter(self._mouse_filter)
        self._refresh_top_status(check_phantoms=True)


    def closeEvent(self, event):
        QtWidgets.QApplication.instance().removeEventFilter(self._mouse_filter)
        super().closeEvent(event)

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

        def _apply_state(s):
            header.setArrowType(ARROWS[s])
            header.setText(LABELS[s])
            shelf_widget.setVisible(s == 1)
            body.setVisible(s == 2)

        def _on_click():
            cur = state[0]
            if two_state:
                nxt = 2 if cur == 0 else 0
            elif initial_state == 1:
                # Bounce cycle: 1→2→1→0→1→2→...
                # 2 and 0 always return to compact (1).
                # From compact, direction depends on where we came from:
                #   came from open (2) → go to closed (0)
                #   otherwise         → go to open (2)
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

        outer.add_compact_action    = add_compact_action
        outer.add_compact_text_btn  = add_compact_text_btn
        outer.add_compact_spacer    = add_compact_spacer
        outer.add_compact_row_break = add_compact_row_break
        outer.finalize_compact      = finalize_compact

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
        root = QtWidgets.QVBoxLayout(inner)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(10)

        # ── Maya Tools Shelf ──────────────────────────────────────────────
        shelf_frame = QtWidgets.QFrame()
        shelf_frame.setFrameShape(QtWidgets.QFrame.NoFrame)
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
        self.btn_clean_bs = _shelf_btn(
            f"{_icons_dir}/clean_bsnode.png",
            "Clean Blendshape Node\n"
            "Removes phantom (empty/unaliased) target slots from the blendShape node(s)\n"
            "of the selected targets in the Shape Editor.",
            callback=self._run_clean_bs)
        shelf_lay.addWidget(self.btn_clean_bs)
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
            "that shares the same target name:  bs_A.name  →  bs_B.name",
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
        btn_rig_browse = QtWidgets.QPushButton()
        _pix_browse = QtGui.QPixmap(f"{_icons_dir}/path.png")
        if not _pix_browse.isNull():
            btn_rig_browse.setIcon(QtGui.QIcon(_pix_browse))
            btn_rig_browse.setIconSize(QtCore.QSize(28, 28))
        btn_rig_browse.setFixedSize(32, 32)
        btn_rig_browse.setToolTip("Browse for a rig mapping JSON file")
        self.line_rig_json = QtWidgets.QLineEdit()
        self.line_rig_json.setReadOnly(True)
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
            _scaled_connect = _pix_connect.scaled(32, 32, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)
            btn_rig_connect.setIcon(QtGui.QIcon(_scaled_connect))
            btn_rig_connect.setIconSize(QtCore.QSize(32, 32))
        btn_rig_connect.setFixedSize(40, 40)
        cff_row.addWidget(btn_rig_browse)
        cff_row.addWidget(self.line_rig_json, 1)
        cff_row.addWidget(btn_rig_connect)
        shelf_wrapper_lay.addLayout(cff_row)
        btn_rig_connect.setEnabled(False)
        self.line_rig_json.textChanged.connect(
            lambda text: self.btn_rig_connect.setEnabled(bool(text.strip())))
        btn_rig_browse.clicked.connect(self._browse_rig_json)
        btn_rig_connect.clicked.connect(self._run_connect_from_file)

        shelf_wrapper_lay.addWidget(self._ts_toggle)
        shelf_wrapper_lay.addWidget(ts_widget)
        shelf_wrapper_lay.addWidget(shelf_frame)

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

        btn_naming_conv = QtWidgets.QPushButton("Naming Convention…")
        btn_naming_conv.setToolTip(
            "Configure token order, prefix, and custom naming pairs\n"
            "used by the tool when generating target names.")
        btn_naming_conv.clicked.connect(self._open_naming_convention)
        lay_nom.addWidget(btn_naming_conv)

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
        lbl_pfx = QtWidgets.QLabel("Set Prfx")
        lbl_pfx.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        self.edit_rename_pfx = QtWidgets.QLineEdit()
        self.edit_rename_pfx.setPlaceholderText("prefix")
        self.edit_rename_pfx.setToolTip("Add a prefix to each selected target name")
        lbl_sfx = QtWidgets.QLabel("Sufx")
        lbl_sfx.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        self.edit_rename_sfx = QtWidgets.QLineEdit()
        self.edit_rename_sfx.setPlaceholderText("suffix")
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
        lbl_search.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        self.edit_search = QtWidgets.QLineEdit()
        self.edit_search.setPlaceholderText("search")
        self.edit_search.setToolTip("String to find in target names")
        lbl_arrow = QtWidgets.QLabel("→")
        lbl_arrow.setAlignment(QtCore.Qt.AlignCenter)
        self.edit_replace = QtWidgets.QLineEdit()
        self.edit_replace.setPlaceholderText("replace")
        self.edit_replace.setToolTip("Replacement string (leave empty to delete)")
        btn_apply_sr = QtWidgets.QPushButton("Apply")
        btn_apply_sr.setFixedWidth(_REN_BTN_W)
        btn_apply_sr.setToolTip("Apply search & replace to all selected target names")
        btn_apply_sr.clicked.connect(self._run_search_replace)
        ren_grid.addWidget(lbl_search,       1, 0)
        ren_grid.addWidget(self.edit_search,  1, 1)
        ren_grid.addWidget(lbl_arrow,         1, 2)
        ren_grid.addWidget(self.edit_replace, 1, 3)
        ren_grid.addWidget(btn_apply_sr,      1, 4)

        lay_rename.addLayout(ren_grid)

        # ── Swap Target Names ─────────────────────────────────────────────
        _w_swap, self.btn_swap_names = self._icon_btn(
            f"{_icons_dir}/swap_names.png", "Swap Target Names",
            "Swaps the names of exactly 2 selected targets in the Shape Editor.\n"
            "Select 2 targets, then click — their names are exchanged instantly.")
        self.btn_swap_names.clicked.connect(self._run_swap_names)
        lay_rename.addWidget(_w_swap)

        lay_nom.addWidget(grp_rename)


        root.addWidget(grp_nom)

        # ── Split (includes Locator controls) ─────────────────────────────
        grp_split, _body_split, lay_split = self._collapsible_section("Split", two_state=True)
        lay_split.setSpacing(6)

        # ── Locators ──────────────────────────────────────────────────────
        #
        # Right column layout:
        #   [   Create Locator (colspan 2)   ]
        #   [+][−]
        #   [↑][↓]
        #   [🔗][⛓]
        #
        _BW = 28  # button size (square)
        _SP = 2   # grid spacing
        _grid_h = 4 * _BW + 3 * _SP  # 118 px — drives table height too
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
                b.setIcon(QtGui.QIcon(px.scaled(20, 20,
                    QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)))
                b.setIconSize(QtCore.QSize(20, 20))
            b.clicked.connect(callback)
            return b

        btn_get = _side_btn("+",
            "Select locators from the character's right side to left\n"
            "(i.e. from your left to right when facing the character).\n"
            "The selection order maps directly to zone naming:\n"
            "  1 locator  →  symmetric L_ / R_ pair\n"
            "  3 locators →  R_ / C_ / L_\n"
            "  4+ locators → alphabetical  (a, b, c…)",
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
        btn_create_loc.setFixedSize(_BW * 2 + _SP, _BW)
        btn_create_loc.setAutoRaise(True)
        btn_create_loc.setStyleSheet(_ICON_BTN_STYLE)
        btn_create_loc.setToolTip("Create a locator at the origin and add it to the table")
        _px_loc = QtGui.QPixmap(f"{_icons_dir}/locator.png")
        if not _px_loc.isNull():
            btn_create_loc.setIcon(QtGui.QIcon(_px_loc.scaled(
                24, 24, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)))
            btn_create_loc.setIconSize(QtCore.QSize(24, 24))
        btn_create_loc.clicked.connect(self._create_locator)

        side_grid = QtWidgets.QGridLayout()
        side_grid.setSpacing(_SP)
        side_grid.setContentsMargins(0, 0, 0, 0)
        side_grid.addWidget(btn_create_loc, 0, 0, 1, 2)  # row 0, colspan 2
        side_grid.addWidget(btn_get,        1, 0)
        side_grid.addWidget(btn_up,         1, 1)
        side_grid.addWidget(btn_rm,         2, 0)
        side_grid.addWidget(btn_dn,         2, 1)
        side_grid.addWidget(btn_link,       3, 0)
        side_grid.addWidget(btn_unlink,     3, 1)

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

        loc_row = QtWidgets.QHBoxLayout()
        loc_row.setSpacing(4)
        loc_row.setContentsMargins(0, 0, 0, 0)
        loc_row.addWidget(self.table, 1)
        loc_row.addLayout(side_grid)
        lay_split.addLayout(loc_row)

        # ── Separateur visuel ─────────────────────────────────────────────
        _sep_loc = QtWidgets.QFrame()
        _sep_loc.setFrameShape(QtWidgets.QFrame.HLine)
        _sep_loc.setFrameShadow(QtWidgets.QFrame.Sunken)
        lay_split.addWidget(_sep_loc)

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

        self.spin_radius = QtWidgets.QDoubleSpinBox()
        self.spin_radius.setRange(0.0, 15.0)
        self.spin_radius.setValue(1.0)
        self.spin_radius.setSingleStep(0.1)
        self.spin_radius.setDecimals(1)
        self.spin_radius.setFixedWidth(55)
        self.spin_radius.setButtonSymbols(QtWidgets.QAbstractSpinBox.NoButtons)
        self.spin_radius.setLocale(QtCore.QLocale(QtCore.QLocale.English))
        self.spin_radius.setEnabled(False)
        self.spin_radius.valueChanged.connect(self._on_radius_spin)
        row_rad.addWidget(self.spin_radius)

        lay_falloff.addLayout(row_rad)
        lay_split.addWidget(grp_falloff)

        # Split Target button
        _w_split, self.btn_split = self._icon_btn(
            f"{_icons_dir}/split.png", "Split Target",
            "Creates split targets in the blendShape node")
        self.btn_split.clicked.connect(self._run_split)

        # ── Edge Loop Split button ────────────────────────────────────────
        _w_els, self.btn_edge_loop_split = self._icon_btn(
            f"{_icons_dir}/edge_split.png", "Edge Loop Split",
            "Splits selected targets along the stored edge loop.\n"
            "Set Vertices and Edgeloop via the Setup section below.\n"
            "The Radius setting controls the falloff blend at the seam (default: 1).\n"
            "Enable Radius and increase the value for a softer transition.")
        self.btn_edge_loop_split.clicked.connect(self._run_edge_loop_split)
        lay_split.addWidget(_w_split)
        lay_split.addWidget(_w_els)

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
            "  1 locator  →  symmetric L_ / R_ pair\n"
            "  3 locators →  R_ / C_ / L_\n"
            "  4+ locators → alphabetical  (a, b, c…)",
            self._get_locators_from_selection)
        grp_split.add_compact_action(
            f"{_icons_dir}/split.png", "Split Target", self._run_split)
        grp_split.add_compact_action(
            f"{_icons_dir}/edge_split.png", "Edge Loop Split", self._run_edge_loop_split)
        root.addWidget(grp_split)

        # ── Modify ────────────────────────────────────────────────────────
        grp_mod, _body_mod, lay_mod = self._collapsible_section("Modify Deltas", initial_state=1, compact_rows=4)
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
            lbl.setFixedWidth(22)
            lbl.setFixedHeight(24)
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
            self.btn_mult_sign.setIcon(_ico_minus if self._mult_sign < 0 else _ico_plus)
        self.btn_mult_sign.clicked.connect(_on_mult_sign_clicked)
        row_xyz.addWidget(self.btn_mult_sign)
        for _lbl, _fld in zip(self._mult_labels, self._mult_fields):
            _fld.setFixedWidth(16777215)
            _fld.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
            row_xyz.addWidget(_lbl)
            row_xyz.addWidget(_fld, 1)
        lay_scalar.addLayout(row_xyz)

        _w_mult, _b_mult = self._label_icon_btn(f"{_icons_dir}/multiply_delta.png", "Multiply", _tt_multiply)
        _b_mult.clicked.connect(self._run_multiply)
        _w_inv,  _b_inv  = self._label_icon_btn(f"{_icons_dir}/invert_delta.png",   "Invert",   _tt_invert)
        _b_inv.clicked.connect(self._run_invert_deltas)
        _w_nul,  _b_nul  = self._label_icon_btn(f"{_icons_dir}/nullify_delta.png",  "Nullify",  _tt_nullify)
        _b_nul.clicked.connect(self._run_nullify)
        self._align_label_icon_btns([_w_mult, _w_inv, _w_nul])
        row_min = QtWidgets.QHBoxLayout()
        row_min.setSpacing(4)
        row_min.addWidget(_w_mult)
        row_min.addWidget(_w_inv)
        row_min.addWidget(_w_nul)
        lay_scalar.addLayout(row_min)

        lay_scalar.addSpacing(8)

        # Normal Push
        self.field_push_factor = _make_factor_field("0.20")
        self.field_push_factor.setToolTip("Push magnitude relative to existing delta length.")

        self._push_sign = 1.0
        self.btn_push_sign = QtWidgets.QToolButton()
        self.btn_push_sign.setFixedSize(26, 34)
        self.btn_push_sign.setToolButtonStyle(QtCore.Qt.ToolButtonTextOnly)
        self.btn_push_sign.setText("+")
        self.btn_push_sign.setAutoRaise(True)
        self.btn_push_sign.setStyleSheet("""
            QToolButton {
                background-color: transparent;
                border: none;
                border-radius: 3px;
                padding: 0px 0px 4px 0px;
                font-size: 16px;
                font-weight: bold;
            }
            QToolButton:hover   { background-color: rgba(255,255,255,30); }
            QToolButton:pressed { background-color: rgba(0,0,0,40); }
        """)
        self.btn_push_sign.setToolTip("Toggle direction: + pushes outward, − pushes inward.")
        def _on_push_sign_clicked():
            self._push_sign *= -1.0
            self.btn_push_sign.setText("−" if self._push_sign < 0 else "+")
        self.btn_push_sign.clicked.connect(_on_push_sign_clicked)

        btn_push = QtWidgets.QToolButton()
        btn_push.setFixedSize(36, 34)
        btn_push.setIconSize(QtCore.QSize(32, 32))
        btn_push.setAutoRaise(True)
        btn_push.setStyleSheet("""
            QToolButton {
                background-color: transparent;
                border: none;
                border-radius: 3px;
                padding: 1px;
            }
            QToolButton:hover   { background-color: rgba(255,255,255,30); }
            QToolButton:pressed { background-color: rgba(0,0,0,40); }
        """)
        _px_push = QtGui.QPixmap(f"{_icons_dir}/normal_push.png")
        if not _px_push.isNull():
            btn_push.setIcon(QtGui.QIcon(_px_push))
        btn_push.setToolTip("Add displacement along vertex normals,\n"
                            "weighted by existing delta magnitude.\n"
                            "Only vertices with existing deltas are affected.")
        btn_push.clicked.connect(self._run_push_normals)

        row_push = QtWidgets.QHBoxLayout()
        row_push.setSpacing(4)
        row_push.setContentsMargins(0, 0, 0, 0)
        _push_sign_field = QtWidgets.QHBoxLayout()
        _push_sign_field.setSpacing(1)
        _push_sign_field.setContentsMargins(0, 0, 0, 0)
        _push_sign_field.addWidget(self.btn_push_sign)
        _push_sign_field.addWidget(self.field_push_factor)
        row_push.addWidget(QtWidgets.QLabel("Normal Push"))
        row_push.addLayout(_push_sign_field)
        row_push.addWidget(btn_push)
        row_push.addStretch()

        lay_scalar.addLayout(row_push)
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
            "Select target A first, then add select target B.\n"
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
        _w_sub,  self.btn_delta_sub         = self._label_icon_btn(f"{_icons_dir}/sub_delta.png",      "Sub",      _tt_sub)
        _w_msh,  self.btn_delta_mult_shapes = self._label_icon_btn(f"{_icons_dir}/mult_delta.png",     "Mult",     _tt_mult_sh)
        _w_xfer, self.btn_delta_swap        = self._label_icon_btn(f"{_icons_dir}/transfer_delta.png", "Transfer", _tt_xfer)
        _w_swap, self.btn_delta_swap_pure   = self._label_icon_btn(f"{_icons_dir}/swap_delta.png",     "Swap",     _tt_swap)
        _w_repl, self.btn_delta_replace     = self._label_icon_btn(f"{_icons_dir}/replace_delta.png",  "Replace",  _tt_repl)
        self.btn_delta_add.clicked.connect(self._run_delta_add)
        self.btn_delta_sub.clicked.connect(self._run_delta_sub)
        self.btn_delta_mult_shapes.clicked.connect(self._run_delta_mult_shapes)
        self.btn_delta_swap.clicked.connect(self._run_delta_swap)
        self.btn_delta_swap_pure.clicked.connect(self._run_delta_swap_pure)
        self.btn_delta_replace.clicked.connect(self._run_delta_replace)

        grid_2tgt = QtWidgets.QGridLayout()
        grid_2tgt.setSpacing(4)
        grid_2tgt.addWidget(_w_add,  0, 0)
        grid_2tgt.addWidget(_w_sub,  0, 1)
        grid_2tgt.addWidget(_w_msh,  0, 2)
        grid_2tgt.addWidget(_w_xfer, 1, 0)
        grid_2tgt.addWidget(_w_swap, 1, 1)
        grid_2tgt.addWidget(_w_repl, 1, 2)
        self._align_label_icon_btns([_w_add, _w_sub, _w_msh, _w_xfer, _w_swap, _w_repl])
        grid_2tgt.setColumnStretch(0, 1)
        grid_2tgt.setColumnStretch(1, 1)
        grid_2tgt.setColumnStretch(2, 1)
        lay_2tgt.addLayout(grid_2tgt)
        lay_mod.addWidget(grp_2tgt)

        # ── Smooth & Relax ────────────────────────────────────────────────────
        grp_smooth = QtWidgets.QGroupBox("Smooth & Average")
        grp_smooth.setStyleSheet(_GRP_STYLE)
        lay_smooth = QtWidgets.QVBoxLayout(grp_smooth)
        lay_smooth.setContentsMargins(*_GRP_MARGINS)
        lay_smooth.setSpacing(4)

        row_opacity = QtWidgets.QHBoxLayout()
        row_opacity.setSpacing(4)
        lbl_opacity = QtWidgets.QLabel("Opacity")
        lbl_opacity.setFixedWidth(52)
        self.slider_smooth_opacity = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.slider_smooth_opacity.setRange(1, 100)
        self.slider_smooth_opacity.setValue(100)
        self.slider_smooth_opacity.setToolTip(
            "Strength for Smooth, Relax, Hammer and Average.\n"
            "Smooth / Relax: maps to 1–10 iterative passes.\n"
            "Hammer: maps to 1–20 iterative passes (50% = 10, default).\n"
            "Average: blend weight between original and averaged value.")
        self.lbl_smooth_opacity_val = QtWidgets.QLabel("1.00")
        self.lbl_smooth_opacity_val.setFixedWidth(30)
        self.slider_smooth_opacity.valueChanged.connect(
            lambda v: self.lbl_smooth_opacity_val.setText(f"{v/100:.2f}"))
        row_opacity.addWidget(lbl_opacity)
        row_opacity.addWidget(self.slider_smooth_opacity)
        row_opacity.addWidget(self.lbl_smooth_opacity_val)
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
        lbl_smooth_neighbors = QtWidgets.QLabel("Space")
        lbl_smooth_neighbors.setFixedWidth(40)
        self.combo_smooth_falloff = QtWidgets.QComboBox()
        self.combo_smooth_falloff.addItem("Neutral",  "neutral")
        self.combo_smooth_falloff.addItem("Deformed", "deformed")
        self.combo_smooth_falloff.setCurrentIndex(0)
        self.combo_smooth_falloff.setToolTip(
            "Space in which neighbor distances are computed (Hammer only).\n"
            "\n"
            "Neutral  — distances on the rest mesh (no deltas applied).\n"
            "Deformed — distances on the mesh with deltas applied.\n"
            "\n"
            "Example — open mouth shape:\n"
            "  Neutral : upper and lower lip vertices are close at rest,\n"
            "            so the hammer may blend deltas across the gap.\n"
            "  Deformed: lips are spread apart, each lip is hammered\n"
            "            independently without cross-influence.")
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

        row_prune = QtWidgets.QHBoxLayout()
        row_prune.setSpacing(4)
        row_prune.addWidget(QtWidgets.QLabel("Prune Small Deltas"))
        row_prune.addWidget(self.spin_prune_tol)
        row_prune.addWidget(self.btn_prune)

        _w_sel_delta, self.btn_sel_delta = self._label_icon_btn(
            f"{_icons_dir}/select_delta.png", "Select Delta Vertices",
            "Selects all vertices that have non-zero deltas on the active target.")
        self.btn_sel_delta.clicked.connect(self._run_select_delta_vertices)

        grid_cps = QtWidgets.QGridLayout()
        grid_cps.setSpacing(4)
        grid_cps.addWidget(_w_copy_delta,  0, 0)
        grid_cps.addWidget(_w_sel_delta,   0, 1)
        grid_cps.addWidget(_w_paste_delta, 1, 0)
        grid_cps.addLayout(row_prune,      1, 1)
        self._align_label_icon_btns([_w_copy_delta, _w_paste_delta])
        grid_cps.setColumnStretch(0, 1)
        grid_cps.setColumnStretch(1, 1)
        lay_sel.addLayout(grid_cps)
        lay_mod.addWidget(grp_sel)

        # ── Import / Bake ─────────────────────────────────────────────────────
        grp_bake = QtWidgets.QGroupBox("Deltas Bake")
        grp_bake.setStyleSheet(_GRP_STYLE)
        lay_bake = QtWidgets.QVBoxLayout(grp_bake)
        lay_bake.setContentsMargins(*_GRP_MARGINS)
        lay_bake.setSpacing(4)

        _w_apply, self.btn_apply_moves = self._label_icon_btn(
            f"{_icons_dir}/bake_moves.png", "Bake Moves",
            "Transfers vertex tweaks (pnts[]) from the mesh to the selected target.\n"
            "Use when you sculpted the mesh directly without entering edit mode first.\n"
            "The vertex moves are added to the target's existing deltas,\n"
            "then zeroed out on the mesh.\n"
            "Works on 1 selected target only.")
        self.btn_apply_moves.clicked.connect(self._run_apply_moves)

        _w_bake, self.btn_bake_deformers = self._label_icon_btn(
            f"{_icons_dir}/bake_deformer.png", "Bake Deformers",
            "Bakes the contribution of all deformers stacked above the blendShape into the\n"
            "selected targets. For each target the tool activates it at weight 1.0, samples\n"
            "the mesh with all deformers evaluated, and stores the result as the new delta set.\n\n"
            "Typical workflow:\n"
            "  1. Add a Delta Mush (or any deformer) on the base mesh and adjust it.\n"
            "  2. Select the targets to improve in the Shape Editor.\n"
            "  3. Click Bake Deformers.\n"
            "  4. Delete the deformer.\n\n"
            "Works on all targets selected in the Shape Editor.")
        self.btn_bake_deformers.clicked.connect(self._run_bake_deformers)
        self._align_label_icon_btns([_w_apply, _w_bake])
        lay_bake.addWidget(_w_apply)
        lay_bake.addWidget(_w_bake)
        # ── Rig Extraction ────────────────────────────────────────────────────
        grp_rig = QtWidgets.QGroupBox("Deltas to Rig")
        grp_rig.setStyleSheet(_GRP_STYLE)
        lay_rig = QtWidgets.QVBoxLayout(grp_rig)
        lay_rig.setContentsMargins(*_GRP_MARGINS)
        lay_rig.setSpacing(4)

        row_delta_opts = QtWidgets.QHBoxLayout()
        self.chk_delta_neutral = QtWidgets.QCheckBox("Neutral")
        self.chk_delta_neutral.setChecked(True)
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
        row_bake_rig = QtWidgets.QHBoxLayout()
        row_bake_rig.setSpacing(4)
        row_bake_rig.addWidget(grp_bake, 9)
        row_bake_rig.addWidget(grp_rig, 11)
        lay_mod.addLayout(row_bake_rig)

        # Row 0 — Deltas Scale + Smooth & Average (8)
        grp_mod.add_compact_action(f"{_icons_dir}/multiply_delta.png", "Multiply Deltas",      self._run_multiply)
        grp_mod.add_compact_action(f"{_icons_dir}/invert_delta.png",   "Invert Deltas",        self._run_invert_deltas)
        grp_mod.add_compact_action(f"{_icons_dir}/nullify_delta.png",  "Nullify",              self._run_nullify)
        grp_mod.add_compact_action(f"{_icons_dir}/normal_push.png",    "Normal Push",          self._run_push_normals)
        grp_mod.add_compact_action(f"{_icons_dir}/smooth_delta.png",   "Smooth Deltas",        self._run_smooth_deltas)
        grp_mod.add_compact_action(f"{_icons_dir}/relax_delta.png",    "Relax Deltas",         self._run_relax_deltas)
        grp_mod.add_compact_action(f"{_icons_dir}/hammer_delta.png",   "Hammer Deltas",        self._run_hammer_deltas)
        grp_mod.add_compact_action(f"{_icons_dir}/average_delta.png",  "Average Deltas",       self._run_average_deltas)
        grp_mod.add_compact_row_break()
        # Row 1 — Deltas Exchange (6)
        grp_mod.add_compact_action(f"{_icons_dir}/add_delta.png",      "Add",                  self._run_delta_add)
        grp_mod.add_compact_action(f"{_icons_dir}/sub_delta.png",      "Sub",                  self._run_delta_sub)
        grp_mod.add_compact_action(f"{_icons_dir}/mult_delta.png",     "Mult",                 self._run_delta_mult_shapes)
        grp_mod.add_compact_action(f"{_icons_dir}/transfer_delta.png", "Transfer",             self._run_delta_swap)
        grp_mod.add_compact_action(f"{_icons_dir}/swap_delta.png",     "Swap",                 self._run_delta_swap_pure)
        grp_mod.add_compact_action(f"{_icons_dir}/replace_delta.png",  "Replace",              self._run_delta_replace)
        grp_mod.add_compact_row_break()
        # Row 2 — Deltas Clipboard + Bake + Rig (8)
        grp_mod.add_compact_action(f"{_icons_dir}/copy_delta.png",     "Copy Delta",           self._run_copy_delta)
        grp_mod.add_compact_action(f"{_icons_dir}/paste_delta.png",    "Paste Delta",          self._run_paste_delta)
        grp_mod.add_compact_action(f"{_icons_dir}/prune_delta.png",    "Prune Small Deltas",      self._run_prune_deltas)
        grp_mod.add_compact_action(f"{_icons_dir}/select_delta.png",   "Select Delta Vertices",   self._run_select_delta_vertices)
        grp_mod.add_compact_action(f"{_icons_dir}/bake_moves.png",    "Bake Moves",          self._run_apply_moves)
        grp_mod.add_compact_action(f"{_icons_dir}/bake_deformer.png",   "Bake Deformers",       self._run_bake_deformers)
        grp_mod.add_compact_action(f"{_icons_dir}/delta_cluster.png",  "Create Delta Cluster", self._run_delta_cluster)
        grp_mod.add_compact_action(f"{_icons_dir}/delta_joint.png",    "Create Delta Joint",   self._run_delta_joint)
        grp_mod.finalize_compact()
        root.addWidget(grp_mod)

        # ── Tools ─────────────────────────────────────────────────────────────
        grp_tools, _body_tools, lay_tools = self._collapsible_section("Tools", two_state=True, initial_state=0)
        lay_tools.setSpacing(6)

        # ── Wrap Setup ────────────────────────────────────────────────────
        grp_wrap_setup = QtWidgets.QGroupBox("Wrap Setup")
        grp_wrap_setup.setStyleSheet("QGroupBox { font-size: 11px; }")
        lay_wrap_setup = QtWidgets.QVBoxLayout(grp_wrap_setup)
        lay_wrap_setup.setContentsMargins(8, 8, 8, 8)
        lay_wrap_setup.setSpacing(6)

        lbl_wrap_info = QtWidgets.QLabel(
            "Select targets in the Shape Editor + one mesh in the scene.")
        lbl_wrap_info.setStyleSheet("color: #888888; font-size: 11px;")
        lbl_wrap_info.setWordWrap(True)
        lay_wrap_setup.addWidget(lbl_wrap_info)

        self.chk_connect_targets = QtWidgets.QCheckBox("Connect extracted targets")
        self.chk_connect_targets.setChecked(True)
        self.chk_connect_targets.setToolTip(
            "After extraction, connects each target weight from the source blendShape\n"
            "to the matching target on the mesh's blendShape.\n"
            "source_bs.target_name  →  mesh_bs.target_name")
        lay_wrap_setup.addWidget(self.chk_connect_targets)

        row_wrap_btns = QtWidgets.QHBoxLayout()
        row_wrap_btns.setSpacing(2)

        _w_wrap, self.btn_wrap_extract = self._icon_btn(
            f"{_icons_dir}/wrap_extract.png",
            "Extract Wrap Targets",
            "Select master mesh (with BS) + one or more receiver meshes.\n"
            "If targets are selected in the Shape Editor, wraps those targets.\n"
            "Otherwise wraps all targets and prunes near-zero results.\n"
            "A BS node is created on each receiver if none exists.")
        self.btn_wrap_extract.clicked.connect(self._run_wrap_extract)

        _w_extract, self.btn_extract_only = self._icon_btn(
            f"{_icons_dir}/extract_only.png",
            "Extract Only",
            "Extracts each selected target using the deformer setup already present\n"
            "on the selected mesh (wrap, proximity wrap, etc.).\n"
            "No deformer is created or deleted.")
        self.btn_extract_only.clicked.connect(self._run_extract_only)

        row_wrap_btns.addWidget(_w_wrap)
        row_wrap_btns.addWidget(_w_extract)
        lay_wrap_setup.addLayout(row_wrap_btns)
        lay_tools.addWidget(grp_wrap_setup)

        grp_wire = QtWidgets.QGroupBox("Wire Setup")
        grp_wire.setStyleSheet("QGroupBox { font-size: 11px; }")
        grp_wire.setToolTip(
            "Wire Setup — Lip/Mouth curve-based deformation rig.\n\n"
            "Workflow:\n"
            "  1. Capture a base mesh and a symmetrical edge loop (upper or lower lip line).\n"
            "  2. List the shape names you want to generate (e.g. lip_up, lip_dn, …).\n"
            "  3. Create Wire Setup: builds a wire_crv driven by a blendShape (wire_bs),\n"
            "     deforming a duplicate of the base mesh (wire_setup_msh).\n"
            "  4. Sculpt each shape curve directly in the viewport.\n"
            "  5. Bake Wire to Mesh: transfers each posed wire_setup_msh as a\n"
            "     blendShape target onto the original base mesh."
        )
        lay_wire = QtWidgets.QVBoxLayout(grp_wire)
        lay_wire.setContentsMargins(8, 8, 8, 8)
        lay_wire.setSpacing(6)

        # Paint Wire Weights — shelf button
        _wire_shelf_row = QtWidgets.QHBoxLayout()
        btn_paint_wire = QtWidgets.QToolButton()
        btn_paint_wire.setFixedSize(40, 40)
        btn_paint_wire.setToolButtonStyle(QtCore.Qt.ToolButtonIconOnly)
        btn_paint_wire.setToolTip("Paint Wire Weights\nOpens the Paint Attributes tool on wire_setup_wire.weights.")
        btn_paint_wire.setStyleSheet("""
            QToolButton { background-color: transparent; border: none; border-radius: 3px; padding: 2px; }
            QToolButton:hover { background-color: rgba(255,255,255,30); }
            QToolButton:pressed { background-color: rgba(0,0,0,40); }
        """)
        _pw_px = QtGui.QPixmap(f"{_icons_dir}/paint_wire.png")
        if not _pw_px.isNull():
            btn_paint_wire.setIcon(QtGui.QIcon(
                _pw_px.scaled(32, 32, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)))
            btn_paint_wire.setIconSize(QtCore.QSize(32, 32))
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
        btn_mirror_wire.setFixedSize(40, 40)
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
            btn_mirror_wire.setIcon(QtGui.QIcon(
                _mw_px.scaled(32, 32, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)))
            btn_mirror_wire.setIconSize(QtCore.QSize(32, 32))
        btn_mirror_wire.clicked.connect(self._run_mirror_wire_weights)
        btn_mirror_wire.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        btn_mirror_wire.customContextMenuRequested.connect(
            lambda _: cmds.MirrorDeformerWeightsOptions()
        )
        _wire_shelf_row.addWidget(btn_mirror_wire)

        _wire_shelf_row.addStretch(1)
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
        btn_wire_add_shape = QtWidgets.QPushButton("Add")
        btn_wire_add_shape.setFixedWidth(40)
        btn_wire_add_shape.clicked.connect(self._wire_add_shape)
        btn_wire_rm_shape = QtWidgets.QPushButton("Remove")
        btn_wire_rm_shape.setFixedWidth(56)
        btn_wire_rm_shape.setToolTip("Remove selected shape from the list")
        btn_wire_rm_shape.clicked.connect(self._wire_remove_shape)
        row_shapes_ctrl.addWidget(self.edit_wire_shape_add, 1)
        row_shapes_ctrl.addWidget(btn_wire_add_shape)
        row_shapes_ctrl.addWidget(btn_wire_rm_shape)
        lay_wire.addLayout(row_shapes_ctrl)

        # Dropoff / Rotation / Spans / Flat Curve
        row_wparams = QtWidgets.QHBoxLayout()
        row_wparams.addWidget(QtWidgets.QLabel("Dropoff"))
        self.spin_wire_dropoff = QtWidgets.QDoubleSpinBox()
        self.spin_wire_dropoff.setRange(0.1, 9999.0)
        self.spin_wire_dropoff.setValue(100.0)
        self.spin_wire_dropoff.setDecimals(1)
        self.spin_wire_dropoff.setFixedWidth(60)
        self.spin_wire_dropoff.setLocale(QtCore.QLocale(QtCore.QLocale.English))
        self.spin_wire_dropoff.setToolTip("Wire deformer dropoff distance")
        row_wparams.addWidget(self.spin_wire_dropoff)
        row_wparams.addSpacing(8)
        row_wparams.addWidget(QtWidgets.QLabel("Rotation"))
        self.spin_wire_rotation = QtWidgets.QDoubleSpinBox()
        self.spin_wire_rotation.setRange(0.0, 1.0)
        self.spin_wire_rotation.setValue(0.0)
        self.spin_wire_rotation.setSingleStep(0.05)
        self.spin_wire_rotation.setDecimals(2)
        self.spin_wire_rotation.setFixedWidth(50)
        self.spin_wire_rotation.setLocale(QtCore.QLocale(QtCore.QLocale.English))
        self.spin_wire_rotation.setToolTip("Wire deformer rotation value")
        row_wparams.addWidget(self.spin_wire_rotation)
        row_wparams.addSpacing(8)
        row_wparams.addWidget(QtWidgets.QLabel("Spans"))
        self.spin_wire_spans = QtWidgets.QSpinBox()
        self.spin_wire_spans.setRange(1, 64)
        self.spin_wire_spans.setValue(4)
        self.spin_wire_spans.setFixedWidth(44)
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
        btn_create_wire = QtWidgets.QPushButton("Create Wire Setup")
        btn_create_wire.setToolTip(
            "Creates wire_setup_msh, wire_crv, wire_bs and the wire deformer\n"
            "from the base mesh and edge selection above.")
        btn_create_wire.clicked.connect(self._run_create_wire_setup)
        lay_wire.addWidget(btn_create_wire)

        # Bake Wire to Mesh
        btn_bake_wire = QtWidgets.QPushButton("Bake Wire to Mesh")
        btn_bake_wire.setToolTip(
            "For each shape curve, poses wire_setup_msh and adds the result\n"
            "as a blendShape target on the base mesh's bs_node.\n"
            "Existing targets with the same name are overwritten.")
        btn_bake_wire.clicked.connect(self._run_bake_wire)
        lay_wire.addWidget(btn_bake_wire)

        self.chk_wire_delete_after_bake = QtWidgets.QCheckBox("Delete Wire Setup after Bake")
        self.chk_wire_delete_after_bake.setChecked(False)
        self.chk_wire_delete_after_bake.setToolTip(
            "If checked, deletes wire_setup_grp from the scene after a successful bake.")
        lay_wire.addWidget(self.chk_wire_delete_after_bake)

        lay_tools.addWidget(grp_wire)

        # ── Joints Setup ──────────────────────────────────────────────────
        grp_joints = QtWidgets.QGroupBox("Joints Setup")
        lay_joints = QtWidgets.QVBoxLayout(grp_joints)
        lay_joints.setContentsMargins(8, 8, 8, 8)
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

        self._update_single_loc_state()

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _auto_suffixes(self, n):
        if n == 0:
            return []
        if n <= 3:
            mapping = {
                1: ["in"],
                2: ["in", "out"],
                3: ["in", "mid", "out"],
            }
            return mapping[n]
        return [chr(ord('a') + i) for i in range(n)]

    def _resize_table_to_content(self):
        header_h   = self.table.horizontalHeader().height()
        rows_h     = sum(self.table.rowHeight(i) for i in range(self.table.rowCount()))
        content_h  = header_h + rows_h + 2
        final_h    = max(content_h, self._loc_grid_h)
        self.table.setMinimumHeight(final_h)
        self.table.setMaximumHeight(final_h)

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
        # n=1 : [""]          → R_name   L_name
        # n=2 : ["", ""]      → R_name   L_name
        # n=3 : ["", "", ""]  → R_name   C_name  L_name
        # n=4 : ["_b","_a","_a","_b"] → R_name_b  R_name_a  L_name_a  L_name_b
        # n=5 : ["_b","_a","","_a","_b"] → R_name_b  R_name_a  C_name  L_name_a  L_name_b

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
        self.spin_radius.setValue(value / 10.0)
        self.spin_radius.blockSignals(False)

    def _on_radius_spin(self, value):
        self.slider_radius.blockSignals(True)
        self.slider_radius.setValue(int(value * 10))
        self.slider_radius.blockSignals(False)

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
        self.spin_radius.setValue(1.0)
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
        self.lbl_smooth_opacity_val.setText("0.50")

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

        Gradient: black(0) → red(0.5) → yellow(1)
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
              t=0.000–0.001 → noir pur  (0,    0,    0  )
              t=0.001       → bleu      hue=0.667
              t=0.125       → cyan      hue=0.500
              t=0.250       → vert      hue=0.333
              t=0.500       → jaune     hue=0.167
              t=0.625       → jaune-org hue=0.100
              t=0.750       → orange    hue=0.050
              t=0.999       → rouge     hue=0.000
              t=0.999–1.000 → blanc pur (1,    1,    1  )
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
                    cmds.polyColorPerVertex(base_mesh, remove=True)
                    cmds.setAttr(f"{base_mesh}.displayColors", 1 if prev_disp else 0)
            except Exception:
                pass
        self._dv_meshes = []
        self.btn_delta_view.setEnabled(True)
        self.btn_exit_delta_view.setEnabled(False)
        self._set_status("Delta View exited.")

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
        item_loc.setFlags(item_loc.flags() & ~QtCore.Qt.ItemIsEditable)
        item_loc.setData(QtCore.Qt.UserRole, long_name)
        self.table.setItem(new_row, 0, item_loc)
        self.table.setItem(new_row, 1, QtWidgets.QTableWidgetItem(""))

        # Refresh all suffixes now that row count changed
        suffixes = self._auto_suffixes(self.table.rowCount())
        for row in range(self.table.rowCount()):
            self.table.setItem(row, 2, QtWidgets.QTableWidgetItem(suffixes[row]))

        self._update_single_loc_state()
        self._resize_table_to_content()
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

        self.table.setRowCount(0)
        suffixes = self._auto_suffixes(len(locators))

        for i, loc in enumerate(locators):
            short_name = loc.split("|")[-1]
            self.table.insertRow(i)
            item_loc = QtWidgets.QTableWidgetItem(short_name)
            item_loc.setFlags(item_loc.flags() & ~QtCore.Qt.ItemIsEditable)
            item_loc.setData(QtCore.Qt.UserRole, loc)
            self.table.setItem(i, 0, item_loc)
            self.table.setItem(i, 1, QtWidgets.QTableWidgetItem(""))        # Side — empty by default
            self.table.setItem(i, 2, QtWidgets.QTableWidgetItem(suffixes[i]))

        self._update_single_loc_state()
        self._resize_table_to_content()

    def _move_row_up(self):
        rows = sorted({idx.row() for idx in self.table.selectedIndexes()})
        if not rows or rows[0] <= 0:
            return
        for row in rows:
            self._swap_rows(row, row - 1)
        self._reselect_rows([r - 1 for r in rows])

    def _move_row_down(self):
        rows = sorted({idx.row() for idx in self.table.selectedIndexes()}, reverse=True)
        if not rows or rows[0] >= self.table.rowCount() - 1:
            return
        for row in rows:
            self._swap_rows(row, row + 1)
        self._reselect_rows([r + 1 for r in rows])

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
            results = get_selected_targets()  # [(bs_node, idx), ...]
            if not results:
                self.lbl_top_status.setText("— no selection —")
                self.lbl_top_status.setStyleSheet("color: #666666; font-size: 11px; padding-top: 4px; padding-bottom: 4px;")
                self._cached_phantom_count = 0
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
        except Exception:
            pass

    def _get_targets_or_warn(self):
        results = get_selected_targets()
        if not results:
            cmds.warning("Please select at least one target in the Shape Editor.")
            return []
        return results

    def _update_nom_preview(self):
        pass  # Preview is now shown inside the Naming Convention dialog

    def _build_target_name(self, base_name, side, suffix):
        """
        Assembles the final target name from self._nom_token_order and self._nom_prefix.
        Empty tokens are dropped — no double underscores produced.
          {prefix} → self._nom_prefix
          {side}   → e.g. "R", "L", "C"  (empty string = token skipped)
          {target} → base_name
          {suffix} → e.g. "a", "b", "up"  (empty string = token skipped)
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
                dropoff=self.spin_wire_dropoff.value(),
                rotation=self.spin_wire_rotation.value(),
                spans=self.spin_wire_spans.value(),
                flat_curve=self.chk_wire_flat.isChecked()
            )
            self._set_status(f"✓ Wire setup created — {len(shape_names)} shape(s)")
        except Exception as e:
            import traceback; traceback.print_exc()
            self._set_status(f"✗ Wire Setup: {e}", error=True)

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
            if self.chk_wire_delete_after_bake.isChecked():
                if cmds.objExists("wire_setup_grp"):
                    cmds.delete("wire_setup_grp")
            self._set_status(f"✓ Baked {len(baked)} shape(s) → {bs_node}")
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

    def _open_check_shapes(self):
        dlg = CheckShapesDialog(parent=self)
        dlg.show()

    def _open_rig_connector(self):
        dlg = RigConnectorDialog(parent=self)
        dlg.show()

    def _browse_rig_json(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Load Rig Mapping", _rig_mapping_prefs_path(), "JSON files (*.json)")
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

        # Normalize rows (handles old JSON format with direction / custom_attr)
        rows = []
        for rd in data:
            if not isinstance(rd, dict):
                continue
            attr        = rd.get("attr", "ty")
            in_min      = float(rd.get("in_min", 0.0))
            in_max      = float(rd.get("in_max", 1.0))
            custom_attr = rd.get("custom_attr", "")
            if attr == "custom" and custom_attr:
                attr = custom_attr
            if rd.get("direction", "+") == "\u2212":
                in_max = -abs(in_max)
                if in_min != 0.0:
                    in_min = -abs(in_min)
            rows.append({
                "shape":      rd.get("shape", ""),
                "proxy":      rd.get("proxy", False),
                "controller": rd.get("controller", ""),
                "attr":       attr,
                "in_min":     in_min,
                "in_max":     in_max,
                "gate":       rd.get("gate", ""),
            })

        results = build_and_connect_rig(bs_node, rows)
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

    @undo_chunk
    def _run_split(self):
        n_locs = self.table.rowCount()
        if n_locs < 1:
            cmds.warning("Please add at least 1 locator.")
            return

        targets = self._get_targets_or_warn()
        if not targets:
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
        radius        = self.spin_radius.value() if self.chk_radius.isChecked() else 0.0
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
                                    print(f"  Warning: could not reconnect {src} → {new_attr}: {e}")

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
        """Propagate the edited value to all other selected fields."""
        if self._mult_labels[idx].isChecked():
            value = self._mult_fields[idx].text()
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
    def _run_delta_swap(self):
        targets = self._get_targets_or_warn()
        if not targets:
            return
        if len(targets) < 2:
            self._set_status("✗ Swap: select A (receiver) then B (donor)", error=True)
            return

        raw_sel     = cmds.ls(sl=True, flatten=True) or []
        vtx_sel     = [s for s in raw_sel if ".vtx[" in s]
        vtx_indices = None if not vtx_sel else [int(s.split(".vtx[")[1].rstrip("]")) for s in vtx_sel]

        if vtx_indices is None:
            bs_node_a, idx_a, name_a = targets[0]
            bs_node_b, idx_b, name_b = targets[1]
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

        bs_node_a, idx_a, name_a = targets[0]
        bs_node_b, idx_b, name_b = targets[1]

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

    @undo_chunk
    def _run_multiply(self):
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
    def _run_push_normals(self):
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
        n_passes = max(1, int(round(opacity * 20)))
        vtx_indices = [int(s.split(".vtx[")[1].rstrip("]")) for s in vtx_sel]
        use_volume = self.combo_smooth_falloff.currentData() == "deformed"
        n_laplacian = int(self.spin_hammer_lap.text() or "0")
        n_t = len(targets)
        try:
            self._progress_begin(n_t)
            for i, (bs_node, logical_index, target_name) in enumerate(targets):
                self._progress_step(i, f"Hammering {target_name}…")
                hammer_target_deltas(bs_node, logical_index, vtx_indices,
                                     n_passes=n_passes, progress_cb=None,
                                     n_laplacian=n_laplacian, use_volume=use_volume)
            scope = f"{len(vtx_indices)} vtx"
            self._set_status(
                f"Hammer Deltas {n_t} target{'s' if n_t > 1 else ''}"
                f"  {scope}  ({n_passes} passes)")
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
        vtx_indices = [int(s.split(".vtx[")[1].rstrip("]")) for s in vtx_sel]
        n_t = len(targets)
        try:
            for bs_node, logical_index, target_name in targets:
                average_target_deltas(bs_node, logical_index, vtx_indices,
                                      opacity=opacity)
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
            n = apply_mesh_moves_to_target(bs_node, base_mesh, logical_index)
            self._set_status(f"✓ Bake Moves: {n} vertex move(s) added to '{target_name}'")
        except Exception as e:
            traceback.print_exc()
            self._set_status(f"✗ Bake Moves: {e}", error=True)

    @undo_chunk
    def _run_bake_deformers(self):
        targets = self._get_targets_or_warn()
        if not targets:
            return
        try:
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
                    total += bake_deformers_to_targets(bs_node, base_mesh, [idx])
                    done += 1
            self._set_status(f"✓ Bake Deformers: {total} target(s) baked")
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

        radius     = max(1, int(self.spin_radius.value())) if self.chk_radius.isChecked() else 1
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
                added.append(f"{bs_node} → {name}")
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
                f"onto {len(vtx_sel)} vtx → '{target_name}'")

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
        auto_mode  = not ui_targets  # True → wrap all targets, then prune near-zero

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
                    bs_node, base_mesh, receiver, targets)

                # Prune near-zero targets in auto mode
                pruned = 0
                if auto_mode:
                    for target_name, _ in log:
                        idx = get_bs_weight_attribute_logical_index(bs_target, target_name)
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
                    names = [name for name, _ in log]
                    connect_extracted_targets(bs_node, bs_target, names)

                rec_short = receiver.split(":")[-1].split("|")[-1]
                n_replaced = sum(1 for _, r in log if r)
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
                f"✓ Extract Only: {n_total} shape{'s' if n_total > 1 else ''} → '{grp}'")
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
                    f"  ({bs_A}  →  {bs_B})")
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