import bpy
import importlib.util
import json
import os
import time

# Make a library of face poses for the MetaHuman's face board: the facial grammar and mouth morphemes of ASL, and the
# basic emotions, each a pose asset that sets every face board control (so applying one also clears the others).
# They are saved in Storage/Face_Poses (one .asset.blend each, with a thumbnail of the face drawn by the 3D Viewport),
# and Face_Poses is added to the Asset Browser. pack_pose_library.py packs it like the body libraries.
#
# Run it in MetaHuman_character.blend (or a take file) from the Text Editor, with a 3D Viewport open: the face board is
# the DNA add-on's (an armature called *_face_gui), and RigLogic moves the face from it. To apply a face pose: select
# the face board, Pose Mode, and apply the pose from the Asset Browser (library Face_Poses).
#
# The poses are recipes (POSES below): face board controls and how far each is moved, 0 to 1 of its range ("*" = both
# sides, L and R; a 2D control such as C_jaw takes (sideways, up/down)). A control this face board hasn't is listed
# and left out. Edit, add or remove recipes freely and run again: every pose is made again (OVERWRITE).

# --- settings (None = from pipeline_config.json in the workspace) ---
OUTPUT_FOLDER = None        # None = <Storage>/Face_Poses
LIBRARY_NAME = "Face_Poses"
PREFIX = "Face"             # pose names: "<PREFIX> <name>", e.g. "Face Yes-No Question"
OVERWRITE = True            # True = make every pose again (they are made from the recipes below, so nothing is lost);
                            #   False = leave the poses already in the library as they are
STRENGTH = 1.0              # every amount below is multiplied by this
THUMBNAILS = True           # a picture of the face for each pose (drawn by the 3D Viewport; see convert_poses_to_metahuman.py's
                            #   THUMBNAIL_SHADING / THUMBNAIL_SIZE, which are used for it)
FACE_VIEW = "FRONT"         # the thumbnail camera: "FRONT", or the face's "LEFT" / "RIGHT" side
AUTHOR = "Studio Galt Mocap"
CHECK_FACE = True           # first move the face board and look whether the face follows (RigLogic, from the Character
                            #   DNA add-on). If it doesn't, the thumbnails would all show the same face: stop and say why
DNA_ADDON_MODULES = ("character_dna_pro", "character_dna")

# name -> {control (after "CTRL_"): amount}. Alternatives for controls that differ between face boards: "a|b".
POSES = {
    # facial grammar
    "Neutral": {},
    "Yes-No Question": {"*_brow_raiseIn": 0.8, "*_brow_raiseOut": 0.8, "*_eye_blink": -0.25},
    "WH Question": {"*_brow_down": 0.8, "*_brow_lateral": 0.6, "*_eye_squintInner": 0.4},
    "Negation": {"*_brow_down": 0.4, "*_mouth_cornerDepress": 0.5, "*_mouth_lowerLipDepress": 0.2},
    "Topic": {"*_brow_raiseIn": 0.5, "*_brow_raiseOut": 0.6},
    "Conditional": {"*_brow_raiseIn": 0.7, "*_brow_raiseOut": 0.7, "*_eye_blink": -0.15},
    # mouth morphemes
    "MM (normal, regular)": {"*_mouth_lipsTogetherU": 0.6, "*_mouth_lipsTogetherD": 0.6, "*_mouth_pressU": 0.3,
                             "*_mouth_pressD": 0.3},
    "OO (small, thin)": {"*_mouth_funnelU": 0.6, "*_mouth_funnelD": 0.6, "*_mouth_purseU": 0.5, "*_mouth_purseD": 0.5,
                         "C_jaw": (0.0, 0.1)},
    "CHA (large)": {"C_jaw": (0.0, 0.45), "*_mouth_upperLipRaise": 0.4, "*_mouth_lowerLipDepress": 0.4,
                    "*_mouth_stretch": 0.3},
    "CS (close, recent)": {"R_mouth_cornerPull": 0.6, "R_mouth_stretch": 0.6, "R_eye_cheekRaise": 0.4,
                           "*_jaw_clench": 0.5, "*_mouth_lipsTogetherU": 0.3, "*_mouth_lipsTogetherD": 0.3},
    "TH (careless)": {"C_jaw": (0.0, 0.15), "C_tongue_inOut|C_tongue_move": 0.6, "*_mouth_lowerLipDepress": 0.3},
    "PUFF (a lot, large)": {"*_mouth_suckBlow|*_mouth_lipsBlow": 0.8, "*_mouth_lipsTogetherU": 0.5,
                            "*_mouth_lipsTogetherD": 0.5},
    "PAH (finally)": {"C_jaw": (0.0, 0.3), "*_mouth_upperLipRaise": 0.2, "*_mouth_lowerLipDepress": 0.3},
    "INTENSE": {"*_mouth_stretch": 0.7, "*_mouth_upperLipRaise": 0.4, "*_mouth_lowerLipDepress": 0.4,
                "*_jaw_clench": 0.6, "*_brow_down": 0.3},
    "Squint (far, focus)": {"*_eye_squintInner": 0.8, "*_eye_cheekRaise": 0.5, "*_brow_down": 0.3},
    # emotions
    "Smile": {"*_mouth_cornerPull": 0.7, "*_eye_cheekRaise": 0.5},
    "Big Smile": {"*_mouth_cornerPull": 0.9, "*_eye_cheekRaise": 0.7, "*_mouth_upperLipRaise": 0.3,
                  "C_jaw": (0.0, 0.25)},
    "Sad": {"*_brow_raiseIn": 0.7, "*_mouth_cornerDepress": 0.6, "*_eye_blink": 0.15},
    "Angry": {"*_brow_down": 0.9, "*_brow_lateral": 0.8, "*_eye_squintInner": 0.5, "*_nose_wrinkleUpper": 0.4,
              "*_mouth_pressU": 0.5, "*_mouth_pressD": 0.5},
    "Surprise": {"*_brow_raiseIn": 1.0, "*_brow_raiseOut": 1.0, "*_eye_blink": -0.5, "C_jaw": (0.0, 0.5)},
    "Disgust": {"*_nose_wrinkleUpper": 0.8, "*_mouth_upperLipRaise": 0.6, "*_brow_down": 0.4,
                "*_eye_squintInner": 0.3},
    "Fear": {"*_brow_raiseIn": 0.9, "*_brow_lateral": 0.5, "*_eye_blink": -0.4, "*_mouth_stretch": 0.5},
    "Eyes Closed": {"*_eye_blink": 1.0},
}


# --- the pipeline's other scripts ---
def script_folders():
    folders = []
    here = globals().get("__file__")
    if here and os.path.isfile(here):
        folders.append(os.path.dirname(os.path.abspath(here)))
    for text in bpy.data.texts:
        if text.filepath:
            folders.append(os.path.dirname(os.path.abspath(bpy.path.abspath(text.filepath))))
    try:
        with open(os.path.join(os.path.expanduser("~"), ".sign_pipeline.json"), "r", encoding="utf-8") as fh:
            noted = json.load(fh).get("config")
        if noted:
            folders.append(os.path.join(os.path.dirname(noted), "Scripts"))
    except (OSError, ValueError, AttributeError):
        pass
    return list(dict.fromkeys(folders))


def load(file_name, module_name):
    for folder in script_folders():
        path = os.path.join(folder, file_name)
        if os.path.isfile(path):
            spec = importlib.util.spec_from_file_location(module_name, path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module
    raise RuntimeError(f"{file_name} isn't next to this script or in the workspace's Scripts folder: put it there")


# --- the face board ---
def controls_for(board, key):
    """The face board bones a recipe's control means: '*_x' -> CTRL_L_x and CTRL_R_x; 'a|b' -> the first one there."""
    for option in key.split("|"):
        names = [option.replace("*", s) for s in ("L", "R")] if "*" in option else [option]
        found = [f"CTRL_{n}" for n in names if f"CTRL_{n}" in board.pose.bones]
        if found:
            return found
    return []


def limits(pb):
    """(min x, max x, min y, max y) of the control's travel, from its Limit Location constraint, or None."""
    for c in pb.constraints:
        if c.type == "LIMIT_LOCATION" and not c.mute:
            return (c.min_x if c.use_min_x else None, c.max_x if c.use_max_x else None,
                    c.min_y if c.use_min_y else None, c.max_y if c.use_max_y else None)
    return None


def scaled(amount, low, high):
    """`amount` (-1..1) of a control's travel: towards its upper limit when positive, its lower when negative; a
    control that only travels one way (0..-1, say) is moved that way whatever the sign. Without limits: amount."""
    if low is None and high is None:
        return amount
    low, high = (low if low is not None else -1.0), (high if high is not None else 1.0)
    if abs(high) < 1e-6 or abs(low) < 1e-6:          # one-way travel
        reach = high if abs(high) >= abs(low) else low
        return abs(amount) * reach
    return amount * (high if amount >= 0 else -low)


def set_pose(board, recipe):
    """Put the recipe on the face board (every other control at 0). Returns the recipe's controls it hasn't."""
    for pb in [pb for pb in board.pose.bones if pb.name.startswith("CTRL_")]:
        pb.location = (0.0, 0.0, 0.0)
    missing = []
    for key, amount in recipe.items():
        names = controls_for(board, key)
        if not names:
            missing.append(key)
            continue
        for name in names:
            pb = board.pose.bones[name]
            lim = limits(pb) or (None, None, None, None)
            if isinstance(amount, (tuple, list)):
                x, y = amount
                pb.location.x = scaled(x * STRENGTH, lim[0], lim[1])
                pb.location.y = scaled(y * STRENGTH, lim[2], lim[3])
            else:
                pb.location.y = scaled(amount * STRENGTH, lim[2], lim[3])
    return missing


_evaluator = {}


def riglogic_ops():
    """The Character DNA add-on's 'evaluate now' operators (force_evaluate), as (namespace, name)."""
    found = []
    for space in DNA_ADDON_MODULES:
        ops = getattr(bpy.ops, space, None)
        for name in (dir(ops) if ops else []):
            if "force_evaluate" in name.lower() or name.lower() in ("evaluate", "evaluate_rig_logic", "evaluate_riglogic"):
                found.append((space, name))
    return found


def update_face(board, heads):
    """Let RigLogic move the face from the face board now: the scene updated, and the Character DNA add-on asked to
    evaluate (its force_evaluate), with the face board - else the head's rig - active, whichever it accepts."""
    scene, layer = bpy.context.scene, bpy.context.view_layer
    layer.update()
    scene.frame_set(scene.frame_current)
    if _evaluator.get("failed"):
        return
    window = next(iter(bpy.context.window_manager.windows), None) if bpy.context.window_manager else None
    area = next((a for a in window.screen.areas if a.type == "VIEW_3D"), None) if window else None
    region = next((r for r in area.regions if r.type == "WINDOW"), None) if area else None
    chosen = _evaluator.get("how")
    tries = [chosen] if chosen else [(space, name, target) for space, name in riglogic_ops()
                                     for target in [board] + list(heads) + [None]]
    for space, name, target in tries:
        op = getattr(getattr(bpy.ops, space), name)
        override = {k: v for k, v in (("window", window), ("area", area), ("region", region)) if v is not None}
        if target is not None:
            override.update(active_object=target, object=target, selected_objects=[target])
        try:
            with bpy.context.temp_override(**override):
                if not op.poll():
                    continue
                if "FINISHED" in op():
                    _evaluator["how"] = (space, name, target)
                    layer.update()
                    return
        except Exception:
            continue
    _evaluator["failed"] = True


def face_vertices(meshes):
    """The face meshes' vertices as the face shows them now (deformed by joints and shape keys), as one array."""
    import numpy as np
    depsgraph = bpy.context.evaluated_depsgraph_get()
    chunks = []
    for obj in meshes:
        ev = obj.evaluated_get(depsgraph)
        mesh = ev.to_mesh()
        try:
            co = np.empty(len(mesh.vertices) * 3, dtype=np.float64)
            mesh.vertices.foreach_get("co", co)
        finally:
            ev.to_mesh_clear()
        chunks.append(co)
    return np.concatenate(chunks) if chunks else np.zeros(0)


def check_face(rig, board, magic):
    """Does the face follow the face board? (True/False, lines saying what was found)."""
    import numpy as np
    scene, layer = bpy.context.scene, bpy.context.view_layer
    meshes = [o for o in magic.face_meshes(rig) if o.type == "MESH"]
    lines = []
    heads = [o for o in scene.objects if o.type == "ARMATURE" and any(b.name.startswith("FACIAL_") for b in o.data.bones)]
    for obj in meshes:
        keys = obj.data.shape_keys
        owners = sorted({m.object.name for m in obj.modifiers if m.type == "ARMATURE" and m.object} |
                        ({obj.parent.name} if obj.parent else set()))
        lines.append(f"    face mesh '{obj.name}': {len(keys.key_blocks) - 1 if keys else 0} shape keys, moved by "
                     f"{', '.join(owners) or 'nothing'}")
    lines.append(f"    face skeletons (FACIAL_ bones): {', '.join(o.name for o in heads) or 'none'}")
    enabled = [a.module for a in bpy.context.preferences.addons]
    addon = next((m for m in enabled for w in DNA_ADDON_MODULES if m == w or m.endswith("." + w)), None)
    lines.append(f"    Character DNA add-on: {addon or 'NOT ENABLED'}")
    for space in DNA_ADDON_MODULES:
        ops = getattr(bpy.ops, space, None)
        names = [n for n in dir(ops) if any(w in n.lower() for w in ("eval", "rig_logic", "riglogic"))] if ops else []
        if names:
            lines.append(f"    its RigLogic operators: {', '.join(f'{space}.{n}' for n in names)}")
    if not meshes:
        return False, lines
    set_pose(board, {})
    update_face(board, heads)
    before = face_vertices(meshes)
    set_pose(board, {"C_jaw": (0.0, 1.0), "*_mouth_cornerPull": 1.0, "*_brow_raiseIn": 1.0, "*_eye_blink": 1.0})
    update_face(board, heads)
    after = face_vertices(meshes)
    set_pose(board, {})
    update_face(board, heads)
    how = _evaluator.get("how")
    lines.append("    RigLogic asked to evaluate with " + (f"{how[0]}.{how[1]}" + (f" ('{how[2].name}' active)" if how[2] else "")
                                                         if how else "nothing (no add-on operator accepted)"))
    moved = float(np.abs(after - before).max()) if len(before) == len(after) and len(before) else 0.0
    lines.insert(0, f"    the jaw opened, a smile, brows up and eyes closed on the face board moved the face by "
                    f"{moved:.4f} at most")
    return moved > 1e-4, lines


def generate():
    started = time.perf_counter()
    print("[generate_face_poses]")
    conv = load("convert_poses_to_metahuman.py", "sgm_convert_poses")
    magic = load("pose_magic_lib.py", "sgm_pose_magic_lib")
    rig = conv.find_mh_rig()
    board = magic.face_board_of(rig)
    if board is None:
        raise RuntimeError("No face board in this file (an armature called *_face_gui, from the Character DNA add-on): "
                           "run this in MetaHuman_character.blend")
    storage = conv.workspace_path("storage", "")
    folder = OUTPUT_FOLDER or (os.path.join(storage, LIBRARY_NAME) if storage else "")
    if not folder:
        raise RuntimeError("No pipeline_config.json found: set OUTPUT_FOLDER at the top of this script")
    os.makedirs(folder, exist_ok=True)
    controls = [pb for pb in board.pose.bones if pb.name.startswith("CTRL_") and "GUI" not in pb.name]
    print(f"  face board: '{board.name}', {len(controls)} controls")
    print(f"  saved to:   {folder}")

    scene, layer = bpy.context.scene, bpy.context.view_layer
    if bpy.context.object is not None and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    ad = board.animation_data
    old_action = ad.action if ad else None
    if ad:
        ad.action = None                            # so the board holds the pose we set, not its animation
    old_pose = {pb.name: pb.matrix_basis.copy() for pb in board.pose.bones}
    if CHECK_FACE and THUMBNAILS:
        try:
            follows, lines = check_face(rig, board, magic)
        finally:
            for name, basis in old_pose.items():
                board.pose.bones[name].matrix_basis = basis
        print("  does the face follow the face board?")
        for line in lines:
            print(line)
        if not follows:
            if ad:
                ad.action = old_action
            raise RuntimeError(
                "The face doesn't follow the face board, so every thumbnail would show the same face (nothing was "
                "saved). The face board only moves the face through RigLogic, which the Character DNA add-on runs on "
                "the DNA head: the console lists what this file has. Either the head isn't the DNA head (a face "
                "moved only by MH_face_skeleton is the FBX face: export the head DNA with export_metahuman.py and "
                "run build_metahuman_rig.py again with the add-on enabled), or the add-on's RigLogic isn't running on "
                "it (its panel in the sidebar, N: turn on evaluating the bones / shape keys, or its auto evaluate). "
                "To make the poses anyway, without thumbnails: THUMBNAILS = False")
        print("    yes")
    face_heads = [o for o in scene.objects if o.type == "ARMATURE" and o is not board
                  and any(b.name.startswith("FACIAL_") for b in o.data.bones)]
    keep = set(magic.face_meshes(rig)) | set(magic.character_objects(rig))
    keep -= set(board.children_recursive)           # the board's own frame and labels stay out of the picture
    hidden, camera, made, skipped, missing_all = [], None, 0, 0, {}
    try:
        if THUMBNAILS:
            camera = magic.frame_face(rig, FACE_VIEW)
            for obj in scene.objects:
                if obj not in keep and obj.type not in ("CAMERA", "LIGHT") and obj.visible_get():
                    obj.hide_set(True)
                    hidden.append(obj)
        for name, recipe in POSES.items():
            pose_name = f"{PREFIX} {name}".strip()
            path = os.path.join(folder, "".join("_" if c in '<>:"/\\|?*' else c for c in pose_name) + ".asset.blend")
            if os.path.isfile(path) and not OVERWRITE:
                skipped += 1
                continue
            missing = set_pose(board, recipe)
            for key in missing:
                missing_all.setdefault(key, []).append(name)
            update_face(board, face_heads)          # RigLogic moves the face from the board
            action = magic.pose_action(board, controls, pose_name)
            action.asset_mark()
            action.asset_data.author = AUTHOR
            action.asset_data.description = (f"Face board pose: {name}" + (f" (left out, not on this board: "
                                             f"{', '.join(missing)})" if missing else ""))
            action.asset_data.copyright = "CC0"
            action.asset_data.tags.new("face")
            if THUMBNAILS and camera is not None:
                try:
                    conv.viewport_thumbnail(action, camera)
                except Exception as err:
                    print(f"    {pose_name}: no thumbnail ({err})")
            bpy.data.libraries.write(path, {action}, path_remap="NONE", fake_user=True, compress=True)
            bpy.data.actions.remove(action)
            made += 1
            print(f"  [{made}] {pose_name}" + (f"   (not on this board: {', '.join(missing)})" if missing else ""))
    finally:
        for obj in hidden:
            try:
                obj.hide_set(False)
            except (ReferenceError, RuntimeError):
                pass
        if camera is not None:
            data = camera.data
            bpy.data.objects.remove(camera)
            if data is not None and data.users == 0:
                bpy.data.cameras.remove(data)
        for name, basis in old_pose.items():
            board.pose.bones[name].matrix_basis = basis
        if ad:
            ad.action = old_action
        scene.frame_set(scene.frame_current)
    note = conv.add_library(LIBRARY_NAME, folder) if made else ""
    if skipped and not made:
        print(f"  Nothing was made: all {skipped} poses are in {folder} already, and OVERWRITE is False. Set "
              "OVERWRITE = True at the top of this script to make them again.")
    print(f"Made {made} face pose(s) in {time.perf_counter() - started:.1f} s"
          + (f"; {skipped} were there already (OVERWRITE = True makes them again)" if skipped else "") + ".")
    if missing_all:
        print("  controls this face board hasn't (left out of those poses; check the names against the board's bones):")
        for key, names in sorted(missing_all.items()):
            print(f"    {key}: {', '.join(names)}")
    if note:
        print(f"  {note}")
    print(f"  Apply one: select '{board.name}', Pose Mode, then the pose from the Asset Browser (library "
          f"'{LIBRARY_NAME}'). Refresh the Asset Browser to see new thumbnails.")


if __name__ == "__main__":
    generate()
