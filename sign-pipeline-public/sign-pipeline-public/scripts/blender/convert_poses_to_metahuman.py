import bpy
import contextlib
import importlib.util
import io
import json
import os
import re
import sys
import time
import traceback
from mathutils import Matrix

# Convert pose assets made on another rig into pose assets for the MetaHuman rig (MH_rig, from build_metahuman_rig.py).
#
# Run it in the MetaHuman character file (MetaHuman_character.blend), or in a take file whose rig has no animation
# layers. For every pose asset in SOURCE it:
#   1. puts the pose on the rig it was made on (SOURCE_RIG, brought in from SOURCE_RIG_FILE for the run): that
#      rig's own controls, IK and constraints pose its body, whether the limbs were posed in FK or in IK
#   2. turns MH_rig's FK controls until the MetaHuman's bones point the way that rig's bones of the same name do
#      (the retarget of import_unreal_take.py: different rest poses - T-pose against A-pose - and different bone
#      axes are taken out; only where each bone points is carried over). The hips move as the source's pelvis does.
#   3. saves the result as a pose asset of the same name, with a thumbnail, in OUTPUT_FOLDER (one .asset.blend each),
#      and adds that folder to Blender's asset libraries, so the poses show in the Asset Browser
# The converted poses are in FK: they hold the FK controls and the hip, and set the four FK/IK switches to FK (master
# and the IK controls are left out). After applying one, Match IK to FK in the Metahuman Rig tab puts the IK on it.
#
# The source rig needs the MetaHuman's (Unreal's) bone names on its deforming bones - pelvis, spine_01..spine_05,
# neck_01, neck_02, head, clavicle_l, upperarm_l, lowerarm_l, hand_l, the fingers (thumb_01_l, index_01_l ...),
# thigh_l, calf_l, foot_l, ball_l and the _r side. Galtis has them; so does any rig made on the MetaHuman or
# Unreal skeleton, in A-pose or T-pose: for those, set SOURCE, SOURCE_RIG_FILE and SOURCE_RIG to that rig's files.
#
# Poses already in OUTPUT_FOLDER are skipped (OVERWRITE = True converts them again). Try MAX_POSES = 5 first.

# --- settings (None = from pipeline_config.json in the workspace) ---
SOURCE = None            # a .blend holding pose assets, or a folder of them (sub-folders too). None = <Storage>/Galtis
SOURCE_RIG_FILE = None   # the .blend with the rig the poses were made on. None = the .blend in paths.avatar
                         #   (AvatarFolder) that has an object called SOURCE_RIG - the Galtis avatar
SOURCE_RIG = "root"      # that rig's object name (Galtis: "root")
OUTPUT_FOLDER = None     # where the converted poses are saved. None = <Storage>/MH_rig
LIBRARY_NAME = "MH_rig"  # OUTPUT_FOLDER is added to Preferences > File Paths > Asset Libraries under this name;
                         #   "" = leave the Preferences alone
NAME_FORMAT = "{name}"   # the converted pose's name; {name} = the source pose's name, e.g. "{name} MH"
ONLY = ""                # only poses whose name contains this text (case-insensitive); "" = all
MAX_POSES = 0            # stop after this many conversions (0 = all); e.g. 5 for a trial run
OVERWRITE = False        # True = convert again the poses that are in OUTPUT_FOLDER already
REFRESH_THUMBNAILS = True  # with THUMBNAILS = "KEEP": poses converted already get the source pose's thumbnail now
                           #   (in place of a drawn one), without being converted again
THUMBNAILS = "KEEP"      # "KEEP" = each converted pose keeps the source pose's thumbnail (a pose without one gets a
                         #   new one); "NEW" = a new picture of the MetaHuman in the pose, drawn by Blender's preview
                         #   job through Pose Magic's camera (the poses are then converted one at a time in the
                         #   background, so Blender can draw each picture); "NONE" = no thumbnails
SWEEPS = 8               # correction passes per pose; more = closer to the source, slower
RIG_NAME = ""            # the MetaHuman rig; "" = the one build_metahuman_rig.py made
AUTHOR = "Studio Galt Mocap"
WARN_ABOVE = 3.0         # degrees: a pose whose bones end up further than this from the source is listed at the end
SWITCH_BONE = "FKIK Switch"
PREVIEW_WAIT = 10.0      # seconds to wait for a thumbnail before saving the pose without one


def pipeline_settings():
    """(pipeline_config module, settings) from the workspace's pipeline_config.json, or (None, None) without one.
    The config is looked for above the open .blend, above this script, and in the workspace create_workspace.py
    noted on this computer; pipeline_config.py is loaded from next to this script or that workspace's Scripts."""
    folders = script_folders()
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


def script_folders():
    """This script's folder, the folders of the scripts open in the Text Editor, and the noted workspace's Scripts."""
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
    unique = []
    for folder in folders:
        if folder not in unique:
            unique.append(folder)
    return unique


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


def load_script(file_name, module_name):
    """A pipeline script loaded as a module (its own run code doesn't start), from the first folder that has it."""
    for folder in script_folders() + [workspace_path("scripts", "")]:
        path = os.path.join(folder, file_name) if folder else ""
        if path and os.path.isfile(path):
            spec = importlib.util.spec_from_file_location(module_name, path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module, path
    raise RuntimeError(f"{file_name} isn't next to this script or in the workspace's Scripts folder: put it there")


# --- the files ---
def resolve_settings():
    global SOURCE, SOURCE_RIG_FILE, OUTPUT_FOLDER
    _pc, cfg = pipeline_settings()
    print(f"  settings:      {cfg['_config_path']}" if cfg else "  settings:      no pipeline_config.json found")
    storage = workspace_path("storage")
    if not SOURCE:
        SOURCE = os.path.join(storage, "Galtis") if storage else ""
    if not OUTPUT_FOLDER:
        OUTPUT_FOLDER = os.path.join(storage, "MH_rig") if storage else ""
    avatar_folder = workspace_path("avatar", "")
    if SOURCE_RIG_FILE and not os.path.isfile(SOURCE_RIG_FILE):
        given = SOURCE_RIG_FILE
        if os.path.isfile(given + ".blend"):           # written without its .blend (File Explorer hides it)
            SOURCE_RIG_FILE = given + ".blend"
        elif os.path.isdir(given):                     # a folder: the .blend in it that has the rig
            SOURCE_RIG_FILE = find_rig_file(given, SOURCE_RIG)
            avatar_folder = given
        if SOURCE_RIG_FILE != given:
            print(f"  (SOURCE_RIG_FILE: {given} -> {SOURCE_RIG_FILE or 'no .blend there with ' + repr(SOURCE_RIG)})")
    if not SOURCE_RIG_FILE:
        SOURCE_RIG_FILE = find_rig_file(avatar_folder, SOURCE_RIG)
    if not SOURCE_RIG_FILE:
        raise RuntimeError(rig_file_help(cfg, avatar_folder))
    if not SOURCE or not OUTPUT_FOLDER:
        raise RuntimeError("No pipeline_config.json found, so the Storage folder isn't known: set SOURCE and "
                           "OUTPUT_FOLDER at the top of this script (or run create_workspace.py for this workspace)")
    if not os.path.exists(SOURCE):
        raise RuntimeError(f"SOURCE not found: {SOURCE}")
    if not os.path.isfile(SOURCE_RIG_FILE):
        folder = os.path.dirname(SOURCE_RIG_FILE)
        near = sorted(f for f in os.listdir(folder) if f.lower().endswith(".blend"))[:10] if os.path.isdir(folder) else []
        raise RuntimeError(f"SOURCE_RIG_FILE not found: {SOURCE_RIG_FILE}"
                           + (f"\n  .blend files in {folder}: {', '.join(near)}" if near else ""))


def find_rig_file(folder, rig_name):
    """The first .blend in `folder` (not its backups) that has an object called rig_name."""
    if not folder or not os.path.isdir(folder):
        return ""
    for name in sorted(os.listdir(folder)):
        path = os.path.join(folder, name)
        if not name.lower().endswith(".blend") or not os.path.isfile(path):
            continue
        try:
            with bpy.data.libraries.load(path, link=False) as (src, _dst):
                if rig_name in src.objects:
                    return path
        except (OSError, RuntimeError):
            continue
    return ""


def rig_file_help(cfg, folder):
    """Why no rig file was found: which settings and folder were used, and what is in that folder."""
    lines = [f"The rig the poses were made on wasn't found: no .blend with an object called '{SOURCE_RIG}'"]
    lines.append(f"  settings used: {cfg['_config_path'] if cfg else 'none found'}")
    if not folder:
        lines.append("  AvatarFolder: not known")
    elif not os.path.isdir(folder):
        lines.append(f"  AvatarFolder: {folder} doesn't exist")
    else:
        blends = [f for f in sorted(os.listdir(folder)) if f.lower().endswith(".blend")]
        lines.append(f"  AvatarFolder: {folder} - " + (f"{len(blends)} .blend file(s):" if blends else "no .blend files"))
        for name in blends[:8]:
            try:
                with bpy.data.libraries.load(os.path.join(folder, name), link=False) as (src, _dst):
                    objects = list(src.objects)
                lines.append(f"    {name}: objects {', '.join(objects[:12])}" + (" ..." if len(objects) > 12 else ""))
            except (OSError, RuntimeError) as err:
                lines.append(f"    {name}: couldn't be read ({err})")
    lines.append("Set SOURCE_RIG_FILE at the top of this script to the Galtis avatar .blend (and SOURCE_RIG to its rig's "
                 "object name, if it isn't 'root'), or put the avatar file in that AvatarFolder.")
    if cfg and os.path.normcase(os.path.dirname(cfg["_config_path"])) not in os.path.normcase(
            os.path.abspath(bpy.data.filepath or "")):
        lines.append("Note: those settings belong to another workspace than this .blend's (the one create_workspace.py "
                     "last set up). To use this workspace's folders, run create_workspace.py for it.")
    return "\n".join(lines)


def pose_files(source):
    if os.path.isfile(source):
        return [source]
    found = []
    for root, dirs, files in os.walk(source):
        dirs.sort()
        found += [os.path.join(root, f) for f in sorted(files) if f.lower().endswith(".blend")]
    return found


def safe_name(name):
    return "".join("_" if c in '<>:"/\\|?*' else c for c in name)


def output_path(pose_name):
    return os.path.join(OUTPUT_FOLDER, f"{safe_name(pose_name)}.asset.blend")


# --- the rigs ---
def find_mh_rig():
    rig = bpy.data.objects.get(RIG_NAME) if RIG_NAME else None
    if rig is None or rig.type != "ARMATURE":
        rig = next((o for o in bpy.context.scene.objects if o.type == "ARMATURE" and o.get("sgm_rig") == "metahuman"),
                   None)
    if rig is None:
        raise RuntimeError("No MetaHuman rig in this file: run this in MetaHuman_character.blend "
                           "(build_metahuman_rig.py makes it)")
    return rig


ID_COLLECTIONS = ("objects", "armatures", "meshes", "materials", "images", "textures", "node_groups", "actions",
                  "collections", "curves", "lattices", "cameras", "lights", "texts", "fonts")


def id_snapshot():
    return {kind: set(getattr(bpy.data, kind)) for kind in ID_COLLECTIONS}


def remove_new_ids(before):
    """Remove everything added to the file since `before` (the source rig and what came in with it)."""
    for kind in ("objects",) + tuple(k for k in ID_COLLECTIONS if k != "objects"):
        store = getattr(bpy.data, kind)
        for item in [i for i in store if i not in before[kind]]:
            try:
                store.remove(item)
            except (ReferenceError, RuntimeError, TypeError):
                pass


def bring_in_source_rig():
    """Append SOURCE_RIG from SOURCE_RIG_FILE into a collection of its own. Returns the rig."""
    with bpy.data.libraries.load(SOURCE_RIG_FILE, link=False) as (src, dst):
        if SOURCE_RIG not in src.objects:
            raise RuntimeError(f"There is no object called '{SOURCE_RIG}' in {SOURCE_RIG_FILE}")
        dst.objects = [SOURCE_RIG]
    rig = dst.objects[0]
    if rig is None or rig.type != "ARMATURE":
        raise RuntimeError(f"'{SOURCE_RIG}' in {SOURCE_RIG_FILE} isn't an armature")
    holder = bpy.data.collections.new("SGM pose source")
    bpy.context.scene.collection.children.link(holder)
    holder.objects.link(rig)
    rig.hide_set(False)
    rig.hide_viewport = False
    rig.data.pose_position = "POSE"
    if rig.animation_data is not None:
        rig.animation_data.action = None
        for track in list(rig.animation_data.nla_tracks):
            rig.animation_data.nla_tracks.remove(track)
    return rig


def clear_pose(rig):
    for pb in rig.pose.bones:
        pb.matrix_basis = Matrix()


def switch_names(rig):
    sw = rig.pose.bones.get(SWITCH_BONE)
    return [k for k in sw.keys() if re.search(r"IKFK_[LR]$", k)] if sw is not None else []


def pose_action(rig, bones, name):
    """An action holding the bones' current pose on frame 1, and the FK/IK switches at their current values."""
    action = bpy.data.actions.new(name)
    slot = action.slots.new(id_type="OBJECT", name=rig.name)
    strip = action.layers.new("Layer").strips.new(type="KEYFRAME")
    bag = strip.channelbag(slot, ensure=True)
    for pb in bones:
        channels = [("location", pb.location),
                    {"QUATERNION": ("rotation_quaternion", pb.rotation_quaternion),
                     "AXIS_ANGLE": ("rotation_axis_angle", pb.rotation_axis_angle)}
                    .get(pb.rotation_mode, ("rotation_euler", pb.rotation_euler)),
                    ("scale", pb.scale)]
        for prop, values in channels:
            for i, v in enumerate(values):
                fc = bag.fcurves.new(f'pose.bones["{pb.name}"].{prop}', index=i, group_name=pb.name)
                fc.keyframe_points.insert(1, float(v), options={"FAST"})
    sw = rig.pose.bones.get(SWITCH_BONE)
    for key in switch_names(rig):
        fc = bag.fcurves.new(f'pose.bones["{SWITCH_BONE}"]["{key}"]', index=0, group_name=SWITCH_BONE)
        fc.keyframe_points.insert(1, float(sw[key]), options={"FAST"})
    return action


ERROR_LINE = re.compile(r"remaining error .*?: ([\d.]+) deg on average, ([\d.]+) deg at worst \(([^,]+),")


# --- run ---
def convert_poses():
    """Convert the poses: one at a time from a timer when thumbnails are drawn (Blender draws them between poses),
    else straight through."""
    steps = conversion()
    if thumbnail_mode() == "NONE" or bpy.app.background:
        for _ in steps:
            pass
        return
    try:
        first = next(steps)          # the settings are checked and the first pose converted here, so a problem
    except StopIteration:            # with them stops the script with its message
        return
    run_in_steps(steps, first)
    print("  (converting the rest in the background - the console shows each pose; leave the file alone until it "
          "says Converted)")


def thumbnail_mode():
    mode = {True: "NEW", False: "NONE", None: "NONE"}.get(THUMBNAILS, str(THUMBNAILS).upper())
    return mode if mode in ("KEEP", "NEW", "NONE") else "KEEP"


def copy_thumbnail(source, target):
    """Give `target` the thumbnail of `source` (a pose asset read from its file). Returns True when it had one."""
    preview = source.preview
    if preview is None or not preview.image_size[0] or not preview.image_size[1]:
        return False
    try:
        import numpy as np
        px = np.empty(preview.image_size[0] * preview.image_size[1] * 4, dtype=np.float32)
        preview.image_pixels_float.foreach_get(px)
        if not px[3::4].any():                       # an empty (never drawn) thumbnail
            return False
        copy = target.preview_ensure()
        copy.image_size = tuple(preview.image_size)
        copy.image_pixels_float.foreach_set(px)
        return True
    except Exception as err:
        print(f"    (the source's thumbnail couldn't be copied: {err})")
        return False


def preview_pixels(action):
    """(size, RGBA floats) of an action's thumbnail, or None."""
    preview = action.preview
    if preview is None or not preview.image_size[0] or not preview.image_size[1]:
        return None
    import numpy as np
    px = np.empty(preview.image_size[0] * preview.image_size[1] * 4, dtype=np.float32)
    preview.image_pixels_float.foreach_get(px)
    return (tuple(preview.image_size), px) if px[3::4].any() else None


def refresh_thumbnail(source, pose_name):
    """Give the converted pose already in OUTPUT_FOLDER the thumbnail of `source` (its pose asset). Returns True when
    the file was changed."""
    wanted = preview_pixels(source)
    if wanted is None:
        return False                                  # the source has none: the converted pose keeps its own
    path = output_path(pose_name)
    with bpy.data.libraries.load(path, link=False) as (src, dst):
        dst.actions = list(src.actions)
    actions = [a for a in dst.actions if a is not None]
    try:
        target = next((a for a in actions if a.asset_data is not None), None)
        if target is None:
            return False
        have = preview_pixels(target)
        import numpy as np
        if have is not None and have[0] == wanted[0] and np.allclose(have[1], wanted[1], atol=1 / 255):
            return False                              # it has that thumbnail already
        if not copy_thumbnail(source, target):
            return False
        bpy.data.libraries.write(path, {target}, path_remap="NONE", fake_user=True, compress=True)
        print(f"  {pose_name}: thumbnail replaced with the source pose's")
        return True
    finally:
        for a in actions:
            try:
                bpy.data.actions.remove(a)
            except ReferenceError:
                pass


def has_picture(action):
    """True once Blender's preview job has drawn the action's thumbnail."""
    preview = action.preview
    if preview is None or not preview.image_size[0]:
        return False
    try:
        import numpy as np
        px = np.empty(preview.image_size[0] * preview.image_size[1] * 4, dtype=np.float32)
        preview.image_pixels_float.foreach_get(px)
        return bool(px[3::4].any())
    except Exception:
        return False


def run_in_steps(steps, first=None):
    """Run the conversion from a timer, waiting after each pose until Blender has drawn its thumbnail."""
    state = {"action": first if isinstance(first, bpy.types.Action) else None, "since": time.perf_counter(),
             "busy_seen": False}

    def tick():
        action = state["action"]
        if action is not None:
            try:
                busy = bpy.app.is_job_running("RENDER_PREVIEW")
            except (AttributeError, TypeError, ValueError):
                busy = False
            state["busy_seen"] = state["busy_seen"] or busy
            waited = time.perf_counter() - state["since"]
            try:
                drawn = has_picture(action)
            except ReferenceError:
                drawn = True
            if busy or (not drawn and not state["busy_seen"] and waited < PREVIEW_WAIT):
                return 0.1                           # still drawing (or not started yet)
        try:
            what = next(steps)
        except StopIteration:
            return None
        except Exception:
            traceback.print_exc()
            steps.close()                            # the clean-up in conversion() runs
            return None
        state["action"] = what if isinstance(what, bpy.types.Action) else None
        state["since"], state["busy_seen"] = time.perf_counter(), False
        return 0.1

    bpy.app.timers.register(tick, first_interval=0.1)


def conversion():
    """The conversion, a pose at a time. Yields each converted pose's action once its thumbnail has been asked for
    (the caller waits for Blender to draw it before going on)."""
    started = time.perf_counter()
    print("[convert_poses_to_metahuman]")
    rig = find_mh_rig()
    resolve_settings()
    retarget, retarget_path = load_script("import_unreal_take.py", "sgm_import_unreal_take")
    magic = None
    mode = thumbnail_mode()
    if mode != "NONE":
        try:
            magic, _ = load_script("pose_magic_lib.py", "sgm_pose_magic_lib")
        except RuntimeError as err:
            print(f"  thumbnails: {err} - the poses get none")
    retarget.RETARGET_ARMATURE = rig.name
    retarget.RETARGET_SWEEPS = max(1, int(SWEEPS))
    retarget.RETARGET_SKIP = ()
    print(f"  MetaHuman rig: {rig.name}")
    print(f"  poses from:    {SOURCE}")
    print(f"  made on:       '{SOURCE_RIG}' in {SOURCE_RIG_FILE}")
    print(f"  saved to:      {OUTPUT_FOLDER}")
    print(f"  retarget:      {retarget_path}")
    print("  thumbnails:    " + {"KEEP": "the source poses' own (a new one where a pose has none)",
                                 "NEW": "new pictures of the MetaHuman", "NONE": "none"}[mode])
    os.makedirs(OUTPUT_FOLDER, exist_ok=True)

    files = pose_files(SOURCE)
    if not files:
        raise RuntimeError(f"No .blend files in {SOURCE}")

    scene, layer = bpy.context.scene, bpy.context.view_layer
    if bpy.context.object is not None and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    saved = {"start": scene.frame_start, "end": scene.frame_end, "current": scene.frame_current,
             "camera": scene.camera}
    animation = rig.animation_data or rig.animation_data_create()
    old_action, old_slot = animation.action, getattr(animation, "action_slot", None)
    sw = rig.pose.bones.get(SWITCH_BONE)
    old_switches = {k: sw[k] for k in switch_names(rig)} if sw is not None else {}
    old_basis = {pb.name: pb.matrix_basis.copy() for pb in rig.pose.bones}
    # the pose holds the FK controls the retarget turns (and the hip), not master or the IK controls, so applying it
    # leaves where the character stands and the IK controls alone
    try:
        keyed = list(dict(json.loads(rig.get("sgm_control_map", "{}"))).values())
    except (TypeError, ValueError):
        keyed = []
    controls = [rig.pose.bones[n] for n in dict.fromkeys(keyed) if n in rig.pose.bones]
    if not controls:
        raise RuntimeError(f"'{rig.name}' has no control map: rebuild it with build_metahuman_rig.py")
    camera_existed = magic is not None and bpy.data.objects.get(magic.CAMERA_NAME) is not None
    before = id_snapshot()
    hidden = []                                     # objects hidden while a thumbnail is drawn

    done, skipped, failed, results = 0, 0, [], []
    kept, drawn, refreshed = 0, 0, 0
    source = None
    try:
        source = bring_in_source_rig()
        layer.update()
        keep = None
        for path in files:
            if MAX_POSES and done >= MAX_POSES:
                break
            try:
                with bpy.data.libraries.load(path, link=False, assets_only=True) as (src, dst):
                    wanted, refresh = [], set()
                    for name in src.actions:
                        if ONLY and ONLY.lower() not in name.lower():
                            continue
                        if not OVERWRITE and os.path.isfile(output_path(NAME_FORMAT.format(name=name))):
                            if mode == "KEEP" and REFRESH_THUMBNAILS:
                                refresh.add(name)           # done already: only its thumbnail is looked at
                            else:
                                skipped += 1
                                continue
                        wanted.append(name)
                    dst.actions = wanted
                loaded = [a for a in dst.actions if a is not None]
            except (OSError, RuntimeError) as err:
                print(f"  {os.path.basename(path)}: couldn't be read ({err})")
                continue
            to_refresh = [a for a in loaded if a.name in refresh]
            loaded = [a for a in loaded if a.name not in refresh]
            for action in to_refresh:
                skipped += 1
                name = action.name
                action.name = f"~source {name}"
                try:
                    refreshed += bool(refresh_thumbnail(action, NAME_FORMAT.format(name=name)))
                except Exception as err:
                    print(f"  {name}: thumbnail not refreshed ({type(err).__name__}: {err})")
                finally:
                    bpy.data.actions.remove(action)
            for action in loaded:
                if MAX_POSES and done >= MAX_POSES:
                    bpy.data.actions.remove(action)
                    continue
                source_name = action.name
                new_name = NAME_FORMAT.format(name=source_name)
                action.name = f"~source {source_name}"         # so the converted pose can take the name
                frame = int(round(action.frame_range[0]))
                try:
                    # the pose on the source rig
                    clear_pose(source)
                    source_anim = source.animation_data or source.animation_data_create()
                    source_anim.action = action
                    if getattr(action, "slots", None) and len(action.slots):
                        source_anim.action_slot = action.slots[0]
                    scene.frame_start = scene.frame_end = frame
                    # MH_rig from its rest pose, then the retarget's solve
                    animation.action = None
                    clear_pose(rig)
                    log = io.StringIO()
                    with contextlib.redirect_stdout(log):
                        solved = retarget.retarget_body(source, "~sgm pose convert", rig)
                    if solved is None:
                        raise RuntimeError(log.getvalue().strip().splitlines()[-1] if log.getvalue().strip()
                                           else "the retarget found nothing to do")
                    match = ERROR_LINE.search(log.getvalue())
                    mean, worst, worst_bone = (float(match.group(1)), float(match.group(2)), match.group(3)) \
                        if match else (0.0, 0.0, "")
                    if done == 0:                           # what the two rest poses say, once
                        for line in log.getvalue().splitlines():
                            if line.strip().startswith(("rest poses", "the skeleton faces", "WARNING", "followed")):
                                print(f"  {line.strip()}")
                    scene.frame_set(frame)                  # MH_rig in the converted pose
                    animation.action = None                 # (the pose stays until the frame changes)
                    bpy.data.actions.remove(solved)
                    layer.update()

                    result = pose_action(rig, controls, new_name)
                    result.asset_mark()
                    result.asset_data.author = AUTHOR
                    result.asset_data.description = f"Converted from '{source_name}' ({SOURCE_RIG})"
                    result.asset_data.copyright = "CC0"
                    keep_old = mode == "KEEP" and copy_thumbnail(action, result)
                    kept += bool(keep_old)
                    if magic is not None and not keep_old and not bpy.app.background:
                        try:
                            # Blender's preview job draws the pose through the scene camera, on the active armature,
                            # with everything but the character hidden (as Pose Magic does)
                            if keep is None:
                                keep = set(magic.character_objects(rig)) | {rig}
                                keep |= {o for o in scene.objects if magic.follows(o, rig)}
                            camera = magic.frame_camera(rig, [frame])   # the whole body in view
                            if camera is not None:
                                scene.camera = camera
                                for obj in scene.objects:
                                    if obj not in keep and obj.type not in ("CAMERA", "LIGHT") and obj.visible_get():
                                        obj.hide_set(True)
                                        hidden.append(obj)
                                layer.objects.active = rig
                                result.asset_generate_preview()
                                drawn += 1
                                yield result            # back here once Blender has drawn it
                        except Exception as err:            # a pose without a thumbnail is still a pose
                            print(f"    (no thumbnail: {err})")
                        finally:
                            for obj in hidden:
                                try:
                                    obj.hide_set(False)
                                except (ReferenceError, RuntimeError):
                                    pass
                            hidden.clear()
                    out = output_path(new_name)
                    bpy.data.libraries.write(out, {result}, path_remap="NONE", fake_user=True, compress=True)
                    bpy.data.actions.remove(result)
                    done += 1
                    results.append((worst, mean, worst_bone, new_name))
                    print(f"  [{done}] {source_name} -> {os.path.basename(out)}   off by {mean:.2f} deg on average, "
                          f"{worst:.1f} at worst ({worst_bone})")
                except Exception as err:
                    failed.append(f"{source_name}: {type(err).__name__}: {err}")
                    print(f"  FAILED {source_name}: {type(err).__name__}: {err}")
                finally:
                    if source.animation_data is not None:
                        source.animation_data.action = None
                    try:
                        bpy.data.actions.remove(action)
                    except ReferenceError:
                        pass
    finally:
        # put the file back as it was
        scene.frame_start, scene.frame_end = saved["start"], saved["end"]
        scene.camera = saved["camera"]
        if magic is not None and not camera_existed:
            cam = bpy.data.objects.get(magic.CAMERA_NAME)
            if cam is not None:
                data = cam.data
                bpy.data.objects.remove(cam)
                if data is not None and data.users == 0:
                    bpy.data.cameras.remove(data)
        remove_new_ids(before)
        for name, basis in old_basis.items():
            if name in rig.pose.bones:
                rig.pose.bones[name].matrix_basis = basis
        if sw is not None:
            for key, value in old_switches.items():
                sw[key] = value
        animation.action = old_action
        if old_action is not None and old_slot is not None:
            try:
                animation.action_slot = old_slot
            except (AttributeError, RuntimeError, TypeError):
                pass
        scene.frame_set(saved["current"])

    added = add_library() if done else ""
    minutes = (time.perf_counter() - started) / 60
    print("")
    print(f"Converted {done} pose(s) in {minutes:.1f} min" + (f"; {skipped} were done already (OVERWRITE = True "
          "converts them again)" if skipped else "") + (f"; {len(failed)} failed" if failed else "") + ".")
    if done and mode != "NONE":
        print(f"  thumbnails: {kept} kept from the source poses" + (f", {drawn} drawn new" if drawn else ""))
    if refreshed:
        print(f"  {refreshed} pose(s) converted earlier now have the source pose's thumbnail in place of a drawn one. "
              "If the Asset Browser still shows the old pictures, refresh it (the circular-arrows button next to the "
              "library name in its header, or reopen Blender).")
    if results:
        means = [m for _, m, _, _ in results]
        print(f"  how close: {sum(means) / len(means):.2f} deg on average")
        far = sorted((r for r in results if r[0] > WARN_ABOVE), reverse=True)
        if far:
            print(f"  {len(far)} pose(s) are more than {WARN_ABOVE:g} degrees off somewhere - check them by eye "
                  "(a bone the MetaHuman can't turn that far, or SWEEPS too low):")
            for worst, _, bone, name in far[:15]:
                print(f"    {name}: {worst:.1f} deg at {bone}")
    for line in failed[:15]:
        print(f"  FAILED {line}")
    if added:
        print(f"  {added}")
    print(f"  The poses are in {OUTPUT_FOLDER}: Asset Browser, library '{LIBRARY_NAME or os.path.basename(OUTPUT_FOLDER)}'. "
          "They are in FK; Match IK to FK (Metahuman Rig tab) after applying one puts the IK controls on it.")


def add_library():
    """Add OUTPUT_FOLDER to the Preferences' asset libraries as LIBRARY_NAME (once). Returns a note."""
    if not LIBRARY_NAME:
        return ""
    libraries = bpy.context.preferences.filepaths.asset_libraries
    folder = os.path.normcase(os.path.abspath(OUTPUT_FOLDER))
    for lib in libraries:
        if os.path.normcase(os.path.abspath(bpy.path.abspath(lib.path))) == folder:
            return f"asset library '{lib.name}' is {OUTPUT_FOLDER}"
    try:
        bpy.ops.preferences.asset_library_add(directory=OUTPUT_FOLDER + os.sep)
        new = libraries[-1]
        new.name = LIBRARY_NAME
        return (f"added the asset library '{LIBRARY_NAME}' ({OUTPUT_FOLDER}) to the Preferences - they are saved when "
                "Blender closes if Auto-Save Preferences is on")
    except Exception as err:
        return (f"couldn't add the asset library ({err}): add {OUTPUT_FOLDER} in Edit > Preferences > File Paths > "
                "Asset Libraries")


if __name__ == "__main__":
    convert_poses()
