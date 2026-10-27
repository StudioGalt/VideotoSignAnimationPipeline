import bpy, json, math, os, re, shutil, time, traceback
from mathutils import Euler, Quaternion


#from Unreal to blender
#Works on what export_animations.py writes from Unreal:
#    EXPORT_ROOT\<take folder>\<animation>_body.fbx              (one or more; BODY_PREFER picks one)
#    EXPORT_ROOT\<take folder>\<animation>_body_curves.json      (animation curves; filed with the take)
#    EXPORT_ROOT\<take folder>\<performance>_face.fbx            (the face on the face board, as a Control Rig FBX; imported with FACE_OP)
#    EXPORT_ROOT\<take folder>\<performance>_face_board.json     (the same face board keys as numbers; used when FACE_OP can't run here)
#    EXPORT_ROOT\<take folder>\<video>.mp4                        (the take's source video, moved in from Labelled by
#                                                                 export_animations.py; else it is searched for by name)
#The take name is the folder name. Each run handles one take and moves its files out of
#EXPORT_ROOT. With more than one take waiting there, a list of them opens (type to search it) to pick the one to
#import (CHOOSE_TAKE); running again lists the ones still to do.
#
#Run it in the MetaHuman character file (made by build_metahuman_rig.py): the body is keyed onto the
#MetaHuman rig's controls, and the take is saved as OUTPUT_ROOT\<take>\<take>.blend for the procedural
#edits (metahuman_procedural_fixes.py). (Run in an avatar file with Galtis-style control bones instead, it
#retargets straight onto that rig.)
VERSION = "2026-10-26"   # printed first, so the console shows which copy of this script ran
# --- config ---
# Settings left as None come from pipeline_config.json in the workspace (create_workspace.py makes it).
OUTPUT_ROOT = None   # each finished take goes to OUTPUT_ROOT\<take name>\. None = paths.takes (the workspace folder)
EXPORT_ROOT = None   # where export_animations.py wrote the takes. None = paths.unreal_exports (<workspace>\Unreal Exports)
VIDEO_SEARCH_ROOTS = None   # where a take's video is looked for when it isn't in the take's export folder, in this
                            # order. None = paths.video_source, paths.video_holding, then video.extra_folders
WIP_ROOT    = ""     # optional: a folder of <take name> folders, each holding that take's video; "" = none
VIDEO_OVERRIDE = ""   # full path to a video to force, e.g. r"D:\Videos\Goodbye.mp4"
REQUIRE_VIDEO = True  # False = carry on without a source video, timing taken from the export instead
BODY_OP     = ""   # an add-on's body import to try first, e.g. "character_control_rig.import_fk_animation"; "" = none
FACE_OP     = "character_dna_pro.import_face_board_animation"
DNA_ADDON_MODULES = ("character_dna_pro", "character_dna")   # the Character DNA add-on's module names (Pro, standard)
REQUIRE_DNA_ADDON = False   # True = stop when the Character DNA add-on isn't enabled; False = warn, and import anyway
BODY_TARGET = ""   # object made active before BODY_OP (the add-on's control rig); "" = leave the selection alone
FACE_TARGET = "GaltisMH_face_gui"      # same for the face import (not in the file: the *_face_gui armature that is)
BODY_FALLBACK_OPS = ("wm.fbx_import", "import_scene.fbx")   # plain FBX import, used when BODY_OP can't run in this file; () = none
BODY_SCALE  = 100.0   # size of the plain FBX import; 100 = the Unreal skeleton comes in 100x larger (it arrived at 1/100 of the avatar)
ACTION_NAME = "fbx"   # name of the imported action: "fbx" = the FBX file's name, "take" = the take folder's name, "video" = the video's name
FACE_BOARD_JSON = True   # when FACE_OP can't run in this file, key the face board straight from *_face_board.json
FACE_STANDIN = True      # no face board in this file: make a plain face board armature from the export, so the
                         # face board animation is in the file (like the body's plain skeleton)
FACE_BOARD_SCALE = 1000.0   # size of that plain face board, set like BODY_SCALE: 1000 = a thousand times its first
                            # version, about 830 x 980 units with 22-unit bones. Halve the number for half the size.
FACE_CURVES = True    # key the export's expression curves (CTRL_expressions_*) onto the avatar's shape keys, through
                      # FACE_SHAPE_MAP below and by matching names; skipped when a real face board took the face
FACE_SHAPE_GAIN = 1.0   # every keyed shape key value is multiplied by this
# The avatar's shape key -> the Unreal expression curve(s) that drive it. Names are the part after
# "CTRL_expressions_". Several curves are averaged (a curve that is idle in a take counts as 0).
# Shape keys not listed here are still keyed when a curve has the same name. Edit freely.
FACE_SHAPE_MAP = {
    "InnerBrowRaiserL_AU1_L":     ["browRaiseInL"],
    "InnerBrowRaiserR_AU1_R":     ["browRaiseInR"],
    "OuterBrowRaiserL_AU2_L":     ["browRaiseOuterL"],
    "OuterBrowRaiserR_AU2_R":     ["browRaiseOuterR"],
    "BrowLowerL_AU4_L":           ["browDownL"],
    "BrowLowerR_AU4_R":           ["browDownR"],
    "EyesUpperLidRaiserL_AU5_L":  ["eyeWidenL"],
    "EyesUpperLidRaiserR_AU5_R":  ["eyeWidenR"],
    "CheekRaiserL_AU6_L":         ["eyeCheekRaiseL"],
    "CheekRaiserR_AU6_R":         ["eyeCheekRaiseR"],
    "LidTightener_AU7":           ["eyeLowerLidUpL", "eyeLowerLidUpR"],
    "LipsTowardsEachother_AU8":   ["mouthLipsTogetherUL", "mouthLipsTogetherUR", "mouthLipsTogetherDL", "mouthLipsTogetherDR"],
    "NoseWrinklerL_AU9_L":        ["noseWrinkleL"],
    "NoseWrinklerR_AU9_R":        ["noseWrinkleR"],
    "UpperLipRaiserL_AU10_L":     ["mouthUpperLipRaiseL"],
    "UpperLipRaiserR_AU10_R":     ["mouthUpperLipRaiseR"],
    "NasolabialDeepenerL_AU11_L": ["noseNasolabialDeepenL"],
    "NasolabialDeepenerR_AU11_R": ["noseNasolabialDeepenR"],
    "LipCornerPullerL_AU12_L":    ["mouthCornerPullL"],
    "LipCornerPullerR_AU12_R":    ["mouthCornerPullR"],
    "SharpLipPullerL_AU13_L":     ["mouthSharpCornerPullL"],
    "SharpLipPullerR_AU13_R":     ["mouthSharpCornerPullR"],
    "DimplerL_AU14_L":            ["mouthDimpleL"],
    "DimplerR_AU14_R":            ["mouthDimpleR"],
    "LipCornerDepressorL_AU15_L": ["mouthCornerDepressL"],
    "LipCornerDepressorR_AU15_R": ["mouthCornerDepressR"],
    "LowerLipDepressorL_AU16_L":  ["mouthLowerLipDepressL"],
    "LowerLipDepressorR_AU16_R":  ["mouthLowerLipDepressR"],
    "ChinRaiser_AU17":            ["jawChinRaiseDL", "jawChinRaiseDR"],
    "Pucker_AU18":                ["mouthLipsPurseUL", "mouthLipsPurseUR", "mouthLipsPurseDL", "mouthLipsPurseDR"],
    "TongueShow_AU19":            ["tongueOut"],
    "LipStretcherL_AU20_L":       ["mouthStretchL"],
    "LipStretcherR_AU20_R":       ["mouthStretchR"],
    "NeckTighten_AU21":           ["neckStretchL", "neckStretchR"],
    "Funneler_AU22":              ["mouthFunnelUL", "mouthFunnelUR", "mouthFunnelDL", "mouthFunnelDR"],
    "LipTightenerH_AU23H":        ["mouthLipsTightenUL", "mouthLipsTightenUR", "mouthLipsTightenDL", "mouthLipsTightenDR"],
    "LipPressor_AU24":            ["mouthLipsPressL", "mouthLipsPressR"],
    "JawDrop_AU27":               ["jawOpen"],
    "LipSuck_AU28":               ["mouthUpperLipRollInL", "mouthUpperLipRollInR", "mouthLowerLipRollInL", "mouthLowerLipRollInR"],
    "JawThrust_AU29":             ["jawFwd"],
    "JawSlideLeft_AU30_L":        ["jawLeft"],
    "JawSlideRight_AU30_R":       ["jawRight"],
    "JawClenchL_AU31_L":          ["jawClenchL"],
    "JawClenchR_AU31_R":          ["jawClenchR"],
    "LipBite_AU32":               ["mouthLowerLipBiteL", "mouthLowerLipBiteR"],
    "CheekBlowL_AU33_L":          ["mouthLipsBlowL"],
    "CheekBlowR_AU33_R":          ["mouthLipsBlowR"],
    "CheekPuffL_AU34_L":          ["mouthCheekBlowL"],
    "CheekPuffR_AU34_R":          ["mouthCheekBlowR"],
    "CheekSuckL_AU35_L":          ["mouthCheekSuckL"],
    "CheekSuckR_AU35_R":          ["mouthCheekSuckR"],
    "EyesCloseL_AU43_L":          ["eyeBlinkL"],
    "EyesCloseR_AU43_R":          ["eyeBlinkR"],
    "SquintL_AU44_L":             ["eyeSquintInnerL"],
    "SquintR_AU44_R":             ["eyeSquintInnerR"],
    "EyesLookLeft_AU61":          ["eyeLookLeftL", "eyeLookLeftR"],
    "EyesLookRight_AU62":         ["eyeLookRightL", "eyeLookRightR"],
    "EyesLookUp_AU63":            ["eyeLookUpL", "eyeLookUpR"],
    "EyesLookDown_AU64":          ["eyeLookDownL", "eyeLookDownR"],
    "MouthSlideLeft":             ["mouthLeft"],
    "MouthSlideRight":            ["mouthRight"],
}
CHOOSE_TAKE = True    # more than one take waiting in EXPORT_ROOT: a list of them (type to search) to pick from;
                      # False = the first one (and in a background run, always the first)
REDO_TAKE = ""        # a take that was already imported and filed, e.g. "Hello_tk1": its exported files and its video are
                      # moved back from OUTPUT_ROOT to its export folder first, then it is imported again
RETARGET = True       # after the body comes in as a plain skeleton: key the avatar's control bones so its body follows
                      # that skeleton, on every frame, as a new action on the avatar's armature
RETARGET_ARMATURE = ""   # the armature to key. "" = the MetaHuman rig (build_metahuman_rig.py) if the file has one,
                         # else the armature that has the control bones of RETARGET_MAP (Galtis)
RETARGET_HIP_LOCATION = True   # also move the hips (pelvis position); False = rotations only
RETARGET_SKIP = ()    # skeleton bones to leave alone, by the start of their name, e.g. ("thigh", "calf", "foot", "ball")
                      # for a waist-up take: the legs then stay as they are in the avatar file
RETARGET_SWEEPS = 2   # correction passes per frame. Raise it if the console reports a large remaining error.
# Skeleton bone (MetaHuman name) -> the Galtis control bone that is keyed for it. From the armature report of the
# Galtis rig: each deform bone follows an "...inte..." bone, which copies one of these FK controls.
# (The MetaHuman rig carries its own map, written on it by build_metahuman_rig.py.)
RETARGET_MAP = {
    "pelvis": "hip", "spine_01": "chest_1", "spine_02": "chest_2", "spine_03": "chest_3", "spine_04": "chest_5",
    "neck_02": "neckfk", "head": "headfk",
    "clavicle_l": "collar_L",       "clavicle_r": "collar_R",
    "upperarm_l": "bicepfk_L",      "upperarm_r": "bicepfk_R",
    "lowerarm_l": "forearmfk_L",    "lowerarm_r": "forearmfk_R",
    "hand_l": "handfk_L",           "hand_r": "handfk_R",
    "thumb_01_l": "thumbfk_01_L",   "thumb_01_r": "thumbfk_01_R",
    "thumb_02_l": "thumbfk_02_L",   "thumb_02_r": "thumbfk_02_R",
    "thumb_03_l": "thumbfk_03_L",   "thumb_03_r": "thumbfk_03_R",
    "index_01_l": "indexfk_01_L",   "index_01_r": "indexfk_01_R",
    "index_02_l": "indexfk_02_L",   "index_02_r": "indexfk_02_R",
    "index_03_l": "indexfk_03_L",   "index_03_r": "indexfk_03_R",
    "middle_01_l": "middlefk_01_L", "middle_01_r": "middlefk_01_R",
    "middle_02_l": "middlefk_02_L", "middle_02_r": "middlefk_02_R",
    "middle_03_l": "middlefk_03_L", "middle_03_r": "middlefk_03_R",
    "ring_01_l": "ringfk_01_L",     "ring_01_r": "ringfk_01_R",
    "ring_02_l": "ringfk_02_L",     "ring_02_r": "ringfk_02_R",
    "ring_03_l": "ringfk_03_L",     "ring_03_r": "ringfk_03_R",
    "pinky_01_l": "pinkyfk_01_L",   "pinky_01_r": "pinkyfk_01_R",
    "pinky_02_l": "pinkyfk_02_L",   "pinky_02_r": "pinkyfk_02_R",
    "pinky_03_l": "pinkyfk_03_L",   "pinky_03_r": "pinkyfk_03_R",
    "thigh_l": "thighfk_L",         "thigh_r": "thighfk_R",
    "calf_l": "shinfk_L",           "calf_r": "shinfk_R",
    "foot_l": "footfk_L",           "foot_r": "footfk_R",
    "ball_l": "toefk_L",            "ball_r": "toefk_R",
}
# Skeleton bone -> the bone its direction is measured towards, to compare the two rest poses (an A-pose
# skeleton against a T-pose avatar, say). A bone not listed takes the alignment of the bone above it.
RETARGET_AIM = {"spine_01": "spine_02", "spine_02": "spine_03", "spine_03": "spine_04", "spine_04": "spine_05",
                "neck_02": "head"}
for _side in ("l", "r"):
    RETARGET_AIM.update({f"clavicle_{_side}": f"upperarm_{_side}", f"upperarm_{_side}": f"lowerarm_{_side}",
                         f"lowerarm_{_side}": f"hand_{_side}", f"hand_{_side}": f"middle_01_{_side}",
                         f"thigh_{_side}": f"calf_{_side}", f"calf_{_side}": f"foot_{_side}",
                         f"foot_{_side}": f"ball_{_side}"})
    for _finger in ("thumb", "index", "middle", "ring", "pinky"):
        RETARGET_AIM.update({f"{_finger}_01_{_side}": f"{_finger}_02_{_side}",
                             f"{_finger}_02_{_side}": f"{_finger}_03_{_side}"})
BODY_PREFER = ("AS_MHP_", "AS_", "A_")   # a take folder can hold the same animation twice; first matching prefix wins
NAME_PREFIXES = ("AS_MHP_", "MHP_", "AS_", "A_")   # dropped from animation names when looking for the video
TARGET_FPS  = 60
VIDEO_EXTS  = (".mp4", ".mov")
BAKE_POSES  = True    # re-key armatures on whole frames after the retime


# --- helpers ---
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


def dna_addon():
    """The enabled Character DNA add-on's module name (Pro or standard), or None."""
    enabled = [addon.module for addon in bpy.context.preferences.addons]
    for wanted in DNA_ADDON_MODULES:
        for module in enabled:
            if module == wanted or module.endswith("." + wanted):   # an extension is bl_ext.<repository>.<name>
                return module
    for idname in [FACE_OP] if FACE_OP else []:     # installed some other way: its operator is registered
        mod, op = idname.split(".")
        try:
            getattr(getattr(bpy.ops, mod), op).get_rna_type()
            return mod
        except (AttributeError, KeyError, RuntimeError):
            continue
    return None


def check_dna_addon():
    """Warn (in the console and a popup), or stop with REQUIRE_DNA_ADDON, when the Character DNA add-on is off."""
    module = dna_addon()
    if module:
        print(f"  Character DNA add-on: {module}")
        return
    lines = ["The Character DNA add-on (Character DNA Pro, or the standard edition) isn't enabled.",
             "Without it RigLogic doesn't run, so the MetaHuman's face doesn't move in Blender: the face board",
             "is still keyed (from the take's *_face_board.json, on this file's face board or a plain stand-in)",
             "and the expression curves still go onto the shape keys, but the face board's own import can't run.",
             "Install / enable it in Edit > Preferences > Add-ons (or Get Extensions), then run this script again."]
    if REQUIRE_DNA_ADDON:
        lines.append("REQUIRE_DNA_ADDON is True, so nothing was imported.")
    else:
        lines.append("The take is imported anyway (REQUIRE_DNA_ADDON = True stops here instead).")
    print("\n  " + "!" * 100)
    for line in lines:
        print(f"  WARNING: {line}")
    print("  " + "!" * 100 + "\n")

    def draw(self, context):
        for line in lines:
            self.layout.label(text=line)
    try:
        bpy.context.window_manager.popup_menu(draw, title="import_unreal_take: Character DNA add-on missing",
                                              icon="ERROR")
    except Exception:
        pass
    if REQUIRE_DNA_ADDON:
        raise RuntimeError("The Character DNA add-on isn't enabled (REQUIRE_DNA_ADDON = True): enable it and run again")


def resolve_settings():
    """Fill OUTPUT_ROOT, EXPORT_ROOT and VIDEO_SEARCH_ROOTS from pipeline_config.json where they are None."""
    global OUTPUT_ROOT, EXPORT_ROOT, VIDEO_SEARCH_ROOTS
    pc, cfg = pipeline_settings()
    print(f"  settings: {cfg['_config_path']}" if cfg else "  settings: no pipeline_config.json found (run create_workspace.py)")
    if cfg is not None:
        path = (lambda key: getattr(pc, "setting_path", lambda c, k, f=None: pc.project_path(c, k))(cfg, key, None))
        OUTPUT_ROOT = OUTPUT_ROOT or path("takes")
        EXPORT_ROOT = EXPORT_ROOT or path("unreal_exports")
        if VIDEO_SEARCH_ROOTS is None:
            roots = [path("video_source"), path("video_holding")]
            for extra in pc.get(cfg, "video.extra_folders", []) or []:
                full = os.path.expandvars(os.path.expanduser(extra))
                roots.append(full if os.path.isabs(full) else os.path.join(cfg["_root"], full))
            VIDEO_SEARCH_ROOTS = [os.path.normpath(r) for r in roots if r]
    VIDEO_SEARCH_ROOTS = list(VIDEO_SEARCH_ROOTS or [])
    missing = [name for name, value in (("OUTPUT_ROOT", OUTPUT_ROOT), ("EXPORT_ROOT", EXPORT_ROOT)) if not value]
    if missing:
        raise RuntimeError("No pipeline_config.json found, so " + " and ".join(missing) + " aren't known: run "
                           "create_workspace.py, or set them at the top of this script")
    print(f"  Unreal exports: {EXPORT_ROOT}")
    print(f"  takes filed in: {OUTPUT_ROOT}")


def view3d_context():
    """A 3D Viewport to run importers in: the Text Editor's own context makes many operators refuse to run."""
    wm = bpy.context.window_manager
    for window in (wm.windows if wm else []):
        for area in window.screen.areas:
            if area.type == "VIEW_3D":
                region = next((r for r in area.regions if r.type == "WINDOW"), None)
                if region:
                    return {"window": window, "area": area, "region": region}
    return {}


def make_active(object_name):
    """Object Mode, with `object_name` selected and active (if it exists). Returns True when it was found."""
    if bpy.context.object and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    obj = bpy.data.objects.get(object_name) if object_name else None
    if obj is None or obj.name not in bpy.context.view_layer.objects:
        return False
    for other in bpy.context.view_layer.objects:
        other.select_set(False)
    obj.hide_set(False)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    return True


def call_op(idname, filepath, **kwargs):
    """Run an import operator. Returns True, False (it ran and failed) or "poll" (it refused to run here)."""
    mod, op = idname.split(".")
    try:
        fn = getattr(getattr(bpy.ops, mod), op)
        with bpy.context.temp_override(**view3d_context()):
            if not fn.poll():
                return "poll"
            fn("EXEC_DEFAULT", filepath=filepath, **kwargs)
        return True
    except (RuntimeError, AttributeError) as e:
        print(f"    {e}")
        return "poll" if "poll()" in str(e) else False
    except TypeError as e:                       # the operator doesn't take one of the options
        print(f"    {e}")
        return "args"


def list_dirs(root):
    return sorted(d for d in os.listdir(root)
                  if os.path.isdir(os.path.join(root, d)))


def norm(s):
    """'SGM Amazing 2026-09-08' and 'SGM_Amazing_2026_09_08' -> 'sgmamazing20260908'."""
    return "".join(c for c in s.lower() if c.isalnum())


def videos_in(folder):
    if not os.path.isdir(folder):
        return []
    return sorted(os.path.join(folder, f) for f in os.listdir(folder)
                  if f.lower().endswith(VIDEO_EXTS))


def walk_videos():
    for base in VIDEO_SEARCH_ROOTS:
        if not os.path.isdir(base):
            print(f"  (search root missing: {base})")
            continue
        for root, _, files in os.walk(base):
            for f in files:
                if f.lower().endswith(VIDEO_EXTS):
                    yield root, f


# --- takes in the export folder ---
def files_with(folder, suffix):
    return sorted(f for f in os.listdir(folder)
                  if f.lower().endswith(suffix) and os.path.isfile(os.path.join(folder, f)))


def list_takes():
    """[(take name, folder)] for every folder under EXPORT_ROOT that holds a *_body.fbx or *_face.fbx."""
    takes = []
    for root, dirs, _ in os.walk(EXPORT_ROOT):
        dirs.sort()
        if root != EXPORT_ROOT and (files_with(root, "_body.fbx") or files_with(root, "_face.fbx")):
            takes.append((os.path.basename(root), root))
    return takes


def pick(folder, suffix):
    """The take's file ending in `suffix`: the one whose name starts with the earliest BODY_PREFER prefix."""
    found = files_with(folder, suffix)
    if not found:
        return None
    def rank(f):
        return next((i for i, p in enumerate(BODY_PREFER) if f.startswith(p)), len(BODY_PREFER))
    found.sort(key=lambda f: (rank(f), f))
    if len(found) > 1:
        print(f"  {len(found)} files end in {suffix}; using {found[0]} (others: {', '.join(found[1:])})")
    return os.path.join(folder, found[0])


def take_keys(name, folder):
    """Names the source video might go by: the folder name, and the animation names without prefixes and numbers."""
    names = [name, re.sub(r"[_ ]tk\d+$", "", name, flags=re.I)]
    for f in files_with(folder, ".fbx"):
        stem = re.sub(r"_(body|face)\.fbx$", "", f, flags=re.I)
        names.append(stem)
        names.append(re.sub(r"_\d+$", "", stem))
        for prefix in NAME_PREFIXES:
            if stem.startswith(prefix):
                stem = stem[len(prefix):]
                break
        names.append(stem)
        names.append(re.sub(r"_\d+$", "", stem))
    keys = []
    for n in names:
        if norm(n) and norm(n) not in keys:
            keys.append(norm(n))
    return keys


def find_video(name, folder):
    """Look for the take's source video, most specific location first."""
    # 1. the video in the take's own export folder: export_animations.py moves it there with the take
    vids = videos_in(folder)
    if vids:
        print(f"  video: in the take's export folder")
        return vids[0]
    keys = take_keys(name, folder)
    all_videos = list(walk_videos())
    # 2. a video in the search roots whose file name matches the take or its animation (Labeled first) -
    #    for takes exported before export_animations.py brought the video along
    for key in keys:
        for root, f in all_videos:
            if norm(os.path.splitext(f)[0]) == key:
                return os.path.join(root, f)
    # 3. WIP_ROOT/<name>, exact or loosely matching folder name
    for d in list_dirs(WIP_ROOT) if WIP_ROOT and os.path.isdir(WIP_ROOT) else []:
        if norm(d) in keys:
            vids = videos_in(os.path.join(WIP_ROOT, d))
            if vids:
                return vids[0]
    # 4. the one video whose file name contains the take's name (e.g. with a prefix or a date around it)
    for key in sorted((k for k in keys if len(k) >= 5), key=len, reverse=True):
        hits = [os.path.join(root, f) for root, f in all_videos if key in norm(os.path.splitext(f)[0])]
        if len(hits) == 1:
            print(f"  video matched loosely on '{key}'")
            return hits[0]
        if len(hits) > 1 and len({os.path.basename(h).lower() for h in hits}) == 1:
            print(f"  video matched loosely on '{key}'; the same video is in {len(hits)} places, using {hits[0]}")
            return hits[0]                        # copies of one video: the first search root wins (Labelled)
        if len(hits) > 1:
            for h in hits:
                print(f"    {h}")
            print(f"  {len(hits)} videos contain '{key}' - not guessing between them")
            break
    return None


def near_misses(name, limit=15):
    """Videos in the search roots sharing a word with the take name (dates/prefix ignored), the videos sharing the
    take's own words first ('Jesus' before 'ASL', which nearly every video has)."""
    common = {"sgm", "asl", "the", "and"}
    words = [w.lower() for w in name.replace("-", "_").replace(" ", "_").split("_")
             if w.isalpha() and len(w) > 2 and w.lower() != "sgm"]
    scored = []
    for root, f in walk_videos():
        low = f.lower()
        rare = sum(1 for w in words if w not in common and w in low)
        any_ = sum(1 for w in words if w in low)
        if any_:
            scored.append((-rare, -any_, f, os.path.join(root, f)))
    scored.sort()
    return [path for *_, path in scored[:limit]]


def redo_take(name):
    """Bring a filed take back for another import: its exported files and its video to EXPORT_ROOT\\<take>."""
    filed = os.path.join(OUTPUT_ROOT, name)
    if not os.path.isdir(filed):
        print(f"  redo: there is no folder {filed} - nothing moved back")
        return
    if bpy.data.filepath and os.path.dirname(os.path.abspath(bpy.data.filepath)) == os.path.abspath(filed):
        raise RuntimeError(f"REDO_TAKE: this is the filed take itself ({bpy.data.filepath}). Open the avatar file "
                           "and run the script from there, or the take is imported on top of itself.")
    export = os.path.join(EXPORT_ROOT, name)
    os.makedirs(export, exist_ok=True)
    for f in sorted(os.listdir(filed)):
        src = os.path.join(filed, f)
        if not os.path.isfile(src):
            continue
        if f.lower().endswith((".fbx", ".json") + VIDEO_EXTS):  # the video goes back with the take's files
            dest = os.path.join(export, f)
        else:
            continue                              # the .blend files stay; the new one is saved next to them
        if os.path.exists(dest):
            print(f"  redo: {f} is already in {os.path.dirname(dest)} - left where it is")
            continue
        shutil.move(src, dest)
        print(f"  redo: moved back {f}")


def resolve_take(chosen=None):
    """REDO_TAKE if set; else the take picked from the list (its folder, `chosen`); else the take named like the open
    .blend, if it has an Export folder; else the first one."""
    if not os.path.isdir(EXPORT_ROOT):
        raise RuntimeError(f"Export folder not found: {EXPORT_ROOT}")
    if REDO_TAKE:
        redo_take(REDO_TAKE)
    takes = list_takes()
    if REDO_TAKE and REDO_TAKE not in [take[0] for take in takes]:
        raise RuntimeError(f"REDO_TAKE: no exported files for '{REDO_TAKE}' in {EXPORT_ROOT} or {OUTPUT_ROOT}")
    if not takes:
        print(f"  nothing to import in {EXPORT_ROOT}:")
        entries = sorted(os.listdir(EXPORT_ROOT))
        for entry in entries[:20]:
            path = os.path.join(EXPORT_ROOT, entry)
            inside = sorted(os.listdir(path)) if os.path.isdir(path) else None
            print(f"    {entry}" + ("" if inside is None else
                  f"\\  ({', '.join(inside[:6]) + (' ...' if len(inside) > 6 else '') if inside else 'empty'})"))
        if not entries:
            print("    (the folder is empty)")
        print("  export_animations.py (in Unreal) writes a folder per take here, with <take>_body.fbx in it. If it ran,")
        print("  its Output Log says where it wrote: <project>\\Saved\\AnimationExports means it didn't find the")
        print("  workspace's settings - run create_workspace.py again, then export_animations.py from the workspace's Scripts.")
        raise RuntimeError(f"No take folders with a *_body.fbx in {EXPORT_ROOT}: run export_animations.py in Unreal first "
                           "(the console lists what is in the folder)")
    if chosen:
        for take in takes:
            if os.path.normcase(take[1]) == os.path.normcase(chosen):
                return take
        raise RuntimeError(f"The take picked ({chosen}) isn't in {EXPORT_ROOT} any more")
    blend = bpy.data.filepath
    name = os.path.splitext(os.path.basename(blend))[0] if blend else None
    for wanted in (REDO_TAKE, name):
        for take in takes:
            if wanted and take[0] == wanted:
                return take
    return takes[0]


# --- face board, keyed straight from the exported numbers ---
# Switches and frames of the face board: never animated (the same list the face board add-on leaves out).
FACE_BOARD_SKIP = ("CTRL_rigLogicSwitch", "CTRL_lookAtSwitch", "CTRL_faceGUIfollowHead", "CTRL_eyesAimFollowHead",
                   "CTRL_rigLogic", "CTRL_expressions", "CTRL_faceGUI")


def read_face_board(path):
    """The face board keys written by export_face_board.py, or None when the file isn't one."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        float(data["frame_rate"])
        return data if data.get("kind") == "face_board" and data.get("curves") else None
    except Exception as e:
        print(f"  face: couldn't read {os.path.basename(path)}: {e}")
        return None


def face_board_object(controls):
    """The face board armature: FACE_TARGET when it is in the file, else the armature whose bones are these controls."""
    obj = bpy.data.objects.get(FACE_TARGET) if FACE_TARGET else None
    if obj is not None and obj.type == "ARMATURE":
        return obj
    best = None
    for other in bpy.data.objects:
        if other.type != "ARMATURE" or not other.pose:
            continue
        hits = sum(1 for c in controls if c in other.pose.bones)
        if hits >= max(3, len(controls) // 2) and (best is None or hits > best[0]):
            best = (hits, other)
    if best:
        print(f"  face: no object named '{FACE_TARGET}'; using '{best[1].name}', which has {best[0]} of the "
              f"{len(controls)} face board controls as bones")
    return best[1] if best else None


def whole_frames(frames, values, origin, fps):
    """The keys (source frames, values) as [frame, value, ...] on the TARGET_FPS timeline; frame 1 = `origin`."""
    place = lambda f: 1.0 + (f - origin) * TARGET_FPS / fps
    if not BAKE_POSES or len(frames) < 2:
        return [x for f, v in zip(frames, values) for x in (place(f), v)]
    co, i = [], 0                                 # one key on every whole frame, values read off the straight lines
    first, last = place(frames[0]), place(frames[-1])
    frame = int(first) if float(first).is_integer() else int(first) + 1
    while frame <= last + 1e-6:
        source = origin + (frame - 1.0) * fps / TARGET_FPS
        while i < len(frames) - 2 and frames[i + 1] <= source:
            i += 1
        a, b = frames[i], frames[i + 1]
        t = min(1.0, max(0.0, (source - a) / (b - a))) if b > a else 0.0
        co += [float(frame), values[i] + (values[i + 1] - values[i]) * t]
        frame += 1
    return co


def fbx_loose_sliders(path):
    """
    How many slider controls a Control Rig FBX stores as custom properties instead of on translation.
    FBX importers (Blender's own and the face board add-on's) don't read those, so such a file only
    carries the 2D controls; the *_face_board.json next to it has every control.
    """
    try:
        with open(path, "rb") as fh:
            return len(re.findall(rb"CTRL_[A-Za-z0-9_]+\x00\x01AnimCurveNode", fh.read()))
    except OSError:
        return 0


def build_face_board(path, name):
    """
    A plain face board for a file that has none: an armature with one bone per face board control in
    the export, laid out in a grid beside the origin. Bones point up, so a slider's value moves its
    bone up and a 2D control's X moves it sideways. Cells are 2.5 wide and a full slider travels 1,
    so controls stay in their own cell. FACE_BOARD_SCALE sets the size of the whole board (as object
    scale, like the body's plain skeleton). Returns the armature object, or None.
    """
    data = read_face_board(path)
    if data is None:
        return None
    controls = sorted({c.split(".")[0] for c in data["curves"]
                       if c.split(".")[0] not in FACE_BOARD_SKIP and len(c.split(".")) <= 2})
    if not controls:
        return None
    columns, cell, length = 12, 2.5, 0.75
    rows = (len(controls) + columns - 1) // columns
    armature = bpy.data.armatures.new(name)
    board = bpy.data.objects.new(name, armature)
    bpy.context.scene.collection.objects.link(board)
    make_active(board.name)
    bpy.ops.object.mode_set(mode="EDIT")
    for i, control in enumerate(controls):
        bone = armature.edit_bones.new(control)
        x, z = (i % columns) * cell, (rows - 1 - i // columns) * cell + 1.0      # first row on top, all above the floor
        bone.head, bone.tail = (x, 0.0, z), (x, 0.0, z + length)
    bpy.ops.object.mode_set(mode="OBJECT")
    size = FACE_BOARD_SCALE * 0.03            # FACE_BOARD_SCALE 1 is the first version of this board (0.9 units wide)
    board.scale = (size, size, size)
    board.location = (size * 8.0, 0.0, 0.0)   # beside the avatar, not on top of it
    return board


def import_face_board(path, action_name, board=None):
    """
    Key the face board from <performance>_face_board.json, for files where FACE_OP can't run.
    A slider control goes to its bone's Y location and a 2D control (name.X, name.Y) to its X and Y
    location: the mapping the face board add-on uses. The keys go straight onto the TARGET_FPS
    timeline, with the take's first frame on frame 1. Returns the new action, or None.
    """
    data = read_face_board(path)
    if data is None:
        return None
    fps = float(data["frame_rate"])
    curves, shared = data["curves"], data.get("frames")
    controls = sorted({name.split(".")[0] for name in curves})
    if board is None:
        print(f"  face: {len(curves)} face board channels in {os.path.basename(path)} at {fps:g} fps")
        board = face_board_object(controls)
    if board is None:
        armatures = [o.name for o in bpy.data.objects if o.type == "ARMATURE"]
        print(f"  face: this file has no face board - there is no armature named '{FACE_TARGET}', and no "
              f"armature has bones named like {', '.join(controls[:4])}.")
        print(f"        Armatures in this file: {', '.join(armatures[:10]) if armatures else 'none'}"
              + (" ..." if len(armatures) > 10 else ""))
        return None
    per_control = {}
    for name in curves:
        per_control[name.split(".")[0]] = per_control.get(name.split(".")[0], 0) + 1
    origin = data.get("start_frame")
    if origin is None:
        origin = data.get("first_key", shared[0] if shared else 0)

    action = bpy.data.actions.new(action_name)
    animation = board.animation_data or board.animation_data_create()
    animation.action = action
    keyed, left_out, no_bone = 0, [], []
    for name in sorted(curves):
        parts = name.split(".")
        if parts[0] in FACE_BOARD_SKIP or len(parts) > 2:        # switches, and world-space controls (eye aim)
            left_out.append(name)
            continue
        index = 1 if len(parts) == 1 else {"x": 0, "y": 1, "z": 2}.get(parts[1].lower())
        if index is None and per_control[parts[0]] == 1:        # a control with a single channel is a slider
            index = 1
        curve = curves[name]
        frames, values = (shared, curve) if isinstance(curve, list) else (curve.get("frames"), curve.get("values"))
        if index is None or not frames or len(frames) != len(values):
            left_out.append(name)
            continue
        if parts[0] not in board.pose.bones:
            no_bone.append(name)
            continue
        co = whole_frames(frames, values, origin, fps)
        fc = action.fcurve_ensure_for_datablock(board, f'pose.bones["{parts[0]}"].location', index=index)
        fc.keyframe_points.clear()
        fc.keyframe_points.add(len(co) // 2)
        fc.keyframe_points.foreach_set("co", co)
        for kp in fc.keyframe_points:
            kp.interpolation = "LINEAR"
        fc.update()
        keyed += 1
    print(f"  face: {keyed} channels keyed onto '{board.name}' (action '{action.name}')")
    if left_out:
        print(f"    {len(left_out)} left out (switches and eye-aim controls), e.g.: {', '.join(left_out[:6])}")
    if no_bone:
        print(f"    {len(no_bone)} have no bone of that name on '{board.name}', e.g.: {', '.join(no_bone[:6])}")
    if not keyed:
        animation.action = None
        bpy.data.actions.remove(action)
        return None
    return action


def face_timing(folder):
    """(seconds from the take's first frame to the face's first key, seconds from its first to its last key) or None."""
    board_file = pick(folder, "_face_board.json")
    data = read_face_board(board_file) if board_file else None
    try:
        fps = float(data["frame_rate"])
        first, last = float(data["first_key"]), float(data["last_key"])
        start = data.get("start_frame")
        return (first - float(first if start is None else start)) / fps, (last - first) / fps
    except Exception:
        return None


# --- 1. import body + face FBX ---
def face_target():
    """FACE_TARGET, else the face board the DNA add-on made in this file (an armature named *_face_gui)."""
    if FACE_TARGET and FACE_TARGET in bpy.data.objects:
        return FACE_TARGET
    found = next((o.name for o in bpy.data.objects if o.type == "ARMATURE" and o.name.endswith("_face_gui")), None)
    return found or FACE_TARGET


def import_take(folder):
    """
    Returns (how the body came in, the body FBX, the face's actions, the face actions already on the
    TARGET_FPS timeline): how is "op" (BODY_OP), "plain" (plain FBX import) or None.
    """
    how = None
    body = pick(folder, "_body.fbx")
    if body:
        found = make_active(BODY_TARGET)
        result = call_op(BODY_OP, body) if BODY_OP else "none"
        if result is True:
            print(f"  body: ok ({os.path.basename(body)})")
            how = "op"
        else:
            if result == "none":
                pass
            elif result == "poll":
                print(f"  body: {BODY_OP} can't run in this file"
                      + (f" - there is no object named '{BODY_TARGET}' for it to import onto" if BODY_TARGET and not found
                         else " - its add-on doesn't find what it needs here (is the control rig set up in this file?)"))
            else:
                print(f"  body: {BODY_OP} failed")
            for fallback in BODY_FALLBACK_OPS:
                make_active("")
                done = call_op(fallback, body, global_scale=BODY_SCALE)
                if done == "args":               # no scale option on this importer: import at its own size
                    done = call_op(fallback, body)
                    print(f"  body: {fallback} has no scale option - imported at its own size, not x{BODY_SCALE:g}")
                if done is True:
                    print(f"  body: imported as a plain skeleton with {fallback}{'' if result == 'none' else ' instead'}, at x{BODY_SCALE:g} size "
                          f"({os.path.basename(body)})")
                    how = "plain"
                    break
            else:
                print(f"  body: failed ({os.path.basename(body)})")
    else:
        print("  body: no *_body.fbx in the take folder")

    face_actions, face_timed = [], []
    face = pick(folder, "_face.fbx")
    board_file = pick(folder, "_face_board.json")
    loose = fbx_loose_sliders(face) if face else 0
    if face and loose and FACE_BOARD_JSON and board_file:
        print(f"  face: {os.path.basename(face)} stores {loose} slider controls as custom properties, which FBX "
              f"importers don't read; using {os.path.basename(board_file)} instead, it has every control")
    elif face:
        if loose:
            print(f"  face: {os.path.basename(face)} stores {loose} slider controls as custom properties, which FBX "
                  "importers don't read - only its 2D controls will come through")
        actions_before = set(bpy.data.actions)
        found = make_active(face_target())
        result = call_op(FACE_OP, face, match_frame_rate=True)
        face_actions = [a for a in bpy.data.actions if a not in actions_before]
        if result is True and face_actions:
            print(f"  face: ok ({os.path.basename(face)})")
        elif result == "poll":
            print(f"  face: {FACE_OP} can't run in this file"
                  + (f" - there is no object named '{FACE_TARGET}' for it to import onto" if FACE_TARGET and not found
                     else " - its add-on doesn't find what it needs here (is the face board set up in this file?)"))
        elif result is True:
            print(f"  face: {FACE_OP} ran but imported nothing ({os.path.basename(face)})")
        else:
            print(f"  face: {FACE_OP} failed ({os.path.basename(face)})")
    if not face_actions and FACE_BOARD_JSON and board_file:
        stem = re.sub(r"_board$", "", os.path.splitext(os.path.basename(board_file))[0])
        action = import_face_board(board_file, stem)
        if action:
            face_actions, face_timed = [action], [action]
    elif not face and not board_file:
        print("  face: no *_face.fbx or *_face_board.json in this export")
    return how, body, face_actions, face_timed


def name_actions(actions, base):
    """Give the imported action(s) a proper name instead of the importer's 'Unreal Take'."""
    for i, action in enumerate(actions):
        old = action.name
        action.name = base if i == 0 else f"{base}.{old}"
        print(f"  action '{old}' renamed to '{action.name}'")


def name_armatures(armatures, action_name):
    """Name the imported skeleton after its action, with 'body' replaced by 'take' (instead of 'root.001')."""
    base = re.sub(r"_body$", "_take", action_name, flags=re.I)
    if base == action_name:
        base = f"{action_name}_take"
    for i, obj in enumerate(armatures):
        old = obj.name
        obj.name = base if i == 0 else f"{base}.{i:03d}"
        obj.data.name = obj.name
        print(f"  skeleton '{old}' renamed to '{obj.name}'")


def loose(s):
    """Curve / shape key names compared without case, punctuation or the 'CTRL_expressions_' prefix."""
    s = "".join(c for c in s.lower() if c.isalnum())
    return s[len("ctrlexpressions"):] if s.startswith("ctrlexpressions") else s


def sample(frames, values, frame):
    """Value at `frame` on straight lines between the keys (held before the first and after the last key)."""
    if frame <= frames[0]:
        return values[0]
    if frame >= frames[-1]:
        return values[-1]
    lo, hi = 0, len(frames) - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if frames[mid] <= frame:
            lo = mid
        else:
            hi = mid
    span = frames[hi] - frames[lo]
    return values[lo] + (values[hi] - values[lo]) * ((frame - frames[lo]) / span if span else 0.0)


def import_face_curves(curves_path, action_name):
    """
    Key the export's expression curves (the CTRL_expressions_* curves of the body animation) onto the
    avatar's shape keys, on the TARGET_FPS timeline. A shape key listed in FACE_SHAPE_MAP gets the
    average of its curves; any other shape key gets the curve with the same name, if there is one.
    Prints what it found, so the map can be corrected. Returns the number of shape keys keyed.
    """
    try:
        with open(curves_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        fps = float(data["frame_rate"])
        curves = data.get("curves") or {}
    except Exception as e:
        print(f"  face curves: couldn't read {os.path.basename(curves_path)}: {e}")
        return 0
    names = sorted(curves)
    if not names:
        print(f"  face curves: {os.path.basename(curves_path)} holds no curves")
        return 0
    print(f"  face curves: {len(names)} curves in {os.path.basename(curves_path)} at {fps:g} fps")
    print(f"    curve names, e.g.: {', '.join(names[:10])}" + (" ..." if len(names) > 10 else ""))

    shared = data.get("frames")
    origin = shared[0] if shared else 0.0         # the animation's first frame lands on frame 1, like the body
    series = {}                                   # loose curve name -> (frames, values)
    for name in names:
        curve = curves[name]
        frames, values = (shared, curve) if isinstance(curve, list) else (curve.get("frames"), curve.get("values"))
        if frames and len(frames) == len(values):
            series[loose(name)] = (frames, values)
    idle = {loose(n) for n in data.get("idle_curves") or []}     # curves of the animation that never leave 0
    knows_idle = "idle_curves" in data

    wanted = {loose(key): [loose(src) for src in sources] for key, sources in FACE_SHAPE_MAP.items()}
    targets = {}                                  # loose shape key name -> [(shape key datablock, key block)]
    for mesh in bpy.data.meshes:
        keys = mesh.shape_keys
        if not keys:
            continue
        blocks = [kb for kb in keys.key_blocks if kb != keys.reference_key]
        print(f"    shape keys on '{mesh.name}': {len(blocks)}, e.g. {', '.join(kb.name for kb in blocks[:6])}"
              + (" ..." if len(blocks) > 6 else ""))
        for kb in blocks:
            targets.setdefault(loose(kb.name), []).append((keys, kb))
    if not targets:
        print("    no mesh in this file has shape keys, so there is nothing to key the curves onto")

    mapped = by_name = 0
    at_rest, no_source, unknown, used, actions = [], [], set(), set(), {}
    for key in sorted(targets):
        label = targets[key][0][1].name
        sources = wanted.get(key) or ([key] if key in series else [])
        if not sources:
            no_source.append(label)
            continue
        found = [src for src in sources if src in series]
        if knows_idle:
            unknown.update(src for src in sources if src not in series and src not in idle)
        if not found:
            at_rest.append(label)                 # every curve behind this shape key is idle in this take
            continue
        used.update(found)
        frames = sorted({f for src in found for f in series[src][0]})
        values = [FACE_SHAPE_GAIN * sum(sample(series[src][0], series[src][1], f) for src in found) / len(sources)
                  for f in frames]
        co = whole_frames(frames, values, origin, fps)
        for keys, kb in targets[key]:
            if keys not in actions:
                ad = keys.animation_data or keys.animation_data_create()
                if ad.action is None:
                    ad.action = bpy.data.actions.new(re.sub(r"_body$", "", action_name) + "_face_shapekeys")
                actions[keys] = ad.action
            fc = actions[keys].fcurve_ensure_for_datablock(keys, f'key_blocks["{kb.name}"].value')
            fc.keyframe_points.clear()
            fc.keyframe_points.add(len(co) // 2)
            fc.keyframe_points.foreach_set("co", co)
            fc.update()
            kb.slider_min = min(kb.slider_min, min(values))
            kb.slider_max = max(kb.slider_max, max(values))
        if key in wanted:
            mapped += 1
        else:
            by_name += 1
    keyed = mapped + by_name
    print(f"    {keyed} shape keys keyed from the expression curves ({mapped} through FACE_SHAPE_MAP, {by_name} by name)")
    if at_rest:
        print(f"    {len(at_rest)} left at rest, their expressions don't move in this take: {', '.join(at_rest[:12])}"
              + (" ..." if len(at_rest) > 12 else ""))
    if no_source:
        print(f"    {len(no_source)} shape keys have no curve assigned in FACE_SHAPE_MAP: {', '.join(no_source[:14])}"
              + (" ..." if len(no_source) > 14 else ""))
    if unknown:
        print(f"    NOT CURVES OF THIS ANIMATION (check these names in FACE_SHAPE_MAP): {', '.join(sorted(unknown))}")
    spare = [n for n in names if loose(n) not in used and loose(n) not in ("bodyframesolved", "mhfdsversion", "disablefaceoverride")]
    if targets and spare:
        print(f"    {len(spare)} moving curves drive no shape key, e.g.: {', '.join(spare[:12])}"
              + (" ..." if len(spare) > 12 else ""))
    return keyed


# --- retarget the plain skeleton onto the avatar's control bones ---
def quat_of(matrix):
    """Rotation of a matrix as a quaternion, whatever its scale."""
    return matrix.to_3x3().normalized().to_quaternion()


def quat_power(q, power):
    """The same rotation, `power` times as far (used where a control is only followed at part strength)."""
    if abs(power - 1.0) < 1e-6:
        return q
    axis, angle = q.to_axis_angle()
    if angle > math.pi:
        angle -= 2.0 * math.pi
    return Quaternion(axis, max(-math.pi, min(math.pi, angle * power)))


def angle_deg(q):
    """Size of a rotation in degrees, 0..180."""
    return math.degrees(2.0 * math.acos(max(-1.0, min(1.0, abs(q.w)))))


def control_map_of(target):
    """Skeleton bone -> control bone for this armature: the map the MetaHuman rig carries, else RETARGET_MAP."""
    stored = target.get("sgm_control_map") if target is not None else None
    if stored:
        try:
            return dict(json.loads(stored))
        except (TypeError, ValueError):
            pass
    return RETARGET_MAP


def is_metahuman_rig(obj):
    return obj is not None and obj.type == "ARMATURE" and obj.get("sgm_rig") == "metahuman"


def find_retarget_armature(source):
    """
    The armature to key: RETARGET_ARMATURE; else a MetaHuman rig in this file (not the source itself); else
    the armature holding the most control bones of RETARGET_MAP (the Galtis avatar).
    """
    if RETARGET_ARMATURE:
        obj = bpy.data.objects.get(RETARGET_ARMATURE)
        return obj if obj is not None and obj.type == "ARMATURE" else None
    usable = [obj for obj in bpy.data.objects if obj.type == "ARMATURE" and obj is not source and obj.pose
              and obj.name in bpy.context.view_layer.objects]
    rigs = [obj for obj in usable if is_metahuman_rig(obj) and not is_metahuman_rig(source)]
    if rigs:
        return rigs[0]
    best = None
    for obj in usable:
        if is_metahuman_rig(obj):
            continue
        hits = sum(1 for control in RETARGET_MAP.values() if control in obj.pose.bones)
        if hits >= max(3, len(RETARGET_MAP) // 4) and (best is None or hits > best[0]):
            best = (hits, obj)
    return best[1] if best else None


def retarget_pairs(source, target, cmap=None):
    """
    One entry per skeleton bone that is retargeted, parents before children:
      name     the skeleton bone
      control  the avatar bone that is keyed
      deform   the avatar bone that has to end up oriented like the skeleton bone: the bone with the
               skeleton bone's own name when the avatar has one (Galtis does), else the control itself
      driver   the bone the deform bone is attached to with a Child Of constraint, else the deform bone.
               It turns exactly as the control's own rotation turns it.
      gain     how much of the control the driver follows (the influence of its copy constraint)
      parent   index of the nearest retargeted bone above it in the skeleton, or None
    """
    skipped = tuple(RETARGET_SKIP)
    cmap = cmap or control_map_of(target)
    pairs, missing = [], []
    def depth(bone):
        return 0 if bone.parent is None else 1 + depth(bone.parent)

    for bone in sorted(source.data.bones, key=depth):          # parents before children
        control = cmap.get(bone.name)
        if control is None or (skipped and bone.name.startswith(skipped)):
            continue
        if control not in target.pose.bones:
            missing.append(control)
            continue
        deform = bone.name if bone.name in target.pose.bones else control
        driver, gain = deform, 1.0
        for constraint in target.pose.bones[deform].constraints:
            if constraint.type == "CHILD_OF" and constraint.target is target and constraint.subtarget \
                    and not constraint.mute and constraint.influence > 0.0:
                driver = constraint.subtarget
        for constraint in target.pose.bones[driver].constraints:
            if getattr(constraint, "target", None) is target and getattr(constraint, "subtarget", "") == control \
                    and not constraint.mute and constraint.influence > 0.0:
                gain = max(0.2, min(1.0, constraint.influence))
        parent, above = None, bone.parent
        while above is not None and parent is None:
            parent = next((i for i, pair in enumerate(pairs) if pair["name"] == above.name), None)
            above = above.parent
        pairs.append({"name": bone.name, "control": control, "deform": deform, "driver": driver,
                      "gain": gain, "parent": parent})
    return pairs, missing


def retarget_rest(source, target, pairs):
    """
    What the two rest poses say. Returns (turn, scale, offsets, notes):
      turn     rotation about the vertical that turns the skeleton to face the way the avatar faces
      scale    avatar size / skeleton size, for the hip's travel
      offsets  per pair, the constant rotation that takes the skeleton bone's world rotation to the
               rotation the avatar's deform bone should have
    The avatar's rest pose need not match the skeleton's: each bone's rest direction (towards its
    RETARGET_AIM bone) is compared, and the difference is taken out. Nor do the bones need the same
    axes: only how far each bone has turned from its own rest pose is carried over.
    """
    s_world, t_world = source.matrix_world, target.matrix_world
    s_bones, t_bones = source.data.bones, target.data.bones
    s_head = lambda name: s_world @ s_bones[name].head_local
    t_head = lambda name: t_world @ t_bones[name].head_local
    both = lambda name: name in s_bones and name in t_bones
    notes = []

    turn = Quaternion()
    for left, right in (("upperarm_l", "upperarm_r"), ("clavicle_l", "clavicle_r"), ("thigh_l", "thigh_r")):
        if both(left) and both(right):
            a, b = s_head(right) - s_head(left), t_head(right) - t_head(left)
            if a.xy.length > 1e-6 and b.xy.length > 1e-6:
                yaw = math.atan2(b.y, b.x) - math.atan2(a.y, a.x)
                yaw = (yaw + math.pi) % (2.0 * math.pi) - math.pi
                if abs(yaw) > math.radians(3.0):
                    turn = Quaternion((0.0, 0.0, 1.0), yaw)
                    notes.append(f"the skeleton faces {math.degrees(yaw):.0f} degrees away from the avatar; turned to match")
            break

    scale = 1.0
    if both("pelvis") and both("head"):
        a, b = (s_head("head") - s_head("pelvis")).length, (t_head("head") - t_head("pelvis")).length
        if a > 1e-6 and b > 1e-6:
            scale = b / a

    offsets, aligns, swings = [], [], []
    for pair in pairs:
        name = pair["name"]
        aim = RETARGET_AIM.get(name)
        if aim and both(name) and both(aim):
            a, b = turn @ (s_head(aim) - s_head(name)), t_head(aim) - t_head(name)
            align = a.rotation_difference(b) if a.length > 1e-9 and b.length > 1e-9 else Quaternion()
        elif pair["parent"] is not None and name != "pelvis":
            align = aligns[pair["parent"]]
        else:
            align = Quaternion()
        aligns.append(align)
        above = aligns[pair["parent"]] if pair["parent"] is not None else Quaternion()
        swings.append((angle_deg(above.inverted() @ align), name))     # where this bone differs from the one above it
        s_rest = quat_of(s_world @ s_bones[name].matrix_local)
        t_rest = quat_of(t_world @ t_bones[pair["deform"]].matrix_local)
        offsets.append(s_rest.inverted() @ turn.inverted() @ align.inverted() @ t_rest)
    worst = [(a, n) for a, n in sorted(swings, reverse=True) if a > 5.0]
    if worst:
        notes.append("rest poses differ at " + ", ".join(f"{n} {a:.0f} deg" for a, n in worst[:6])
                     + (f" and {len(worst) - 6} more" if len(worst) > 6 else "")
                     + " (and so in the bones below them); the difference is taken out")
    else:
        notes.append("rest poses match (every bone within 5 degrees)")
    # A neutral rest pose is the same left and right. If the skeleton's isn't, its rest pose is a posed frame.
    by_name = {name: angle for angle, name in swings}
    lopsided = sorted(((abs(angle - by_name[name[:-2] + "_r"]), name[:-2]) for name, angle in by_name.items()
                       if name.endswith("_l") and name[:-2] + "_r" in by_name), reverse=True)
    if lopsided and lopsided[0][0] > 15.0:
        notes.append("WARNING: left and right differ in the rest poses (" + ", ".join(
            f"{n} by {a:.0f} deg" for a, n in lopsided[:4] if a > 15.0) + "). One of the two rest poses is not a "
            "neutral pose, so the retarget will be off; tell me which bones are listed here.")
    return turn, scale, offsets, notes


def write_rotation(pose_bone, rotation, previous):
    """Set a pose bone's rotation in its own rotation mode, continuing smoothly from `previous`. Returns the values."""
    mode = pose_bone.rotation_mode
    if mode == "QUATERNION":
        if previous is not None and Quaternion(previous).dot(rotation) < 0.0:
            rotation = -rotation
        pose_bone.rotation_quaternion = rotation
        return tuple(rotation)
    if mode == "AXIS_ANGLE":
        axis, angle = rotation.to_axis_angle()
        pose_bone.rotation_axis_angle = (angle, axis.x, axis.y, axis.z)
        return (angle, axis.x, axis.y, axis.z)
    euler = rotation.to_euler(mode, Euler(previous, mode)) if previous is not None else rotation.to_euler(mode)
    pose_bone.rotation_euler = euler
    return tuple(euler)


def retarget_body(source, action_name, target=None):
    """
    Key the avatar's control bones so its body follows the imported skeleton `source`, on every frame
    of the scene. For each retargeted bone and frame: work out the rotation the avatar's deform bone
    should have (the skeleton bone's rotation carried over through the rest poses), compare it with
    where the rig puts that bone now, and turn the control by the difference. The comparison is made
    against the bone above, so a correction higher up doesn't throw off the bones below; a second
    pass per frame mops up what the rig's blended bones (upper spine, neck) leave over.
    Returns the new action, or None.
    """
    bpy.context.view_layer.update()                # object transforms current before anything is measured
    target = target or find_retarget_armature(source)
    if target is None:
        print("  retarget: no avatar armature found" + (f" named '{RETARGET_ARMATURE}'" if RETARGET_ARMATURE else
              f" - no MetaHuman rig, and none with the control bones of RETARGET_MAP "
              f"({', '.join(list(RETARGET_MAP.values())[:4])} ...)") + " - the body stays on the plain skeleton")
        return None
    pairs, missing = retarget_pairs(source, target)
    if missing:
        print(f"  retarget: {len(missing)} control bones of the map are not on '{target.name}': "
              + ", ".join(missing[:8]) + (" ..." if len(missing) > 8 else ""))
    if not pairs:
        print("  retarget: nothing to retarget")
        return None
    turn, scale, offsets, notes = retarget_rest(source, target, pairs)
    print(f"  retarget: '{source.name}' -> '{target.name}', {len(pairs)} control bones"
          + (f" (left alone: bones starting with {', '.join(RETARGET_SKIP)})" if RETARGET_SKIP else ""))
    for note in notes:
        print(f"    {note}")
    partial = [f"{p['control']} x{p['gain']:g}" for p in pairs if p["gain"] < 0.999]
    if partial:
        print(f"    followed at part strength, so turned further: {', '.join(partial)}")

    scene, layer = bpy.context.scene, bpy.context.view_layer
    s_pose, t_pose = source.pose.bones, target.pose.bones
    frames = list(range(scene.frame_start, scene.frame_end + 1))
    hips = pairs[0] if RETARGET_HIP_LOCATION and pairs[0]["name"] == "pelvis" else None
    to_target = quat_of(target.matrix_world).inverted()
    world_to_target = target.matrix_world.inverted()
    pelvis_rest_s = source.matrix_world @ source.data.bones["pelvis"].head_local if hips else None
    pelvis_rest_t = target.matrix_world @ target.data.bones[hips["deform"]].head_local if hips else None

    animation = target.animation_data or target.animation_data_create()
    old_action = animation.action
    animation.action = None                         # so the frame changes below don't put the controls back
    # The FK controls are what gets keyed, so arms and legs must follow FK. With an FK/IK switch left on IK, the
    # limbs ignore the FK controls and the solve turns them round and round looking for a change that never comes.
    switch = target.pose.bones.get("FKIK Switch")
    were_ik = []
    if switch is not None:
        for key in [k for k in switch.keys() if re.search(r"IKFK_[LR]$", k)]:
            try:
                if float(switch[key]) > 1e-6:
                    were_ik.append(f"{key} {float(switch[key]):g}")
                switch[key] = type(switch[key])(0)
            except (TypeError, ValueError):
                pass
        target.update_tag()
        layer.update()
    if were_ik:
        print(f"    arms and legs set to FK for the retarget (were on IK: {', '.join(were_ik)}); they stay on FK")
    if animation.nla_tracks and any(not t.mute for t in animation.nla_tracks):
        print(f"    WARNING: '{target.name}' has animation layers / NLA tracks ({', '.join(t.name for t in animation.nla_tracks)}); "
              "they are evaluated on top and will change the result. Run the import in a file where the rig has none.")
    hidden = [o for o in layer.objects if o.type == "MESH" and not o.hide_viewport]
    for obj in hidden:
        obj.hide_viewport = True                    # the meshes needn't be rebuilt on every frame of the solve
    start_frame = scene.frame_current
    started = time.perf_counter()
    rotations = [[] for _ in pairs]                 # per pair: the control's rotation values on every frame
    locations = []
    sample_every = max(1, len(frames) // 24)
    errors, worst_error = [], (0.0, "", 0)
    hips_note, first_miss = "", None
    try:
        for index, frame in enumerate(frames):
            scene.frame_set(frame)
            wanted = [to_target @ turn @ quat_of(source.matrix_world @ s_pose[p["name"]].matrix) @ offsets[i]
                      for i, p in enumerate(pairs)]
            wanted_position = None
            if hips:
                travel = (source.matrix_world @ s_pose["pelvis"].matrix).translation - pelvis_rest_s
                wanted_position = world_to_target @ (pelvis_rest_t + scale * (turn @ travel))
            for sweep in range(max(8, RETARGET_SWEEPS) if index == 0 else max(1, RETARGET_SWEEPS)):
                if sweep:
                    layer.update()
                now = [quat_of(t_pose[p["deform"]].matrix) for p in pairs]
                changes = []
                for i, p in enumerate(pairs):
                    if p["parent"] is None:
                        error = now[i].inverted() @ wanted[i]
                    else:
                        j = p["parent"]
                        error = (now[j].inverted() @ now[i]).inverted() @ (wanted[j].inverted() @ wanted[i])
                    attach = quat_of(t_pose[p["driver"]].matrix).inverted() @ now[i]
                    changes.append(quat_power(attach @ error @ attach.inverted(), 1.0 / p["gain"]))
                move = None
                if hips:                            # worked out before the rotations below change anything
                    control, deform, driver = t_pose[hips["control"]], t_pose[hips["deform"]], t_pose[hips["driver"]]
                    miss = wanted_position - deform.matrix.translation
                    if first_miss is None:
                        first_miss, hips_start = miss.length, control.location.copy()
                    control_now = quat_of(control.matrix)
                    carried = (quat_of(driver.matrix) @ control_now.inverted()).inverted() @ miss
                    frame_of_basis = control_now @ quat_of(control.matrix_basis).inverted()
                    move = frame_of_basis.inverted() @ carried
                for i, p in enumerate(pairs):
                    control = t_pose[p["control"]]
                    basis = quat_of(control.matrix_basis)
                    previous = rotations[i][-1] if rotations[i] else None
                    write_rotation(control, basis @ changes[i], previous)
                if move is not None:
                    t_pose[hips["control"]].location = t_pose[hips["control"]].location + move
            for i, p in enumerate(pairs):
                control = t_pose[p["control"]]
                mode = control.rotation_mode
                rotations[i].append(tuple(control.rotation_quaternion) if mode == "QUATERNION" else
                                    tuple(control.rotation_axis_angle) if mode == "AXIS_ANGLE" else
                                    tuple(control.rotation_euler))
            if hips:
                locations.append(tuple(t_pose[hips["control"]].location))
            if index % sample_every == 0:           # how close did it get? checked on a spread of frames
                layer.update()
                if hips and index == 0:             # does moving the hip control move the hips at all?
                    left = (wanted_position - t_pose[hips["deform"]].matrix.translation).length
                    if first_miss > 1e-4 * max(1.0, scale) and left > 0.5 * first_miss:
                        t_pose[hips["control"]].location = hips_start
                        hips_note = (f"the hips stay where they are: moving '{hips['control']}' doesn't move "
                                     f"'{hips['deform']}' (a limit on that control?). Rotations only.")
                        hips, locations = None, []
                        layer.update()
                for i, p in enumerate(pairs):
                    off = angle_deg(quat_of(t_pose[p["deform"]].matrix).inverted() @ wanted[i])
                    errors.append(off)
                    if off > worst_error[0]:
                        worst_error = (off, p["name"], frame)
    finally:
        for obj in hidden:
            obj.hide_viewport = False

    action = bpy.data.actions.new(action_name)
    animation.action = action
    xs = [float(f) for f in frames]

    def write_curve(path, index, values, group):
        fc = action.fcurve_ensure_for_datablock(target, path, index=index, group_name=group)
        fc.keyframe_points.clear()
        fc.keyframe_points.add(len(values))
        fc.keyframe_points.foreach_set("co", [v for pair in zip(xs, values) for v in pair])
        fc.update()

    for i, p in enumerate(pairs):
        mode = t_pose[p["control"]].rotation_mode
        prop = {"QUATERNION": "rotation_quaternion", "AXIS_ANGLE": "rotation_axis_angle"}.get(mode, "rotation_euler")
        for channel in range(len(rotations[i][0])):
            write_curve(f'pose.bones["{p["control"]}"].{prop}', channel, [v[channel] for v in rotations[i]], p["control"])
    if hips:
        for channel in range(3):
            write_curve(f'pose.bones["{hips["control"]}"].location', channel, [v[channel] for v in locations],
                        hips["control"])
    scene.frame_set(start_frame)

    print(f"    keyed {len(pairs)} controls on frames {frames[0]}-{frames[-1]}"
          + (f", hips moved (avatar is {scale:.2f} x the skeleton's size)" if hips else "")
          + f"; action '{action.name}' on '{target.name}' ({time.perf_counter() - started:.0f} s)")
    if hips_note:
        print(f"    {hips_note}")
    if errors:
        mean = sum(errors) / len(errors)
        print(f"    remaining error on {len(errors) // len(pairs)} sampled frames: {mean:.2f} deg on average, "
              f"{worst_error[0]:.1f} deg at worst ({worst_error[1]}, frame {worst_error[2]})")
        if worst_error[0] > 3.0:
            print(f"    that is more than 3 degrees: set RETARGET_SWEEPS above {RETARGET_SWEEPS} and import again. If it "
                  "stays, the rig isn't following that control (an IK switch that is on, or a constraint in between).")
    if old_action is not None:
        print(f"    the armature's earlier action '{old_action.name}' is still in the file, no longer assigned")
    return action


# --- 2. read source video timing ---
def read_video(path):
    clip = bpy.data.movieclips.load(path)
    src_fps, src_frames = clip.fps, clip.frame_duration
    bpy.data.movieclips.remove(clip)

    duration = src_frames / src_fps
    print(f"  video: {path}")
    print(f"  {src_fps:.3f} fps, {src_frames} frames, {duration:.3f}s")
    return src_fps, duration


def action_fcurves(action):
    """Every F-curve of an action (Blender 4.4+ keeps them in layers / strips / channelbags)."""
    if hasattr(action, "fcurves"):
        return list(action.fcurves)
    return [fc for layer in action.layers for strip in layer.strips
            for bag in strip.channelbags for fc in bag.fcurves]


def export_length(folder):
    """
    (frame rate, seconds from first to last key) of the exported animation, from its curves file.
    Either can be None when the file doesn't say.
    """
    for f in files_with(folder, "_curves.json"):
        try:
            with open(os.path.join(folder, f), "r", encoding="utf-8") as fh:
                data = json.load(fh)
            fps = float(data["frame_rate"])
            if data.get("length_seconds"):
                return fps, float(data["length_seconds"])
            if isinstance(data.get("frames"), list) and len(data["frames"]) > 1:
                return fps, (data["frames"][-1] - data["frames"][0]) / fps
            ends = [(c["frames"][0], c["frames"][-1]) for c in data.get("curves", {}).values()
                    if isinstance(c, dict) and c.get("frames")]         # curves keyed on different frames
            if ends and max(e for _, e in ends) > min(b for b, _ in ends):
                return fps, (max(e for _, e in ends) - min(b for b, _ in ends)) / fps
            return fps, None
        except Exception:
            continue
    return None, None


def key_span(actions):
    """(first, last) key frame over the given actions."""
    xs = [kp.co.x for a in actions for fc in action_fcurves(a) for kp in fc.keyframe_points]
    return (min(xs), max(xs)) if xs else (1.0, 1.0)


# --- 3. retime actions + set scene rate ---
def set_scene(duration):
    """Scene at TARGET_FPS, as long as the video. Returns the last frame."""
    new_end = max(1, int(round(duration * TARGET_FPS)))
    scn = bpy.context.scene
    scn.render.fps = TARGET_FPS
    scn.render.fps_base = 1.0
    scn.frame_start = 1
    scn.frame_end = new_end
    return new_end


def retime(actions, src_fps, duration, anim_seconds=None, anim_fps=None, first_frame=1.0):
    """
    Put the imported keys on a TARGET_FPS timeline starting at frame 1 (`first_frame`: where the
    first key lands, for a face that starts later than the take).
    The animation's speed comes from the animation, not from the video (Unreal can export a
    25 fps video's animation at 30 fps). In order of preference:
      anim_seconds - how long the exported animation is, first to last key: the keys are scaled
                     to exactly that long, whatever frame numbers the importer gave them
      anim_fps     - the animation's frame rate: the keys are taken to be on its frames
      src_fps      - the video's frame rate (the old behaviour), when nothing else is known
    The scene length still comes from `duration` (the video).
    """
    first, last = key_span(actions)
    if anim_seconds and last > first:
        factor = anim_seconds * TARGET_FPS / (last - first)
        why = f"the animation is {anim_seconds:.3f}s long"
    elif anim_fps:
        factor = TARGET_FPS / anim_fps
        why = f"the animation is {anim_fps:g} fps"
    else:
        factor, first = TARGET_FPS / src_fps, 1.0
        why = f"keys taken to be on video frames, {src_fps:g} fps"
    new_end = set_scene(duration)
    print(f"  retime factor {factor:.4f} -> {new_end} frames @ {TARGET_FPS}fps"
          f" ({why}; imported keys ran from frame {first:g} to {last:g})")
    if abs(factor - TARGET_FPS / src_fps) > 1e-3:
        print(f"  note: the video is {src_fps:g} fps but the animation isn't; the animation's own timing is used")

    if abs(factor - 1.0) <= 1e-6 and abs(first - first_frame) <= 1e-6:
        print("  source already at target rate — no retime needed")
        return

    for action in actions:
        for fc in action_fcurves(action):
            for kp in fc.keyframe_points:
                kp.co.x           = first_frame + (kp.co.x - first) * factor
                kp.handle_left.x  = first_frame + (kp.handle_left.x - first) * factor
                kp.handle_right.x = first_frame + (kp.handle_right.x - first) * factor
            fc.update()
        print(f"  retimed action: {action.name}")


# --- 4. bake armature poses onto whole frames ---
def bake_armatures(actions):
    scn = bpy.context.scene
    for obj in bpy.data.objects:
        ad = obj.animation_data
        if obj.type != "ARMATURE" or not (ad and ad.action) or ad.action not in actions:
            continue
        if bpy.context.object and bpy.context.object.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        bpy.ops.object.select_all(action="DESELECT")
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj
        bpy.ops.object.mode_set(mode="POSE")
        bpy.ops.nla.bake(
            frame_start=scn.frame_start,
            frame_end=scn.frame_end,
            step=1,
            only_selected=False,
            visual_keying=True,
            clear_constraints=False,
            clear_parents=False,
            use_current_action=True,
            bake_types={"POSE"},
        )
        bpy.ops.object.mode_set(mode="OBJECT")
        print(f"  baked: {obj.name}")


# --- 5. file the take: new folder, save .blend there, move the exported files + video in ---
def unique_path(path):
    """path, or path with _2, _3 ... if something is already there."""
    if not os.path.exists(path):
        return path
    stem, ext = os.path.splitext(path)
    i = 2
    while os.path.exists(f"{stem}_{i}{ext}"):
        i += 1
    return f"{stem}_{i}{ext}"


def move_into(src, dest_dir):
    dest = unique_path(os.path.join(dest_dir, os.path.basename(src)))
    try:
        shutil.move(src, dest)
        print(f"  moved: {os.path.basename(src)} -> {dest}")
    except OSError as e:
        print(f"  could not move {src}: {e}")


def file_take(name, export_dir, video):
    take_dir = os.path.join(OUTPUT_ROOT, name)
    os.makedirs(take_dir, exist_ok=True)

    # save first, so the work is safe even if a move fails
    blend_path = unique_path(os.path.join(take_dir, f"{name}.blend"))
    bpy.ops.wm.save_as_mainfile(filepath=blend_path, relative_remap=False)
    print(f"[saved] {blend_path}")

    # every exported file of the take: all FBX and curves files, used or not
    for f in files_with(export_dir, ".fbx") + files_with(export_dir, ".json"):
        move_into(os.path.join(export_dir, f), take_dir)
    if video and os.path.isfile(video) and os.path.dirname(os.path.abspath(video)) != os.path.abspath(export_dir):
        move_into(video, take_dir)
    for v in videos_in(export_dir):
        move_into(v, take_dir)

    # drop the take's export folder if nothing is left in it
    if os.path.isdir(export_dir) and not os.listdir(export_dir):
        os.rmdir(export_dir)
        print(f"  removed empty folder: {export_dir}")
    print(f"[filed] {take_dir}")


# --- run ---
def main(chosen=None):
    """Import one take. chosen: the folder of the take picked from the list; "" = none picked (settings already
    read); None = read the settings first (main() on its own)."""
    if chosen is None:
        print(f"[import_unreal_take] version {VERSION}")
        resolve_settings()
    check_dna_addon()
    name, take_folder = resolve_take(chosen)
    print(f"[take] {name}   ({take_folder})")

    # find the video BEFORE importing, so a missing video doesn't leave a half-done file
    video = VIDEO_OVERRIDE if VIDEO_OVERRIDE and os.path.isfile(VIDEO_OVERRIDE) else find_video(name, take_folder)
    if not video:
        close = near_misses(name)
        print(f"  no video match for '{name}'.")
        if close:
            print("  closest candidates:")
            for p in close:
                print(f"    {p}")
        else:
            print(f"  no video in {', '.join(VIDEO_SEARCH_ROOTS) or '(no video folders set)'} shares a word with the take name.")
        if REQUIRE_VIDEO:
            raise RuntimeError(f"No source video for '{name}' in its export folder or "
                               f"{' or '.join(VIDEO_SEARCH_ROOTS) or '(no video folders set)'}"
                               + (f" (closest: {'; '.join(os.path.basename(p) for p in close[:5])})" if close else "")
                               + " - set VIDEO_OVERRIDE to the video's full path, rename/move the video, "
                               "or set REQUIRE_VIDEO = False to use the export's own timing.")
    timing = read_video(video) if video else None

    before = set(bpy.data.actions)
    objects_before = set(bpy.data.objects)
    fps_before = bpy.context.scene.render.fps / bpy.context.scene.render.fps_base
    body_how, body_file, face_actions, face_timed = import_take(take_folder)
    fps_after = bpy.context.scene.render.fps / bpy.context.scene.render.fps_base
    new_actions = [a for a in bpy.data.actions if a not in before]
    body_actions = [a for a in new_actions if a not in face_actions]
    face_untimed = [a for a in face_actions if a not in face_timed]     # imported by FACE_OP: still on the file's frames
    print(f"  {len(new_actions)} new action(s) imported"
          + (f" ({len(body_actions)} body, {len(face_actions)} face)" if face_actions else ""))

    if new_actions:
        export_fps, anim_seconds = export_length(take_folder)   # the animation's own rate and length, if the export says
        if timing:
            src_fps, duration = timing
        else:
            scn = bpy.context.scene
            src_fps = export_fps or scn.render.fps / scn.render.fps_base
            first_key, last_key = key_span(body_actions or new_actions)
            duration = anim_seconds or max(last_key - first_key, 1.0) / src_fps
            print(f"  no video: using the export's timing, {src_fps:.3f} fps, {duration:.3f}s")
        # the animation's frame rate: Blender's FBX import sets the scene to it; else what the export says
        if body_how == "plain" or abs(fps_after - fps_before) > 1e-6:
            anim_fps = fps_after
        else:
            anim_fps = export_fps
        if body_actions:
            retime(body_actions, src_fps, duration, anim_seconds, anim_fps)
        if face_untimed:                              # the face keeps its own length and its own start within the take
            offset, face_seconds = face_timing(take_folder) or (0.0, anim_seconds)
            print("  face timing:")
            retime(face_untimed, src_fps, duration, face_seconds, anim_fps, first_frame=1.0 + offset * TARGET_FPS)
        if not body_actions and not face_untimed:
            set_scene(duration)
        if BAKE_POSES:                                # (face keys placed from the numbers are on whole frames already)
            bake_armatures(body_actions + face_untimed)
    else:
        print("  nothing imported — skipping retime")

    # name the action after the file it came from, and bring in the face curves
    take_label = name
    if body_file:
        take_label = {"take": name,
                      "video": os.path.splitext(os.path.basename(video))[0] if video else name,
                      }.get(ACTION_NAME, os.path.splitext(os.path.basename(body_file))[0])
    if body_actions and body_how == "plain":
        take_skeletons = [o for o in bpy.data.objects if o not in objects_before and o.type == "ARMATURE"]
        name_actions(body_actions, take_label)
        name_armatures(take_skeletons, take_label)
        if RETARGET and take_skeletons:
            try:
                target = find_retarget_armature(take_skeletons[0])
                suffix = "_mh" if is_metahuman_rig(target) else "_retarget"
                retarget_body(take_skeletons[0], re.sub(r"_body$", "", take_label) + suffix, target)
            except Exception as e:                    # the take is still imported and filed without it
                print(f"  retarget: stopped - {type(e).__name__}: {e}")
                traceback.print_exc()
    if ACTION_NAME in ("take", "video"):
        for action in face_timed:
            action.name = f"{take_label}_face"
    face_keyed = 0
    if FACE_CURVES and body_file and new_actions and not face_actions:
        curves_file = os.path.splitext(body_file)[0] + "_curves.json"
        if os.path.isfile(curves_file):
            face_keyed = import_face_curves(curves_file, take_label)
        else:
            print("  face curves: this animation has no curves file")
    # no face board in this file: keep the face board animation on a plain one, like the body's plain skeleton
    board_file = pick(take_folder, "_face_board.json") if FACE_STANDIN and FACE_BOARD_JSON and not face_actions else None
    if board_file:
        face_stem = re.sub(r"_board$", "", os.path.splitext(os.path.basename(board_file))[0])
        if ACTION_NAME in ("take", "video"):
            face_stem = f"{take_label}_face"
        standin = build_face_board(board_file, f"{face_stem}_board")
        action = import_face_board(board_file, face_stem, standin) if standin else None
        if action:
            if not new_actions and timing:
                set_scene(timing[1])
            face_actions = [action]
            print(f"  face: made a plain face board, '{standin.name}' (size x{FACE_BOARD_SCALE:g}), and keyed the face "
                  "board animation onto it.")
        elif standin:
            bpy.data.objects.remove(standin)
    new_actions = [a for a in bpy.data.actions if a not in before]

    if new_actions:
        file_take(name, take_folder, video)
    elif bpy.data.filepath:
        bpy.ops.wm.save_mainfile()
        print(f"[saved] {bpy.data.filepath}")


# --- picking the take ---
_take_items = []


def take_items(self, context):
    return _take_items


def describe_take(folder):
    files = os.listdir(folder)
    parts = [label for label, ok in (("body", any(f.lower().endswith("_body.fbx") for f in files)),
                                     ("face", any(f.lower().endswith(("_face.fbx", "_face_board.json")) for f in files)),
                                     ("video", any(f.lower().endswith(VIDEO_EXTS) for f in files))) if ok]
    when = time.strftime("%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(folder)))
    return f"{', '.join(parts)} - exported {when}"


class SGM_OT_import_unreal_take_choose(bpy.types.Operator):
    """Pick the take to import from the ones waiting in the Unreal Exports folder"""
    bl_idname = "sgm.import_unreal_take_choose"
    bl_label = "Import which take?"
    bl_property = "take"

    take: bpy.props.EnumProperty(name="Take", items=take_items)

    def invoke(self, context, event):
        context.window_manager.invoke_search_popup(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        index = int(self.take) if str(self.take).isdigit() else -1
        if not 0 <= index < len(_take_folders):
            return {"CANCELLED"}
        try:
            main(chosen=_take_folders[index])
        except Exception as err:
            traceback.print_exc()
            self.report({"ERROR"}, f"import_unreal_take: {err}")
            return {"CANCELLED"}
        return {"FINISHED"}


_take_folders = []


def start():
    """The take to import: picked from a list when several are waiting (CHOOSE_TAKE), else straight on."""
    print(f"[import_unreal_take] version {VERSION}")
    resolve_settings()
    if CHOOSE_TAKE and not REDO_TAKE and not bpy.app.background and os.path.isdir(EXPORT_ROOT):
        takes = sorted(list_takes(), key=lambda take: take[0].lower())
        if len(takes) > 1:
            _take_folders[:] = [folder for _name, folder in takes]
            _take_items[:] = [(str(i), name, describe_take(folder)) for i, (name, folder) in enumerate(takes)]
            try:
                bpy.utils.unregister_class(bpy.types.SGM_OT_import_unreal_take_choose)
            except (AttributeError, RuntimeError):
                pass
            bpy.utils.register_class(SGM_OT_import_unreal_take_choose)
            print(f"  {len(takes)} takes waiting in {EXPORT_ROOT}: pick one in the list (type to search it)")
            bpy.ops.sgm.import_unreal_take_choose("INVOKE_DEFAULT")
            return
    main(chosen="")                                  # "" = no take picked (the first one), settings already read


if __name__ == "__main__":
    start()
