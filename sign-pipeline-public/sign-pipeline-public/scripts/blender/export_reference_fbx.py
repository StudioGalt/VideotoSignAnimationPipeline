import bpy
import math
import os
import re
from mathutils import Matrix, Quaternion, Vector

# The MetaHuman take as an Xsens-style "Reference" FBX: the format the Galtis pipeline was built on (Xsens MVN
# exports: a 'Reference' root, 53 bones Hips / Spine..Spine3 / Neck / Head / LeftShoulder ... in a T-pose, Y up,
# centimetres, rotations on every bone and the hips' position). Galtis follows that skeleton as before, so the
# spine lines up - unlike a straight MetaHuman -> Galtis retarget, where the rest poses and the number of
# spine bones differ.
#
# Run it in a MetaHuman take file (the take's .blend, "<take> Working.blend" or "<stamp> Unedited.blend") - the
# animation as it plays, with every animation layer. It:
#   1. brings in the Reference skeleton from TEMPLATE_FBX (any Xsens export, e.g. SG_ASL_Persona_3821-4248.fbx)
#   2. keys it from the MetaHuman on every frame of the scene's range:
#        - arms, legs, hands, fingers and feet point where the MetaHuman's do (the T-pose / A-pose difference is
#          taken out by bone direction; the hand's palm by the line across its knuckles)
#        - the chest turns as the MetaHuman's does (Spine3 follows spine_05: the shoulders hang from both); the
#          MetaHuman's spine_01..spine_04 are spread over Spine, Spine1 and Spine2 by height; the Neck follows
#          neck_01 and neck_02 half and half
#        - the hips move as the MetaHuman's pelvis does, scaled to the Reference skeleton's size
#   3. saves "<stamp> Reference.fbx" in the take's folder (and, with KEEP_IN_FILE, leaves the Reference rig in
#      this file, hidden, to compare) - the take file itself is not changed on disk
# Then bring "<stamp> Reference.fbx" into Galtis the way Xsens takes were.
#
# The face is not in the FBX (Xsens has none): it stays in the take's expression curves (*_body_curves.json).

# --- config ---
TEMPLATE_FBX = None            # an Xsens MVN FBX; only its skeleton is used. None = paths.reference_template in
                               #   pipeline_config.json (<workspace>/MetaHuman/Xsens_reference.fbx)
MH_RIG_NAME = "MH_rig"         # "" = the armature made by build_metahuman_rig.py
REFERENCE_NAME = "Reference"
EXPORT_FPS = 0                 # 0 = the take's own rate; e.g. 240 = resampled to Xsens's rate
SAVE_AS = "{stamp} Reference.fbx"
KEEP_IN_FILE = False           # True = leave the Reference rig in this file (hidden) to compare; the .blend isn't saved
FIX_FLIPS = True               # first make the MetaHuman's quaternion keys continuous (a key stored as -q between keys
                               #   stored as q makes Blender turn the long way round: a finger spins in a frame or two);
                               #   the pose on every key stays the same. The take file isn't saved by this script
JUMP = 25.0                    # degrees a bone may turn from one frame to the next before the export warns of a flip

# Reference bone -> MetaHuman bone (arms, legs, hands, fingers: matched by direction)
LIMBS = {
    "LeftShoulder": "clavicle_l", "LeftArm": "upperarm_l", "LeftForeArm": "lowerarm_l", "LeftHand": "hand_l",
    "RightShoulder": "clavicle_r", "RightArm": "upperarm_r", "RightForeArm": "lowerarm_r", "RightHand": "hand_r",
    "LeftUpLeg": "thigh_l", "LeftLeg": "calf_l", "LeftFoot": "foot_l", "LeftToeBase": "ball_l",
    "RightUpLeg": "thigh_r", "RightLeg": "calf_r", "RightFoot": "foot_r", "RightToeBase": "ball_r",
}
for side, s in (("Left", "l"), ("Right", "r")):
    for finger, mh in (("Thumb", "thumb"), ("Index", "index"), ("Middle", "middle"), ("Ring", "ring"), ("Pinky", "pinky")):
        for i in (1, 2, 3):
            LIMBS[f"{side}Hand{finger}{i}"] = f"{mh}_0{i}_{s}"
# the bone each one points at (its direction), on both skeletons
AIM = {"LeftShoulder": "LeftArm", "LeftArm": "LeftForeArm", "LeftForeArm": "LeftHand", "LeftHand": "LeftHandMiddle1",
       "RightShoulder": "RightArm", "RightArm": "RightForeArm", "RightForeArm": "RightHand", "RightHand": "RightHandMiddle1",
       "LeftUpLeg": "LeftLeg", "LeftLeg": "LeftFoot", "LeftFoot": "LeftToeBase",
       "RightUpLeg": "RightLeg", "RightLeg": "RightFoot", "RightFoot": "RightToeBase"}
for side in ("Left", "Right"):
    for finger in ("Thumb", "Index", "Middle", "Ring", "Pinky"):
        AIM[f"{side}Hand{finger}1"] = f"{side}Hand{finger}2"
        AIM[f"{side}Hand{finger}2"] = f"{side}Hand{finger}3"
# the palm: the line across the knuckles, index -> pinky
ACROSS = {"LeftHand": ("LeftHandIndex1", "LeftHandPinky1"), "RightHand": ("RightHandIndex1", "RightHandPinky1")}
# hips to head: the MetaHuman's chain and the Reference's
MH_TORSO = ["pelvis", "spine_01", "spine_02", "spine_03", "spine_04", "spine_05", "neck_01", "neck_02", "head"]
REF_TORSO = ["Hips", "Spine", "Spine1", "Spine2", "Spine3", "Neck", "Head"]
MH_CHEST, REF_CHEST = "spine_05", "Spine3"     # the bones the shoulders hang from: these two turn alike


def pipeline_settings():
    """(pipeline_config module, settings) from the workspace's pipeline_config.json, or (None, None) without one.
    The config is looked for above the open .blend, above this script, and in the workspace create_workspace.py
    noted on this computer; pipeline_config.py is loaded from next to this script or that workspace's Scripts."""
    import json, sys
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
    for folder in folders + [os.path.join(os.path.dirname(f), "common") for f in folders]:
        if os.path.isfile(os.path.join(folder, "pipeline_config.py")) and folder not in sys.path:
            sys.path.append(folder)
    try:
        import pipeline_config
    except ImportError:
        return None, None
    try:
        cfg = pipeline_config.load_config([p for p in [bpy.data.filepath] + folders if p])
    except (OSError, ValueError) as err:
        print(f"  pipeline_config.json couldn't be read: {err}")
        return pipeline_config, None
    return (pipeline_config, cfg) if cfg.get("_config_path") else (pipeline_config, None)


def workspace_path(key, fallback=None):
    """A folder or file from the "paths" of pipeline_config.json (absolute), or `fallback` without settings."""
    pc, cfg = pipeline_settings()
    if cfg is None:
        return fallback
    getter = getattr(pc, "setting_path", None)
    if getter is not None:
        return getter(cfg, key, fallback)
    try:
        return pc.project_path(cfg, key)
    except KeyError:
        return fallback


def rot3(m):
    return m.to_3x3().normalized()


def find_mh_rig():
    obj = bpy.data.objects.get(MH_RIG_NAME) if MH_RIG_NAME else None
    if obj is not None and obj.type == "ARMATURE":
        return obj
    return next((o for o in bpy.context.scene.objects if o.type == "ARMATURE" and o.get("sgm_rig") == "metahuman"), None)


def take_stamp():
    words = os.path.splitext(bpy.path.basename(bpy.data.filepath))[0].split()
    return " ".join(words[:-1]) if len(words) > 1 else " ".join(words)


def bring_in_reference():
    """The Reference skeleton from TEMPLATE_FBX, without its animation."""
    global TEMPLATE_FBX
    if not TEMPLATE_FBX:
        TEMPLATE_FBX = workspace_path("reference_template", "")
    if not TEMPLATE_FBX or not os.path.isfile(TEMPLATE_FBX):
        raise RuntimeError(f"No Xsens template FBX at {TEMPLATE_FBX or '(no pipeline_config.json found)'}. Put any "
                           "Xsens MVN FBX there (only its skeleton is used), or set TEMPLATE_FBX at the top of this "
                           "script / paths.reference_template in pipeline_config.json")
    before_obj, before_act = set(bpy.data.objects), set(bpy.data.actions)
    scene = bpy.context.scene
    timing = (scene.render.fps, scene.render.fps_base, scene.frame_start, scene.frame_end)
    bpy.ops.import_scene.fbx(filepath=TEMPLATE_FBX, use_anim=False)
    scene.render.fps, scene.render.fps_base, scene.frame_start, scene.frame_end = timing   # (the import sets its own)
    new = [o for o in bpy.data.objects if o not in before_obj]
    ref = next((o for o in new if o.type == "ARMATURE"), None)
    for obj in new:
        if obj is not ref:
            bpy.data.objects.remove(obj)
    for act in [a for a in bpy.data.actions if a not in before_act]:
        bpy.data.actions.remove(act)
    if ref is None:
        raise RuntimeError(f"No skeleton in {TEMPLATE_FBX}")
    missing = [n for n in list(LIMBS) + REF_TORSO if n not in ref.data.bones]
    if missing:
        bpy.data.objects.remove(ref)
        raise RuntimeError(f"{os.path.basename(TEMPLATE_FBX)} isn't an Xsens skeleton (no {', '.join(missing[:6])})")
    old = bpy.data.objects.get(REFERENCE_NAME)
    if old is not None and old is not ref:
        old.name = REFERENCE_NAME + " old"
    ref.name = REFERENCE_NAME
    if ref.animation_data:
        ref.animation_data.action = None
    for pb in ref.pose.bones:
        pb.rotation_mode = "QUATERNION"
        pb.location, pb.rotation_quaternion, pb.scale = (0, 0, 0), (1, 0, 0, 0), (1, 1, 1)
    return ref


def fix_quaternion_flips(actions):
    """Keep every bone's rotation_quaternion keys on one side: a key stored as -q (the same rotation) between keys
    stored as q is turned back to q, so Blender turns the short way between them instead of all the way round. The
    pose on every key stays exactly as it is. Returns (keys turned back, bones)."""
    flipped, bones = 0, set()
    for action in actions:
        for layer in action.layers:
            for strip in layer.strips:
                for bag in strip.channelbags:
                    groups = {}
                    for fc in bag.fcurves:
                        if fc.data_path.endswith(".rotation_quaternion") and 0 <= fc.array_index < 4:
                            groups.setdefault(fc.data_path, [None] * 4)[fc.array_index] = fc
                    for path, fcs in groups.items():
                        if any(f is None for f in fcs):
                            continue
                        keys = [{round(k.co.x, 3): k for k in f.keyframe_points} for f in fcs]
                        previous = None
                        for t in sorted(set().union(*keys)):
                            q = [kk[t].co.y if t in kk else f.evaluate(t) for kk, f in zip(keys, fcs)]
                            if previous is not None and sum(a * b for a, b in zip(q, previous)) < 0.0:
                                for kk in keys:
                                    k = kk.get(t)
                                    if k is not None:
                                        k.co.y, k.handle_left.y, k.handle_right.y = -k.co.y, -k.handle_left.y, -k.handle_right.y
                                q = [-v for v in q]
                                flipped += 1
                                bones.add(path.split('"')[1] if '"' in path else path)
                            previous = q
                        for f in fcs:
                            f.update()
    return flipped, sorted(bones)


def rig_actions(obj):
    ad = obj.animation_data
    if ad is None:
        return []
    found = [ad.action] if ad.action else []
    found += [st.action for t in ad.nla_tracks for st in t.strips if st.action and st.action not in found]
    return found


def frame_of(primary, secondary=None):
    """A rotation whose Y is primary (and X along secondary, made square to it)."""
    y = primary.normalized()
    if secondary is None or secondary.length < 1e-9 or abs(secondary.normalized().dot(y)) > 0.999:
        return y.to_track_quat("Y", "Z").to_matrix()
    x = (secondary - y * secondary.dot(y)).normalized()
    z = x.cross(y)
    return Matrix((x, y, z)).transposed()


class Mapping:
    """What is worked out once, from the two skeletons at rest."""

    def __init__(self, mh, ref):
        self.mh, self.ref = mh, ref
        mw, rw = mh.matrix_world, ref.matrix_world
        mh_head = {b.name: mw @ b.head_local for b in mh.data.bones}
        ref_head = {b.name: rw @ b.head_local for b in ref.data.bones}
        self.mh_rest = {b.name: rot3(mw @ b.matrix_local) for b in mh.data.bones}
        self.ref_rest = {b.name: rot3(rw @ b.matrix_local) for b in ref.data.bones}

        # limbs: offset K so that ref_world = mh_world @ K, with the bones pointing the same way at rest
        self.offset, self.align = {}, {}
        for ref_name, mh_name in LIMBS.items():
            aim = AIM.get(ref_name)
            if aim is None:                                    # finger tips and toes: like the bone above
                parent = self.ref.data.bones[ref_name].parent.name
                align = self.align[parent]
            else:
                src = mh_head[LIMBS[aim]] - mh_head[mh_name]
                dst = ref_head[aim] - ref_head[ref_name]
                across = ACROSS.get(ref_name)
                if across:
                    s2 = mh_head[LIMBS[across[1]]] - mh_head[LIMBS[across[0]]]
                    d2 = ref_head[across[1]] - ref_head[across[0]]
                    align = frame_of(dst, d2) @ frame_of(src, s2).inverted()
                else:
                    align = src.rotation_difference(dst).to_matrix()
            self.align[ref_name] = align
            posed = align @ self.mh_rest[mh_name]                 # the MetaHuman bone turned into the T-pose
            self.offset[ref_name] = posed.inverted() @ self.ref_rest[ref_name]

        # torso. The chest is anchored: Spine3 (the shoulders hang from it) follows spine_05 (the MetaHuman's
        # shoulders hang from that); Spine, Spine1 and Spine2 follow the MetaHuman's spine below it, by height
        # between the hips and the chest (by the middle of each bone); Neck follows both neck bones half and half.
        base, top = mh_head["pelvis"].z, mh_head["head"].z
        rbase, rtop = ref_head["Hips"].z, ref_head["Head"].z
        mh_chest, ref_chest = mh_head[MH_CHEST].z, ref_head[REF_CHEST].z
        mid = lambda heads, chain, i, lo, hi: (((heads[chain[i]] + heads[chain[i + 1]]) / 2).z - lo) / (hi - lo)
        lower = MH_TORSO[:MH_TORSO.index(MH_CHEST) + 1]                 # pelvis .. spine_05
        ref_lower = REF_TORSO[:REF_TORSO.index(REF_CHEST) + 1]          # Hips .. Spine3
        self.mh_marks = [mid(mh_head, lower, i, base, mh_chest) for i in range(len(lower) - 1)]
        self.torso = {}
        for i, name in enumerate(ref_lower[1:-1], start=1):
            f = mid(ref_head, ref_lower, i, rbase, ref_chest)
            j = max(0, min(len(self.mh_marks) - 2, sum(1 for m in self.mh_marks if m <= f) - 1))
            a, b = self.mh_marks[j], self.mh_marks[j + 1]
            w = 0.0 if f <= a else (1.0 if f >= b else (f - a) / (b - a))
            self.torso[name] = (lower[j], lower[j + 1], w)
        self.torso[REF_CHEST] = (MH_CHEST, MH_CHEST, 0.0)
        self.torso["Neck"] = ("neck_01", "neck_02", 0.5)
        self.scale = (rtop - rbase) / max(top - base, 1e-9)
        self.mh_hips = mh_head["pelvis"]
        self.ref_hips = ref_head["Hips"]

    def world_rotations(self):
        """The Reference bones' world rotations (and the hips' world position) for the MetaHuman's current pose."""
        mw = self.mh.matrix_world
        pose = {n: mw @ self.mh.pose.bones[n].matrix for n in set(LIMBS.values()) | set(MH_TORSO)}
        out = {}
        for ref_name, mh_name in LIMBS.items():
            out[ref_name] = rot3(pose[mh_name]) @ self.offset[ref_name]

        def turn(n):                                    # how far a MetaHuman bone has turned from its rest, in world
            return (rot3(pose[n]) @ self.mh_rest[n].inverted()).to_quaternion()
        out["Hips"] = turn("pelvis").to_matrix() @ self.ref_rest["Hips"]
        out["Head"] = turn("head").to_matrix() @ self.ref_rest["Head"]
        for name, (a, b, w) in self.torso.items():
            blend = turn(a) if a == b or w <= 0.0 else (turn(b) if w >= 1.0 else turn(a).slerp(turn(b), w))
            out[name] = blend.to_matrix() @ self.ref_rest[name]
        hips = self.ref_hips + (pose["pelvis"].to_translation() - self.mh_hips) * self.scale
        return out, hips


def pose_values(ref, world, hips):
    """Local rotations (quaternions) for every bone from the world rotations; the hips' location too."""
    inv = rot3(ref.matrix_world).inverted()
    arm_scale = ref.matrix_world.to_scale()[0]
    posed = {}
    values = {}
    for bone in ref.data.bones:                        # parents come before their children
        rest = bone.matrix_local.to_3x3()
        want = inv @ world[bone.name] if bone.name in world else None
        if bone.parent is None:
            base = rest
        else:
            base = posed[bone.parent.name] @ (bone.parent.matrix_local.to_3x3().inverted() @ rest)
        if want is None:                               # not driven: keeps its rest relative to its parent
            posed[bone.name] = base
            values[bone.name] = (Quaternion(), None)
            continue
        basis = (base.inverted() @ want).to_quaternion()
        posed[bone.name] = base @ basis.to_matrix()
        loc = None
        if bone.parent is None:
            local_head = ref.matrix_world.inverted() @ hips
            loc = rest.inverted() @ (local_head - bone.head_local)
        values[bone.name] = (basis, loc)
    return values


def key_reference(ref, frames, samples):
    """Write the samples as an action on the Reference rig, every frame keyed (like an Xsens export)."""
    action = bpy.data.actions.new(take_stamp() or "Take")
    ref.animation_data_create()
    ref.animation_data.action = action
    slot = ref.animation_data.action_slot
    if slot is None:
        slot = action.slots.new(id_type="OBJECT", name=ref.name)
        ref.animation_data.action_slot = slot
    strip = action.layers.new("Xsens Animation Layer").strips.new(type="KEYFRAME")
    bag = strip.channelbag(slot, ensure=True)
    for bone in ref.data.bones:
        quats = [s[bone.name][0] for s in samples]
        for k in range(1, len(quats)):                 # no flips between frames
            if quats[k].dot(quats[k - 1]) < 0:
                quats[k] = -quats[k]
        channels = [("rotation_quaternion", [[q[i] for q in quats] for i in range(4)])]
        if bone.parent is None:
            locs = [s[bone.name][1] or Vector() for s in samples]
            channels.append(("location", [[v[i] for v in locs] for i in range(3)]))
        for prop, series in channels:
            for index, values in enumerate(series):
                fc = bag.fcurves.new(f'pose.bones["{bone.name}"].{prop}', index=index, group_name=bone.name)
                rest = (1.0 if prop == "rotation_quaternion" and index == 0 else 0.0)
                fc.keyframe_points.add(len(frames) + 1)
                co = [REST_FRAME, rest] + [c for f, v in zip(frames, values) for c in (f, v)]
                fc.keyframe_points.foreach_set("co", co)
                fc.keyframe_points.foreach_set("interpolation", [0] + [2] * len(frames))   # CONSTANT, then LINEAR
                fc.update()
    return action


REST_FRAME = -10   # a key with the rest pose, before the take: the FBX's bones are written as they stand on the
                   # current frame (Blender writes no bind pose for a skeleton alone), so the export is made from here
                   # and the FBX's rest is the T-pose, as in an Xsens export. Not part of the exported range.


def export(ref, path, frames, take):
    for obj in bpy.context.view_layer.objects:
        obj.select_set(False)
    ref.hide_set(False)
    ref.select_set(True)
    bpy.context.view_layer.objects.active = ref
    scene = bpy.context.scene
    keep = (scene.frame_start, scene.frame_end, scene.frame_current, scene.name)
    scene.frame_start, scene.frame_end = frames[0], frames[-1]
    scene.frame_set(REST_FRAME)
    scene.name = take                                    # the FBX's take is named after the scene
    try:
        bpy.ops.export_scene.fbx(filepath=path, use_selection=True, object_types={"ARMATURE"}, add_leaf_bones=False,
                                 bake_anim=True, bake_anim_use_all_actions=False, bake_anim_use_nla_strips=False,
                                 bake_anim_force_startend_keying=True, bake_anim_simplify_factor=0.0,
                                 bake_anim_step=1.0, use_armature_deform_only=False)
    finally:
        scene.name = keep[3]
        scene.frame_start, scene.frame_end = keep[0], keep[1]
        scene.frame_set(keep[2])


def make_reference_fbx(mh, path, take):
    """Key a Reference skeleton from the MetaHuman rig over the scene's frame range and save it as an FBX at path,
    its take called take. Returns path. (With KEEP_IN_FILE the Reference rig stays in the file, on the take's frames.)"""
    missing = [n for n in set(LIMBS.values()) | set(MH_TORSO) if n not in mh.data.bones]
    if missing:
        raise RuntimeError(f"'{mh.name}' has no {', '.join(sorted(missing)[:6])}: is it the MetaHuman rig?")
    if bpy.context.object is not None and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    scene = bpy.context.scene
    keep_frame, keep_fps = scene.frame_current, (scene.render.fps, scene.render.fps_base)
    if FIX_FLIPS:
        flipped, bones = fix_quaternion_flips(rig_actions(mh))
        if flipped:
            print(f"  flips fixed first: {flipped} key(s) stored the other way round (as -q) turned back, on "
                  + ", ".join(bones[:10]) + (" ..." if len(bones) > 10 else "")
                  + " - save the take file to keep the fix (Fix Flips in the Metahuman Rig tab does the same)")
    ref = bring_in_reference()
    try:
        mapping = Mapping(mh, ref)
        rate = scene.render.fps / scene.render.fps_base
        out_rate = EXPORT_FPS or rate
        step = rate / out_rate
        first, last = scene.frame_start, scene.frame_end
        count = int(round((last - first) / step)) + 1
        samples, frames, jumps, before = [], [], [], None
        for k in range(count):
            t = first + k * step
            scene.frame_set(int(math.floor(t)), subframe=t - math.floor(t))
            world, hips = mapping.world_rotations()
            if before is not None:                        # a bone turning further in a frame than a body can: a flip
                for name, rot in world.items():
                    a = math.degrees(before[name].to_quaternion().rotation_difference(rot.to_quaternion()).angle)
                    if min(a, 360.0 - a) > JUMP * step:
                        jumps.append((min(a, 360.0 - a), name, t))
            before = world
            samples.append(pose_values(ref, world, hips))
            frames.append(k)                              # the FBX starts at time 0 (Blender reads that as frame 1)
        if EXPORT_FPS:
            scene.render.fps, scene.render.fps_base = int(EXPORT_FPS), 1.0
        action = key_reference(ref, frames, samples)
        action.name = take
        torso = ", ".join(f"{n} <- {a}/{b} {w:.2f}" for n, (a, b, w) in mapping.torso.items())
        print(f"  '{mh.name}' -> '{ref.name}' ({len(ref.data.bones)} bones), frames {first}-{last} at {rate:g} fps"
              + (f" -> {len(frames)} frames at {out_rate:g} fps" if EXPORT_FPS else "") + f"; size x{mapping.scale:.3f}")
        print(f"  spine: {torso}")
        if jumps:
            jumps.sort(reverse=True)
            bones = sorted({n for _, n, _ in jumps})
            print(f"  WARNING: {len(jumps)} time(s) a bone turns more than {JUMP:g} deg in a frame (a flip in the take "
                  f"itself, now in the FBX too): {', '.join(bones[:10])}" + (" ..." if len(bones) > 10 else ""))
            for a, n, t in jumps[:6]:
                print(f"      {n}: {a:.0f} deg into frame {t:g}")
        export(ref, path, frames, take)
        print(f"[saved] {path}")
        if KEEP_IN_FILE:                                  # back onto the take's frames, to compare in this file
            for bag in [b for l in action.layers for st in l.strips for b in st.channelbags]:
                for fc in bag.fcurves:
                    for kp in fc.keyframe_points:
                        if kp.co.x >= 0:
                            x = first + kp.co.x * step
                            kp.co.x, kp.handle_left.x, kp.handle_right.x = x, x, x
                    fc.update()
        return path
    finally:
        scene.render.fps, scene.render.fps_base = keep_fps
        scene.frame_set(keep_frame)
        if KEEP_IN_FILE:
            ref.hide_set(True)
        else:
            act = ref.animation_data.action if ref.animation_data else None
            data = ref.data
            bpy.data.objects.remove(ref)
            if data.users == 0:
                bpy.data.armatures.remove(data)
            if act is not None and act.users == 0:
                bpy.data.actions.remove(act)


def main():
    print("[export_reference_fbx]")
    if not bpy.data.filepath:
        raise RuntimeError("Open a MetaHuman take file first (it is named after the take)")
    mh = find_mh_rig()
    if mh is None:
        raise RuntimeError("No MetaHuman rig in this file (run this in a take file made by import_unreal_take.py)")
    take = take_stamp() or "Take"
    path = os.path.join(os.path.dirname(bpy.data.filepath), SAVE_AS.format(stamp=take))
    return make_reference_fbx(mh, path, take)


if __name__ == "__main__":
    main()
