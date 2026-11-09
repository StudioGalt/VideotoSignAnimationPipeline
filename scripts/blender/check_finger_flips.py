import bpy
import math

# Finger check: where do the fingers flip? Run it in the MetaHuman take file ("<stamp> Working.blend" or
# "<stamp> Unedited.blend") and in "<stamp> Galtis.blend" (which still has the 'Reference' rig from the import). It
# looks at every armature it knows in the file - the MetaHuman rig, the Xsens-style 'Reference', Galtis ('root') -
# and, over the scene's frame range, reports:
#   1. jumps: a finger bone that turns more than JUMP degrees from one frame to the next (a flip; a real finger
#      at 60 fps rarely turns more than 10-15 degrees in a frame)
#   2. in the Galtis file: how far each Galtis finger points away from the Reference finger it follows, at worst
#      and on which frame (the Remap's error)
# Nothing in the file is changed. The console shows the results; send them over.

JUMP = 25.0          # degrees from one frame to the next that count as a flip
FINGERS = ("thumb", "index", "middle", "ring", "pinky")
XSENS = {"thumb": "Thumb", "index": "Index", "middle": "Middle", "ring": "Ring", "pinky": "Pinky"}


def finger_bones(obj):
    """{label: bone name} for the finger bones of an armature, by its naming (MetaHuman / Galtis or Xsens)."""
    bones = obj.data.bones
    found = {}
    for s, side in (("l", "Left"), ("r", "Right")):
        for f in FINGERS:
            for i in (1, 2, 3):
                label = f"{f}_0{i}_{s}"
                for name in (f"{f}_0{i}_{s}", f"{side}Hand{XSENS[f]}{i}"):
                    if name in bones:
                        found[label] = name
                        break
    return found


def rigs():
    out = []
    for obj in bpy.context.scene.objects:
        if obj.type != "ARMATURE":
            continue
        fingers = finger_bones(obj)
        if len(fingers) >= 10:
            out.append((obj, fingers))
    return out


def world_rot(obj, bone):
    return (obj.matrix_world @ obj.pose.bones[bone].matrix).to_quaternion()


def direction(obj, bone):
    pb = obj.pose.bones[bone]
    return (obj.matrix_world.to_3x3() @ (pb.tail - pb.head)).normalized()


def angle(q1, q2):
    a = math.degrees(q1.rotation_difference(q2).angle)
    return min(a, 360.0 - a)


def check():
    scene = bpy.context.scene
    found = rigs()
    if not found:
        print("[check_finger_flips] no armature with finger bones (thumb_01_l ... or LeftHandThumb1 ...) in this scene")
        return
    frames = list(range(scene.frame_start, scene.frame_end + 1))
    keep = scene.frame_current
    print(f"[check_finger_flips] {bpy.path.basename(bpy.data.filepath) or 'this file'}, frames {frames[0]}-{frames[-1]}: "
          + ", ".join(f"'{o.name}' ({len(f)} finger bones)" for o, f in found))
    hidden = [o for o in bpy.context.view_layer.objects if o.type == "MESH" and not o.hide_viewport]
    for o in hidden:
        o.hide_viewport = True                     # quicker: the meshes needn't follow
    try:
        rot = {o.name: {lab: [] for lab in f} for o, f in found}
        dirs = {o.name: {lab: [] for lab in f} for o, f in found}
        for frame in frames:
            scene.frame_set(frame)
            for o, f in found:
                for lab, bone in f.items():
                    rot[o.name][lab].append(world_rot(o, bone))
                    dirs[o.name][lab].append(direction(o, bone))
    finally:
        for o in hidden:
            o.hide_viewport = False
        scene.frame_set(keep)

    for o, f in found:
        flips = []
        for lab in f:
            series = rot[o.name][lab]
            for k in range(1, len(series)):
                a = angle(series[k - 1], series[k])
                if a > JUMP:
                    flips.append((a, lab, frames[k]))
        flips.sort(reverse=True)
        if flips:
            bones = sorted({lab for _, lab, _ in flips})
            print(f"  '{o.name}': {len(flips)} jump(s) over {JUMP:g} deg in a frame, in {', '.join(bones[:10])}"
                  + (" ..." if len(bones) > 10 else ""))
            for a, lab, fr in flips[:8]:
                print(f"      {lab} ({f[lab]}): {a:.0f} deg into frame {fr}")
        else:
            print(f"  '{o.name}': no finger turns more than {JUMP:g} deg in a frame - no flips")

    ref = next((o for o, _ in found if o.name.startswith("Reference")), None)
    galtis = next((o for o, _ in found if o.name == "root"), None)
    if ref is not None and galtis is not None:
        print(f"  '{galtis.name}' against '{ref.name}' (how far each finger points away from the one it follows):")
        rows = []
        for lab in rot[galtis.name]:
            if lab not in dirs[ref.name]:
                continue
            offs = [math.degrees(a.angle(b)) for a, b in zip(dirs[galtis.name][lab], dirs[ref.name][lab])]
            base = offs[0]
            worst = max(range(len(offs)), key=lambda k: abs(offs[k] - base))
            rows.append((abs(offs[worst] - base), lab, frames[worst], base))
        rows = sorted((r for r in rows if r[0] > 5.0), reverse=True)
        for change, lab, fr, base in rows[:8]:
            print(f"      {lab}: {base:.0f} deg apart on the first frame, {change:.0f} deg more (or less) at frame {fr}")
        if not rows:
            print("      every finger stays within 5 deg of how it starts against the Reference - the Remap follows it")


if __name__ == "__main__":
    check()
