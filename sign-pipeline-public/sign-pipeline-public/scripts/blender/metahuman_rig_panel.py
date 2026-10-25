import bpy
import datetime, math, json, os, re, shutil
from mathutils import Matrix, Vector

# "Metahuman Rig" tab in the 3D Viewport sidebar (N-panel), for the rig made by build_metahuman_rig.py.
#   - the FK/IK switch of each arm and leg (0 = FK, 1 = IK)
#   - Match IK to FK: the IK hand / foot goes onto the FK one and the elbow / knee target behind / in front
#     of the FK elbow / knee, so the IK limb takes the FK pose; then the limb can be switched to IK without a jump
#   - Match FK to IK: the FK bones are turned to the IK pose; then the limb can be switched to FK without a jump
#   - on the current frame, the scene's frame range, or a range you set; keyed or not
#   - mirroring: Copy Pose / Paste Mirrored, and Left -> Right / Right -> Left. Blender's own "Paste Flipped"
#     mirrors each bone's local rotation, which only works when the left and right bones have mirror-image axes;
#     the MetaHuman's don't (Unreal sets up its right side differently). These mirror the pose itself, across
#     the character's middle, whatever the bones' axes
#   - cleanup: Trim Start (a frame becomes frame 0, everything moves earlier), Trim End (a frame becomes the
#     last one), on every animation layer, the face board and the shape keys alike, easing into the neutral pose;
#     Rest Hand (hold one hand and its fingers in the frame-1 pose over a range, for one-handed signs).
#     Pose Magic: a pose asset and JSON / CSV / TXT for every timeline marker (runs pose_magic_lib.py).
#     Rename File: save as "<Product Line> <Language> <Name> <Date> Unedited.blend" in a new folder of that name.
#     Each writes a line to "<take> Framelog.txt"
#   - Import Video: the take's source video on an upright plane beside the character, playing in step with the
#     timeline (at the video's own speed, whatever the scene's frame rate), to compare the animation against
#   - which bone collections are shown
# Run once from the Text Editor. It also ticks Text > Register for itself, so the tab comes back when the
# file is opened again (Blender asks to allow scripts to run unless Auto Run is on in the Preferences).
# build_metahuman_rig.py puts it in the character file, and every take filed from that file has it too.

TAB = "Metahuman Rig"
SWITCH_BONE = "FKIK Switch"
COLLECTIONS = ("Torso", "Limbs FK", "Limbs IK", "Fingers", "Switch", "Deform")


# --- the rig ---
def find_rig(context):
    obj = context.object
    if obj is not None and obj.type == "ARMATURE" and obj.get("sgm_rig") == "metahuman":
        return obj
    return next((o for o in context.scene.objects if o.type == "ARMATURE" and o.get("sgm_rig") == "metahuman"), None)


def limbs(rig):
    try:
        return json.loads(rig.get("sgm_limbs", "[]"))
    except ValueError:
        return []


def limb_label(limb):
    return f"{'Arm' if limb['kind'] == 'arm' else 'Leg'} {limb['side']}"


def rotation_path(pb):
    return {"QUATERNION": "rotation_quaternion", "AXIS_ANGLE": "rotation_axis_angle"}.get(pb.rotation_mode, "rotation_euler")


def frames_to_do(context):
    s = context.scene.sgm_mh_rig
    scene = context.scene
    if s.frames == "CURRENT":
        return [scene.frame_current]
    if s.frames == "SCENE":
        return list(range(scene.frame_start, scene.frame_end + 1))
    lo, hi = sorted((s.frame_from, s.frame_to))
    return list(range(lo, hi + 1))


def pole_position(rig, limb):
    """
    Where the elbow / knee target goes so the IK limb bends like the FK one: behind the elbow / in front of the
    knee, in the plane of the FK limb. When the limb is (nearly) straight that plane means nothing - a few
    tenths of a degree turn it right round - so there the target follows the upper bone instead (where its rest
    pose puts the target, turned with the upper arm / thigh), blending from one to the other between about
    6 and 2 degrees of bend. No more knees flicking to the side on a straight leg.
    """
    pbs, bones = rig.pose.bones, rig.data.bones
    fk = limb["fk"]
    a, b, c = pbs[fk[0]].head, pbs[fk[1]].head, pbs[fk[2]].head
    reach = max((b - a).length, 1e-6)
    axis = (c - a).normalized()
    bend = (b - a) - axis * (b - a).dot(axis)
    rest_offset = bones[limb["pole"]].head_local - bones[fk[1]].head_local
    carried = pbs[fk[0]].matrix.to_3x3().normalized() @ (bones[fk[0]].matrix_local.to_3x3().normalized().inverted() @ rest_offset)
    carried = carried - axis * carried.dot(axis)
    if carried.length < 1e-9:
        carried = bend.copy()
    if carried.length < 1e-9:
        return None
    carried.normalize()
    angle = math.degrees((b - a).angle(c - b)) if (c - b).length > 1e-9 else 0.0     # how far the elbow / knee is bent
    weight = max(0.0, min(1.0, (angle - 2.0) / (6.0 - 2.0)))
    direction = carried if weight <= 0.0 else (bend.normalized() * weight + carried * (1.0 - weight))
    if direction.length < 1e-6:
        direction = carried
    return b + direction.normalized() * reach

def match_ik_to_fk(rig, limb, key, frame):
    pbs, bones = rig.pose.bones, rig.data.bones
    fk_end, control = pbs[limb["fk"][2]], pbs[limb["control"]]
    offset = bones[limb["fk"][2]].matrix_local.inverted() @ bones[limb["control"]].matrix_local
    control.matrix = fk_end.matrix @ offset
    if key:
        for path in ("location", rotation_path(control), "scale"):
            control.keyframe_insert(path, frame=frame, group=control.name)
    where = pole_position(rig, limb)
    if where is not None:
        pole = pbs[limb["pole"]]
        m = pole.matrix.copy()
        m.translation = where
        pole.matrix = m
        if key:
            pole.keyframe_insert("location", frame=frame, group=pole.name)


def match_fk_to_ik(rig, limb, key, frame):
    """Turn the FK bones to where the IK puts the limb (the IK helper bones always hold the IK pose)."""
    pbs = rig.pose.bones
    for fk_name, part in zip(limb["fk"], ("upper", "lower", "end")):
        fk, ik = pbs[fk_name], pbs.get(f"{limb[part]}_ikmch")
        if ik is None:
            continue
        location = fk.location.copy()
        fk.matrix = ik.matrix
        fk.location = location                  # FK bones only turn
        bpy.context.view_layer.update()         # the next bone down is placed from this one
        if key:
            fk.keyframe_insert(rotation_path(fk), frame=frame, group=fk.name)


def set_switch(rig, limb, value, key, frame):
    sw = rig.pose.bones[SWITCH_BONE]
    sw[limb["switch"]] = value
    if key:
        sw.keyframe_insert(f'["{limb["switch"]}"]', frame=frame, group=SWITCH_BONE)


# --- mirroring ---
_CLIPBOARD = {}      # bone name -> its pose as a change from rest, in armature space


def flip_name(name):
    flipped = getattr(bpy.utils, "flip_name", None)
    if flipped is not None:
        return flipped(name)
    for a, b in (("_L", "_R"), ("_l", "_r"), (".L", ".R"), (".l", ".r")):
        if name.endswith(a):
            return name[:-len(a)] + b
        if name.endswith(b):
            return name[:-len(b)] + a
    return name


def mirror_matrix(rig):
    """Reflection across the character's middle (the plane between its left and right shoulders / hips)."""
    bones = rig.data.bones
    for left, right in (("upperarm_l", "upperarm_r"), ("clavicle_l", "clavicle_r"), ("thigh_l", "thigh_r")):
        if left in bones and right in bones:
            a, b = bones[left].head_local, bones[right].head_local
            break
    else:
        a, b = Vector((1, 0, 0)), Vector((-1, 0, 0))
    n = (b - a).normalized()
    centre = (a + b) / 2
    reflect = Matrix.Identity(4)
    for i in range(3):
        for j in range(3):
            reflect[i][j] = (1.0 if i == j else 0.0) - 2.0 * n[i] * n[j]
    return Matrix.Translation(centre) @ reflect @ Matrix.Translation(-centre)


def pose_delta(pb):
    """The bone's pose as a change from its rest pose, in armature space."""
    return pb.matrix @ pb.bone.matrix_local.inverted()


def apply_deltas(rig, deltas, key):
    """Pose bones from {name: change from rest}; parents first, so each child is placed after its parent."""
    pbs = rig.pose.bones
    def depth(name):
        b, n = pbs[name].bone, 0
        while b.parent:
            b, n = b.parent, n + 1
        return n
    frame = bpy.context.scene.frame_current
    done = []
    for name in sorted(deltas, key=depth):
        pb = pbs.get(name)
        if pb is None:
            continue
        m = deltas[name] @ pb.bone.matrix_local
        pb.matrix = m
        bpy.context.view_layer.update()
        if key:
            for path in ("location", rotation_path(pb), "scale"):
                pb.keyframe_insert(path, frame=frame, group=pb.name)
        done.append(name)
    return done


def mirrored_deltas(rig, deltas):
    """{flipped name: mirrored change from rest}; a middle bone (no side) is mirrored onto itself."""
    s = mirror_matrix(rig)
    return {flip_name(name): s @ d @ s for name, d in deltas.items()}


def selected_controls(rig):
    return [pb for pb in rig.pose.bones if (pb.select if hasattr(pb, "select") else pb.bone.select)]


def side_controls(rig, side):
    """Every control on one side ('L' or 'R')."""
    return [pb for pb in rig.pose.bones if pb.bone.get("sgm_control") and pb.name.endswith(("_" + side, "." + side))]


def autokey(context):
    return bool(context.scene.tool_settings.use_keyframe_insert_auto)


class SGM_OT_mh_copy_pose(bpy.types.Operator):
    """Copy the pose of the selected bones (for Paste Mirrored)"""
    bl_idname = "sgm.mh_copy_pose"
    bl_label = "Copy Pose"

    @classmethod
    def poll(cls, context):
        return find_rig(context) is not None

    def execute(self, context):
        rig = find_rig(context)
        bones = selected_controls(rig)
        if not bones:
            self.report({"ERROR"}, "Select the bones to copy (Pose Mode)")
            return {"CANCELLED"}
        _CLIPBOARD.clear()
        _CLIPBOARD.update({pb.name: pose_delta(pb) for pb in bones})
        self.report({"INFO"}, f"Copied {len(bones)} bones")
        return {"FINISHED"}


class SGM_OT_mh_paste_mirrored(bpy.types.Operator):
    """Paste the copied pose mirrored: left bones onto the right ones and back, middle bones mirrored in place"""
    bl_idname = "sgm.mh_paste_mirrored"
    bl_label = "Paste Mirrored"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return find_rig(context) is not None and bool(_CLIPBOARD)

    def execute(self, context):
        rig = find_rig(context)
        done = apply_deltas(rig, mirrored_deltas(rig, _CLIPBOARD), autokey(context))
        self.report({"INFO"}, f"Pasted {len(done)} bones mirrored" + (" and keyed" if autokey(context) else ""))
        return {"FINISHED"}


class SGM_OT_mh_mirror_side(bpy.types.Operator):
    """Mirror one side of the body onto the other"""
    bl_idname = "sgm.mh_mirror_side"
    bl_label = "Mirror side"
    bl_options = {"REGISTER", "UNDO"}

    source: bpy.props.EnumProperty(items=[("L", "Left to Right", ""), ("R", "Right to Left", "")])

    @classmethod
    def description(cls, context, props):
        a, b = ("left", "right") if props.source == "L" else ("right", "left")
        return (f"Pose the {b} side as the mirror image of the {a} side (only the selected {a} bones when "
                f"'Selected only' is on)")

    @classmethod
    def poll(cls, context):
        return find_rig(context) is not None

    def execute(self, context):
        rig = find_rig(context)
        bones = side_controls(rig, self.source)
        if context.scene.sgm_mh_rig.mirror_selected:
            chosen = {pb.name for pb in selected_controls(rig)}
            bones = [pb for pb in bones if pb.name in chosen]
        if not bones:
            self.report({"ERROR"}, "Nothing to mirror on that side" + (" (select bones, or turn off 'Selected only')"
                                                                         if context.scene.sgm_mh_rig.mirror_selected else ""))
            return {"CANCELLED"}
        done = apply_deltas(rig, mirrored_deltas(rig, {pb.name: pose_delta(pb) for pb in bones}), autokey(context))
        self.report({"INFO"}, f"Mirrored {len(done)} bones" + (" and keyed" if autokey(context) else ""))
        return {"FINISHED"}


# --- cleanup: trim the start, trim the end, rest a hand ---
EASE = 20            # default Ease In / Ease Out of Trim Start / Trim End (each dialog asks)
FINGERS = ("thumb", "index", "middle", "ring", "pinky")


def control_names(rig):
    """The controls a person keys (not the MetaHuman bones, IK chain or helper bones)."""
    chain = {n for limb in limbs(rig) for n in limb["ik"]}
    return [b.name for b in rig.data.bones if b.get("sgm_control") and b.name not in chain and b.name != SWITCH_BONE
            and not re.search(r"(_ikmch|_aim|_follow\d+)$", b.name)]


def face_board(rig):
    for o in bpy.context.scene.objects:
        if o.type == "ARMATURE" and o is not rig and (o.name.endswith(("_face_gui", "_face_board"))
                                                     or sum(1 for b in o.data.bones if b.name.startswith("CTRL_")) >= 10):
            return o
    return None


def face_controls(face):
    names = set()
    for act in object_actions(face):
        for fc in all_fcurves(act):
            m = re.match(r'pose\.bones\["([^"]+)"\]', fc.data_path)
            if m and m.group(1) in face.pose.bones:
                names.add(m.group(1))
    return sorted(names) or [b.name for b in face.pose.bones if b.name.startswith("CTRL_") and "GUI" not in b.name]


def object_actions(idblock):
    ad = getattr(idblock, "animation_data", None)
    if ad is None:
        return []
    found = [ad.action] if ad.action else []
    found += [st.action for t in ad.nla_tracks for st in t.strips if st.action and st.action not in found]
    return found


def scene_actions():
    """Every action that animates something in the scene: objects (with all their layers) and shape keys."""
    found = []
    for obj in bpy.context.scene.objects:
        blocks = [obj]
        if obj.type == "MESH" and obj.data.shape_keys:
            blocks.append(obj.data.shape_keys)
        for block in blocks:
            for act in object_actions(block):
                if act not in found:
                    found.append(act)
    return found


def all_fcurves(action):
    if hasattr(action, "fcurves") and not hasattr(action, "layers"):
        return list(action.fcurves)
    return [fc for layer in action.layers for strip in layer.strips for bag in strip.channelbags for fc in bag.fcurves]


def shift_and_cut(actions, offset, keep_from=None, keep_to=None):
    """Move every key `offset` frames earlier, then drop the keys outside keep_from..keep_to. Returns (moved, cut)."""
    moved = cut = 0
    for act in actions:
        for fc in all_fcurves(act):
            if offset:
                for kp in fc.keyframe_points:
                    kp.co.x -= offset
                    kp.handle_left.x -= offset
                    kp.handle_right.x -= offset
                    moved += 1
            for kp in reversed(list(fc.keyframe_points)):
                if (keep_from is not None and kp.co.x < keep_from - 1e-6) or (keep_to is not None and kp.co.x > keep_to + 1e-6):
                    fc.keyframe_points.remove(kp)
                    cut += 1
            fc.update()
    return moved, cut


def set_ranges(actions, start, end):
    """Give the actions (and the animation-layer strips that play them) the new frame range."""
    for act in actions:
        try:
            act.use_frame_range = True
            act.frame_start, act.frame_end = start, max(end, start + 1)
        except (AttributeError, TypeError, ValueError):
            pass
    for obj in bpy.context.scene.objects:
        ad = obj.animation_data
        for track in (ad.nla_tracks if ad else []):
            for strip in track.strips:
                if strip.action in actions:
                    try:
                        strip.action_frame_end = max(end, start + 1)
                        strip.action_frame_start = start
                        strip.frame_end = strip.frame_start + (strip.action_frame_end - strip.action_frame_start)
                    except (AttributeError, TypeError, ValueError, RuntimeError):
                        pass


def layer_count(obj):
    return len(obj.Anim_Layers) if hasattr(obj, "Anim_Layers") and hasattr(obj, "als") and len(obj.Anim_Layers) else 1


def use_layer(obj, index):
    """Make an Animation Layers layer the one that is keyed (no-op without the add-on)."""
    if layer_count(obj) > 1:
        obj.als.layer_index = index


def into_pose_mode(obj):
    if bpy.context.object and bpy.context.object.mode != "OBJECT" and bpy.context.object is not obj:
        bpy.ops.object.mode_set(mode="OBJECT")
    for o in bpy.context.view_layer.objects:
        o.select_set(False)
    obj.hide_set(False)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    if obj.mode != "POSE":
        bpy.ops.object.mode_set(mode="POSE")


def capture(obj, names):
    pbs = obj.pose.bones
    return {n: (pbs[n].location.copy(), pbs[n].rotation_quaternion.copy(), pbs[n].rotation_euler.copy(),
                tuple(pbs[n].rotation_axis_angle), pbs[n].scale.copy()) for n in names if n in pbs}


def apply(obj, pose):
    for n, (loc, quat, euler, axis_angle, scale) in pose.items():
        pb = obj.pose.bones[n]
        pb.location, pb.rotation_quaternion, pb.rotation_euler, pb.rotation_axis_angle, pb.scale = loc, quat, euler, axis_angle, scale


def rest(obj, names, rotation_only=False):
    for n in names:
        pb = obj.pose.bones.get(n)
        if pb is None:
            continue
        pb.rotation_quaternion, pb.rotation_euler, pb.rotation_axis_angle = (1, 0, 0, 0), (0, 0, 0), (0, 0, 1, 0)
        if not rotation_only:
            pb.location, pb.scale = (0, 0, 0), (1, 1, 1)


def key(obj, names, frame):
    """Insert keys (location, rotation, scale) on these bones, on the layer being keyed."""
    for o in obj.pose.bones:
        sel = o.name in names
        if hasattr(o, "select"):
            o.select = sel
        else:
            o.bone.select = sel
    try:
        if bpy.ops.anim.keyframe_insert.poll():
            bpy.ops.anim.keyframe_insert()        # through Animation Layers, like the procedural fixes
            return
    except RuntimeError:
        pass
    for n in names:
        pb = obj.pose.bones.get(n)
        if pb is not None:
            for path in ("location", rotation_path(pb), "scale"):
                pb.keyframe_insert(path, frame=frame, group=n)


def take_stamp():
    """The take's name from the .blend, as the procedural fixes take it: 'SG ASL Hello 2026-10-12 Working.blend'
    -> 'SG ASL Hello 2026-10-12' (the last word dropped); 'Hello_tk1_5.blend' -> 'Hello_tk1_5'."""
    stem = os.path.splitext(bpy.path.basename(bpy.data.filepath))[0] if bpy.data.filepath else "Untitled"
    words = stem.split()
    return " ".join(words[:-1]) if len(words) > 1 else stem


def framelog(text):
    """Append to '<take> Framelog.txt' next to the .blend (as the procedural fixes write it)."""
    if not bpy.data.filepath:
        return None
    path = os.path.join(os.path.dirname(bpy.data.filepath), f"{take_stamp()} Framelog.txt")
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(text.rstrip() + "\n\n")
        return path
    except OSError:
        return None


class SGM_OT_mh_trim_start(bpy.types.Operator):
    """Cut off the start of the take: the chosen frame becomes frame 0 (the rest frame) and everything moves
    earlier, on every layer, the face and the shape keys alike. Frame 1 is then the neutral pose again,
    easing into the take by the Ease In frame (20 by default)"""
    bl_idname = "sgm.mh_trim_start"
    bl_label = "Trim Start"
    bl_options = {"REGISTER", "UNDO"}

    frame: bpy.props.IntProperty(name="Cut point", min=1, description="This frame becomes frame 0; the frames before it are dropped")
    ease: bpy.props.IntProperty(name="Ease In", default=EASE, min=1,
                                description="Frames over which the take eases out of the neutral pose on frame 1")

    @classmethod
    def poll(cls, context):
        return find_rig(context) is not None

    def invoke(self, context, event):
        self.frame = max(1, context.scene.frame_current)
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        rig, scene = find_rig(context), context.scene
        offset = self.frame
        old_start, old_end = scene.frame_start, scene.frame_end
        new_end = old_end - offset
        if new_end <= self.ease + 1:
            self.report({"ERROR"}, f"Cutting at {offset} leaves {new_end} frames; that's too short")
            return {"CANCELLED"}
        names, face = control_names(rig), face_board(rig)
        actions = scene_actions()
        moved, cut = shift_and_cut(actions, offset, keep_from=0)
        for marker in scene.timeline_markers:
            marker.frame -= offset
            marker.name = f"F_{marker.frame}"
        scene.frame_end = new_end
        scene.frame_preview_end = new_end
        set_ranges(actions, 0, new_end)

        into_pose_mode(rig)
        count = layer_count(rig)
        for index in range(count):                     # every layer: keep what is now on frame 1, rest on frame 0
            use_layer(rig, index)
            scene.frame_set(1)
            key(rig, names, 1)
            scene.frame_set(0)
            rest(rig, names)
            key(rig, names, 0)
        scene.frame_set(new_end)                       # the neutral pose the take ends on
        neutral = capture(rig, names)
        use_layer(rig, count - 1)                      # the top layer: ease from the neutral pose on frame 1
        scene.frame_set(self.ease)
        key(rig, names, self.ease)
        scene.frame_set(1)
        apply(rig, neutral)
        key(rig, names, 1)
        face_note = ""
        if face is not None:
            controls = face_controls(face)
            into_pose_mode(face)
            use_layer(face, layer_count(face) - 1)
            scene.frame_set(self.ease)
            key(face, controls, self.ease)
            scene.frame_set(1)
            rest(face, controls)
            key(face, controls, 1)
            into_pose_mode(rig)
            face_note = f"; face board at rest on frame 1"
        scene.frame_set(1)
        report = (f"Trim start: cut at frame {offset}. Range {old_start}-{old_end} -> {scene.frame_start}-{new_end} "
                  f"({new_end - scene.frame_start + 1} frames). {moved} keys moved, {cut} dropped, {len(actions)} actions, "
                  f"{count} layer(s){face_note}.")
        print(report)
        log = framelog(report)
        self.report({"INFO"}, report + (f" Logged in {os.path.basename(log)}." if log else ""))
        return {"FINISHED"}


class SGM_OT_mh_trim_end(bpy.types.Operator):
    """Cut off the end of the take: the chosen frame becomes the last one, on every layer, the face and the
    shape keys alike. The take eases into the neutral pose over the last Ease Out frames (20 by default)"""
    bl_idname = "sgm.mh_trim_end"
    bl_label = "Trim End"
    bl_options = {"REGISTER", "UNDO"}

    frame: bpy.props.IntProperty(name="New last frame", min=2)
    ease: bpy.props.IntProperty(name="Ease Out", default=EASE, min=1,
                                description="Frames over which the take eases into the neutral pose at the end")

    @classmethod
    def poll(cls, context):
        return find_rig(context) is not None

    def invoke(self, context, event):
        self.frame = max(2, context.scene.frame_current)
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        rig, scene = find_rig(context), context.scene
        end, old_end = self.frame, scene.frame_end
        if end >= old_end:
            self.report({"ERROR"}, f"Frame {end} isn't before the current end ({old_end})")
            return {"CANCELLED"}
        if end - self.ease <= 1:
            self.report({"ERROR"}, f"Ending at {end} leaves too short a take")
            return {"CANCELLED"}
        names, face = control_names(rig), face_board(rig)
        scene.frame_set(old_end)
        neutral = capture(rig, names)                  # the neutral pose the take ends on now
        into_pose_mode(rig)
        count = layer_count(rig)
        use_layer(rig, count - 1)
        scene.frame_set(end - self.ease)                    # anchor: the take stays as it is up to here
        key(rig, names, end - self.ease)
        if face is not None:
            controls = face_controls(face)
            into_pose_mode(face)
            use_layer(face, layer_count(face) - 1)
            key(face, controls, end - self.ease)
            into_pose_mode(rig)
        actions = scene_actions()
        _, cut = shift_and_cut(actions, 0, keep_to=end)
        scene.frame_end = end
        scene.frame_preview_end = end
        set_ranges(actions, 0, end)
        use_layer(rig, count - 1)
        scene.frame_set(end)
        apply(rig, neutral)
        key(rig, names, end)
        if face is not None:
            into_pose_mode(face)
            use_layer(face, layer_count(face) - 1)
            rest(face, controls)
            key(face, controls, end)
            into_pose_mode(rig)
        report = (f"Trim end: range {scene.frame_start}-{old_end} -> {scene.frame_start}-{end} "
                  f"({end - old_end:+d} frames). {cut} keys dropped; eases into the neutral pose from frame {end - self.ease}.")
        print(report)
        log = framelog(report)
        self.report({"INFO"}, report + (f" Logged in {os.path.basename(log)}." if log else ""))
        return {"FINISHED"}


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


class SGM_OT_mh_fix_flips(bpy.types.Operator):
    """Fingers or limbs that spin round in a frame or two: a key stored as -q between keys stored as q (the same
    rotation) makes Blender turn the long way round between them. This turns those keys back, on every animation layer
    of the rig, so it turns the short way. The pose on every key stays exactly as it is"""
    bl_idname = "sgm.mh_fix_flips"
    bl_label = "Fix Flips"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        rig = find_rig(context)
        if rig is None:
            self.report({"ERROR"}, "No Metahuman rig in this scene")
            return {"CANCELLED"}
        flipped, bones = fix_quaternion_flips(object_actions(rig))
        if not flipped:
            self.report({"INFO"}, "No flips: every rotation key is on the same side as its neighbours")
            return {"FINISHED"}
        framelog(f"Fix Flips: {flipped} key(s) turned back on {', '.join(bones)}")
        self.report({"INFO"}, f"Fixed {flipped} flip(s) on {len(bones)} bone(s): {', '.join(bones[:6])}"
                              + (" ..." if len(bones) > 6 else ""))
        print(f"[fix flips] {flipped} key(s) turned back on {', '.join(bones)}")
        return {"FINISHED"}


class SGM_OT_mh_rest_hand(bpy.types.Operator):
    """Hold one hand (and its fingers) in its frame-1 pose over a range of frames, for a one-handed sign.
    Keys go on the layer being keyed (the Working layer after the procedural fixes)"""
    bl_idname = "sgm.mh_rest_hand"
    bl_label = "Rest Hand"
    bl_options = {"REGISTER", "UNDO"}

    side: bpy.props.EnumProperty(name="Hand", items=[("L", "Left", ""), ("R", "Right", "")], default="L")
    start_frame: bpy.props.IntProperty(name="Start Frame", default=1)
    end_frame: bpy.props.IntProperty(name="End Frame", default=100)
    step: bpy.props.IntProperty(name="Step Interval", default=10, min=1)
    ease: bpy.props.IntProperty(name="Ease", default=15, min=1, description="Frames to ease into and out of the held hand")
    fingers: bpy.props.EnumProperty(name="Fingers", items=[
        ("FRAME1", "Frame-1 pose", "Hold the fingers as on frame 1 (the neutral pose)"),
        ("REST", "Rest pose", "Clear the finger rotations (the rig's rest pose)"),
        ("LEAVE", "Leave", "Don't touch the fingers")], default="FRAME1")

    @classmethod
    def poll(cls, context):
        return find_rig(context) is not None

    def invoke(self, context, event):
        self.start_frame, self.end_frame = context.scene.frame_start, context.scene.frame_end
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        rig, scene = find_rig(context), context.scene
        side = self.side
        hand = [l["control"] for l in limbs(rig) if l["kind"] == "arm" and l["side"] == side]
        fingers = [b.name for b in rig.data.bones if b.get("sgm_control") and b.name.endswith("_" + side)
                   and b.name.startswith(FINGERS)]
        lo, hi = sorted((self.start_frame, self.end_frame))
        if hi - lo <= 2 * self.ease:
            self.report({"ERROR"}, "The range is shorter than the ease in and out")
            return {"CANCELLED"}
        into_pose_mode(rig)
        scene.frame_set(1)
        held = capture(rig, hand + (fingers if self.fingers == "FRAME1" else []))
        after = None
        if hi + self.ease <= scene.frame_end:          # how the hand moves where the hold lets go again
            scene.frame_set(hi + self.ease)
            after = capture(rig, hand + fingers)
        names = hand + (fingers if self.fingers != "LEAVE" else [])
        scene.frame_set(lo)
        key(rig, names, lo)                            # anchor: the take as it is before the hold
        frames = list(range(lo + self.ease, hi + 1, self.step))
        if frames and frames[-1] != hi:
            frames.append(hi)
        for f in frames:
            scene.frame_set(f)
            apply(rig, {n: v for n, v in held.items() if n in hand or self.fingers == "FRAME1"})
            if self.fingers == "REST":
                rest(rig, fingers, rotation_only=True)
            key(rig, names, f)
        if after is not None:
            scene.frame_set(hi + self.ease)
            apply(rig, {n: v for n, v in after.items() if n in names})
            key(rig, names, hi + self.ease)
        scene.frame_set(lo)
        self.report({"INFO"}, f"{'Left' if side == 'L' else 'Right'} hand held from frame {lo + self.ease} to {hi} "
                              f"({len(frames)} keys), fingers: {self.fingers.lower()}")
        return {"FINISHED"}


# --- rename the take: "<product line> <language> <name> <date> Unedited.blend" in a folder of that name ---
def today_stamp():
    now = datetime.datetime.now()
    return f"{now.year}-{now.month}-{now.day}"         # as the original Rename File script wrote dates


def guess_name():
    """The sign's name: from 'SG ASL <name> <date> <stage>.blend', else from the take ('Hello_tk1_5' -> 'Hello')."""
    stem = os.path.splitext(bpy.path.basename(bpy.data.filepath))[0] if bpy.data.filepath else ""
    words = stem.split()
    if len(words) >= 4:
        return " ".join(words[2:-2] if len(words) >= 5 else words[2:-1])
    base = re.sub(r"(_tk\d+)?(_\d+)?$", "", words[0] if words else "", flags=re.I)
    return base.replace("_", " ").strip()


class SGM_OT_mh_rename_take(bpy.types.Operator):
    """Save the take as '<Product Line> <Language> <Name> <Date> Unedited.blend' in a new folder of that name,
    next to the take's folder; actions named after the old file are renamed to match"""
    bl_idname = "sgm.mh_rename_take"
    bl_label = "Rename File"
    bl_options = {"REGISTER"}

    product: bpy.props.StringProperty(name="Product Line", default="SG", maxlen=255)
    language: bpy.props.StringProperty(name="Language", default="ASL", maxlen=255)
    # (not "name": every operator already has a read-only "name", and the two get mixed up)
    sign_name: bpy.props.StringProperty(name="Name", default="", maxlen=255)
    date: bpy.props.StringProperty(name="Date", default="", maxlen=32)
    stage: bpy.props.StringProperty(name="Ends with", default="Unedited", maxlen=64,
                                    description="Last word of the new file name")
    bring_files: bpy.props.BoolProperty(name="Copy the take's files", default=True,
                                        description="Copy the take's other files (video, FBX, JSON, logs) into the new "
                                                    "folder, so the next steps (Transfer to Galtis) find them there")

    @classmethod
    def poll(cls, context):
        return bool(bpy.data.filepath)

    def invoke(self, context, event):
        self.sign_name = guess_name()
        self.date = today_stamp()
        return context.window_manager.invoke_props_dialog(self, width=350)

    def execute(self, context):
        parts = [p.strip() for p in (self.product, self.language, self.sign_name, self.date) if p.strip()]
        if not self.sign_name.strip():
            self.report({"ERROR"}, "Give the sign a Name")
            return {"CANCELLED"}
        new_stamp = " ".join(parts)
        if any(c in new_stamp for c in '<>:"/\\|?*'):
            self.report({"ERROR"}, 'The name can\'t contain any of < > : " / \\ | ? *')
            return {"CANCELLED"}
        old_path = bpy.path.abspath(bpy.data.filepath)
        old_dir = os.path.dirname(old_path)
        old_stamp = take_stamp()
        new_dir = os.path.join(os.path.dirname(old_dir), new_stamp)
        new_path = os.path.join(new_dir, f"{new_stamp} {self.stage.strip()}.blend" if self.stage.strip() else f"{new_stamp}.blend")
        if os.path.abspath(new_path) == os.path.abspath(old_path):
            self.report({"ERROR"}, "That is this file's name already")
            return {"CANCELLED"}
        os.makedirs(new_dir, exist_ok=True)
        renamed = 0
        for action in bpy.data.actions:
            if old_stamp and old_stamp in action.name:
                action.name = action.name.replace(old_stamp, new_stamp)
                renamed += 1
        copied = 0
        if self.bring_files and os.path.abspath(new_dir) != os.path.abspath(old_dir):
            for f in sorted(os.listdir(old_dir)):
                src = os.path.join(old_dir, f)
                if os.path.isfile(src) and not re.search(r"\.blend\d*$", f, re.I) and not os.path.exists(os.path.join(new_dir, f)):
                    shutil.copy2(src, os.path.join(new_dir, f))
                    copied += 1
        bpy.ops.wm.save_as_mainfile(filepath=new_path, relative_remap=False)
        log = framelog(f"Renamed: {os.path.basename(old_path)} -> {os.path.basename(new_path)}")
        self.report({"INFO"}, f"Saved in the folder '{new_stamp}' as {os.path.basename(new_path)}" + (f"; {renamed} actions renamed" if renamed else "")
                              + (f"; {copied} take files copied" if copied else ""))
        print(f"[rename] {old_path} -> {new_path}; {renamed} actions renamed; {copied} files copied")
        return {"FINISHED"}


# --- Import Video: the take's video beside the character, for reference ---
VIDEO_OBJECT = "SGM Reference Video"
VIDEO_EXTS = (".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v")
TIMELINE_SPAN = 1000000   # the plane's frame duration: far beyond any take, so Blender never stops counting frames


def _loose(text):
    return "".join(c for c in text.lower() if c.isalnum())


def take_videos():
    """Videos in the take's folder, the one named most like the take first."""
    if not bpy.data.filepath:
        return []
    folder = os.path.dirname(bpy.path.abspath(bpy.data.filepath))
    found = [os.path.join(folder, f) for f in sorted(os.listdir(folder)) if f.lower().endswith(VIDEO_EXTS)]
    stamp = _loose(re.sub(r"(_tk\d+)$", "", take_stamp(), flags=re.I))
    found.sort(key=lambda p: (0 if stamp and stamp in _loose(os.path.basename(p)) else 1, os.path.basename(p)))
    return found


def set_video_driver(obj):
    """Drive the plane's image frame from the timeline: frame `start` shows the video's first frame, and the
    video runs at its own speed (rate = video fps / scene fps)."""
    path = "image_user.frame_offset"
    try:
        obj.driver_remove(path)
    except (TypeError, RuntimeError):
        pass
    fcurve = obj.driver_add(path)
    driver = fcurve.driver
    driver.type = "SCRIPTED"
    for name, prop in (("start", "sgm_video_start"), ("rate", "sgm_video_rate"), ("frames", "sgm_video_frames")):
        var = driver.variables.new()
        var.name, var.type = name, "SINGLE_PROP"
        var.targets[0].id_type = "OBJECT"
        var.targets[0].id = obj
        var.targets[0].data_path = f'["{prop}"]'
    # Blender shows image frame min(max(frame - frame_start + 1, 0), frame_duration) + offset (the offset is added
    # after that clamp). frame_start is 1 and frame_duration far beyond the take, so the clamp is max(frame, 0);
    # wanted: the video's frame round((frame - start) * rate) + 1, held at its first and last frame outside it.
    # (A simple expression - min, max, floor - so it runs without Auto Run Python Scripts.)
    driver.expression = "min(max(floor((frame - start) * rate + 0.5) + 1, 1), frames) - max(frame, 0)"
    for modifier in list(fcurve.modifiers):
        fcurve.modifiers.remove(modifier)


VIDEO_PLACES = [("LEFT", "Left", "Beside the character, on the left as seen in the Front view"),
                ("RIGHT", "Right", "Beside the character, on the right as seen in the Front view"),
                ("BEHIND", "Behind", "Behind the character, centred on it (the default)")]


def place_video(obj, context):
    """Stand the video plane on the floor where the Video setting says: left, right or behind the character,
    as tall as it, facing the Front view."""
    rig = find_rig(context)
    tall = rig.dimensions.z if rig is not None and rig.dimensions.z > 0 else 2.0
    base = rig.matrix_world.translation if rig is not None else Vector((0.0, 0.0, 0.0))
    aspect = obj.get("sgm_video_aspect")
    if not aspect and isinstance(obj.data, bpy.types.Image) and obj.data.size[1]:   # a plane from an older version
        aspect = obj.data.size[0] / obj.data.size[1]
    aspect = float(aspect or 16 / 9)
    obj.empty_display_size = tall * max(aspect, 1.0)      # the size is the image's longer side
    wide = tall * aspect
    where = context.scene.sgm_mh_rig.video_place
    if where == "RIGHT":
        obj.location = (base.x + 0.6 * tall + wide / 2, base.y, base.z + tall / 2)
    elif where == "LEFT":
        obj.location = (base.x - 0.6 * tall - wide / 2, base.y, base.z + tall / 2)
    else:                                                  # BEHIND, the default
        obj.location = (base.x, base.y + 0.6 * tall, base.z + tall / 2)
    obj.rotation_euler = (math.pi / 2, 0.0, 0.0)


def video_place_changed(settings, context):
    obj = bpy.data.objects.get(VIDEO_OBJECT)
    if obj is not None and obj.get("sgm_video_path"):
        place_video(obj, context)


class SGM_OT_mh_import_video(bpy.types.Operator):
    """Put the take's video on a plane beside the character, playing in step with the timeline.
    Uses the video in the take's folder; without one (or with several), a file browser opens"""
    bl_idname = "sgm.mh_import_video"
    bl_label = "Import Video"
    bl_options = {"REGISTER", "UNDO"}

    filepath: bpy.props.StringProperty(subtype="FILE_PATH")
    filter_movie: bpy.props.BoolProperty(default=True, options={"HIDDEN"})
    filter_folder: bpy.props.BoolProperty(default=True, options={"HIDDEN"})
    start_frame: bpy.props.IntProperty(name="Video starts at frame", default=1,
                                       description="The timeline frame that shows the video's first frame (the take "
                                                   "import starts the take on frame 1)")
    choose: bpy.props.BoolProperty(name="Choose the video", default=False, options={"SKIP_SAVE"},
                                   description="Open a file browser even when the take's folder has a video")

    def invoke(self, context, event):
        videos = take_videos()
        if videos and not self.choose and not event.shift:
            if len(videos) > 1:
                print(f"[video] {len(videos)} videos in the take's folder; using {os.path.basename(videos[0])} "
                      "(Shift-click Import Video to choose another)")
            self.filepath = videos[0]
            return self.execute(context)
        if bpy.data.filepath:
            self.filepath = os.path.dirname(bpy.path.abspath(bpy.data.filepath)) + os.sep
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        path = bpy.path.abspath(self.filepath)
        if not os.path.isfile(path) or not path.lower().endswith(VIDEO_EXTS):
            self.report({"ERROR"}, f"Not a video file: {path or '(none chosen)'}")
            return {"CANCELLED"}
        scene = context.scene
        clip = bpy.data.movieclips.load(path)           # the video's own rate, length and size
        fps, frames, (width, height) = clip.fps, clip.frame_duration, tuple(clip.size)
        bpy.data.movieclips.remove(clip)
        if not fps or not frames:
            self.report({"ERROR"}, f"Blender couldn't read {os.path.basename(path)} as a video")
            return {"CANCELLED"}
        image = bpy.data.images.load(path, check_existing=True)
        image.source = "MOVIE"

        obj = bpy.data.objects.get(VIDEO_OBJECT)
        if obj is None or obj.type != "EMPTY":
            obj = bpy.data.objects.new(VIDEO_OBJECT, None)
            scene.collection.objects.link(obj)
        elif obj.name not in scene.objects:
            scene.collection.objects.link(obj)
        old = obj.data if isinstance(obj.data, bpy.types.Image) else None
        obj.empty_display_type = "IMAGE"
        obj.data = image
        if old is not None and old is not image and old.users == 0:
            bpy.data.images.remove(old)
        obj.use_empty_image_alpha = False
        obj.empty_image_depth = "DEFAULT"
        obj.hide_render = True
        user = obj.image_user
        user.frame_start = 1
        user.frame_duration = TIMELINE_SPAN     # counts timeline frames; the driver maps them to the video's
        user.use_auto_refresh = True
        user.use_cyclic = False

        # as tall as the character, standing on the floor to its left, to its right or behind it (the Video setting)
        obj["sgm_video_aspect"] = (width / height) if width and height else 16 / 9
        place_video(obj, context)

        scene_fps = scene.render.fps / scene.render.fps_base
        obj["sgm_video_start"] = int(self.start_frame)
        obj["sgm_video_rate"] = fps / scene_fps
        obj["sgm_video_frames"] = int(frames)
        obj["sgm_video_path"] = path
        set_video_driver(obj)
        context.view_layer.update()

        last = self.start_frame + round((frames - 1) / obj["sgm_video_rate"])
        framelog(f"Video: {os.path.basename(path)} beside the character ({fps:.3f} fps, {frames} frames; "
                 f"frames {self.start_frame}-{last} of the timeline)")
        self.report({"INFO"}, f"{os.path.basename(path)}: {fps:g} fps, {frames} frames, on the timeline from frame "
                              f"{self.start_frame} to {last}")
        print(f"[video] {path} -> '{obj.name}': {fps:.3f} fps (scene {scene_fps:g}), {frames} frames, "
              f"{width}x{height}; timeline frames {self.start_frame}-{last}")
        return {"FINISHED"}


class SGM_OT_mh_remove_video(bpy.types.Operator):
    """Remove the reference video plane from the file (the video file itself is left alone)"""
    bl_idname = "sgm.mh_remove_video"
    bl_label = "Remove Video"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        obj = bpy.data.objects.get(VIDEO_OBJECT)
        if obj is None:
            return {"CANCELLED"}
        image = obj.data if isinstance(obj.data, bpy.types.Image) else None
        bpy.data.objects.remove(obj)
        if image is not None and image.users == 0:
            bpy.data.images.remove(image)
        self.report({"INFO"}, "Reference video removed")
        return {"FINISHED"}


# --- Pose Magic: a pose asset (and JSON / CSV / TXT) for every timeline marker; runs pose_magic_lib.py ---
POSE_MAGIC_FILE = "pose_magic_lib.py"
POSE_MAGIC_FOLDERS = []   # more folders to look in, after the workspace's Scripts folder (paths.scripts in
                          # pipeline_config.json) and <folder above the take's folder>\Scripts (and one more up)
_library_items = []


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


def pose_magic_module():
    """pose_magic_lib.py, loaded fresh from the first folder that has it. (module, where it was looked for)"""
    import importlib.util
    folders = [f for f in [workspace_path("scripts")] if f]
    if bpy.data.filepath:
        here = os.path.dirname(bpy.path.abspath(bpy.data.filepath))
        folders += [os.path.join(os.path.dirname(here), "Scripts"), os.path.join(os.path.dirname(os.path.dirname(here)), "Scripts")]
    folders += POSE_MAGIC_FOLDERS
    for folder in folders:
        path = os.path.join(folder, POSE_MAGIC_FILE)
        if os.path.isfile(path):
            spec = importlib.util.spec_from_file_location("pose_magic_lib", path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            module.__sgm_path__ = path
            return module, folders
    return None, folders


def asset_libraries(self, context):
    _library_items.clear()
    _library_items.append(("LOCAL", "Current File", "Keep the pose assets in this .blend"))
    for lib in context.preferences.filepaths.asset_libraries:
        _library_items.append((lib.name, lib.name, lib.path))
    return _library_items


class SGM_OT_mh_pose_magic(bpy.types.Operator):
    """A pose asset, and JSON / CSV / TXT files, for every timeline marker: the pose of the rig on that marker's frame.
    Arms and legs on IK are read as the IK shows them"""
    bl_idname = "sgm.mh_pose_magic"
    bl_label = "Pose Magic"
    bl_options = {"REGISTER", "UNDO"}

    max_poses: bpy.props.IntProperty(name="Max poses", default=50, min=1)
    export_json: bpy.props.BoolProperty(name="JSON (Pose Manager)", default=True)
    export_csv: bpy.props.BoolProperty(name="CSV", default=True)
    export_txt: bpy.props.BoolProperty(name="TXT", default=True)
    library: bpy.props.EnumProperty(name="Asset library", items=asset_libraries,
                                    description="Where the pose assets go. Keep MetaHuman poses out of the Galtis "
                                                "library: they are for this rig")
    pose_prefix: bpy.props.StringProperty(name="Pose name", default="",
                                          description="The poses are called '<Pose name> P1', 'P2' ... "
                                                      "(filled in with the .blend's name, as Pose Magic always named them)")
    new_library: bpy.props.BoolProperty(name="Create a new asset library", default=False,
                                        description="Add a new asset library to Blender's Preferences and put the poses in it")
    new_library_name: bpy.props.StringProperty(name="Name", default="MetaHuman Poses")
    new_library_folder: bpy.props.StringProperty(name="Folder", default="", subtype="DIR_PATH",
                                                 description="Where the library keeps its files (made if it doesn't exist)")
    save_preferences: bpy.props.BoolProperty(name="Keep it (save Preferences)", default=True,
                                             description="Save Blender's Preferences, so the library is still there "
                                                         "next time Blender starts")
    thumbnail: bpy.props.EnumProperty(
        name="Thumbnails",
        items=[("WHOLE_BODY", "Whole body", "Pose Magic's camera, in front of the character, framed so the whole "
                                            "body fits in every marked pose"),
               ("UPPER_BODY", "Upper body", "Pose Magic's camera, framed from the hips up (bigger hands)"),
               ("SCENE", "Scene camera", "The scene's own camera, as it is")],
        default="WHOLE_BODY",
        description="Blender draws each pose's thumbnail through the scene's camera")
    face_poses: bpy.props.BoolProperty(
        name="Face board poses", default=True,
        description="Also make a pose of the face board on each marker ('<Pose name> Face P1' ...), in the same "
                    "library, with a picture of the face as its thumbnail")
    face_view: bpy.props.EnumProperty(
        name="Face thumbnail",
        items=[("FRONT", "Front of the face", "The camera in front of the face"),
               ("LEFT", "Left side of the face", "The camera at the face's left side (the character's left)"),
               ("RIGHT", "Right side of the face", "The camera at the face's right side (the character's right)")],
        default="FRONT")
    thumbnail_isolate: bpy.props.BoolProperty(
        name="Only the character", default=True,
        description="Hide everything else (face board, props) while the thumbnails are drawn; it comes back after")

    @classmethod
    def poll(cls, context):
        return find_rig(context) is not None and bool(bpy.data.filepath)

    def invoke(self, context, event):
        if not context.scene.timeline_markers:
            self.report({"ERROR"}, "Put a timeline marker (M) on each pose first")
            return {"CANCELLED"}
        stem = os.path.splitext(bpy.path.basename(bpy.data.filepath))[0]
        words = stem.split()
        self.pose_prefix = " ".join(words[:-1]) if len(words) > 1 else stem
        if not self.new_library_folder:
            workspace = os.path.dirname(os.path.dirname(bpy.path.abspath(bpy.data.filepath)))
            self.new_library_folder = os.path.join(workspace, "Pose Libraries", self.new_library_name)
        return context.window_manager.invoke_props_dialog(self, width=420)

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "pose_prefix")
        layout.label(text=f"Poses: '{self.pose_prefix.strip() or '?'} P1', 'P2' ... "
                          f"({len(context.scene.timeline_markers)} markers)")
        if self.face_poses:
            layout.label(text=f"Face poses: '{self.pose_prefix.strip() or '?'} Face P1', 'Face P2' ...")
        layout.separator()
        layout.prop(self, "max_poses")
        row = layout.row(align=True)
        row.prop(self, "export_json", toggle=True)
        row.prop(self, "export_csv", toggle=True)
        row.prop(self, "export_txt", toggle=True)
        sub = layout.column()
        sub.enabled = not self.new_library
        sub.prop(self, "library")
        row = layout.row(align=True)
        row.prop(self, "thumbnail")
        row.prop(self, "thumbnail_isolate", toggle=True)
        row = layout.row(align=True)
        row.prop(self, "face_poses", toggle=True)
        sub = row.row(align=True)
        sub.enabled = self.face_poses
        sub.prop(self, "face_view", text="")
        layout.separator()
        box = layout.box()
        box.prop(self, "new_library")
        col = box.column()
        col.enabled = self.new_library
        col.prop(self, "new_library_name")
        col.prop(self, "new_library_folder")
        col.prop(self, "save_preferences")

    def make_library(self, context):
        """Add the new asset library to the Preferences (or reuse one of that name). Returns its name or None."""
        name = self.new_library_name.strip()
        folder = bpy.path.abspath(self.new_library_folder.strip())
        if not name or not folder:
            self.report({"ERROR"}, "Give the new asset library a name and a folder")
            return None
        libraries = context.preferences.filepaths.asset_libraries
        existing = libraries.get(name)
        if existing is not None:
            if os.path.normcase(os.path.abspath(bpy.path.abspath(existing.path))) != os.path.normcase(os.path.abspath(folder)):
                self.report({"ERROR"}, f"There is already an asset library called '{name}' (in {existing.path}); "
                                       "pick it in the list, or give the new one another name")
                return None
            return name
        os.makedirs(folder, exist_ok=True)
        before = set(libraries)
        bpy.ops.preferences.asset_library_add(directory=folder)
        added = [lib for lib in libraries if lib not in before]
        if not added:
            self.report({"ERROR"}, f"Blender didn't add the asset library for {folder}")
            return None
        added[0].name = name
        if self.save_preferences:
            try:
                bpy.ops.wm.save_userpref()
            except RuntimeError as e:
                self.report({"WARNING"}, f"Library added, but the Preferences weren't saved: {e}")
        print(f"[pose magic] new asset library '{name}': {folder}")
        return name

    def execute(self, context):
        module, folders = pose_magic_module()
        if module is None:
            self.report({"ERROR"}, f"{POSE_MAGIC_FILE} not found; put it in one of: " + "; ".join(folders))
            return {"CANCELLED"}
        if getattr(module, "LIB_VERSION", 1) < 2:
            self.report({"ERROR"}, f"{module.__sgm_path__} is an older Pose Magic (it reads the MetaHuman's arms wrongly "
                                   "on IK, or can't take a pose name). Replace it with the new pose_magic_lib.py and run again")
            return {"CANCELLED"}
        library = self.library
        if self.new_library:
            library = self.make_library(context)
            if library is None:
                return {"CANCELLED"}
        rig = find_rig(context)
        into_pose_mode(rig)
        start = context.scene.frame_current
        extra = {}
        version = getattr(module, "LIB_VERSION", 1)
        if version >= 3:
            extra = {"thumbnail": self.thumbnail, "thumbnail_isolate": self.thumbnail_isolate}
        if version >= 4:
            extra.update(face_poses=self.face_poses, face_view=self.face_view)
        if version < 4 and (self.face_poses or self.thumbnail != "SCENE"):
            self.report({"WARNING"}, f"{module.__sgm_path__} is older than this panel: no face board poses"
                                     + ("" if version >= 3 else " or whole-body thumbnails")
                                     + ". Replace it with the new pose_magic_lib.py")
        done, tried = module.run_pose_magic(armature_name=rig.name, max_poses=self.max_poses,
                                            export_json=self.export_json, export_csv=self.export_csv,
                                            export_txt=self.export_txt, pose_prefix=self.pose_prefix.strip() or None,
                                            asset_library_reference=library, **extra) or (0, 0)
        context.scene.frame_set(start)
        log = framelog(f"Pose Magic: {done} of {tried} markers made into poses (library {library})")
        self.report({"INFO"} if done == tried else {"WARNING"},
                    f"Pose Magic: {done} of {tried} poses ({os.path.basename(os.path.dirname(module.__sgm_path__))}\\"
                    f"{POSE_MAGIC_FILE})" + ("" if done == tried else " - see the console"))
        return {"FINISHED"}


class SGM_OT_clear_console(bpy.types.Operator):
    """Clear the System Console (Window > Toggle System Console), so the next script's output stands alone"""
    bl_idname = "sgm.clear_console"
    bl_label = "Clear Console"

    def execute(self, context):
        os.system("cls" if os.name == "nt" else "clear")
        return {"FINISHED"}

# --- operators ---
class SGM_OT_mh_match(bpy.types.Operator):
    """Match one limb (or all) between FK and IK, on the frames chosen below"""
    bl_idname = "sgm.mh_match"
    bl_label = "Match"
    bl_options = {"REGISTER", "UNDO"}

    direction: bpy.props.EnumProperty(items=[("IK_TO_FK", "IK to FK", "Put the IK controls on the FK pose"),
                                             ("FK_TO_IK", "FK to IK", "Turn the FK bones to the IK pose")])
    limb: bpy.props.StringProperty(default="")      # its switch name, e.g. ArmIKFK_L; "" = every limb

    @classmethod
    def description(cls, context, props):
        what = "every limb" if not props.limb else props.limb
        return (f"Put the IK hand / foot and elbow / knee target of {what} on the FK pose" if props.direction == "IK_TO_FK"
                else f"Turn the FK bones of {what} to the IK pose")

    @classmethod
    def poll(cls, context):
        return find_rig(context) is not None

    def execute(self, context):
        rig = find_rig(context)
        s = context.scene.sgm_mh_rig
        chosen = [l for l in limbs(rig) if not self.limb or l["switch"] == self.limb]
        if not chosen:
            self.report({"ERROR"}, "No such limb on the rig")
            return {"CANCELLED"}
        scene, start = context.scene, context.scene.frame_current
        frames = frames_to_do(context)
        key = s.insert_keys or len(frames) > 1          # a range is always keyed, else only the last frame would stay
        for frame in frames:
            if frames != [start]:
                scene.frame_set(frame)          # (not on the current frame: that would drop pose changes not keyed yet)
            for limb in chosen:
                if self.direction == "IK_TO_FK":
                    match_ik_to_fk(rig, limb, key, frame)
                else:
                    match_fk_to_ik(rig, limb, key, frame)
            context.view_layer.update()
        if s.switch_after:
            value = 1.0 if self.direction == "IK_TO_FK" else 0.0
            for limb in chosen:
                set_switch(rig, limb, value, s.key_switch, start if len(frames) == 1 else frames[0])
        if frames != [start]:
            scene.frame_set(start)
        rig.update_tag()
        names = ", ".join(limb_label(l) for l in chosen)
        self.report({"INFO"}, f"{names}: {'IK matched to FK' if self.direction == 'IK_TO_FK' else 'FK matched to IK'} "
                              f"on {len(frames)} frame{'s' if len(frames) != 1 else ''}"
                              + (f", switched to {'IK' if self.direction == 'IK_TO_FK' else 'FK'}" if s.switch_after else ""))
        return {"FINISHED"}


class SGM_OT_mh_switch_all(bpy.types.Operator):
    """Set every limb's switch"""
    bl_idname = "sgm.mh_switch_all"
    bl_label = "Switch all"
    bl_options = {"REGISTER", "UNDO"}

    value: bpy.props.FloatProperty(min=0.0, max=1.0)

    @classmethod
    def poll(cls, context):
        return find_rig(context) is not None

    def execute(self, context):
        rig = find_rig(context)
        s = context.scene.sgm_mh_rig
        for limb in limbs(rig):
            set_switch(rig, limb, self.value, s.key_switch, context.scene.frame_current)
        rig.update_tag()
        return {"FINISHED"}


# --- settings and panel ---
class SGM_MHRigSettings(bpy.types.PropertyGroup):
    frames: bpy.props.EnumProperty(name="Frames", items=[
        ("CURRENT", "Current frame", "Only the frame you are on"),
        ("SCENE", "Scene range", "Every frame from the scene's start to its end"),
        ("CUSTOM", "Range", "Every frame between From and To")], default="CURRENT")
    frame_from: bpy.props.IntProperty(name="From", default=1)
    frame_to: bpy.props.IntProperty(name="To", default=100)
    insert_keys: bpy.props.BoolProperty(name="Insert keys", default=True,
                                        description="Key the matched controls (always on for a range of frames)")
    switch_after: bpy.props.BoolProperty(name="Switch after matching", default=True,
                                         description="After Match IK to FK, set the limb to IK; after Match FK to IK, set it to FK")
    mirror_selected: bpy.props.BoolProperty(name="Selected only", default=False,
                                            description="Left to Right / Right to Left mirror only the selected bones")
    key_switch: bpy.props.BoolProperty(name="Key the switch", default=False,
                                       description="Insert a key on the switch when it is changed from this panel")
    video_place: bpy.props.EnumProperty(name="Place", items=VIDEO_PLACES, default="BEHIND", update=video_place_changed,
                                        description="Where the reference video stands (changing it moves the video)")


class SGM_PT_mh_rig(bpy.types.Panel):
    bl_label = "Metahuman Rig"
    bl_idname = "SGM_PT_mh_rig"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = TAB

    def draw(self, context):
        layout = self.layout
        rig = find_rig(context)
        if rig is None:
            layout.label(text="No Metahuman rig in this scene", icon="INFO")
            return
        s = context.scene.sgm_mh_rig
        row = layout.row()
        row.label(text=rig.name, icon="ARMATURE_DATA")
        row.operator("sgm.clear_console", text="", icon="CONSOLE")
        sw = rig.pose.bones.get(SWITCH_BONE)

        box = layout.box()
        box.label(text="FK / IK  (0 = FK, 1 = IK)")
        for limb in limbs(rig):
            col = box.column(align=True)
            if sw is not None and limb["switch"] in sw.keys():
                col.prop(sw, f'["{limb["switch"]}"]', text=limb_label(limb), slider=True)
            row = col.row(align=True)
            op = row.operator("sgm.mh_match", text="Match IK to FK")
            op.direction, op.limb = "IK_TO_FK", limb["switch"]
            op = row.operator("sgm.mh_match", text="Match FK to IK")
            op.direction, op.limb = "FK_TO_IK", limb["switch"]
        col = box.column(align=True)
        col.label(text="All limbs")
        row = col.row(align=True)
        op = row.operator("sgm.mh_match", text="Match IK to FK")
        op.direction, op.limb = "IK_TO_FK", ""
        op = row.operator("sgm.mh_match", text="Match FK to IK")
        op.direction, op.limb = "FK_TO_IK", ""
        row = col.row(align=True)
        row.operator("sgm.mh_switch_all", text="All FK").value = 0.0
        row.operator("sgm.mh_switch_all", text="All IK").value = 1.0

        box = layout.box()
        box.label(text="Matching")
        box.prop(s, "frames", text="")
        if s.frames == "CUSTOM":
            row = box.row(align=True)
            row.prop(s, "frame_from")
            row.prop(s, "frame_to")
        box.prop(s, "insert_keys")
        box.prop(s, "switch_after")
        box.prop(s, "key_switch")

        box = layout.box()
        box.label(text="Mirror")
        row = box.row(align=True)
        row.operator("sgm.mh_copy_pose", text="Copy Pose", icon="COPYDOWN")
        row.operator("sgm.mh_paste_mirrored", text="Paste Mirrored", icon="PASTEFLIPDOWN")
        row = box.row(align=True)
        row.operator("sgm.mh_mirror_side", text="Left \u2192 Right").source = "L"
        row.operator("sgm.mh_mirror_side", text="Right \u2192 Left").source = "R"
        box.prop(s, "mirror_selected")
        if autokey(context):
            box.label(text="Auto-keying is on: mirrored bones are keyed", icon="REC")

        box = layout.box()
        box.label(text="Cleanup")
        row = box.row(align=True)
        row.operator("sgm.mh_trim_start", text="Trim Start", icon="TRIA_LEFT_BAR")
        row.operator("sgm.mh_trim_end", text="Trim End", icon="TRIA_RIGHT_BAR")
        row = box.row(align=True)
        row.operator("sgm.mh_rest_hand", text="Rest Hand", icon="HAND")
        row.operator("sgm.mh_fix_flips", text="Fix Flips", icon="MOD_SMOOTH")
        box.operator("sgm.mh_rename_take", text="Rename File", icon="FILE_TICK")
        box.operator("sgm.mh_pose_magic", text="Pose Magic", icon="ASSET_MANAGER")

        box = layout.box()
        box.label(text="Video")
        video = bpy.data.objects.get(VIDEO_OBJECT)
        box.row(align=True).prop(s, "video_place", expand=True)
        row = box.row(align=True)
        row.operator("sgm.mh_import_video", text="Import Video" if video is None else "Replace Video",
                     icon="FILE_MOVIE")
        row.operator("sgm.mh_import_video", text="", icon="FILEBROWSER").choose = True
        if video is not None:
            row.operator("sgm.mh_remove_video", text="", icon="X")
            row = box.row(align=True)
            row.prop(video, "hide_viewport", text="Show" if video.hide_viewport else "Hide", toggle=True,
                     invert_checkbox=True, icon="HIDE_ON" if video.hide_viewport else "HIDE_OFF")
            if "sgm_video_start" in video.keys():
                row.prop(video, '["sgm_video_start"]', text="Starts at")

        box = layout.box()
        box.label(text="Show")
        grid = box.grid_flow(columns=2, align=True)
        for name in COLLECTIONS:
            coll = rig.data.collections_all.get(name)
            if coll is not None:
                grid.prop(coll, "is_visible", text=name, toggle=True)


CLASSES = (SGM_MHRigSettings, SGM_OT_mh_match, SGM_OT_mh_switch_all, SGM_OT_mh_copy_pose, SGM_OT_mh_paste_mirrored,
           SGM_OT_mh_mirror_side, SGM_OT_mh_trim_start, SGM_OT_mh_trim_end, SGM_OT_mh_rest_hand,
           SGM_OT_mh_rename_take, SGM_OT_mh_pose_magic, SGM_OT_mh_import_video, SGM_OT_mh_remove_video,
           SGM_OT_mh_fix_flips,
           SGM_OT_clear_console, SGM_PT_mh_rig)


def register():
    for cls in CLASSES:
        old = getattr(bpy.types, cls.__name__, None)
        if old is not None:
            try:
                bpy.utils.unregister_class(old)
            except RuntimeError:
                pass
        bpy.utils.register_class(cls)
    bpy.types.Scene.sgm_mh_rig = bpy.props.PointerProperty(type=SGM_MHRigSettings)


def unregister():
    for cls in reversed(CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass


register()

# come back with the file: tick Text > Register on this script's text block
for _text in bpy.data.texts:
    if _text.name.startswith("metahuman_rig_panel") or (_text.filepath and _text.filepath.endswith("metahuman_rig_panel.py")):
        _text.use_module = True

# run from a file (Text > Open): bring the copy build_metahuman_rig.py put in this .blend up to date too, so the
# file opens with this version of the tab next time
for _opened in [t for t in bpy.data.texts if t.filepath and t.filepath.endswith("metahuman_rig_panel.py")]:
    try:
        with open(bpy.path.abspath(_opened.filepath), "r", encoding="utf-8") as _fh:
            _source = _fh.read()
    except OSError:
        continue
    for _text in bpy.data.texts:
        if not _text.filepath and _text.name.startswith("metahuman_rig_panel") and _text.as_string() != _source:
            _text.clear()
            _text.write(_source)
            print(f"[metahuman_rig_panel] updated the copy in this file ('{_text.name}'); save the file to keep it")
    break
