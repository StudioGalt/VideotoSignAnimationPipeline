import bpy, json, os, time

# Save the MetaHuman rig's current pose as the NEUTRAL pose for metahuman_procedural_fixes.py.
# Pose the rig (MH_rig) the way a sign should start and end - arms down, hands relaxed - on any
# frame, then run this. The rotation of every FK control (and the hip) is written to NEUTRAL_POSE_FILE.
# Run it again whenever you want to change the neutral pose; takes done before keep theirs.

RIG_NAME = "MH_rig"   # "" = the armature made by build_metahuman_rig.py
NEUTRAL_POSE_FILE = None   # None = paths.neutral_pose in pipeline_config.json (<workspace>/MetaHuman/neutral_pose.json);
                           # metahuman_procedural_fixes.py reads the same file


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


if not NEUTRAL_POSE_FILE:
    NEUTRAL_POSE_FILE = workspace_path("neutral_pose")
if not NEUTRAL_POSE_FILE:
    raise RuntimeError("No pipeline_config.json found (run create_workspace.py), so there is nowhere to save the pose: "
                       "set NEUTRAL_POSE_FILE at the top of this script to a .json path")

rig = bpy.data.objects.get(RIG_NAME) if RIG_NAME else None
if rig is None or rig.type != "ARMATURE":
    rig = next((o for o in bpy.data.objects if o.type == "ARMATURE" and o.get("sgm_rig") == "metahuman"), None)
if rig is None:
    raise RuntimeError("No MetaHuman rig in this file (build_metahuman_rig.py makes it)")
try:
    controls = set(json.loads(rig.get("sgm_control_map", "{}")).values())
except ValueError:
    controls = set()
if not controls:
    raise RuntimeError(f"'{rig.name}' has no control map; rebuild it with build_metahuman_rig.py")
bones = {}
for name in sorted(controls):
    pb = rig.pose.bones.get(name)
    if pb is None:
        continue
    q = pb.matrix_basis.to_quaternion()            # whatever the rotation mode
    bones[name] = [round(v, 6) for v in q]
moved = [n for n, q in bones.items() if abs(abs(q[0]) - 1.0) > 1e-4]
os.makedirs(os.path.dirname(NEUTRAL_POSE_FILE) or ".", exist_ok=True)
with open(NEUTRAL_POSE_FILE, "w", encoding="utf-8") as fh:
    json.dump({"rig": rig.name, "frame": bpy.context.scene.frame_current, "saved": time.strftime("%Y-%m-%d %H:%M:%S"),
               "bones": bones}, fh, indent=1)
print(f"[save_neutral_pose] {len(bones)} controls ({len(moved)} turned away from rest) from frame "
      f"{bpy.context.scene.frame_current} -> {NEUTRAL_POSE_FILE}")
