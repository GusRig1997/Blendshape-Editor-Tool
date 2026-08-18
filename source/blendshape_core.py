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

    Projection principle:
      The checked axis (X/Y/Z) defines which column of the locator matrix
      is used as the projection direction. Result is identical regardless of
      the checked axis if the locator has the same orientation in the scene.
      All projections use absolute world coordinates (world origin),
      which guarantees consistent comparisons for clamping.
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
        """Euclidean distance between p and ref along the checked axes."""
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

    # ── 1 locator — 1D projection ─────────────────────────────────────────────
    # proj1d returns absolute world coordinates.
    # loc_1d = projected locator position → serves as the center of the zone.
    # in_end / out_start = boundaries of the transition zone around the locator.
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
    # All projections use proj1d (absolute world coordinates).
    # This guarantees that peak(locator i) is its true projected position,
    # and that clamping compares vertices to that real position —
    # regardless of the checked axis (local X/Y/Z or world).
    #
    # sorted_asc: determines the chain direction by projecting all locators
    # using locator 0's axes as a common reference.

    ax_x0, ax_y0, ax_z0 = get_axes(0)
    loc_1d_ref = [proj1d(lp, ax_x0, ax_y0, ax_z0) for lp in loc_positions]
    sorted_asc = loc_1d_ref[0] <= loc_1d_ref[-1]

    def hat_score(vi, i):
        """
        Hat score of vertex vi for locator i.
        peak   = projected position of locator i (absolute world coords).
        v_1d   = projected position of the vertex (absolute world coords).
        Locator i's own axes are used for its projection.
        """
        p                = vtx_positions[vi]
        ax_x, ax_y, ax_z = get_axes(i)

        v_1d = proj1d(p,               ax_x, ax_y, ax_z)
        peak = proj1d(loc_positions[i], ax_x, ax_y, ax_z)

        if i == 0:
            # Left edge: clamp everything before locator 0
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
            # Right edge: clamp everything after the last locator
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
            # Interior locator: symmetric hat
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

    Raises RuntimeError with a specific message for the two known failure modes:
      - The regen mesh is already live (connected) for this target.
      - A scene node with the same name as the target already exists (name collision).
    """
    geom_plug  = (f"{bs_node}.inputTarget[0]"
                  f".inputTargetGroup[{logical_index}]"
                  f".inputTargetItem[6000].inputGeomTarget")
    geom_conns = cmds.listConnections(geom_plug, source=True, destination=False)

    if geom_conns:
        # Regen mesh is already live — use it directly, do not delete it.
        return geom_conns[0], None, True

    # Detect name collision before attempting regenerate.
    # sculptTarget names the regen mesh after the target alias.
    target_alias = cmds.aliasAttr(f"{bs_node}.w[{logical_index}]", q=True) or f"target_{logical_index}"
    if cmds.objExists(target_alias):
        raise RuntimeError(
            f"Cannot regenerate target '{target_alias}': a node named '{target_alias}' "
            f"already exists in the scene. Rename or delete it before using this tool.")

    tgt_transform = cmds.sculptTarget(bs_node, e=True, target=logical_index, regenerate=True)
    if not tgt_transform:
        # Re-check in case it became connected during the call
        geom_conns = cmds.listConnections(geom_plug, source=True, destination=False)
        if geom_conns:
            raise RuntimeError(
                f"Target '{target_alias}' regen mesh is already connected to the blendShape. "
                f"Exit sculpt mode or delete the existing regen mesh before using this tool.")
        raise RuntimeError(
            f"Could not regenerate target '{target_alias}' ({bs_node}[{logical_index}]). "
            f"Make sure no other node named '{target_alias}' exists in the scene.")
    if not isinstance(tgt_transform, str):
        tgt_transform = tgt_transform[0]

    geom_conns = cmds.listConnections(geom_plug, source=True, destination=False)
    if not geom_conns:
        raise RuntimeError(
            f"Regenerate succeeded for '{target_alias}' but no geometry connection was "
            f"established on {geom_plug}. The blendShape node may be in an unexpected state.")
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
    Uses a single regen mesh for both read and write (one undo step).
    Returns the number of vertices pruned.
    """
    saved = _save_shape_editor_selection()
    count = 0
    try:
        mesh_shape, tgt_transform, was_live = _get_regen_mesh(bs_node, logical_index)

        n_verts = cmds.polyEvaluate(mesh_shape, vertex=True)
        for i in range(n_verts):
            dx = cmds.getAttr(f"{mesh_shape}.pnts[{i}].pntx")
            dy = cmds.getAttr(f"{mesh_shape}.pnts[{i}].pnty")
            dz = cmds.getAttr(f"{mesh_shape}.pnts[{i}].pntz")
            if abs(dx) < 1e-6 and abs(dy) < 1e-6 and abs(dz) < 1e-6:
                continue
            if math.sqrt(dx*dx + dy*dy + dz*dz) < tolerance:
                cmds.setAttr(f"{mesh_shape}.pnts[{i}].pntx", 0.0)
                cmds.setAttr(f"{mesh_shape}.pnts[{i}].pnty", 0.0)
                cmds.setAttr(f"{mesh_shape}.pnts[{i}].pntz", 0.0)
                count += 1

        if not was_live:
            cmds.delete(tgt_transform)
    finally:
        _restore_shape_editor_selection(saved)

    return count


def _read_tweak_node(tweak_node):
    """
    Returns {vertex_index: (x, y, z)} for all non-zero entries in a Maya tweak node.
    Uses OpenMaya to iterate the vlist[0].vertex multi-compound reliably,
    since cmds.getAttr with multiIndices=True does not work on nested compounds.
    """
    from maya.api import OpenMaya as om
    tweaks = {}
    sel = om.MSelectionList()
    sel.add(tweak_node)
    dep = sel.getDependNode(0)
    fn = om.MFnDependencyNode(dep)
    vlist_plug = fn.findPlug("vlist", False)
    if vlist_plug.numElements() == 0:
        return tweaks
    vlist0 = vlist_plug.elementByLogicalIndex(0)
    # child(0) of vlist element is the 'vertex' compound array
    vertex_plug = vlist0.child(0)
    for i in range(vertex_plug.numElements()):
        elem = vertex_plug.elementByPhysicalIndex(i)
        logical_idx = elem.logicalIndex()
        x = elem.child(0).asFloat()  # xVertex
        y = elem.child(1).asFloat()  # yVertex
        z = elem.child(2).asFloat()  # zVertex
        if abs(x) > 1e-6 or abs(y) > 1e-6 or abs(z) > 1e-6:
            tweaks[logical_idx] = (x, y, z)
    return tweaks


def apply_mesh_moves_to_target(bs_node, base_mesh, logical_index, vtx_indices=None):
    """
    Absorbs vertex moves (tweak node or pnts[]) from the base mesh into the
    blendShape target at logical_index, then removes those moves.

    Replicates the Maya Shape Editor "Rebuild" workflow:
      1. sculptTarget(regenerate=True) → regen mesh live with stored target deltas.
      2. Sample current output positions (tweaks included) - rest positions,
         write the result into the regen mesh pnts[].
      3. Delete the tweak node (or zero pnts[]) to clean the base mesh.
      4. Delete the regen mesh → Maya commits the pnts[] back to the target.

    vtx_indices : optional list of int — restrict the operation to these vertex indices.
                  Unselected vertices keep their existing target deltas and tweaks intact.

    Returns the number of vertices affected.
    """
    from maya.api import OpenMaya as om

    # 1. Identify output shape (with tweaks) and intermediate shape (rest pose)
    all_shapes   = cmds.listRelatives(base_mesh, shapes=True) or []
    output_shape = next((s for s in all_shapes
                         if not cmds.getAttr(f"{s}.intermediateObject")), None)
    rest_shape   = next((s for s in all_shapes
                         if cmds.getAttr(f"{s}.intermediateObject")), None)
    if not output_shape or not rest_shape:
        raise RuntimeError(f"Could not find output/rest shapes on '{base_mesh}'.")

    # 2. Detect tweak source
    history     = cmds.listHistory(output_shape, pruneDagObjects=True) or []
    tweak_nodes = cmds.ls(history, type="tweak") or []
    pnt_indices = cmds.getAttr(f"{output_shape}.pnts", multiIndices=True) or []
    tweak_node  = tweak_nodes[0] if tweak_nodes else None

    if not tweak_node and not pnt_indices:
        raise RuntimeError("No vertex moves found on the mesh.")

    # 3. Sample current positions via OpenMaya (output has tweaks, rest has none)
    sel = om.MSelectionList()
    sel.add(output_shape)
    sel.add(rest_shape)
    deformed_pts = om.MFnMesh(sel.getDagPath(0)).getPoints(om.MSpace.kObject)
    rest_pts     = om.MFnMesh(sel.getDagPath(1)).getPoints(om.MSpace.kObject)

    vtx_set = set(vtx_indices) if vtx_indices else None

    new_deltas = {}
    for vi in range(len(deformed_pts)):
        if vtx_set is not None and vi not in vtx_set:
            continue
        dx = deformed_pts[vi].x - rest_pts[vi].x
        dy = deformed_pts[vi].y - rest_pts[vi].y
        dz = deformed_pts[vi].z - rest_pts[vi].z
        if abs(dx) > 1e-6 or abs(dy) > 1e-6 or abs(dz) > 1e-6:
            new_deltas[vi] = (dx, dy, dz)

    if not new_deltas:
        raise RuntimeError("No vertex differences found between output and rest pose.")

    # 4. Create regen mesh, overwrite its pnts[] with new_deltas, then commit
    saved = _save_shape_editor_selection()
    try:
        tgt_transform = cmds.sculptTarget(bs_node, e=True,
                                          target=logical_index, regenerate=True)
        if not tgt_transform:
            raise RuntimeError(f"sculptTarget returned None for {bs_node}[{logical_index}]")
        if not isinstance(tgt_transform, str):
            tgt_transform = tgt_transform[0]

        regen_shapes = cmds.listRelatives(tgt_transform, shapes=True) or []
        if not regen_shapes:
            cmds.delete(tgt_transform)
            raise RuntimeError("No shape found on regen mesh.")
        regen_shape = regen_shapes[0]

        # Zero any existing pnts[] that are no longer needed
        # (skip unselected vertices — their stored deltas must not be altered)
        existing_idx = cmds.getAttr(f"{regen_shape}.pnts", multiIndices=True) or []
        for vi in existing_idx:
            if vtx_set is not None and vi not in vtx_set:
                continue
            if vi not in new_deltas:
                cmds.setAttr(f"{regen_shape}.pnts[{vi}]", 0, 0, 0, type="float3")

        # Write new deltas
        for vi, (dx, dy, dz) in new_deltas.items():
            cmds.setAttr(f"{regen_shape}.pnts[{vi}].pntx", dx)
            cmds.setAttr(f"{regen_shape}.pnts[{vi}].pnty", dy)
            cmds.setAttr(f"{regen_shape}.pnts[{vi}].pntz", dz)

        # 5. Remove tweak source
        if vtx_set is not None:
            # Vertex selection mode: zero only the selected vertices' tweaks
            for vi in vtx_set:
                if tweak_node:
                    try:
                        cmds.setAttr(f"{tweak_node}.vlist[0].vertex[{vi}].xVertex", 0.0)
                        cmds.setAttr(f"{tweak_node}.vlist[0].vertex[{vi}].yVertex", 0.0)
                        cmds.setAttr(f"{tweak_node}.vlist[0].vertex[{vi}].zVertex", 0.0)
                    except Exception:
                        pass
                else:
                    try:
                        cmds.setAttr(f"{output_shape}.pnts[{vi}]", 0, 0, 0, type="float3")
                    except Exception:
                        pass
        elif tweak_node:
            cmds.delete(tweak_node)
        else:
            for i in pnt_indices:
                cmds.setAttr(f"{output_shape}.pnts[{i}]", 0, 0, 0, type="float3")

        # 6. Commit: deleting the regen mesh writes pnts[] back to the blendShape
        cmds.delete(tgt_transform)

    finally:
        _restore_shape_editor_selection(saved)

    return len(new_deltas)


def bake_deformers_to_targets(bs_node, base_mesh, logical_indices, vtx_indices=None):
    """
    For each target in logical_indices:
      - Activates the target at weight 1.0 (all others at 0).
      - Samples the output mesh vertex positions with all downstream deformers active.
      - Computes delta = baked_pos - rest_pos
        (rest = intermediate/base mesh positions, no deformers applied).
      - Writes the result as the target's new complete delta set.

    This formula is correct for any deformer type (blendShape, delta mush, etc.):
    after baking and deleting the downstream deformer, the shapes look identical
    to the pre-bake viewport result.

    vtx_indices : optional list of int — restrict the bake to these vertex indices.
                  Unselected vertices keep their existing target deltas unchanged.

    Returns the number of targets processed.
    """
    from maya.api import OpenMaya as om2

    # Output shape (non-intermediate) — reads through the full deformer stack
    out_shapes = cmds.listRelatives(base_mesh, shapes=True, noIntermediate=True, fullPath=True) or []
    if not out_shapes:
        raise RuntimeError(f"No output shape found on '{base_mesh}'")
    mesh_shape = out_shapes[0]

    # Intermediate shape — pure rest-pose positions, no deformers
    all_shapes = cmds.listRelatives(base_mesh, shapes=True, fullPath=True) or []
    int_shapes  = [s for s in all_shapes if cmds.getAttr(f"{s}.intermediateObject")]
    if not int_shapes:
        raise RuntimeError(f"No intermediate shape found on '{base_mesh}'")
    int_shape = int_shapes[0]

    def _sample(shape_name):
        sel = om2.MSelectionList()
        sel.add(shape_name)
        fn  = om2.MFnMesh(sel.getDagPath(0))
        return fn.getPoints(om2.MSpace.kObject)

    # Rest positions — sampled once from the intermediate shape (no deformers)
    rest_pts = _sample(int_shape)

    vtx_set = set(vtx_indices) if vtx_indices else None

    bs_state = zero_all_bs_weights(bs_node)
    try:
        EPS   = 1e-6
        count = 0

        for idx in logical_indices:
            try_set_weight(bs_node, idx, 1.0)
            cmds.dgeval(f"{mesh_shape}.worldMesh[0]")
            baked_pts = _sample(mesh_shape)
            try_set_weight(bs_node, idx, 0.0)

            new_deltas = {}
            for vi in range(len(rest_pts)):
                if vtx_set is not None and vi not in vtx_set:
                    continue
                dx = baked_pts[vi].x - rest_pts[vi].x
                dy = baked_pts[vi].y - rest_pts[vi].y
                dz = baked_pts[vi].z - rest_pts[vi].z
                if abs(dx) > EPS or abs(dy) > EPS or abs(dz) > EPS:
                    new_deltas[vi] = (dx, dy, dz)

            # Zero out any vertex present in original but absent from the baked set
            # (skip unselected vertices — their existing deltas must not be altered)
            original_deltas = get_target_deltas(bs_node, idx)
            for vi in original_deltas:
                if vtx_set is not None and vi not in vtx_set:
                    continue
                if vi not in new_deltas:
                    new_deltas[vi] = (0.0, 0.0, 0.0)

            _bake_deltas(bs_node, idx, new_deltas, original_deltas)
            count += 1

        return count

    finally:
        restore_all_bs_weights(bs_node, bs_state)


def _source_is_in_subgroup(bs_node, source_index):
    """
    Returns True if source_index is inside a sub-group in the targetDirectory tree
    (i.e. NOT at the root level).

    When source IS in a sub-group, callers must NOT call _insert_indices_after —
    the Shape Editor maintains its own in-memory directory state and our direct
    childIndices writes would diverge from it, causing targets to be duplicated
    across directories or orphaned when the user later re-groups them in the SE.
    """
    dir_indices = cmds.getAttr(f"{bs_node}.targetDirectory", multiIndices=True) or []

    # Find which directory hosts source_index
    host_dir = None
    for d in dir_indices:
        children = cmds.getAttr(f"{bs_node}.targetDirectory[{d}].childIndices") or []
        if source_index in children:
            host_dir = d
            break

    if host_dir is None:
        return False  # Not in any directory — root / flat list

    # If host_dir is referenced as a sub-group (negative value) by another directory,
    # the source is nested and not at root level.
    neg_ref = -host_dir
    for d in dir_indices:
        children = cmds.getAttr(f"{bs_node}.targetDirectory[{d}].childIndices") or []
        if neg_ref in children:
            return True  # host_dir is a sub-group

    return False  # host_dir is the root directory


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

def try_set_weight(bs_node, idx, value):
    """
    Sets bs_node.w[idx] to value.
    Silently skips if the attribute is locked or has an incoming connection
    (e.g. combination targets driven by a SDK or expression).
    Returns True if the value was set, False if skipped.
    """
    attr = f"{bs_node}.w[{idx}]"
    if cmds.getAttr(attr, lock=True):
        return False
    if cmds.listConnections(attr, source=True, destination=False, plugs=False):
        return False
    cmds.setAttr(attr, value)
    return True


def reset_all_target_weights(bs_node):
    """Sets every target weight on bs_node to 0.0. Skips connected/locked attrs. Returns the number of weights reset."""
    indices = cmds.getAttr(f"{bs_node}.weight", multiIndices=True) or []
    return sum(1 for i in indices if try_set_weight(bs_node, i, 0.0))


def zero_all_bs_weights(bs_node):
    """
    Zeros all blendShape weights on bs_node, temporarily disconnecting any driven
    (connected) attributes so they can be set freely.

    Returns a state dict: {idx: {'value': float, 'connections': [src_plug, ...]}}
    Pass the returned state to restore_all_bs_weights() to reconnect and restore.
    """
    indices = cmds.getAttr(f"{bs_node}.weight", multiIndices=True) or []
    state = {}
    for i in indices:
        attr = f"{bs_node}.w[{i}]"
        value = cmds.getAttr(attr)
        connections = cmds.listConnections(attr, source=True, destination=False, plugs=True) or []
        # Combination shape connections: skip entirely — disconnecting them destroys the target slot
        if any(cmds.nodeType(s.split(".")[0]) == "combinationShape" for s in connections):
            continue
        for src in connections:
            cmds.disconnectAttr(src, attr)
        if not cmds.getAttr(attr, lock=True):
            cmds.setAttr(attr, 0.0)
        state[i] = {"value": value, "connections": connections}
    return state


def restore_all_bs_weights(bs_node, state):
    """
    Restores blendShape weights and connections saved by zero_all_bs_weights().
    Connected attributes are reconnected (the controller value then drives the weight again).
    Free attributes are set back to their saved value.
    Slots that were deleted during the operation (no alias) are skipped — their
    connections have already been re-routed to the new slot by the caller.
    """
    for i, info in state.items():
        attr = f"{bs_node}.w[{i}]"
        if info["connections"]:
            for src in info["connections"]:
                if cmds.objExists(src.split(".")[0]):
                    cmds.connectAttr(src, attr, force=True)
        elif not cmds.getAttr(attr, lock=True):
            cmds.setAttr(attr, info["value"])


def purge_empty_bs_slots(bs_node):
    """
    Removes phantom and empty target slots from a blendShape node and cleans up
    stale targetDirectory references.

    Pass 1 — Phantom slots (no alias):
      A slot where weight[N] exists but has no alias.  Shows up as 'w[N]' in the
      Shape Editor with no name.  Common after Maya .shp re-import.
      - If inputTargetGroup[N] also exists → blendShapeDeleteTargetGroup (full removal)
      - Otherwise                          → removeMultiInstance on w[N] only

    Pass 2 — Empty named slots (alias present but no inputTargetGroup or empty ITI):
      Slots that appear in the Shape Editor with a name but carry no deformation data.
      Created for example by .shp imports that register a weight entry without geometry.
      - Removed via blendShapeDeleteTargetGroup if ITG exists, else removeMultiInstance.

    After purging, stale childIndices entries in targetDirectory are cleaned up.

    Called automatically before every new target creation via duplicate_target().
    Returns the total number of slots removed.
    """
    purged = 0

    # ── Pass 1: phantom slots (no alias) ──────────────────────────────────────
    for idx in sorted(cmds.getAttr(f"{bs_node}.w", multiIndices=True) or []):
        alias = cmds.aliasAttr(f"{bs_node}.w[{idx}]", q=True)
        if alias:
            continue  # named — skip for now, handled in pass 2

        try:
            mel.eval(f"blendShapeDeleteTargetGroup {bs_node} {idx};")
            print(f"  purged phantom slot [{idx}] on {bs_node}")
            purged += 1
        except Exception:
            try:
                cmds.removeMultiInstance(f"{bs_node}.w[{idx}]", b=True)
                print(f"  removed orphaned weight [{idx}] on {bs_node}")
                purged += 1
            except Exception as e:
                print(f"  could not purge slot [{idx}] on {bs_node}: {e}")

    # ── Pass 2: empty named slots (alias present, no usable ITG data) ─────────
    itg_indices = set(
        cmds.getAttr(f"{bs_node}.inputTarget[0].inputTargetGroup", multiIndices=True) or []
    )
    for idx in sorted(cmds.getAttr(f"{bs_node}.w", multiIndices=True) or []):
        alias = cmds.aliasAttr(f"{bs_node}.w[{idx}]", q=True)
        if not alias:
            continue  # still phantom (pass 1 couldn't remove it) — skip

        if idx not in itg_indices:
            # Named weight entry with no inputTargetGroup at all
            try:
                cmds.removeMultiInstance(f"{bs_node}.w[{idx}]", b=True)
                print(f"  removed named slot [{idx}] ({alias}) with no ITG on {bs_node}")
                purged += 1
            except Exception as e:
                print(f"  could not remove slot [{idx}] on {bs_node}: {e}")
        else:
            # ITG exists — check if it has any inputTargetItem data
            iti = cmds.getAttr(
                f"{bs_node}.inputTarget[0].inputTargetGroup[{idx}].inputTargetItem",
                multiIndices=True
            ) or []
            if not iti:
                try:
                    mel.eval(f"blendShapeDeleteTargetGroup {bs_node} {idx};")
                    print(f"  removed empty named slot [{idx}] ({alias}) on {bs_node}")
                    purged += 1
                except Exception as e:
                    print(f"  could not remove empty slot [{idx}] on {bs_node}: {e}")

    # ── Cleanup: remove stale targetDirectory references ──────────────────────
    if purged:
        valid = set(cmds.getAttr(f"{bs_node}.w", multiIndices=True) or [])
        for d in (cmds.getAttr(f"{bs_node}.targetDirectory", multiIndices=True) or []):
            attr = f"{bs_node}.targetDirectory[{d}].childIndices"
            children = list(cmds.getAttr(attr) or [])
            filtered = [c for c in children if c < 0 or c in valid]
            if filtered != children:
                cmds.setAttr(attr, filtered, type="Int32Array")

    return purged


def duplicate_target(bs_node, base_mesh, original_index, new_name, reorder=True, force_reorder=False, target_index=None):
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

    # 4. Find target logical index (caller may specify an index to reuse a freed slot)
    if target_index is not None:
        next_idx = target_index
    else:
        used_indices = cmds.getAttr(f"{bs_node}.w", multiIndices=True) or []
        next_idx = (max(used_indices) + 1) if used_indices else 0

    # 5. Add new target slot using the duplicate as geometry reference
    cmds.blendShape(bs_node, e=True, target=(base_mesh, next_idx, temp_dup, 1.0))

    # Set alias immediately — before disconnect/delete so the Shape Editor never
    # shows an unnamed or geometry-named ("_TEMP") slot if it refreshes mid-operation.
    try:
        cmds.aliasAttr(new_name, f"{bs_node}.w[{next_idx}]")
    except Exception:
        # Alias assignment failed — clean up the slot immediately so it doesn't
        # become a phantom w[N] target
        try:
            mel.eval(f"blendShapeDeleteTargetGroup {bs_node} {next_idx};")
        except Exception:
            pass
        raise RuntimeError(
            f"Could not assign alias '{new_name}' on {bs_node}.w[{next_idx}] — "
            f"name may already be in use or contain invalid characters."
        )

    # Disconnect inputGeomTarget before deleting temp_dup — if left connected to a
    # deleted node, Maya stores a dead reference that causes phantom slots when the
    # target is later deleted via the Shape Editor.
    igt_attr = (f"{bs_node}.inputTarget[0].inputTargetGroup[{next_idx}]"
                f".inputTargetItem[6000].inputGeomTarget")
    for src in (cmds.listConnections(igt_attr, source=True, plugs=True) or []):
        cmds.disconnectAttr(src, igt_attr)

    cmds.delete(temp_dup)

    # 6. Reorder Shape Editor display: insert just after the source target.
    # Skip when source is inside a sub-group — the SE caches the directory tree
    # in memory and our direct childIndices writes would diverge from that cache,
    # causing targets to appear in duplicate directories or become orphaned when
    # the user subsequently groups them inside the Shape Editor.
    if reorder and (force_reorder or not _source_is_in_subgroup(bs_node, original_index)):
        _insert_indices_after(bs_node, original_index, [next_idx])

    return next_idx


def create_split_target(bs_node, base_mesh, target_name, source_index, loc_idx, weights, deltas):
    """
    Creates a split target by:
      1. Duplicating the source target into a new blendShape slot
      2. Regenerating it to get a live mesh whose pnts[] contain the full deltas
      3. Scaling each pnts[vi] by w[loc_idx] in place — vertices where w==1 are skipped
      4. Deleting the regen mesh to bake the result back into the blendShape slot

    The caller (_run_split) is responsible for zeroing all blendShape weights
    before the split loop, and for restoring incoming connections (SDK etc.) to the
    new slot after this function returns (via the state it popped from bs_states).

    Outgoing connections and combination-shape incoming connections are handled here.

    Returns the logical index of the newly created target.
    """
    # 1. If a target with the same name exists: save its outgoing connections and any
    #    combination-shape incoming connections (regular incoming were disconnected by
    #    zero_all_bs_weights and are handled by the caller), then delete the old slot.
    saved_out  = []
    saved_in   = []   # combo-incoming only; regular incoming handled by caller
    saved_val  = 0.0
    existing = cmds.listAttr(f"{bs_node}.w", m=True) or []
    if target_name in existing:
        old_idx  = get_bs_weight_attribute_logical_index(bs_node, target_name)
        old_attr = f"{bs_node}.weight[{old_idx}]"
        saved_val = cmds.getAttr(old_attr)
        saved_out = cmds.listConnections(old_attr, plugs=True, s=False, d=True) or []
        # Incoming at this point: only combo connections (regular ones were disconnected earlier)
        saved_in  = cmds.listConnections(old_attr, plugs=True, s=True,  d=False) or []
        for src in saved_in:
            cmds.disconnectAttr(src, old_attr)
        for dst in saved_out:
            cmds.disconnectAttr(old_attr, dst)
        mel.eval(f"blendShapeDeleteTargetGroup {bs_node} {old_idx};")
        print(f"  overriding existing target : {target_name}")

    # 2. Duplicate source target — reorder handled below after sculptTarget cycle
    target_idx = duplicate_target(bs_node, base_mesh, source_index, target_name, reorder=False)

    # Restore outgoing + combo-incoming connections onto the new slot
    new_attr = f"{bs_node}.weight[{target_idx}]"
    for dst in saved_out:
        cmds.connectAttr(new_attr, dst, force=True)
    for src in saved_in:
        cmds.connectAttr(src, new_attr, force=True)

    # Regenerate the duplicate → live mesh with full source deltas in pnts[]
    saved = _save_shape_editor_selection()
    try:
        regen_mesh = cmds.sculptTarget(bs_node, e=True, target=target_idx, regenerate=True)
        regen_mesh = regen_mesh if isinstance(regen_mesh, str) else regen_mesh[0]

        # 3. Scale each delta by w — skip w==1 (vertex keeps full delta, no write needed)
        for vi, (dx, dy, dz) in deltas.items():
            w_list = weights.get(vi)
            w      = w_list[loc_idx] if w_list is not None else 0.0
            w      = max(0.0, min(1.0, w))
            if abs(w - 1.0) < 1e-7:
                continue
            cmds.setAttr(f"{regen_mesh}.pnts[{vi}].pntx", dx * w)
            cmds.setAttr(f"{regen_mesh}.pnts[{vi}].pnty", dy * w)
            cmds.setAttr(f"{regen_mesh}.pnts[{vi}].pntz", dz * w)

        # 4. Delete regen mesh — bakes modified pnts[] back into the blendShape slot
        cmds.delete(regen_mesh)
    finally:
        _restore_shape_editor_selection(saved)

    # 5. Position in targetDirectory — skip if source is inside a sub-group
    #    (see _source_is_in_subgroup for explanation).
    if not _source_is_in_subgroup(bs_node, source_index):
        _insert_indices_after(bs_node, source_index, [target_idx])

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
    "Topology": (0, None),
}


def _setup_topo_sym(topo_edge):
    """Sets symmetricModelling to topology mode. Returns previous symmetry state."""
    if not topo_edge:
        raise ValueError("Topology symmetry requires a central edge. Use 'Get Edge' in the UI.")
    was_sym = cmds.symmetricModelling(q=True, symmetry=True)
    cmds.symmetricModelling(topo_edge, e=True, topoSymmetry=True)
    return was_sym


def _restore_sym_state(was_sym):
    """Restores symmetricModelling state after a topology flip/mirror."""
    cmds.symmetricModelling(e=True, topoSymmetry=False)
    cmds.symmetricModelling(e=True, symmetry=was_sym)


def do_flip_target(bs_node, logical_index, base_shape, mirror_direction,
                   symmetry_axis="Topology", topo_edge=None):
    space, axis = FLIP_AXIS_MAP.get(symmetry_axis, (1, 'x'))
    if space == 0:  # Topology
        was_sym = _setup_topo_sym(topo_edge)
        try:
            cmds.blendShape(bs_node, edit=True,
                            flipTarget=[(0, logical_index)],
                            mirrorDirection=mirror_direction,
                            symmetrySpace=0)
        finally:
            _restore_sym_state(was_sym)
    else:
        was_sym = cmds.symmetricModelling(q=True, symmetry=True)
        try:
            cmds.blendShape(bs_node, edit=True,
                            flipTarget=[(0, logical_index)],
                            mirrorDirection=mirror_direction,
                            symmetrySpace=space,
                            symmetryAxis=axis)
        finally:
            cmds.symmetricModelling(e=True, symmetry=was_sym)
    print(f"  ✓ Flip : {bs_node}.w[{logical_index}] ({symmetry_axis})")




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


@undo_chunk
def combine_target_deltas(bs_node_a, idx_a, donors, operation='add', vtx_indices=None):
    """
    Adds or subtracts donor target deltas onto target A (in-place).

    bs_node_a   : blendShape node of the receiver target
    idx_a       : logical index of the receiver target
    donors      : list of (bs_node, idx) tuples — delta sources
    operation   : 'add' or 'sub'
    vtx_indices : list of vertex indices to restrict the operation, or None for all

    Reads each donor's raw pnts[] deltas via get_target_deltas, accumulates them,
    then regens A's mesh, applies the accumulated delta, and bakes.
    Returns the number of vertices written.
    """
    sign = 1.0 if operation == 'add' else -1.0
    vtx_set = set(vtx_indices) if vtx_indices is not None else None

    # Accumulate donor deltas
    accumulated = {}
    for bs_node_b, idx_b in donors:
        for vi, (dx, dy, dz) in get_target_deltas(bs_node_b, idx_b).items():
            if vtx_set is not None and vi not in vtx_set:
                continue
            if vi in accumulated:
                ax, ay, az = accumulated[vi]
                accumulated[vi] = (ax + dx, ay + dy, az + dz)
            else:
                accumulated[vi] = (dx, dy, dz)

    if not accumulated:
        return 0

    # Regen A and apply accumulated delta
    mesh_a, tgt_a, was_live = _get_regen_mesh(bs_node_a, idx_a)
    written = 0
    try:
        for vi, (ddx, ddy, ddz) in accumulated.items():
            ax = cmds.getAttr(f"{mesh_a}.pnts[{vi}].pntx")
            ay = cmds.getAttr(f"{mesh_a}.pnts[{vi}].pnty")
            az = cmds.getAttr(f"{mesh_a}.pnts[{vi}].pntz")
            cmds.setAttr(f"{mesh_a}.pnts[{vi}].pntx", ax + sign * ddx)
            cmds.setAttr(f"{mesh_a}.pnts[{vi}].pnty", ay + sign * ddy)
            cmds.setAttr(f"{mesh_a}.pnts[{vi}].pntz", az + sign * ddz)
            written += 1
    finally:
        if not was_live:
            cmds.delete(tgt_a)

    scope = f"{len(vtx_indices)} vtx" if vtx_indices is not None else "all verts"
    op_sym = "+" if operation == 'add' else "\u2212"
    print(f"  Delta {op_sym} : {written} verts written on {bs_node_a}.w[{idx_a}]  ({scope})")
    return written


@undo_chunk
def transfer_target_deltas(bs_node_a, idx_a, bs_node_b, idx_b, vtx_indices=None):
    """
    Transfers (moves) deltas from B to A:
      - vtx_indices provided : transfers B's deltas at those verts only
      - vtx_indices is None  : transfers all vertices that have non-zero deltas on B
    Adds the transferred deltas onto A, then zeros them on B.
    Returns the number of vertices transferred.
    """
    deltas_b = get_target_deltas(bs_node_b, idx_b)
    if not deltas_b:
        return 0

    if vtx_indices is None:
        to_transfer = deltas_b  # all delta verts on B
    else:
        to_transfer = {vi: deltas_b[vi] for vi in vtx_indices if vi in deltas_b}

    if not to_transfer:
        return 0

    # Add to A
    mesh_a, tgt_a, was_live_a = _get_regen_mesh(bs_node_a, idx_a)
    try:
        for vi, (dx, dy, dz) in to_transfer.items():
            ax = cmds.getAttr(f"{mesh_a}.pnts[{vi}].pntx")
            ay = cmds.getAttr(f"{mesh_a}.pnts[{vi}].pnty")
            az = cmds.getAttr(f"{mesh_a}.pnts[{vi}].pntz")
            cmds.setAttr(f"{mesh_a}.pnts[{vi}].pntx", ax + dx)
            cmds.setAttr(f"{mesh_a}.pnts[{vi}].pnty", ay + dy)
            cmds.setAttr(f"{mesh_a}.pnts[{vi}].pntz", az + dz)
    finally:
        if not was_live_a:
            cmds.delete(tgt_a)

    # Zero out from B
    mesh_b, tgt_b, was_live_b = _get_regen_mesh(bs_node_b, idx_b)
    try:
        for vi in to_transfer:
            cmds.setAttr(f"{mesh_b}.pnts[{vi}].pntx", 0.0)
            cmds.setAttr(f"{mesh_b}.pnts[{vi}].pnty", 0.0)
            cmds.setAttr(f"{mesh_b}.pnts[{vi}].pntz", 0.0)
    finally:
        if not was_live_b:
            cmds.delete(tgt_b)

    scope = f"{len(vtx_indices)} vtx" if vtx_indices is not None else "all delta verts"
    print(f"  XFER : {len(to_transfer)} verts  {bs_node_b}.w[{idx_b}] \u2192 {bs_node_a}.w[{idx_a}]  ({scope})")
    return len(to_transfer)


def replace_target_deltas(bs_node_a, idx_a, bs_node_b, idx_b, vtx_indices=None):
    """
    Replaces A's deltas with B's (A = B). B is left intact.
      - vtx_indices provided : replaces only those verts on A with B's values
      - vtx_indices is None  : replaces all of A's delta set with B's delta set
    Verts that were in A but not in B are zeroed out.
    Returns the number of vertices written.
    """
    deltas_a = get_target_deltas(bs_node_a, idx_a)
    deltas_b = get_target_deltas(bs_node_b, idx_b)

    if vtx_indices is not None:
        vtx_set  = set(vtx_indices)
        to_write = {vi: v for vi, v in deltas_b.items() if vi in vtx_set}
        to_zero  = {vi for vi in deltas_a if vi in vtx_set and vi not in deltas_b}
    else:
        to_write = deltas_b
        to_zero  = {vi for vi in deltas_a if vi not in deltas_b}

    mesh_a, tgt_a, was_live_a = _get_regen_mesh(bs_node_a, idx_a)
    try:
        for vi in to_zero:
            cmds.setAttr(f"{mesh_a}.pnts[{vi}].pntx", 0.0)
            cmds.setAttr(f"{mesh_a}.pnts[{vi}].pnty", 0.0)
            cmds.setAttr(f"{mesh_a}.pnts[{vi}].pntz", 0.0)
        for vi, (dx, dy, dz) in to_write.items():
            cmds.setAttr(f"{mesh_a}.pnts[{vi}].pntx", dx)
            cmds.setAttr(f"{mesh_a}.pnts[{vi}].pnty", dy)
            cmds.setAttr(f"{mesh_a}.pnts[{vi}].pntz", dz)
    finally:
        if not was_live_a:
            cmds.delete(tgt_a)

    scope = f"{len(vtx_indices)} vtx" if vtx_indices is not None else "all delta verts"
    print(f"  REPL : {len(to_write)} verts  {bs_node_b}.w[{idx_b}] \u2192\u2192 {bs_node_a}.w[{idx_a}]  ({scope})")
    return len(to_write)


def swap_target_deltas(bs_node_a, idx_a, bs_node_b, idx_b, vtx_indices=None):
    """
    Swaps deltas between A and B (A \u2194 B), pure replace in both directions.
      - vtx_indices provided : swaps only those verts
      - vtx_indices is None  : swaps the full delta sets
    Verts present in one target but not the other are zeroed on the receiving side.
    Returns the number of vertices affected (max of both sides).
    """
    deltas_a = get_target_deltas(bs_node_a, idx_a)
    deltas_b = get_target_deltas(bs_node_b, idx_b)

    if vtx_indices is not None:
        vtx_set  = set(vtx_indices)
        new_a    = {vi: v for vi, v in deltas_b.items() if vi in vtx_set}
        new_b    = {vi: v for vi, v in deltas_a.items() if vi in vtx_set}
        orig_a   = {vi for vi in deltas_a if vi in vtx_set}
        orig_b   = {vi for vi in deltas_b if vi in vtx_set}
    else:
        new_a, new_b = deltas_b, deltas_a
        orig_a, orig_b = set(deltas_a), set(deltas_b)

    mesh_a, tgt_a, was_live_a = _get_regen_mesh(bs_node_a, idx_a)
    try:
        for vi in orig_a:
            if vi not in new_a:
                cmds.setAttr(f"{mesh_a}.pnts[{vi}].pntx", 0.0)
                cmds.setAttr(f"{mesh_a}.pnts[{vi}].pnty", 0.0)
                cmds.setAttr(f"{mesh_a}.pnts[{vi}].pntz", 0.0)
        for vi, (dx, dy, dz) in new_a.items():
            cmds.setAttr(f"{mesh_a}.pnts[{vi}].pntx", dx)
            cmds.setAttr(f"{mesh_a}.pnts[{vi}].pnty", dy)
            cmds.setAttr(f"{mesh_a}.pnts[{vi}].pntz", dz)
    finally:
        if not was_live_a:
            cmds.delete(tgt_a)

    mesh_b, tgt_b, was_live_b = _get_regen_mesh(bs_node_b, idx_b)
    try:
        for vi in orig_b:
            if vi not in new_b:
                cmds.setAttr(f"{mesh_b}.pnts[{vi}].pntx", 0.0)
                cmds.setAttr(f"{mesh_b}.pnts[{vi}].pnty", 0.0)
                cmds.setAttr(f"{mesh_b}.pnts[{vi}].pntz", 0.0)
        for vi, (dx, dy, dz) in new_b.items():
            cmds.setAttr(f"{mesh_b}.pnts[{vi}].pntx", dx)
            cmds.setAttr(f"{mesh_b}.pnts[{vi}].pnty", dy)
            cmds.setAttr(f"{mesh_b}.pnts[{vi}].pntz", dz)
    finally:
        if not was_live_b:
            cmds.delete(tgt_b)

    n = max(len(new_a), len(new_b))
    scope = f"{len(vtx_indices)} vtx" if vtx_indices is not None else "all delta verts"
    print(f"  SWAP : {n} verts  {bs_node_a}.w[{idx_a}] \u2194 {bs_node_b}.w[{idx_b}]  ({scope})")
    return n


def multiply_shapes_deltas(bs_node_a, idx_a, bs_node_b, idx_b, vtx_indices=None):
    """
    Multiplies A's deltas by B's deltas component-wise (A[vi] *= B[vi]).
    Vertices present in A but not in B are zeroed (B contributes 0).
    Vertices in B but not in A are ignored (nothing to multiply).
    Returns the number of vertices affected.
    """
    deltas_a = get_target_deltas(bs_node_a, idx_a)
    deltas_b = get_target_deltas(bs_node_b, idx_b)

    if vtx_indices is not None:
        vtx_set  = set(vtx_indices)
        work_a   = {vi: v for vi, v in deltas_a.items() if vi in vtx_set}
    else:
        work_a = deltas_a

    if not work_a:
        return 0

    mesh_a, tgt_a, was_live_a = _get_regen_mesh(bs_node_a, idx_a)
    try:
        for vi, (ax, ay, az) in work_a.items():
            bx, by, bz = deltas_b.get(vi, (0.0, 0.0, 0.0))
            cmds.setAttr(f"{mesh_a}.pnts[{vi}].pntx", ax * bx)
            cmds.setAttr(f"{mesh_a}.pnts[{vi}].pnty", ay * by)
            cmds.setAttr(f"{mesh_a}.pnts[{vi}].pntz", az * bz)
    finally:
        if not was_live_a:
            cmds.delete(tgt_a)

    scope = f"{len(vtx_indices)} vtx" if vtx_indices is not None else "all delta verts"
    print(f"  MULT : {len(work_a)} verts  {bs_node_a}.w[{idx_a}] \u00d7 {bs_node_b}.w[{idx_b}]  ({scope})")
    return len(work_a)


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

    Uses a single regen mesh for both read and write (one undo step).

    opacity     : 0.0–1.0  blend weight between original and one relaxed pass.
    vtx_indices : optional list of ints to restrict the operation.
    """
    base_mesh = get_base_mesh(bs_node)
    adj       = _build_adjacency(base_mesh)

    saved = _save_shape_editor_selection()
    try:
        mesh_shape, tgt_transform, was_live = _get_regen_mesh(bs_node, logical_index)

        # Read all deltas directly from pnts[]
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

        # One Laplacian pass in delta-vector space
        snapshot = dict(deltas)
        for vi in vtx_set:
            nbrs = adj.get(vi, [])
            if not nbrs:
                continue
            sx = sy = sz = 0.0
            for nb in nbrs:
                d = snapshot.get(nb, (0.0, 0.0, 0.0))
                sx += d[0]; sy += d[1]; sz += d[2]
            n = len(nbrs)
            ox, oy, oz = deltas.get(vi, (0.0, 0.0, 0.0))
            nx = ox + (sx / n - ox) * opacity
            ny = oy + (sy / n - oy) * opacity
            nz = oz + (sz / n - oz) * opacity
            if abs(nx - ox) > 1e-8 or abs(ny - oy) > 1e-8 or abs(nz - oz) > 1e-8:
                cmds.setAttr(f"{mesh_shape}.pnts[{vi}].pntx", nx)
                cmds.setAttr(f"{mesh_shape}.pnts[{vi}].pnty", ny)
                cmds.setAttr(f"{mesh_shape}.pnts[{vi}].pntz", nz)

        if not was_live:
            cmds.delete(tgt_transform)
    finally:
        _restore_shape_editor_selection(saved)

    scope = f"{len(vtx_set)} vtx" if vtx_indices is not None else "all verts"
    print(f"  Relax Deltas: opacity={opacity:.2f}  ({scope})")


def hammer_target_deltas(bs_node, logical_index, vtx_indices, opacity=1.0, progress_cb=None, n_laplacian=1, mode="volume"):
    """
    Hammer applied to blendShape deltas — two modes:

    mode="volume"  — Spatial IDW in neutral-space (regen_pos − delta).
        k-nearest Euclidean neighbors via hash grid, IDW(1/dist²) weighting.
        Converges to a spatially-smooth delta field regardless of mesh topology.
        Good for volume-preserving corrections where rest-pose proximity matters.

    mode="surface" — Topological Laplacian along mesh edges (1-ring connectivity).
        Each selected vertex converges to the uniform average of its edge-connected
        neighbors' deltas. Respects mesh topology: never bleeds across disconnected
        regions or across seams. Classic surface-aware smoothing.

    Both modes use Dirichlet boundary conditions:
        - Selected vertices are the unknowns (updated each pass).
        - Non-selected vertices are frozen anchors at their original delta.

    The loop runs to convergence (max 200 passes, tol=1e-4). `opacity` then blends
    between the original deltas and the converged result.

    vtx_indices : list of ints — must be non-empty (selection required).
    opacity     : 0.0–1.0  blend weight between original and converged result.
    """
    if not vtx_indices:
        return

    import math
    from maya.api import OpenMaya as om

    vtx_set = set(vtx_indices)
    _MAX_PASSES = 200
    _TOL        = 1e-4

    saved = _save_shape_editor_selection()
    try:
        mesh_shape, tgt_transform, was_live = _get_regen_mesh(bs_node, logical_index)
        n_verts = cmds.polyEvaluate(mesh_shape, vertex=True)

        # Read deltas from pnts[]
        deltas = {}
        for i in range(n_verts):
            dx = cmds.getAttr(f"{mesh_shape}.pnts[{i}].pntx")
            dy = cmds.getAttr(f"{mesh_shape}.pnts[{i}].pnty")
            dz = cmds.getAttr(f"{mesh_shape}.pnts[{i}].pntz")
            if abs(dx) > 1e-6 or abs(dy) > 1e-6 or abs(dz) > 1e-6:
                deltas[i] = (dx, dy, dz)

        current      = dict(deltas)
        n_passes_run = _MAX_PASSES
        adj          = None   # built lazily, shared between surface + n_laplacian

        if mode == "surface":
            # ── Topological Laplacian (1-ring edge neighbors) ─────────────────
            base_mesh = get_base_mesh(bs_node)
            adj = _build_adjacency(base_mesh)
            for _pass in range(_MAX_PASSES):
                if progress_cb:
                    progress_cb(_pass, _MAX_PASSES, f"Hammer pass {_pass + 1}…")
                snapshot   = dict(current)
                max_change = 0.0
                for vi in vtx_set:
                    nbrs = adj.get(vi, [])
                    if not nbrs:
                        continue
                    sx = sy = sz = 0.0
                    for nb in nbrs:
                        d = snapshot.get(nb, (0.0, 0.0, 0.0))
                        sx += d[0]; sy += d[1]; sz += d[2]
                    n      = len(nbrs)
                    new_d  = (sx / n, sy / n, sz / n)
                    old_d  = current.get(vi, (0.0, 0.0, 0.0))
                    chg    = abs(new_d[0] - old_d[0]) + abs(new_d[1] - old_d[1]) + abs(new_d[2] - old_d[2])
                    if chg > max_change:
                        max_change = chg
                    current[vi] = new_d
                if max_change < _TOL:
                    n_passes_run = _pass + 1
                    break

        else:
            # ── Spatial IDW in neutral space (regen_pos − delta) ──────────────
            om_sel    = om.MSelectionList()
            om_sel.add(mesh_shape)
            regen_pts = om.MFnMesh(om_sel.getDagPath(0)).getPoints(om.MSpace.kObject)

            neutral = []
            for i in range(n_verts):
                p = regen_pts[i]
                d = deltas.get(i, (0.0, 0.0, 0.0))
                neutral.append((p.x - d[0], p.y - d[1], p.z - d[2]))

            xs = [p[0] for p in neutral]
            ys = [p[1] for p in neutral]
            zs = [p[2] for p in neutral]
            bbox_diag = math.sqrt(
                (max(xs) - min(xs)) ** 2 +
                (max(ys) - min(ys)) ** 2 +
                (max(zs) - min(zs)) ** 2
            ) or 1.0
            n_cells   = max(n_verts // 40, 8)
            cell_size = bbox_diag / max(n_cells ** (1.0 / 3.0), 1.0)

            grid = {}
            for vi, (x, y, z) in enumerate(neutral):
                c = (math.floor(x / cell_size),
                     math.floor(y / cell_size),
                     math.floor(z / cell_size))
                grid.setdefault(c, []).append(vi)

            k       = min(max(8, n_verts // 50), 32)
            eps_idw = (bbox_diag / max(n_verts ** 0.5, 1.0)) ** 2
            spatial_neighbors = {}
            for vi in vtx_indices:
                x, y, z = neutral[vi]
                cx = math.floor(x / cell_size)
                cy = math.floor(y / cell_size)
                cz = math.floor(z / cell_size)
                candidates = set()
                for radius in range(1, 6):
                    for ddx in range(-radius, radius + 1):
                        for ddy in range(-radius, radius + 1):
                            for ddz in range(-radius, radius + 1):
                                c = (cx + ddx, cy + ddy, cz + ddz)
                                if c in grid:
                                    candidates.update(grid[c])
                    if len(candidates) >= k + 1:
                        break
                dists = []
                for vj in candidates:
                    if vj == vi:
                        continue
                    qx, qy, qz = neutral[vj]
                    d2 = (x - qx) ** 2 + (y - qy) ** 2 + (z - qz) ** 2
                    dists.append((d2, vj))
                dists.sort()
                spatial_neighbors[vi] = [(vj, d2) for d2, vj in dists[:k]]

            for _pass in range(_MAX_PASSES):
                if progress_cb:
                    progress_cb(_pass, _MAX_PASSES, f"Hammer pass {_pass + 1}…")
                snapshot   = dict(current)
                max_change = 0.0
                for vi in vtx_set:
                    sx = sy = sz = total_w = 0.0
                    for vj, dist2 in spatial_neighbors[vi]:
                        w = 1.0 / (dist2 + eps_idw)
                        d = snapshot.get(vj, (0.0, 0.0, 0.0))
                        sx += w * d[0]; sy += w * d[1]; sz += w * d[2]
                        total_w += w
                    if total_w > 0:
                        new_d = (sx / total_w, sy / total_w, sz / total_w)
                        old_d = current.get(vi, (0.0, 0.0, 0.0))
                        chg   = abs(new_d[0] - old_d[0]) + abs(new_d[1] - old_d[1]) + abs(new_d[2] - old_d[2])
                        if chg > max_change:
                            max_change = chg
                        current[vi] = new_d
                if max_change < _TOL:
                    n_passes_run = _pass + 1
                    break

        # Blend converged result with original by opacity
        if opacity < 1.0:
            for vi in vtx_set:
                orig = deltas.get(vi, (0.0, 0.0, 0.0))
                conv = current.get(vi, (0.0, 0.0, 0.0))
                current[vi] = (
                    orig[0] + (conv[0] - orig[0]) * opacity,
                    orig[1] + (conv[1] - orig[1]) * opacity,
                    orig[2] + (conv[2] - orig[2]) * opacity,
                )

        # Optional topological Laplacian post-smooth
        if n_laplacian > 0:
            if adj is None:
                adj = _build_adjacency(get_base_mesh(bs_node))
            for _ in range(n_laplacian):
                snapshot = dict(current)
                for vi in vtx_set:
                    nbrs = adj.get(vi, [])
                    if not nbrs:
                        continue
                    sx = sy = sz = 0.0
                    for nb in nbrs:
                        d = snapshot.get(nb, (0.0, 0.0, 0.0))
                        sx += d[0]; sy += d[1]; sz += d[2]
                    n      = len(nbrs)
                    ox, oy, oz = current.get(vi, (0.0, 0.0, 0.0))
                    current[vi] = (ox + (sx / n - ox) * 0.5,
                                   oy + (sy / n - oy) * 0.5,
                                   oz + (sz / n - oz) * 0.5)

        # Write back only changed verts
        for vi in vtx_set:
            nx, ny, nz = current.get(vi, (0.0, 0.0, 0.0))
            ox, oy, oz = deltas.get(vi, (0.0, 0.0, 0.0))
            if abs(nx - ox) > 1e-8 or abs(ny - oy) > 1e-8 or abs(nz - oz) > 1e-8:
                cmds.setAttr(f"{mesh_shape}.pnts[{vi}].pntx", nx)
                cmds.setAttr(f"{mesh_shape}.pnts[{vi}].pnty", ny)
                cmds.setAttr(f"{mesh_shape}.pnts[{vi}].pntz", nz)

        if not was_live:
            cmds.delete(tgt_transform)
    finally:
        _restore_shape_editor_selection(saved)

    print(f"  Hammer Deltas ({mode}): {n_passes_run}/{_MAX_PASSES} passes, {len(vtx_indices)} vtx, opacity={opacity:.0%}")


def average_target_deltas(bs_node, logical_index, vtx_indices, opacity=1.0, mode="volume"):
    """
    Averages blendShape deltas for selected vertices — two modes:

    mode="volume"  — Global arithmetic mean: all selected vertices are set to the
                     same single average delta value. Useful to level a cluster of
                     verts to a common midpoint displacement.

    mode="surface" — Local 1-ring mean: each selected vertex is set to the uniform
                     average of its edge-connected neighbors (selected or not).
                     Non-selected neighbors act as frozen anchors, so the operation
                     fades naturally at the selection boundary.

    vtx_indices : list of ints — must be non-empty (selection required).
    opacity     : 0.0–1.0  blend weight between original and the averaged value.
    """
    if not vtx_indices:
        return

    saved = _save_shape_editor_selection()
    try:
        mesh_shape, tgt_transform, was_live = _get_regen_mesh(bs_node, logical_index)

        # Read all deltas for the selected verts (+ neighbors may be needed)
        vals = {}
        for vi in vtx_indices:
            dx = cmds.getAttr(f"{mesh_shape}.pnts[{vi}].pntx")
            dy = cmds.getAttr(f"{mesh_shape}.pnts[{vi}].pnty")
            dz = cmds.getAttr(f"{mesh_shape}.pnts[{vi}].pntz")
            vals[vi] = (dx, dy, dz)

        if mode == "surface":
            # ── Local 1-ring average ──────────────────────────────────────────
            adj = _build_adjacency(get_base_mesh(bs_node))
            # Also read deltas for non-selected neighbors (frozen anchors)
            nbr_set = set()
            for vi in vtx_indices:
                for nb in adj.get(vi, []):
                    if nb not in vals:
                        nbr_set.add(nb)
            for nb in nbr_set:
                dx = cmds.getAttr(f"{mesh_shape}.pnts[{nb}].pntx")
                dy = cmds.getAttr(f"{mesh_shape}.pnts[{nb}].pnty")
                dz = cmds.getAttr(f"{mesh_shape}.pnts[{nb}].pntz")
                vals[nb] = (dx, dy, dz)

            result = {}
            for vi in vtx_indices:
                nbrs = adj.get(vi, [])
                if not nbrs:
                    result[vi] = vals[vi]
                    continue
                sx = sy = sz = 0.0
                for nb in nbrs:
                    d = vals.get(nb, (0.0, 0.0, 0.0))
                    sx += d[0]; sy += d[1]; sz += d[2]
                n = len(nbrs)
                result[vi] = (sx / n, sy / n, sz / n)

            for vi in vtx_indices:
                ox, oy, oz = vals[vi]
                tx, ty, tz = result[vi]
                nx = ox + (tx - ox) * opacity
                ny = oy + (ty - oy) * opacity
                nz = oz + (tz - oz) * opacity
                if abs(nx - ox) > 1e-8 or abs(ny - oy) > 1e-8 or abs(nz - oz) > 1e-8:
                    cmds.setAttr(f"{mesh_shape}.pnts[{vi}].pntx", nx)
                    cmds.setAttr(f"{mesh_shape}.pnts[{vi}].pnty", ny)
                    cmds.setAttr(f"{mesh_shape}.pnts[{vi}].pntz", nz)

            print(f"  Average Deltas (surface): opacity={opacity:.2f}, {len(vtx_indices)} vtx")

        else:
            # ── Global arithmetic mean ────────────────────────────────────────
            mx = my = mz = 0.0
            for dx, dy, dz in vals.values():
                mx += dx; my += dy; mz += dz
            n = len(vtx_indices)
            mx /= n; my /= n; mz /= n

            for vi in vtx_indices:
                ox, oy, oz = vals[vi]
                nx = ox + (mx - ox) * opacity
                ny = oy + (my - oy) * opacity
                nz = oz + (mz - oz) * opacity
                if abs(nx - ox) > 1e-8 or abs(ny - oy) > 1e-8 or abs(nz - oz) > 1e-8:
                    cmds.setAttr(f"{mesh_shape}.pnts[{vi}].pntx", nx)
                    cmds.setAttr(f"{mesh_shape}.pnts[{vi}].pnty", ny)
                    cmds.setAttr(f"{mesh_shape}.pnts[{vi}].pntz", nz)

            print(f"  Average Deltas (volume): opacity={opacity:.2f}, {len(vtx_indices)} vtx → ({mx:.4f}, {my:.4f}, {mz:.4f})")

        if not was_live:
            cmds.delete(tgt_transform)
    finally:
        _restore_shape_editor_selection(saved)


def _collect_magnitudes(bs_node, logical_index):
    """
    Regenerates the target, reads per-vertex delta magnitudes from pnts[],
    then immediately deletes the regen mesh (bakes back the unchanged deltas).
    Returns (magnitudes_dict {vi: mag}, n_verts).
    Used when we need magnitude data without keeping the regen mesh alive.
    """
    saved = _save_shape_editor_selection()
    try:
        regen = cmds.sculptTarget(bs_node, e=True, target=logical_index, regenerate=True)
        regen = regen if isinstance(regen, str) else regen[0]
        n_verts = cmds.polyEvaluate(regen, vertex=True)
        magnitudes = {}
        for i in range(n_verts):
            dx = cmds.getAttr(f"{regen}.pnts[{i}].pntx")
            dy = cmds.getAttr(f"{regen}.pnts[{i}].pnty")
            dz = cmds.getAttr(f"{regen}.pnts[{i}].pntz")
            mag = math.sqrt(dx*dx + dy*dy + dz*dz)
            if mag > 1e-6:
                magnitudes[i] = mag
        cmds.delete(regen)
    finally:
        _restore_shape_editor_selection(saved)
    return magnitudes, n_verts


def _ensure_geom_target_connected(bs_node, logical_index, working_mesh):
    """
    After applying a deformer (skinCluster, cluster…) to a regen mesh, Maya can
    break the shape.outMesh → blendShape.inputGeomTarget connection.
    This helper verifies the connection is still live and force-reconnects it if not.
    """
    geom_plug = (f"{bs_node}.inputTarget[0]"
                 f".inputTargetGroup[{logical_index}]"
                 f".inputTargetItem[6000].inputGeomTarget")
    conns = cmds.listConnections(geom_plug, source=True, plugs=True) or []
    if conns:
        return  # still connected — nothing to do

    shapes = cmds.listRelatives(working_mesh, shapes=True, type="mesh") or []
    if not shapes:
        return
    # Prefer the non-intermediate shape
    non_intermediate = [s for s in shapes
                        if not cmds.getAttr(f"{s}.intermediateObject")]
    shape = non_intermediate[0] if non_intermediate else shapes[0]
    cmds.connectAttr(f"{shape}.outMesh", geom_plug, force=True)
    print(f"  ⚠ Re-connected {shape}.outMesh → {geom_plug}")


def _add_empty_bs_target(bs_node, base_mesh, ref_logical_index, new_name):
    """
    Add a new empty (zero-delta) target to the blendShape, positioned just after
    ref_logical_index in the Shape Editor.
    Returns the logical index of the new target.
    """
    purge_empty_bs_slots(bs_node)

    temp = cmds.duplicate(base_mesh, name=f"_temp_{new_name}")[0]
    try:
        used_indices = cmds.getAttr(f"{bs_node}.w", multiIndices=True) or []
        next_idx = (max(used_indices) + 1) if used_indices else 0
        cmds.blendShape(bs_node, e=True, target=(base_mesh, next_idx, temp, 1.0))
    finally:
        cmds.delete(temp)

    try:
        cmds.aliasAttr(new_name, f"{bs_node}.w[{next_idx}]")
    except Exception:
        try:
            mel.eval(f"blendShapeDeleteTargetGroup {bs_node} {next_idx};")
        except Exception:
            pass
        raise RuntimeError(
            f"Could not assign alias '{new_name}' on {bs_node}.w[{next_idx}] — "
            f"name may already be in use or contain invalid characters."
        )

    _insert_indices_after(bs_node, ref_logical_index, [next_idx])
    return next_idx


def create_delta_cluster(bs_node, logical_index, target_name,
                         neutral=False, _precomputed=None):
    """
    Creates a cluster rig driven by the delta magnitudes of the target.
    Cluster handle is placed at the bbox center of delta vertices.

    neutral=False, _precomputed=None  — single target, posed mesh:
        Regen mesh stays live; delete it to bake back into the target.
    neutral=False, _precomputed=(mags, n)  — multi-target combined, posed mesh:
        Regen mesh of the primary target stays live; weights = sum of all targets.
    neutral=True, _precomputed=None  — single target, neutral mesh:
        Creates a new empty '{target_name}_Copy' target; its regen mesh (neutral
        pose, connected to the new target) stays live for bake-back.
    neutral=True, _precomputed=(mags, n)  — multi-target combined, neutral mesh:
        Like above but magnitudes are pre-summed externally.

    Returns (grp, working_mesh, cluster_handle).
    """
    saved = _save_shape_editor_selection()
    try:
        if neutral:
            # ── Collect magnitudes (regen → read → delete) ──────────────────
            if _precomputed is None:
                magnitudes, n_verts = _collect_magnitudes(bs_node, logical_index)
            else:
                magnitudes, n_verts = _precomputed

            # ── Create the '_Copy' target and regen it (neutral live mesh) ──
            base_mesh = get_base_mesh(bs_node)
            copy_name = f"{target_name}_Copy"
            copy_idx  = _add_empty_bs_target(bs_node, base_mesh, logical_index, copy_name)

            regen = cmds.sculptTarget(bs_node, e=True, target=copy_idx, regenerate=True)
            regen = regen if isinstance(regen, str) else regen[0]
            cmds.rename(regen, f"{copy_name}_regenerated")
            working_mesh = f"{copy_name}_regenerated"
        else:
            # ── Regen primary target → posed live mesh ───────────────────────
            regen = cmds.sculptTarget(bs_node, e=True, target=logical_index, regenerate=True)
            regen = regen if isinstance(regen, str) else regen[0]
            cmds.rename(regen, f"{target_name}_regenerated")
            working_mesh = f"{target_name}_regenerated"

            if _precomputed is None:
                n_verts = cmds.polyEvaluate(working_mesh, vertex=True)
                magnitudes = {}
                for i in range(n_verts):
                    dx = cmds.getAttr(f"{working_mesh}.pnts[{i}].pntx")
                    dy = cmds.getAttr(f"{working_mesh}.pnts[{i}].pnty")
                    dz = cmds.getAttr(f"{working_mesh}.pnts[{i}].pntz")
                    mag = math.sqrt(dx*dx + dy*dy + dz*dz)
                    if mag > 1e-6:
                        magnitudes[i] = mag
            else:
                magnitudes, n_verts = _precomputed
    finally:
        _restore_shape_editor_selection(saved)

    max_mag = max(magnitudes.values()) if magnitudes else 1.0

    # ── Compute bbox center of delta vertices (for cluster handle position) ──
    wshapes = cmds.listRelatives(working_mesh, shapes=True, type="mesh") or [working_mesh]
    wshape  = wshapes[0]
    xs, ys, zs = [], [], []
    for vi in magnitudes:
        pos = cmds.pointPosition(f"{wshape}.vtx[{vi}]", world=True)
        xs.append(pos[0]); ys.append(pos[1]); zs.append(pos[2])

    if xs:
        center = (
            (min(xs) + max(xs)) * 0.5,
            (min(ys) + max(ys)) * 0.5,
            (min(zs) + max(zs)) * 0.5,
        )
    else:
        bbox = cmds.exactWorldBoundingBox(working_mesh)
        center = (
            (bbox[0] + bbox[3]) * 0.5,
            (bbox[1] + bbox[4]) * 0.5,
            (bbox[2] + bbox[5]) * 0.5,
        )

    rig_name = f"{target_name}_Copy" if neutral else target_name

    # ── Create cluster ───────────────────────────────────────────────────────
    cluster_node, cluster_handle = cmds.cluster(working_mesh, name=f"{rig_name}_cluster")
    if not neutral:
        _ensure_geom_target_connected(bs_node, logical_index, working_mesh)

    # ── Set weights — normalized magnitudes ─────────────────────────────────
    weights_list = [magnitudes.get(i, 0.0) / max_mag for i in range(n_verts)]
    cmds.setAttr(
        f"{cluster_node}.weightList[0].weights[0:{n_verts - 1}]",
        *weights_list, size=n_verts
    )

    # ── Reposition cluster handle at delta center (no deformation:
    #    move handle then update bindPreMatrix → net transform = identity) ───
    cx, cy, cz = center
    cmds.xform(cluster_handle, ws=True, t=[cx, cy, cz])
    new_inv = cmds.getAttr(f"{cluster_handle}.worldInverseMatrix[0]")
    cmds.setAttr(f"{cluster_node}.bindPreMatrix", new_inv, type="matrix")

    mode_label = "neutral (_Copy)" if neutral else "posed"
    grp = cmds.group(working_mesh, cluster_handle, name=f"{rig_name}_deltaCluster_grp")
    print(f"  ✓ Delta cluster on {mode_label} mesh : {cluster_handle} → {grp}")
    print(f"    Delete '{working_mesh}' when done to bake back into blendShape.")
    return grp, working_mesh, cluster_handle


def create_delta_joint(bs_node, logical_index, target_name,
                       neutral=False, _precomputed=None):
    """
    Creates a joint rig driven by the delta magnitudes of the target.
      - {name}_jnt      : weights = normalized delta magnitudes
      - {name}_zero_jnt : absorbs remaining weights (1 - w)
    Joint is placed at the bbox center of delta vertices.

    neutral=False, _precomputed=None  — single target, posed mesh.
    neutral=False, _precomputed=(mags, n)  — multi-target combined, posed mesh.
    neutral=True, _precomputed=None  — single target: creates '{target_name}_Copy'
        target; its neutral regen mesh stays live for bake-back.
    neutral=True, _precomputed=(mags, n)  — multi-target combined, neutral mesh.

    Returns (group, working_mesh, deform_jnt, zero_jnt).
    """
    saved = _save_shape_editor_selection()
    try:
        if neutral:
            if _precomputed is None:
                magnitudes, n_verts = _collect_magnitudes(bs_node, logical_index)
            else:
                magnitudes, n_verts = _precomputed

            base_mesh = get_base_mesh(bs_node)
            copy_name = f"{target_name}_Copy"
            copy_idx  = _add_empty_bs_target(bs_node, base_mesh, logical_index, copy_name)

            regen = cmds.sculptTarget(bs_node, e=True, target=copy_idx, regenerate=True)
            regen = regen if isinstance(regen, str) else regen[0]
            cmds.rename(regen, f"{copy_name}_regenerated")
            working_mesh = f"{copy_name}_regenerated"
        else:
            regen = cmds.sculptTarget(bs_node, e=True, target=logical_index, regenerate=True)
            regen = regen if isinstance(regen, str) else regen[0]
            cmds.rename(regen, f"{target_name}_regenerated")
            working_mesh = f"{target_name}_regenerated"

            if _precomputed is None:
                n_verts = cmds.polyEvaluate(working_mesh, vertex=True)
                magnitudes = {}
                for i in range(n_verts):
                    dx = cmds.getAttr(f"{working_mesh}.pnts[{i}].pntx")
                    dy = cmds.getAttr(f"{working_mesh}.pnts[{i}].pnty")
                    dz = cmds.getAttr(f"{working_mesh}.pnts[{i}].pntz")
                    mag = math.sqrt(dx*dx + dy*dy + dz*dz)
                    if mag > 1e-6:
                        magnitudes[i] = mag
            else:
                magnitudes, n_verts = _precomputed
    finally:
        _restore_shape_editor_selection(saved)

    max_mag = max(magnitudes.values()) if magnitudes else 1.0
    rig_name = f"{target_name}_Copy" if neutral else target_name

    # ── Compute bbox center of delta vertices ────────────────────────────────
    wshapes    = cmds.listRelatives(working_mesh, shapes=True, type="mesh") or [working_mesh]
    wshape     = wshapes[0]
    xs, ys, zs = [], [], []
    for vi in magnitudes:
        pos = cmds.pointPosition(f"{wshape}.vtx[{vi}]", world=True)
        xs.append(pos[0]); ys.append(pos[1]); zs.append(pos[2])

    if xs:
        center = (
            (min(xs) + max(xs)) * 0.5,
            (min(ys) + max(ys)) * 0.5,
            (min(zs) + max(zs)) * 0.5,
        )
    else:
        bbox = cmds.exactWorldBoundingBox(working_mesh)
        center = (
            (bbox[0] + bbox[3]) * 0.5,
            (bbox[1] + bbox[4]) * 0.5,
            (bbox[2] + bbox[5]) * 0.5,
        )
        print(f"  ⚠ '{target_name}' has no deltas — joint placed at mesh bbox center.")

    # ── Create joints ────────────────────────────────────────────────────────
    cmds.select(clear=True)
    deform_jnt = cmds.joint(name=f"{rig_name}_jnt", position=center)
    cmds.select(clear=True)
    zero_jnt   = cmds.joint(name=f"{rig_name}_zero_jnt")
    cmds.select(clear=True)

    # ── Create skinCluster ───────────────────────────────────────────────────
    skin_node = cmds.skinCluster(
        deform_jnt, zero_jnt, working_mesh,
        name=f"{rig_name}_skinCluster",
        toSelectedBones=True,
        bindMethod=0,
        skinMethod=0,
        normalizeWeights=1
    )[0]
    if not neutral:
        _ensure_geom_target_connected(bs_node, logical_index, working_mesh)

    # ── Write weights — disable normalization mid-loop ───────────────────────
    cmds.setAttr(f"{skin_node}.normalizeWeights", 0)
    for vi in range(n_verts):
        w = magnitudes.get(vi, 0.0) / max_mag if magnitudes else 0.0
        cmds.setAttr(f"{skin_node}.weightList[{vi}].weights[0]", w)
        cmds.setAttr(f"{skin_node}.weightList[{vi}].weights[1]", 1.0 - w)
    cmds.setAttr(f"{skin_node}.normalizeWeights", 1)

    # ── Group everything ─────────────────────────────────────────────────────
    mode_label = "neutral (_Copy)" if neutral else "posed"
    grp = cmds.group(working_mesh, deform_jnt, zero_jnt, name=f"{rig_name}_deltaJoint_grp")

    print(f"  ✓ Delta joint on {mode_label} mesh : {deform_jnt} / {zero_jnt} → {grp}")
    print(f"    Delete '{working_mesh}' when done to bake back into blendShape.")
    return grp, working_mesh, deform_jnt, zero_jnt


import re as _re

_SIDE_PREFIX_RE = _re.compile(r'^(L|R|C|M)_(.+)$')

def _els_name(target_name, side):
    """Return the edge-loop-split name for one side ('upper' or 'lower').
    Inserts the side label after the side prefix if present, otherwise prepends it.
      brow_up      -> upper_brow_up  / lower_brow_up
      L_brow_up    -> L_upper_brow_up / L_lower_brow_up
    """
    m = _SIDE_PREFIX_RE.match(target_name)
    if m:
        return f"{m.group(1)}_{side}_{m.group(2)}"
    return f"{side}_{target_name}"


def edge_loop_split_target(bs_node, logical_index, target_name,
                            seam_edges, seed_upper, seed_lower, falloff_radius=1,
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
      2. BFS from seed_upper blocked by seam_vis  → reachable_upper + distances_upper
      3. BFS from seed_lower blocked by seam_vis  → reachable_lower + distances_lower
      4. Assign each delta vertex to the closer seed (by BFS distance)
      5. Seam vertices → 0.5 / 0.5 blend
      6. Falloff: weight ramps from 0.5 at seam to 1.0 at falloff_radius hops

    Parameters
    ----------
    seam_edges     : set of frozenset({a, b}) — selected edges as vertex pairs
    seed_upper     : int — vertex on the UPPER side
    seed_lower     : int — vertex on the LOWER side
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
    if seed_upper in seam_vis:
        raise RuntimeError(
            f"Upper seed (vtx[{seed_upper}]) is on the seam — pick a vertex clearly on the upper side.")
    if seed_lower in seam_vis:
        raise RuntimeError(
            f"Lower seed (vtx[{seed_lower}]) is on the seam — pick a vertex clearly on the lower side.")

    # ── Read source deltas ─────────────────────────────────────────────────
    deltas    = get_target_deltas(bs_node, logical_index)
    delta_vis = set(deltas.keys())

    # ── Local active region ────────────────────────────────────────────────
    active_vis = delta_vis | seam_vis
    active_vis.add(seed_upper)
    active_vis.add(seed_lower)

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

    dist_from_upper = _bfs_dist_from_seed(seed_upper, seam_vis)
    dist_from_lower = _bfs_dist_from_seed(seed_lower, seam_vis)

    # ── Side assignment ────────────────────────────────────────────────────
    # Each vertex goes to whichever seed is topologically closer.
    # Seam vertices stay at 0.5 / 0.5.
    # Unreachable from either seed → default side upper (edge case: isolated island)
    side_upper = set()
    side_lower = set()
    for vi in active_vis - seam_vis:
        du = dist_from_upper.get(vi, None)
        dl = dist_from_lower.get(vi, None)
        if du is not None and dl is not None:
            if du <= dl:
                side_upper.add(vi)
            else:
                side_lower.add(vi)
        elif du is not None:
            side_upper.add(vi)
        elif dl is not None:
            side_lower.add(vi)
        else:
            side_upper.add(vi)  # isolated — default to upper

    d_upper = delta_vis & side_upper
    d_lower = delta_vis & side_lower
    d_s = delta_vis & seam_vis
    print(f"  Edge Loop Split — seam:{len(seam_vis)} vtx  "
          f"delta upper:{len(d_upper)}  delta lower:{len(d_lower)}  delta seam:{len(d_s)}")

    if not d_upper:
        cmds.warning("Upper side has no delta vertices — check seed_upper position.")
    if not d_lower:
        cmds.warning("Lower side has no delta vertices — check seed_lower position.")

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

    dist_seam_upper = _bfs_dist_from_seam(side_lower, falloff_radius)
    dist_seam_lower = _bfs_dist_from_seam(side_upper, falloff_radius)

    # Resolve falloff function — default to linear if not provided
    _falloff = falloff_func if falloff_func is not None else linear

    def _w(dist):
        if dist <= 0:
            return 0.5
        t = min(1.0, dist / falloff_radius)
        return 0.5 + 0.5 * _falloff(t)

    # ── Per-vertex weights (weight_upper + weight_lower == 1.0 always) ───────────
    weight_upper = {}
    weight_lower = {}
    for vi in delta_vis:
        if vi in seam_vis:
            weight_upper[vi] = 0.5;  weight_lower[vi] = 0.5
        elif vi in side_upper:
            wu = _w(dist_seam_upper.get(vi, falloff_radius))
            weight_upper[vi] = wu;   weight_lower[vi] = 1.0 - wu
        elif vi in side_lower:
            wl = _w(dist_seam_lower.get(vi, falloff_radius))
            weight_lower[vi] = wl;   weight_upper[vi] = 1.0 - wl
        else:
            weight_upper[vi] = 1.0;  weight_lower[vi] = 0.0

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

    upper_idx = _write_weighted_target(_els_name(target_name, "upper"), weight_upper)
    lower_idx = _write_weighted_target(_els_name(target_name, "lower"), weight_lower)
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
    before_wraps      = set(cmds.ls(type='wrap') or [])
    before_transforms = set(cmds.ls(type='transform') or [])

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

    # Identify the base mesh Maya created — snapshot diff is the only reliable method.
    # listConnections on basePoints[0] can return a transform instead of a shape,
    # causing listRelatives to walk up to a parent group and delete it.
    after_transforms = set(cmds.ls(type='transform') or [])
    new_transforms   = after_transforms - before_transforms
    base_transform   = list(new_transforms)[0] if new_transforms else None

    print(f"  ✓ Wrap created : {wrap_node}  (base: {base_transform})")
    return wrap_node, base_transform


def _delete_wrap_deformer(wrap_node, base_transform):
    """Deletes the wrap node and its associated base mesh."""
    if wrap_node and cmds.objExists(wrap_node):
        cmds.delete(wrap_node)
    if base_transform and cmds.objExists(base_transform):
        cmds.delete(base_transform)
    print(f"  Wrap cleaned up.")



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
    bs_state = zero_all_bs_weights(bs_node)

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
        restore_all_bs_weights(bs_node, bs_state)

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
    bs_state = zero_all_bs_weights(bs_node)

    mesh_short = mesh_target.split(":")[-1].split("|")[-1]
    extracted  = []

    try:
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

    finally:
        restore_all_bs_weights(bs_node, bs_state)


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
                      dropoff=100.0, rotation=0.15, spans=4, flat_curve=True):
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

    # ── Always draw on top for all wire curves ────────────────────────────────
    for crv in [shp + "_crv" for shp in shape_names]:
        shp_node = cmds.listRelatives(crv, shapes=True)
        if shp_node:
            cmds.setAttr(f"{shp_node[0]}.alwaysDrawOnTop", 1)

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


# ── Joints Setup ──────────────────────────────────────────────────────────────

_UPPER_BONE_PARAMS = [
    (0.0,    "L_lip_corner"),
    (0.0625, "L_lip_up_b"),
    (0.25,   "L_lip_up_a"),
    (0.5,    "M_lip_up"),
    (0.75,   "R_lip_up_a"),
    (0.935,  "R_lip_up_b"),
    (1.0,    "R_lip_corner"),
]
_LOWER_BONE_PARAMS = [
    (0.0,    "L_lip_corner"),
    (0.0625, "L_lip_dn_b"),
    (0.25,   "L_lip_dn_a"),
    (0.5,    "M_lip_dn"),
    (0.75,   "R_lip_dn_a"),
    (0.935,  "R_lip_dn_b"),
    (1.0,    "R_lip_corner"),
]

_LIP_RIG_MSH  = "lip_rig_msh"
_LIP_RIG_GRP  = "lip_rig_setup_grp"
_UPPER_CRV    = "lip_upper_crv"
_LOWER_CRV    = "lip_lower_crv"
_OUTER_UP_CRV = "lip_outer_upper_crv"
_OUTER_DN_CRV = "lip_outer_lower_crv"
_ZERO_OUT_JNT = "zero_out_jnt"


def _get_ordered_loop_verts(mesh, edge_str):
    """Return ordered vertex-index list for the edge loop containing edge_str.
    edge_str: e.g. 'meshName.e[42]'
    """
    import re as _re
    edge_id = int(_re.search(r'\.e\[(\d+)\]', edge_str).group(1))

    old_sel = cmds.ls(sl=True, fl=True) or []
    try:
        cmds.polySelect(mesh, edgeLoop=edge_id)
        loop_edges = cmds.ls(sl=True, fl=True) or []
    finally:
        if old_sel:
            cmds.select(old_sel)
        else:
            cmds.select(cl=True)

    if not loop_edges:
        raise RuntimeError(f"No edge loop found for {edge_str}")

    # Build adjacency (vertex → [connected vertices]) from edge info
    adj = {}
    for e in loop_edges:
        tokens = cmds.polyInfo(e, edgeToVertex=True)[0].split()
        v0, v1 = int(tokens[2]), int(tokens[3])
        adj.setdefault(v0, []).append(v1)
        adj.setdefault(v1, []).append(v0)

    # Walk the loop in order
    start = next(iter(adj))
    ordered = [start]
    prev, cur = None, start
    while True:
        nxt = next((n for n in adj[cur] if n != prev), None)
        if nxt is None or nxt == start:
            break
        ordered.append(nxt)
        prev, cur = cur, nxt

    return ordered


def _arc_verts_to_edges(mesh, vert_indices):
    """Return edge components for each consecutive vertex pair in the arc."""
    edges = []
    for i in range(len(vert_indices) - 1):
        v0, v1 = vert_indices[i], vert_indices[i + 1]
        e0_toks = cmds.polyInfo(f"{mesh}.vtx[{v0}]", vertexToEdge=True)[0].split()[2:]
        e1_set  = set(cmds.polyInfo(f"{mesh}.vtx[{v1}]", vertexToEdge=True)[0].split()[2:])
        shared  = [e for e in e0_toks if e in e1_set]
        if shared:
            edges.append(f"{mesh}.e[{shared[0]}]")
    return edges


def _arc_to_curve(mesh, vert_indices, name):
    """Build a NURBS curve from arc edges via polyToCurve, oriented R→L (param 0 = R corner)."""
    if cmds.objExists(name):
        cmds.delete(name)
    edges = _arc_verts_to_edges(mesh, vert_indices)
    cmds.select(edges)
    cmds.polyToCurve(f=0, dg=1, usm=0, n=name)
    cmds.delete(name, ch=True)
    cmds.select(cl=True)

    # Ensure param 0 is at R corner (most +X end)
    min_p   = cmds.getAttr(f"{name}.minValue")
    max_p   = cmds.getAttr(f"{name}.maxValue")
    p_start = cmds.pointOnCurve(name, pr=min_p, p=True)
    p_end   = cmds.pointOnCurve(name, pr=max_p, p=True)
    if p_start[0] < p_end[0]:  # start is more -X (L side) → reverse to get R→L
        cmds.reverseCurve(name, ch=False, rpo=True)

    return name


def _find_corner_local_indices(mesh, vert_indices):
    """Return (r_local, l_local): indices into vert_indices of most +X and -X verts."""
    import maya.OpenMaya as om
    sel = om.MSelectionList()
    sel.add(mesh)
    dag = om.MDagPath()
    sel.getDagPath(0, dag)
    fn_mesh = om.MFnMesh(dag)
    pts = om.MPointArray()
    fn_mesh.getPoints(pts, om.MSpace.kWorld)

    r_local = l_local = 0
    max_x, min_x = -1e9, 1e9
    for i, vi in enumerate(vert_indices):
        x = pts[vi].x
        if x > max_x:
            max_x = x; r_local = i
        if x < min_x:
            min_x = x; l_local = i
    return r_local, l_local


def _split_loop_at_corners(mesh, vert_indices, r_local, l_local):
    """Split ordered loop into (upper_verts, lower_verts), both in R→L order."""
    # Arc A: r_local → l_local going forward in the list
    if r_local <= l_local:
        arc_a = vert_indices[r_local:l_local + 1]
    else:
        arc_a = vert_indices[r_local:] + vert_indices[:l_local + 1]

    # Arc B: l_local → r_local going forward, then reversed to get R→L
    if l_local <= r_local:
        arc_b = list(reversed(vert_indices[l_local:r_local + 1]))
    else:
        arc_b = list(reversed(vert_indices[l_local:] + vert_indices[:r_local + 1]))

    def mean_y(verts):
        return sum(
            cmds.xform(f"{mesh}.vtx[{v}]", q=True, ws=True, t=True)[1]
            for v in verts
        ) / len(verts)

    return (arc_a, arc_b) if mean_y(arc_a) >= mean_y(arc_b) else (arc_b, arc_a)


def _closest_on_curve(crv_name, pt_world):
    """Return (t_normalized 0-1, world_distance) for closest point on curve."""
    import maya.OpenMaya as om
    sel = om.MSelectionList()
    sel.add(crv_name)
    dag = om.MDagPath()
    sel.getDagPath(0, dag)
    fn_crv = om.MFnNurbsCurve(dag)

    pt = om.MPoint(pt_world[0], pt_world[1], pt_world[2])
    util = om.MScriptUtil()
    util.createFromDouble(0.0)
    p_ptr = util.asDoublePtr()
    closest = fn_crv.closestPoint(pt, p_ptr, 0.001, om.MSpace.kWorld)
    param = util.getDouble(p_ptr)
    dist = float(pt.distanceTo(closest))

    min_p = cmds.getAttr(f"{crv_name}.minValue")
    max_p = cmds.getAttr(f"{crv_name}.maxValue")
    span = max_p - min_p
    t = (param - min_p) / span if span > 1e-9 else 0.0
    return t, dist


def _interp_bone_weights(bone_params, t):
    """Return [(name, weight), (name, weight)] for the two bones bracketing t."""
    t = max(0.0, min(1.0, t))
    for i in range(len(bone_params) - 1):
        t0, n0 = bone_params[i]
        t1, n1 = bone_params[i + 1]
        if t0 <= t <= t1:
            span = t1 - t0
            a = (t - t0) / span if span > 1e-9 else 0.0
            return [(n0, 1.0 - a), (n1, a)]
    if t <= bone_params[0][0]:
        return [(bone_params[0][1], 1.0), (bone_params[1][1], 0.0)]
    return [(bone_params[-2][1], 0.0), (bone_params[-1][1], 1.0)]


@undo_chunk
def build_lip_rig(base_mesh, middle_edge):
    """Build the full lip rig from a middle edge on base_mesh.
    Positions and orients joints from a motionPath (baked then deleted).
    Binds skin with all weights on zero_out — user paints weights manually.
    Returns (rig_grp, skin_joint_list).
    """
    import maya.OpenMaya as om
    import maya.OpenMayaAnim as oma

    # ── Clean up any existing rig ──────────────────────────────────────────
    if cmds.objExists(_LIP_RIG_GRP):
        cmds.delete(_LIP_RIG_GRP)
    # Cleanup orphan tmp motionPath nodes from a failed previous build
    _seen = set()
    for _, _bname in _UPPER_BONE_PARAMS + _LOWER_BONE_PARAMS:
        if _bname not in _seen:
            for _suffix in ("_mp", "_mp_tmp"):
                _mp_node = f"{_bname}{_suffix}"
                if cmds.objExists(_mp_node):
                    cmds.delete(_mp_node)
            _seen.add(_bname)

    # ── Root group ─────────────────────────────────────────────────────────
    rig_grp = cmds.group(em=True, name=_LIP_RIG_GRP)

    # ── Duplicate head mesh ────────────────────────────────────────────────
    if cmds.objExists(_LIP_RIG_MSH):
        cmds.delete(_LIP_RIG_MSH)
    cmds.duplicate(base_mesh, name=_LIP_RIG_MSH)
    orig = _LIP_RIG_MSH + "ShapeOrig"
    if cmds.objExists(orig):
        cmds.delete(orig)
    cmds.parent(_LIP_RIG_MSH, rig_grp)

    # ── Middle loop → upper / lower arc curves ─────────────────────────────
    mid_verts = _get_ordered_loop_verts(base_mesh, middle_edge)
    r_m, l_m  = _find_corner_local_indices(base_mesh, mid_verts)
    upper_mid, lower_mid = _split_loop_at_corners(base_mesh, mid_verts, r_m, l_m)
    upper_crv = _arc_to_curve(base_mesh, upper_mid, _UPPER_CRV)
    lower_crv = _arc_to_curve(base_mesh, lower_mid, _LOWER_CRV)

    # ── zero_out joint at mesh bounding-box centre ─────────────────────────
    bb = cmds.exactWorldBoundingBox(base_mesh)
    center = [(bb[0] + bb[3]) / 2.0, (bb[1] + bb[4]) / 2.0, (bb[2] + bb[5]) / 2.0]
    cmds.select(cl=True)
    zero_jnt = cmds.joint(name=_ZERO_OUT_JNT)
    cmds.xform(zero_jnt, ws=True, t=center)
    cmds.setAttr(f"{zero_jnt}.jointOrient", 0, 0, 0)
    cmds.parent(zero_jnt, rig_grp)

    skin_joints = [zero_jnt]
    created = {}  # bone name → skin_jnt  (corners are shared upper/lower)

    # ── Controller + skin joints — orientation baked from motionPath ──────
    for arc_params, crv in [(_UPPER_BONE_PARAMS, upper_crv),
                              (_LOWER_BONE_PARAMS, lower_crv)]:
        crv_shape = cmds.listRelatives(crv, shapes=True)[0]

        for t, name in arc_params:
            if name in created:
                continue

            # Parent group: connect motionPath live, read result, then disconnect
            grp = cmds.group(em=True, name=f"{name}_grp")
            cmds.parent(grp, rig_grp)

            mp = cmds.createNode("motionPath", name=f"{name}_mp_tmp")
            cmds.connectAttr(f"{crv_shape}.worldSpace[0]", f"{mp}.geometryPath")
            cmds.setAttr(f"{mp}.uValue", t)
            cmds.setAttr(f"{mp}.fractionMode", True)
            cmds.setAttr(f"{mp}.follow", True)
            cmds.setAttr(f"{mp}.frontAxis", 0)    # X along curve tangent
            cmds.setAttr(f"{mp}.upAxis", 1)        # Y up
            cmds.setAttr(f"{mp}.worldUpType", 3)   # vector
            cmds.setAttr(f"{mp}.worldUpVectorY", 1.0)
            cmds.connectAttr(f"{mp}.xCoordinate", f"{grp}.translateX")
            cmds.connectAttr(f"{mp}.yCoordinate", f"{grp}.translateY")
            cmds.connectAttr(f"{mp}.zCoordinate", f"{grp}.translateZ")
            cmds.connectAttr(f"{mp}.rotate",      f"{grp}.rotate")
            # Force evaluation and read back from grp
            cmds.dgeval(grp)
            tx, ty, tz = cmds.getAttr(f"{grp}.translate")[0]
            rx, ry, rz = cmds.getAttr(f"{grp}.rotate")[0]
            # Disconnect and delete motionPath — bake static values
            cmds.disconnectAttr(f"{mp}.xCoordinate", f"{grp}.translateX")
            cmds.disconnectAttr(f"{mp}.yCoordinate", f"{grp}.translateY")
            cmds.disconnectAttr(f"{mp}.zCoordinate", f"{grp}.translateZ")
            cmds.disconnectAttr(f"{mp}.rotate",      f"{grp}.rotate")
            cmds.delete(mp)
            cmds.setAttr(f"{grp}.translate", tx, ty, tz)
            # Corners and middles: world orientation (no rotation)
            # a/b: keep Y and Z from motionPath, zero X
            if "corner" in name or name.startswith("M_"):
                cmds.setAttr(f"{grp}.rotate", 0, 0, 0)
            else:
                cmds.setAttr(f"{grp}.rotate", 0, ry, rz)

            # Controller joint (drawStyle 3 = Joint marker, no bone line)
            cmds.select(cl=True)
            ctrl = cmds.joint(name=f"{name}_ctl")
            cmds.parent(ctrl, grp)
            cmds.setAttr(f"{ctrl}.t", 0, 0, 0)
            cmds.setAttr(f"{ctrl}.r", 0, 0, 0)
            cmds.setAttr(f"{ctrl}.jointOrient", 0, 0, 0)
            cmds.setAttr(f"{ctrl}.drawStyle", 3)

            # Skin joint directly under controller (drawStyle driven by condition)
            cmds.select(cl=True)
            skin = cmds.joint(name=f"{name}_skin_jnt")
            cmds.parent(skin, ctrl)
            cmds.setAttr(f"{skin}.t", 0, 0, 0)
            cmds.setAttr(f"{skin}.r", 0, 0, 0)
            cmds.setAttr(f"{skin}.jointOrient", 0, 0, 0)
            cmds.setAttr(f"{skin}.drawStyle", 2)

            skin_joints.append(skin)
            created[name] = skin

    # ── Debug group (curves hidden by default) ────────────────────────────
    debug_grp = cmds.group(em=True, name="lip_rig_debug_grp")
    cmds.parent(debug_grp, rig_grp)
    cmds.parent(upper_crv, lower_crv, debug_grp)
    cmds.setAttr(f"{debug_grp}.visibility", 0)

    # ── show_skn_joints — boolean attr driving all skin joint drawStyles ───
    cmds.addAttr(rig_grp, ln="show_skn_joints", at="bool", dv=0, keyable=True)
    cond = cmds.createNode("condition", name="lip_rig_show_skn_jnts")
    cmds.connectAttr(f"{rig_grp}.show_skn_joints", f"{cond}.firstTerm")
    cmds.setAttr(f"{cond}.secondTerm", 1)
    cmds.setAttr(f"{cond}.operation", 0)          # Equal
    cmds.setAttr(f"{cond}.colorIfTrueR",  0)      # drawStyle 0 = Bone (visible)
    cmds.setAttr(f"{cond}.colorIfFalseR", 2)      # drawStyle 2 = None (hidden)
    for _skin in skin_joints[1:]:                  # skip zero_out_jnt
        cmds.connectAttr(f"{cond}.outColorR", f"{_skin}.drawStyle")

    # ── Bind skin — all weights on zero_out ───────────────────────────────
    sc = cmds.skinCluster(
        skin_joints, _LIP_RIG_MSH,
        name="lip_rig_sc",
        toSelectedBones=True,
        bindMethod=0,
        normalizeWeights=1,
        weightDistribution=0,
        removeUnusedInfluence=False,
    )[0]

    n_verts = cmds.polyEvaluate(_LIP_RIG_MSH, v=True)

    mesh_sel = om.MSelectionList()
    mesh_sel.add(_LIP_RIG_MSH)
    mesh_dag = om.MDagPath()
    mesh_sel.getDagPath(0, mesh_dag)

    sc_sel = om.MSelectionList()
    sc_sel.add(sc)
    sc_mobj = om.MObject()
    sc_sel.getDependNode(0, sc_mobj)
    fn_sc = oma.MFnSkinCluster(sc_mobj)
    inf_paths = om.MDagPathArray()
    fn_sc.influenceObjects(inf_paths)
    n_infs = inf_paths.length()
    inf_idx = {inf_paths[i].partialPathName(): i for i in range(n_infs)}

    fn_comp = om.MFnSingleIndexedComponent()
    comp = fn_comp.create(om.MFn.kMeshVertComponent)
    fn_comp.setCompleteData(n_verts)
    inf_array = om.MIntArray(n_infs)
    for i in range(n_infs):
        inf_array.set(i, i)
    weights = om.MDoubleArray(n_verts * n_infs, 0.0)
    zi = inf_idx.get(_ZERO_OUT_JNT)
    if zi is not None:
        for vi in range(n_verts):
            weights.set(1.0, vi * n_infs + zi)
    fn_sc.setWeights(mesh_dag, comp, inf_array, weights, False)

    return rig_grp, skin_joints


# ── Rig Connector ─────────────────────────────────────────────────────────────

# Maps transform attr → transformLimits kwarg + enable attribute name
# tuple: (tl_kwarg, positive_enable_attr, negative_enable_attr)
_RC_LIMIT_INFO = {
    "tx": ("tx", "maxTransXLimitEnable", "minTransXLimitEnable"),
    "ty": ("ty", "maxTransYLimitEnable", "minTransYLimitEnable"),
    "tz": ("tz", "maxTransZLimitEnable", "minTransZLimitEnable"),
    "rx": ("rx", "maxRotXLimitEnable",   "minRotXLimitEnable"),
    "ry": ("ry", "maxRotYLimitEnable",   "minRotYLimitEnable"),
    "rz": ("rz", "maxRotZLimitEnable",   "minRotZLimitEnable"),
}
# Scale attrs handled separately: lock/hide if unused, no transformLimits
_SCALE_ATTRS = {"sx", "sy", "sz"}


def _soft_blend_keys(in_max, partner_in_max):
    """
    Returns 5 (driver_value, weight_value, tangent_type) tuples for a soft blend
    animCurveUU.  The slight undershoot near 0 is a natural consequence of the
    smooth/auto tangents — no explicit negative key needed.

    in_max         : this shape's activation maximum (e.g.  2.0 for mouth_lft)
    partner_in_max : opposite shape's in_max           (e.g. -2.0 for mouth_rgt)
    """
    if in_max >= 0:
        return [
            (partner_in_max,        0.0, "linear"),  # opposite full extent, shape=0
            (partner_in_max / 2.0,  0.0, "linear"),  # opposite half extent, dead zone
            (0.0,                   0.0, "smooth"),   # neutral — smooth tangent → soft dip
            (in_max / 2.0,          0.5, "auto"),     # midpoint
            (in_max,                1.0, "linear"),   # full activation
        ]
    else:
        return [
            (in_max,                1.0, "linear"),   # full activation (negative)
            (in_max / 2.0,          0.5, "smooth"),   # midpoint — smooth for soft ease-out
            (0.0,                   0.0, "smooth"),   # neutral
            (partner_in_max / 2.0,  0.0, "linear"),  # positive half extent, dead zone
            (partner_in_max,        0.0, "linear"),  # positive full extent, shape=0
        ]


@undo_chunk
def build_and_connect_rig(bs_node, rows, soft_blend_pairs=None, soft_blend_curve=None):
    """
    Per shape, builds the following network:
      offset_{shape}_{i}  (addDoubleLinear, if in_min≠0) : subtracts in_min
      norm_{shape}_{i}    (multiplyDivide)                : normalizes → [0..1..∞]
      sum_{shape}         (plusMinusAverage, if >1 driver): additive sum
      cond_{shape}        (condition)                     : hasLimits → maxR=1 or 1e6
      clamp_{shape}       (clamp)                         : minR=0 always, maxR driven
      gate_{shape}        (multDoubleLinear, if gate≠"")  : multiplies by the gate target weight

    Multiple rows with the same shape = proxy / additive drivers.
    hasLimits=ON  : weight [0,1]  + controller physically clamped (transform attrs)
    hasLimits=OFF : weight proportional beyond 1.0, never negative

    rows : list of dicts with keys:
        shape, controller, attr, in_min, in_max
        (in_max can be negative: sign encodes direction)
    """
    import re as _re
    from collections import defaultdict

    # Build alias → logical index map
    aliases_flat = cmds.aliasAttr(bs_node, query=True) or []
    target_map = {}
    for i in range(0, len(aliases_flat) - 1, 2):
        alias    = aliases_flat[i]
        attr_str = aliases_flat[i + 1]
        m = _re.search(r"\[(\d+)\]", attr_str)
        if m:
            target_map[alias] = int(m.group(1))

    results        = []
    pending_limits = {}  # ctrl → {attr_name: (lim_min, lim_max)}
    pending_conds  = {}  # ctrl → [cond_name, ...]

    # ── Phase 0: wipe custom attrs on involved controllers ────────────────────
    # Delete every user-defined attr (except hasLimits) so the build starts from
    # a clean state: no stale locks, no leftover attrs from old mappings.
    _native_attrs = set(_RC_LIMIT_INFO) | _SCALE_ATTRS
    _ctrls_in_build = {r.get("controller", "").strip() for r in rows
                       if r.get("controller", "").strip()}
    for _ctrl in _ctrls_in_build:
        if not cmds.objExists(_ctrl):
            continue
        for _attr in (cmds.listAttr(_ctrl, userDefined=True) or []):
            if _attr == "hasLimits" or _attr in _native_attrs:
                continue
            _full = f"{_ctrl}.{_attr}"
            if cmds.objExists(_full):
                try:
                    cmds.deleteAttr(_full)
                except Exception:
                    pass

    # ── Phase 1: validation + custom attr creation ───────────────────────────
    valid_rows = []
    for row in rows:
        shape         = row.get("shape", "").strip()
        ctrl          = row.get("controller", "").strip()
        resolved_attr = row.get("attr", "ty").strip()
        in_min        = float(row.get("in_min", 0.0))
        in_max        = float(row.get("in_max", 1.0))
        gate          = row.get("gate", "").strip()

        if not shape or not ctrl or not resolved_attr:
            results.append({"shape": shape, "status": "skip"})
            continue

        idx = target_map.get(shape)
        if idx is None:
            results.append({"shape": shape, "status": "no_target"})
            continue

        if not cmds.objExists(ctrl):
            results.append({"shape": shape, "status": "no_ctrl"})
            continue

        ctrl_attr_full = f"{ctrl}.{resolved_attr}"
        if not cmds.objExists(ctrl_attr_full):
            # Attr doesn't exist → create as custom float attr (min/max applied later)
            try:
                cmds.addAttr(ctrl, longName=resolved_attr, attributeType="float",
                             defaultValue=0.0, keyable=True)
            except Exception:
                results.append({"shape": shape, "status": "no_attr"})
                continue
        valid_rows.append({
            "shape": shape, "idx": idx, "ctrl": ctrl,
            "ctrl_attr": ctrl_attr_full, "resolved_attr": resolved_attr,
            "in_min": in_min, "in_max": in_max,
            "gate": gate,
        })

    # ── Apply physical slider limits to custom attrs ───────────────────────────
    # Aggregate in_min/in_max across all shapes sharing the same custom attr so
    # the slider stops at the activation range — matching transformLimits behaviour
    # for native attrs when hasLimits=ON.
    _custom_ranges = {}  # (ctrl, attr) → [lo, hi]
    for vr in valid_rows:
        ra = vr["resolved_attr"]
        if ra in _RC_LIMIT_INFO or ra in _SCALE_ATTRS:
            continue
        key = (vr["ctrl"], ra)
        lo = min(0.0, vr["in_min"], vr["in_max"])
        hi = max(0.0, vr["in_min"], vr["in_max"])
        if key not in _custom_ranges:
            _custom_ranges[key] = [lo, hi]
        else:
            _custom_ranges[key][0] = min(_custom_ranges[key][0], lo)
            _custom_ranges[key][1] = max(_custom_ranges[key][1], hi)

    for (ctrl_k, ra_k), (lo_k, hi_k) in _custom_ranges.items():
        full_k = f"{ctrl_k}.{ra_k}"
        if cmds.objExists(full_k):
            try:
                cmds.addAttr(full_k, edit=True,
                             hasMinValue=True, minValue=lo_k,
                             hasMaxValue=True, maxValue=hi_k)
            except Exception:
                pass

    # ── Soft blend lookup ─────────────────────────────────────────────────────
    _soft_pair_map = {}   # shape → partner_shape
    if soft_blend_pairs:
        for _a, _b in soft_blend_pairs:
            _soft_pair_map[_a] = _b
            _soft_pair_map[_b] = _a
    _shape_in_max = {vr["shape"]: vr["in_max"] for vr in valid_rows}

    # ── Phase 2: group by shape ───────────────────────────────────────────────
    shape_groups = defaultdict(list)
    for vr in valid_rows:
        shape_groups[vr["shape"]].append(vr)

    # ── Phase 3: build network per shape ─────────────────────────────────────
    for shape, group in shape_groups.items():
        bs_weight_attr = f"{bs_node}.w[{group[0]['idx']}]"
        try:
            # Remove old nodes (deterministic names, including sdk_ for soft blend)
            old = (cmds.ls(f"offset_{shape}_*", f"norm_{shape}_*",
                           f"gate_{shape}_*", f"rev_{shape}_*") or [])
            old += [n for n in (f"sum_{shape}", f"clamp_{shape}", f"cond_{shape}",
                                f"rmv_{shape}", f"gate_{shape}", f"sdk_{shape}")
                    if cmds.objExists(n)]
            if old:
                cmds.delete(old)
            # No explicit disconnectAttr: force=True on the final connection is sufficient

            # ── Soft blend path (animCurveUU, direct to bs weight) ────────────
            if shape in _soft_pair_map:
                partner_in_max = _shape_in_max.get(_soft_pair_map[shape])
                if partner_in_max is not None:
                    vr0           = group[0]
                    ctrl          = vr0["ctrl"]
                    ctrl_attr     = vr0["ctrl_attr"]
                    resolved_attr = vr0["resolved_attr"]
                    in_max        = vr0["in_max"]

                    curve = cmds.createNode("animCurveUU", name=f"sdk_{shape}")
                    cmds.setAttr(f"{curve}.preInfinity",  1)  # linear extrapolation
                    cmds.setAttr(f"{curve}.postInfinity", 1)

                    # Use custom normalized curve if provided; else fall back to defaults.
                    # Mapping: u_actual = u_norm * in_max  (works for ± shapes)
                    if soft_blend_curve:
                        keys = [(k["u"] * in_max, k["v"], k.get("tangent", "auto"))
                                for k in soft_blend_curve]
                    else:
                        keys = _soft_blend_keys(in_max, partner_in_max)
                    for t, v, _ in keys:
                        cmds.setKeyframe(curve, float=t, value=v)
                    for i, (_, _, tang) in enumerate(keys):
                        cmds.keyTangent(curve, index=(i, i),
                                        inTangentType=tang, outTangentType=tang)

                    cmds.connectAttr(ctrl_attr,         f"{curve}.input",  force=True)
                    cmds.connectAttr(f"{curve}.output", bs_weight_attr,    force=True)

                    # transformLimits: cover the full symmetric range
                    if resolved_attr in _RC_LIMIT_INFO:
                        lim_min = min(0.0, in_max, partner_in_max)
                        lim_max = max(0.0, in_max, partner_in_max)
                        prev = pending_limits.setdefault(ctrl, {}).get(
                            resolved_attr, (0.0, 0.0))
                        pending_limits[ctrl][resolved_attr] = (
                            min(prev[0], lim_min), max(prev[1], lim_max))
                    # Register ctrl so hasLimits attr is still created
                    pending_conds.setdefault(ctrl, [])

                    for _ in group:
                        results.append({"shape": shape, "status": "ok"})
                    continue  # skip standard norm/clamp/cond path

            # ── Norm nodes (one per driver) ───────────────────────────────────
            norm_outputs = []
            for i, vr in enumerate(group):
                in_min = vr["in_min"]
                in_max = vr["in_max"]

                # in_max=0 and in_min=0 → driver disabled, skip
                if abs(in_max) < 1e-9 and abs(in_min) < 1e-9:
                    continue

                # span can be negative (negative in_max = negative direction)
                span = in_max - in_min
                if abs(span) < 1e-9:
                    continue

                # Offset: subtracts in_min from the signal (always -in_min)
                if abs(in_min) > 1e-9:
                    adl = cmds.createNode("addDL", name=f"offset_{shape}_{i}")
                    cmds.connectAttr(vr["ctrl_attr"], f"{adl}.input1", force=True)
                    cmds.setAttr(f"{adl}.input2", -in_min)
                    norm_src = f"{adl}.output"
                else:
                    norm_src = vr["ctrl_attr"]

                # Factor = 1/span: negative if in_max < in_min (negative direction)
                norm = cmds.createNode("multiplyDivide", name=f"norm_{shape}_{i}")
                cmds.setAttr(f"{norm}.operation", 1)
                cmds.setAttr(f"{norm}.input2X", 1.0 / span)
                cmds.connectAttr(norm_src, f"{norm}.input1X", force=True)
                norm_outputs.append(f"{norm}.outputX")

            # ── Additive sum if multiple drivers ──────────────────────────────
            # All drivers disabled → skip network creation
            if not norm_outputs:
                for _ in group:
                    results.append({"shape": shape, "status": "skip"})
                continue

            if len(norm_outputs) == 1:
                sum_out = norm_outputs[0]
            else:
                pma = cmds.createNode("plusMinusAverage", name=f"sum_{shape}")
                cmds.setAttr(f"{pma}.operation", 1)  # sum
                for i, out in enumerate(norm_outputs):
                    cmds.connectAttr(out, f"{pma}.input1D[{i}]", force=True)
                sum_out = f"{pma}.output1D"

            # ── Condition: drives clamp.maxR ──────────────────────────────────
            cond = cmds.createNode("condition", name=f"cond_{shape}")
            cmds.setAttr(f"{cond}.operation",     0)    # equal
            cmds.setAttr(f"{cond}.secondTerm",    1)
            cmds.setAttr(f"{cond}.colorIfTrueR",  1.0)  # hasLimits=ON  → max=1
            cmds.setAttr(f"{cond}.colorIfFalseR", 1e6)  # hasLimits=OFF → libre

            # ── Clamp: minR=0 always, maxR driven ────────────────────────────
            clp = cmds.createNode("clamp", name=f"clamp_{shape}")
            cmds.setAttr(f"{clp}.minR", 0.0)
            cmds.connectAttr(f"{cond}.outColorR", f"{clp}.maxR",   force=True)
            cmds.connectAttr(sum_out,             f"{clp}.inputR", force=True)
            cmds.connectAttr(f"{clp}.outputR", bs_weight_attr, force=True)

            # ── Combo drivers: chain of multDoubleLinear nodes (comma-separated) ─
            gate_field = group[0].get("gate", "").strip()
            gate_names = [g.strip() for g in gate_field.split(",") if g.strip()] if gate_field else []
            current_src = f"{clp}.outputR"
            for gi, gname in enumerate(gate_names):
                # "rev:" prefix → inversion via reverse node
                use_reverse = gname.startswith("rev:")
                if use_reverse:
                    gname = gname[4:].strip()

                # "node.attr" → direct Maya plug; otherwise → bs_node target lookup
                if "." in gname:
                    if not cmds.objExists(gname):
                        continue
                    raw_plug = gname
                else:
                    gate_idx = target_map.get(gname)
                    if gate_idx is None:
                        continue
                    raw_plug = f"{bs_node}.w[{gate_idx}]"

                if use_reverse:
                    rev_node = cmds.createNode("reverse", name=f"rev_{shape}_{gi}")
                    cmds.connectAttr(raw_plug, f"{rev_node}.inputX", force=True)
                    gate_plug = f"{rev_node}.outputX"
                else:
                    gate_plug = raw_plug

                gate_node = cmds.createNode("multDL", name=f"gate_{shape}_{gi}")
                cmds.disconnectAttr(current_src, bs_weight_attr)
                cmds.connectAttr(current_src,  f"{gate_node}.input1", force=True)
                cmds.connectAttr(gate_plug,    f"{gate_node}.input2", force=True)
                cmds.connectAttr(f"{gate_node}.output", bs_weight_attr, force=True)
                current_src = f"{gate_node}.output"

            # ── Collect post-loop ─────────────────────────────────────────────
            # Use the real node name (Maya may auto-rename on collision)
            primary_ctrl = group[0]["ctrl"]
            pending_conds.setdefault(primary_ctrl, []).append(cond)

            for vr in group:
                ctrl          = vr["ctrl"]
                resolved_attr = vr["resolved_attr"]
                in_max        = vr["in_max"]
                in_min        = vr["in_min"]
                # Skip disabled drivers (in_max=0 and in_min=0) for pending_limits
                if abs(in_max) < 1e-9 and abs(in_min) < 1e-9:
                    pending_conds.setdefault(ctrl, [])  # still register hasLimits
                    continue
                if resolved_attr in _RC_LIMIT_INFO:
                    # Physical limit covering the full activation range (includes 0)
                    lim_min = min(0.0, in_min, in_max)
                    lim_max = max(0.0, in_min, in_max)
                    prev = pending_limits.setdefault(ctrl, {}).get(resolved_attr, (0.0, 0.0))
                    pending_limits[ctrl][resolved_attr] = (min(prev[0], lim_min),
                                                           max(prev[1], lim_max))
                # Each ctrl has its own hasLimits → register it
                if ctrl != primary_ctrl:
                    pending_conds.setdefault(ctrl, [])  # force creation without cond

            for _ in group:
                results.append({"shape": shape, "status": "ok"})

        except Exception as e:
            for _ in group:
                results.append({"shape": shape, "status": f"error:{e}"})

    # ── Post-loop: hasLimits attr (added last) + connections ─────────────────
    for ctrl in set(pending_limits) | set(pending_conds):
        used_native = set(pending_limits.get(ctrl, {}).keys())

        # Unlock/unhide native attrs in use (a previous build may have locked them)
        for attr_name in used_native:
            attr_full = f"{ctrl}.{attr_name}"
            if cmds.getAttr(attr_full, lock=True):
                cmds.setAttr(attr_full, lock=False)
            cmds.setAttr(attr_full, keyable=True)

        # Native attrs in use → full range [lim_min, lim_max]
        for attr_name, (lim_min, lim_max) in pending_limits.get(ctrl, {}).items():
            tl_kwarg = _RC_LIMIT_INFO[attr_name][0]
            cmds.transformLimits(ctrl, **{tl_kwarg: (lim_min, lim_max)})

        # Unused tx/ty/tz/rx/ry/rz attrs → limit (0,0) + lock and hide
        for attr_name in set(_RC_LIMIT_INFO) - used_native:
            tl_kwarg = _RC_LIMIT_INFO[attr_name][0]
            cmds.transformLimits(ctrl, **{tl_kwarg: (0.0, 0.0)})
            attr_full = f"{ctrl}.{attr_name}"
            cmds.setAttr(attr_full, keyable=False)
            cmds.setAttr(attr_full, channelBox=False)
            cmds.setAttr(attr_full, lock=True)

        # Unused scale attrs (sx/sy/sz) → lock and hide (no transformLimits)
        for attr_name in _SCALE_ATTRS - used_native:
            attr_full = f"{ctrl}.{attr_name}"
            cmds.setAttr(attr_full, keyable=False)
            cmds.setAttr(attr_full, channelBox=False)
            cmds.setAttr(attr_full, lock=True)

        # hasLimits attr — must always be the last attribute on the ctrl.
        # Technique: delete it then immediately undo the delete.
        # Maya's undo restores the attribute at the END of the list while keeping
        # all existing connections intact (including any custom user connections).
        # On first build the attribute simply does not exist yet, so we add it fresh.
        if cmds.attributeQuery("hasLimits", node=ctrl, exists=True):
            cmds.deleteAttr(f"{ctrl}.hasLimits")
            cmds.undo()  # restores attribute last in the list with connections intact
        else:
            cmds.addAttr(ctrl, longName="hasLimits", attributeType="bool",
                         defaultValue=True, keyable=True)
        cmds.setAttr(f"{ctrl}.hasLimits", True)

        # Connect hasLimits → ALL native enable attrs (6 axes × min + max)
        for _, pos_en, neg_en in _RC_LIMIT_INFO.values():
            for en_attr in (pos_en, neg_en):
                if not cmds.isConnected(f"{ctrl}.hasLimits", f"{ctrl}.{en_attr}"):
                    cmds.connectAttr(f"{ctrl}.hasLimits", f"{ctrl}.{en_attr}", force=True)

        for cond_name in pending_conds.get(ctrl, []):
            if cmds.objExists(cond_name):
                if not cmds.isConnected(f"{ctrl}.hasLimits", f"{cond_name}.firstTerm"):
                    cmds.connectAttr(f"{ctrl}.hasLimits", f"{cond_name}.firstTerm", force=True)

    return results


@undo_chunk
def disconnect_rig_shapes(bs_node, shape_names):
    """
    Disconnects the rig network for the specified shapes.
    Deletes utility nodes (offset_, norm_, sum_, cond_, clamp_)
    and disconnects bs_node.w[idx].
    Returns the number of shapes disconnected.
    """
    import re as _re

    aliases_flat = cmds.aliasAttr(bs_node, query=True) or []
    target_map   = {}
    for i in range(0, len(aliases_flat) - 1, 2):
        m = _re.search(r"\[(\d+)\]", aliases_flat[i + 1])
        if m:
            target_map[aliases_flat[i]] = int(m.group(1))

    disconnected = 0
    for shape in shape_names:
        # Supprimer les utility nodes
        old = (cmds.ls(f"offset_{shape}_*", f"norm_{shape}_*",
                       f"gate_{shape}_*", f"rev_{shape}_*") or [])
        old += [n for n in (f"sum_{shape}", f"clamp_{shape}", f"cond_{shape}",
                            f"gate_{shape}", f"sdk_{shape}")
                if cmds.objExists(n)]
        if old:
            cmds.delete(old)

        # Déconnecter bs_node.w[idx]
        idx = target_map.get(shape)
        if idx is not None:
            bs_weight_attr = f"{bs_node}.w[{idx}]"
            src_list = cmds.listConnections(
                bs_weight_attr, source=True, destination=False, plugs=True) or []
            for src in src_list:
                cmds.disconnectAttr(src, bs_weight_attr)
            disconnected += 1

    return disconnected
