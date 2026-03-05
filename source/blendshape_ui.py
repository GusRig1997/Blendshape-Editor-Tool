from maya import cmds, mel
import traceback
from maya.app.general.mayaMixin import MayaQWidgetDockableMixin

from PySide6 import QtWidgets, QtCore, QtGui

from blendshape_core import *
from blendshape_core import _save_shape_editor_selection, _restore_shape_editor_selection

import json, os


def _user_naming_prefs_path():
    return os.path.join(cmds.internalVar(userPrefDir=True),
                        "blendshape_editor_naming.json")


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


def create_opposite_shape(symmetry_axis="Object X"):
    """
    Supports multiple selection in the Shape Editor.
    Duplicates each selected target, flips it, and renames it with the opposite naming convention.
    symmetry_axis : one of "Object X", "Object Y", "Object Z" — matches FLIP_AXIS_MAP.
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

    # Build axis lookup: duo -> axis name
    DUO_TO_AXIS = {}
    for ax_name, duos in AXIS_DUOS.items():
        for duo in duos:
            key = tuple(sorted(duo))
            if key not in DUO_TO_AXIS:
                DUO_TO_AXIS[key] = ax_name

    # Try axis-specific pairs first, then fall back to all pairs
    primary_duos  = AXIS_DUOS.get(symmetry_axis, [])
    fallback_duos = [d for ax, duos in AXIS_DUOS.items() for d in duos
                     if ax != symmetry_axis and d not in primary_duos]
    suffix_duos   = primary_duos + fallback_duos

    # Cache user decisions per axis mismatch to avoid repeated popups
    # key: (shape_token, suggested_axis) -> bool (True = use suggested, False = keep chosen)
    _user_decisions = {}

    for bs_name, index, shape in targets:
        # Find opposite name from naming convention
        names          = shape.split("_")
        opposite_shape = None
        matched_duo    = None
        matched_in_primary = False

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

        # If not found in primary, check fallback and warn
        if not matched_in_primary:
            for duos in fallback_duos:
                fix = [x for x in names if x in duos]
                if fix:
                    matched_duo     = duos
                    suggested_axis  = DUO_TO_AXIS.get(tuple(sorted(duos)), symmetry_axis)
                    decision_key    = (fix[0], suggested_axis)

                    if decision_key not in _user_decisions:
                        # Show confirmation dialog
                        _ax_sug = suggested_axis.replace("Object ", "")
                        _ax_sel = symmetry_axis.replace("Object ", "")
                        msg = (
                            f"Target '{shape}' contains token '{fix[0]}' "
                            f"which matches axis '{_ax_sug}', "
                            f"not the selected '{_ax_sel}' axis.\n\n"
                            f"Switch flip to '{_ax_sug}' for this target?"
                        )
                        reply = QtWidgets.QMessageBox.question(
                            None,
                            "Axis Mismatch",
                            msg,
                            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                            QtWidgets.QMessageBox.Yes
                        )
                        _user_decisions[decision_key] = (reply == QtWidgets.QMessageBox.Yes)

                    if _user_decisions[decision_key]:
                        # Use suggested axis for this target
                        space, axis = FLIP_AXIS_MAP.get(suggested_axis, (1, 'x'))
                    else:
                        space, axis = FLIP_AXIS_MAP.get(symmetry_axis, (1, 'x'))

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
        dup_idx          = duplicate_target(bs_name, base_mesh, index, f"{shape}_Copy")
        duplicated_shape = f"{shape}_Copy"

        # Flip the duplicate — space/axis may have been overridden by user dialog
        if 'space' not in dir() or matched_in_primary or opposite_shape is None:
            space, axis = FLIP_AXIS_MAP.get(symmetry_axis, (1, 'x'))
        sym_state = cmds.symmetricModelling(symmetry=True, q=True)
        cmds.blendShape(bs_name, e=True,
                        flipTarget=[(0, dup_idx)],
                        mirrorDirection=0,
                        symmetrySpace=space,
                        symmetryAxis=axis)
        if not sym_state == 1:
            cmds.symmetricModelling(symmetry=False)
        cmds.setAttr(f"{bs_name}.{duplicated_shape}", 0)

        # Replace the existing opposite target if it already exists
        existing_shapes = cmds.listAttr(f'{bs_name}.w', m=True) or []
        if opposite_shape in existing_shapes:
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
    VERSION   = "v.03.003"

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
        # Naming convention state — edited via Naming Convention dialog
        self._nom_token_order = ["{side}", "{target}", "{suffix}"]
        self._nom_prefix = ""
        self._build_ui()
        self.resize(_SHELF_W, _DEFAULT_H)


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

        if compact_rows == 2:
            # Grid mode: buttons placed in zigzag order (row = idx%2, col = idx//2)
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
            QtCore.QTimer.singleShot(0, self.adjustSize)

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

        if compact_rows == 2:
            def add_compact_action(icon_path, tooltip, callback):
                btn = _make_compact_btn(icon_path, tooltip, callback)
                idx = shelf_btn_idx[0]
                shelf_grid.addWidget(btn, idx % 2, idx // 2)
                shelf_btn_idx[0] += 1

            def add_compact_text_btn(label, tooltip, callback):
                btn = QtWidgets.QToolButton()
                btn.setFixedHeight(40)
                btn.setToolButtonStyle(QtCore.Qt.ToolButtonTextOnly)
                btn.setText(label)
                btn.setToolTip(tooltip)
                btn.setStyleSheet(SHELF_BTN_STYLE)
                btn.clicked.connect(callback)
                idx = shelf_btn_idx[0]
                shelf_grid.addWidget(btn, idx % 2, idx // 2)
                shelf_btn_idx[0] += 1

            def add_compact_row_break():
                pass  # no-op in grid mode
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

            def add_compact_row_break():
                shelf_cur_row[0] = _new_shelf_row()

        outer.add_compact_action    = add_compact_action
        outer.add_compact_text_btn  = add_compact_text_btn
        outer.add_compact_row_break = add_compact_row_break

        return outer, body, body_lay

    def _build_ui(self):
        import maya.cmds as _cmds
        _icons_dir = _cmds.internalVar(userAppDir=True) + "prefs/icons"


        # ── Outer layout + menu bar (fixes, hors scroll) ──────────────────
        outer_layout = QtWidgets.QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(0)

        # ── Menu bar ──────────────────────────────────────────────────────
        menu_bar   = QtWidgets.QMenuBar(self)
        menu_bar.setStyleSheet("QMenuBar { font-size: 11px; } QMenuBar::item { padding: 2px 8px; }")
        menu_edit  = menu_bar.addMenu("Edit")
        act_reset  = menu_edit.addAction("Reset Default Options")
        act_reset.setToolTip("Restore all split options to their default values")
        act_reset.triggered.connect(self._reset_default_options)
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
        root.setSpacing(6)

        # ── Maya Tools Shelf ──────────────────────────────────────────────
        shelf_frame = QtWidgets.QFrame()
        shelf_frame.setFrameShape(QtWidgets.QFrame.NoFrame)
        shelf_frame.setFixedHeight(42)
        shelf_lay = QtWidgets.QHBoxLayout(shelf_frame)
        shelf_lay.setContentsMargins(4, 3, 4, 3)
        shelf_lay.setSpacing(2)

        def _shelf_btn(icon_path, tooltip, mel_cmd=None, callback=None):
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
            return btn

        # Sculpt tools — left
        for _ic, _tt, _cmd in [
            (f"{_icons_dir}/Grab.png",    "Sculpt Grab",    "SetMeshGrabTool"),
            (f"{_icons_dir}/Flatten.png", "Sculpt Flatten", "SetMeshFlattenTool"),
            (f"{_icons_dir}/Bulge.png",   "Sculpt Bulge",   "SetMeshBulgeTool"),
        ]:
            shelf_lay.addWidget(_shelf_btn(_ic, _tt, _cmd))

        # Separator
        _sep = QtWidgets.QFrame()
        _sep.setFrameShape(QtWidgets.QFrame.VLine)
        _sep.setFrameShadow(QtWidgets.QFrame.Sunken)
        _sep.setFixedWidth(6)
        shelf_lay.addWidget(_sep)

        # Target tools — right
        shelf_lay.addWidget(_shelf_btn(
            f"{_icons_dir}/blendShapeEditor.png", "Shape Editor", mel_cmd="ShapeEditor"))
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
        shelf_lay.addWidget(self.btn_add_target)
        for _ic, _tt, _cmd in [
            (f"{_icons_dir}/SmoothTarget.png", "Smooth Target", "SetMeshSmoothTargetTool"),
            (f"{_icons_dir}/Erase.png",        "Erase Target",  "SetMeshEraseTool"),
        ]:
            shelf_lay.addWidget(_shelf_btn(_ic, _tt, _cmd))

        # Separator — visualization tools
        _sep2 = QtWidgets.QFrame()
        _sep2.setFrameShape(QtWidgets.QFrame.VLine)
        _sep2.setFrameShadow(QtWidgets.QFrame.Sunken)
        _sep2.setFixedWidth(6)
        shelf_lay.addWidget(_sep2)

        self.btn_delta_view = _shelf_btn(
            f"{_icons_dir}/delta_view.png",
            "Delta View — colorize vertices by delta magnitude (black→red→yellow)",
            callback=self._run_delta_view)
        shelf_lay.addWidget(self.btn_delta_view)

        self.btn_exit_delta_view = _shelf_btn(
            f"{_icons_dir}/exit_delta.png",
            "Exit Delta View — restore original vertex colors",
            callback=self._exit_delta_view)
        self.btn_exit_delta_view.setEnabled(False)
        shelf_lay.addWidget(self.btn_exit_delta_view)

        shelf_lay.addStretch()

        # Shelf pinned above the scroll — always visible
        shelf_wrapper = QtWidgets.QWidget()
        shelf_wrapper_lay = QtWidgets.QHBoxLayout(shelf_wrapper)
        shelf_wrapper_lay.setContentsMargins(8, 4, 8, 2)
        shelf_wrapper_lay.setSpacing(0)
        shelf_wrapper_lay.addWidget(shelf_frame)
        outer_layout.addWidget(shelf_wrapper)
        outer_layout.addWidget(scroll, 1)

        # ── Nomenclature ──────────────────────────────────────────────────────
        grp_nom, _body_nom, lay_nom = self._collapsible_section("Nomenclature", initial_state=0)
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

        # ── Split (inclut les contrôles Locators) ─────────────────────────
        grp_split, _body_split, lay_split = self._collapsible_section("Split")
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
            "  3 locators →  R_ / M_ / L_\n"
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
            "Odd locators: R_b R_a M_ L_a L_b\n"
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
        lay_split.addWidget(_w_split)

        _w_els, self.btn_edge_loop_split = self._icon_btn(
            f"{_icons_dir}/edge_split.png", "Edge Loop Split",
            "Splits the active target along a selected edge loop.\n"
            "Select the edge loop + 2 vertices: one on the upper side, one on the lower side.\n"
            "  → Select edges (edge loop) then Shift+click vtx_upper then Shift+click vtx_lower\n"
            "Creates <target>_upper and <target>_lower with Laplacian falloff.\n"
            "Falloff radius = spin_radius value when Radius is enabled, else 2.")
        self.btn_edge_loop_split.clicked.connect(self._run_edge_loop_split)
        lay_split.addWidget(_w_els)

        grp_split.add_compact_action(
            f"{_icons_dir}/locator.png", "Create Locator", self._create_locator)
        grp_split.add_compact_text_btn(
            "Get Selection",
            "Select locators from the character's right side to left\n"
            "(i.e. from your left to right when facing the character).\n"
            "The selection order maps directly to zone naming:\n"
            "  1 locator  →  symmetric L_ / R_ pair\n"
            "  3 locators →  R_ / M_ / L_\n"
            "  4+ locators → alphabetical  (a, b, c…)",
            self._get_locators_from_selection)
        grp_split.add_compact_action(
            f"{_icons_dir}/split.png", "Split Target", self._run_split)
        grp_split.add_compact_action(
            f"{_icons_dir}/edge_split.png", "Edge Loop Split", self._run_edge_loop_split)
        root.addWidget(grp_split)

        # ── Secondary Meshes ──────────────────────────────────────────────────
        grp_wrap, _body_wrap, lay_wrap = self._collapsible_section("Secondary Meshes", initial_state=1)
        lay_wrap.setSpacing(6)

        lbl_wrap_info = QtWidgets.QLabel(
            "Select targets in the Shape Editor + one mesh in the scene.")
        lbl_wrap_info.setStyleSheet("color: #888888; font-size: 11px;")
        lbl_wrap_info.setWordWrap(True)
        lay_wrap.addWidget(lbl_wrap_info)

        self.chk_connect_targets = QtWidgets.QCheckBox("Connect extracted targets")
        self.chk_connect_targets.setChecked(True)
        self.chk_connect_targets.setToolTip(
            "After extraction, connects each target weight from the source blendShape\n"
            "to the matching target on the mesh's blendShape.\n"
            "source_bs.target_name  →  mesh_bs.target_name")
        lay_wrap.addWidget(self.chk_connect_targets)

        row_wrap_btns = QtWidgets.QHBoxLayout()
        row_wrap_btns.setSpacing(2)

        _w_wrap, self.btn_wrap_extract = self._icon_btn(
            f"{_icons_dir}/wrap_extract.png",
            "Extract Wrap Targets",
            "Creates a wrap deformer on the selected mesh driven by the blendShape\n"
            "base mesh, extracts each selected target as a shape, then integrates\n"
            "them into the mesh's blendShape node (created if needed).")
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
        lay_wrap.addLayout(row_wrap_btns)

        _w_connect_ab, self.btn_connect_ab = self._icon_btn(
            f"{_icons_dir}/connect_a_b.png",
            "Connect Targets A to B",
            "Select two meshes (source first, target second).\n"
            "Finds the blendShape on each and connects every weight attribute\n"
            "that shares the same target name:  bs_A.name  →  bs_B.name")
        self.btn_connect_ab.clicked.connect(self._run_connect_targets_A_to_B)
        lay_wrap.addWidget(_w_connect_ab)

        grp_wrap.add_compact_action(
            f"{_icons_dir}/wrap_extract.png", "Extract Wrap Targets", self._run_wrap_extract)
        grp_wrap.add_compact_action(
            f"{_icons_dir}/extract_only.png", "Extract Only", self._run_extract_only)
        grp_wrap.add_compact_action(
            f"{_icons_dir}/connect_a_b.png", "Connect Targets A to B",
            self._run_connect_targets_A_to_B)
        root.addWidget(grp_wrap)

        # ── Actions ───────────────────────────────────────────────────────
        grp_act, _body_act, lay_act = self._collapsible_section("Actions")
        lay_act.setSpacing(4)

        row_dup = QtWidgets.QHBoxLayout()
        row_dup.setSpacing(2)
        _w_dup, self.btn_duplicate = self._icon_btn(
            f"{_icons_dir}/duplicate.png", "Duplicate Target",
            "Duplicates each selected target N times.\n"
            "Each copy is suffixed _Copy, _Copy2, _Copy3…")
        self.btn_duplicate.clicked.connect(self._run_duplicate)
        self.spin_duplicate_passes = QtWidgets.QSpinBox()
        self.spin_duplicate_passes.setMinimum(1)
        self.spin_duplicate_passes.setMaximum(20)
        self.spin_duplicate_passes.setValue(1)
        self.spin_duplicate_passes.setFixedWidth(90)
        self.spin_duplicate_passes.setToolTip("Number of duplicates to create")
        row_dup.addWidget(_w_dup, 1)
        row_dup.addWidget(self.spin_duplicate_passes)
        lay_act.addLayout(row_dup)

        row_mirror = QtWidgets.QHBoxLayout()
        row_mirror.setSpacing(2)
        _w_mirror, self.btn_mirror = self._icon_btn(
            f"{_icons_dir}/mirror.png", "Mirror Target",
            "Copies the active target to the opposite side.\n"
            "Uses the direction selected in the combobox.")
        self.btn_mirror.clicked.connect(self._run_mirror)
        self.combo_mirror_dir = QtWidgets.QComboBox()
        self.combo_mirror_dir.addItems(["-", "+"])
        self.combo_mirror_dir.setFixedWidth(90)
        self.combo_mirror_dir.setToolTip("Mirror direction: positive to negative or negative to positive")
        row_mirror.addWidget(_w_mirror, 1)
        row_mirror.addWidget(self.combo_mirror_dir)
        lay_act.addLayout(row_mirror)

        row_flip = QtWidgets.QHBoxLayout()
        row_flip.setSpacing(2)
        _w_flip, self.btn_flip = self._icon_btn(
            f"{_icons_dir}/flip.png", "Flip Target",
            "Flips the target onto itself (internal symmetry).\n"
            "Useful for creating the opposite side of an asymmetric shape.")
        self.btn_flip.clicked.connect(self._run_flip)
        self.combo_flip_axis = QtWidgets.QComboBox()
        self.combo_flip_axis.addItems(["Object X", "Object Y", "Object Z"])
        self.combo_flip_axis.setFixedWidth(90)
        self.combo_flip_axis.setToolTip("Symmetry axis used for the flip operation")
        row_flip.addWidget(_w_flip, 1)
        row_flip.addWidget(self.combo_flip_axis)
        lay_act.addLayout(row_flip)

        row_opp = QtWidgets.QHBoxLayout()
        row_opp.setSpacing(2)
        _w_opp, self.btn_opposite = self._icon_btn(
            f"{_icons_dir}/create_opposite.png", "Create Opposite Target",
            "Duplicates the target, flips it and renames it with the opposite naming convention.\n"
            "Supports L_/R_, lft/rgt, up/dn, fwd/bwd conventions.")
        self.btn_opposite.clicked.connect(self._run_opposite)
        self.combo_opp_axis = QtWidgets.QComboBox()
        self.combo_opp_axis.addItems(["Object X", "Object Y", "Object Z"])
        self.combo_opp_axis.setFixedWidth(90)
        self.combo_opp_axis.setToolTip("Symmetry axis used for the Create Opposite operation")
        row_opp.addWidget(_w_opp, 1)
        row_opp.addWidget(self.combo_opp_axis)
        lay_act.addLayout(row_opp)

        grp_act.add_compact_action(
            f"{_icons_dir}/duplicate.png", "Duplicate Target", self._run_duplicate)
        grp_act.add_compact_action(
            f"{_icons_dir}/mirror.png", "Mirror Target", self._run_mirror)
        grp_act.add_compact_action(
            f"{_icons_dir}/flip.png", "Flip Target", self._run_flip)
        grp_act.add_compact_action(
            f"{_icons_dir}/create_opposite.png", "Create Opposite Target", self._run_opposite)
        root.addWidget(grp_act)

        # ── Modify ────────────────────────────────────────────────────────
        grp_mod, _body_mod, lay_mod = self._collapsible_section("Modify Deltas", initial_state=1, compact_rows=2)
        lay_mod.setSpacing(4)

        lay_mod.addSpacing(10)

        def _hsep():
            sep = QtWidgets.QFrame()
            sep.setFrameShape(QtWidgets.QFrame.HLine)
            sep.setFrameShadow(QtWidgets.QFrame.Sunken)
            return sep

        def _make_factor_field(default="1.0"):
            field = QtWidgets.QLineEdit(default)
            field.setFixedWidth(52)
            field.setAlignment(QtCore.Qt.AlignCenter)
            validator = QtGui.QRegularExpressionValidator(
                QtCore.QRegularExpression(r"-?\d*\.?\d*"), field)
            field.setValidator(validator)
            return field

        # ── Multiply : X Y Z on one row + Multiply Deltas button ─────────────
        self._mult_labels = []
        self._mult_fields = []

        row_mult = QtWidgets.QHBoxLayout()
        row_mult.setSpacing(4)

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
            row_mult.addWidget(lbl)
            row_mult.addWidget(fld)
            if idx < 2:
                row_mult.addSpacing(6)

        for idx, lbl in enumerate(self._mult_labels):
            lbl.clicked.connect(lambda *args, i=idx: self._on_mult_label_click(i))
        for idx, fld in enumerate(self._mult_fields):
            fld.editingFinished.connect(lambda i=idx: self._on_mult_field_edited(i))

        lay_mod.addLayout(row_mult)

        _w_mult, self.btn_mult = self._icon_btn(
            f"{_icons_dir}/multiply.png", "Multiply Deltas",
            "Multiply X/Y/Z delta components directly (object space).\n"
            "1.0 = unchanged   0.0 = zero   -1.0 = invert\n"
            "Click X/Y/Z labels to select axes — Shift+click to multi-select.")
        self.btn_mult.clicked.connect(self._run_multiply)
        lay_mod.addWidget(_w_mult)
        lay_mod.addSpacing(4)
        lay_mod.addWidget(_hsep())
        lay_mod.addSpacing(4)

        # ── Normal Push ───────────────────────────────────────────────────────
        self.field_push_factor = _make_factor_field("0.20")
        self.field_push_factor.setToolTip("Push magnitude relative to existing delta length.")
        self.field_push_factor.setMaximumWidth(16777215)
        self.field_push_factor.setSizePolicy(QtWidgets.QSizePolicy.Expanding,
                                             QtWidgets.QSizePolicy.Fixed)

        self.btn_push_out = QtWidgets.QPushButton("+")
        self.btn_push_out.setCheckable(True)
        self.btn_push_out.setChecked(True)
        self.btn_push_out.setFixedHeight(22)
        self.btn_push_out.setToolTip("Outward — push along positive normal")

        self.btn_push_in = QtWidgets.QPushButton("\u2212")
        self.btn_push_in.setCheckable(True)
        self.btn_push_in.setFixedHeight(22)
        self.btn_push_in.setToolTip("Inward — push along negative normal")

        self._push_dir_group = QtWidgets.QButtonGroup(self)
        self._push_dir_group.addButton(self.btn_push_out)
        self._push_dir_group.addButton(self.btn_push_in)
        self._push_dir_group.setExclusive(True)

        push_icon = QtWidgets.QLabel()
        _px = QtGui.QPixmap(f"{_icons_dir}/normal_push.png")
        if not _px.isNull():
            push_icon.setPixmap(_px.scaled(32, 32, QtCore.Qt.KeepAspectRatio,
                                           QtCore.Qt.SmoothTransformation))
        push_icon.setFixedWidth(40)
        push_icon.setAlignment(QtCore.Qt.AlignCenter)

        btn_push = QtWidgets.QPushButton("Normal Push")
        btn_push.setToolTip("Add displacement along vertex normals,\n"
                            "weighted by existing delta magnitude.\n"
                            "Only vertices with existing deltas are affected.")
        btn_push.clicked.connect(self._run_push_normals)
        btn_push.setSizePolicy(QtWidgets.QSizePolicy.Expanding,
                               QtWidgets.QSizePolicy.Expanding)

        row_pm = QtWidgets.QHBoxLayout()
        row_pm.setSpacing(4)
        row_pm.setContentsMargins(0, 0, 0, 0)
        row_pm.addWidget(self.btn_push_out)
        row_pm.addWidget(self.btn_push_in)

        push_left = QtWidgets.QVBoxLayout()
        push_left.setSpacing(4)
        push_left.setContentsMargins(0, 0, 0, 0)
        push_left.addWidget(self.field_push_factor)
        push_left.addLayout(row_pm)

        push_left_w = QtWidgets.QWidget()
        push_left_w.setLayout(push_left)
        push_left_w.setFixedWidth(60)
        push_left_w.setSizePolicy(QtWidgets.QSizePolicy.Fixed,
                                  QtWidgets.QSizePolicy.Expanding)

        push_outer = QtWidgets.QHBoxLayout()
        push_outer.setSpacing(4)
        push_outer.setContentsMargins(0, 0, 0, 0)
        push_outer.addWidget(push_icon)
        push_outer.addWidget(push_left_w)
        push_outer.addWidget(btn_push, 1)

        lay_mod.addLayout(push_outer)
        lay_mod.addSpacing(4)
        lay_mod.addWidget(_hsep())
        lay_mod.addSpacing(4)

        # ── Smooth / Relax ────────────────────────────────────────────────────
        row_opacity = QtWidgets.QHBoxLayout()
        row_opacity.setSpacing(4)
        lbl_opacity = QtWidgets.QLabel("Opacity")
        lbl_opacity.setFixedWidth(52)
        self.slider_smooth_opacity = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.slider_smooth_opacity.setRange(1, 100)
        self.slider_smooth_opacity.setValue(50)
        self.slider_smooth_opacity.setToolTip(
            "Smoothing strength: maps to 1–10 iterative passes.\n"
            "100 = 10 passes (very powerful)   1 = 1 pass (subtle).")
        self.lbl_smooth_opacity_val = QtWidgets.QLabel("0.50")
        self.lbl_smooth_opacity_val.setFixedWidth(30)
        self.slider_smooth_opacity.valueChanged.connect(
            lambda v: self.lbl_smooth_opacity_val.setText(f"{v/100:.2f}"))
        row_opacity.addWidget(lbl_opacity)
        row_opacity.addWidget(self.slider_smooth_opacity)
        row_opacity.addWidget(self.lbl_smooth_opacity_val)
        lay_mod.addLayout(row_opacity)

        row_sr = QtWidgets.QHBoxLayout()
        row_sr.setSpacing(2)
        _w_smt, self.btn_smooth = self._icon_btn(
            f"{_icons_dir}/smooth_delta.png", "Smooth Deltas",
            "Laplacian smoothing of the delta field.\n"
            "Each vertex is replaced by the average of its neighbors' deltas.\n"
            "Works on vertex selection or full target (no selection).\n"
            "Opacity maps to 1–10 iterative passes.")
        self.btn_smooth.clicked.connect(self._run_smooth_deltas)
        _w_rlx, self.btn_relax = self._icon_btn(
            f"{_icons_dir}/smooth_delta.png", "Relax Deltas",
            "Relaxes the delta field by averaging 3D positions in deformed space.\n"
            "Like a mesh relax, but applied only to the blendShape target.\n"
            "Works on vertex selection or full target (no selection).\n"
            "Opacity maps to 1–10 iterative passes.")
        self.btn_relax.clicked.connect(self._run_relax_deltas)
        row_sr.addWidget(_w_smt)
        row_sr.addWidget(_w_rlx)
        lay_mod.addLayout(row_sr)
        lay_mod.addSpacing(4)
        lay_mod.addWidget(_hsep())
        lay_mod.addSpacing(4)

        # ── Copy / Paste — utility row ──────────────────────────────────────
        row_cps = QtWidgets.QHBoxLayout()
        row_cps.setSpacing(2)

        _w_copy_delta, self.btn_copy_delta = self._icon_btn(
            f"{_icons_dir}/copy_delta.png", "Copy Delta",
            "Copies the delta of the single selected vertex on the active target.\n"
            "The value is stored until a new Copy or tool restart.")
        self.btn_copy_delta.clicked.connect(self._run_copy_delta)

        _w_paste_delta, self.btn_paste_delta = self._icon_btn(
            f"{_icons_dir}/paste_delta.png", "Paste Delta",
            "Pastes the copied delta onto all selected vertices on the active target.\n"
            "Undoable.")
        self.btn_paste_delta.setEnabled(False)
        self.btn_paste_delta.clicked.connect(self._run_paste_delta)

        _w_sel_delta, self.btn_sel_delta = self._icon_btn(
            f"{_icons_dir}/select_delta.png", "Select Delta Verts",
            "Selects all vertices that have non-zero deltas on the active target.")
        self.btn_sel_delta.clicked.connect(self._run_select_delta_vertices)

        row_cps.addWidget(_w_copy_delta)
        row_cps.addWidget(_w_paste_delta)
        row_cps.addWidget(_w_sel_delta)
        row_cps.addStretch()
        lay_mod.addLayout(row_cps)
        lay_mod.addSpacing(4)
        lay_mod.addWidget(_hsep())
        lay_mod.addSpacing(4)

        # ── Prune Small Deltas ────────────────────────────────────────────────
        row_prune = QtWidgets.QHBoxLayout()
        row_prune.setSpacing(4)

        _w_prune, self.btn_prune = self._icon_btn(
            f"{_icons_dir}/prune_delta.png", "Prune Small Deltas",
            "Zeros out deltas whose magnitude is below the tolerance threshold.")
        self.btn_prune.clicked.connect(self._run_prune_deltas)

        self.spin_prune_tol = QtWidgets.QDoubleSpinBox()
        self.spin_prune_tol.setRange(0.001, 10.0)
        self.spin_prune_tol.setValue(0.001)
        self.spin_prune_tol.setSingleStep(0.001)
        self.spin_prune_tol.setDecimals(3)
        self.spin_prune_tol.setFixedWidth(75)
        self.spin_prune_tol.setLocale(QtCore.QLocale(QtCore.QLocale.English))
        self.spin_prune_tol.setToolTip("Tolerance — deltas with magnitude below this value are zeroed out.")

        row_prune.addWidget(_w_prune, 1)
        row_prune.addWidget(self.spin_prune_tol)
        lay_mod.addLayout(row_prune)
        lay_mod.addSpacing(4)
        lay_mod.addWidget(_hsep())
        lay_mod.addSpacing(4)

        # ── Create Delta Cluster — button ──────────────────────────
        _w_dc, self.btn_delta_cluster = self._icon_btn(
            f"{_icons_dir}/delta_cluster.png", "Create Delta Cluster",
            "Duplicates the target as a posed mesh and creates a cluster\n"
            "with weights matching the delta magnitudes of the shape.")
        self.btn_delta_cluster.clicked.connect(self._run_delta_cluster)
        lay_mod.addWidget(_w_dc)

        # ── Create Delta Joint — button ──────────────────────────
        _w_dj, self.btn_delta_joint = self._icon_btn(
            f"{_icons_dir}/delta_joint.png", "Create Delta Joint",
            "Duplicates the target as a posed mesh and binds two joints:\n"
            "  - {target}_jnt       : weights = normalized delta magnitudes\n"
            "  - {target}_zero_jnt  : absorbs remaining weights\n"
            "Everything is grouped under {target}_grp.")
        self.btn_delta_joint.clicked.connect(self._run_delta_joint)
        lay_mod.addWidget(_w_dj)

        grp_mod.add_compact_action(
            f"{_icons_dir}/multiply.png",      "Multiply Deltas",       self._run_multiply)
        grp_mod.add_compact_action(
            f"{_icons_dir}/normal_push.png",   "Normal Push",           self._run_push_normals)
        grp_mod.add_compact_action(
            f"{_icons_dir}/smooth_delta.png",  "Smooth Deltas",         self._run_smooth_deltas)
        grp_mod.add_compact_action(
            f"{_icons_dir}/smooth_delta.png",  "Relax Deltas",          self._run_relax_deltas)
        grp_mod.add_compact_action(
            f"{_icons_dir}/copy_delta.png",    "Copy Delta",            self._run_copy_delta)
        grp_mod.add_compact_action(
            f"{_icons_dir}/paste_delta.png",   "Paste Delta",           self._run_paste_delta)
        grp_mod.add_compact_action(
            f"{_icons_dir}/select_delta.png",  "Select Delta Verts",    self._run_select_delta_vertices)
        grp_mod.add_compact_action(
            f"{_icons_dir}/prune_delta.png",   "Prune Small Deltas",    self._run_prune_deltas)
        grp_mod.add_compact_action(
            f"{_icons_dir}/delta_cluster.png", "Create Delta Cluster",  self._run_delta_cluster)
        grp_mod.add_compact_action(
            f"{_icons_dir}/delta_joint.png",   "Create Delta Joint",    self._run_delta_joint)
        root.addWidget(grp_mod)

        # ── Tools ─────────────────────────────────────────────────────────────
        grp_tools, _body_tools, lay_tools = self._collapsible_section("Tools")
        lay_tools.setSpacing(6)

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
        root.addWidget(grp_tools)

        root.addStretch(1)

        scroll.setWidget(inner)

        # ── Status + Version pinned below scroll — always visible ──────────
        self.lbl_status = QtWidgets.QLabel("")
        self.lbl_status.setAlignment(QtCore.Qt.AlignCenter)

        lbl_version = QtWidgets.QLabel(self.VERSION)
        lbl_version.setAlignment(QtCore.Qt.AlignRight)
        lbl_version.setStyleSheet("color: #7a7a7a; font-size: 10px;")

        bottom_wrapper = QtWidgets.QWidget()
        bottom_lay = QtWidgets.QVBoxLayout(bottom_wrapper)
        bottom_lay.setContentsMargins(8, 2, 8, 4)
        bottom_lay.setSpacing(0)
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
        QtCore.QTimer.singleShot(0, self.adjustSize)

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

        # suffixes store only the letter part — prefix (R_/L_/M_) is handled in _run_split
        # n=1 : [""]          → R_name   L_name
        # n=2 : ["", ""]      → R_name   L_name
        # n=3 : ["", "", ""]  → R_name   M_name  L_name
        # n=4 : ["_b","_a","_a","_b"] → R_name_b  R_name_a  L_name_a  L_name_b
        # n=5 : ["_b","_a","","_a","_b"] → R_name_b  R_name_a  M_name  L_name_a  L_name_b

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
        if n == 1:
            sides = [""]
        elif n == 2:
            sides = ["R", "L"]
        elif n == 3:
            sides = ["R", "M", "L"]
        elif n % 2 == 1:
            half = n // 2
            sides = (["R"] * half) + ["M"] + (["L"] * half)
        else:
            half = n // 2
            sides = (["R"] * half) + (["L"] * half)

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

        # ── Actions ───────────────────────────────────────────────────────────
        self.spin_duplicate_passes.setValue(1)
        self.combo_mirror_dir.setCurrentIndex(0)
        self.combo_flip_axis.setCurrentIndex(0)
        self.combo_opp_axis.setCurrentIndex(0)

        # ── Modify Deltas ─────────────────────────────────────────────────────
        self.slider_smooth_opacity.blockSignals(True)
        self.slider_smooth_opacity.setValue(50)
        self.slider_smooth_opacity.blockSignals(False)
        self.lbl_smooth_opacity_val.setText("0.50")

        self.spin_prune_tol.blockSignals(True)
        self.spin_prune_tol.setValue(0.001)
        self.spin_prune_tol.blockSignals(False)

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
                (0.080, 0.500),   # leaving cyan  — pic étroit autour de hue=0.5
                (0.100, 0.333),   # green
                (0.500, 0.167),   # yellow
                (0.625, 0.100),   # yellow-orange
                (0.9500, 0.050),   # orange — plage élargie
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
        row = self.table.currentRow()
        if row <= 0:
            return
        self._swap_rows(row, row - 1)
        self.table.selectRow(row - 1)

    def _move_row_down(self):
        row = self.table.currentRow()
        if row < 0 or row >= self.table.rowCount() - 1:
            return
        self._swap_rows(row, row + 1)
        self.table.selectRow(row + 1)

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

    def _set_status(self, msg, error=False):
        self.lbl_status.setText(msg)
        color = "#e05252" if error else "#7ec87e"
        self.lbl_status.setStyleSheet(f"color: {color};")

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
          {side}   → e.g. "R", "L", "M"  (empty string = token skipped)
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

    def _open_naming_convention(self):
        dlg = NamingConventionDialog(parent_ui=self)
        dlg.exec_()

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

                # Zero all blendShape weights once before the split loop
                for attr in (cmds.listAttr(f"{bs_node}.w", multi=True) or []):
                    cmds.setAttr(f"{bs_node}.{attr}", 0.0)

                # Build the list of (loc_idx, final_name) pairs to create
                if symmetric:
                    # Strip existing L_/R_/M_ prefix so we can rebuild with the correct side
                    base_name = target_name
                    for pfx in ("L_", "R_", "M_", "l_", "r_", "m_"):
                        if target_name.startswith(pfx):
                            base_name = target_name[len(pfx):]
                            break
                    if n_locs == 1:
                        pairs = [(i, self._build_target_name(base_name, sv, ""))
                                 for i, sv in enumerate(["R", "L"])]
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
                for loc_idx, final_name in pairs:
                    idx = create_split_target(bs_node, base_mesh, final_name,
                                              logical_index, loc_idx, weights, deltas)
                    new_indices.append(idx)
                    total += 1

            self._set_status(f"✓ {total} target{'s' if total > 1 else ''} created")
        except Exception as e:
            traceback.print_exc()
            self._set_status(f"✗ {e}", error=True)
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
    def _run_multiply(self):
        raw_sel   = cmds.ls(sl=True, flatten=True) or []
        vtx_sel   = [s for s in raw_sel if ".vtx[" in s]
        all_verts = not vtx_sel

        targets = self._get_targets_or_warn()
        if not targets:
            return

        fx = self._parse_factor(self._mult_fields[0])
        fy = self._parse_factor(self._mult_fields[1])
        fz = self._parse_factor(self._mult_fields[2])

        vtx_indices        = None if all_verts else [int(s.split(".vtx[")[1].rstrip("]")) for s in vtx_sel]
        targets_to_process = targets if all_verts else [targets[0]]

        try:
            for bs_node, logical_index, target_name in targets_to_process:
                multiply_target_deltas(bs_node, logical_index, fx, fy, fz,
                                       vtx_indices=vtx_indices)
            scope = "all verts" if all_verts else f"{len(vtx_indices)} vtx"
            n_t   = len(targets_to_process)
            if fx == 0.0 and fy == 0.0 and fz == 0.0:
                self._set_status(
                    f"Deltas wiped on {n_t} target{'s' if n_t > 1 else ''}  {scope}"
                    f" — Ctrl+Z to undo", error=True)
            else:
                self._set_status(
                    f"Multiplied {n_t} target{'s' if n_t > 1 else ''}"
                    f"  X\xd7{fx} Y\xd7{fy} Z\xd7{fz}  {scope}")
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

        factor      = self._parse_factor(self.field_push_factor)
        if self.btn_push_in.isChecked():
            factor = -factor

        vtx_indices        = None if all_verts else [int(s.split(".vtx[")[1].rstrip("]")) for s in vtx_sel]
        targets_to_process = targets if all_verts else [targets[0]]

        try:
            for bs_node, logical_index, target_name in targets_to_process:
                push_normals_deltas(bs_node, logical_index, factor,
                                    vtx_indices=vtx_indices)
            scope     = "all verts" if all_verts else f"{len(vtx_indices)} vtx"
            n_t       = len(targets_to_process)
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
        vtx_indices        = None if all_verts else [int(s.split(".vtx[")[1].rstrip("]")) for s in vtx_sel]
        targets_to_process = targets if all_verts else [targets[0]]

        try:
            for bs_node, logical_index, target_name in targets_to_process:
                smooth_target_deltas(bs_node, logical_index, opacity,
                                     vtx_indices=vtx_indices)
            n_passes = max(1, int(round(opacity * 10)))
            scope = "all verts" if all_verts else f"{len(vtx_indices)} vtx"
            n_t   = len(targets_to_process)
            self._set_status(
                f"Smooth Deltas {n_t} target{'s' if n_t > 1 else ''}"
                f"  {n_passes} pass{'es' if n_passes > 1 else ''}  {scope}")
        except Exception as e:
            traceback.print_exc()
            self._set_status(f"✗ {e}", error=True)

    @undo_chunk
    def _run_relax_deltas(self):
        raw_sel   = cmds.ls(sl=True, flatten=True) or []
        vtx_sel   = [s for s in raw_sel if ".vtx[" in s]
        all_verts = not vtx_sel

        targets = self._get_targets_or_warn()
        if not targets:
            return

        opacity = self.slider_smooth_opacity.value() / 100.0
        vtx_indices        = None if all_verts else [int(s.split(".vtx[")[1].rstrip("]")) for s in vtx_sel]
        targets_to_process = targets if all_verts else [targets[0]]

        try:
            for bs_node, logical_index, target_name in targets_to_process:
                relax_target_deltas(bs_node, logical_index, opacity,
                                    vtx_indices=vtx_indices)
            n_passes = max(1, int(round(opacity * 10)))
            scope = "all verts" if all_verts else f"{len(vtx_indices)} vtx"
            n_t   = len(targets_to_process)
            self._set_status(
                f"Relax Deltas {n_t} target{'s' if n_t > 1 else ''}"
                f"  {n_passes} pass{'es' if n_passes > 1 else ''}  {scope}")
        except Exception as e:
            traceback.print_exc()
            self._set_status(f"✗ {e}", error=True)

    @undo_chunk
    def _run_opposite(self):
        try:
            create_opposite_shape(symmetry_axis=self.combo_opp_axis.currentText())
            self._set_status("✓ Opposite(s) created")
        except Exception as e:
            traceback.print_exc()
            self._set_status(f"✗ {e}", error=True)
    @undo_chunk
    def _run_edge_loop_split(self):
        """
        UI handler for Edge Loop Split.
        Select: edge loop + 2 vertices (1 upper, 1 lower) then click the button.
        """
        targets = self._get_targets_or_warn()
        if not targets:
            return
        bs_node, logical_index, target_name = targets[0]
        if len(targets) > 1:
            self._set_status(
                f"Edge Loop Split: using first target '{target_name}' only")

        sel_flat = cmds.ls(sl=True, flatten=True) or []
        edges    = [s for s in sel_flat if ".e["   in s]
        seeds    = [s for s in sel_flat if ".vtx[" in s]

        if not edges:
            self._set_status(
                "✗ Edge Loop Split: select edge loop + 2 vertices (one per side)", error=True)
            return
        if len(seeds) < 2:
            self._set_status(
                f"✗ Edge Loop Split: need 2 vertices — 1 upper + 1 lower "
                f"({len(seeds)} selected)", error=True)
            return

        seed_a = int(seeds[0].split(".vtx[")[1].rstrip("]"))
        seed_b = int(seeds[1].split(".vtx[")[1].rstrip("]"))

        seam_edges = set()
        seam_vis   = set()
        for e in edges:
            info = cmds.polyInfo(e, edgeToVertex=True)
            if info:
                parts = info[0].split()
                a, b  = int(parts[2]), int(parts[3])
                seam_vis.add(a);  seam_vis.add(b)
                seam_edges.add(frozenset({a, b}))

        if seed_a in seam_vis or seed_b in seam_vis:
            self._set_status("✗ Edge Loop Split: seed vertices must not be on the seam", error=True)
            return

        radius      = max(1, int(self.spin_radius.value())) if self.chk_radius.isChecked() else 1
        curve_name  = self.combo_curve.currentText()
        falloff_fn  = CURVE_FUNCTIONS.get(curve_name, linear)

        try:
            # Zero all blendShape weights so the original target is at 0
            for attr in (cmds.listAttr(f"{bs_node}.w", multi=True) or []):
                cmds.setAttr(f"{bs_node}.{attr}", 0.0)

            upper_idx, lower_idx = edge_loop_split_target(
                bs_node, logical_index, target_name,
                seam_edges, seed_a, seed_b,
                falloff_radius=radius, falloff_func=falloff_fn)
            self._set_status(
                f"✓ Edge Loop Split : '{target_name}_upper' + '{target_name}_lower'"
                f"  (radius={radius}, curve={curve_name}, seam={len(seam_edges)} edges)")
        except Exception as e:
            import traceback
            traceback.print_exc()
            self._set_status(f"✗ Edge Loop Split: {e}", error=True)
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

    @undo_chunk
    def _run_duplicate(self):
        targets = self._get_targets_or_warn()
        if not targets:
            return

        passes = self.spin_duplicate_passes.value()
        try:
            total = 0
            for bs_node, logical_index, target_name in targets:
                base_mesh = get_base_mesh(bs_node)
                if not base_mesh:
                    continue
                for n in range(passes):
                    suffix   = "_Copy" if n == 0 else f"_Copy{n + 1}"
                    new_name = f"{target_name}{suffix}"
                    duplicate_target(bs_node, base_mesh, logical_index, new_name)
                    print(f"  ✓ Duplicated : {new_name}")
                    total += 1
            self._set_status(f"✓ {total} duplicate{'s' if total > 1 else ''} created")
        except Exception as e:
            traceback.print_exc()
            self._set_status(f"✗ {e}", error=True)
    @undo_chunk
    def _run_flip(self):
        targets = self._get_targets_or_warn()
        if not targets:
            return
        direction = 0 if self.combo_mirror_dir.currentIndex() == 0 else 1

        try:
            flip_axis = self.combo_flip_axis.currentText()
            for bs_node, logical_index, _ in targets:
                base_mesh   = get_base_mesh(bs_node)
                base_shapes = cmds.listRelatives(base_mesh, shapes=True, type="mesh", fullPath=True)
                base_shape  = base_shapes[0] if base_shapes else base_mesh
                do_flip_target(bs_node, logical_index, base_shape, direction, flip_axis)
            self._set_status(f"✓ Flip on {len(targets)} target{'s' if len(targets) > 1 else ''}")
        except Exception as e:
            traceback.print_exc()
            self._set_status(f"✗ {e}", error=True)
    @undo_chunk
    def _run_mirror(self):
        targets = self._get_targets_or_warn()
        if not targets:
            return
        direction = 0 if self.combo_mirror_dir.currentIndex() == 0 else 1

        try:
            for bs_node, logical_index, _ in targets:
                base_mesh   = get_base_mesh(bs_node)
                base_shapes = cmds.listRelatives(base_mesh, shapes=True, type="mesh", fullPath=True)
                base_shape  = base_shapes[0] if base_shapes else base_mesh
                do_mirror_target(bs_node, logical_index, base_shape, direction)
            self._set_status(f"✓ Mirror on {len(targets)} target{'s' if len(targets) > 1 else ''}")
        except Exception as e:
            traceback.print_exc()
            self._set_status(f"✗ {e}", error=True)
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
        tolerance = self.spin_prune_tol.value()
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

        try:
            for bs_node, logical_index, target_name in targets:
                create_delta_joint(bs_node, logical_index, target_name)
            self._set_status(f"✓ {len(targets)} joint group{'s' if len(targets) > 1 else ''} created")
        except Exception as e:
            traceback.print_exc()
            self._set_status(f"✗ {e}", error=True)
    @undo_chunk
    def _run_delta_cluster(self):
        targets = self._get_targets_or_warn()
        if not targets:
            return

        try:
            for bs_node, logical_index, target_name in targets:
                create_delta_cluster(bs_node, logical_index, target_name)
            self._set_status(f"✓ {len(targets)} cluster{'s' if len(targets) > 1 else ''} created")
        except Exception as e:
            traceback.print_exc()
            self._set_status(f"✗ {e}", error=True)

    @undo_chunk
    def _run_wrap_extract(self):
        targets = self._get_targets_or_warn()
        if not targets:
            return

        # All targets must be on the same bs_node
        bs_nodes = list({t[0] for t in targets})
        if len(bs_nodes) > 1:
            self._set_status(
                "✗ Wrap Extract: all selected targets must be on the same blendShape node",
                error=True)
            return

        bs_node   = bs_nodes[0]
        base_mesh = get_base_mesh(bs_node)
        if not base_mesh:
            self._set_status(f"✗ Wrap Extract: cannot find base mesh for '{bs_node}'", error=True)
            return

        # Get mesh_target from Maya scene selection
        sel    = cmds.ls(sl=True, type='transform') or []
        meshes = [s for s in sel if cmds.listRelatives(s, shapes=True, type='mesh')]
        if not meshes:
            self._set_status(
                "✗ Wrap Extract: select a mesh in the scene to use as wrap target", error=True)
            return
        if len(meshes) > 1:
            self._set_status(
                "✗ Wrap Extract: select only one mesh as wrap target", error=True)
            return

        mesh_target = meshes[0]
        if mesh_target == base_mesh:
            self._set_status(
                "✗ Wrap Extract: the selected mesh cannot be the base mesh itself", error=True)
            return

        try:
            bs_target, log = extract_targets_via_wrap(
                bs_node, base_mesh, mesh_target, targets)
            n_total    = len(log)
            n_replaced = sum(1 for _, r in log if r)
            n_added    = n_total - n_replaced
            parts = [f"✓ Wrap Extract: {n_total} target{'s' if n_total > 1 else ''} → '{bs_target}'"]
            if n_added:
                parts.append(f"{n_added} added")
            if n_replaced:
                parts.append(f"{n_replaced} replaced")
            if self.chk_connect_targets.isChecked():
                names = [name for name, _ in log]
                connect_extracted_targets(bs_node, bs_target, names)
                parts.append("connected")
            self._set_status("  ".join(parts))
        except Exception as e:
            traceback.print_exc()
            self._set_status(f"✗ Wrap Extract: {e}", error=True)

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

    # If the workspace control already exists just raise it
    if cmds.workspaceControl(WS_CTRL, q=True, exists=True):
        cmds.workspaceControl(WS_CTRL, edit=True, restore=True)
        return

    _win = BlendshapeEditorUI()
    _win.show(dockable=True, floating=True, retain=False)


show()