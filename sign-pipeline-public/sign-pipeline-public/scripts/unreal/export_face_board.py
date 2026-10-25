"""
Export the face of each take out of Unreal for Blender, in batches.

MetaHuman Animator keeps the solved face in the MetaHuman Performance asset. The Animation
Sequence made from it holds the face only as expression curves (CTRL_expressions_*), which
Blender's face board can't read. Blender's face board import reads the face board's own
controls (CTRL_C_jaw, CTRL_L_brow_down, ...), as Sequencer writes them with "Export Control
Rig FBX". This script produces that file for every Performance:

  1. it exports a Level Sequence from the Performance onto the MetaHuman, with the face keyed
     on the face control board                      -> asset  LS_FaceBoard_<performance>,
                                                       next to the Performance
  2. it writes that face board track to the take's export folder as
        <performance>_face.fbx          Control Rig FBX (what "Export Control Rig FBX" writes)
        <performance>_face_board.json   the same keys as numbers, one value per frame

Which Performances: every Performance under SOURCE_PATH (the project's Content/WIP folder, which
Unreal shows as /Game/WIP; sub-folders included) and nowhere else. With USE_SELECTION = True, only the ones selected in the Content Browser (still
inside SOURCE_PATH). SOURCE_PATH is created when it doesn't exist.
export_animations.py runs this for the takes it exports, so one run writes body and face; it is
that script which moves finished takes to the Completed folder, not this one.

What it changes in the project: it creates (and saves) one Level Sequence per Performance,
named LS_FaceBoard_<performance>. On a later run that sequence is deleted and made again, so
it always matches the Performance. Nothing else is touched. The Level Sequence that was open
in Sequencer is opened again at the end.

Run in the Unreal Editor: File > Execute Python Script.
A summary goes to the Output Log and to <export folder>/export_face_report.txt.
"""

import json
import os
import re
import sys
import time

import unreal

# ------------------------------------------------------------------ settings
SOURCE_PATH = None          # the only folder that is searched (sub-folders included); a Content Browser path
                            # ("/Game/WIP") or the folder on disk. None = the config's unreal.wip_folder, else /Game/WIP
EXPORT_FOLDER = None        # folder on disk. None = the config's paths.unreal_exports, else <project>/Saved/AnimationExports
USE_SELECTION = False       # True = only the Performances selected in the Content Browser (inside SOURCE_PATH)
NAME_FILTER = ""            # only Performances whose name contains this text (case-insensitive)
METAHUMAN_BLUEPRINT = None  # the MetaHuman the face is exported onto, e.g.
                            # "/Game/MetaHumans/NewMetaHumanCharacter/BP_NewMetaHumanCharacter".
                            # None = from the config ("unreal.metahuman_blueprint"), else the one in /Game/MetaHumans
EXPORT_FBX = True           # <performance>_face.fbx
EXPORT_JSON = True          # <performance>_face_board.json
OVERWRITE = True            # False = skip a Performance whose files already exist
REBUILD_SEQUENCES = True    # True = make LS_FaceBoard_<performance> again on every run; False = reuse it when it exists
SAVE_SEQUENCES = True       # save the Level Sequences this script creates
HEAD_MOVEMENT = False       # False = the head's own movement stays with the body animation
WHOLE_SEQUENCE = True       # True = the whole footage, like the Animation Sequence; False = only the processed range
SEQUENCE_PREFIX = "LS_FaceBoard_"
SHOW_DIALOG = True
REPORT_NAME = "export_face_report.txt"
FACE_CONTROL_PREFIX = "CTRL_"

LIB = getattr(unreal, "ControlRigSequencerLibrary", None)
SEQ_LIB = getattr(unreal, "LevelSequenceEditorBlueprintLibrary", None)
UTILS = getattr(unreal, "MetaHumanPerformanceExportUtils", None)
ASSETS = getattr(unreal, "EditorAssetLibrary", None)

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
    """(export folder on disk, MetaHuman blueprint name or path or None); also fills SOURCE_PATH when it's None."""
    global SOURCE_PATH
    pc, cfg = pipeline_settings()
    folder, blueprint = EXPORT_FOLDER, METAHUMAN_BLUEPRINT
    if cfg:
        folder = folder or pc.project_path(cfg, "unreal_exports")
        blueprint = blueprint or pc.get(cfg, "unreal.metahuman_blueprint")
    if SOURCE_PATH is None:
        SOURCE_PATH = pc.get(cfg, "unreal.wip_folder", "/Game/WIP") if cfg else "/Game/WIP"
    folder = folder or os.path.join(_full(unreal.Paths.project_saved_dir()), "AnimationExports")
    return os.path.normpath(folder), blueprint


def ensure_folder(path):
    """Create the Content Browser folder when it isn't there yet. Returns True when it was created."""
    if ASSETS is None or attempt(lambda: ASSETS.does_directory_exist(path), True):
        return False
    return bool(attempt(lambda: ASSETS.make_directory(path), False))


# ------------------------------------------------------------------ assets
def class_name(asset_data):
    try:
        return str(asset_data.asset_class_path.asset_name)
    except Exception:
        return str(getattr(asset_data, "asset_class", ""))


def assets_under(path, recursive=True):
    registry = unreal.AssetRegistryHelpers.get_asset_registry()
    return list(registry.get_assets_by_path(path, recursive))


def asset_path(asset):
    """/Game/Folder/Name of a loaded asset."""
    return asset.get_path_name().split(".", 1)[0]


def folder_of(asset):
    return asset_path(asset).rsplit("/", 1)[0]


def is_performance(asset):
    cls = getattr(unreal, "MetaHumanPerformance", None)
    if cls is not None:
        return isinstance(asset, cls)
    return "performance" in type(asset).__name__.lower()


def find_performances(source, folders=None):
    """(performances, how they were chosen). Only ever from inside `source`."""
    inside = (lambda content_folder: (content_folder + "/").startswith(source + "/"))

    def load(rows):
        found = []
        for data in rows:
            if class_name(data) == "MetaHumanPerformance" and inside(str(data.package_path)):
                asset = data.get_asset()
                if asset is not None:
                    found.append(asset)
        return found

    if folders is not None:
        found = []
        for folder in sorted(set(folders)):
            found += load(assets_under(folder, False))
        return found, "in the folders of the exported animations"
    if USE_SELECTION:
        library = getattr(unreal, "EditorUtilityLibrary", None)
        selected = [a for a in attempt(lambda: list(library.get_selected_assets()), []) if is_performance(a)] \
            if library is not None else []
        if selected:
            kept = [a for a in selected if inside(folder_of(a))]
            if len(kept) < len(selected):
                out(f"({len(selected) - len(kept)} selected Performance(s) are outside {source} and were left out)")
            return kept, f"selected in the Content Browser, inside {source}"
    return load(assets_under(source, True)), f"all under {source}"


def character_folder():
    """Where the MetaHumans are kept: the config's unreal.character_folder, else /Game/MetaHumans (Unreal's own)."""
    pc, cfg = pipeline_settings()
    return str(pc.get(cfg, "unreal.character_folder", "/Game/MetaHumans") if cfg else "/Game/MetaHumans").rstrip("/")


def find_blueprint(wanted):
    """(MetaHuman blueprint asset or None, note for the log)."""
    home = character_folder()
    if wanted and str(wanted).startswith("/"):
        asset = attempt(lambda: unreal.load_asset(str(wanted)))
        if asset is not None:
            return asset, asset_path(asset)
        warn(f"MetaHuman blueprint {wanted} couldn't be loaded; looking in {home} instead.")
        wanted = None
    rows = [d for d in assets_under("/Game", True) if class_name(d) == "Blueprint"]
    if wanted:
        named = [d for d in rows if str(d.asset_name).lower() == str(wanted).lower()]
        if named:
            asset = named[0].get_asset()
            if asset is not None:
                return asset, asset_path(asset)
        out(f"(no blueprint named {wanted} in this project; looking in {home} instead)")
    meshes = {str(d.package_path) for d in assets_under(home, True) if class_name(d) == "SkeletalMesh"}
    candidates = []
    for d in rows:
        folder = str(d.package_path)
        if not folder.startswith(home + "/") or folder.startswith(home + "/Common"):
            continue
        has_face = any(m == folder + "/Face" or m.startswith(folder + "/Face/") for m in meshes)
        candidates.append((0 if has_face else 1, 0 if str(d.asset_name).startswith("BP_") else 1,
                           folder.count("/"), f"{folder}/{d.asset_name}", d))
    candidates.sort(key=lambda row: row[:4])
    for row in candidates:
        asset = row[4].get_asset()
        if asset is not None:
            others = [r[3] for r in candidates if r is not row and r[0] == row[0]]
            note = asset_path(asset) + (f"   (others found: {', '.join(others[:4])}; set METAHUMAN_BLUEPRINT to "
                                        "choose)" if others else "")
            return asset, note
    return None, ""


# ------------------------------------------------------------------ the Performance
def performance_state(performance):
    """(ready to export?, text for the log)."""
    has_data = attempt(lambda: bool(performance.contains_animation_data()))
    frames = attempt(lambda: int(performance.get_number_of_processed_frames()))
    can_export = attempt(lambda: bool(performance.can_export_animation()))
    if has_data is False or frames == 0:
        return False, ("not processed: the Performance holds no solved frames. Open it in Unreal, press Process, "
                       "then run this again")
    if can_export is False:
        return False, "Unreal reports that this Performance can't be exported (can_export_animation is False)"
    return True, (f"{frames} solved frames" if frames else "")


def set_properties(target, values):
    """Set editor properties; returns the names this engine version doesn't have."""
    missing = []
    for name, value in values:
        try:
            target.set_editor_property(name, value)
        except Exception:
            missing.append(name)
    return missing


def export_sequence(performance, blueprint, package_path, asset_name):
    """The Level Sequence exported from the Performance onto the MetaHuman, or (None, why)."""
    settings = attempt(lambda: UTILS.get_export_level_sequence_settings(performance))
    if settings is None:
        settings = unreal.MetaHumanPerformanceExportLevelSequenceSettings()
    range_enum = getattr(unreal, "PerformanceExportRange", None)
    values = [("show_export_dialog", False), ("package_path", package_path), ("asset_name", asset_name),
              ("export_control_rig_track", True), ("export_identity", False),
              ("export_video_track", False), ("export_depth_track", False), ("export_depth_mesh", False),
              ("export_audio_track", False), ("export_image_plane", False), ("export_camera", False),
              ("export_transform_track", False), ("apply_lens_distortion", False),
              ("enable_control_rig_head_movement", bool(HEAD_MOVEMENT)),
              ("enable_meta_human_head_movement", bool(HEAD_MOVEMENT)),
              ("remove_redundant_keys", False)]
    if range_enum is not None:
        values.append(("export_range", range_enum.WHOLE_SEQUENCE if WHOLE_SEQUENCE else range_enum.PROCESSING_RANGE))
    missing = set_properties(settings, values)
    if "show_export_dialog" in missing:
        return None, "this engine's export settings have no 'show_export_dialog', so the export would stop for a dialog"
    try:
        settings.set_editor_property("target_meta_human_class", blueprint)
    except Exception as first:
        try:                                    # some versions take the blueprint's class instead of the asset
            settings.set_editor_property("target_meta_human_class", blueprint.generated_class())
        except Exception:
            return None, f"the MetaHuman blueprint wasn't accepted as the target: {type(first).__name__}: {first}"
    try:
        sequence = UTILS.export_level_sequence(performance, settings)
    except Exception as err:
        return None, f"export_level_sequence failed: {type(err).__name__}: {err}"
    if sequence is None:
        return None, "export_level_sequence returned nothing (see the lines above this one in the Output Log)"
    return sequence, (f"settings not in this engine: {', '.join(missing)}" if missing else "")


def prepare_sequence_slot(path):
    """
    Make sure no asset is in the way at `path`. Returns (existing sequence to reuse or None, path to export to).
    """
    if ASSETS is None or not attempt(lambda: ASSETS.does_asset_exist(path), False):
        return None, path
    if not REBUILD_SEQUENCES:
        existing = attempt(lambda: unreal.load_asset(path))
        if existing is not None:
            return existing, path
    attempt(lambda: SEQ_LIB.close_level_sequence())          # it can't be deleted while it is open
    if attempt(lambda: ASSETS.delete_asset(path), False):
        return None, path
    existing = attempt(lambda: unreal.load_asset(path))      # couldn't delete it (Unreal still holds it): reuse it
    if existing is not None:
        warn(f"{path} couldn't be deleted to make it again; using it as it is.")
        return existing, path
    number = 2                                               # couldn't load it either: export next to it
    while attempt(lambda: ASSETS.does_asset_exist(f"{path}_{number}"), False):
        number += 1
    warn(f"{path} couldn't be deleted; exporting to {path}_{number} instead.")
    return None, f"{path}_{number}"


# ------------------------------------------------------------------ the face board track
def channel_name(channel):
    try:
        return str(channel.channel_name)
    except Exception:
        try:
            return str(channel.get_editor_property("channel_name"))
        except Exception:
            return ""


def key_frame(key):
    stamp = key.get_time()
    return float(stamp.frame_number.value) + float(getattr(stamp, "sub_frame", 0.0) or 0.0)


def find_face_section(sequence):
    """
    (track, section, channels, notes, rig name) of the Control Rig section with the most face
    board channels, or (None, None, [], notes, ""). Notes describe every Control Rig in the sequence.
    """
    best, notes = None, []
    for proxy in list(LIB.get_control_rigs(sequence)):
        track = getattr(proxy, "track", None)
        rig = getattr(proxy, "control_rig", None)
        if track is None:
            continue
        rig_name = attempt(lambda: rig.get_class().get_name(), None) or attempt(lambda: rig.get_name(), "?")
        for section in list(track.get_sections()):
            channels = [(channel_name(c), c) for c in list(section.get_all_channels())]
            face = sum(1 for name, _ in channels if name.startswith(FACE_CONTROL_PREFIX))
            notes.append(f"{rig_name}: {len(channels)} channels, {face} on face board controls")
            if face and (best is None or face > best[0]):
                best = (face, track, section, channels, rig_name)
    if best is None:
        return None, None, [], notes, ""
    return best[1], best[2], best[3], notes, best[4]


def read_face_keys(channels):
    """
    {channel name: (frames, values)} for every face board channel that has keys, and
    {channel name: value} for the ones that only have a default value.
    """
    keyed, constant = {}, {}
    shared = None                               # frames of the first channel, reused when the keys line up
    for name, channel in channels:
        if not name.startswith(FACE_CONTROL_PREFIX):
            continue
        keys = list(attempt(channel.get_keys, []) or [])
        if not keys:
            if attempt(channel.has_default, False):
                value = attempt(lambda: float(channel.get_default()))
                if value is not None and abs(value) > 1e-9:
                    constant[name] = round(value, 6)
            continue
        try:
            values = [float(key.get_value()) for key in keys]
        except (TypeError, ValueError):
            continue                            # not a number channel
        if shared is not None and len(keys) == len(shared) \
                and abs(key_frame(keys[0]) - shared[0]) < 1e-6 and abs(key_frame(keys[-1]) - shared[-1]) < 1e-6:
            frames = shared
        else:
            frames = [key_frame(key) for key in keys]
            if shared is None:
                shared = frames
        keyed[name] = (frames, values)
    return keyed, constant


def frame_rate(sequence):
    try:
        rate = sequence.get_display_rate()
        return float(rate.numerator) / float(rate.denominator)
    except Exception:
        return 30.0


def face_document(performance, sequence, rig_name, keyed, constant):
    """The face board keys as a JSON-ready dict (same layout as the animation curves file)."""
    fps = frame_rate(sequence)
    start = attempt(lambda: int(sequence.get_playback_start()))
    end = attempt(lambda: int(sequence.get_playback_end()))
    first = min(frames[0] for frames, _ in keyed.values())
    last = max(frames[-1] for frames, _ in keyed.values())
    # most curves are keyed on the same frames: those share one "frames" list, the rest keep their own
    groups = []                                 # [frames, number of curves keyed on them]
    for frames, _ in keyed.values():
        for group in groups:
            if frames is group[0] or (len(frames) == len(group[0])
                                      and all(abs(a - b) < 1e-6 for a, b in zip(frames, group[0]))):
                group[1] += 1
                break
        else:
            groups.append([frames, 1])
    reference = max(groups, key=lambda group: (group[1], len(group[0])))[0]
    on_reference = (lambda frames: frames is reference or (
        len(frames) == len(reference) and all(abs(a - b) < 1e-6 for a, b in zip(frames, reference))))
    clean = (lambda f: int(f) if float(f).is_integer() else round(f, 4))
    return {
        "format": 1,
        "kind": "face_board",
        "performance": asset_path(performance),
        "sequence": asset_path(sequence),
        "control_rig": rig_name,
        "frame_rate": fps,
        "start_frame": start if start is not None else first,       # the sequence's first frame
        "end_frame": end,
        "first_key": clean(first),
        "last_key": clean(last),
        "length_seconds": round((last - first) / fps, 6) if fps else None,
        "curve_count": len(keyed),
        "constants": constant,
        "frames": [clean(f) for f in reference],
        "curves": {name: ([round(v, 6) for v in values] if on_reference(frames) else
                          {"frames": [clean(f) for f in frames], "values": [round(v, 6) for v in values]})
                   for name, (frames, values) in sorted(keyed.items())},
    }


def slider_mappings():
    """
    Control-to-FBX mappings that put slider (float) controls on translation Y, the layout Sequencer's
    "Export Control Rig FBX" dialog writes and face board importers read. Without mappings Unreal
    stores each slider as a custom property on its node, which those importers ignore.
    Returns a list of unreal.ControlToTransformMappings, or None when this engine names things differently.
    """
    struct = getattr(unreal, "ControlToTransformMappings", None)
    controls = getattr(unreal, "ControlRigChannelEnum", None)
    channels = getattr(unreal, "TransformChannelEnum", None)
    if struct is None or controls is None or channels is None:
        return None

    def member(enum, name):                     # FLOAT, VECTOR2DX or VECTOR2D_X, TRANSLATE_Y ...: spelling-tolerant
        want = name.lower()
        for key in dir(enum):
            if not key.startswith("_") and key.replace("_", "").lower() == want:
                return getattr(enum, key)
        return None

    rows = []
    for control, channel in (("Float", "TranslateY"), ("Vector2DX", "TranslateX"), ("Vector2DY", "TranslateY"),
                             ("PositionX", "TranslateX"), ("PositionY", "TranslateY"), ("PositionZ", "TranslateZ"),
                             ("RotatorX", "RotateX"), ("RotatorY", "RotateY"), ("RotatorZ", "RotateZ"),
                             ("ScaleX", "ScaleX"), ("ScaleY", "ScaleY"), ("ScaleZ", "ScaleZ")):
        source, target = member(controls, control), member(channels, channel)
        if source is None or target is None:
            if control == "Float":
                return None
            continue
        try:
            row = struct()
            row.set_editor_property("control_channel", source)
            row.set_editor_property("fbx_channel", target)
            row.set_editor_property("negate", False)
        except Exception:
            return None
        rows.append(row)
    return rows


def fbx_layout(path):
    """(controls animated as translation, slider controls stored as custom properties) in a Control Rig FBX."""
    try:
        with open(path, "rb") as handle:
            data = handle.read()
    except OSError:
        return 0, 0
    return (data.count(b"T\x00\x01AnimCurveNode"),
            len(re.findall(rb"CTRL_[A-Za-z0-9_]+\x00\x01AnimCurveNode", data)))


def export_fbx(sequence, section, path):
    """
    Control Rig FBX of the section, as Sequencer's "Export Control Rig FBX" writes it, with the slider
    controls on translation Y. Returns (ok, why not, note about the sliders).
    """
    settings_class = getattr(unreal, "MovieSceneUserExportFBXControlRigSettings", None)
    function = getattr(LIB, "export_fbx_from_control_rig_section", None)
    if settings_class is None or function is None:
        return False, "this engine doesn't offer export_fbx_from_control_rig_section to Python", ""
    if not attempt(lambda: SEQ_LIB.open_level_sequence(sequence), False):        # the export needs it open in Sequencer
        current = attempt(SEQ_LIB.get_current_level_sequence)
        if current is None or asset_path(current) != asset_path(sequence):
            return False, "the Level Sequence couldn't be opened in Sequencer", ""

    def with_mappings(settings):                # our own list
        rows = slider_mappings()
        if not rows:
            return False
        settings.set_editor_property("control_channel_mappings", rows)
        return True

    def with_preset(settings):                  # Unreal's own MetaHuman preset
        settings.load_control_mappings_from_preset(True)
        return True

    result, written, sliders_loose = None, False, 0
    for prepare in (with_mappings, with_preset, None):
        settings = settings_class()
        set_properties(settings, [("export_file_name", path), ("ascii", False),
                                  ("export_only_selected_controls", False)])
        if prepare is not None and not attempt(lambda: prepare(settings), False):
            continue
        if os.path.isfile(path):
            try:
                os.remove(path)
            except OSError as err:
                return False, f"the old file is in use: {err}", ""
        try:
            result = function(sequence, section, settings)
        except Exception as err:
            if prepare is None:
                return False, f"{type(err).__name__}: {err}", ""
            continue
        written = os.path.isfile(path) and os.path.getsize(path) > 0
        on_translation, sliders_loose = fbx_layout(path) if written else (0, 0)
        if written and sliders_loose == 0:
            return True, "", f"{on_translation} controls on translation"
    if written:
        return True, "", (f"SLIDERS NOT ON TRANSLATION: {sliders_loose} slider controls are stored as custom properties, "
                          "which face board importers don't read - use the _face_board.json, it has every control")
    return False, f"Unreal wrote no file (export_fbx_from_control_rig_section returned {result})", ""


# ------------------------------------------------------------------ run
def export_face_boards(folders=None, source=None, folder=None, lines=None, restore_sequencer=True):
    """
    Export the face board of every Performance. Called by export_animations.py with the content
    folders of the animations it exported (`folders`), its source folder (`source`) and export
    folder (`folder`); run on its own, it finds the Performances itself.
    Returns (ok, message, entries): one entry per Performance, with "folder" (its content folder)
    and "status" - "exported", "kept", "not processed" or "failed". entries is None when the face
    export couldn't run at all.
    """
    global _lines
    if lines is not None:
        _lines = lines                          # write into the caller's report
    else:
        del _lines[:]
    started = time.perf_counter()
    config_folder, blueprint_setting = load_settings()
    folder = os.path.normpath(folder) if folder else config_folder

    out("Export face boards for Blender")
    if UTILS is None or LIB is None or SEQ_LIB is None:
        missing = [name for name, value in (("MetaHumanPerformanceExportUtils", UTILS),
                                            ("ControlRigSequencerLibrary", LIB),
                                            ("LevelSequenceEditorBlueprintLibrary", SEQ_LIB)) if value is None]
        message = ("The face can't be exported: this editor doesn't offer " + ", ".join(missing) + " to Python. "
                   "Enable the MetaHuman Animator, Control Rig and Sequencer Scripting plugins.")
        warn(message)
        return False, message, None

    if source is None:
        source, why = content_path(SOURCE_PATH)
        if why:
            message = f"No face exported. SOURCE_PATH: {why}"
            warn(message)
            return False, message, None
        if str(SOURCE_PATH).replace("\\", "/").rstrip("/") != source:
            out(f"Looks in:      {source}   ({SOURCE_PATH})")
    source = source.rstrip("/")
    if ensure_folder(source):
        out(f"(created {source})")
    performances, how = find_performances(source, folders)
    if NAME_FILTER:
        performances = [p for p in performances if NAME_FILTER.lower() in p.get_name().lower()]
    performances.sort(key=lambda p: p.get_path_name())
    out(f"Performances:  {len(performances)} ({how}" + (f", name contains '{NAME_FILTER}'" if NAME_FILTER else "") + ")")
    out(f"Export folder: {folder}")
    if not performances:
        message = (f"Face: no MetaHuman Performance next to the exported animations." if folders is not None
                   else f"No MetaHuman Performances in {source}, so no face was exported.")
        out(message)
        return folders is not None, message, []

    blueprint, note = find_blueprint(blueprint_setting)
    if blueprint is None:
        message = ("No MetaHuman blueprint found to export the face onto. Set unreal.metahuman_blueprint in "
                   "pipeline_config.json (or METAHUMAN_BLUEPRINT at the top of export_face_board.py) to its Content "
                   f"Browser path, e.g. {character_folder()}/<name>/BP_<name>.")
        warn(message)
        return False, message, None
    out(f"MetaHuman:     {note}")

    was_open = attempt(SEQ_LIB.get_current_level_sequence)
    entries, failed, not_ready, written, exported_count = [], [], [], 0, 0
    slow_task = getattr(unreal, "ScopedSlowTask", None)
    task = slow_task(len(performances), "Exporting face boards") if slow_task else None
    if task is not None:
        task.__enter__()
        attempt(lambda: task.make_dialog(True))
    try:
        for performance in performances:
            name = performance.get_name()
            if task is not None:
                if task.should_cancel():
                    out("Cancelled.")
                    break
                task.enter_progress_frame(1, name)
            content_folder = folder_of(performance)
            relative = content_folder[len(source):].strip("/") if content_folder.startswith(source) else ""
            target = os.path.join(folder, *relative.split("/")) if relative else folder
            fbx_path = os.path.join(target, f"{name}_face.fbx")
            json_path = os.path.join(target, f"{name}_face_board.json")

            entry = {"name": name, "performance": asset_path(performance), "folder": content_folder,
                     "status": "failed"}
            entries.append(entry)
            ready, state = performance_state(performance)
            if not ready:
                not_ready.append(name)
                entry["status"] = "not processed"
                out(f"  face  {name:<40} SKIPPED - {state}")
                continue
            wanted = [p for p, on in ((fbx_path, EXPORT_FBX), (json_path, EXPORT_JSON)) if on]
            if not OVERWRITE and wanted and all(os.path.isfile(p) for p in wanted):
                entry["status"] = "kept"
                out(f"  face  {name:<40} kept (files already there)")
                continue

            sequence, sequence_path = prepare_sequence_slot(f"{content_folder}/{SEQUENCE_PREFIX}{name}")
            notes = []
            if sequence is None:
                sequence, why = export_sequence(performance, blueprint, content_folder,
                                                sequence_path.rsplit("/", 1)[1])
                if sequence is None:
                    failed.append(f"{name}: {why}")
                    out(f"  face  {name:<40} FAILED - {why}")
                    continue
                if why:
                    notes.append(why)
            else:
                notes.append("sequence reused")

            track, section, channels, rigs, rig_name = find_face_section(sequence)
            if section is None:
                why = ("the exported Level Sequence has no Control Rig track with face board controls. Control Rigs "
                       "in it: " + ("; ".join(rigs) if rigs else "none"))
                failed.append(f"{name}: {why}")
                out(f"  face  {name:<40} FAILED - {why}")
                continue
            keyed, constant = read_face_keys(channels)
            if not any(len(values) > 1 for _, values in keyed.values()):
                why = (f"the face board track ({rig_name}) has no animation on it (no channel has more than one "
                       "key). Control Rigs in the sequence: " + "; ".join(rigs))
                failed.append(f"{name}: {why}")
                out(f"  face  {name:<40} FAILED - {why}")
                continue

            os.makedirs(target, exist_ok=True)
            document = face_document(performance, sequence, rig_name, keyed, constant)
            moving = sum(1 for _, values in keyed.values() if max(values) - min(values) > 1e-4)
            frames = max(len(f) for f, _ in keyed.values())
            notes.insert(0, f"{len(keyed)} channels keyed on {frames} frames at {document['frame_rate']:g} fps, "
                            f"{moving} change")
            entry.update({"status": "exported", "sequence": asset_path(sequence),
                          "frame_rate": document["frame_rate"], "length_seconds": document["length_seconds"],
                          "channels": len(keyed), "moving_channels": moving})
            exported_count += 1
            if EXPORT_JSON:
                with open(json_path, "w", encoding="utf-8") as handle:
                    json.dump(document, handle, separators=(",", ":"))
                written += 1
                entry["face_board"] = os.path.relpath(json_path, folder).replace("\\", "/")
            if SAVE_SEQUENCES and ASSETS is not None:
                attempt(lambda: ASSETS.save_loaded_asset(sequence, False))
            if EXPORT_FBX:
                ok, why, layout = export_fbx(sequence, section, fbx_path)
                if ok:
                    written += 1
                    entry["fbx"] = os.path.relpath(fbx_path, folder).replace("\\", "/")
                    entry["fbx_sliders_on_translation"] = not layout.startswith("SLIDERS NOT")
                    notes.append(f"fbx {os.path.getsize(fbx_path) / 1048576.0:.1f} MB ({layout})")
                    if layout.startswith("SLIDERS NOT"):
                        warn(f"{name}: {layout}")
                else:
                    entry["status"] = "failed"
                    failed.append(f"{name}: face FBX not written - {why}")
                    notes.append(f"FBX NOT WRITTEN ({why})")
            if moving == 0:
                warn(f"{name}: the face board is keyed but nothing on it moves.")
            out(f"  face  {name:<40} {', '.join(notes)}")
            if exported_count == 1:             # once per run: what the exported sequence holds
                out(f"        Control Rigs in {sequence.get_name()}: {'; '.join(rigs)}")
    finally:
        if task is not None:
            task.__exit__(None, None, None)
        if EXPORT_FBX and restore_sequencer:        # put Sequencer back the way it was
            current = attempt(SEQ_LIB.get_current_level_sequence)
            if was_open is not None:
                if current is None or asset_path(current) != asset_path(was_open):
                    attempt(lambda: SEQ_LIB.open_level_sequence(was_open))
            elif current is not None and current.get_name().startswith(SEQUENCE_PREFIX):
                attempt(lambda: SEQ_LIB.close_level_sequence())

    done = sum(1 for e in entries if e["status"] == "exported")
    message = (f"Face: {done} of {len(performances)} Performances exported, {written} files written"
               + (f", {len(failed)} FAILED" if failed else "")
               + (f", {len(not_ready)} not processed ({', '.join(not_ready)})" if not_ready else "")
               + f", in {time.perf_counter() - started:.1f} s.")
    for line in failed:
        unreal.log_error(line)
        _lines.append("FAILED: " + line)
    out(message)
    if lines is None:
        try:
            os.makedirs(folder, exist_ok=True)
            with open(os.path.join(folder, REPORT_NAME), "w", encoding="utf-8") as handle:
                handle.write("\n".join(_lines) + "\n")
        except OSError:
            pass
    return not failed, message + (f"\nFolder: {folder}" if lines is None else ""), entries


def _main():
    ok, message, _ = export_face_boards()
    if SHOW_DIALOG and getattr(unreal, "EditorDialog", None) is not None:
        attempt(lambda: unreal.EditorDialog.show_message("Export face boards", message, unreal.AppMsgType.OK))
    return ok


if __name__ != "export_face_board":
    _main()
