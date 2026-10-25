"""
pipeline_config - shared settings for the Unreal and Blender pipeline scripts.

Every path and name that differs from one person's setup to the next lives in one file,
pipeline_config.json, at the top of the workspace folder (create_workspace.py makes the folder and
the file). Paths in it are relative to that folder, so a workspace can be moved to another drive
and keep working; a path on another drive (a video folder, say) can be written in full.

Finding the config (first match wins):
  1. the SIGN_PIPELINE_CONFIG environment variable (full path to the .json)
  2. walking up from a starting file or folder (a script, a .blend, a take folder) until a folder
     holding pipeline_config.json is found - scripts kept in <workspace>/Scripts find it this way
  3. the workspace create_workspace.py last made or updated on this computer (it notes the config's
     path in ~/.sign_pipeline.json), so scripts run from anywhere else find it too

Usage (any Python: Unreal, Blender, the command line):

    import pipeline_config as pc
    cfg = pc.load_config(__file__)                       # or bpy.data.filepath in Blender
    videos = pc.project_path(cfg, "video_source")        # an absolute folder
    wip = pc.get(cfg, "unreal.wip_folder")               # "/Game/WIP"

Keys missing from a config file come from DEFAULTS, so an older config keeps working after an update.
"""

import copy
import json
import os

CONFIG_FILENAME = "pipeline_config.json"
ENV_VAR = "SIGN_PIPELINE_CONFIG"
POINTER_FILE = os.path.join(os.path.expanduser("~"), ".sign_pipeline.json")   # {"config": "<path>"}
CONFIG_VERSION = 2

DEFAULTS = {
    "config_version": CONFIG_VERSION,
    "project_name": "Sign Pipeline",
    # Folders, relative to the workspace (the folder holding pipeline_config.json)
    "paths": {
        "scripts": "Scripts",                       # every pipeline script (Unreal, Blender, export), in one folder
        "video_source": "Videos/Labelled",          # the labelled source videos, before Unreal
        "video_unlabelled": "Videos/Unlabelled",    # videos not labelled yet
        "video_holding": "Videos/Holding",          # videos set aside (the export doesn't look here)
        "capture_media": "Capture Media",           # Capture Manager's converted footage (Ingest.py sets it)
        "unreal_exports": "Unreal Exports",         # export_animations.py writes one folder per take in here
        "metahuman_export": "MetaHuman Export",     # export_metahuman.py writes the character here
        "metahuman": "MetaHuman",                   # the Blender MetaHuman files (phase 2):
        "character_blend": "MetaHuman/MetaHuman_character.blend",   # build_metahuman_rig.py saves the character here
        "neutral_pose": "MetaHuman/neutral_pose.json",              # save_neutral_pose.py / metahuman_procedural_fixes.py
        "reference_template": "MetaHuman/Xsens_reference.fbx",      # any Xsens MVN FBX; export_reference_fbx.py
                                                                    #   takes the Reference skeleton from it
        "takes": ".",                               # the take folders Blender works in (<workspace>/<take>)
        "avatar": "AvatarFolder",                   # the Galtis avatar .blend
        "reference_rigs": "Reference Rigs",         # the rigs Process Rigs.py converts to (you supply them): SKM_Manny_Simple.FBX,
                                                    #   SKM_Quinn_Simple.FBX, SKM_GaltisMH_BodyMesh.FBX, GaltisRPM.glb,
                                                    #   X Bot.fbx, SOMA_rig2.fbx, reze.pmx
        "storage": "Storage",                       # pose libraries and stored animations (Storage/Galtis ...)
        "ready_for_upload": "Ready for Upload",     # the Bundler's copies of each finished take
        "logs": "Logs",
        # older keys, still read by older scripts
        "scripts_blender": "Scripts",
        "scripts_unreal": "Scripts",
        "scripts_common": "Scripts",
        "blender_clips": ".",
    },
    "unreal": {
        "project": "",                              # the .uproject (optional; for the record and create_workspace's notes)
        "wip_folder": "/Game/WIP",                  # export_animations.py looks for takes here only
        "completed_folder": "/Game/Completed",      # ... and moves the exported ones here
        "character_folder": "/Game/MetaHumans",     # MetaHuman Character assets are kept here
        "metahuman_blueprint": "",                  # the MetaHuman the takes are put on; "" = the one in character_folder
        "identity": "",                             # a MetaHuman Identity for every take; "" = none (each mono video take
                                                    #   is solved from the video alone - needed when the clips are of
                                                    #   different people)
        "identities": {},                           # {text in a take's name: Identity} for known performers,
                                                    #   e.g. {"SGM": "/Game/Identities/MHI_SGM"}
        "output_path": "/Game/PCap",                # where processed performances / animation sequences go
        "body_tracking": True,
        "required_plugins": [                       # checked by check_plugins.py (internal names)
            "MetaHuman",
            "MetaHumanBodyTracker",
            "MetaHumanCalibrationProcessing",
            "MetaHumanCharacter",
            "MetaHumanCoreTech",
            "MetaHumanDepthProcessing",
            "MetaHumanGenerator",
            "MetaHumanLiveLink",
            "ModelingToolsEditorMode",
            "PerformanceCaptureCore",
            "PerformanceCaptureToolkit",
            "PerformanceCaptureWorkflow",
        ],
    },
    "video": {
        "action": "move",                           # "move" = a take's video goes into its export folder; "copy"; "" = leave
        "extra_folders": [],                        # more folders to look for videos in, after video_source
        "slate_drop_words": [],                     # words left out of a take's name, e.g. ["SGM"]: "SGM Hello.mp4" -> Hello_tk1
    },
    "blender": {
        "body_rig": "GaltisMH_control_rig",
        "face_rig": "GaltisMH_face_gui",
        "face_armature_data": "face_gui",
        "required_addons": [
            ["Animation Layers", "Animation_Layers"],
            ["Rigify", "rigify"],
            ["Character Control Rig", "bl_ext.polyhammer.character_control_rig"],
        ],
        "defaults": {
            "ease_in": 30,
            "ease_out": 30,
            "leg_pin": 20,
            "plant_feet": True,
            "match_poles": True,
            "face": True,
            "face_ease_in": 30,
            "face_ease_out": 30,
        },
    },
    # Optional program locations (leave empty if not needed)
    "tools": {
        "blender_exe": "",
        "unreal_editor": "",
        "live_link_hub": "",
    },
}

# The folders create_workspace.py makes (keys of "paths")
WORKSPACE_FOLDERS = ("scripts", "video_source", "video_unlabelled", "video_holding", "capture_media", "unreal_exports",
                     "metahuman_export", "metahuman", "avatar", "reference_rigs", "storage", "ready_for_upload", "logs")


def deep_merge(base, override):
    """Copy of `base` with `override` laid on top (nested dicts merged, other values replaced)."""
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def _walk_up(start):
    folder = os.path.abspath(start)
    if os.path.isfile(folder) or not os.path.isdir(folder):
        folder = os.path.dirname(folder)
    while True:
        candidate = os.path.join(folder, CONFIG_FILENAME)
        if os.path.isfile(candidate):
            return candidate
        parent = os.path.dirname(folder)
        if parent == folder:
            return None
        folder = parent


def remembered_config():
    """The config create_workspace.py noted on this computer, or None."""
    try:
        with open(POINTER_FILE, "r", encoding="utf-8") as fh:
            path = json.load(fh).get("config")
        return path if path and os.path.isfile(path) else None
    except (OSError, ValueError, AttributeError):
        return None


def remember_config(path):
    """Note `path` as this computer's workspace config (read by remembered_config)."""
    try:
        with open(POINTER_FILE, "w", encoding="utf-8") as fh:
            json.dump({"config": os.path.abspath(path)}, fh, indent=2)
        return True
    except OSError:
        return False


def find_config(start=None):
    """Path of pipeline_config.json, or None. `start` may be a file or a folder, or a list of them."""
    env = os.environ.get(ENV_VAR)
    if env and os.path.isfile(env):
        return os.path.abspath(env)
    starts = start if isinstance(start, (list, tuple)) else [start]
    for s in [s for s in starts if s] or [os.getcwd()]:
        found = _walk_up(s)
        if found:
            return found
    return remembered_config()


def load_config(start=None, required=False):
    """
    Settings merged over DEFAULTS. Adds "_config_path" and "_root" (the workspace folder).
    required=True raises FileNotFoundError when no config is found; otherwise the defaults are
    returned with "_root" = None (and project_path() then gives the relative defaults as they are).
    """
    path = find_config(start)
    if path is None:
        if required:
            raise FileNotFoundError(
                f"No {CONFIG_FILENAME} found above {start or os.getcwd()}. "
                f"Run create_workspace.py, or set {ENV_VAR} to the config file.")
        cfg = copy.deepcopy(DEFAULTS)
        cfg["_config_path"] = None
        cfg["_root"] = None
        return cfg
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    cfg = deep_merge(DEFAULTS, data)
    cfg["_config_path"] = path
    cfg["_root"] = os.path.dirname(path)
    return cfg


def get(cfg, dotted, default=None):
    """A setting by its dotted name: get(cfg, "unreal.wip_folder"). `default` when it's missing or empty."""
    value = cfg
    for part in dotted.split("."):
        if not isinstance(value, dict) or part not in value:
            return default
        value = value[part]
    return default if value in (None, "") else value


def project_path(cfg, key):
    """Absolute path for a key in cfg["paths"] (relative entries are resolved from the workspace
    folder; absolute ones, e.g. a video folder on another drive, are used as-is)."""
    value = cfg["paths"][key]
    value = os.path.expandvars(os.path.expanduser(value))
    if os.path.isabs(value) or cfg.get("_root") is None:
        return os.path.normpath(value)
    return os.path.normpath(os.path.join(cfg["_root"], value))


def setting_path(cfg, key, fallback=None):
    """project_path(cfg, key), or `fallback` when there are no settings or no such key (an older config module)."""
    try:
        return project_path(cfg, key) if cfg and cfg.get("_root") else fallback
    except (KeyError, TypeError):
        return fallback


def save_config(cfg, path):
    """Write settings (without the runtime "_" keys) as pretty JSON."""
    clean = {k: v for k, v in cfg.items() if not k.startswith("_")}
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(clean, fh, indent=2)
        fh.write("\n")
