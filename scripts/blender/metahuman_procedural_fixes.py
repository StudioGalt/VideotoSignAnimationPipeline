import bpy
import math
import json
import re
import os
import time
from mathutils import Quaternion

#Procedural fixes on the MetaHuman rig (MH_rig, made by build_metahuman_rig.py)
#Necessary Addon Animation Layers by Tal
#
#Run it in a take's .blend (OUTPUT_ROOT\<take>\<take>.blend, filed by import_unreal_take.py), after the
#take has been keyed onto the MetaHuman rig. It does what the Galtis procedural edits did, on the
#MetaHuman rig's controls (named like the Galtis ones):
#  Layers   : Base_Layer (muted), Subtractive_Layer (a copy, REPLACE), Correction_Layer, Working_Layer
#  Rest     : the rest pose keyed on frame 0
#  Neutral  : the NEUTRAL pose keyed on frame 1 and the last frame; the take eases out of it by Ease In
#             and back into it from end - Ease Out (anchors on the Correction_Layer)
#  Smooth   : the captured hips and legs smoothed (Smooth Legs, in frames; 0 = off), which takes out the
#             frame-to-frame jitter of a video capture before anything else is done
#  Leg pin  : (on by default) hips and legs keyed to their frame-1 pose every Leg Pin frames (1, the default =
#             on every frame: the hips and legs hold their frame-1 pose for the whole take); off = the captured
#             hips and legs are kept (smoothed by Smooth Legs)
#  Torso    : chest, neck and head held to their frame-1 pose at Ease In, end - Ease Out and the end
#  IK snap  : on every frame (the last one included) the IK hands and feet are put on the FK ones and the
#             elbow and knee targets behind / in front of the FK elbows and knees, so the IK limbs pose
#             exactly like the FK limbs; then arms and legs are switched to IK
#  Feet     : optionally the IK feet hold their frame-1 position for the whole clip (stops foot sliding)
#  Face     : the face board rests on frame 1 and the last frame, easing into its animation
#  Frame 0  : last of all, every bone of the rig is cleared and keyed on frame 0 (on every layer), so frame 0
#             is the rest pose whatever the steps before keyed there
#  Saves    : "<take> Working.blend" and "<take> Framelog.txt" next to the take's .blend
#
#Running it opens a dialog for Ease In, Ease Out, Leg Pin and the options.
#
#The NEUTRAL pose comes from NEUTRAL_POSE_FILE: pose the MetaHuman rig once in its neutral pose and run
#save_neutral_pose.py. Without that file the rig's rest pose (the MetaHuman A-pose) is used.
#
#Not carried over from the Galtis script: the blinks (the face capture has real blinks), the
#elbow-target nudge and the rotation clear on the IK arm bones (the matched elbow targets replace both),
#and the shape key renaming (done later, on the avatar the take is put on).


# --- config ---
RIG_NAME = "MH_rig"         # "" = the armature made by build_metahuman_rig.py
NEUTRAL_POSE_FILE = None    # None = paths.neutral_pose in pipeline_config.json (<workspace>/MetaHuman/neutral_pose.json)
FACE_RIG = ""               # face board armature; "" = the one in this file (*_face_gui, *_face_board, or CTRL_ bones)
USE_ANIMATION_LAYERS = True  # False = key straight into the rig's action (no layers; for testing without the add-on)
SAVE_WORKING = True         # save as "<take> Working.blend" at the end
DEFAULTS = {"ease_in": 30, "ease_out": 30, "pin_legs": True, "leg_pin": 1, "smooth_legs": 4, "plant_feet": True, "match_poles": True,
            "ik_arms": True, "ik_legs": True, "face": True, "face_ease_in": 30, "face_ease_out": 30}

TORSO = ["chest_1", "chest_2", "chest_3", "chest_4", "chest_5", "neckfk_01", "neckfk", "headfk"]
LEGS = ["hip", "thighfk_L", "shinfk_L", "footfk_L", "toefk_L", "thighfk_R", "shinfk_R", "footfk_R", "toefk_R"]
SWITCH_BONE = "FKIK Switch"

_TIMINGS = []


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


def _stage(name):
    _TIMINGS.append((name, time.perf_counter()))


def _timing_report():
    if len(_TIMINGS) < 2:
        return 0.0
    total = _TIMINGS[-1][1] - _TIMINGS[0][1]
    width = max(len(name) for name, _ in _TIMINGS[:-1])
    print("\n⏱ Procedural fixes timing")
    for (name, t), (_, t_next) in zip(_TIMINGS, _TIMINGS[1:]):
        secs = t_next - t
        print(f"   {name:<{width}}  {secs:7.2f} s  {secs / total * 100 if total else 0:5.1f} %")
    print(f"   {'TOTAL':<{width}}  {total:7.2f} s  ({total / 60:.1f} min)")
    return total


# --- finding things ---
def find_rig():
    rig = bpy.data.objects.get(RIG_NAME) if RIG_NAME else None
    if rig is None or rig.type != "ARMATURE":
        rig = next((o for o in bpy.data.objects if o.type == "ARMATURE" and o.get("sgm_rig") == "metahuman"), None)
    return rig


def limbs(rig):
    """The rig's IK limbs, as build_metahuman_rig.py wrote them on it."""
    try:
        return json.loads(rig.get("sgm_limbs", "[]"))
    except ValueError:
        return []


def find_face(rig):
    if FACE_RIG:
        obj = bpy.data.objects.get(FACE_RIG)
        return obj if obj is not None and obj.type == "ARMATURE" else None
    candidates = [o for o in bpy.data.objects if o.type == "ARMATURE" and o is not rig
                  and (o.name.endswith(("_face_gui", "_face_board")) or o.data.name == "face_gui"
                       or sum(1 for b in o.data.bones if b.name.startswith("CTRL_")) >= 10)]
    return candidates[0] if candidates else None


LAYERS_ADDON_MODULES = ("Animation_Layers", "animation_layers")   # the Animation Layers add-on (by Tal)'s module names


def animation_layers_on():
    return hasattr(bpy.types.Object, "als") and hasattr(bpy.types.Object, "Anim_Layers")


def layers_addon_installed():
    """The Animation Layers add-on's module name if it is installed (enabled or not), else None."""
    try:
        import addon_utils
        modules = [m.__name__ for m in addon_utils.modules()]
    except Exception:
        return None
    for module in modules:
        if module.split(".")[-1] in LAYERS_ADDON_MODULES or module.split(".")[-1].lower() == "animation_layers":
            return module
    return None


def enable_layers_addon():
    """Turn the Animation Layers add-on on when it is installed but off. Returns its module name, or None."""
    installed = layers_addon_installed()
    if installed is None:
        return None
    try:
        import addon_utils
        addon_utils.enable(installed, default_set=True, persistent=True)
    except Exception as err:
        print(f"  Animation Layers: couldn't enable {installed}: {err}")
        return None
    if animation_layers_on():
        print(f"  Animation Layers: the add-on ({installed}) was off; enabled it")
        return installed
    return None


def warn_layers_missing():
    """The Animation Layers add-on isn't on: say so in the console and in a popup (the fixes then don't run)."""
    installed = layers_addon_installed()
    lines = ["The Animation Layers add-on (by Tal) isn't enabled" + (f" - it is installed ({installed}), "
             "tick it in Edit > Preferences > Add-ons." if installed else
             " - it doesn't seem to be installed: install it, then enable it in Edit > Preferences > Add-ons."),
             "This script builds its fixes on animation layers (Base, Subtractive, Correction, Working),",
             "so it can't run without it. Nothing was changed. Enable the add-on and run this script again.",
             "(USE_ANIMATION_LAYERS = False at the top of the script keys straight into the action instead -",
             "for testing only: the later steps expect the layers.)"]
    print("\n  " + "!" * 100)
    for line in lines:
        print(f"  WARNING: {line}")
    print("  " + "!" * 100 + "\n")

    def draw(self, context):
        for line in lines:
            self.layout.label(text=line)
    try:
        bpy.context.window_manager.popup_menu(draw, title="metahuman_procedural_fixes: Animation Layers add-on missing",
                                              icon="ERROR")
    except Exception:
        pass


def take_stamp():
    """The take's name from the .blend: 'Hello_tk1.blend' -> 'Hello_tk1'; 'SGM Hello 2026-09-08.blend' -> 'SGM Hello'."""
    stem = os.path.splitext(bpy.path.basename(bpy.data.filepath))[0] if bpy.data.filepath else "Untitled"
    words = stem.split()
    return " ".join(words[:-1]) if len(words) > 1 else stem


# --- keying helpers ---
def key_bone_rotation(pb, quat, frame):
    """Key a rotation in the bone's own rotation mode, through keyframe_insert (so it lands on the active layer)."""
    mode = pb.rotation_mode
    if mode == "QUATERNION":
        if pb.rotation_quaternion.dot(quat) < 0:
            quat = -quat
        pb.rotation_quaternion = quat
        pb.keyframe_insert("rotation_quaternion", frame=frame, group=pb.name)
    elif mode == "AXIS_ANGLE":
        axis, angle = quat.to_axis_angle()
        pb.rotation_axis_angle = (angle, axis.x, axis.y, axis.z)
        pb.keyframe_insert("rotation_axis_angle", frame=frame, group=pb.name)
    else:
        pb.rotation_euler = quat.to_euler(mode, pb.rotation_euler)
        pb.keyframe_insert("rotation_euler", frame=frame, group=pb.name)


def rotation_path(pb):
    return {"QUATERNION": "rotation_quaternion", "AXIS_ANGLE": "rotation_axis_angle"}.get(pb.rotation_mode, "rotation_euler")


# --- operators, with a fallback when they can't run here (no 3D Viewport, e.g. a background run) ---
_clipboard = {}


def _can(op):
    try:
        return op.poll()
    except Exception:
        return False


def selected_bones(rig):
    return [pb for pb in rig.pose.bones if (pb.select if hasattr(pb, "select") else pb.bone.select)]


def key_selected(rig):
    """Key location, rotation and scale of the selected bones on the current frame (Insert Keyframe)."""
    if _can(bpy.ops.anim.keyframe_insert):
        bpy.ops.anim.keyframe_insert()
        return
    frame = bpy.context.scene.frame_current
    for pb in selected_bones(rig):
        for path in ("location", rotation_path(pb), "scale"):
            pb.keyframe_insert(path, frame=frame, group=pb.name)


def copy_pose(rig):
    _clipboard.clear()
    for pb in selected_bones(rig):
        _clipboard[pb.name] = (pb.location.copy(), pb.rotation_quaternion.copy(), pb.rotation_euler.copy(),
                               tuple(pb.rotation_axis_angle), pb.scale.copy())
    if _can(bpy.ops.pose.copy):
        bpy.ops.pose.copy()


def paste_pose(rig):
    if _can(bpy.ops.pose.paste):
        bpy.ops.pose.paste(flipped=False)
        return
    for name, (loc, quat, euler, axis_angle, scale) in _clipboard.items():
        pb = rig.pose.bones[name]
        pb.location, pb.rotation_quaternion, pb.rotation_euler = loc, quat, euler
        pb.rotation_axis_angle, pb.scale = axis_angle, scale


def clear_pose(rig):
    if _can(bpy.ops.pose.transforms_clear):
        bpy.ops.pose.transforms_clear()
        return
    for pb in selected_bones(rig):
        pb.location, pb.rotation_quaternion, pb.scale = (0, 0, 0), (1, 0, 0, 0), (1, 1, 1)
        pb.rotation_euler, pb.rotation_axis_angle = (0, 0, 0), (0, 0, 1, 0)


def select_bones(rig, names):
    """Pose Mode with exactly these bones selected. Returns the names that aren't on the rig."""
    if rig.mode != "POSE":
        bpy.ops.object.mode_set(mode="POSE")
    bpy.ops.pose.select_all(action="DESELECT")
    missing = []
    for name in names:
        pb = rig.pose.bones.get(name)
        if pb is None:
            missing.append(name)
            continue
        if hasattr(pb, "select"):
            pb.select = True             # Blender 5
        else:
            pb.bone.select = True
    return missing


def control_bones(rig):
    """Every control a person keys (not the MetaHuman bones, IK chain or helper bones)."""
    chain = {n for limb in limbs(rig) for n in limb["ik"]}
    return [b.name for b in rig.data.bones if b.get("sgm_control") and b.name not in chain
            and not b.name.endswith(("_ikmch", "_aim")) and b.name != SWITCH_BONE]


def layer_actions(rig):
    ad = rig.animation_data
    found = []
    if ad and ad.action:
        found.append(ad.action)
    for track in (ad.nla_tracks if ad else []):
        for strip in track.strips:
            if strip.action and strip.action not in found:
                found.append(strip.action)
    return found


def remove_curves(rig, paths):
    removed = 0
    for act in layer_actions(rig):
        for layer in act.layers:
            for strip in layer.strips:
                for bag in strip.channelbags:
                    for fc in [fc for fc in bag.fcurves if fc.data_path in paths]:
                        bag.fcurves.remove(fc)
                        removed += 1
    return removed


def remove_bone_curves(rig, bone_names):
    """Delete the F-curves of these bones from the active layer's action."""
    ad = rig.animation_data
    act = ad.action if ad else None
    if act is None:
        return 0
    prefixes = tuple(f'pose.bones["{n}"]' for n in bone_names)
    removed = 0
    for layer in act.layers:
        for strip in layer.strips:
            for bag in strip.channelbags:
                for fc in [fc for fc in bag.fcurves if fc.data_path.startswith(prefixes)]:
                    bag.fcurves.remove(fc)
                    removed += 1
    return removed


def load_neutral(rig):
    """{control: quaternion}. From NEUTRAL_POSE_FILE, else the rest pose."""
    global NEUTRAL_POSE_FILE
    if not NEUTRAL_POSE_FILE:
        NEUTRAL_POSE_FILE = workspace_path("neutral_pose", "")
    if NEUTRAL_POSE_FILE and os.path.isfile(NEUTRAL_POSE_FILE):
        with open(NEUTRAL_POSE_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        pose = {name: Quaternion(wxyz).normalized() for name, wxyz in data.get("bones", {}).items()}
        print(f"NEUTRAL pose: {len(pose)} controls from {NEUTRAL_POSE_FILE}")
        return pose
    print(f"⚠️ No NEUTRAL pose file ({NEUTRAL_POSE_FILE}): the rest pose (MetaHuman A-pose) is used as NEUTRAL. "
          "Pose the rig and run save_neutral_pose.py to make one.")
    return {name: Quaternion() for name in control_bones(rig)}


# --- smoothing the captured hips and legs ---
def captured_action(rig):
    """The action holding the captured motion: the Subtractive_Layer's (the active copy of it), else the rig's own."""
    ad = rig.animation_data
    if ad is None:
        return None
    if USE_ANIMATION_LAYERS:
        tracks = list(ad.nla_tracks)
        for track in tracks:
            if track.name in ("Subtractive_Layer", "Base_Layer.001") and track.strips:
                return track.strips[0].action
        if len(tracks) > 1 and tracks[1].strips:
            return tracks[1].strips[0].action
    return ad.action


def gaussian(values, sigma):
    """The values smoothed with a Gaussian of `sigma` samples (the ends held, so the take's ends don't drift)."""
    radius = max(1, int(math.ceil(sigma * 3)))
    weights = [math.exp(-0.5 * (k / sigma) ** 2) for k in range(-radius, radius + 1)]
    last = len(values) - 1
    out = []
    for i in range(len(values)):
        total = norm = 0.0
        for k, w in zip(range(-radius, radius + 1), weights):
            total += w * values[min(last, max(0, i + k))]
            norm += w
        out.append(total / norm)
    return out


def smooth_bones(rig, bone_names, sigma):
    """Smooth the keyed location and rotation of these bones in the captured motion. Returns the curves smoothed."""
    if sigma <= 0:
        return 0
    action = captured_action(rig)
    if action is None:
        return 0
    prefixes = tuple(f'pose.bones["{n}"].' for n in bone_names)
    smoothed = 0
    for layer in action.layers:
        for strip in layer.strips:
            for bag in strip.channelbags:
                for fc in bag.fcurves:
                    if not fc.data_path.startswith(prefixes) or fc.data_path.endswith(".scale"):
                        continue
                    points = sorted(fc.keyframe_points, key=lambda p: p.co[0])
                    if len(points) < 3:
                        continue
                    new = gaussian([p.co[1] for p in points], sigma)
                    for p, value in zip(points, new):
                        delta = value - p.co[1]
                        p.co[1] = value
                        p.handle_left[1] += delta
                        p.handle_right[1] += delta
                    fc.update()
                    smoothed += 1
    return smoothed


# --- the IK snap ---
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

def snap_ik_to_fk(rig, chosen, frames, match_poles=True):
    """On every frame: IK hand / foot onto the FK one, and the pole behind the FK elbow / in front of the knee."""
    pbs, bones = rig.pose.bones, rig.data.bones
    scene = bpy.context.scene
    for frame in frames:
        scene.frame_set(frame)
        for limb in chosen:
            fk_end, control = pbs[limb["fk"][2]], pbs[limb["control"]]
            offset = bones[limb["fk"][2]].matrix_local.inverted() @ bones[limb["control"]].matrix_local
            control.matrix = fk_end.matrix @ offset
            for path in ("location", rotation_path(control), "scale"):
                control.keyframe_insert(path, frame=frame, group=control.name)
            if match_poles:
                where = pole_position(rig, limb)
                if where is not None:
                    pole = pbs[limb["pole"]]
                    m = pole.matrix.copy()
                    m.translation = where
                    pole.matrix = m
                    pole.keyframe_insert("location", frame=frame, group=pole.name)
        bpy.context.view_layer.update()


# --- layers ---
def layers_state(rig):
    ad = rig.animation_data
    slot = getattr(ad, "action_slot", None) if ad else None
    return (f"turn_on={getattr(rig.als, 'turn_on', '?')}, layers={[l.name for l in getattr(rig, 'Anim_Layers', [])]}, "
            f"NLA tracks={[t.name for t in ad.nla_tracks] if ad else []}, action={ad.action.name if ad and ad.action else None}, "
            f"slot={getattr(slot, 'identifier', None)}")


def layers_addon_version():
    """'Animation Layers 2.1.9.6' (from the add-on's bl_info), or '' when it can't be read."""
    module = layers_addon_installed()
    if module is None:
        return ""
    try:
        import addon_utils, sys
        info = addon_utils.module_bl_info(sys.modules[module])
        return f"{info.get('name', module)} {'.'.join(str(v) for v in info.get('version', ()))}"
    except Exception:
        return module


def switch_layers_on(rig):
    """Animation Layers on for the rig, the way it's done by hand: the rig made the active object, then
    bpy.context.object.als.turn_on = True. Returns True when the rig's turn_on reads True afterwards."""
    current = bpy.context.object
    if current is not None and current is not rig and current.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")         # leave another object's edit / pose mode before switching
    rig.hide_set(False)
    rig.select_set(True)
    bpy.context.view_layer.objects.active = rig
    if bpy.context.object is not rig:
        print(f"  ⚠️ the active object is '{getattr(bpy.context.object, 'name', None)}', not '{rig.name}': "
              "Animation Layers may be switched on for the wrong object")
    bpy.context.object.als.turn_on = True
    on = bool(rig.als.turn_on)
    print(f"  Animation Layers turn_on for '{rig.name}': {on}")
    return on


def turn_layers_on(rig):
    """Animation Layers on for the rig, with its base layer holding the take's action.

    Ticking turn_on only switches the add-on on for the object: Animation Layers 2.x builds the base layer
    (the rig's action moved onto an NLA track) when the first layer is added. So, as the Galtis script did:
    turn on, add a layer, then take the extra one back off (when the add made a base layer and a new one)."""
    ad = rig.animation_data
    take_action = ad.action if ad else None
    view_layer = bpy.context.view_layer
    if not switch_layers_on(rig):
        raise RuntimeError(f"bpy.context.object.als.turn_on = True didn't switch Animation Layers on for '{rig.name}' "
                           f"(the active object is '{getattr(bpy.context.object, 'name', None)}')")
    if rig.mode != "POSE":
        bpy.ops.object.mode_set(mode="POSE")
    bpy.ops.anim.add_anim_layer()
    if len(rig.Anim_Layers) >= 2:
        bpy.ops.anim.remove_anim_layer()
    view_layer.update()
    version = layers_addon_version() or "the Animation Layers add-on"
    if not len(rig.Anim_Layers):
        print(f"\n  Animation Layers made no layer on '{rig.name}' ({layers_state(rig)}); "
              f"{version} on Blender {bpy.app.version_string}")
        raise RuntimeError(
            f"Animation Layers made no layer on '{rig.name}' ({version}, Blender {bpy.app.version_string}), even "
            "after adding one. Check the System Console (Window > Toggle System Console) for an error from the "
            "add-on, and try Add Layer by hand in its panel (sidebar N, Animation tab).")
    ad = rig.animation_data
    held = {s.action for t in (ad.nla_tracks if ad else []) for s in t.strips if s.action}
    if take_action is not None and take_action not in held and not (ad and ad.action is take_action):
        print(f"\n  The base layer doesn't hold the take ({layers_state(rig)})")
        raise RuntimeError(
            f"Animation Layers made its layers on '{rig.name}', but the take's action ({take_action.name}) isn't in "
            f"any of them, so the fixes would run on an empty layer. Nothing past this point was done. Reopen the take's "
            ".blend (without saving this one) and check the layers in the Animation Layers panel.")
    print(f"✅ Animation Layers on for '{rig.name}': {[l.name for l in rig.Anim_Layers]} ({version})")


def setup_layers(rig):
    """Base_Layer (muted copy source), Subtractive_Layer (REPLACE), Correction_Layer. As in the Galtis script."""
    turn_layers_on(rig)
    rig.als.layer_index = 0
    rig.Anim_Layers[0].name = "Base_Layer"
    bpy.ops.anim.duplicate_anim_layer()
    rig.animation_data.nla_tracks["Base_Layer.001"].strips["Base_Layer.001"].blend_type = "REPLACE"
    rig.als.layer_index = 0
    rig.Anim_Layers[0].influence_mute = True
    rig.Anim_Layers[0].influence = 0
    rig.als.layer_index = 1
    rig.Anim_Layers[1].name = "Subtractive_Layer"
    bpy.ops.anim.add_anim_layer()
    rig.Anim_Layers[2].name = "Correction_Layer"
    wanted = {"Base_Layer", "Subtractive_Layer", "Correction_Layer"}
    if not wanted <= {l.name for l in rig.Anim_Layers}:
        raise RuntimeError("The animation layers weren't made as expected (" + layers_state(rig) + ")")


def add_layer(rig, name):
    if USE_ANIMATION_LAYERS:
        bpy.ops.anim.add_anim_layer()
        rig.Anim_Layers[len(rig.Anim_Layers) - 1].name = name


BOARD_BONES = ("ctrl_facegui", "ctrl_faceguifollowhead", "ctrl_eyesaimfollowhead", "ctrl_rigLogicswitch".lower(),
               "ctrl_lookatswitch", "ctrl_riglogic", "ctrl_expressions")   # the board itself and its switches
BOARD_PREFIXES = ("frm_", "grp_", "text_", "loc_", "gui", "root")


def face_controls(face):
    """
    The face board controls to rest on frame 1 and the last frame: the CTRL_ bones the face animation moves.
    Left out: the board's own bones (its frame, the CTRL_faceGUI it hangs from, the switches), any bone other
    controls hang from, and any bone keyed to one value all through the take - that is where the add-on placed
    the board beside the head, and clearing it would pull the board to the origin. Returns (controls, left out).
    """
    animated, changing = set(), set()
    ad = face.animation_data
    actions = ([ad.action] if ad and ad.action else []) + [s.action for t in (ad.nla_tracks if ad else []) for s in t.strips if s.action]
    for act in actions:
        for layer in act.layers:
            for strip in layer.strips:
                for bag in strip.channelbags:
                    for fc in bag.fcurves:
                        m = re.match(r'pose\.bones\["([^"]+)"\]', fc.data_path)
                        if not (m and m.group(1) in face.pose.bones):
                            continue
                        animated.add(m.group(1))
                        values = [k.co.y for k in fc.keyframe_points]
                        if values and max(values) - min(values) > 1e-6:
                            changing.add(m.group(1))
    if not animated:
        animated = changing = {b.name for b in face.pose.bones if b.name.startswith("CTRL_")}
    left_out = set()
    for name in animated:
        low = name.lower()
        if low in BOARD_BONES or "gui" in low or low.startswith(BOARD_PREFIXES) or not low.startswith("ctrl_"):
            left_out.add(name)
        elif name not in changing:
            left_out.add(name)                        # keyed, but never moves: a placement, not an expression
    controls = animated - left_out
    for name in list(controls):                       # a bone other controls hang from is a group, not a control
        if any(other != name and name in [p.name for p in face.pose.bones[other].parent_recursive] for other in controls):
            left_out.add(name)
    controls -= left_out
    return sorted(controls), sorted(left_out)


def face_fixes(face, ease_in, ease_out):
    """The face board rests on frame 1 and the last frame, easing into its animation (own layer)."""
    scene = bpy.context.scene
    body = bpy.context.view_layer.objects.active
    was_hidden, was_disabled = face.hide_get(), face.hide_viewport
    face.hide_viewport = False
    face.hide_set(False)
    if bpy.context.object and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="DESELECT")
    face.select_set(True)
    bpy.context.view_layer.objects.active = face
    if USE_ANIMATION_LAYERS:
        bpy.context.object.als.turn_on = True      # the face board is the active object here
    bpy.ops.object.mode_set(mode="POSE")
    if USE_ANIMATION_LAYERS:
        if len(face.Anim_Layers) == 0:
            bpy.ops.anim.add_anim_layer()
            bpy.ops.anim.remove_anim_layer()
        face.als.layer_index = len(face.Anim_Layers) - 1
        bpy.ops.anim.add_anim_layer()
        face.Anim_Layers[len(face.Anim_Layers) - 1].name = "Face_Correction_Layer"
    # only the controls the face animation moves: the board's own frame and layout bones (which the add-on
    # sizes and places beside the head) are left alone, or the board would shrink to the origin
    controls, left_out = face_controls(face)
    if left_out:
        print(f"   face board: left where they are (board placement, switches, groups): {', '.join(left_out[:8])}"
              + (" ..." if len(left_out) > 8 else ""))
    select_bones(face, controls)
    scene.frame_set(ease_in)
    key_selected(face)
    scene.frame_set(scene.frame_end - ease_out)
    key_selected(face)
    for f in (1, scene.frame_end):
        scene.frame_set(f)
        select_bones(face, controls)
        for name in controls:
            pb = face.pose.bones[name]
            pb.location = (0.0, 0.0, 0.0)
            pb.rotation_quaternion = (1.0, 0.0, 0.0, 0.0)
            pb.rotation_euler = (0.0, 0.0, 0.0)
        key_selected(face)
    bpy.ops.pose.select_all(action="DESELECT")
    print(f"✅ {face.name}: {len(controls)} face controls at rest on 1 and {scene.frame_end}, "
          f"anchors at {ease_in} and {scene.frame_end - ease_out}")
    bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="DESELECT")
    face.hide_set(was_hidden)
    face.hide_viewport = was_disabled
    if body is not None:
        body.select_set(True)
        bpy.context.view_layer.objects.active = body
        bpy.ops.object.mode_set(mode="POSE")


def _key_rest_here(rig, frame):
    """Key every bone at rest. Blender keys through the layer stack (NLA remapping): the value stored on the active
    layer is the one that makes the rig's final pose the rest pose, whatever the layers below hold."""
    for pb in rig.pose.bones:
        pb.location, pb.scale = (0, 0, 0), (1, 1, 1)
        pb.rotation_quaternion, pb.rotation_euler, pb.rotation_axis_angle = (1, 0, 0, 0), (0, 0, 0), (0, 0, 1, 0)
        for path in ("location", rotation_path(pb), "scale"):
            pb.keyframe_insert(path, frame=frame, group=pb.name)


def key_rest_frame0(rig):
    """Frame 0 as the rest pose: every bone's transforms cleared and keyed on frame 0, on every layer that plays
    (so no layer above puts anything back), the same as Clear Transform + Insert Keyframe on each layer by hand.
    Bone by bone, so hidden bones and bone collections are keyed too. Returns the layers keyed."""
    scene = bpy.context.scene
    scene.frame_set(0)
    keyed = []
    if USE_ANIMATION_LAYERS and getattr(rig, "Anim_Layers", None):
        active = rig.als.layer_index
        tracks = rig.animation_data.nla_tracks if rig.animation_data else {}
        for i, layer in enumerate(rig.Anim_Layers):
            # Base_Layer is silenced (influence 0): nothing keyed there plays, and Blender can't key through it
            if getattr(layer, "mute", False) or getattr(layer, "influence", 1.0) <= 1e-6:
                continue
            track = tracks.get(layer.name) if hasattr(tracks, "get") else None
            strip = track.strips[0] if track is not None and len(track.strips) else None
            rig.als.layer_index = i                       # the add-on puts this layer's action on the rig
            _key_rest_here(rig, 0)
            keyed.append(f"{layer.name} ({(strip.blend_type if strip is not None else 'replace').lower()})")
        rig.als.layer_index = active
    else:
        _key_rest_here(rig, 0)
        keyed.append(rig.animation_data.action.name if rig.animation_data and rig.animation_data.action else "action")
    bpy.context.view_layer.update()
    return keyed


# --- the fixes ---
def run_procedural_fixes(ease_in=30, ease_out=30, leg_pin=30, plant_feet=False, match_poles=True,
                         ik_arms=True, ik_legs=True, face=True, face_ease_in=30, face_ease_out=30, smooth_legs=0,
                         resume=None):
    _TIMINGS.clear()
    _stage("Setup")
    rig = find_rig()
    scene = bpy.context.scene
    if bpy.context.object and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    for obj in bpy.context.view_layer.objects:
        obj.select_set(False)
    rig.hide_set(False)
    rig.select_set(True)
    bpy.context.view_layer.objects.active = rig
    if USE_ANIMATION_LAYERS:
        switch_layers_on(rig)                          # Animation Layers on for the rig before anything else
    for coll in rig.data.collections_all:
        coll.is_solo = False
        coll.is_visible = coll.name not in ("Deform", "MCH")
    end = scene.frame_end
    controls = control_bones(rig)
    chosen = [l for l in limbs(rig) if (l["kind"] == "arm" and ik_arms) or (l["kind"] == "leg" and ik_legs)]

    _stage("Layer stack")
    if USE_ANIMATION_LAYERS:
        setup_layers(rig)
    else:
        bpy.ops.object.mode_set(mode="POSE")
    # the IK switches are off (FK) while the FK pose is corrected
    switch = rig.pose.bones[SWITCH_BONE]
    switch_paths = {f'pose.bones["{SWITCH_BONE}"]["{l["switch"]}"]' for l in limbs(rig)}
    remove_curves(rig, switch_paths)
    for l in limbs(rig):
        switch[l["switch"]] = 0.0
    rig.update_tag()

    _stage("Smooth hips and legs")
    if smooth_legs > 0:
        count = smooth_bones(rig, LEGS, float(smooth_legs))
        rig.update_tag()
        bpy.context.view_layer.update()
        print(f"✅ Hips and legs smoothed ({smooth_legs} frames): {count} curves" if count else
              "⚠️ Smooth hips and legs: no keyed hip / leg curves found in the captured motion")

    _stage("Anchors")
    # anchors keep the middle of the take as it is: corrections at the ends fade out by these frames
    select_bones(rig, controls)
    for f in (ease_in, end - ease_out):
        scene.frame_set(f)
        key_selected(rig)

    _stage("Rest pose on frame 0")
    scene.frame_set(0)
    select_bones(rig, controls)
    clear_pose(rig)
    key_selected(rig)

    _stage("Neutral pose")
    neutral = load_neutral(rig)
    scene.frame_set(1)
    keyed, missing = 0, []
    for name, quat in neutral.items():
        pb = rig.pose.bones.get(name)
        if pb is None:
            missing.append(name)
            continue
        key_bone_rotation(pb, quat, 1)
        keyed += 1
    bpy.context.view_layer.update()
    print(f"[NEUTRAL] keyed {keyed} controls at frame 1" + (f"; not on this rig: {', '.join(missing)}" if missing else ""))
    select_bones(rig, controls)
    key_selected(rig)
    copy_pose(rig)
    scene.frame_set(end)
    paste_pose(rig)
    key_selected(rig)

    _stage("Leg and hip pin")
    if leg_pin > 0:
        scene.frame_set(1)
        absent = select_bones(rig, LEGS)
        copy_pose(rig)
        for f in sorted({2, leg_pin, end - leg_pin} | set(range(scene.frame_start, end + 1, leg_pin))):
            if 1 <= f <= end:
                scene.frame_set(f)
                paste_pose(rig)
                key_selected(rig)
        print("✅ Leg pin: hips and legs " + ("held in their frame-1 pose on every frame" if leg_pin == 1 else
                                             f"keyed to their frame-1 pose every {leg_pin} frames"))
        if absent:
            print(f"⚠️ leg pin: not on this rig: {', '.join(absent)}")
    else:
        print("Leg pin: off - the captured hips and legs are kept")

    _stage("Torso hold")
    scene.frame_set(1)
    select_bones(rig, TORSO)
    copy_pose(rig)
    for f in (ease_in, end - ease_out, end):
        scene.frame_set(f)
        paste_pose(rig)
        key_selected(rig)
    scene.frame_set(1)
    select_bones(rig, controls)
    copy_pose(rig)
    scene.frame_set(2)
    paste_pose(rig)
    key_selected(rig)

    _stage("IK snap")
    bpy.ops.pose.select_all(action="DESELECT")
    snap_ik_to_fk(rig, chosen, range(0, end + 1), match_poles)
    print(f"✅ IK snapped to FK on frames 0-{end}: {', '.join(l['control'] for l in chosen) or 'none'}"
          + (" (with elbow / knee targets)" if match_poles else ""))

    _stage("Plant feet")
    feet = [l["control"] for l in chosen if l["kind"] == "leg"]
    if plant_feet and feet:
        held = {}
        for f in (0, 1):
            scene.frame_set(f)
            for name in feet:
                held[(name, f)] = rig.pose.bones[name].matrix.copy()
        remove_bone_curves(rig, feet)
        for f in (0, 1):
            scene.frame_set(f)
            for name in feet:
                pb = rig.pose.bones[name]
                pb.matrix = held[(name, f)]
                bpy.context.view_layer.update()
                for path in ("location", rotation_path(pb), "scale"):
                    pb.keyframe_insert(path, frame=f, group=name)
        print(f"✅ Feet planted: {', '.join(feet)} held at their frame-1 position")

    _stage("Working layer")
    add_layer(rig, "Working_Layer")
    select_bones(rig, controls)
    for f in (0, 1, end - 1):
        scene.frame_set(f)
        key_selected(rig)
    scene.frame_set(1)
    copy_pose(rig)
    scene.frame_set(end)
    paste_pose(rig)
    key_selected(rig)
    bpy.ops.pose.select_all(action="DESELECT")

    _stage("IK switch")
    removed = remove_curves(rig, switch_paths)
    for l in chosen:
        switch[l["switch"]] = 1.0
    rig.update_tag()
    bpy.context.view_layer.update()
    print(f"✅ IK switch: {', '.join(l['switch'] for l in chosen) or 'nothing'} set to IK"
          + (f" ({removed} switch curves removed)" if removed else ""))

    _stage("Face board")
    if face:
        board = find_face(rig)
        if board is None:
            print("⚠️ No face board armature in this file; face ease in/out skipped")
        else:
            face_fixes(board, face_ease_in, face_ease_out)

    _stage("Rest pose on frame 0 (final)")
    if bpy.context.view_layer.objects.active is not rig:
        if bpy.context.object and bpy.context.object.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        bpy.context.view_layer.objects.active = rig
        rig.select_set(True)
    if rig.mode != "POSE":
        bpy.ops.object.mode_set(mode="POSE")
    layers = key_rest_frame0(rig)
    print(f"✅ Frame 0: all {len(rig.pose.bones)} bones cleared and keyed (rest pose) on {', '.join(layers)}")

    _stage("Visibility and save")
    scene.tool_settings.transform_pivot_point = "INDIVIDUAL_ORIGINS"
    for coll in rig.data.collections_all:
        coll.is_visible = coll.name in ("Limbs IK", "Torso", "Fingers", "Switch")
    for action in list(bpy.data.actions):
        if action.name.startswith(("Action", "Anim")) and action.users == 0:
            bpy.data.actions.remove(action)
    scene.frame_set(1)
    stamp = take_stamp()
    folder = os.path.dirname(bpy.data.filepath) if bpy.data.filepath else os.path.expanduser("~")
    with open(os.path.join(folder, f"{stamp} Framelog.txt"), "w") as fh:
        fh.write("End Frame Adjustment Report\n")
        fh.write(f"Generated from: {stamp}\n")
        fh.write(f"Blender Version: {bpy.app.version_string}\n")
        fh.write(f"File: {bpy.path.basename(bpy.data.filepath) if bpy.data.filepath else 'Untitled'}\n")
        fh.write(f"Frame Range: {scene.frame_start} - {scene.frame_end}\n")
        fh.write(f"Ease In {ease_in}, Ease Out {ease_out}, Leg Pin {leg_pin if leg_pin > 0 else 'off'}, "
                 f"Smooth Legs {smooth_legs}, "
                 f"IK arms {ik_arms}, IK legs {ik_legs}, "
                 f"plant feet {plant_feet}, match poles {match_poles}\n")
    if resume is not None:
        resume()                                       # Live Face back on before the file is saved
    if SAVE_WORKING and bpy.data.filepath:
        path = os.path.join(folder, f"{stamp} Working.blend")
        bpy.ops.wm.save_as_mainfile(filepath=path, relative_remap=False)
        print(f"[saved] {path}")
    _stage("end")
    return _timing_report()


class SGM_OT_metahuman_procedural_fixes(bpy.types.Operator):
    """Procedural fixes on the MetaHuman rig (Animation Layers, neutral pose, stabilizers, IK snap)"""
    bl_idname = "sgm.metahuman_procedural_fixes"
    bl_label = "MetaHuman Procedural Fixes"
    bl_options = {"REGISTER", "UNDO"}

    ease_in: bpy.props.IntProperty(name="Ease In", default=DEFAULTS["ease_in"], min=1,
                                   description="Correction-layer anchor this many frames after the start")
    ease_out: bpy.props.IntProperty(name="Ease Out", default=DEFAULTS["ease_out"], min=1,
                                    description="Correction-layer anchor this many frames before the end")
    pin_legs: bpy.props.BoolProperty(name="Leg Pin", default=DEFAULTS["pin_legs"], options={"SKIP_SAVE"},
                                     description="Key the hips and legs to their frame-1 pose (off = keep the captured "
                                                 "hips and legs)")
    leg_pin: bpy.props.IntProperty(name="Every N Frames", default=DEFAULTS["leg_pin"], min=1, options={"SKIP_SAVE"},
                                   description="Key the frame-1 legs and hips every this many frames "
                                               "(1 = every frame: hips and legs hold their frame-1 pose)")
    smooth_legs: bpy.props.IntProperty(name="Smooth Legs", default=DEFAULTS["smooth_legs"], min=0, max=30,
                                       description="Smooth the captured hips and legs over about this many frames, "
                                                   "to take out the capture's jitter (0 = off)")
    ik_arms: bpy.props.BoolProperty(name="Arms to IK", default=DEFAULTS["ik_arms"])
    ik_legs: bpy.props.BoolProperty(name="Legs to IK", default=DEFAULTS["ik_legs"])
    match_poles: bpy.props.BoolProperty(name="Match elbow / knee targets", default=DEFAULTS["match_poles"],
                                        description="Place the IK elbow and knee targets from the FK limbs on every frame")
    plant_feet: bpy.props.BoolProperty(name="Plant feet", default=DEFAULTS["plant_feet"],
                                       description="Hold the IK feet at their frame-1 position for the whole clip")
    face: bpy.props.BoolProperty(name="Face board ease in/out", default=DEFAULTS["face"])
    face_ease_in: bpy.props.IntProperty(name="Face Ease In", default=DEFAULTS["face_ease_in"], min=1)
    face_ease_out: bpy.props.IntProperty(name="Face Ease Out", default=DEFAULTS["face_ease_out"], min=1)

    def check_ready(self, context):
        if USE_ANIMATION_LAYERS and not animation_layers_on():
            enable_layers_addon()                         # installed but off: turn it on
        if USE_ANIMATION_LAYERS and not animation_layers_on():
            warn_layers_missing()
            self.report({"ERROR"}, "Enable the Animation Layers add-on first (Edit > Preferences > Add-ons)")
            return False
        rig = find_rig()
        if rig is None:
            self.report({"ERROR"}, "No MetaHuman rig in this file (run build_metahuman_rig.py and import_unreal_take.py)")
            return False
        if not (rig.animation_data and (rig.animation_data.action or rig.animation_data.nla_tracks)):
            self.report({"ERROR"}, f"'{rig.name}' has no animation: import a take onto it first")
            return False
        return True

    def invoke(self, context, event):
        if not self.check_ready(context):
            return {"CANCELLED"}
        return context.window_manager.invoke_props_dialog(self, width=300)

    def draw(self, context):
        scene = context.scene
        col = self.layout.column()
        col.label(text=f"Frames {scene.frame_start}-{scene.frame_end}")
        box = col.box()
        box.prop(self, "ease_in")
        box.prop(self, "ease_out")
        row = box.row()
        row.prop(self, "pin_legs")
        number = row.row()
        number.enabled = self.pin_legs                  # greyed out while Leg Pin is off
        number.prop(self, "leg_pin", text="Every")
        for name in ("smooth_legs", "ik_arms", "ik_legs", "match_poles", "plant_feet"):
            box.prop(self, name)
        box = col.box()
        box.prop(self, "face")
        sub = box.column()
        sub.enabled = self.face
        sub.prop(self, "face_ease_in")
        sub.prop(self, "face_ease_out")

    def execute(self, context):
        scene = context.scene
        if not self.check_ready(context):
            return {"CANCELLED"}
        if self.ease_in >= scene.frame_end - self.ease_out:
            self.report({"ERROR"}, f"Ease In + Ease Out ({self.ease_in + self.ease_out}) doesn't fit in frames "
                                   f"{scene.frame_start}-{scene.frame_end}")
            return {"CANCELLED"}
        settings = getattr(scene, "sgm_mh_rig", None)
        live = bool(getattr(settings, "live_face", False))
        if live:
            settings.live_face = False                 # RigLogic on every frame change would make the fixes crawl
            print("  Live Face paused for the procedural fixes")
        try:
            total = run_procedural_fixes(self.ease_in, self.ease_out, self.leg_pin if self.pin_legs else 0,
                                         self.plant_feet, self.match_poles,
                                         self.ik_arms, self.ik_legs, self.face, self.face_ease_in, self.face_ease_out,
                                         self.smooth_legs, resume=(lambda: setattr(settings, "live_face", True)) if live
                                         else None)
        finally:
            if live and not settings.live_face:
                settings.live_face = True
        self.report({"INFO"}, f"Procedural fixes done in {total:.1f} s")
        return {"FINISHED"}


try:
    bpy.utils.unregister_class(bpy.types.SGM_OT_metahuman_procedural_fixes)
except (AttributeError, RuntimeError):
    pass
bpy.utils.register_class(SGM_OT_metahuman_procedural_fixes)

if __name__ == "__main__":
    # switch Animation Layers on here, from the script itself (the same as typing
    # bpy.context.object.als.turn_on = True in the Python Console), before the dialog opens
    if USE_ANIMATION_LAYERS and not animation_layers_on():
        enable_layers_addon()
    _rig = find_rig()
    if USE_ANIMATION_LAYERS and animation_layers_on() and _rig is not None:
        switch_layers_on(_rig)
    bpy.ops.sgm.metahuman_procedural_fixes("INVOKE_DEFAULT")
