import bpy
from mathutils import Matrix, Vector

# Size and place the face board (the DNA add-on's *_face_gui armature) beside the MetaHuman's head.
#
# The DNA add-on builds the board in its own units; when its head is scaled onto the body
# (build_metahuman_rig.py), the board isn't, and it can come out thousands of metres wide - far past the
# viewport's clipping distance, so it seems to vanish. This scales the board object (its controls keep their
# own values, so the face animation and RigLogic are unchanged) to FACE_BOARD_WIDTH of the body's
# pelvis-to-head distance and puts its middle beside the head.
#
# build_metahuman_rig.py runs it. Run it on its own in a character, take or Working file that has an
# oversized or misplaced board; a board that is already about the right size is left alone.

RIG_NAME = "MH_rig"
FACE_BOARD = ""              # the board armature; "" = any armature called *_face_gui / *_face_board
FACE_BOARD_WIDTH = 0.4       # the board's width, as a share of the pelvis-to-head distance (about 30 cm on 1.7 m)
FACE_BOARD_SIDE = 1.1        # its middle goes this many board widths to the side of the head (+ = character's left)
TOLERANCE = 0.25             # a board within 25 % of the wanted width is left as it is


def boards():
    if FACE_BOARD:
        obj = bpy.data.objects.get(FACE_BOARD)
        return [obj] if obj is not None and obj.type == "ARMATURE" else []
    return [o for o in bpy.data.objects if o.type == "ARMATURE"
            and (o.name.endswith(("_face_gui", "_face_board")) or o.data.name == "face_gui")]


def world_box(obj):
    corners = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
    low = Vector(tuple(min(c[i] for c in corners) for i in range(3)))
    high = Vector(tuple(max(c[i] for c in corners) for i in range(3)))
    return low, high


def fit_face_board(rig=None):
    """Returns the boards it changed."""
    rig = rig or bpy.data.objects.get(RIG_NAME)
    if rig is None or rig.type != "ARMATURE" or "head" not in rig.data.bones:
        print("fit_face_board: no MetaHuman rig with a 'head' bone; nothing done")
        return []
    bpy.context.view_layer.update()
    head = rig.matrix_world @ rig.data.bones["head"].head_local
    pelvis = rig.matrix_world @ rig.data.bones["pelvis"].head_local if "pelvis" in rig.data.bones else head - Vector((0, 0, 0.75))
    want = FACE_BOARD_WIDTH * (head - pelvis).length
    changed = []
    for board in boards():
        low, high = world_box(board)
        size = high - low
        width = max(size.x, size.z)
        if width < 1e-9:
            continue
        center = (low + high) * 0.5
        target = head + Vector((FACE_BOARD_SIDE * want, 0.0, 0.0))
        ratio = want / width
        if abs(ratio - 1.0) < TOLERANCE and (center - target).length < 2.0 * want:
            print(f"fit_face_board: '{board.name}' is already {width:.3g} wide beside the head; left as it is")
            continue
        move = Matrix.Translation(target) @ Matrix.Scale(ratio, 4) @ Matrix.Translation(-center)
        world = board.matrix_world.copy()          # through the basis, so a parent or a follow-head constraint is kept
        board.matrix_basis = board.matrix_basis @ world.inverted() @ (move @ world)
        bpy.context.view_layer.update()
        print(f"fit_face_board: '{board.name}' was {width:.4g} wide at {tuple(round(c, 2) for c in center)}; "
              f"scaled x{ratio:.4g} to {want:.3g} and put beside the head")
        changed.append(board)
    if not changed and not boards():
        print("fit_face_board: no face board (*_face_gui) in this file")
    return changed


if __name__ != "build_metahuman_rig_face_board":
    fit_face_board()
