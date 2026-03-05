from maya import cmds, mel
import math
import functools
import traceback


def undo_chunk(func):
    """
    Decorator that wraps a UI action in a single Maya undo chunk.
    Any number of Maya operations performed during the call will be
    collapsed into one step so that a single Ctrl+Z reverts them all.
    """
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        cmds.undoInfo(openChunk=True, chunkName=func.__name__)
        try:
            return func(*args, **kwargs)
        finally:
            cmds.undoInfo(closeChunk=True, chunkName=func.__name__)
    return wrapper


# ─────────────────────────────────────────────────────────────────────────────
# CORE FUNCTIONS
# ─────────────────────────────────────────────────────────────────────────────

def smoother_step(t):
    t = max(0.0, min(1.0, t))
    return t * t * t * (t * (t * 6.0 - 15.0) + 10.0)

def linear(t):
    return max(0.0, min(1.0, t))

def ease_in(t):
    t = max(0.0, min(1.0, t))
    return t * t

def ease_out(t):
    t = max(0.0, min(1.0, t))
    return t * (2.0 - t)

CURVE_FUNCTIONS = {
    "Smoother Step": smoother_step,
    "Linear":        linear,
    "Ease In":       ease_in,
    "Ease Out":      ease_out,
}


def _save_shape_editor_selection():
    """
    Saves the current Shape Editor selection.
    Returns the raw list of strings from getShapeEditorTreeviewSelection(4).
    """
    try:
        return mel.eval('getShapeEditorTreeviewSelection(4)') or []
    except Exception:
        return []


def _restore_shape_editor_selection(saved):
    """
    No-op placeholder — Maya has no public MEL command to restore Shape Editor selection.
    We keep the save/restore API so callers don't need to change, but restoration
    is handled passively: we simply avoid doing anything that clears the selection.
    """
    pass


def get_selected_targets():
    """Returns a list of (bs_node, logical_index, target_name) for all selected targets."""
    selection = mel.eval('getShapeEditorTreeviewSelection(4)')
    if not selection:
        return []
    results = []
    for entry in selection:
        parts = entry.split('.')
        bs_node       = parts[0]
        logical_index = int(parts[-1])
        target_name   = cmds.aliasAttr(f'{bs_node}.w[{logical_index}]', q=True)
        if target_name:
            results.append((bs_node, logical_index, target_name))
    return results


def get_base_mesh(bs_node):
    # Primary: blendShape -q -geometry returns the deformed shape directly,
    # bypassing intermediate nodes (groupParts, tweak, etc.)
    geo = cmds.blendShape(bs_node, q=True, geometry=True)
    if geo:
        shape = geo[0]
        if cmds.nodeType(shape) == "mesh":
            parent = cmds.listRelatives(shape, parent=True, fullPath=True)
            return parent[0] if parent else shape
        return shape

    # Fallback: traverse input[0].inputGeometry, skip deformer utility nodes
    plug = f"{bs_node}.input[0].inputGeometry"
    for _ in range(10):  # limit traversal depth
        conns = cmds.listConnections(plug, source=True, destination=False) or []
        if not conns:
            break
        node = conns[0]
        node_type = cmds.nodeType(node)
        if node_type == "mesh":
            parent = cmds.listRelatives(node, parent=True, fullPath=True)
            return parent[0] if parent else node
        if node_type in ("groupParts", "tweak", "groupId"):
            plug = f"{node}.inputGeometry"
            continue
        return node  # transform or other — return as-is

    # Last resort: output side
    conns = cmds.listConnections(
        f"{bs_node}.outputGeometry[0]", source=False, destination=True
    ) or []
    if conns:
        node = conns[0]
        if cmds.nodeType(node) == "mesh":
            parent = cmds.listRelatives(node, parent=True, fullPath=True)
            return parent[0] if parent else node
        return node
    return None

@undo_chunk
def add_empty_target(bs_node, name=None):
    """
    Adds a new empty (zero-delta) target to bs_node and enters sculpt mode.

    The only way to add an internal target is to supply a physical mesh then
    delete it immediately.  Order matters:
      1. blendShape -e -target (base_transform, idx, duplicate, 1.0)
      2. delete duplicate          ← before resetTargetDelta
      3. blendShape -e -rtd        ← cleans up any residual deltas
      4. aliasAttr                 ← name the weight
      5. blendShape -e -weight 1.0 ← required before sculptTarget
      6. sculptTarget -e -target   ← enter sculpt mode
    """
    base_mesh = get_base_mesh(bs_node)
    if not base_mesh:
        raise RuntimeError(f"Could not find base mesh for {bs_node}")

    used = cmds.getAttr(f"{bs_node}.weight", multiIndices=True) or []
    new_idx = (max(used) + 1) if used else 0

    temp = cmds.duplicate(base_mesh, name="_add_target_tmp")[0]
    cmds.blendShape(bs_node, edit=True, topologyCheck=True,
                    target=(base_mesh, new_idx, temp, 1.0))
    cmds.delete(temp)
    cmds.blendShape(bs_node, edit=True, resetTargetDelta=(0, new_idx))

    if name is None:
        name = f"target_{new_idx}"
    cmds.aliasAttr(name, f"{bs_node}.weight[{new_idx}]")

    cmds.blendShape(bs_node, edit=True, weight=(new_idx, 1.0))
    cmds.sculptTarget(bs_node, edit=True, target=new_idx)

    return new_idx, name


def get_vtx_world_positions(mesh):
    shapes = cmds.listRelatives(mesh, shapes=True, type="mesh")
    mesh_shape = shapes[0] if shapes else mesh
    n_verts = cmds.polyEvaluate(mesh_shape, vertex=True)
    return [cmds.pointPosition(f"{mesh_shape}.vtx[{i}]", world=True) for i in range(n_verts)]


def get_locator_local_axes(loc):
    """
    Returns the 3 local axes of a locator in world space as unit vectors.
    m = cmds.xform(loc, q=True, ws=True, m=True) returns a flat 16-element list:
      [ax, ay, az, 0,  bx, by, bz, 0,  cx, cy, cz, 0,  tx, ty, tz, 1]
    where (ax,ay,az) = local X, (bx,by,bz) = local Y, (cx,cy,cz) = local Z in world space.
    """
    m  = cmds.xform(loc, q=True, ws=True, m=True)
    def norm(v):
        l = math.sqrt(v[0]*v[0] + v[1]*v[1] + v[2]*v[2])
        return (v[0]/l, v[1]/l, v[2]/l) if l > 1e-6 else (1.0, 0.0, 0.0)
    local_x = norm((m[0], m[1], m[2]))
    local_y = norm((m[4], m[5], m[6]))
    local_z = norm((m[8], m[9], m[10]))
    return local_x, local_y, local_z


def compute_weights(vtx_positions, loc_positions, delta_indices, falloff_func, axes, radius=1.0, radial=False, loc_axes=None, invert_axis=False):
    """
    axes   : tuple (use_x, use_z, use_y)
    radial : if True, uses euclidean distance instead of 1D projection

    1 locator  -> 2 weights : [w_in, w_out]
    N locators -> N weights : [w0, w1, ..., wN-1]

    Principe de projection :
      L'axe coché (X/Y/Z) désigne quelle colonne de la matrice du locator
      sert de direction de projection. Résultat identique quel que soit l'axe
      coché si le locator est orienté pareil dans la scène.
      Toutes les projections sont en coordonnées monde absolues (origine world),
      ce qui garantit des comparaisons cohérentes pour le clamping.
    """
    n_locs  = len(loc_positions)
    use_x, use_z, use_y = axes

    # ── Helpers ───────────────────────────────────────────────────────────────
    WORLD_AXES = ((1.0,0.0,0.0), (0.0,1.0,0.0), (0.0,0.0,1.0))

    def dot(v, axis):
        return v[0]*axis[0] + v[1]*axis[1] + v[2]*axis[2]

    def normalize_w(w):
        t = sum(w)
        return [wi/t for wi in w] if t > 1e-6 else [1.0/len(w)]*len(w)

    def get_axes(i):
        """Axes locaux du locator i, ou world axes si loc_axes absent."""
        if loc_axes and i < len(loc_axes):
            return loc_axes[i]
        if loc_axes:
            return loc_axes[-1]
        return WORLD_AXES

    def proj1d(p, ax_x, ax_y, ax_z):
        """
        Projects p (absolute world coords) onto the active axis of the frame.
        invert_axis negates the result, flipping which side is "in" vs "out".
        """
        if use_x:   raw = dot(p, ax_x)
        elif use_z: raw = dot(p, ax_z)
        elif use_y: raw = dot(p, ax_y)
        else:       raw = 0.0
        return -raw if invert_axis else raw

    def dist_in_frame(p, ref, ax_x, ax_y, ax_z):
        """Distance euclidienne entre p et ref dans les axes cochés."""
        dx = p[0]-ref[0]; dy = p[1]-ref[1]; dz = p[2]-ref[2]
        v  = (dx, dy, dz)
        d2 = 0.0
        if use_x: d2 += dot(v, ax_x)**2
        if use_y: d2 += dot(v, ax_y)**2
        if use_z: d2 += dot(v, ax_z)**2
        return math.sqrt(d2)

    # ── Radial IDW ────────────────────────────────────────────────────────────
    if radial:
        if n_locs == 1:
            ax_x, ax_y, ax_z = get_axes(0)
            weights = {}
            for vi in delta_indices:
                p = vtx_positions[vi]
                d = dist_in_frame(p, loc_positions[0], ax_x, ax_y, ax_z)
                if d <= 0.0:
                    weights[vi] = [1.0, 0.0]
                elif d >= radius:
                    weights[vi] = [0.0, 1.0]
                else:
                    t = d / radius
                    w1 = falloff_func(t)
                    weights[vi] = [1.0 - w1, w1]
            return weights

        weights = {}
        for vi in delta_indices:
            p = vtx_positions[vi]
            raw = []
            for i, lp in enumerate(loc_positions):
                ax_x, ax_y, ax_z = get_axes(i)
                d = dist_in_frame(p, lp, ax_x, ax_y, ax_z)
                raw.append(d)
            if any(d < 1e-6 for d in raw):
                w = [1.0 if d < 1e-6 else 0.0 for d in raw]
            else:
                inv = [falloff_func(1.0/(d**2)) for d in raw]
                w   = normalize_w(inv)
            weights[vi] = normalize_w(w)
        return weights

    # ── 1 locator — projection 1D ─────────────────────────────────────────────
    # proj1d retourne des coordonnées monde absolues.
    # loc_1d = position du locator projetée → sert de centre de la zone.
    # in_end / out_start = bornes de la zone de transition autour du locator.
    if n_locs == 1:
        ax_x, ax_y, ax_z = get_axes(0)
        loc_1d    = proj1d(loc_positions[0], ax_x, ax_y, ax_z)
        in_end    = loc_1d - radius
        out_start = loc_1d + radius
        weights   = {}
        for vi in delta_indices:
            v_1d = proj1d(vtx_positions[vi], ax_x, ax_y, ax_z)
            if v_1d <= in_end:
                weights[vi] = [1.0, 0.0]
            elif v_1d >= out_start:
                weights[vi] = [0.0, 1.0]
            else:
                t  = (v_1d - in_end) / (out_start - in_end)
                w1 = falloff_func(t)
                weights[vi] = [1.0 - w1, w1]
        return weights

    # ── N locators — hat functions ────────────────────────────────────────────
    # Toutes les projections utilisent proj1d (coordonnées monde absolues).
    # Cela garantit que peak(locator i) est sa vraie position projetée,
    # et que le clamping compare les vertices à cette position réelle —
    # quel que soit l'axe coché (X/Y/Z local ou world).
    #
    # sorted_asc : détermine le sens de la chaîne en projetant tous les locators
    # avec les axes du locator 0 comme référence commune.

    ax_x0, ax_y0, ax_z0 = get_axes(0)
    loc_1d_ref = [proj1d(lp, ax_x0, ax_y0, ax_z0) for lp in loc_positions]
    sorted_asc = loc_1d_ref[0] <= loc_1d_ref[-1]

    def hat_score(vi, i):
        """
        Score hat du vertex vi pour le locator i.
        peak   = position projetée du locator i (coords monde absolues).
        v_1d   = position projetée du vertex    (coords monde absolues).
        Les axes du locator i sont utilisés pour sa propre projection.
        """
        p                = vtx_positions[vi]
        ax_x, ax_y, ax_z = get_axes(i)

        v_1d = proj1d(p,               ax_x, ax_y, ax_z)
        peak = proj1d(loc_positions[i], ax_x, ax_y, ax_z)

        if i == 0:
            # Bord gauche : clampe tout ce qui est avant le locator 0
            if (sorted_asc and v_1d <= peak) or (not sorted_asc and v_1d >= peak):
                return 1.0
            neighbor_1d = proj1d(loc_positions[1], ax_x, ax_y, ax_z)
            right = neighbor_1d + radius if sorted_asc else neighbor_1d - radius
            if sorted_asc:
                span = right - peak
                t    = (v_1d - peak) / span if span > 1e-6 else 0.0
            else:
                span = peak - right
                t    = (peak - v_1d) / span if span > 1e-6 else 0.0
            return falloff_func(max(0.0, min(1.0, 1.0 - t)))

        elif i == n_locs - 1:
            # Bord droit : clampe tout ce qui est après le dernier locator
            if (sorted_asc and v_1d >= peak) or (not sorted_asc and v_1d <= peak):
                return 1.0
            neighbor_1d = proj1d(loc_positions[-2], ax_x, ax_y, ax_z)
            left = neighbor_1d - radius if sorted_asc else neighbor_1d + radius
            if sorted_asc:
                span = peak - left
                t    = (v_1d - left) / span if span > 1e-6 else 1.0
            else:
                span = left - peak
                t    = (left - v_1d) / span if span > 1e-6 else 1.0
            return falloff_func(max(0.0, min(1.0, t)))

        else:
            # Locator intérieur : hat symétrique
            prev_1d = proj1d(loc_positions[i-1], ax_x, ax_y, ax_z)
            next_1d = proj1d(loc_positions[i+1], ax_x, ax_y, ax_z)
            left  = prev_1d - radius if sorted_asc else prev_1d + radius
            right = next_1d + radius if sorted_asc else next_1d - radius
            if sorted_asc:
                if v_1d <= peak:
                    span = peak - left
                    t    = (v_1d - left) / span if span > 1e-6 else 1.0
                    return falloff_func(max(0.0, min(1.0, t)))
                else:
                    span = right - peak
                    t    = (v_1d - peak) / span if span > 1e-6 else 0.0
                    return falloff_func(max(0.0, min(1.0, 1.0 - t)))
            else:
                if v_1d >= peak:
                    span = left - peak
                    t    = (v_1d - peak) / span if span > 1e-6 else 1.0
                    return falloff_func(max(0.0, min(1.0, t)))
                else:
                    span = peak - right
                    t    = (peak - v_1d) / span if span > 1e-6 else 0.0
                    return falloff_func(max(0.0, min(1.0, 1.0 - t)))

    weights = {}
    for vi in delta_indices:
        w = [hat_score(vi, i) for i in range(n_locs)]
        weights[vi] = normalize_w(w)
    return weights

def _get_regen_mesh(bs_node, logical_index):
    """
    Returns (mesh_shape, tgt_transform, was_already_live).

    mesh_shape       : shape node to use for pnts[] read/write.
    tgt_transform    : transform to cmds.delete() when done (None if was_already_live).
    was_already_live : True when the user had the target regenerated before this call.
                       In that case do NOT delete the mesh — it belongs to the user's
                       sculpt session.  Changes written to pnts[] propagate immediately.

    This avoids the crash where sculptTarget(regenerate=True) returns None because the
    target is already live.
    """
    geom_plug  = (f"{bs_node}.inputTarget[0]"
                  f".inputTargetGroup[{logical_index}]"
                  f".inputTargetItem[6000].inputGeomTarget")
    geom_conns = cmds.listConnections(geom_plug, source=True, destination=False)

    if geom_conns:
        # Regen mesh is already live — use it directly, do not delete it.
        return geom_conns[0], None, True

    tgt_transform = cmds.sculptTarget(bs_node, e=True, target=logical_index, regenerate=True)
    if not tgt_transform:
        cmds.error(
            f"sculptTarget returned None for {bs_node}[{logical_index}] "
            f"and the geomTarget plug has no connection — cannot proceed.")
    if not isinstance(tgt_transform, str):
        tgt_transform = tgt_transform[0]

    geom_conns = cmds.listConnections(geom_plug, source=True, destination=False)
    if not geom_conns:
        cmds.error(f"_get_regen_mesh: no geomTarget connection after regenerate "
                   f"for {bs_node}[{logical_index}]")
    return geom_conns[0], tgt_transform, False


def get_target_deltas(bs_node, logical_index):
    """
    Returns {vertex_index: (dx, dy, dz)} for non-zero deltas only.
    If the target is already regenerated (live regen mesh), reads from it without
    destroying it.  Otherwise creates a temporary regen mesh and deletes it after.
    """
    saved = _save_shape_editor_selection()
    try:
        mesh_shape, tgt_transform, was_live = _get_regen_mesh(bs_node, logical_index)
        n_verts = cmds.polyEvaluate(mesh_shape, vertex=True)
        deltas  = {}
        for i in range(n_verts):
            dx = cmds.getAttr(f"{mesh_shape}.pnts[{i}].pntx")
            dy = cmds.getAttr(f"{mesh_shape}.pnts[{i}].pnty")
            dz = cmds.getAttr(f"{mesh_shape}.pnts[{i}].pntz")
            if abs(dx) > 1e-6 or abs(dy) > 1e-6 or abs(dz) > 1e-6:
                deltas[i] = (dx, dy, dz)
        if not was_live:
            cmds.delete(tgt_transform)
    finally:
        _restore_shape_editor_selection(saved)
    return deltas

def select_delta_vertices(bs_node, logical_index):
    """
    Selects all vertices that have non-zero deltas on the given blendShape target.
    Returns the number of vertices selected (0 if the target has no deltas).
    """
    deltas    = get_target_deltas(bs_node, logical_index)
    if not deltas:
        return 0
    base_mesh = get_base_mesh(bs_node)
    vtx_list  = [f"{base_mesh}.vtx[{vi}]" for vi in sorted(deltas.keys())]
    cmds.select(vtx_list)
    return len(vtx_list)


def prune_small_deltas(bs_node, logical_index, tolerance):
    """
    Zeros out any delta whose Euclidean magnitude is strictly below `tolerance`.
    Returns the number of vertices pruned.
    """
    import math
    deltas = get_target_deltas(bs_node, logical_index)
    if not deltas:
        return 0

    to_zero = {vi: (0.0, 0.0, 0.0)
               for vi, (dx, dy, dz) in deltas.items()
               if math.sqrt(dx*dx + dy*dy + dz*dz) < tolerance}

    if not to_zero:
        return 0

    _bake_deltas(bs_node, logical_index, to_zero, deltas)
    return len(to_zero)


def _insert_indices_after(bs_node, source_index, new_indices, source_is_directory=False):

    key = -source_index if source_is_directory else source_index

    dir_indices = cmds.getAttr(f"{bs_node}.targetDirectory", multiIndices=True) or []

    # retirer new_indices de partout
    for d in dir_indices:
        attr = f"{bs_node}.targetDirectory[{d}].childIndices"
        children = list(cmds.getAttr(attr) or [])
        filtered = [c for c in children if c not in new_indices]
        if filtered != children:
            cmds.setAttr(attr, filtered, type="Int32Array")

    # trouver le parent contenant la source
    for d in dir_indices:
        attr = f"{bs_node}.targetDirectory[{d}].childIndices"
        children = list(cmds.getAttr(attr) or [])
        if key not in children:
            continue

        pos = children.index(key)

        for offset, ni in enumerate(new_indices):
            children.insert(pos + 1 + offset, ni)

        cmds.setAttr(attr, children, type="Int32Array")
        return True

    return False

def purge_empty_bs_slots(bs_node):
    """
    Removes empty (unaliased) target slots from a blendShape node.

    Maya sometimes leaves orphaned inputTargetGroup indices with no weight alias
    when targets are deleted or operations are interrupted. These ghost slots
    accumulate and can cause index collisions when adding new targets.

    Called automatically before every new target creation via duplicate_target().
    """
    list_idx = [
        x for x in (cmds.listAttr(f"{bs_node}.inputTarget[0].inputTargetGroup", sn=True, m=True) or [])
        if not len(x) > 15
    ]
    aliases = cmds.aliasAttr(bs_node, query=True) or []
    list_alias = [
        x.split("[")[-1].replace("]", "")
        for x in aliases
        if x.startswith("weight")
    ]
    for a_idx in list_idx:
        idx = a_idx.split("itg[")[-1].replace("]", "")
        if idx not in list_alias:
            mel.eval(f"blendShapeDeleteTargetGroup {bs_node} {idx};")
            print(f"  purged empty slot [{idx}] on {bs_node}")


def duplicate_target(bs_node, base_mesh, original_index, new_name):
    """
    Regenerates the original target to get its live mesh,
    duplicates it, uses the duplicate to create a new target slot,
    then cleans up. Returns the logical index of the new duplicate.
    Shape Editor selection is preserved around the sculptTarget calls.
    """
    # Purge orphaned empty slots before adding a new one — prevents index collisions
    purge_empty_bs_slots(bs_node)

    saved = _save_shape_editor_selection()
    try:
        # 1. Regenerate original target → live mesh named after the target alias
        regen_mesh = cmds.sculptTarget(bs_node, e=True, target=original_index, regenerate=True)
        regen_mesh = regen_mesh if isinstance(regen_mesh, str) else regen_mesh[0]

        # 2. Duplicate the regenerated mesh
        temp_dup = cmds.duplicate(regen_mesh, n=f"{new_name}_TEMP")[0]

        # 3. Delete the regenerated mesh — restores original target
        cmds.delete(regen_mesh)
    finally:
        _restore_shape_editor_selection(saved)

    # 4. Find next available logical index
    used_indices = cmds.getAttr(f"{bs_node}.w", multiIndices=True) or []
    next_idx = (max(used_indices) + 1) if used_indices else 0

    # 5. Add new target slot using the duplicate as geometry reference
    cmds.blendShape(bs_node, e=True, target=(base_mesh, next_idx, temp_dup, 1.0))
    cmds.delete(temp_dup)

    cmds.aliasAttr(new_name, f"{bs_node}.w[{next_idx}]")

    # 6. Reorder Shape Editor display: insert just after the source target
    _insert_indices_after(bs_node, original_index, [next_idx])

    return next_idx


def create_split_target(bs_node, base_mesh, target_name, source_index, loc_idx, weights, deltas):
    """
    Creates a split target by:
      1. Duplicating the source target into a new blendShape slot
      2. Regenerating it to get a live mesh whose pnts[] contain the full deltas
      3. Scaling each pnts[vi] by w[loc_idx] in place — vertices where w==1 are skipped
      4. Deleting the regen mesh to bake the result back into the blendShape slot

    The caller (_run_split) is responsible for:
      - Zeroing all blendShape weights before the split loop
      - Calling _insert_indices_after once with all new indices in order

    Returns the logical index of the newly created target.
    """
    # 1. Duplicate source target → new blendShape slot named target_name
    target_idx = duplicate_target(bs_node, base_mesh, source_index, target_name)

    # 2. Regenerate the duplicate → live mesh with full deltas in pnts[]
    saved = _save_shape_editor_selection()
    try:
        regen_mesh = cmds.sculptTarget(bs_node, e=True, target=target_idx, regenerate=True)
        regen_mesh = regen_mesh if isinstance(regen_mesh, str) else regen_mesh[0]

        # 3. Scale each delta by w — skip vertices where w==1 (no change needed)
        for vi, (dx, dy, dz) in deltas.items():
            w_list = weights.get(vi)
            w      = w_list[loc_idx] if w_list is not None else 0.0
            w      = max(0.0, min(1.0, w))
            if abs(w - 1.0) < 1e-7:
                continue  # vertex keeps full delta — no write needed
            cmds.setAttr(f"{regen_mesh}.pnts[{vi}].pntx", dx * w)
            cmds.setAttr(f"{regen_mesh}.pnts[{vi}].pnty", dy * w)
            cmds.setAttr(f"{regen_mesh}.pnts[{vi}].pntz", dz * w)

        # 4. Delete regen mesh — bakes modified pnts[] back into the blendShape slot
        cmds.delete(regen_mesh)
    finally:
        _restore_shape_editor_selection(saved)

    print(f"  ✓ Created : {target_name}")
    return target_idx


def get_bs_weight_attribute_logical_index(node, attr):
    """Returns the logical index of a weight attribute on a blendShape node."""
    from maya.api import OpenMaya as om
    mobject = om.MGlobal.getSelectionListByName(node).getDependNode(0)
    fn_dep  = om.MFnDependencyNode(mobject)
    weight_plug = fn_dep.findPlug("weight", False)
    for i in weight_plug.getExistingArrayAttributeIndices():
        plug = weight_plug.elementByLogicalIndex(i)
        if attr == plug.name().split(".")[-1]:
            return plug.logicalIndex()
    raise RuntimeError(f"BlendShape {node} does not have attribute {attr}")

# Maps UI label -> (symmetrySpace, symmetryAxis)
FLIP_AXIS_MAP = {
    "Object X": (1, 'x'),
    "Object Y": (1, 'y'),
    "Object Z": (1, 'z'),
}

def do_flip_target(bs_node, logical_index, base_shape, mirror_direction, symmetry_axis="Object X"):
    space, axis = FLIP_AXIS_MAP.get(symmetry_axis, (1, 'x'))
    cmds.blendShape(bs_node, edit=True,
                    flipTarget=[(0, logical_index)],
                    mirrorDirection=mirror_direction,
                    symmetrySpace=space,
                    symmetryAxis=axis)
    print(f"  ✓ Flip : {bs_node}.w[{logical_index}] ({symmetry_axis})")


def do_mirror_target(bs_node, logical_index, base_shape, mirror_direction):
    cmds.blendShape(bs_node, edit=True,
                    mirrorTarget=[(0, logical_index)],
                    mirrorDirection=mirror_direction,
                    symmetrySpace=1,
                    symmetryAxis='x')
    print(f"  ✓ Mirror : {bs_node}.w[{logical_index}]")


def multiply_target_deltas(bs_node, logical_index, fx, fy, fz, vtx_indices=None):
    """
    Multiplies delta X/Y/Z components directly (object space).
    fx=fy=fz=1.0 is identity. fx=0 zeros the X component. fx=-1 inverts it.
    vtx_indices : optional list of vertex indices to restrict the operation.
    Works even when the target is already regenerated (live regen mesh).
    """
    mesh_shape, tgt_transform, was_live = _get_regen_mesh(bs_node, logical_index)

    n_verts = cmds.polyEvaluate(mesh_shape, vertex=True)
    indices = vtx_indices if vtx_indices is not None else range(n_verts)

    for i in indices:
        dx = cmds.getAttr(f"{mesh_shape}.pnts[{i}].pntx")
        dy = cmds.getAttr(f"{mesh_shape}.pnts[{i}].pnty")
        dz = cmds.getAttr(f"{mesh_shape}.pnts[{i}].pntz")
        if abs(dx) < 1e-6 and abs(dy) < 1e-6 and abs(dz) < 1e-6:
            continue
        cmds.setAttr(f"{mesh_shape}.pnts[{i}].pntx", dx * fx)
        cmds.setAttr(f"{mesh_shape}.pnts[{i}].pnty", dy * fy)
        cmds.setAttr(f"{mesh_shape}.pnts[{i}].pntz", dz * fz)

    if not was_live:
        cmds.delete(tgt_transform)
    scope = f"{len(vtx_indices)} vtx" if vtx_indices is not None else "all verts"
    print(f"  Multiplied — X\xd7{fx} Y\xd7{fy} Z\xd7{fz}  ({scope})")


def push_normals_deltas(bs_node, logical_index, factor, vtx_indices=None):
    """
    Adds displacement along vertex outward normals, weighted by existing delta magnitude.
      new_delta = existing_delta + normal * length(existing_delta) * factor
    factor > 0 : push outward   factor < 0 : push inward
    Vertices with no existing delta are untouched.
    vtx_indices : optional list of vertex indices to restrict the operation.
    """
    from maya.api import OpenMaya as om

    # 1. Get base mesh vertex normals in object space
    base_mesh   = get_base_mesh(bs_node)
    base_shapes = cmds.listRelatives(base_mesh, shapes=True, type="mesh") or [base_mesh]
    om_sel  = om.MSelectionList()
    om_sel.add(base_shapes[0])
    fn_base = om.MFnMesh(om_sel.getDagPath(0))
    normals = fn_base.getVertexNormals(False, om.MSpace.kObject)

    # 2. Get or create the regen mesh (handles already-live case)
    mesh_shape, tgt_transform, was_live = _get_regen_mesh(bs_node, logical_index)

    # 3. Determine which vertices to process
    n_verts = cmds.polyEvaluate(mesh_shape, vertex=True)
    indices = vtx_indices if vtx_indices is not None else range(n_verts)

    # 4. Push along normals weighted by delta magnitude
    for i in indices:
        dx = cmds.getAttr(f"{mesh_shape}.pnts[{i}].pntx")
        dy = cmds.getAttr(f"{mesh_shape}.pnts[{i}].pnty")
        dz = cmds.getAttr(f"{mesh_shape}.pnts[{i}].pntz")
        if abs(dx) < 1e-6 and abs(dy) < 1e-6 and abs(dz) < 1e-6:
            continue
        mag  = math.sqrt(dx*dx + dy*dy + dz*dz)
        nv   = normals[i]
        nlen = math.sqrt(nv.x*nv.x + nv.y*nv.y + nv.z*nv.z)
        if nlen < 1e-6:
            continue
        nx, ny, nz = nv.x / nlen, nv.y / nlen, nv.z / nlen
        cmds.setAttr(f"{mesh_shape}.pnts[{i}].pntx", dx + nx * mag * factor)
        cmds.setAttr(f"{mesh_shape}.pnts[{i}].pnty", dy + ny * mag * factor)
        cmds.setAttr(f"{mesh_shape}.pnts[{i}].pntz", dz + nz * mag * factor)

    # 5. Bake back only if we created the regen ourselves
    if not was_live:
        cmds.delete(tgt_transform)
    direction = "outward" if factor >= 0 else "inward"
    scope     = f"{len(vtx_indices)} vtx" if vtx_indices is not None else "all verts"
    print(f"  Normal Push — {direction} \xd7{abs(factor)}  ({scope})")


def _build_adjacency(base_mesh):
    """Returns {vertex_index: [neighbor_indices]} for the given mesh transform."""
    from maya.api import OpenMaya as om
    shapes = cmds.listRelatives(base_mesh, shapes=True, type="mesh") or [base_mesh]
    om_sel = om.MSelectionList()
    om_sel.add(shapes[0])
    dag = om_sel.getDagPath(0)
    adj = {}
    edge_iter = om.MItMeshEdge(dag)
    while not edge_iter.isDone():
        a = edge_iter.vertexId(0)
        b = edge_iter.vertexId(1)
        adj.setdefault(a, []).append(b)
        adj.setdefault(b, []).append(a)
        edge_iter.next()
    return adj


def _bake_deltas(bs_node, logical_index, new_deltas, original_deltas):
    """
    Writes updated delta values back into the blendShape target.
    Uses _get_regen_mesh so it works whether the target is already live or not.
    new_deltas      : {vi: (dx, dy, dz)} — desired final deltas
    original_deltas : {vi: (dx, dy, dz)} — values before the operation (skip unchanged)
    """
    mesh_shape, tgt_transform, was_live = _get_regen_mesh(bs_node, logical_index)

    for vi, (nx, ny, nz) in new_deltas.items():
        ox, oy, oz = original_deltas.get(vi, (0.0, 0.0, 0.0))
        if abs(nx - ox) < 1e-8 and abs(ny - oy) < 1e-8 and abs(nz - oz) < 1e-8:
            continue
        cmds.setAttr(f"{mesh_shape}.pnts[{vi}].pntx", nx)
        cmds.setAttr(f"{mesh_shape}.pnts[{vi}].pnty", ny)
        cmds.setAttr(f"{mesh_shape}.pnts[{vi}].pntz", nz)

    if not was_live:
        cmds.delete(tgt_transform)


def smooth_target_deltas(bs_node, logical_index, opacity, vtx_indices=None):
    """
    Levels vertex positions by averaging actual 3D positions in deformed space.
    Equivalent to Maya's built-in Smooth Target tool:
        "Levels vertex positions in relation to each other by averaging the
         positions of vertices."

    ALL connected neighbours (delta and non-delta alike) contribute to the
    average, giving a natural geometric smooth of the deformed shape.
    Zero-delta neighbours participate at their base position, so the boundary
    of the displaced region is naturally attenuated.

    Base positions are derived from the regen mesh itself
        base[vi] = regen_pos[vi] - pnts[vi]
    which guarantees coordinate-system consistency and avoids any mismatch
    between the base-mesh DAG path and the blendShape target space.

    Uses a single regen mesh for both read and write — no double sculptTarget.

    opacity     : 0.0–1.0  →  1–10 iterative passes.
    vtx_indices : optional list of ints to restrict the operation.
    """
    from maya.api import OpenMaya as om

    base_mesh = get_base_mesh(bs_node)
    adj       = _build_adjacency(base_mesh)

    saved = _save_shape_editor_selection()
    try:
        mesh_shape, tgt_transform, was_live = _get_regen_mesh(bs_node, logical_index)

        # Read current pnts[] from regen mesh (these ARE the deltas)
        n_verts = cmds.polyEvaluate(mesh_shape, vertex=True)
        deltas  = {}
        for i in range(n_verts):
            dx = cmds.getAttr(f"{mesh_shape}.pnts[{i}].pntx")
            dy = cmds.getAttr(f"{mesh_shape}.pnts[{i}].pnty")
            dz = cmds.getAttr(f"{mesh_shape}.pnts[{i}].pntz")
            if abs(dx) > 1e-6 or abs(dy) > 1e-6 or abs(dz) > 1e-6:
                deltas[i] = (dx, dy, dz)

        if not deltas:
            if not was_live:
                cmds.delete(tgt_transform)
            return

        vtx_set = set(vtx_indices) if vtx_indices is not None else set(deltas.keys())

        # Read actual 3D positions from regen mesh (= base + pnts)
        om_sel    = om.MSelectionList()
        om_sel.add(mesh_shape)
        regen_pts = om.MFnMesh(om_sel.getDagPath(0)).getPoints(om.MSpace.kObject)

        # Pre-cache base positions for vtx_set + all their neighbours
        relevant = set(vtx_set)
        for vi in vtx_set:
            relevant.update(adj.get(vi, []))
        base_cache = {}
        for vi in relevant:
            p = regen_pts[vi]
            d = deltas.get(vi, (0.0, 0.0, 0.0))
            base_cache[vi] = (p.x - d[0], p.y - d[1], p.z - d[2])

        # One Laplacian pass in position space — ALL neighbours
        snapshot  = dict(deltas)
        smoothed  = dict(deltas)
        for vi in vtx_set:
            nbrs = adj.get(vi, [])
            if not nbrs:
                continue
            sx = sy = sz = 0.0
            for nb in nbrs:
                bx, by, bz = base_cache.get(
                    nb, (regen_pts[nb].x, regen_pts[nb].y, regen_pts[nb].z))
                d = snapshot.get(nb, (0.0, 0.0, 0.0))
                sx += bx + d[0]
                sy += by + d[1]
                sz += bz + d[2]
            n          = len(nbrs)
            bx, by, bz = base_cache[vi]
            smoothed[vi] = (sx / n - bx, sy / n - by, sz / n - bz)

        # Blend: result = lerp(original, smoothed, opacity)
        for vi, (sx, sy, sz) in smoothed.items():
            ox, oy, oz = deltas.get(vi, (0.0, 0.0, 0.0))
            nx = ox + (sx - ox) * opacity
            ny = oy + (sy - oy) * opacity
            nz = oz + (sz - oz) * opacity
            if abs(nx - ox) < 1e-8 and abs(ny - oy) < 1e-8 and abs(nz - oz) < 1e-8:
                continue
            cmds.setAttr(f"{mesh_shape}.pnts[{vi}].pntx", nx)
            cmds.setAttr(f"{mesh_shape}.pnts[{vi}].pnty", ny)
            cmds.setAttr(f"{mesh_shape}.pnts[{vi}].pntz", nz)

        if not was_live:
            cmds.delete(tgt_transform)

    finally:
        _restore_shape_editor_selection(saved)

    scope = f"{len(vtx_set)} vtx" if vtx_indices is not None else "all verts"
    print(f"  Smooth Deltas: opacity={opacity:.2f}  ({scope})")


def relax_target_deltas(bs_node, logical_index, opacity, vtx_indices=None):
    """
    Laplacian smoothing of the delta field (delta-vector space).
    Each delta vertex is replaced by the Jacobi average of its neighbours' deltas.
    Zero-delta neighbours contribute (0, 0, 0), so the boundary of the displaced
    region is naturally attenuated toward the base shape.

    opacity     : 0.0–1.0  blend weight between original and one relaxed pass.
    vtx_indices : optional list of ints to restrict the operation.
    """
    deltas = get_target_deltas(bs_node, logical_index)
    if not deltas:
        return

    base_mesh = get_base_mesh(bs_node)
    adj       = _build_adjacency(base_mesh)

    vtx_set = set(vtx_indices) if vtx_indices is not None else set(deltas.keys())

    # One Laplacian pass in delta-vector space
    snapshot = dict(deltas)
    relaxed  = dict(deltas)
    for vi in vtx_set:
        nbrs = adj.get(vi, [])
        if not nbrs:
            continue
        sx = sy = sz = 0.0
        for nb in nbrs:
            d = snapshot.get(nb, (0.0, 0.0, 0.0))
            sx += d[0]; sy += d[1]; sz += d[2]
        n = len(nbrs)
        relaxed[vi] = (sx / n, sy / n, sz / n)

    # Blend: result = lerp(original, relaxed, opacity)
    blended = {}
    for vi in vtx_set:
        ox, oy, oz = deltas.get(vi, (0.0, 0.0, 0.0))
        rx, ry, rz = relaxed.get(vi, (ox, oy, oz))
        blended[vi] = (ox + (rx - ox) * opacity,
                       oy + (ry - oy) * opacity,
                       oz + (rz - oz) * opacity)

    _bake_deltas(bs_node, logical_index, blended, deltas)
    scope = f"{len(vtx_set)} vtx" if vtx_indices is not None else "all verts"
    print(f"  Relax Deltas: opacity={opacity:.2f}  ({scope})")


def create_delta_cluster(bs_node, logical_index, target_name):
    """
    Regenerates the target mesh directly (no duplicate) and creates a cluster on it.
    The regen mesh stays live — user sculpts, then deletes it to bake back.
    Cluster weights = normalized delta magnitudes.
    Returns (grp, regen_mesh, cluster_handle).
    """
    # 1. Regenerate target → live mesh connected to blendShape target
    regen_mesh = cmds.sculptTarget(bs_node, e=True, target=logical_index, regenerate=True)
    regen_mesh = regen_mesh if isinstance(regen_mesh, str) else regen_mesh[0]
    cmds.rename(regen_mesh, f"{target_name}_regenerated")
    regen_mesh = f"{target_name}_regenerated"

    # 2. Read delta magnitudes directly from regen pnts[]
    n_verts    = cmds.polyEvaluate(regen_mesh, vertex=True)
    magnitudes = {}
    for i in range(n_verts):
        dx = cmds.getAttr(f"{regen_mesh}.pnts[{i}].pntx")
        dy = cmds.getAttr(f"{regen_mesh}.pnts[{i}].pnty")
        dz = cmds.getAttr(f"{regen_mesh}.pnts[{i}].pntz")
        mag = math.sqrt(dx*dx + dy*dy + dz*dz)
        if mag > 1e-6:
            magnitudes[i] = mag

    max_mag = max(magnitudes.values()) if magnitudes else 1.0

    # 3. Create cluster on the regen mesh
    cluster_node, cluster_handle = cmds.cluster(regen_mesh, name=f"{target_name}_cluster")

    # 4. Set weights — normalized magnitudes
    weights_list = [magnitudes.get(i, 0.0) / max_mag for i in range(n_verts)]
    cmds.setAttr(
        f"{cluster_node}.weightList[0].weights[0:{n_verts - 1}]",
        *weights_list, size=n_verts
    )

    grp = cmds.group(regen_mesh, cluster_handle, name=f"{target_name}_deltaCluster_grp")
    print(f"  ✓ Delta cluster on regen mesh : {cluster_handle} → {grp}")
    print(f"    Delete '{regen_mesh}' when done to bake back into blendShape.")
    return grp, regen_mesh, cluster_handle


def create_delta_joint(bs_node, logical_index, target_name):
    """
    Regenerates the target mesh, duplicates it as a posed mesh,
    creates two joints and a skinCluster:
      - {target_name}_jnt   : weights = normalized delta magnitudes
      - {target_name}_zero_jnt : absorbs remaining weights (1 - w)
    Everything is grouped under {target_name}_grp.
    Returns (group, posed_mesh, deform_jnt, zero_jnt).
    """
    # 1. Regenerate target → live mesh connected to blendShape target
    regen_mesh = cmds.sculptTarget(bs_node, e=True, target=logical_index, regenerate=True)
    regen_mesh = regen_mesh if isinstance(regen_mesh, str) else regen_mesh[0]
    cmds.rename(regen_mesh, f"{target_name}_regenerated")
    posed_mesh = f"{target_name}_regenerated"

    # 2. Compute normalized delta magnitudes directly from regen pnts[]
    n_verts    = cmds.polyEvaluate(posed_mesh, vertex=True)
    magnitudes = {}
    for i in range(n_verts):
        dx = cmds.getAttr(f"{posed_mesh}.pnts[{i}].pntx")
        dy = cmds.getAttr(f"{posed_mesh}.pnts[{i}].pnty")
        dz = cmds.getAttr(f"{posed_mesh}.pnts[{i}].pntz")
        mag = math.sqrt(dx*dx + dy*dy + dz*dz)
        if mag > 1e-6:
            magnitudes[i] = mag

    max_mag = max(magnitudes.values()) if magnitudes else 1.0

    # 3. Compute bbox center of delta vertices in world space.
    #    If the shape is empty (no deltas), fall back to the mesh bbox center
    #    so the joint is still placed at a meaningful position.
    posed_shapes = cmds.listRelatives(posed_mesh, shapes=True, type="mesh") or [posed_mesh]
    posed_shape  = posed_shapes[0]
    xs, ys, zs   = [], [], []
    for vi in magnitudes:
        pos = cmds.pointPosition(f"{posed_shape}.vtx[{vi}]", world=True)
        xs.append(pos[0]); ys.append(pos[1]); zs.append(pos[2])

    if xs:
        center = (
            (min(xs) + max(xs)) * 0.5,
            (min(ys) + max(ys)) * 0.5,
            (min(zs) + max(zs)) * 0.5,
        )
    else:
        # Empty shape — place joint at the overall mesh bbox center
        bbox = cmds.exactWorldBoundingBox(posed_mesh)   # xmin xmax ymin ymax zmin zmax
        center = (
            (bbox[0] + bbox[3]) * 0.5,
            (bbox[1] + bbox[4]) * 0.5,
            (bbox[2] + bbox[5]) * 0.5,
        )
        print(f"  ⚠ '{target_name}' has no deltas — joint placed at mesh bbox center.")

    # 4. Create joints
    cmds.select(clear=True)
    deform_jnt = cmds.joint(name=f"{target_name}_jnt", position=center)
    cmds.select(clear=True)
    zero_jnt   = cmds.joint(name=f"{target_name}_zero_jnt")  # stays at world origin
    cmds.select(clear=True)

    # 5. Create skinCluster
    skin_node = cmds.skinCluster(
        deform_jnt, zero_jnt, posed_mesh,
        name=f"{target_name}_skinCluster",
        toSelectedBones=True,
        bindMethod=0,
        skinMethod=0,
        normalizeWeights=1
    )[0]

    # 6. Write weights via direct setAttr — faster than skinPercent loop
    # Disable normalization while writing to avoid Maya redistributing mid-loop.
    # If the shape is empty (no deltas), all weights go to zero_jnt (index 1)
    # so the user can paint custom weights from scratch.
    cmds.setAttr(f"{skin_node}.normalizeWeights", 0)
    for vi in range(n_verts):
        w = magnitudes.get(vi, 0.0) / max_mag if magnitudes else 0.0
        cmds.setAttr(f"{skin_node}.weightList[{vi}].weights[0]", w)
        cmds.setAttr(f"{skin_node}.weightList[{vi}].weights[1]", 1.0 - w)
    cmds.setAttr(f"{skin_node}.normalizeWeights", 1)

    # 7. Group everything
    grp = cmds.group(posed_mesh, deform_jnt, zero_jnt, name=f"{target_name}_deltaJoint_grp")

    print(f"  ✓ Delta joint on regen mesh : {deform_jnt} / {zero_jnt} → {grp}")
    print(f"    Delete '{posed_mesh}' when done to bake back into blendShape.")
    return grp, posed_mesh, deform_jnt, zero_jnt


def edge_loop_split_target(bs_node, logical_index, target_name,
                            seam_edges, seed_a, seed_b, falloff_radius=1,
                            falloff_func=None):
    """
    Splits a blendShape target into two along a partial or full edge loop.

    Requires TWO seed vertices — one on each side of the seam.
    This is the only robust approach when delta regions extend beyond the
    immediate seam area (e.g. eye blink: upper lid + cheek + brow + lower lid).
    A purely topological barrier fails because paths exist around the seam
    through the cheek/brow region without crossing any seam edge.

    Algorithm:
      1. Build LOCAL adjacency (delta_vis + seam_vis), seam edges removed
      2. BFS from seed_a blocked by seam_vis  → reachable_a + distances_a
      3. BFS from seed_b blocked by seam_vis  → reachable_b + distances_b
      4. Assign each delta vertex to the closer seed (by BFS distance)
      5. Seam vertices → 0.5 / 0.5 blend
      6. Falloff: weight ramps from 0.5 at seam to 1.0 at falloff_radius hops

    Parameters
    ----------
    seam_edges     : set of frozenset({a, b}) — selected edges as vertex pairs
    seed_a         : int — vertex on the UPPER side
    seed_b         : int — vertex on the LOWER side
    falloff_radius : int — topological falloff distance in hops (default 1)
    falloff_func   : callable(t) -> w, t in [0,1]. Defaults to linear.
                     Pass one of CURVE_FUNCTIONS values for custom shaping.

    Creates  <target_name>_upper  and  <target_name>_lower.
    Returns (upper_idx, lower_idx).
    """
    from collections import deque
    from maya.api import OpenMaya as om

    base_mesh = get_base_mesh(bs_node)
    if not base_mesh:
        raise RuntimeError(f"edge_loop_split_target: no base mesh for {bs_node}")

    # Seam vertex set
    seam_vis = set()
    for e in seam_edges:
        seam_vis.update(e)

    # ── Validate seeds ─────────────────────────────────────────────────────
    if seed_a in seam_vis:
        raise RuntimeError(
            f"Seed A (vtx[{seed_a}]) is on the seam — pick a vertex clearly on the upper side.")
    if seed_b in seam_vis:
        raise RuntimeError(
            f"Seed B (vtx[{seed_b}]) is on the seam — pick a vertex clearly on the lower side.")

    # ── Read source deltas ─────────────────────────────────────────────────
    deltas    = get_target_deltas(bs_node, logical_index)
    delta_vis = set(deltas.keys())

    # ── Local active region ────────────────────────────────────────────────
    active_vis = delta_vis | seam_vis
    active_vis.add(seed_a)
    active_vis.add(seed_b)

    # ── Build local adjacency — seam edges removed ─────────────────────────
    om_sel = om.MSelectionList()
    om_sel.add(base_mesh)
    dag = om_sel.getDagPath(0)

    adj = {vi: [] for vi in active_vis}
    edge_iter = om.MItMeshEdge(dag)
    while not edge_iter.isDone():
        a = edge_iter.vertexId(0)
        b = edge_iter.vertexId(1)
        if a in active_vis and b in active_vis:
            if frozenset({a, b}) not in seam_edges:
                adj[a].append(b)
                adj[b].append(a)
        edge_iter.next()

    # ── BFS from each seed, blocked by seam_vis ────────────────────────────
    # Returns {vertex: topological_distance} for all reachable vertices
    def _bfs_dist_from_seed(seed, blocked):
        dist = {seed: 0}
        q = deque([seed])
        while q:
            vi = q.popleft()
            for nb in adj.get(vi, []):
                if nb not in dist and nb not in blocked:
                    dist[nb] = dist[vi] + 1
                    q.append(nb)
        return dist

    dist_from_a = _bfs_dist_from_seed(seed_a, seam_vis)
    dist_from_b = _bfs_dist_from_seed(seed_b, seam_vis)

    # ── Side assignment ────────────────────────────────────────────────────
    # Each vertex goes to whichever seed is topologically closer.
    # Seam vertices stay at 0.5 / 0.5.
    # Unreachable from either seed → default side A (edge case: isolated island)
    side_a = set()
    side_b = set()
    for vi in active_vis - seam_vis:
        da = dist_from_a.get(vi, None)
        db = dist_from_b.get(vi, None)
        if da is not None and db is not None:
            if da <= db:
                side_a.add(vi)
            else:
                side_b.add(vi)
        elif da is not None:
            side_a.add(vi)
        elif db is not None:
            side_b.add(vi)
        else:
            side_a.add(vi)  # isolated — default to A

    d_a = delta_vis & side_a
    d_b = delta_vis & side_b
    d_s = delta_vis & seam_vis
    print(f"  Edge Loop Split — seam:{len(seam_vis)} vtx  "
          f"delta A:{len(d_a)}  delta B:{len(d_b)}  delta seam:{len(d_s)}")

    if not d_a:
        cmds.warning("Side A (upper) has no delta vertices — check seed_a position.")
    if not d_b:
        cmds.warning("Side B (lower) has no delta vertices — check seed_b position.")

    # ── Falloff weights from seam ──────────────────────────────────────────
    # BFS from seam outward on each side, respecting the local adjacency
    def _bfs_dist_from_seam(blocked, max_d):
        dist = {vi: 0 for vi in seam_vis if vi in active_vis}
        q    = deque([vi for vi in seam_vis if vi in active_vis])
        while q:
            vi = q.popleft()
            if dist[vi] >= max_d:
                continue
            for nb in adj.get(vi, []):
                if nb not in dist and nb not in blocked:
                    dist[nb] = dist[vi] + 1
                    q.append(nb)
        return dist

    dist_seam_a = _bfs_dist_from_seam(side_b, falloff_radius)
    dist_seam_b = _bfs_dist_from_seam(side_a, falloff_radius)

    # Resolve falloff function — default to linear if not provided
    _falloff = falloff_func if falloff_func is not None else linear

    def _w(dist):
        if dist <= 0:
            return 0.5
        t = min(1.0, dist / falloff_radius)
        return 0.5 + 0.5 * _falloff(t)

    # ── Per-vertex weights (weight_A + weight_B == 1.0 always) ───────────
    weight_a = {}
    weight_b = {}
    for vi in delta_vis:
        if vi in seam_vis:
            weight_a[vi] = 0.5;  weight_b[vi] = 0.5
        elif vi in side_a:
            wa = _w(dist_seam_a.get(vi, falloff_radius))
            weight_a[vi] = wa;   weight_b[vi] = 1.0 - wa
        elif vi in side_b:
            wb = _w(dist_seam_b.get(vi, falloff_radius))
            weight_b[vi] = wb;   weight_a[vi] = 1.0 - wb
        else:
            weight_a[vi] = 1.0;  weight_b[vi] = 0.0

    # ── Duplicate + write weighted deltas ──────────────────────────────────
    def _write_weighted_target(new_name, weight_map):
        idx   = duplicate_target(bs_node, base_mesh, logical_index, new_name)
        saved = _save_shape_editor_selection()
        try:
            regen = cmds.sculptTarget(bs_node, e=True, target=idx, regenerate=True)
            regen = regen if isinstance(regen, str) else regen[0]
            for vi, (dx, dy, dz) in deltas.items():
                w = weight_map.get(vi, 0.0)
                cmds.setAttr(f"{regen}.pnts[{vi}].pntx", dx * w)
                cmds.setAttr(f"{regen}.pnts[{vi}].pnty", dy * w)
                cmds.setAttr(f"{regen}.pnts[{vi}].pntz", dz * w)
            cmds.delete(regen)
        finally:
            _restore_shape_editor_selection(saved)
        print(f"  ✓ Created : {new_name}")
        return idx

    upper_idx = _write_weighted_target(f"{target_name}_upper", weight_a)
    lower_idx = _write_weighted_target(f"{target_name}_lower", weight_b)
    return upper_idx, lower_idx


def link_mirror_locators(L_loc, R_loc):
    """
    Creates multiplyDivide nodes to drive R_loc as the X-axis mirror of L_loc.
    Returns the list of created node names (for later cleanup).

    All channels go through MD nodes so deleting them fully severs every connection.

    _mirror_TRA : input2 = (-1,  1,  1) → R.translateX/Y/Z
    _mirror_ROT : input2 = (-1, -1,  1) → R.rotateY / R.rotateZ / R.rotateX (passthrough)
    _mirror_SCL : input2 = ( 1,  1,  1) → R.scaleX/Y/Z (passthrough)
    """
    L_short = L_loc.split("|")[-1]
    R_short = R_loc.split("|")[-1]
    created = []

    # ── Translate mirror ───────────────────────────────────────────────────
    md_t = cmds.createNode('multiplyDivide', name=f'{L_short}_mirror_TRA')
    created.append(md_t)
    cmds.setAttr(f'{md_t}.input2X', -1)
    cmds.setAttr(f'{md_t}.input2Y',  1)
    cmds.setAttr(f'{md_t}.input2Z',  1)
    cmds.connectAttr(f'{L_loc}.translateX', f'{md_t}.input1X')
    cmds.connectAttr(f'{L_loc}.translateY', f'{md_t}.input1Y')
    cmds.connectAttr(f'{L_loc}.translateZ', f'{md_t}.input1Z')
    cmds.connectAttr(f'{md_t}.outputX', f'{R_loc}.translateX', force=True)
    cmds.connectAttr(f'{md_t}.outputY', f'{R_loc}.translateY', force=True)
    cmds.connectAttr(f'{md_t}.outputZ', f'{R_loc}.translateZ', force=True)

    # ── Rotate mirror ──────────────────────────────────────────────────────
    # input1X→rotateY (negated), input1Y→rotateZ (negated), input1Z→rotateX (passthrough)
    md_r = cmds.createNode('multiplyDivide', name=f'{L_short}_mirror_ROT')
    created.append(md_r)
    cmds.setAttr(f'{md_r}.input2X', -1)   # Y negated
    cmds.setAttr(f'{md_r}.input2Y', -1)   # Z negated
    cmds.setAttr(f'{md_r}.input2Z',  1)   # X passthrough
    cmds.connectAttr(f'{L_loc}.rotateY', f'{md_r}.input1X')
    cmds.connectAttr(f'{L_loc}.rotateZ', f'{md_r}.input1Y')
    cmds.connectAttr(f'{L_loc}.rotateX', f'{md_r}.input1Z')
    cmds.connectAttr(f'{md_r}.outputX',  f'{R_loc}.rotateY',  force=True)
    cmds.connectAttr(f'{md_r}.outputY',  f'{R_loc}.rotateZ',  force=True)
    cmds.connectAttr(f'{md_r}.outputZ',  f'{R_loc}.rotateX',  force=True)

    # ── Scale : passthrough MD ─────────────────────────────────────────────
    md_s = cmds.createNode('multiplyDivide', name=f'{L_short}_mirror_SCL')
    created.append(md_s)
    cmds.setAttr(f'{md_s}.input2X', 1)
    cmds.setAttr(f'{md_s}.input2Y', 1)
    cmds.setAttr(f'{md_s}.input2Z', 1)
    cmds.connectAttr(f'{L_loc}.scaleX', f'{md_s}.input1X')
    cmds.connectAttr(f'{L_loc}.scaleY', f'{md_s}.input1Y')
    cmds.connectAttr(f'{L_loc}.scaleZ', f'{md_s}.input1Z')
    cmds.connectAttr(f'{md_s}.outputX', f'{R_loc}.scaleX',    force=True)
    cmds.connectAttr(f'{md_s}.outputY', f'{R_loc}.scaleY',    force=True)
    cmds.connectAttr(f'{md_s}.outputZ', f'{R_loc}.scaleZ',    force=True)

    print(f"  Mirror linked : {L_short} -> {R_short}")
    return created


def unlink_mirror_locators(locators):
    """
    Finds and removes all mirror connections for the given R locators.
    Works standalone — no stored node list needed.

    Strategy:
      1. Scene-wide search for *_mirror_TRA* / *_mirror_ROT* / *_mirror_SCL*
         multiplyDivide nodes (handles numbered duplicates like _mirror_TRA1).
      2. Keep only those whose outputs actually drive one of our R locators.
      3. Delete them — Maya auto-disconnects their outputs on deletion.
      4. Fallback per-attribute scan for any remaining direct transform connections.

    locators : list of locator transform names to inspect (the R side).
    """
    import re
    _MIRROR_RE  = re.compile(r'_mirror_(TRA|ROT|SCL)\d*$')
    _TRS_ATTRS  = [
        'translateX', 'translateY', 'translateZ',
        'rotateX',    'rotateY',    'rotateZ',
        'scaleX',     'scaleY',     'scaleZ',
    ]

    # Short names for quick membership test
    loc_short = {loc.split('|')[-1] for loc in locators}
    loc_set   = set(locators) | loc_short

    # ── 1. Find every mirror MD node in the scene by name pattern ─────────
    candidates   = (cmds.ls('*_mirror_TRA*', '*_mirror_ROT*', '*_mirror_SCL*',
                             type='multiplyDivide') or [])
    mirror_nodes = [n for n in candidates if _MIRROR_RE.search(n)]

    nodes_to_delete = set()
    for node in mirror_nodes:
        dst_nodes = cmds.listConnections(node, source=False, destination=True) or []
        connects_to_R  = False
        unit_conv_seen = []

        for dst in dst_nodes:
            if dst in loc_set or dst.split('|')[-1] in loc_short:
                connects_to_R = True
            elif cmds.nodeType(dst) == 'unitConversion':
                # Maya auto-inserts unitConversion between MD outputs and rotate attrs
                unit_conv_seen.append(dst)
                for dst2 in (cmds.listConnections(dst, source=False, destination=True) or []):
                    if dst2 in loc_set or dst2.split('|')[-1] in loc_short:
                        connects_to_R = True

        if connects_to_R:
            nodes_to_delete.add(node)
            nodes_to_delete.update(unit_conv_seen)

    deleted = 0
    for node in nodes_to_delete:
        if cmds.objExists(node):
            cmds.delete(node)
            deleted += 1
            print(f"  Mirror unlinked : {node} deleted")

    # ── 2. Fallback: break any remaining direct transform→locator connections
    broken = 0
    for loc in locators:
        for attr in _TRS_ATTRS:
            dst_plug  = f'{loc}.{attr}'
            src_plugs = cmds.listConnections(
                dst_plug, source=True, destination=False, plugs=True) or []
            for src_plug in src_plugs:
                if cmds.nodeType(src_plug.split('.')[0]) == 'transform':
                    try:
                        cmds.disconnectAttr(src_plug, dst_plug)
                        broken += 1
                        print(f"  Direct connection broken : {src_plug} -> {dst_plug}")
                    except Exception:
                        pass

    print(f"  Unlink complete : {deleted} node(s) deleted, {broken} direct connection(s) broken")
    return deleted + broken


# ─────────────────────────────────────────────────────────────────────────────
# TARGET WRAP EXTRACTION
# ─────────────────────────────────────────────────────────────────────────────

def _find_blendshape_on_mesh(mesh):
    """Returns the first blendShape node found upstream on mesh, or None."""
    shapes = cmds.listRelatives(mesh, shapes=True) or [mesh]
    for shape in shapes:
        history = cmds.listHistory(shape, pruneDagObjects=True) or []
        for n in history:
            if cmds.nodeType(n) == 'blendShape':
                return n
    return None


def _create_wrap_deformer(driver_mesh, driven_mesh):
    """
    Creates a wrap deformer: driven_mesh is deformed by driver_mesh.
    Returns (wrap_node, base_transform) for later cleanup.
    """
    # Scene-wide snapshot before creation — more reliable than listHistory
    before_wraps = set(cmds.ls(type='wrap') or [])

    # Select driven first, then driver — Maya wrap convention
    cmds.select([driven_mesh, driver_mesh])
    mel.eval('CreateWrap')

    after_wraps = set(cmds.ls(type='wrap') or [])
    new_wraps   = list(after_wraps - before_wraps)
    if not new_wraps:
        raise RuntimeError(
            f"_create_wrap_deformer: failed to create wrap on '{driven_mesh}' "
            f"driven by '{driver_mesh}'"
        )
    wrap_node = new_wraps[0]

    base_conns = cmds.listConnections(
        f"{wrap_node}.basePoints[0]", source=True, destination=False
    ) or []
    base_transform = None
    if base_conns:
        parents = cmds.listRelatives(base_conns[0], parent=True) or []
        base_transform = parents[0] if parents else base_conns[0]

    print(f"  ✓ Wrap created : {wrap_node}  (base: {base_transform})")
    return wrap_node, base_transform


def _delete_wrap_deformer(wrap_node, base_transform):
    """Deletes the wrap node and its associated base mesh."""
    if wrap_node and cmds.objExists(wrap_node):
        cmds.delete(wrap_node)
    if base_transform and cmds.objExists(base_transform):
        cmds.delete(base_transform)
    print(f"  Wrap cleaned up.")


def _zero_bs_weights(bs_node):
    """Forces all weights on bs_node to zero. Warns if any were non-zero."""
    all_weights = cmds.listAttr(f"{bs_node}.w", multi=True) or []
    non_zero    = [w for w in all_weights if abs(cmds.getAttr(f"{bs_node}.{w}")) > 1e-6]
    for w in non_zero:
        cmds.setAttr(f"{bs_node}.{w}", 0.0)
    if non_zero:
        print(f"  ⚠ Forced to zero before extraction: {', '.join(non_zero)}")


def _capture_target_shapes(bs_node, mesh_target, targets):
    """
    For each target: activates it, duplicates mesh_target (history deleted to bake
    the deformed position), resets weight to zero.
    Returns [(target_name, temp_mesh_transform), ...].
    """
    extracted = []
    for _bs, _idx, target_name in targets:
        cmds.setAttr(f"{bs_node}.{target_name}", 1.0)
        temp_dup = cmds.duplicate(mesh_target, name=f"{target_name}_WRAP_TEMP")[0]
        cmds.delete(temp_dup, constructionHistory=True)
        cmds.setAttr(f"{bs_node}.{target_name}", 0.0)
        extracted.append((target_name, temp_dup))
        print(f"  ✓ Captured: {target_name}")
    return extracted


def _integrate_extracted_shapes(mesh_target, extracted):
    """
    Finds or creates a blendShape on mesh_target, then integrates each extracted shape:
      - Replaces an existing target if the name already exists (logs the replacement).
      - Adds as new otherwise.
    Temp meshes are deleted after integration.
    Returns (bs_target, log) where log = [(target_name, was_replaced), ...].
    """
    bs_target = _find_blendshape_on_mesh(mesh_target)
    log = []

    for target_name, temp_mesh in extracted:
        was_replaced = False

        if bs_target is None:
            # No blendShape yet — create an empty one, then add the target via edit mode
            # (using blendShape(temp, base) would keep a live connection; deleting temp
            # would wipe the target data)
            mesh_short = mesh_target.split(":")[-1].split("|")[-1]
            bs_target = cmds.blendShape(mesh_target, frontOfChain=True,
                                        name=f"{mesh_short}_bs")[0]
            cmds.blendShape(bs_target, e=True,
                            target=(mesh_target, 0, temp_mesh, 1.0))
            cmds.aliasAttr(target_name, f"{bs_target}.w[0]")
            cmds.setAttr(f"{bs_target}.{target_name}", 0.0)
            cmds.delete(temp_mesh)
            log.append((target_name, False))
            print(f"  ✓ Created blendShape '{bs_target}' with '{target_name}'")
            continue

        # Replace existing target with the same name
        existing_shapes = cmds.listAttr(f"{bs_target}.w", multi=True) or []
        if target_name in existing_shapes:
            existing_idx = get_bs_weight_attribute_logical_index(bs_target, target_name)
            mel.eval(f"blendShapeDeleteTargetGroup {bs_target} {existing_idx};")
            was_replaced = True

        # Add at next available index
        purge_empty_bs_slots(bs_target)
        used_indices = cmds.getAttr(f"{bs_target}.w", multiIndices=True) or []
        next_idx     = (max(used_indices) + 1) if used_indices else 0

        cmds.blendShape(bs_target, e=True, target=(mesh_target, next_idx, temp_mesh, 1.0))
        cmds.aliasAttr(target_name, f"{bs_target}.w[{next_idx}]")
        cmds.setAttr(f"{bs_target}.{target_name}", 0.0)
        cmds.delete(temp_mesh)

        log.append((target_name, was_replaced))
        action = "Replaced" if was_replaced else "Added"
        print(f"  ✓ {action}: '{target_name}' → {bs_target}")

    return bs_target, log


def extract_targets_via_wrap(bs_node, base_mesh, mesh_target, targets):
    """
    Extracts blendShape targets from bs_node onto mesh_target using a wrap deformer.

    A neutral-pose duplicate of mesh_target is created and used as the wrap proxy —
    the original mesh_target is never modified. The proxy and wrap are always deleted
    at the end of the operation, whether it succeeds or fails.

    targets : list of (bs_node, logical_index, target_name)
    Returns : (bs_target, log)  — see _integrate_extracted_shapes
    """
    _zero_bs_weights(bs_node)

    # Duplicate mesh_target to get a clean neutral-pose proxy
    proxy = cmds.duplicate(mesh_target, name=f"{mesh_target}_wrapProxy")[0]
    cmds.delete(proxy, constructionHistory=True)

    wrap_node, base_transform = _create_wrap_deformer(base_mesh, proxy)
    extracted = []
    try:
        extracted = _capture_target_shapes(bs_node, proxy, targets)
    finally:
        # Always clean up wrap + proxy regardless of success or failure
        _delete_wrap_deformer(wrap_node, base_transform)
        if cmds.objExists(proxy):
            cmds.delete(proxy)

    return _integrate_extracted_shapes(mesh_target, extracted)


def connect_extracted_targets(bs_node, bs_target, target_names):
    """
    Connects matching weight attributes from bs_node to bs_target.
    bs_node.target_name → bs_target.target_name (direct connectAttr).
    Returns the list of successfully connected target names.
    """
    connected = []
    for name in target_names:
        src = f"{bs_node}.{name}"
        dst = f"{bs_target}.{name}"
        if cmds.objExists(src) and cmds.objExists(dst):
            cmds.connectAttr(src, dst, force=True)
            connected.append(name)
            print(f"  ✓ Connected: {src} → {dst}")
    return connected


def extract_targets_only(bs_node, mesh_target, targets):
    """
    Extracts blendShape targets by duplicating the deformed mesh_target (which must
    already have a deformer chain set up by the user). No blendShape is created.

    Each extracted shape:
      - Transform : {target_name}_TEMP
      - Shape node: {target_name}  (so blendShape > Add picks up the name automatically)

    All shapes are grouped under {mesh_short}_extractedShapes_grp at world root.

    targets : list of (bs_node, logical_index, target_name)
    Returns : (grp, [transform_names])
    """
    _zero_bs_weights(bs_node)

    mesh_short = mesh_target.split(":")[-1].split("|")[-1]
    extracted  = []

    for _bs, _idx, target_name in targets:
        cmds.setAttr(f"{bs_node}.{target_name}", 1.0)
        temp_dup = cmds.duplicate(mesh_target, name=f"{target_name}_TEMP")[0]
        cmds.delete(temp_dup, constructionHistory=True)
        cmds.setAttr(f"{bs_node}.{target_name}", 0.0)

        # Remove all intermediate shapes (ShapeOrig, ShapeDeformed, etc.)
        all_shapes = cmds.listRelatives(temp_dup, shapes=True, fullPath=True) or []
        intermediate = [s for s in all_shapes
                        if cmds.getAttr(f"{s}.intermediateObject")]
        if intermediate:
            cmds.delete(intermediate)

        # If multiple non-intermediate shapes remain, keep only the first
        remaining = cmds.listRelatives(temp_dup, shapes=True, fullPath=True) or []
        if len(remaining) > 1:
            cmds.delete(remaining[1:])

        # Rename the surviving shape so blendShape > Add picks up the target name
        surviving = cmds.listRelatives(temp_dup, shapes=True) or []
        if surviving:
            cmds.rename(surviving[0], target_name)
        extracted.append(temp_dup)
        print(f"  ✓ Extracted: {target_name}_TEMP  (shape: {target_name})")

    grp = None
    if extracted:
        grp = cmds.group(*extracted, name=f"{mesh_short}_extractedShapes_grp", world=True)
        print(f"  ✓ Grouped at world root: {grp}")

    return grp, extracted


@undo_chunk
def add_mesh_as_target(source_meshes, target_mesh, delete_source=False):
    """
    Adds one or more meshes directly as new blendshape targets on target_mesh (rest-pose).

    source_meshes: str or list — mesh(es) to import as targets (all selections except the last)
    target_mesh  : mesh with deformers / BS node (last selected in viewport)
    delete_source: if True, deletes each source mesh after extraction

    Returns: (bs_node, [(new_idx, target_name), ...])
    """
    if isinstance(source_meshes, str):
        source_meshes = [source_meshes]

    bs_node = _find_blendshape_on_mesh(target_mesh)
    if not bs_node:
        short   = target_mesh.split(":")[-1].split("|")[-1]
        bs_node = cmds.blendShape(target_mesh, frontOfChain=True, name=f"{short}_bs")[0]

    base_mesh = get_base_mesh(bs_node)
    results   = []

    for source_mesh in source_meshes:
        raw_name    = source_mesh.split(":")[-1].split("|")[-1]
        target_name = f"{raw_name}_mprt"

        used    = cmds.getAttr(f"{bs_node}.weight", multiIndices=True) or []
        new_idx = (max(used) + 1) if used else 0

        cmds.blendShape(bs_node, edit=True, topologyCheck=True,
                        target=(base_mesh, new_idx, source_mesh, 1.0))
        cmds.aliasAttr(target_name, f"{bs_node}.weight[{new_idx}]")

        if delete_source and cmds.objExists(source_mesh):
            cmds.delete(source_mesh)

        print(f"  ✓ Target '{target_name}' added to {bs_node} at index {new_idx}")
        results.append((new_idx, target_name))

    return bs_node, results


@undo_chunk
def create_corrective_shape(corrective_meshes, target_mesh, delete_corrective=False):
    """
    Creates corrective blendshape targets using cmds.invertShape().

    corrective_meshes: str or list — sculpted correction mesh(es) in deformed space
    target_mesh      : mesh with deformers (skinCluster, BS, etc.) (last selected in viewport)
    delete_corrective: if True, deletes each corrective mesh after extraction

    Returns: (bs_node, [(new_idx, target_name), ...])
    """
    if isinstance(corrective_meshes, str):
        corrective_meshes = [corrective_meshes]

    bs_node = _find_blendshape_on_mesh(target_mesh)
    if not bs_node:
        short   = target_mesh.split(":")[-1].split("|")[-1]
        bs_node = cmds.blendShape(target_mesh, frontOfChain=True, name=f"{short}_bs")[0]

    base_mesh = get_base_mesh(bs_node)
    results   = []

    for corrective_mesh in corrective_meshes:
        raw_name    = corrective_mesh.split(":")[-1].split("|")[-1]
        target_name = f"{raw_name}_mprt"

        inverted_result = cmds.invertShape(target_mesh, corrective_mesh)
        if not inverted_result:
            raise RuntimeError(f"cmds.invertShape returned no result for '{corrective_mesh}'.")
        inverted = inverted_result[0] if isinstance(inverted_result, list) else inverted_result

        try:
            used    = cmds.getAttr(f"{bs_node}.weight", multiIndices=True) or []
            new_idx = (max(used) + 1) if used else 0

            cmds.blendShape(bs_node, edit=True, topologyCheck=True,
                            target=(base_mesh, new_idx, inverted, 1.0))
            cmds.aliasAttr(target_name, f"{bs_node}.weight[{new_idx}]")

            print(f"  ✓ Posed target '{target_name}' added to {bs_node} at index {new_idx}")
            results.append((new_idx, target_name))

        finally:
            if cmds.objExists(inverted):
                cmds.delete(inverted)

        if delete_corrective and cmds.objExists(corrective_mesh):
            cmds.delete(corrective_mesh)

    return bs_node, results


def connect_targets_A_to_B(mesh_A, mesh_B):
    """
    Finds the blendShape on mesh_A (source) and mesh_B (target), then connects
    every weight attribute that exists on both nodes by name:
        bs_A.target_name  →  bs_B.target_name  (force=True)

    mesh_A : first selected transform (source)
    mesh_B : second selected transform (target)
    Returns : (bs_A, bs_B, [connected_target_names])
    """
    bs_A = _find_blendshape_on_mesh(mesh_A)
    bs_B = _find_blendshape_on_mesh(mesh_B)

    if not bs_A:
        raise RuntimeError(f"No blendShape found on source mesh '{mesh_A}'")
    if not bs_B:
        raise RuntimeError(f"No blendShape found on target mesh '{mesh_B}'")

    def _target_names(bs_node):
        aliases = cmds.aliasAttr(bs_node, query=True) or []
        # aliasAttr returns [alias, realAttr, alias, realAttr, ...]
        return {aliases[i] for i in range(0, len(aliases), 2)}

    names_A = _target_names(bs_A)
    names_B = _target_names(bs_B)
    common  = sorted(names_A & names_B)

    connected = []
    for name in common:
        src = f"{bs_A}.{name}"
        dst = f"{bs_B}.{name}"
        cmds.connectAttr(src, dst, force=True)
        connected.append(name)
        print(f"  ✓ Connected: {src}  →  {dst}")

    return bs_A, bs_B, connected


# ── Wire Setup ────────────────────────────────────────────────────────────────

@undo_chunk
def create_wire_setup(mesh_base, edge_line, shape_names,
                      dropoff=100.0, rotation=0.0, spans=4, flat_curve=True):
    """
    Creates a wire deformer setup for the given mesh and edge loop selection.

    mesh_base   : transform name of the base mesh
    edge_line   : list of edge components (e.g. ["mesh.e[0]", ...])
    shape_names : list of blendShape target names to create on the wire curve
    dropoff     : wire dropoff distance (default 100)
    rotation    : wire rotation value (default 0)

    Creates:
      wire_setup_grp        — top group
      wire_setup_msh        — duplicate of mesh_base, driven by the wire
      wire_crv              — curve extracted from edge_line
      wire_bs               — blendShape on wire_crv with one target per shape_name
      <shape>_crv           — duplicate curve per shape, hidden, parented to group
      wire_setup_wire       — wire deformer node
    """
    wire_grp  = "wire_setup_grp"
    dup_name  = "wire_setup_msh"
    wire_crv  = "wire_crv"
    wire_bs   = "wire_bs"
    wire_node = "wire_setup_wire"

    # ── Group ────────────────────────────────────────────────────────────────
    if not cmds.objExists(wire_grp):
        cmds.group(em=True, n=wire_grp)

    # ── Duplicate base mesh ──────────────────────────────────────────────────
    if cmds.objExists(dup_name):
        cmds.delete(dup_name)
    cmds.duplicate(mesh_base, name=dup_name)[0]
    orig = dup_name + "ShapeOrig"
    if cmds.objExists(orig):
        cmds.delete(orig)
    cmds.parent(dup_name, wire_grp)

    # ── Remap edges to duplicate mesh ────────────────────────────────────────
    new_line = [e.replace(mesh_base, dup_name) for e in edge_line]

    # ── Extract curve from edges ─────────────────────────────────────────────
    if cmds.objExists(wire_crv):
        cmds.delete(wire_crv)
    cmds.select(new_line)
    cmds.polyToCurve(f=2, dg=1, usm=0, n=wire_crv)
    cmds.select(cl=True)
    cmds.rebuildCurve(wire_crv, ch=1, rpo=1, rt=0, end=1,
                      kr=0, kcp=0, kep=1, kt=0, s=spans, d=3)
    cmds.delete(wire_crv, ch=True)
    cmds.parent(wire_crv, wire_grp)

    # Optionally flatten all CVs to the Y of cv[0] (keep curve planar)
    if flat_curve:
        y_ref   = cmds.xform(f"{wire_crv}.cv[0]", q=True, t=True)[1]
        num_cvs = cmds.getAttr(f"{wire_crv}.spans") + cmds.getAttr(f"{wire_crv}.degree")
        for i in range(num_cvs):
            pos    = list(cmds.xform(f"{wire_crv}.cv[{i}]", q=True, t=True))
            pos[1] = y_ref
            cmds.xform(f"{wire_crv}.cv[{i}]", t=pos)

    # ── BlendShape on wire curve ─────────────────────────────────────────────
    if cmds.objExists(wire_bs):
        cmds.delete(wire_bs)
    cmds.blendShape(wire_crv, n=wire_bs)
    for idx, shp in enumerate(shape_names):
        crv_name = shp + "_crv"
        if cmds.objExists(crv_name):
            cmds.delete(crv_name)
        cmds.duplicate(wire_crv, n=crv_name)
        cmds.blendShape(wire_bs, e=True, t=(wire_crv, idx, crv_name, 1.0))
        cmds.aliasAttr(shp, f"{wire_bs}.weight[{idx}]")
        cmds.hide(crv_name)
        if not cmds.listRelatives(crv_name, parent=True) or \
                cmds.listRelatives(crv_name, parent=True)[0] != wire_grp:
            cmds.parent(crv_name, wire_grp)

    # ── Wire deformer ────────────────────────────────────────────────────────
    if cmds.objExists(wire_node):
        cmds.delete(wire_node)
    cmds.select(dup_name)
    cmds.wire(w=wire_crv, n=wire_node)
    cmds.wire(wire_node, e=True, dds=[0, dropoff])
    cmds.setAttr(f"{wire_node}.rotation", rotation)
    cmds.select(cl=True)

    print(f"  ✓ Wire setup created — {len(shape_names)} shape(s) on {dup_name}")
    return wire_grp


@undo_chunk
def check_wire_shapes_have_deltas(shape_names):
    """
    Returns the list of shape names (among shape_names) that have no vertex
    deltas stored in wire_bs — i.e. the shape curve is identical to the base curve.
    Checked via the blendShape inputComponentsTarget attribute (fast, no posing needed).
    """
    wire_bs = "wire_bs"
    if not cmds.objExists(wire_bs):
        return []

    aliases = cmds.aliasAttr(wire_bs, query=True) or []
    alias_to_idx = {aliases[i]: int(aliases[i + 1].split("[")[1].rstrip("]"))
                    for i in range(0, len(aliases), 2)}

    empty = []
    for shp in shape_names:
        if shp not in alias_to_idx:
            continue
        idx = alias_to_idx[shp]
        try:
            components = cmds.getAttr(
                f"{wire_bs}.inputTarget[0].inputTargetGroup[{idx}]"
                f".inputTargetItem[6000].inputComponentsTarget"
            )
        except Exception:
            components = None
        if not components:
            empty.append(shp)
    return empty


def bake_wire_to_mesh(base_mesh, shape_names):
    """
    For each name in shape_names:
      1. Set wire_bs.<name> = 1.0  →  wire_setup_msh is in that pose
      2. Duplicate wire_setup_msh
      3. Add the duplicate as a blendShape target on base_mesh's bs_node
         (no topologyCheck — vertex count mismatch triggers an orange warning)
      4. If a target with that name already exists → overwrite + warning
      5. Reset wire_bs.<name> = 0.0 and delete the duplicate

    Returns (bs_node, [baked_name, ...])
    """
    wire_msh = "wire_setup_msh"
    wire_bs  = "wire_bs"

    if not cmds.objExists(wire_msh):
        raise RuntimeError("'wire_setup_msh' not found — run Create Wire Setup first.")
    if not cmds.objExists(wire_bs):
        raise RuntimeError("'wire_bs' not found — run Create Wire Setup first.")

    # Vertex count check (warning only, does not abort)
    vtx_wire = cmds.polyEvaluate(wire_msh,  vertex=True)
    vtx_base = cmds.polyEvaluate(base_mesh, vertex=True)
    topo_ok  = (vtx_wire == vtx_base)
    if not topo_ok:
        cmds.warning(
            f"Vertex count mismatch: wire_setup_msh ({vtx_wire}) "
            f"≠ {base_mesh} ({vtx_base}). Proceeding without topology check."
        )

    # Find or create blendShape on base_mesh
    bs_node = _find_blendshape_on_mesh(base_mesh)
    if bs_node is None:
        short   = base_mesh.split(":")[-1].split("|")[-1]
        bs_node = cmds.blendShape(base_mesh, frontOfChain=True, n=f"{short}_bs")[0]

    # Build alias map  {name: index}
    def _alias_map(bs):
        aliases = cmds.aliasAttr(bs, query=True) or []
        return {aliases[i]: int(aliases[i+1].split("[")[1].rstrip("]"))
                for i in range(0, len(aliases), 2)}

    # Reset all wire_bs weights before starting
    def _reset_wire_bs():
        for s in (shape_names):
            try:
                cmds.setAttr(f"{wire_bs}.{s}", 0.0)
            except Exception:
                pass

    _reset_wire_bs()
    baked = []

    for shp in shape_names:
        # Check the shape curve alias exists on wire_bs
        if not cmds.attributeQuery(shp, node=wire_bs, exists=True):
            cmds.warning(f"Shape '{shp}' not found on {wire_bs} — skipping.")
            continue

        # Pose
        cmds.setAttr(f"{wire_bs}.{shp}", 1.0)

        # Duplicate deformed mesh
        tmp = cmds.duplicate(wire_msh, name=f"_bake_tmp_{shp}")[0]

        # Determine target index (overwrite or new)
        amap      = _alias_map(bs_node)
        overwrite = shp in amap
        if overwrite:
            cmds.warning(f"Target '{shp}' already exists on {bs_node} — overwriting.")
            t_idx = amap[shp]
            cmds.blendShape(bs_node, e=True, resetTargetDelta=(0, t_idx))
        else:
            used  = cmds.getAttr(f"{bs_node}.weight", multiIndices=True) or []
            t_idx = (max(used) + 1) if used else 0

        # Add target (no topologyCheck)
        cmds.blendShape(bs_node, e=True, topologyCheck=False,
                        target=(base_mesh, t_idx, tmp, 1.0))

        if not overwrite:
            cmds.aliasAttr(shp, f"{bs_node}.weight[{t_idx}]")

        cmds.delete(tmp)

        # Reset pose
        cmds.setAttr(f"{wire_bs}.{shp}", 0.0)
        baked.append(shp)
        print(f"  ✓ Baked : '{shp}'  →  {bs_node}[{t_idx}]")

    return bs_node, baked
