"""
Procedural fixes for a MetaHuman body in Unreal (Sequencer + Control Rig).

The Unreal version of galtis_procedural_fixes.py. What it does, in order:
  1. IK match     - checks that the IK hand / foot controls and the elbow / knee poles follow
                    the baked animation, and snaps them to the FK chain where they don't.
  2. IK switch    - arms and legs are switched to IK for the whole clip (then the match is
                    checked once more, in IK mode).
  3. Plant feet   - the IK foot controls (and knee poles) hold one position for the whole clip,
                    which stops foot sliding.
  4. Neutral pose - the clip starts in the neutral pose and blends into the captured motion
                    over EASE_IN frames, and blends back to the neutral pose over the last
                    EASE_OUT frames. This is a separate additive layer (a second section on the
                    Control Rig track, tinted teal), keyed on every frame of the two eases:
                    mute or delete it to get the plain clip back.

Before running:
  1. Open the Level Sequence in Sequencer. The animation must be baked to MetaHuman_ControlRig
     (right-click the Body track > Bake To Control Rig > MetaHuman_ControlRig).
  2. For step 4, capture the neutral pose once with capture_neutral_pose.py. It saves
     neutral_pose.json, which this script looks for next to itself. Without that file,
     step 4 is skipped.
  3. File > Execute Python Script > this file.

Everything is one undo step (Ctrl+Z), and the script can be run again on the same sequence:
it replaces its own layer instead of adding another. A report goes to the Output Log and to
    <project>/Saved/procedural_fixes_report.txt    (and next to this script)

The IK/FK switches are on/off channels, which layers can't blend, so steps 1-3 edit the baked
(bottom) section directly. The FK controls keep their baked keys, so nothing from the capture
is lost.
"""

import json
import math
import os
import shutil
import time
import traceback
from collections import namedtuple

import unreal

# ------------------------------------------------------------------ settings
IK_ARMS = True              # switch the arms to IK
IK_LEGS = True              # switch the legs to IK
PLANT_FEET = True           # hold the IK feet in place for the whole clip
FEET_FRAME = None           # frame whose foot position is held; None = first keyed frame
HOLD_KNEE_POLES = True      # hold the knee poles with the feet (stops the knees swivelling)

NEUTRAL_POSE = True         # start and end the clip in the neutral pose (needs neutral_pose.json)
EASE_IN = 30                # frames to ease from the neutral pose into the motion
EASE_OUT = 30               # frames to ease from the motion back to the neutral pose
FEET_AT_NEUTRAL = True      # planted feet stand where the neutral pose has them (else where FEET_FRAME has them)
NEUTRAL_FILE = "neutral_pose.json"      # made by capture_neutral_pose.py; next to this script or in <project>/Saved
NEUTRAL_SKIP = ("global_ctrl", "root_ctrl", "body_offset_ctrl")   # controls the neutral pose leaves alone

# "auto": snap IK controls / poles to the FK chain only where the bake left them off
# "always": snap them all    "never": leave them as baked
MATCH_IK = "auto"

IK_VALUE = True             # value of the *_fk_ik_switch controls that means IK
TOLERANCE_CM = 1.0          # an IK control this close to the FK chain counts as matched
POLE_TOLERANCE_DEG = 10.0   # a pole this close to the bend direction counts as matched
CHECK_SAMPLES = 24          # frames sampled for the check (the fixes themselves cover every frame)
SHOW_DIALOG = True
REPORT_NAME = "procedural_fixes_report.txt"
NEUTRAL_LAYER = "Neutral pose"          # label of the layer this script makes
LAYER_TINT = (0, 150, 136, 90)          # r, g, b, a - also how the script recognises its own layer

# MetaHuman_ControlRig control names
LIMBS = {
    "arm_l": {"switch": "arm_l_fk_ik_switch", "ik": "hand_l_ik_ctrl", "pole": "arm_l_pv_ik_ctrl",
              "fk": ("upperarm_l_fk_ctrl", "lowerarm_l_fk_ctrl", "hand_l_fk_ctrl")},
    "arm_r": {"switch": "arm_r_fk_ik_switch", "ik": "hand_r_ik_ctrl", "pole": "arm_r_pv_ik_ctrl",
              "fk": ("upperarm_r_fk_ctrl", "lowerarm_r_fk_ctrl", "hand_r_fk_ctrl")},
    "leg_l": {"switch": "leg_l_fk_ik_switch", "ik": "foot_l_ik_ctrl", "pole": "leg_l_pv_ik_ctrl",
              "fk": ("thigh_l_fk_ctrl", "calf_l_fk_ctrl", "foot_l_fk_ctrl")},
    "leg_r": {"switch": "leg_r_fk_ik_switch", "ik": "foot_r_ik_ctrl", "pole": "leg_r_pv_ik_ctrl",
              "fk": ("thigh_r_fk_ctrl", "calf_r_fk_ctrl", "foot_r_fk_ctrl")},
}

LIB = getattr(unreal, "ControlRigSequencerLibrary", None)
SEQ_LIB = getattr(unreal, "LevelSequenceEditorBlueprintLibrary", None)

# ------------------------------------------------------------------ report and timing
_lines = []
_stages = []


def out(text=""):
    _lines.append(str(text))


def header(title):
    out()
    out("=" * 78)
    out(title)
    out("=" * 78)


def stage(name):
    _stages.append((name, time.perf_counter()))


def timing_lines():
    rows = []
    end = time.perf_counter()
    for i, (name, start) in enumerate(_stages):
        stop = _stages[i + 1][1] if i + 1 < len(_stages) else end
        rows.append(f"  {name:<28} {stop - start:7.2f} s")
    if _stages:
        rows.append(f"  {'Total':<28} {end - _stages[0][1]:7.2f} s")
    return rows


class Stop(Exception):
    """A problem the user has to fix before the script can run."""


# ------------------------------------------------------------------ transform maths
# Plain Python on (x, y, z) and quaternions (x, y, z, w), so it doesn't depend on which maths
# functions this engine version exposes to Python.
Pose = namedtuple("Pose", "p q s")      # position, rotation, uniform scale


def v_add(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def v_sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def v_scale(a, k):
    return (a[0] * k, a[1] * k, a[2] * k)


def v_dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def v_len(a):
    return math.sqrt(v_dot(a, a))


def v_dist(a, b):
    return v_len(v_sub(a, b))


def q_mul(a, b):
    """Rotation b followed by rotation a."""
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return (aw * bx + ax * bw + ay * bz - az * by,
            aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw,
            aw * bw - ax * bx - ay * by - az * bz)


def q_conj(q):
    return (-q[0], -q[1], -q[2], q[3])


def q_norm(q):
    n = math.sqrt(sum(c * c for c in q)) or 1.0
    return tuple(c / n for c in q)


def q_rotate(q, v):
    r = q_mul(q_mul(q, (v[0], v[1], v[2], 0.0)), q_conj(q))
    return (r[0], r[1], r[2])


def q_angle_deg(a, b):
    d = abs(sum(x * y for x, y in zip(a, b)))
    return math.degrees(2.0 * math.acos(min(1.0, d)))


def to_pose(transform):
    t, r, s = transform.translation, transform.rotation, transform.scale3d
    return Pose((t.x, t.y, t.z), q_norm((r.x, r.y, r.z, r.w)), abs(s.x) or 1.0)


def relative(child, parent):
    """`child` expressed in `parent`'s frame."""
    inv = q_conj(parent.q)
    return Pose(v_scale(q_rotate(inv, v_sub(child.p, parent.p)), 1.0 / parent.s),
                q_norm(q_mul(inv, child.q)), child.s / parent.s)


def compose(local, parent):
    """World pose of something that sits at `local` inside `parent`."""
    return Pose(v_add(parent.p, q_rotate(parent.q, v_scale(local.p, parent.s))),
                q_norm(q_mul(parent.q, local.q)), local.s * parent.s)


def bend_direction(root, mid, end):
    """Unit vector from the root-end line towards the middle joint, or None if the limb is straight."""
    axis = v_sub(end, root)
    length = v_len(axis)
    if length < 1e-6:
        return None
    axis = v_scale(axis, 1.0 / length)
    to_mid = v_sub(mid, root)
    bend = v_sub(to_mid, v_scale(axis, v_dot(to_mid, axis)))
    reach = v_dist(root, mid) + v_dist(mid, end)
    if v_len(bend) < 0.02 * reach:          # nearly straight: no reliable bend direction
        return None
    return v_scale(bend, 1.0 / v_len(bend))


def pole_error_deg(root, mid, end, pole):
    """Angle between the limb's bend direction and the direction of the pole, or None if straight."""
    bend = bend_direction(root, mid, end)
    if bend is None:
        return None
    axis = v_sub(end, root)
    axis = v_scale(axis, 1.0 / v_len(axis))
    to_pole = v_sub(pole, root)
    side = v_sub(to_pole, v_scale(axis, v_dot(to_pole, axis)))
    if v_len(side) < 1e-6:
        return 180.0
    side = v_scale(side, 1.0 / v_len(side))
    return math.degrees(math.acos(max(-1.0, min(1.0, v_dot(bend, side)))))


# ------------------------------------------------------------------ sequence, rig, channels
class Context:
    def __init__(self):
        self.sequence = None
        self.rig = None
        self.track = None
        self.base = None            # the baked (row 0) section
        self.hierarchy = None
        self.controls = set()
        self.channels = {}          # control name -> [(channel name, channel)]
        self.channel_names = []
        self.first = 0              # first and last keyed frame
        self.last = 0
        self.bake_fills_ik = True
        self._rest = {}             # control name -> rest Pose (or None)
        self._world = {}            # control name -> {frame: unreal.Transform}

    # ---- world transforms (cached; every call makes the engine evaluate the rig)
    def world(self, name, frames):
        cache = self._world.setdefault(name, {})
        missing = [f for f in frames if f not in cache]
        if missing:
            result = LIB.get_control_rig_world_transforms(
                self.sequence, self.rig, name, [unreal.FrameNumber(f) for f in missing])
            for f, t in zip(missing, list(result)):
                cache[f] = t
        return [cache[f] for f in frames]

    def forget(self, name):
        self._world.pop(name, None)

    def set_world(self, name, frames, transforms):
        """Key `name` at each frame so it sits at the given world transform."""
        self.forget(name)
        numbers = [unreal.FrameNumber(f) for f in frames]
        batch = getattr(LIB, "set_control_rig_world_transforms", None)
        if batch is not None:
            try:
                batch(self.sequence, self.rig, name, numbers, transforms)
                return "one call"
            except TypeError as err:        # different arguments in this engine: go frame by frame
                out(f"  (set_control_rig_world_transforms not usable here: {err})")
        for number, transform in zip(numbers, transforms):
            LIB.set_control_rig_world_transform(self.sequence, self.rig, name, number, transform)
        return "frame by frame"

    # ---- rest pose
    def rest(self, name):
        if name not in self._rest:
            pose = None
            if self.hierarchy is not None:
                try:
                    key = unreal.RigElementKey(type=unreal.RigElementType.CONTROL, name=name)
                    pose = to_pose(self.hierarchy.get_global_transform(key, True))
                except Exception as err:
                    out(f"  (rest pose of {name} not readable: {type(err).__name__}: {err})")
            self._rest[name] = pose
        return self._rest[name]

    # ---- channels of the baked section
    def load_channels(self):
        self.channels = {}
        self.channel_names = []
        for channel in list(self.base.get_all_channels()):
            name = channel_name(channel)
            self.channel_names.append(name)
            self.channels.setdefault(name.split(".", 1)[0], []).append((name, channel))

    def channels_of(self, control):
        return self.channels.get(control, [])


def channel_name(channel):
    try:
        return str(channel.channel_name)
    except Exception:
        try:
            return str(channel.get_editor_property("channel_name"))
        except Exception:
            return ""


def key_frame(key):
    return int(key.get_time().frame_number.value)


def control_names(rig):
    hierarchy = rig.get_hierarchy()
    for getter in (lambda: hierarchy.get_controls(True), lambda: hierarchy.get_controls()):
        try:
            return hierarchy, {str(getattr(k, "name", k)) for k in getter()}
        except Exception:
            continue
    return hierarchy, set()


def open_context():
    if LIB is None or SEQ_LIB is None:
        raise Stop("This engine doesn't expose the Control Rig Sequencer functions to Python. "
                   "Enable the Control Rig and Python Editor Script plugins.")
    ctx = Context()
    ctx.sequence = SEQ_LIB.get_current_level_sequence()
    if ctx.sequence is None:
        raise Stop("No Level Sequence is open in Sequencer. Open the sequence and run again.")

    wanted = {limb["ik"] for limb in LIMBS.values()}
    best = None
    for proxy in list(LIB.get_control_rigs(ctx.sequence)):
        rig, track = proxy.control_rig, proxy.track
        if rig is None or track is None:
            continue
        try:
            hierarchy, names = control_names(rig)
        except Exception:
            hierarchy, names = None, set()
        score = len(wanted & names)
        if best is None or score > best[0]:
            best = (score, rig, track, hierarchy, names)
    if best is None:
        raise Stop("This sequence has no Control Rig track. Bake the Body animation to "
                   "MetaHuman_ControlRig first (right-click the Body track > Bake To Control Rig).")
    _, ctx.rig, ctx.track, ctx.hierarchy, ctx.controls = best

    sections = list(ctx.track.get_sections())
    if not sections:
        raise Stop("The Control Rig track has no section. Bake the animation to the Control Rig first.")
    ctx.base = min(sections, key=lambda s: s.get_row_index())
    ctx.load_channels()
    if not ctx.controls:                    # hierarchy not readable: take the names from the channels
        ctx.controls = set(ctx.channels)

    missing = sorted(n for limb in LIMBS.values()
                     for n in (limb["switch"], limb["ik"], limb["pole"]) + limb["fk"]
                     if n not in ctx.controls)
    if missing:
        raise Stop(f"'{ctx.rig.get_name()}' doesn't have these controls: {', '.join(missing)}. "
                   "This script needs the animation baked to MetaHuman_ControlRig "
                   "(not FKControlRig).")

    # Keyed range: from the baked FK hand, else from the first channel that has keys
    frames = []
    candidates = ctx.channels_of(LIMBS["arm_l"]["fk"][2]) or [c for cs in ctx.channels.values() for c in cs]
    for _, channel in candidates:
        keys = list(channel.get_keys())
        if len(keys) > 1:
            frames = [key_frame(k) for k in keys]
            break
    if frames:
        ctx.first, ctx.last = min(frames), max(frames)
    else:
        ctx.first, ctx.last = int(ctx.sequence.get_playback_start()), int(ctx.sequence.get_playback_end()) - 1
    if ctx.last <= ctx.first:
        raise Stop("The Control Rig track has no animation keys. Bake the animation to it first.")
    return ctx


def describe(ctx):
    header("Sequence")
    out(f"Sequence:     {ctx.sequence.get_path_name()}")
    out(f"Control Rig:  {ctx.rig.get_name()}  ({len(ctx.controls)} controls)")
    out(f"Sections:     {len(list(ctx.track.get_sections()))} on the Control Rig track; "
        f"editing row {ctx.base.get_row_index()} ({len(ctx.channel_names)} channels)")
    out(f"Keyed frames: {ctx.first} .. {ctx.last}")
    start, end = int(ctx.sequence.get_playback_start()), int(ctx.sequence.get_playback_end())
    out(f"Playback:     {start} .. {end}")
    if end < ctx.last:
        out(f"  NOTE: the playback range stops at {end} but the keys run to {ctx.last}. "
            "The fixes cover every key; set the playback end to see the whole clip.")
    if not ctx.channels_of(LIMBS["arm_l"]["switch"]):
        out("  NOTE: channel names not recognised; using the slower frame-by-frame method. First names:")
        hits = [n for n in ctx.channel_names if "fk_ik_switch" in n or "foot_l_ik" in n]
        for name in (hits or ctx.channel_names)[:20]:
            out(f"    {name}")
    try:
        for binding in list(ctx.sequence.get_bindings()):
            for track in list(binding.get_tracks()):
                if "SkeletalAnimation" in track.get_class().get_name():
                    active = [s.is_active() for s in list(track.get_sections())]
                    out(f"Animation track on '{binding.get_display_name()}': "
                        f"{len(active)} section(s), active: {active}")
                    if any(active):
                        out("  If this track isn't muted, mute or delete it so only the Control Rig "
                            "drives the body.")
    except Exception as err:
        out(f"  (couldn't inspect the other tracks: {type(err).__name__}: {err})")


# ------------------------------------------------------------------ keys
def value_at(channel, keys, frame):
    """Value of the channel's key at `frame` (else the last key before it, else the first key)."""
    best = None
    for key in keys:
        f = key_frame(key)
        if f == frame:
            return key.get_value()
        if f < frame and (best is None or f > best[0]):
            best = (f, key)
    if best is not None:
        return best[1].get_value()
    if keys:
        return min(keys, key=key_frame).get_value()
    try:
        if channel.has_default():
            return channel.get_default()
    except Exception:
        pass
    return None


def hold(ctx, control, frame=None, value=None, values=None):
    """
    Replace every key of `control` with one key, so it holds a single value for the whole clip:
    `value` when given, else the channel's entry in `values` ({channel name: value}), else its
    own value at `frame`. Returns {channel name: held value}, or None when the control's
    channels weren't found.
    """
    channels = ctx.channels_of(control)
    if not channels:
        return None
    held = {}
    for name, channel in channels:
        keys = list(channel.get_keys())
        if value is not None:
            target = value
        elif values is not None and name in values:
            target = values[name]
        else:
            target = value_at(channel, keys, frame)
        for key in keys:
            channel.remove_key(key)
        if target is None:
            continue
        channel.set_default(target)
        channel.add_key(unreal.FrameNumber(ctx.first), target)
        held[name] = target
    ctx.base.modify()           # marks the section as changed, so Sequencer re-evaluates it
    return held


def switch_values(ctx, control):
    """Distinct values keyed on a switch (or its unkeyed value)."""
    values = set()
    for _, channel in ctx.channels_of(control):
        keys = list(channel.get_keys())
        if keys:
            values |= {bool(k.get_value()) for k in keys}
        else:
            try:
                values.add(bool(channel.get_default()))
            except Exception:
                pass
    if not values:
        values.add(bool(LIB.get_local_control_rig_bool(ctx.sequence, ctx.rig, control,
                                                      unreal.FrameNumber(ctx.first))))
    return sorted(values)


# ------------------------------------------------------------------ IK match
def sample_frames(ctx):
    count = ctx.last - ctx.first + 1
    step = max(1, count // max(1, CHECK_SAMPLES))
    frames = list(range(ctx.first, ctx.last + 1, step))
    if frames[-1] != ctx.last:
        frames.append(ctx.last)
    return frames


def check_limb(ctx, limb, frames):
    """How well the IK control and pole of one limb follow its FK chain, on the sampled frames."""
    fk_root, fk_mid, fk_end = (ctx.world(name, frames) for name in limb["fk"])
    ik = ctx.world(limb["ik"], frames)
    pole = ctx.world(limb["pole"], frames)

    rest_ik, rest_fk = ctx.rest(limb["ik"]), ctx.rest(limb["fk"][2])
    rest = relative(rest_ik, rest_fk) if rest_ik and rest_fk else None

    rels = [relative(to_pose(i), to_pose(f)) for i, f in zip(ik, fk_end)]
    spread = max(v_dist(r.p, rels[0].p) for r in rels)
    result = {"rest": rest, "spread_cm": spread, "off_cm": None, "off_deg": None,
              "ik_sample": ik[0], "pole_sample": pole[0]}
    if rest is not None:
        result["off_cm"] = max(v_dist(r.p, rest.p) for r in rels)
        result["off_deg"] = max(q_angle_deg(r.q, rest.q) for r in rels)

    errors = [pole_error_deg(to_pose(a).p, to_pose(b).p, to_pose(c).p, to_pose(p).p)
              for a, b, c, p in zip(fk_root, fk_mid, fk_end, pole)]
    errors = [e for e in errors if e is not None]
    result["pole_deg"] = max(errors) if errors else None

    if result["off_cm"] is not None and result["off_cm"] <= TOLERANCE_CM:
        result["status"] = "matched"
    elif spread <= TOLERANCE_CM:
        result["status"] = "follows"        # rigid with the FK chain, at a different fixed offset
    else:
        result["status"] = "loose"          # not following the FK chain
    return result


def snap_ik(ctx, limb, check, frames):
    """IK control = FK end control carried through their rest-pose difference, on every frame."""
    transforms = []
    for fk_t in ctx.world(limb["fk"][2], frames):
        target = compose(check["rest"], to_pose(fk_t))
        transform = unreal.Transform()
        transform.translation = unreal.Vector(*target.p)
        transform.rotation = unreal.Quat(*target.q)
        transform.scale3d = check["ik_sample"].scale3d          # the control keeps its own scale
        transforms.append(transform)
    return ctx.set_world(limb["ik"], frames, transforms)


def place_pole(ctx, limb, check, frames):
    """Pole = middle joint pushed out along the bend direction, on every frame."""
    fk_root, fk_mid, fk_end = (ctx.world(name, frames) for name in limb["fk"])
    points, last_bend = [], None
    for a, b, c in zip(fk_root, fk_mid, fk_end):
        root, mid, end = to_pose(a).p, to_pose(b).p, to_pose(c).p
        bend = bend_direction(root, mid, end) or last_bend      # straight limb: keep the last direction
        last_bend = bend
        points.append((mid, bend, v_dist(root, mid)))
    first_bend = next((bend for _, bend, _ in points if bend is not None), None)
    if first_bend is None:
        return None                                             # the limb never bends
    transforms = []
    for mid, bend, length in points:
        target = v_add(mid, v_scale(bend or first_bend, max(length, 10.0)))
        transform = unreal.Transform()
        transform.translation = unreal.Vector(*target)
        transform.rotation = check["pole_sample"].rotation      # the pole keeps its rotation and scale
        transform.scale3d = check["pole_sample"].scale3d
        transforms.append(transform)
    return ctx.set_world(limb["pole"], frames, transforms)


def fmt(value, unit):
    return "   n/a" if value is None else f"{value:6.1f} {unit}"


def match_ik(ctx, first_pass):
    """
    Check every limb's IK control and pole against its FK chain and snap the ones that don't
    follow. Runs before the IK switch and again after it, because a rig can move its IK
    controls along with the FK chain while a limb is in FK mode.
    """
    frames = sample_frames(ctx)
    if first_pass:
        out(f"Checked on {len(frames)} frames. 'off' = distance from where the FK chain puts the IK control;")
        out("'spread' = how much that distance changes over the clip; 'pole' = angle off the bend direction.")
        out()
    else:
        for limb in LIMBS.values():             # read the IK controls again, now in IK mode
            ctx.forget(limb["ik"])
            ctx.forget(limb["pole"])
    checks = {}
    for name, limb in LIMBS.items():
        progress(f"Checking {name}")
        c = checks[name] = check_limb(ctx, limb, frames)
        out(f"  {name}: {c['status']:<8} off {fmt(c['off_cm'], 'cm')} / {fmt(c['off_deg'], 'deg')}   "
            f"spread {fmt(c['spread_cm'], 'cm')}   pole {fmt(c['pole_deg'], 'deg')}")

    if MATCH_IK == "never":
        out("\n  MATCH_IK = 'never': nothing snapped.")
        return checks

    if first_pass:
        # A still limb can look like it follows the FK chain when it was simply never baked.
        # The arms always move, so they tell whether the bake fills in the IK controls at all.
        ctx.bake_fills_ik = all(checks[n]["status"] != "loose" for n in ("arm_l", "arm_r"))
    every_frame = list(range(ctx.first, ctx.last + 1))
    out()
    changed = False
    for name, limb in LIMBS.items():
        progress(f"Matching {name}")
        c = checks[name]
        part = "hand" if name.startswith("arm") else "foot"
        if c["status"] == "matched":
            snap = MATCH_IK == "always" and first_pass
        elif c["status"] == "loose":
            snap = True
        else:
            snap = (MATCH_IK == "always" and first_pass) or (name.startswith("leg") and not ctx.bake_fills_ik)
        if snap and c["rest"] is None:
            out(f"  {name}: needs snapping, but the rig's rest pose couldn't be read - left as baked")
            snap = False
        if snap:
            how = snap_ik(ctx, limb, c, every_frame)
            out(f"  {name}: {limb['ik']} snapped to the FK chain on {len(every_frame)} frames ({how})")
            changed = True
        else:
            if c["status"] == "follows":
                out(f"  {name}: {limb['ik']} follows the FK chain at a fixed offset - left as baked")
            if c["off_deg"] is not None and c["off_deg"] > 5.0:
                out(f"  {name}: {limb['ik']} is rotated {c['off_deg']:.0f} deg off the FK control; if the "
                    f"{part} looks twisted in IK, set MATCH_IK = 'always'")

        pole_off = c["pole_deg"] is not None and c["pole_deg"] > POLE_TOLERANCE_DEG
        if pole_off or (MATCH_IK == "always" and first_pass):
            how = place_pole(ctx, limb, c, every_frame)
            out(f"  {name}: {limb['pole']} " + (f"placed on the bend direction ({how})" if how
                                               else "left alone (the limb never bends)"))
            changed = changed or bool(how)
    if not changed:
        out("  Nothing needed snapping." if first_pass else "  Still matched in IK mode; nothing snapped.")
    else:
        ctx.load_channels()         # the snap wrote keys; read the channels again
    return checks


# ------------------------------------------------------------------ IK switch
def ik_switch(ctx):
    limbs = [n for n in LIMBS if (n.startswith("arm") and IK_ARMS) or (n.startswith("leg") and IK_LEGS)]
    if not limbs:
        out("Skipped (IK_ARMS and IK_LEGS are False).")
        return
    for name in limbs:
        control = LIMBS[name]["switch"]
        before = switch_values(ctx, control)
        if hold(ctx, control, value=bool(IK_VALUE)) is None:
            for f in range(ctx.first, ctx.last + 1):
                LIB.set_local_control_rig_bool(ctx.sequence, ctx.rig, control,
                                               unreal.FrameNumber(f), bool(IK_VALUE))
        out(f"  {control}: was {before}, now {bool(IK_VALUE)} for the whole clip")
    out(f"  IK is taken to be {bool(IK_VALUE)}. After the run, move hand_l_ik_ctrl: if the arm doesn't "
        "follow it, undo, set IK_VALUE = " + str(not bool(IK_VALUE)) + " at the top of the script and run again.")


# ------------------------------------------------------------------ plant feet
def hold_by_frames(ctx, control, frame):
    """Fallback when channels can't be read: write the frame's value onto every frame."""
    number = unreal.FrameNumber(frame)
    value = LIB.get_local_control_rig_euler_transform(ctx.sequence, ctx.rig, control, number)
    for f in range(ctx.first, ctx.last + 1):
        LIB.set_local_control_rig_euler_transform(ctx.sequence, ctx.rig, control,
                                                  unreal.FrameNumber(f), value)


def feet_frame(ctx):
    frame = ctx.first if FEET_FRAME is None else int(FEET_FRAME)
    if not ctx.first <= frame <= ctx.last:
        raise Stop(f"FEET_FRAME ({frame}) is outside the keyed frames {ctx.first}..{ctx.last}.")
    return frame


def planted_controls():
    """The controls that plant_feet holds still."""
    if not PLANT_FEET:
        return []
    controls = [LIMBS[n]["ik"] for n in ("leg_l", "leg_r")]
    if HOLD_KNEE_POLES:
        controls += [LIMBS[n]["pole"] for n in ("leg_l", "leg_r")]
    return controls


def plant_feet(ctx, neutral=None):
    if not PLANT_FEET:
        out("Skipped (PLANT_FEET = False).")
        return
    frame = feet_frame(ctx)
    stance = neutral if (neutral and FEET_AT_NEUTRAL) else None
    for control in planted_controls():
        from_neutral = bool(stance) and any(n in stance for n, _ in ctx.channels_of(control))
        held = hold(ctx, control, frame=frame, values=stance)
        if held is None:
            hold_by_frames(ctx, control, frame)
            out(f"  {control}: held at its frame {frame} value (written on every frame)")
        else:
            location = [held.get(f"{control}.Location.{axis}") for axis in "XYZ"]
            where = (" at (" + ", ".join(f"{v:.1f}" for v in location) + ")"
                     if all(v is not None for v in location) else "")
            what = "the neutral pose's value" if from_neutral else f"their frame {frame} value"
            out(f"  {control}: {len(held)} channels now hold {what}{where}")
        ctx.forget(control)

    # How still are the feet now, in the world?
    frames = sample_frames(ctx)
    for name in ("leg_l", "leg_r"):
        control = LIMBS[name]["ik"]
        points = [to_pose(t).p for t in ctx.world(control, frames)]
        drift = max(v_dist(p, points[0]) for p in points)
        out(f"  {control}: moves {drift:.2f} cm over the clip"
            + ("" if drift < 0.1 else "  <- its parent control is animated; tell me if the feet still slide"))
        ctx.forget(control)


# ------------------------------------------------------------------ layers
def is_additive(section):
    try:
        return "ADDITIVE" in str(section.get_blend_type()).upper()
    except Exception:
        return False


def is_our_layer(section):
    """True for a layer this script made (recognised by its tint)."""
    try:
        tint = section.get_color_tint()
        return is_additive(section) and (tint.r, tint.g, tint.b, tint.a) == LAYER_TINT
    except Exception:
        return False


def mark_layer(ctx, section, name):
    """Tint and label a layer made by this script. Returns False if it couldn't be tinted."""
    try:
        ctx.track.set_track_row_display_name(name, section.get_row_index())
    except Exception:
        pass
    try:
        r, g, b, a = LAYER_TINT
        section.set_color_tint(unreal.Color(r=r, g=g, b=b, a=a))
        return is_our_layer(section)
    except Exception:
        return False


def prepare_layers(ctx):
    """
    Remove the layers an earlier run of this script made, and switch any other layers off
    while the script works (they would throw off the IK check). Returns the layers to switch
    back on afterwards.
    """
    switched_off = []
    for section in list(ctx.track.get_sections()):
        if section == ctx.base:
            continue
        if is_our_layer(section):
            row = section.get_row_index()
            ctx.track.remove_section(section)
            try:
                ctx.track.set_track_row_display_name("", row)
            except Exception:
                pass
            out(f"Removed the '{NEUTRAL_LAYER}' layer from an earlier run (it is rebuilt below).")
        elif section.is_active():
            section.set_is_active(False)
            switched_off.append(section)
    if switched_off:
        out(f"{len(switched_off)} other layer(s) on the Control Rig track are switched off while the "
            "script works, and back on at the end.")
    return switched_off


# ------------------------------------------------------------------ neutral pose
def neutral_path():
    if os.path.isabs(NEUTRAL_FILE):
        candidates = [NEUTRAL_FILE]
    else:
        candidates = []
        if "__file__" in globals():
            candidates.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), NEUTRAL_FILE))
        saved = unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_saved_dir())
        candidates.append(os.path.join(os.path.normpath(saved), NEUTRAL_FILE))
    return next((p for p in candidates if os.path.isfile(p)), None)


def load_neutral(ctx):
    """{channel name: value} from neutral_pose.json, or None when there is no file."""
    if not NEUTRAL_POSE:
        return None
    path = neutral_path()
    if path is None:
        out(f"Neutral pose:  no {NEUTRAL_FILE} found, so the neutral pose step is skipped. "
            "Run capture_neutral_pose.py once to make it.")
        return None
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        values = {str(k): float(v) for k, v in dict(data.get("values") or {}).items()}
    except Exception as err:
        raise Stop(f"Couldn't read the neutral pose file {path}: {err}")
    if not values:
        raise Stop(f"The neutral pose file {path} has no values in it. Capture it again.")
    out(f"Neutral pose:  {path}  ({len(values)} channels, captured {data.get('captured', '?')} "
        f"from frame {data.get('frame', '?')})")
    if data.get("rig") and str(data["rig"]) != ctx.rig.get_name():
        out(f"  NOTE: it was captured on '{data['rig']}', this sequence uses '{ctx.rig.get_name()}'.")
    if EASE_IN < 0 or EASE_OUT < 0 or ctx.first + EASE_IN >= ctx.last - EASE_OUT:
        raise Stop(f"EASE_IN ({EASE_IN}) and EASE_OUT ({EASE_OUT}) don't fit in a clip of "
                   f"{ctx.last - ctx.first + 1} frames.")
    return values


NEUTRAL_CHECK_CONTROLS = ("hand_l_ik_ctrl", "hand_r_ik_ctrl", "head_ctrl", "body_ctrl")
SMOOTH_CHECK_CONTROLS = ("hand_l_ik_ctrl", "hand_r_ik_ctrl", "hand_l_fk_ctrl", "hand_r_fk_ctrl",
                         "lowerarm_l_fk_ctrl", "lowerarm_r_fk_ctrl", "head_ctrl", "body_ctrl",
                         "index_01_l_ctrl", "index_01_r_ctrl")
ROTATION_TEST_CONTROLS = ("head_ctrl", "neck_01_ctrl", "spine_02_ctrl", "clavicle_l_ctrl", "body_ctrl")
ROTATION_AXES = {"X": 0, "Y": 1, "Z": 2, "Roll": 0, "Pitch": 1, "Yaw": 2}


# ---- rotations
# A control's three rotation channels are an Unreal rotator: x = roll, y = pitch, z = yaw.
# Those three numbers are a poor thing to blend: the same rotation can be written in several
# ways, and a clip's angles can jump between two frames while the joint barely moves. So the
# neutral pose is blended as a rotation (quaternion), and only the result is turned back into
# three angles. check_rotation_math() tests this maths against the engine before it is used.
def wrap180(angle):
    return (angle + 180.0) % 360.0 - 180.0


def flip_angles(angles):
    """The other way of writing the same rotation with three angles."""
    return [angles[0] + 180.0, 180.0 - angles[1], angles[2] + 180.0]


def rotator_quat(angles):
    """Quaternion (x, y, z, w) of a rotator given as (roll, pitch, yaw) in degrees."""
    r, p, y = (math.radians(math.fmod(a, 360.0)) / 2.0 for a in angles)
    sr, cr, sp, cp, sy, cy = math.sin(r), math.cos(r), math.sin(p), math.cos(p), math.sin(y), math.cos(y)
    return (cr * sp * sy - sr * cp * cy, -cr * sp * cy - sr * cp * sy,
            cr * cp * sy - sr * sp * cy, cr * cp * cy + sr * sp * sy)


def quat_rotator(q):
    """(roll, pitch, yaw) in degrees of a quaternion."""
    x, y, z, w = q
    test = z * x - w * y
    yaw = math.degrees(math.atan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z)))
    if test < -0.4999995:
        return [wrap180(-yaw - math.degrees(2.0 * math.atan2(x, w))), -90.0, yaw]
    if test > 0.4999995:
        return [wrap180(yaw - math.degrees(2.0 * math.atan2(x, w))), 90.0, yaw]
    return [math.degrees(math.atan2(-2.0 * (w * x + y * z), 1.0 - 2.0 * (x * x + y * y))),
            math.degrees(math.asin(max(-1.0, min(1.0, 2.0 * test)))), yaw]


def q_slerp(a, b, t):
    """Rotation a fraction t of the way from a to b, the short way round."""
    dot = sum(i * j for i, j in zip(a, b))
    if dot < 0.0:
        b, dot = tuple(-c for c in b), -dot
    if dot > 0.9995:
        return q_norm(tuple(i + (j - i) * t for i, j in zip(a, b)))
    theta = math.acos(min(1.0, dot))
    wa, wb = math.sin((1.0 - t) * theta) / math.sin(theta), math.sin(t * theta) / math.sin(theta)
    return q_norm(tuple(i * wa + j * wb for i, j in zip(a, b)))


def nearest_angles(angles, target):
    """The way of writing the rotation `angles` whose three numbers are nearest to `target`."""
    options = []
    for option in (angles, flip_angles(angles)):
        option = [t + wrap180(o - t) for o, t in zip(option, target)]
        options.append((sum((o - t) ** 2 for o, t in zip(option, target)), option))
    return min(options)[1]


def plain_rotator(ctx, control):
    """
    True when the control's rotation channels are a plain Unreal rotator (roll, pitch, yaw),
    False when it uses its own rotation order, None when that can't be read.
    """
    try:
        key = unreal.RigElementKey(type=unreal.RigElementType.CONTROL, name=control)
        getter = getattr(ctx.hierarchy, "get_control_settings", None)
        settings = getter(key) if getter is not None else ctx.hierarchy.find_control(key).settings
        return not bool(settings.use_preferred_rotation_order)
    except Exception:
        return None


def check_rotation_math(ctx, layer, layer_channels, base_channels):
    """
    Test the rotation maths above against the engine: turn a control by known angles on the
    (still empty) layer, see how far the engine turns it, and compare with what the maths says.
    The test keys are removed again. Returns True when they agree.
    """
    tests = ((30.0, 40.0, 50.0), (-70.0, 20.0, 110.0), (10.0, -80.0, 45.0))
    for control in ROTATION_TEST_CONTROLS:
        names = [f"{control}.Rotation.{axis}" for axis in "XYZ"]
        if not all(n in layer_channels and n in base_channels for n in names):
            continue
        angles = [sample(ctx, base_channels[n], [ctx.first])[ctx.first] for n in names]
        if any(v is None for v in angles):
            continue
        ctx.forget(control)
        untouched = to_pose(ctx.world(control, [ctx.first])[0])

        def posed(target):
            keys = [layer_channels[n].add_key(unreal.FrameNumber(ctx.first), t - a)
                    for n, t, a in zip(names, target, angles)]
            layer.modify()
            ctx.forget(control)
            try:
                return to_pose(ctx.world(control, [ctx.first])[0])
            finally:
                for n, key in zip(names, keys):
                    layer_channels[n].remove_key(key)
                layer.modify()
                ctx.forget(control)

        targets = [[a + d for a, d in zip(angles, test)] for test in tests]
        poses = [posed(t) for t in targets]
        if q_angle_deg(poses[0].q, untouched.q) < 20.0:
            continue                                    # this control doesn't answer to its own channels here
        quats = [rotator_quat(t) for t in targets]
        start = rotator_quat(angles)
        engine = [q_angle_deg(p.q, untouched.q) for p in poses] + [q_angle_deg(poses[0].q, poses[1].q),
                                                                  q_angle_deg(poses[1].q, poses[2].q)]
        maths = [q_angle_deg(q, start) for q in quats] + [q_angle_deg(quats[0], quats[1]),
                                                           q_angle_deg(quats[1], quats[2])]
        worst = max(abs(e - m) for e, m in zip(engine, maths))
        rewritten = q_angle_deg(posed(flip_angles(targets[0])).q, poses[0].q)
        ok = worst < 0.5 and rewritten < 0.5
        out(f"  Rotation maths checked on {control}: {'agrees with' if ok else 'DOES NOT agree with'} the engine "
            f"(largest difference {worst:.2f} deg over 5 test turns; re-written angles {rewritten:.2f} deg off).")
        return ok
    out("  Rotation maths: no control available to check it on.")
    return False


def smooth(t):
    t = max(0.0, min(1.0, t))
    return t * t * (3.0 - 2.0 * t)


def ease_weights(ctx):
    """{frame: share of the neutral pose in the result} for every frame of the two eases."""
    weights = {}
    if EASE_IN > 0:
        for f in range(ctx.first, ctx.first + EASE_IN + 1):
            weights[f] = 1.0 - smooth((f - ctx.first) / float(EASE_IN))
    if EASE_OUT > 0:
        start = ctx.last - EASE_OUT
        for f in range(start, ctx.last + 1):
            weights[f] = smooth((f - start) / float(EASE_OUT))
    return weights


def sample(ctx, channel, frames):
    """{frame: the channel's value on that frame} (None where it has no value)."""
    keys = list(channel.get_keys())
    if not keys:
        value = value_at(channel, keys, ctx.first)
        return {f: (None if value is None else float(value)) for f in frames}
    count = ctx.last - ctx.first + 1
    if (len(keys) == count and key_frame(keys[0]) == ctx.first and key_frame(keys[-1]) == ctx.last
            and key_frame(keys[count // 2]) == ctx.first + count // 2):
        return {f: float(keys[f - ctx.first].get_value()) for f in frames}     # baked: one key per frame
    rows = sorted((key_frame(k), float(k.get_value())) for k in keys)
    result = {}
    for f in frames:                                    # sparse keys: the last key at or before the frame
        earlier = [v for kf, v in rows if kf <= f]
        result[f] = earlier[-1] if earlier else rows[0][1]
    return result


def neutral_layer_values(ctx, neutral, base_channels, weights, maths_ok):
    """
    {channel name: {frame: value to key on the additive layer}} for every frame of the two
    eases, so that clip + layer = a blend of the clip's pose on that frame and the neutral
    pose, with `weights[frame]` of neutral in it. Positions and other numbers blend as numbers;
    rotations blend as rotations when the maths has been checked against the engine
    (`maths_ok`), else angle by angle. Also returns report lines and the largest moves.
    """
    frames = sorted(weights)
    clip, layer = {}, {}
    for name in neutral:
        if name in base_channels:
            row = sample(ctx, base_channels[name], frames)
            if all(v is not None for v in row.values()):
                clip[name] = row
                layer[name] = {f: weights[f] * (neutral[name] - row[f]) for f in frames}

    groups = {}
    for name in clip:
        if ".Rotation." in name:
            control, axis = name.split(".Rotation.", 1)
            if axis in ROTATION_AXES:
                groups.setdefault(control, {})[ROTATION_AXES[axis]] = name

    ends = {f: [] for f in (ctx.first, ctx.last) if weights.get(f, 0.0) > 0.0}
    jumps, by_angle = [], 0
    for control, axes in groups.items():
        names = [axes.get(i) for i in range(3)]
        if not all(names):                              # a control with fewer than three angles
            for name in axes.values():
                layer[name] = {f: weights[f] * wrap180(neutral[name] - clip[name][f]) for f in frames}
            continue
        pose = [neutral[n] for n in names]
        pose_q = rotator_quat(pose)
        rotator = False
        if not maths_ok:
            by_angle += 1
            rotator = bool(plain_rotator(ctx, control))
        previous = None
        for f in frames:
            angles = [clip[n][f] for n in names]
            angles_q = rotator_quat(angles)
            weight = weights[f]
            if weight <= 0.0:
                target = angles
            elif maths_ok:
                target = nearest_angles(quat_rotator(q_slerp(angles_q, pose_q, weight)), angles)
            elif rotator:                               # unchecked maths, but a plain rotator: angle by angle
                target = [a + weight * (p - a) for a, p in zip(angles, nearest_angles(pose, angles))]
            else:                                       # unknown rotation order: only whole turns removed
                target = [a + weight * wrap180(p - a) for a, p in zip(angles, pose)]
            for name, a, t in zip(names, angles, target):
                layer[name][f] = t - a
            if f in ends:
                ends[f].append((q_angle_deg(angles_q, pose_q), control))
            if previous is not None and f == previous[0] + 1:
                raw = max(abs(a - b) for a, b in zip(angles, previous[1]))
                if raw > 45.0:                          # the clip's own angles jump between two frames
                    jumps.append((raw, q_angle_deg(angles_q, previous[2]), control, previous[0], f))
            previous = (f, angles, angles_q)

    lines = []
    for f, label in ((ctx.first, "first"), (ctx.last, "last")):
        rows = [r for r in sorted(ends.get(f, []), reverse=True)[:6] if r[0] > 1.0]
        if rows:
            lines.append(f"  Largest turns to reach the neutral pose on the {label} frame: "
                         + "; ".join(f"{c} {angle:.0f} deg" for angle, c in rows))
    if jumps:
        real = sorted((j for j in jumps if j[1] > 30.0), key=lambda j: -j[1])
        written = sorted((j for j in jumps if j[1] <= 30.0), reverse=True)
        lines.append(f"  The clip's own angles jump between two frames inside the eases {len(jumps)} time(s):")
        if real:
            lines.append(f"    {len(real)} are real: the joint itself turns that far in one frame of the clip")
            for raw, turn, control, a, b in real[:8]:
                lines.append(f"      {control}: frame {a} to {b}, the joint turns {turn:.0f} deg")
        if written:
            lines.append(f"    {len(written)} are only in the numbers: the same rotation written another way")
            for raw, turn, control, a, b in written[:8]:
                lines.append(f"      {control}: frame {a} to {b}, angles change by {raw:.0f}, the joint turns {turn:.1f} deg")
    if by_angle:
        lines.append(f"  NOTE: {by_angle} rotations were blended angle by angle, because the rotation maths "
                     "couldn't be confirmed against the engine. Jumps in the clip's angles can show as pops.")

    moves = {f: {} for f in ends}
    for name, row in layer.items():
        if ".Location." in name:
            control = name.split(".", 1)[0]
            for f in moves:
                moves[f][control] = max(moves[f].get(control, 0.0), abs(neutral[name] - clip[name][f]))
    return layer, lines, moves


def add_linear_key(channel, frame, value):
    linear = getattr(getattr(unreal, "MovieSceneKeyInterpolation", None), "LINEAR", None)
    if linear is not None:
        try:
            return channel.add_key(unreal.FrameNumber(frame), value, interpolation=linear)
        except TypeError:
            pass
    return channel.add_key(unreal.FrameNumber(frame), value)


def neutral_pose(ctx, neutral):
    """
    Additive layer that blends the clip with the neutral pose: all neutral on the first and
    last frame, fading to all clip EASE_IN frames after the start and from EASE_OUT frames
    before the end. In between the layer is zero, so the captured motion is untouched.

    The layer has a key on every frame of the two eases. On each of those frames the result is
    a mix of the neutral pose and the clip's pose on that same frame, so a jump in the clip
    (frame 0 is often a different pose from frame 1) can't pull the result off the neutral pose.
    """
    if neutral is None:
        out(f"Skipped: {'NEUTRAL_POSE = False' if not NEUTRAL_POSE else 'no ' + NEUTRAL_FILE}.")
        return None
    in_frame, out_frame = ctx.first + EASE_IN, ctx.last - EASE_OUT
    middle_frame = (in_frame + out_frame) // 2
    check_frames = [ctx.first, in_frame, middle_frame, out_frame, ctx.last]
    check_controls = [c for c in NEUTRAL_CHECK_CONTROLS if c in ctx.controls]
    for control in check_controls:
        ctx.forget(control)
    before = {c: [to_pose(t) for t in ctx.world(c, check_frames)] for c in check_controls}

    rows = [s.get_row_index() for s in ctx.track.get_sections()]
    layer = ctx.track.add_section()
    layer.set_blend_type(unreal.MovieSceneBlendType.ADDITIVE)
    layer.set_row_index(max(rows) + 1)
    layer.set_range(ctx.first, ctx.last + 1)
    marked = mark_layer(ctx, layer, NEUTRAL_LAYER)

    layer_channels = {}
    reset = 0
    for channel in list(layer.get_all_channels()):
        name = channel_name(channel)
        if name.split(".", 1)[0] not in ctx.controls:
            continue                                    # the layer's own Weight channel
        layer_channels[name] = channel
        try:                                            # an additive layer must add nothing by default
            if channel.has_default() and abs(float(channel.get_default())) > 1e-9:
                channel.set_default(0.0)
                reset += 1
        except Exception:
            pass

    planted = set(planted_controls())
    base_channels = {name: channel for chans in ctx.channels.values() for name, channel in chans}
    maths_ok = check_rotation_math(ctx, layer, layer_channels, base_channels)
    weights = ease_weights(ctx)
    wanted = {n: v for n, v in neutral.items()
              if n.split(".", 1)[0] not in NEUTRAL_SKIP and n.split(".", 1)[0] not in planted}
    unknown = sorted(n for n in wanted if n not in base_channels or n not in layer_channels)
    values, value_lines, moves = neutral_layer_values(
        ctx, {n: v for n, v in wanted.items() if n in layer_channels}, base_channels, weights, maths_ok)

    # Work out every key first, then look at the rig before and after keying
    plan, controls_keyed = [], set()
    for name in sorted(values):
        keys = [(f, values[name][f]) for f in sorted(weights)]
        if max(abs(v) for _, v in keys) < 1e-4:
            continue                                    # this channel is at its neutral value all through the eases
        plan.append((layer_channels[name], keys))
        controls_keyed.add(name.split(".", 1)[0])

    ease_frames = sorted(weights)
    swing_controls = sorted(c for c in controls_keyed if f"{c}.Rotation.X" in base_channels)
    swing_frames = [ctx.first, ctx.first + 1, ctx.first + EASE_IN // 2,
                    ctx.last - EASE_OUT // 2, ctx.last - 1, ctx.last]
    smooth_controls = [c for c in SMOOTH_CHECK_CONTROLS if c in ctx.controls]
    for control in set(swing_controls) | set(smooth_controls):
        ctx.forget(control)
    progress("Neutral pose: looking at the clip")
    swing_before = {c: [to_pose(t) for t in ctx.world(c, swing_frames)] for c in swing_controls}
    smooth_before = {c: [to_pose(t) for t in ctx.world(c, ease_frames)] for c in smooth_controls}

    progress("Neutral pose: keying the layer")
    keyed = 0
    for layer_channel, keys in plan:
        for frame, value in keys:
            add_linear_key(layer_channel, frame, value)
            keyed += 1
    layer.modify()
    ctx.base.modify()

    out(f"  Layer '{NEUTRAL_LAYER}' added on row {layer.get_row_index()} (additive, tinted teal): "
        f"{len(plan)} channels on {len(controls_keyed)} controls, {keyed} keys.")
    out(f"  Neutral pose on frames {ctx.first} and {ctx.last}; the layer is zero from frame {in_frame} "
        f"to {out_frame} (ease in {EASE_IN}, ease out {EASE_OUT}), with a key on every frame of each ease.")
    for line in value_lines:
        out(line)
    for f, label in ((ctx.first, "first"), (ctx.last, "last")):
        top = sorted(((v, c) for c, v in moves.get(f, {}).items() if v > 0.5), reverse=True)[:4]
        if top:
            out(f"  Largest moves on the {label} frame: " + "; ".join(f"{c} {v:.1f}" for v, c in top))
    if reset:
        out(f"  ({reset} channels of the new layer had a non-zero default; set to 0 so they add nothing.)")
    if unknown:
        out(f"  {len(unknown)} channels in the pose file aren't on this rig and were skipped, "
            f"e.g. {', '.join(unknown[:3])}.")
    if not marked:
        out("  NOTE: the layer couldn't be tinted, so the script won't recognise it next time. "
            "Delete this layer by hand before running the script again on this sequence.")
    if not plan:
        out("  NOTE: nothing was keyed: the clip is in the neutral pose all through both eases.")

    # Check 1: the middle of the clip must be exactly as before, the ends should have moved
    worst = (0.0, 0.0)
    for control in check_controls:
        ctx.forget(control)
        after = [to_pose(t) for t in ctx.world(control, check_frames)]
        moved = [(v_dist(a.p, b.p), q_angle_deg(a.q, b.q)) for a, b in zip(after, before[control])]
        worst = (max([worst[0]] + [m[0] for m in moved[1:4]]), max([worst[1]] + [m[1] for m in moved[1:4]]))
        out(f"  {control}: moves {moved[0][0]:.1f} cm / {moved[0][1]:.0f} deg on the first frame, "
            f"{moved[4][0]:.1f} cm / {moved[4][1]:.0f} deg on the last")
        ctx.forget(control)
    middle_ok = not (worst[0] > 0.05 or worst[1] > 0.1)
    if middle_ok:
        out(f"  Middle of the clip (frames {in_frame}, {middle_frame}, {out_frame}): unchanged.")
    else:
        out(f"  WARNING: the middle of the clip changed by up to {worst[0]:.2f} cm / {worst[1]:.2f} deg. "
            "It should not change at all. Undo (Ctrl+Z) and send me this report.")

    # Check 2: the eases themselves
    #   - the frame next to each end must still be almost the neutral pose (no pop)
    #   - half-way through, a joint must not be further from the clip than the neutral pose is
    progress("Neutral pose: checking the eases")
    pops, swings = [], []
    for control in swing_controls:
        ctx.forget(control)
        after = [to_pose(t) for t in ctx.world(control, swing_frames)]
        was = swing_before[control]
        ctx.forget(control)
        if EASE_IN > 2:
            pop = q_angle_deg(after[1].q, after[0].q)
            if pop > 8.0:
                pops.append((pop, control, swing_frames[0], swing_frames[1]))
            half, span = q_angle_deg(after[2].q, was[2].q), q_angle_deg(after[0].q, was[2].q)
            if half > span + 15.0:
                swings.append((half - span, control, "ease in", swing_frames[2], half, span))
        if EASE_OUT > 2:
            pop = q_angle_deg(after[4].q, after[5].q)
            if pop > 8.0:
                pops.append((pop, control, swing_frames[4], swing_frames[5]))
            half, span = q_angle_deg(after[3].q, was[3].q), q_angle_deg(after[5].q, was[3].q)
            if half > span + 15.0:
                swings.append((half - span, control, "ease out", swing_frames[3], half, span))
    if pops:
        out(f"  WARNING: {len(pops)} control(s) pop between the neutral frame and the frame next to it:")
        for pop, control, a, b in sorted(pops, reverse=True)[:10]:
            out(f"    {control}: turns {pop:.0f} deg between frames {min(a, b)} and {max(a, b)}")
    if swings:
        out(f"  WARNING: {len(swings)} control(s) swing out of line half-way through an ease:")
        for _, control, label, frame, half, span in sorted(swings, reverse=True)[:10]:
            out(f"    {control}, {label} (frame {frame}): {half:.0f} deg from the clip, "
                f"where the neutral pose is only {span:.0f} deg from it")

    # Check 3: frame to frame, the result must not step further than the clip does plus the
    # ease's own motion (at most about 5% of the way to the neutral pose per frame)
    steps = []
    for control in smooth_controls:
        ctx.forget(control)
        after = [to_pose(t) for t in ctx.world(control, ease_frames)]
        was = smooth_before[control]
        ctx.forget(control)
        for i in range(len(ease_frames) - 1):
            if ease_frames[i + 1] != ease_frames[i] + 1:
                continue                                # the gap between the two eases
            neutral_end = after[0] if ease_frames[i] <= in_frame and EASE_IN > 0 else after[-1]
            turn = q_angle_deg(after[i + 1].q, after[i].q) - q_angle_deg(was[i + 1].q, was[i].q)
            move = v_dist(after[i + 1].p, after[i].p) - v_dist(was[i + 1].p, was[i].p)
            turn_room = 0.08 * q_angle_deg(neutral_end.q, was[i].q) + 2.0
            move_room = 0.08 * v_dist(neutral_end.p, was[i].p) + 0.5
            steps.append((max(turn / turn_room, move / move_room), turn, move, control, ease_frames[i]))
    rough = sorted((s for s in steps if s[0] > 1.0), reverse=True)
    if rough:
        out(f"  WARNING: the result steps further than it should between frames, {len(rough)} time(s):")
        for _, turn, move, control, frame in rough[:10]:
            out(f"    {control}: frame {frame} to {frame + 1}, {turn:.1f} deg / {move:.1f} cm more than the clip")
    if not pops and not swings and not rough:
        out(f"  Eases: no pops, no control swinging out of line, no rough steps "
            f"({len(swing_controls)} controls checked, {len(smooth_controls)} of them on every frame).")
    return {"keyed": keyed, "middle_ok": middle_ok, "swings": len(swings) + len(pops) + len(rough)}


# ------------------------------------------------------------------ signatures for the next stage
NEXT_STAGE_FUNCTIONS = {
    "ControlRigSequencerLibrary": [
        "add_anim_layer_from_selection", "get_anim_layers", "get_anim_layer_index", "delete_anim_layer",
        "duplicate_anim_layer", "merge_anim_layers", "reorder_anim_layers", "set_controls_mask",
        "get_controls_mask", "set_control_rig_world_transforms", "set_local_control_rig_euler_transforms",
        "batch_get_control_transforms", "batch_set_control_transforms", "key_controls",
        "key_controls_at_frames", "zero_transforms", "tween_control_rig", "blend_values_on_selected",
        "get_control_values", "clear_selection", "snap_control_rig", "set_control_rig_layered_mode"],
    "AnimLayer": ["set_weight", "get_weight", "set_type", "set_name", "set_keyed", "set_active",
                  "set_selected", "add_selected_in_sequencer", "select_weight_channel"],
    "AnimLayers": ["anim_layers"],
    "ControlRigPoseAsset": ["save_pose", "paste_pose", "get_current_pose", "select_controls",
                            "get_control_names"],
    "ControlRig": ["select_control", "clear_control_selection", "current_control_selection"],
}


def signatures():
    header("Function signatures in this engine (for the later stages)")
    for class_name, names in NEXT_STAGE_FUNCTIONS.items():
        cls = getattr(unreal, class_name, None)
        out(f"\nunreal.{class_name}: {'present' if cls is not None else 'NOT PRESENT'}")
        if cls is None:
            continue
        for name in names:
            member = getattr(cls, name, None)
            if member is None:
                out(f"  {name}: NOT PRESENT")
                continue
            doc = (getattr(member, "__doc__", "") or "").strip().splitlines()
            out(f"  {name}:")
            for line in doc[:14]:
                out(f"      {line.rstrip()}")


# ------------------------------------------------------------------ run
def save_report():
    saved = os.path.normpath(unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_saved_dir()))
    path = os.path.join(saved, REPORT_NAME)
    try:
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("\n".join(_lines) + "\n")
    except OSError as err:
        return f"couldn't save the report ({err})"
    if "__file__" in globals():
        try:
            shutil.copy2(path, os.path.join(os.path.dirname(os.path.abspath(__file__)), REPORT_NAME))
        except OSError:
            pass
    return path


class _NoTask:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


_task = None


def progress(text):
    """Advance the editor's progress dialog (the checks make the engine evaluate the rig many times)."""
    if _task is not None:
        try:
            _task.enter_progress_frame(1, text)
        except Exception:
            pass


def run_procedural_fixes():
    global _task
    del _lines[:], _stages[:]
    out("Unreal procedural fixes (IK match, IK switch, planted feet, neutral pose)")
    out(f"Engine: {unreal.SystemLibrary.get_engine_version()}")
    out(f"Settings: IK_ARMS={IK_ARMS}, IK_LEGS={IK_LEGS}, PLANT_FEET={PLANT_FEET}, FEET_FRAME={FEET_FRAME}, "
        f"HOLD_KNEE_POLES={HOLD_KNEE_POLES}, MATCH_IK='{MATCH_IK}', IK_VALUE={IK_VALUE}, "
        f"NEUTRAL_POSE={NEUTRAL_POSE}, EASE_IN={EASE_IN}, EASE_OUT={EASE_OUT}, FEET_AT_NEUTRAL={FEET_AT_NEUTRAL}")

    summary, ok = "", False
    slow_task = getattr(unreal, "ScopedSlowTask", None)
    try:
        stage("Open sequence")
        ctx = open_context()
        describe(ctx)
        if MATCH_IK not in ("auto", "always", "never"):
            raise Stop(f"MATCH_IK is '{MATCH_IK}'; it has to be 'auto', 'always' or 'never'.")
        if PLANT_FEET:
            feet_frame(ctx)                             # stops here if FEET_FRAME is out of range
        neutral = load_neutral(ctx)                     # stops here if the pose file or the eases are wrong
        key_section = ctx.track.get_section_to_key()
        with (slow_task(22, "Procedural fixes") if slow_task else _NoTask()) as _task, \
                unreal.ScopedEditorTransaction("Procedural fixes"):
            try:
                _task.make_dialog(False)
            except Exception:
                pass
            ctx.base.modify()
            ctx.track.modify()
            switched_off = []
            try:
                switched_off = prepare_layers(ctx)
                ctx.track.set_section_to_key(ctx.base)  # keys go to the baked section
                stage("IK match")
                header("1. IK match (do the IK controls follow the baked animation?)")
                before = match_ik(ctx, first_pass=True)
                stage("IK switch")
                header("2. IK switch")
                progress("IK switch")
                ik_switch(ctx)
                stage("IK match, in IK mode")
                header("3. IK match again, now in IK mode")
                match_ik(ctx, first_pass=False)
                stage("Plant feet")
                header("4. Plant feet")
                progress("Planting feet")
                plant_feet(ctx, neutral)
                stage("Neutral pose")
                header("5. Neutral pose (ease in and ease out)")
                progress("Neutral pose")
                eased = neutral_pose(ctx, neutral)
            finally:
                for section in switched_off:
                    section.set_is_active(True)
                remaining = list(ctx.track.get_sections())
                ctx.track.set_section_to_key(key_section if key_section in remaining else ctx.base)
        stage("Report")
        SEQ_LIB.refresh_current_level_sequence()
        ok = True
        states = ", ".join(f"{n} {c['status']}" for n, c in before.items())
        if eased is None:
            pose = ("neutral pose skipped (no " + NEUTRAL_FILE + "; run capture_neutral_pose.py)"
                    if NEUTRAL_POSE else "neutral pose off")
        elif not eased["middle_ok"]:
            pose = "neutral pose layer added, BUT the middle of the clip changed - see the Output Log"
        elif eased["swings"]:
            pose = (f"neutral pose layer added, BUT the eases have {eased['swings']} pop(s), swing(s) or rough "
                    "step(s) - see the Output Log")
        else:
            pose = f"neutral pose eased in over {EASE_IN} and out over {EASE_OUT} frames"
        summary = (f"Done: arms {'IK' if IK_ARMS else 'unchanged'}, legs {'IK' if IK_LEGS else 'unchanged'}, "
                   f"feet {'planted' if PLANT_FEET else 'unchanged'}, {pose}.\n"
                   f"IK controls before the run: {states}.\nOne Ctrl+Z undoes all of it.")
    except Stop as err:
        summary = str(err)
        out(f"\nSTOPPED: {err}")
    except Exception:
        summary = "Stopped on an error (see the report). Press Ctrl+Z once to undo any partial changes."
        out("\nERROR:")
        for line in traceback.format_exc().splitlines():
            out("  " + line)
    finally:
        _task = None

    try:
        signatures()
    except Exception as err:
        out(f"(signatures not listed: {type(err).__name__}: {err})")
    header("Timing")
    for line in timing_lines():
        out(line)

    path = save_report()
    cut = next((i for i, line in enumerate(_lines) if line.startswith("Function signatures")), len(_lines))
    for line in _lines[:max(cut - 2, 0)]:
        unreal.log(line)
    for line in timing_lines():
        unreal.log(line)
    if not ok:
        unreal.log_error(summary)
    unreal.log(f"Report: {path}")

    if SHOW_DIALOG and getattr(unreal, "EditorDialog", None) is not None:
        try:
            unreal.EditorDialog.show_message("Procedural fixes", f"{summary}\n\nReport: {path}",
                                             unreal.AppMsgType.OK)
        except Exception:
            pass
    return ok


# Run when executed as a script (File > Execute Python Script), not when imported
if __name__ != "unreal_procedural_fixes":
    run_procedural_fixes()
