"""
Export MetaHuman animation out of Unreal for Blender, in batches.

It looks in one folder only: SOURCE_PATH, the project's Content/WIP folder, sub-folders included.
(Unreal shows the project's Content folder as /Game, so <project>/Content/WIP is /Game/WIP in
the Content Browser and in the log. SOURCE_PATH and COMPLETED_PATH can be written either way.)
Each folder directly under it is a take, e.g. Content/WIP/Nice_to_Meet_You_tk1.

For every Animation Sequence in there it writes into the export folder, in a folder named after
the take:
    <name>_body.fbx              the skeleton with its animation (no mesh), for Blender's FBX import
    <name>_body_curves.json      the animation's curves, one value per frame. MetaHuman Animator
                                 stores the face here, as expression curves (CTRL_expressions_*)
and, for every MetaHuman Performance in the same folders (done by export_face_board.py, which
has to sit next to this script):
    <performance>_face.fbx          the face on the face control board, as a Control Rig FBX -
                                    the file Blender's face board import reads
    <performance>_face_board.json   the same face board keys as numbers
and the take's source video, from VIDEO_FOLDERS (the Labelled folder), moved in next to them, so the
video travels with its take (see "the take's source video" below for how it is found);
and one  export_manifest.json  listing everything that was exported, for the Blender side.
(An animation on a face skeleton - "face" in the skeleton's name - is written as <name>_face.fbx.)

Then every take that was exported completely is moved to COMPLETED_PATH (Content/Completed) and
saved there, so the next run only finds what is still to do. A take stays in SOURCE_PATH when
anything of it wasn't exported: an animation or the face failed, its Performance isn't processed
yet, or some of its animations weren't part of this run. The log says why for each one.

SOURCE_PATH and COMPLETED_PATH are created when they don't exist.

Run in the Unreal Editor: File > Execute Python Script.
What it changes in the project: the face export creates one Level Sequence per Performance
(LS_FaceBoard_<performance>), and finished takes are moved. Nothing else.
A summary goes to the Output Log and to <export folder>/export_report.txt.

Settings: the folders and names below are left as None, and come from the workspace's
pipeline_config.json (made by create_workspace.py): unreal.wip_folder, unreal.completed_folder,
unreal.character_folder, paths.unreal_exports, paths.video_source (+ video.extra_folders) and
video.action. Without a config: /Game/WIP, /Game/Completed, /Game/MetaHumans,
<project>/Saved/AnimationExports, no video folder, "move". A value written here wins over the config.
"""

import json
import os
import re
import shutil
import sys
import time

import unreal

# ------------------------------------------------------------------ settings
SOURCE_PATH = None          # the only folder that is searched (sub-folders included). None = unreal.wip_folder
COMPLETED_PATH = None       # finished takes are moved here. None = unreal.completed_folder
                            # (both can be Content Browser paths, "/Game/WIP", or the folder on disk)
MOVE_COMPLETED = True       # False = export only, leave every take where it is
CHARACTER_FOLDER = None     # a MetaHuman Character asset found in a take folder is moved here first, so it
                                        # doesn't go to Completed with the take ("" = leave it in the take)
EXPORT_FOLDER = None        # one folder per take in here; the Blender import (import_unreal_take.py) reads the
                            # same folder. None = paths.unreal_exports, else <project>/Saved/AnimationExports
USE_SELECTION = False       # True = only the Animation Sequences selected in the Content Browser (inside SOURCE_PATH)
NAME_FILTER = ""            # only export assets whose name contains this text (case-insensitive)
EXPORT_FBX = True           # skeleton + animation as FBX
EXPORT_CURVES = True        # animation curves as JSON (written only for animations that have curves)
EXPORT_FACE_BOARD = True    # also export each take's face board (runs export_face_board.py from this folder)
INCLUDE_MESH = False        # True = also put the preview mesh in the FBX (much larger files)
OVERWRITE = True            # False = skip files that already exist
FACE_SKELETON_HINTS = ("face",)     # a skeleton with one of these in its name is a face
SHOW_DIALOG = True
FACE_SEQUENCE_PREFIX = "LS_FaceBoard_"      # the Level Sequences export_face_board.py makes
FACE_CURVE_PREFIX = "ctrl_expressions"      # MetaHuman Animator's face expression curves start with this
FACE_BOARD_SCRIPT = "export_face_board.py"
# The take's source video goes into the take's export folder, next to its FBX and JSON files.
VIDEO_FOLDERS = None        # searched in this order, sub-folders included, e.g. [r"D:\Videos\Labelled"].
                            # None = paths.video_source and video.extra_folders
VIDEO_ACTION = None         # "move" = the video leaves VIDEO_FOLDERS for the take's folder; "copy" = a copy goes there,
                            # the original stays; "" = leave the videos alone. None = video.action ("move")
VIDEO_MAP = {}              # take -> its video's file name, for a take whose video isn't found by name, e.g.
                            # {"ASL_Jesus_tk1": "2026-10-15 SGM ASL Jesus.mp4"}
VIDEOS_ONLY = False         # True = export nothing: only give the take folders already in the export folder their videos
VIDEO_EXTS = (".mp4", ".mov", ".m4v", ".avi", ".mkv")
VIDEO_COMMON_WORDS = ("sgm", "sg", "asl")     # in nearly every name, so they don't count when names are compared
TAKE_PREFIXES = ("AS_MHP_", "MHP_", "AS_", "A_", "CD_", "LS_")
MANIFEST_NAME = "export_manifest.json"
INGEST_LOG = "ingested_videos.json"   # written by Ingest.py (in the workspace's Logs folder): take -> its video
REPORT_NAME = "export_report.txt"

ASSETS = getattr(unreal, "EditorAssetLibrary", None)
SEQ_LIB = getattr(unreal, "LevelSequenceEditorBlueprintLibrary", None)

_lines = []


def out(text=""):
    _lines.append(str(text))
    unreal.log(str(text))


def warn(text):
    _lines.append("WARNING: " + str(text))
    unreal.log_warning(str(text))


def attempt(func, default=None):
    try:
        return func()
    except Exception:
        return default


def _full(path):
    return os.path.normpath(unreal.Paths.convert_relative_path_to_full(path))


# ------------------------------------------------------------------ settings from the config
def content_path(path):
    """
    (Content Browser path, None) or (None, why not). Takes a Content Browser path (/Game/WIP) or the
    same folder as it is on disk (<project>/Content/WIP, either kind of slash): the project's Content folder is /Game.
    """
    text = str(path or "").strip().strip('"').replace("\\", "/").rstrip("/")
    if not text:
        return None, "no folder is set"
    if text.startswith("/") and ":" not in text:                # already a Content Browser path
        return text, None
    content = attempt(lambda: unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_content_dir()), "") or ""
    content = str(content).replace("\\", "/").rstrip("/")
    if content and (text.lower() + "/").startswith(content.lower() + "/"):
        rest = text[len(content):].strip("/")
        return "/Game" + ("/" + rest if rest else ""), None
    return None, (f"{path} is not inside this project's Content folder ({content or 'unknown'}). Use a folder inside "
                  "it, or a Content Browser path such as /Game/WIP.")


def pipeline_settings():
    """(pipeline_config module, settings) from the workspace's pipeline_config.json, or (None, None) without one.
    pipeline_config.py is looked for next to this script (the workspace's Scripts folder) and in ../common."""
    here = os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals() else ""
    for extra in ((here, os.path.join(os.path.dirname(here), "common")) if here else ()):
        if os.path.isdir(extra) and extra not in sys.path:
            sys.path.append(extra)
    try:
        import pipeline_config
    except ImportError:
        return None, None
    project = attempt(lambda: _full(unreal.Paths.project_dir()), "")
    cfg = attempt(lambda: pipeline_config.load_config([p for p in (here, project) if p]))
    return (pipeline_config, cfg) if cfg and cfg.get("_config_path") else (pipeline_config, None)


def load_settings():
    """Fill the settings left as None (above) from the config, else the defaults. Returns (export folder on disk,
    config path or None)."""
    global SOURCE_PATH, COMPLETED_PATH, CHARACTER_FOLDER, EXPORT_FOLDER, VIDEO_FOLDERS, VIDEO_ACTION
    pc, cfg = pipeline_settings()
    get = (lambda key, default: pc.get(cfg, key, default)) if cfg else (lambda key, default: default)
    if SOURCE_PATH is None:
        SOURCE_PATH = get("unreal.wip_folder", "/Game/WIP")
    if COMPLETED_PATH is None:
        COMPLETED_PATH = get("unreal.completed_folder", "/Game/Completed")
    if CHARACTER_FOLDER is None:
        CHARACTER_FOLDER = get("unreal.character_folder", "/Game/MetaHumans")
    if EXPORT_FOLDER is None:
        EXPORT_FOLDER = (pc.project_path(cfg, "unreal_exports") if cfg
                         else os.path.join(_full(unreal.Paths.project_saved_dir()), "AnimationExports"))
    if VIDEO_FOLDERS is None:
        VIDEO_FOLDERS = ([pc.project_path(cfg, "video_source")] + list(get("video.extra_folders", []))) if cfg else []
    if VIDEO_ACTION is None:
        VIDEO_ACTION = cfg["video"].get("action", "move") if cfg else "move"
    # Ingest.py's record of which video became which take: exact, where the names alone could match two videos
    log = os.path.join(pc.project_path(cfg, "logs"), INGEST_LOG) if cfg else \
        os.path.join(_full(unreal.Paths.project_saved_dir()), INGEST_LOG)
    try:
        with open(log, "r", encoding="utf-8") as fh:
            for name, entry in json.load(fh).items():
                take = entry.get("take") if isinstance(entry, dict) else None
                if take and take not in VIDEO_MAP:
                    VIDEO_MAP[take] = entry.get("video") or name
    except (OSError, ValueError, AttributeError):
        pass
    return os.path.normpath(EXPORT_FOLDER), (cfg or {}).get("_config_path")


# ------------------------------------------------------------------ finding the animations
def _class_name(asset_data):
    try:
        return str(asset_data.asset_class_path.asset_name)
    except Exception:
        return str(getattr(asset_data, "asset_class", ""))


def selected_animations():
    library = getattr(unreal, "EditorUtilityLibrary", None)
    if library is None:
        return []
    try:
        return [a for a in library.get_selected_assets() if isinstance(a, unreal.AnimSequence)]
    except Exception:
        return []


def animations_under(source):
    registry = unreal.AssetRegistryHelpers.get_asset_registry()
    rows = [d for d in registry.get_assets_by_path(source, True) if _class_name(d) == "AnimSequence"]
    if not rows:                                # asked the other way round: the whole project, kept when inside `source`
        rows = [d for d in attempt(lambda: registry.get_assets_by_path("/Game", True), []) or []
                if _class_name(d) == "AnimSequence" and is_inside(str(d.package_path), source)]
    found = []
    for data in rows:
        asset = data.get_asset()
        if asset is not None:
            found.append(asset)
    return found


def describe_nothing(source, completed=None):
    """
    Nothing to export: say what `source` holds instead, and where the project's MetaHuman Animation
    Sequences are. Read-only. Returns a short version for the dialog.
    """
    registry = unreal.AssetRegistryHelpers.get_asset_registry()
    rows = list(attempt(lambda: registry.get_assets_by_path("/Game", True), []) or [])
    if not (completed and is_inside(completed, source)):
        completed = None                        # only a Completed folder inside the source is left out below
    inside, elsewhere = {}, {}
    for data in rows:
        folder, kind = str(data.package_path), _class_name(data)
        if completed and is_inside(folder, completed):
            continue                            # finished takes, when the Completed folder sits inside the source
        if is_inside(folder, source):
            take = take_of(folder, source) or source
            inside.setdefault(take, {})
            inside[take][kind] = inside[take].get(kind, 0) + 1
        elif kind == "AnimSequence" and not folder.startswith(CHARACTER_FOLDER or "/Game/MetaHumans"):
            skeleton = str(attempt(lambda: data.get_tag_value("Skeleton"), "") or "").lower()
            if "metahuman" in skeleton.rsplit("/", 1)[-1] or "face_archetype" in skeleton:
                elsewhere[folder] = elsewhere.get(folder, 0) + 1
    for path in attempt(lambda: registry.get_sub_paths(source, False), []) or []:
        if not (completed and is_inside(str(path).rstrip("/"), completed)):
            inside.setdefault(str(path).rstrip("/"), {})                # folders with nothing in them
    out(f"What is in {source}:")
    if not inside:
        out("  nothing - the folder is empty")
    waiting = []
    for take in sorted(inside):
        counts = inside[take]
        label = take[len(source):].strip("/") or "(directly in the folder)"
        listed = ", ".join(f"{n} {kind}" for kind, n in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:6])
        note = ""
        if counts.get("MetaHumanPerformance"):
            note = "   <- a Performance, but no Animation Sequence exported from it yet"
            waiting.append(label)
        out(f"  {label:<36} {listed or 'empty'}{note}")
    if elsewhere:
        out("Animation Sequences on a MetaHuman skeleton in other folders:")
        for folder, count in sorted(elsewhere.items())[:20]:
            out(f"  {count:>3}  {folder}")
        if len(elsewhere) > 20:
            out(f"  ... and {len(elsewhere) - 20} more folders")
    else:
        out("There are no Animation Sequences on a MetaHuman skeleton anywhere else in the project either.")
    short = []
    if waiting:
        short.append(f"{len(waiting)} take(s) there have a Performance but no Animation Sequence yet "
                     f"({', '.join(waiting[:4])}{' ...' if len(waiting) > 4 else ''}).")
    if elsewhere:
        folders = sorted(elsewhere)
        short.append(f"Animation Sequences are in: {', '.join(folders[:4])}{' ...' if len(folders) > 4 else ''}.")
    return " ".join(short) + " The Output Log lists what the folder holds."


def ensure_folder(path):
    """Create the Content Browser folder when it isn't there yet. Returns True when it was created."""
    if ASSETS is None or attempt(lambda: ASSETS.does_directory_exist(path), True):
        return False
    return bool(attempt(lambda: ASSETS.make_directory(path), False))


def is_inside(content_folder, parent):
    return (content_folder + "/").startswith(parent.rstrip("/") + "/")


def take_of(content_folder, source):
    """The take folder (the folder directly under `source`) that `content_folder` belongs to, or None."""
    relative = content_folder[len(source):].strip("/") if is_inside(content_folder, source) else ""
    return f"{source}/{relative.split('/')[0]}" if relative else None


def skeleton_name(animation):
    for getter in (lambda: animation.get_skeleton(), lambda: animation.get_editor_property("skeleton")):
        try:
            skeleton = getter()
            if skeleton is not None:
                return skeleton.get_name()
        except Exception:
            continue
    return ""


def kind_of(animation):
    name = skeleton_name(animation).lower()
    return "face" if any(hint in name for hint in FACE_SKELETON_HINTS) else "body"


def package_folder(animation):
    """Content Browser folder of the asset, e.g. /Game/PCap/Take1."""
    path = animation.get_path_name().split(".", 1)[0]
    return path.rsplit("/", 1)[0]


# ------------------------------------------------------------------ what an animation holds
def frame_rate(animation):
    try:
        rate = animation.get_sampling_frame_rate()
        return float(rate.numerator) / float(rate.denominator)
    except Exception:
        pass
    try:
        frames = unreal.AnimationLibrary.get_num_frames(animation)
        length = unreal.AnimationLibrary.get_sequence_length(animation)
        if frames and length:
            return round(frames / length, 3)
    except Exception:
        pass
    return 30.0


def length_seconds(animation):
    """Seconds from the first to the last key of the animation, or None."""
    for getter in (lambda: unreal.AnimationLibrary.get_sequence_length(animation), lambda: animation.get_play_length()):
        try:
            length = float(getter())
            if length > 0:
                return round(length, 6)
        except Exception:
            continue
    return None


def read_curves(animation):
    """
    ({curve name: (times in seconds, values)}, [names of the curves without keys]) for the float
    curves of the animation. A curve without keys is idle: it stays at 0 for the whole animation.
    """
    library = unreal.AnimationLibrary
    names = library.get_animation_curve_names(animation, unreal.RawCurveTrackTypes.RCT_FLOAT)
    curves, idle = {}, []
    for name in names:
        times, values = library.get_float_keys(animation, name)
        if len(times):
            curves[str(name)] = ([float(t) for t in times], [float(v) for v in values])
        else:
            idle.append(str(name))
    return curves, sorted(idle)


def curves_document(animation, kind, curves, idle=()):
    """The curves as a JSON-ready dict: one value per frame when every curve is keyed on the same frames."""
    fps = frame_rate(animation)
    first_times = next(iter(curves.values()))[0]
    same_frames = all(len(t) == len(first_times) and all(abs(a - b) < 1e-6 for a, b in zip(t, first_times))
                      for t, _ in curves.values())
    document = {
        "format": 1,
        "asset": animation.get_path_name().split(".", 1)[0],
        "kind": kind,
        "skeleton": skeleton_name(animation),
        "frame_rate": fps,
        "length_seconds": length_seconds(animation),
        "curve_count": len(curves),
        "idle_curves": list(idle),              # curves of the animation that have no keys (they stay at 0)
    }
    if same_frames:
        document["frames"] = [int(round(t * fps)) for t in first_times]
        document["curves"] = {name: [round(v, 6) for v in values] for name, (_, values) in sorted(curves.items())}
    else:                                   # curves keyed on different frames: keep each curve's own times
        document["curves"] = {name: {"frames": [round(t * fps, 4) for t in times],
                                     "values": [round(v, 6) for v in values]}
                              for name, (times, values) in sorted(curves.items())}
    return document


# ------------------------------------------------------------------ exporting
def export_fbx(animation, path):
    options = unreal.FbxExportOption()
    for name, value in (("ascii", False), ("export_preview_mesh", INCLUDE_MESH),
                        ("export_morph_targets", INCLUDE_MESH), ("map_skeletal_motion_to_root", False),
                        ("export_local_time", True), ("level_of_detail", False), ("collision", False),
                        ("vertex_color", False)):
        try:
            options.set_editor_property(name, value)
        except Exception:
            pass
    task = unreal.AssetExportTask()
    task.set_editor_property("object", animation)
    task.set_editor_property("filename", path)
    task.set_editor_property("exporter", unreal.AnimSequenceExporterFBX())
    task.set_editor_property("options", options)
    task.set_editor_property("automated", True)         # no dialogs
    task.set_editor_property("prompt", False)
    task.set_editor_property("replace_identical", True)
    unreal.Exporter.run_asset_export_task(task)
    return os.path.isfile(path) and os.path.getsize(path) > 0


# ------------------------------------------------------------------ the take's source video
# Ingest names a take after its video, more or less: "2026-10-15 SGM ASL Jesus.mp4" becomes ASL_Jesus_tk1. So a
# video is found for a take by comparing names without the date, the take number, prefixes and the words in
# VIDEO_COMMON_WORDS ("jesus" = "jesus"). Before that, what the take's own assets say: a video file named in the
# Capture Data or in an asset's import data, and the names of the take's assets. VIDEO_MAP settles the rest.
_DATE = re.compile(r"\d{4}[-_. ]\d{1,2}[-_. ]\d{1,2}")


def video_keys(text):
    """(full key, loose key) of a take or video name: 'ASL_Number_5_tk1' -> ('number5', 'number')."""
    text = os.path.splitext(os.path.basename(str(text)))[0] if os.path.splitext(str(text))[1].lower() in VIDEO_EXTS \
        else os.path.basename(str(text))
    text = re.sub(r"(?i)[_ ]?tk\d+$", "", text)
    for prefix in TAKE_PREFIXES:
        if text.startswith(prefix):
            text = text[len(prefix):]
            break
    words = [w for w in re.findall(r"[a-z]+|\d+", _DATE.sub(" ", text.lower())) if w not in VIDEO_COMMON_WORDS]
    return "".join(words), "".join(w for w in words if not w.isdigit())


def labelled_videos(skip_folder):
    """Every video in VIDEO_FOLDERS (sub-folders included), except those inside `skip_folder` (the export folder)."""
    found, skip = [], os.path.normcase(os.path.abspath(skip_folder)) + os.sep
    for base in VIDEO_FOLDERS:
        if not os.path.isdir(base):
            warn(f"Video folder not found: {base}")
            continue
        for root, dirs, files in os.walk(base):
            dirs.sort()
            if (os.path.normcase(os.path.abspath(root)) + os.sep).startswith(skip):
                continue
            found += [os.path.join(root, f) for f in sorted(files) if f.lower().endswith(VIDEO_EXTS)]
    return found


def _strings_of(value, depth=0):
    """Text found in a property value: strings, file paths, and the same inside structs and lists (a little deep)."""
    if value is None or depth > 3:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple, set)) or (hasattr(value, "__iter__") and not hasattr(value, "get_editor_property")
                                                and type(value).__name__ in ("Array", "Set", "Map")):
        return [s for item in list(value)[:64] for s in _strings_of(item, depth + 1)]
    found = []
    for getter in ("get_sequence_path", "get_path_name"):
        result = attempt(lambda: getattr(value, getter)())
        if isinstance(result, str):
            found.append(result)
    path = attempt(lambda: value.get_editor_property("path"))
    if isinstance(path, str):
        found.append(path)
    if hasattr(value, "get_editor_property") and depth < 3:
        for name in ("file_path", "file_name", "source_file", "media_path", "sequence_path", "image_sequences",
                     "audio_tracks", "depth_sequences", "metadata", "take_metadata", "source_path"):
            found += _strings_of(attempt(lambda: value.get_editor_property(name)), depth + 1)
    return found


def take_hints(content_folder):
    """(video files named by the take's assets, the names those assets give: asset names, file and folder names)."""
    if not content_folder:
        return [], []
    registry = unreal.AssetRegistryHelpers.get_asset_registry()
    rows = list(attempt(lambda: registry.get_assets_by_path(content_folder, True), []) or [])
    names, files = [str(d.asset_name) for d in rows], []
    for data in rows:
        if _class_name(data) not in ("FootageCaptureData", "MetaHumanPerformance", "ImgMediaSource", "SoundWave",
                                     "CaptureData", "MeshCaptureData"):
            continue
        asset = attempt(data.get_asset)
        if asset is None:
            continue
        texts = []
        import_data = attempt(lambda: asset.get_editor_property("asset_import_data"))
        texts += list(attempt(lambda: import_data.extract_filenames(), []) or [])
        texts += _strings_of(asset)
        footage = attempt(lambda: asset.get_editor_property("footage_capture_data"))
        if footage is not None:
            names.append(footage.get_name())
            texts += _strings_of(footage)
        exported = attempt(lambda: asset.export_text())
        if isinstance(exported, str):
            texts += re.findall(r"[A-Za-z]:[\\/][^\"'\r\n]+?\.(?:mp4|mov|m4v|avi|mkv)", exported, re.I)
        for text in texts:
            text = str(text).strip().strip('"')
            if text.lower().endswith(VIDEO_EXTS) and os.path.isfile(text):
                files.append(os.path.normpath(text))
            elif text:
                names.append(os.path.basename(text.rstrip("/\\")))
                names.append(os.path.basename(os.path.dirname(text.rstrip("/\\"))))
    return list(dict.fromkeys(files)), [n for n in dict.fromkeys(names) if n]


def recorded(path):
    """When a video was recorded, for sorting: the date in its name (2026-10-15), else the file's date."""
    found = re.search(r"(\d{4})[-_. ](\d{1,2})[-_. ](\d{1,2})", os.path.basename(path))
    stamp = attempt(lambda: os.path.getmtime(path), 0.0)
    return ((int(found.group(1)), int(found.group(2)), int(found.group(3))) if found else (0, 0, 0)), stamp


def pick_video(take_name, content_folder, videos, taken):
    """(video path, how it was found) or (None, why not)."""
    free = [v for v in videos if v not in taken]
    if take_name in VIDEO_MAP:
        wanted = VIDEO_MAP[take_name]
        hit = [v for v in videos if os.path.basename(v).lower() == os.path.basename(wanted).lower()]
        if os.path.isfile(wanted):
            return os.path.normpath(wanted), "VIDEO_MAP"
        return (hit[0], "VIDEO_MAP") if hit else (None, f"VIDEO_MAP names {wanted}, which isn't in the video folders")
    files, hints = take_hints(content_folder)
    if files:
        return files[0], "named in the take's capture data"
    keyed = [(v, video_keys(v)) for v in free]

    def unique(test):
        hits = [v for v, key in keyed if test(key)]
        return hits[0] if len(hits) == 1 else (hits if hits else None)

    for label, names in (("the take's assets", hints), ("the take's name", [take_name])):
        for full, loose in (video_keys(n) for n in names):
            if len(loose) < 3:
                continue
            for test in (lambda k: k[0] == full, lambda k: k[1] == loose):
                hit = unique(test)
                if isinstance(hit, str):
                    return hit, f"by {label}"
                if hit:                         # the same sign recorded more than once: the newest recording
                    hit = sorted(hit, key=recorded, reverse=True)
                    return hit[0], (f"by {label}; the newest of {len(hit)} videos named like it (the others: "
                                    + "; ".join(os.path.basename(h) for h in hit[1:4]) + " - VIDEO_MAP picks another)")
    full, loose = video_keys(take_name)
    hit = unique(lambda k: len(loose) >= 4 and (loose in k[1] or (len(k[1]) >= 4 and k[1] in loose)))
    if isinstance(hit, str):
        return hit, "by part of the take's name"
    near = [os.path.basename(v) for v, key in keyed if any(w in key[1] for w in re.findall(r"[a-z]{4,}", loose))]
    return None, ("no video in the video folders is named like it"
                  + (f" (closest: {'; '.join(near[:4])})" if near else "") + " - set VIDEO_MAP")


def attach_videos(folder, takes):
    """
    Put each take's source video into its export folder. `takes` is [(take name, content folder or None)].
    Returns [{take, video, from, how}] and logs a line per take.
    """
    if not VIDEO_ACTION:
        return []
    out("")
    if not VIDEO_FOLDERS:
        out("Source videos: no video folder set (paths.video_source in pipeline_config.json, or VIDEO_FOLDERS), so "
            "only videos the takes' own capture data points to are copied")
    out(f"Source videos -> the take folders ({'moved' if VIDEO_ACTION == 'move' else 'copied'} from "
        f"{'; '.join(VIDEO_FOLDERS)})")
    videos = labelled_videos(folder)
    rows, taken = [], set()
    for name, content_folder in takes:
        target = os.path.join(folder, name)
        os.makedirs(target, exist_ok=True)
        there = [f for f in sorted(os.listdir(target)) if f.lower().endswith(VIDEO_EXTS)]
        if there:
            out(f"  {name:<40} has its video already: {there[0]}")
            rows.append({"take": name, "video": f"{name}/{there[0]}", "how": "already there"})
            continue
        video, how = pick_video(name, content_folder, videos, taken)
        if video is None:
            warn(f"  {name}: no video - {how}")
            rows.append({"take": name, "video": None, "why": how})
            continue
        taken.add(video)
        dest = os.path.join(target, os.path.basename(video))
        inside = any((os.path.normcase(os.path.abspath(video)) + "").startswith(os.path.normcase(os.path.abspath(b)) + os.sep)
                     for b in VIDEO_FOLDERS)
        action = VIDEO_ACTION if inside else "copy"     # a video found elsewhere (the capture data's own) is never moved
        try:
            (shutil.move if action == "move" else shutil.copy2)(video, dest)
        except OSError as err:
            warn(f"  {name}: couldn't {action} {video}: {err}")
            rows.append({"take": name, "video": None, "why": f"couldn't {action} {video}: {err}"})
            continue
        out(f"  {name:<40} {os.path.basename(video)}   ({how}; {'moved' if action == 'move' else 'copied'})")
        rows.append({"take": name, "video": f"{name}/{os.path.basename(video)}", "from": video, "how": how})
    return rows


def videos_only(folder, source, completed):
    """VIDEOS_ONLY: every take folder in the export folder gets its video; nothing is exported."""
    out("Source videos only (VIDEOS_ONLY = True): nothing is exported")
    out(f"Export folder: {folder}")
    if not os.path.isdir(folder):
        return False, f"Export folder not found: {folder}"
    registry = unreal.AssetRegistryHelpers.get_asset_registry()
    takes = []
    for name in sorted(os.listdir(folder)):
        if not os.path.isdir(os.path.join(folder, name)):
            continue
        content = next((f"{parent}/{name}" for parent in (source, completed) if parent
                        and attempt(lambda: registry.get_assets_by_path(f"{parent}/{name}", True), [])), None)
        takes.append((name, content))
    rows = attach_videos(folder, takes)
    missing = [r["take"] for r in rows if not r.get("video")]
    path = os.path.join(folder, MANIFEST_NAME)
    try:
        manifest = json.load(open(path, encoding="utf-8")) if os.path.isfile(path) else {"format": 1}
        manifest["videos"] = rows
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(manifest, handle, indent=1)
    except (OSError, ValueError):
        pass
    message = (f"Videos: {len(rows) - len(missing)} of {len(rows)} take folder(s) have their video."
               + (f" None found for: {', '.join(missing[:6])} (see the Output Log; VIDEO_MAP settles it)." if missing else ""))
    out(message)
    return not missing, message + f"\nFolder: {folder}"


# ------------------------------------------------------------------ Sequencer, and moving finished takes
def open_sequence_path():
    """Content path of the Level Sequence open in Sequencer, or None."""
    current = attempt(SEQ_LIB.get_current_level_sequence) if SEQ_LIB is not None else None
    return current.get_path_name().split(".", 1)[0] if current is not None else None


def restore_sequencer(path, moved):
    """Open the Level Sequence that was open before the run again (at its new place when its take was moved)."""
    if SEQ_LIB is None:
        return
    now = open_sequence_path()
    if path is None:
        if now is not None and now.rsplit("/", 1)[1].startswith(FACE_SEQUENCE_PREFIX):
            attempt(SEQ_LIB.close_level_sequence)
        return
    for old, new in moved.items():
        if path.startswith(old + "/"):
            path = new + path[len(old):]
            break
    if now != path:
        asset = attempt(lambda: unreal.load_asset(path))
        if asset is not None:
            attempt(lambda: SEQ_LIB.open_level_sequence(asset))


PLACEHOLDER = "UE_Placeholder"     # what a cancelled or failed folder move leaves in the folder it was going to


def assets_in(content_folder):
    """(assets, redirectors) counted under a Content Browser folder, sub-folders included (placeholders left by
    a failed move aren't counted)."""
    registry = unreal.AssetRegistryHelpers.get_asset_registry()
    rows = [data for data in attempt(lambda: registry.get_assets_by_path(content_folder, True), []) or []
            if not str(data.asset_name).startswith(PLACEHOLDER)]
    redirectors = sum(1 for data in rows if _class_name(data) == "ObjectRedirector")
    return len(rows) - redirectors, redirectors


def clear_placeholders(content_folder):
    """Delete the UE_Placeholder assets a failed move left under a folder, so the next move can go there.
    Returns how many were deleted."""
    registry = unreal.AssetRegistryHelpers.get_asset_registry()
    rows = [data for data in attempt(lambda: registry.get_assets_by_path(content_folder, True), []) or []
            if str(data.asset_name).startswith(PLACEHOLDER)]
    return sum(1 for data in rows
               if attempt(lambda: ASSETS.delete_asset(f"{data.package_name}"), False))


def keep_characters_out(take):
    """Move MetaHuman Character assets (MetaHuman Creator's; the head DNA is read from them) out of the take folder
    to CHARACTER_FOLDER, so they stay put when the take goes to Completed. Returns notes for the log."""
    if not CHARACTER_FOLDER or ASSETS is None:
        return []
    registry = unreal.AssetRegistryHelpers.get_asset_registry()
    notes = []
    for data in attempt(lambda: registry.get_assets_by_path(take, True), []) or []:
        if _class_name(data) != "MetaHumanCharacter":
            continue
        source = f"{data.package_path}/{data.asset_name}"
        target = f"{CHARACTER_FOLDER.rstrip('/')}/{data.asset_name}"
        ensure_folder(CHARACTER_FOLDER.rstrip("/"))
        if attempt(lambda: ASSETS.does_asset_exist(target), False):
            warn(f"  {source} is a MetaHuman Character, but {target} already exists: it goes with the take")
            continue
        if attempt(lambda: ASSETS.rename_asset(source, target), False):
            attempt(lambda: ASSETS.save_directory(CHARACTER_FOLDER.rstrip("/"), False, True))
            notes.append(f"MetaHuman Character {data.asset_name} moved to {CHARACTER_FOLDER} first")
        else:
            warn(f"  couldn't move the MetaHuman Character {source} out of the take; it goes with the take")
    return notes


def move_take(take, destination):
    """Move one take folder to `destination` and save it there. Returns (moved, note for the log)."""
    if ASSETS is None:
        return False, "this editor doesn't offer EditorAssetLibrary to Python"
    kept_out = keep_characters_out(take)
    if sum(assets_in(destination)):
        return False, f"{destination} already exists and has assets in it"
    if clear_placeholders(destination):
        out(f"  (deleted the placeholder an earlier failed move left in {destination})")
    try:
        moved = bool(ASSETS.rename_directory(take, destination))
    except Exception as err:
        moved = False
        out(f"  ({type(err).__name__}: {err})")
    arrived, _ = assets_in(destination)
    if not arrived:
        clear_placeholders(destination)
        return False, ("Unreal couldn't move it. If Unreal asked 'Continue with rename?', it needs Yes: "
                       "run export_animations.py again and choose Yes (otherwise the lines above this one in the "
                       "Output Log say why)")
    notes = [f"{arrived} assets"] + kept_out
    # A moved asset only exists in memory until it is saved, so it is saved straight away.
    if not attempt(lambda: ASSETS.save_directory(destination, False, True), False):
        notes.append("NOT SAVED")
        warn(f"{destination} was moved but couldn't be saved. Use File > Save All before closing Unreal.")
    left, redirectors = assets_in(take)
    if left == 0 and redirectors == 0:
        if attempt(lambda: ASSETS.does_directory_exist(take), False) \
                and not attempt(lambda: ASSETS.does_directory_have_assets(take, True), True):
            attempt(lambda: ASSETS.delete_directory(take))              # the emptied take folder
    elif left:
        notes.append(f"{left} asset(s) stayed behind in {take}")
        moved = False
    else:
        notes.append(f"{redirectors} redirector(s) left in {take}: something outside the take still points at it")
    if not moved:
        warn(f"{take} was only partly moved to {destination}.")
    return True, ", ".join(notes)


# ------------------------------------------------------------------ run
def export_animations():
    del _lines[:]
    started = time.perf_counter()
    folder, config_path = load_settings()
    out("Export animations for Blender")
    source, why = content_path(SOURCE_PATH)
    completed, why_completed = content_path(COMPLETED_PATH) if MOVE_COMPLETED else (None, None)
    if why or why_completed:
        message = ("Nothing exported. " + (f"SOURCE_PATH: {why}" if why else f"COMPLETED_PATH: {why_completed}"))
        warn(message)
        return False, message
    moving = bool(MOVE_COMPLETED) and completed != source
    if VIDEOS_ONLY:
        return videos_only(folder, source, completed if MOVE_COMPLETED else None)
    was_open = open_sequence_path()
    disk = (lambda setting, path: f"   ({setting})" if str(setting).replace("\\", "/").rstrip("/") != path else "")
    out(f"Looks in:      {source}{disk(SOURCE_PATH, source)}")
    if moving:
        out(f"Finished to:   {completed}{disk(COMPLETED_PATH, completed)}")
    for path in [source] + ([completed] if moving else []):
        if ensure_folder(path):
            out(f"(created {path})")
    nested = moving and is_inside(completed, source)        # a Completed folder inside the source isn't searched
    animations = [a for a in animations_under(source) if not (nested and is_inside(package_folder(a), completed))]
    how = f"all under {source}"
    if USE_SELECTION:
        selected = selected_animations()
        if selected:
            wanted = {a.get_path_name() for a in selected}
            outside = sum(1 for a in selected if not is_inside(package_folder(a), source))
            if outside:
                out(f"({outside} selected Animation Sequence(s) are outside {source} and were left out)")
            animations = [a for a in animations if a.get_path_name() in wanted]
            how = f"selected in the Content Browser, inside {source}"
    if NAME_FILTER:
        animations = [a for a in animations if NAME_FILTER.lower() in a.get_name().lower()]
    animations.sort(key=lambda a: a.get_path_name())

    out(f"Animations:    {len(animations)} ({how}" + (f", name contains '{NAME_FILTER}'" if NAME_FILTER else "") + ")")
    out(f"Export folder: {folder}" + (f"   (config: {config_path})" if config_path else ""))
    if not animations:
        message = f"Nothing to export: no Animation Sequences found in {source}."
        out(message)
        return False, message + "\n" + describe_nothing(source, completed if moving else None).strip()

    takes, problems, loose = [], {}, []                     # take folders in this run; why a take can't move yet
    for animation in animations:
        take = take_of(package_folder(animation), source)
        if take is None:
            loose.append(animation.get_name())
        elif take not in takes:
            takes.append(take)
            problems[take] = []

    def problem(animation, text):
        take = take_of(package_folder(animation), source)
        if take is not None:
            problems[take].append(text)

    manifest, failed, skipped, exported, done = [], [], 0, 0, set()
    slow_task = getattr(unreal, "ScopedSlowTask", None)
    task = slow_task(len(animations), "Exporting animations") if slow_task else None
    if task is not None:
        task.__enter__()
        try:
            task.make_dialog(True)
        except Exception:
            pass
    try:
        for animation in animations:
            name = animation.get_name()
            if task is not None:
                if task.should_cancel():
                    out("Cancelled.")
                    break
                task.enter_progress_frame(1, name)
            done.add(animation.get_path_name())
            kind = kind_of(animation)
            asset_folder = package_folder(animation)
            relative = asset_folder[len(source):].strip("/") if asset_folder.startswith(source) else ""
            target = os.path.join(folder, *relative.split("/")) if relative else folder
            os.makedirs(target, exist_ok=True)
            entry = {"name": name, "asset": f"{asset_folder}/{name}", "kind": kind,
                     "skeleton": skeleton_name(animation), "frame_rate": frame_rate(animation),
                     "length_seconds": length_seconds(animation)}
            notes = []

            if EXPORT_FBX:
                path = os.path.join(target, f"{name}_{kind}.fbx")
                if not OVERWRITE and os.path.isfile(path):
                    skipped += 1
                    entry["fbx"] = os.path.relpath(path, folder).replace("\\", "/")
                    notes.append("fbx kept")
                else:
                    try:
                        ok = export_fbx(animation, path)
                    except Exception as err:
                        ok = False
                        notes.append(f"fbx error: {type(err).__name__}: {err}")
                    if ok:
                        exported += 1
                        entry["fbx"] = os.path.relpath(path, folder).replace("\\", "/")
                        notes.append(f"fbx {os.path.getsize(path) / 1048576.0:.1f} MB")
                    else:
                        failed.append(f"{name}: FBX not written")
                        problem(animation, f"{name}: FBX not written")

            if EXPORT_CURVES:
                try:
                    curves, idle_curves = read_curves(animation)
                except Exception as err:
                    curves, idle_curves = {}, []
                    notes.append(f"curves error: {type(err).__name__}: {err}")
                    failed.append(f"{name}: curves not read")
                    problem(animation, f"{name}: curves not read")
                if curves:
                    path = os.path.join(target, f"{name}_{kind}_curves.json")
                    if not OVERWRITE and os.path.isfile(path):
                        skipped += 1
                        notes.append("curves kept")
                    else:
                        with open(path, "w", encoding="utf-8") as handle:
                            json.dump(curves_document(animation, kind, curves, idle_curves), handle, separators=(",", ":"))
                        exported += 1
                    face_curves = sum(1 for curve in curves if curve.lower().startswith(FACE_CURVE_PREFIX))
                    notes.append(f"{len(curves)} curves, {face_curves} of them face expressions" if face_curves
                                 else f"{len(curves)} curves, NO face expressions")
                    entry["curves"] = os.path.relpath(path, folder).replace("\\", "/")
                    entry["curve_count"] = len(curves)
                    entry["face_curve_count"] = face_curves
                elif kind == "face":
                    notes.append("no curves in this animation")

            manifest.append(entry)
            out(f"  {kind:<5} {name:<44} {', '.join(notes)}")
    finally:
        if task is not None:
            task.__exit__(None, None, None)
    for animation in animations:
        if animation.get_path_name() not in done:
            problem(animation, f"{animation.get_name()}: not exported (cancelled)")

    results = {"face_boards": [], "completed": [], "left": [], "videos": []}

    def write_manifest():
        with open(os.path.join(folder, MANIFEST_NAME), "w", encoding="utf-8") as handle:
            json.dump({"format": 1, "source": source, "exported": time.strftime("%Y-%m-%d %H:%M:%S"),
                       "engine": unreal.SystemLibrary.get_engine_version(), "animations": manifest,
                       "face_boards": results["face_boards"], "videos": results["videos"],
                       "completed": results["completed"],
                       "left_in_source": results["left"]}, handle, indent=1)

    def write_report():
        try:
            with open(os.path.join(folder, REPORT_NAME), "w", encoding="utf-8") as handle:
                handle.write("\n".join(_lines) + "\n")
        except OSError:
            pass

    bodies = sum(1 for e in manifest if e["kind"] == "body")
    faces = len(manifest) - bodies
    message = (f"{len(manifest)} animations" + (f" ({faces} on a face skeleton)" if faces else "")
               + f": {exported} files written"
               + (f", {skipped} kept" if skipped else "") + (f", {len(failed)} FAILED" if failed else "")
               + f", in {time.perf_counter() - started:.1f} s.")
    for line in failed:
        unreal.log_error(line)
        _lines.append("FAILED: " + line)
    out(message)
    if EXPORT_CURVES:
        with_face = [e["name"] for e in manifest if e.get("face_curve_count")]
        without = [e["name"] for e in manifest if not e.get("face_curve_count")]
        out(f"Face expression curves (CTRL_expressions_*): in the curves file of {len(with_face)} of "
            f"{len(manifest)} animations.")
        if without:
            out(f"  None in: {', '.join(without)}. An animation has no face when its Performance wasn't processed "
                "(the face lines below say which); process it in Unreal and export the animation from it again.")
    write_manifest()                # written before the face step, so the body export stands on its own
    write_report()

    face_ok, face_message, moved = True, "", {}
    try:
        if EXPORT_FACE_BOARD:
            out("")
            face_ok, face_message, face_boards = run_face_boards(sorted({package_folder(a) for a in animations}),
                                                                 source, folder)
            results["face_boards"] = face_boards or []
            for take in takes:
                if face_boards is None:             # the face step couldn't run at all
                    problems[take].append("the face wasn't exported")
                for entry in face_boards or []:
                    if is_inside(entry["folder"], take) and entry["status"] not in ("exported", "kept"):
                        problems[take].append(f"{entry['name']}: " + ("Performance not processed yet"
                                              if entry["status"] == "not processed" else "face not exported"))
            write_manifest()
            write_report()

        video_message = ""
        if VIDEO_ACTION and takes:
            results["videos"] = attach_videos(folder, [(take.rsplit("/", 1)[1], take) for take in takes])
            missing = [row["take"] for row in results["videos"] if not row.get("video")]
            video_message = (f"Videos: {len(takes) - len(missing)} of {len(takes)} take(s) have their video in their folder."
                             + (f" None found for: {', '.join(missing[:6])} (the Output Log says why; VIDEO_MAP settles it)."
                                if missing else ""))
            out(video_message)
            write_manifest()
            write_report()

        move_message = ""
        if moving:
            out("")
            move_message = move_completed(source, completed, takes, problems, done, loose, results, moved, failed)
            write_manifest()
            write_report()
    finally:
        restore_sequencer(was_open, moved)
    return ((not failed) and face_ok,
            "\n".join(part for part in (message, face_message, video_message, move_message) if part)
            + f"\nFolder: {folder}")


def move_completed(source, completed, takes, problems, done, loose, results, moved, failed):
    """Move every take that was exported completely to `completed`; say why the others stay. Returns a summary."""
    registry = unreal.AssetRegistryHelpers.get_asset_registry()
    for take in takes:                              # a take is only finished when all of its animations were in the run
        missed = [str(data.asset_name) for data in registry.get_assets_by_path(take, True)
                  if _class_name(data) == "AnimSequence"
                  and f"{data.package_path}/{data.asset_name}.{data.asset_name}" not in done]
        if missed:
            problems[take].append(f"{len(missed)} of its animations weren't part of this run ({', '.join(missed[:4])}"
                                  + (" ..." if len(missed) > 4 else "") + ")")

    out(f"Finished takes -> {completed}")
    ready = [take for take in takes if not problems[take]]
    if ready and SEQ_LIB is not None:
        attempt(SEQ_LIB.close_level_sequence)       # a Level Sequence can't be moved while Sequencer has it open
    for take in takes:
        name = take.rsplit("/", 1)[1]
        why = list(dict.fromkeys(problems[take]))
        if not why:
            ok, note = move_take(take, f"{completed}/{name}")
            if ok:
                moved[take] = f"{completed}/{name}"
                results["completed"].append({"take": name, "from": take, "to": f"{completed}/{name}"})
                out(f"  moved  {name:<40} {note}")
                continue
            why = [f"could not be moved: {note}"]
            failed.append(f"{name}: {why[0]}")
            unreal.log_error(f"{name}: {why[0]}")
        results["left"].append({"take": name, "why": why})
        out(f"  stays  {name:<40} {'; '.join(why)}")
    if loose:
        out(f"  (not in a take folder, so exported but not moved: {', '.join(loose[:6])}"
            + (" ..." if len(loose) > 6 else "") + f". Put them in a folder under {source}.)")
    others = [str(p).rstrip("/").rsplit("/", 1)[1] for p in attempt(lambda: registry.get_sub_paths(source, False), []) or []
              if str(p).rstrip("/") not in takes and not is_inside(str(p).rstrip("/"), completed)]
    if others:
        out(f"  Also in {source}, with no Animation Sequence to export: {', '.join(sorted(others)[:12])}"
            + (" ..." if len(others) > 12 else ""))
    stays = [row["take"] for row in results["left"]]
    summary = (f"Moved to {completed}: {len(moved)} take(s)."
               + (f" Left in {source}: {len(stays)} ({', '.join(stays[:6])}{' ...' if len(stays) > 6 else ''})."
                  if stays else ""))
    out(summary)
    return summary


def run_face_boards(folders, source, folder):
    """
    Run export_face_board.py (next to this script) for the given content folders.
    Returns (ok, message, entries); entries is None when the face step couldn't run at all.
    """
    here = os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals() else ""
    path = os.path.join(here, FACE_BOARD_SCRIPT)
    if not here or not os.path.isfile(path):
        message = (f"Face: NOT exported - {FACE_BOARD_SCRIPT} isn't next to this script"
                   + (f" ({here})" if here else "") + ". Put it in the same folder and run again.")
        unreal.log_warning(message)
        _lines.append(message)
        return False, message, None
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("export_face_board", path)     # always the file on disk
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.export_face_boards(folders=folders, source=source, folder=folder, lines=_lines,
                                         restore_sequencer=False)
    except Exception as err:                    # the body export has been written; say what went wrong with the face
        message = f"Face: NOT exported - {FACE_BOARD_SCRIPT} stopped: {type(err).__name__}: {err}"
        unreal.log_error(message)
        _lines.append(message)
        return False, message, None


def _main():
    ok, message = export_animations()
    if SHOW_DIALOG and getattr(unreal, "EditorDialog", None) is not None:
        try:
            unreal.EditorDialog.show_message("Export animations", message, unreal.AppMsgType.OK)
        except Exception:
            pass
    return ok


if __name__ != "export_animations":
    _main()
