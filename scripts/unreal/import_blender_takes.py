"""
Bring takes finished in Blender back into Unreal, onto the MetaHuman.

In Blender, the Metahuman Rig tab's Export FBX writes, next to the take's .blend:
    <name>.fbx                the body skeleton with the cleaned-up animation
    <name> Face.fbx           the face's skeleton, its joints baked (used only when there is no face board file)
    <name> Face Board.json    the face board's controls on every frame
    <name> Export.json        what was exported, read by this script
This script finds every such take in the workspace and shows them in a window: search them by name, pick one or
more, tick what to bring in, and press Import.

  Import body       the body animation, as an Animation Sequence on the MetaHuman's body skeleton
  Import face       the face board, keyed onto the MetaHuman's face control board (the same controls MetaHuman
                    Animator keys, so RigLogic moves the face as it did in Blender). It needs a MetaHuman to key
                    onto: without "Add to MetaHuman", the face skeleton's FBX is imported instead, as an Animation
                    Sequence on the face skeleton
  Add to MetaHuman  a Level Sequence with the MetaHuman (picked from the ones in the open level) playing the body
                    animation, with the face keyed on its face board. Opened in Sequencer at the end

Everything goes into FINISHED_PATH/<name> (Content/Finished/<name>):
    AS_<name>_Body   (AS_<name>_Face when the face skeleton's FBX is imported)   LS_<name>
A take imported before is replaced. The MetaHuman's skeletons are used: with no MetaHuman picked, the skeletons named
BODY_SKELETON_NAME / FACE_SKELETON_NAME in the project.

Run in the Unreal Editor: Tools > Execute Python Script (or File > Execute Python Script). Put a MetaHuman in the level
first (drag its BP_<name> in) to have it added. The window is Python's own (tkinter), which Unreal's Python includes;
if this Unreal has no tkinter, a details panel of the same choices is shown instead.
A summary goes to the Output Log and to <workspace>/Logs/import_blender_takes_report.txt.
"""

import json
import os
import re
import sys
import time

import unreal

# ------------------------------------------------------------------ settings
TAKES_FOLDER = None         # where the takes are looked for (sub-folders included). None = paths.takes (the workspace)
FINISHED_PATH = None        # where they go in the project. None = unreal.finished_folder, else /Game/Finished
SEARCH_DEPTH = 4            # how many folders down from TAKES_FOLDER
SKIP_FOLDERS = ("Videos", "Unreal Exports", "MetaHuman Export", "Capture Media", "Scripts", "Storage", "Logs",
                "Pose Packs", ".git")   # never searched (far too many files, and no takes)
BODY_SKELETON = ""          # a skeleton asset path to use instead of the MetaHuman's; "" = the MetaHuman's (or by name)
FACE_SKELETON = ""
BODY_SKELETON_NAME = "metahuman_base_skel"        # used when no MetaHuman is picked
FACE_SKELETON_NAME = "Face_Archetype_Skeleton"
FACE_BOARD_RIG = ""         # the face board Control Rig's asset path; "" = the one named FACE_BOARD_RIG_NAME
FACE_BOARD_RIG_NAME = "Face_ControlBoard_CtrlRig"
OPEN_IN_SEQUENCER = True    # open the (last) Level Sequence made
SAVE_ASSETS = True
DEFAULT_BODY, DEFAULT_FACE, DEFAULT_ADD = True, True, True
REPORT_NAME = "import_blender_takes_report.txt"
MANIFEST_SUFFIX = " Export.json"

ASSETS = getattr(unreal, "EditorAssetLibrary", None)
LIB = getattr(unreal, "ControlRigSequencerLibrary", None)
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
def pipeline_settings():
    """(pipeline_config module, settings) from the workspace's pipeline_config.json, or (None, None) without one."""
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
    """(folder the takes are in, Content Browser folder they go to, logs folder or None)."""
    pc, cfg = pipeline_settings()
    takes, finished, logs = TAKES_FOLDER, FINISHED_PATH, None
    if cfg:
        takes = takes or attempt(lambda: pc.project_path(cfg, "takes"))
        finished = finished or pc.get(cfg, "unreal.finished_folder", "/Game/Finished")
        logs = attempt(lambda: pc.project_path(cfg, "logs"))
    return takes, (finished or "/Game/Finished").rstrip("/"), logs


# ------------------------------------------------------------------ the takes on disk
def safe_name(text):
    """A Content Browser name: letters, digits and underscores."""
    return re.sub(r"_+", "_", re.sub(r"[^A-Za-z0-9_]", "_", text)).strip("_") or "Take"


def unreal_name(entry):
    """The take's name in the project: the export's name without its ' MetaHuman' ending, made safe."""
    return safe_name(re.sub(r"[ _]MetaHuman$", "", entry["name"]))


def read_manifest(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    if data.get("kind") != "sgm_blender_export":
        return None
    folder = os.path.dirname(path)
    entry = {"name": data.get("name") or os.path.basename(path)[:-len(MANIFEST_SUFFIX)], "folder": folder,
             "saved": data.get("saved", ""), "fps": float(data.get("frame_rate") or 60.0),
             "frames": int(data.get("frame_count") or 0), "manifest": path}
    for key in ("body_fbx", "face_fbx", "face_board"):
        name = data.get(key)
        entry[key] = os.path.join(folder, name) if name and os.path.isfile(os.path.join(folder, name)) else None
    return entry


def find_takes(root):
    """Every take Export FBX wrote under root: from its '<name> Export.json' (or, for one exported before that file
    was written, from '<name> MetaHuman.fbx'). Newest first."""
    found, seen = [], set()
    if not root or not os.path.isdir(root):
        return found
    base = root.rstrip("\\/").count(os.sep)
    for folder, dirs, files in os.walk(root):
        depth = folder.rstrip("\\/").count(os.sep) - base
        dirs[:] = [] if depth >= SEARCH_DEPTH else sorted(d for d in dirs if d not in SKIP_FOLDERS)
        for f in sorted(files):
            if f.endswith(MANIFEST_SUFFIX):
                entry = read_manifest(os.path.join(folder, f))
                if entry and (entry["body_fbx"] or entry["face_board"] or entry["face_fbx"]):
                    found.append(entry)
                    seen.update(p for p in (entry["body_fbx"], entry["face_fbx"]) if p)
        for f in sorted(files):                       # older exports, without a manifest
            path = os.path.join(folder, f)
            if f.endswith(" MetaHuman.fbx") and path not in seen:
                stem = f[:-4]
                face = os.path.join(folder, f"{stem} Face.fbx")
                found.append({"name": stem, "folder": folder, "fps": 60.0, "frames": 0, "manifest": None,
                              "saved": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(os.path.getmtime(path))),
                              "body_fbx": path, "face_fbx": face if os.path.isfile(face) else None, "face_board": None})
    found.sort(key=lambda e: e["saved"], reverse=True)
    return found


def describe(entry):
    parts = [p for p, on in (("body", entry["body_fbx"]), ("face board", entry["face_board"]),
                             ("face skeleton", entry["face_fbx"] and not entry["face_board"])) if on]
    length = f", {entry['frames']} frames at {entry['fps']:g} fps" if entry["frames"] else ""
    return f"{entry['saved'][:16]}{length}: {', '.join(parts)}"


# ------------------------------------------------------------------ the MetaHumans in the level
def level_actors():
    sub = attempt(lambda: unreal.get_editor_subsystem(unreal.EditorActorSubsystem))
    actors = attempt(lambda: list(sub.get_all_level_actors()), None) if sub else None
    if actors is None:
        actors = attempt(lambda: list(unreal.EditorLevelLibrary.get_all_level_actors()), [])
    return actors


def component(actor, name):
    for comp in attempt(lambda: list(actor.get_components_by_class(unreal.SkeletalMeshComponent)), []) or []:
        if comp.get_name() == name:
            return comp
    return None


def metahumans():
    """[(label, actor)] for every actor in the level with a 'Body' and a 'Face' skeletal mesh (a MetaHuman)."""
    found = []
    for actor in level_actors():
        if component(actor, "Body") is not None and component(actor, "Face") is not None:
            found.append((attempt(actor.get_actor_label, actor.get_name()), actor))
    return sorted(found, key=lambda row: row[0].lower())


def mesh_of(comp):
    for getter in (lambda: comp.get_skeletal_mesh_asset(), lambda: comp.get_editor_property("skeletal_mesh_asset"),
                   lambda: comp.get_editor_property("skeletal_mesh")):
        mesh = attempt(getter)
        if mesh is not None:
            return mesh
    return None


def skeleton_of(comp):
    mesh = mesh_of(comp) if comp is not None else None
    return attempt(lambda: mesh.get_editor_property("skeleton")) if mesh is not None else None


# ------------------------------------------------------------------ assets
def class_name(data):
    try:
        return str(data.asset_class_path.asset_name)
    except Exception:
        return str(getattr(data, "asset_class", ""))


def asset_path(asset):
    return asset.get_path_name().split(".", 1)[0]


CLASS_PACKAGES = {"Skeleton": "/Script/Engine", "ControlRigBlueprint": "/Script/ControlRigDeveloper",
                  "RigVMBlueprint": "/Script/RigVMDeveloper"}


def find_asset(name, classes):
    """The asset of that name and class anywhere in the project or the plugins' content (the project first), or None."""
    registry = unreal.AssetRegistryHelpers.get_asset_registry()
    rows = []
    for cls in classes:
        package = CLASS_PACKAGES.get(cls)
        if package is None:
            continue
        flt = attempt(lambda: unreal.ARFilter(class_paths=[unreal.TopLevelAssetPath(package, cls)],
                                              recursive_classes=True))
        if flt is None:
            flt = attempt(lambda: unreal.ARFilter(class_names=[cls], recursive_classes=True))
        rows += attempt(lambda: list(registry.get_assets(flt)), []) or [] if flt is not None else []
    if not rows:                                      # no class filter in this version: the project's assets
        rows = [d for d in attempt(lambda: list(registry.get_assets_by_path("/Game", True)), []) or []
                if class_name(d) in classes]
    rows = [d for d in rows if str(d.asset_name) == name]
    rows.sort(key=lambda d: (0 if str(d.package_name).startswith("/Game/") else 1, str(d.package_name)))
    for data in rows:
        asset = attempt(data.get_asset)
        if asset is not None:
            return asset
    return None


def skeleton(path, name, comp):
    if path:
        asset = attempt(lambda: unreal.load_asset(path))
        if asset is not None:
            return asset, path
        warn(f"Skeleton {path} couldn't be loaded.")
    found = skeleton_of(comp)
    if found is not None:
        return found, f"{asset_path(found)} (the MetaHuman's)"
    found = find_asset(name, ("Skeleton",))
    return found, (asset_path(found) if found is not None else "")


def ensure_folder(path):
    if ASSETS is not None and not attempt(lambda: ASSETS.does_directory_exist(path), True):
        attempt(lambda: ASSETS.make_directory(path))


def remove_asset(path):
    if ASSETS is not None and attempt(lambda: ASSETS.does_asset_exist(path), False):
        if not attempt(lambda: ASSETS.delete_asset(path), False):
            warn(f"{path} couldn't be deleted (is it open in an editor?); it is replaced where it is.")


def anims_in(folder):
    registry = unreal.AssetRegistryHelpers.get_asset_registry()
    rows = attempt(lambda: list(registry.get_assets_by_path(folder, False)), []) or []
    return {f"{d.package_name}" for d in rows if class_name(d) == "AnimSequence"}


def fbx_options(skel):
    ui = unreal.FbxImportUI()
    for key, value in (("import_mesh", False), ("import_animations", True), ("import_as_skeletal", True),
                       ("import_materials", False), ("import_textures", False), ("skeleton", skel),
                       ("automated_import_should_detect_type", False)):
        attempt(lambda: ui.set_editor_property(key, value))
    attempt(lambda: ui.set_editor_property("mesh_type_to_import", unreal.FBXImportType.FBXIT_ANIMATION))
    data = attempt(lambda: ui.get_editor_property("anim_sequence_import_data"))
    if data is not None:
        for key, value in (("use_default_sample_rate", False), ("import_uniform_scale", 1.0),
                           ("import_bone_tracks", True), ("remove_redundant_keys", False),
                           ("import_custom_attribute", True), ("do_not_import_curve_with_zero", False)):
            attempt(lambda: data.set_editor_property(key, value))
        attempt(lambda: data.set_editor_property(
            "animation_length", unreal.FBXAnimationLengthImportType.FBXALIT_EXPORTED_TIME))
    return ui


def interchange_import(fbx, folder, skel):
    """The FBX through Interchange (Unreal's newer importer), animation only. True when it ran."""
    manager_class = getattr(unreal, "InterchangeManager", None)
    if manager_class is None:
        return False
    manager = manager_class.get_interchange_manager_scripted()
    source = manager_class.create_source_data(fbx)
    params = unreal.ImportAssetParameters()
    attempt(lambda: params.set_editor_property("is_automated", True))
    attempt(lambda: params.set_editor_property("replace_existing", True))
    pipeline = unreal.InterchangeGenericAssetsPipeline()
    common = pipeline.get_editor_property("common_skeletal_meshes_and_animations_properties")
    common.set_editor_property("import_only_animations", True)
    common.set_editor_property("skeleton", skel)
    attempt(lambda: pipeline.get_editor_property("animation_pipeline").set_editor_property("import_animations", True))
    attempt(lambda: pipeline.get_editor_property("material_pipeline").set_editor_property("import_materials", False))
    pipelines = params.get_editor_property("override_pipelines")
    try:
        pipelines.append(pipeline)
    except Exception:
        pipelines.append(unreal.SoftObjectPath(pipeline.get_path_name()))
    params.set_editor_property("override_pipelines", pipelines)
    manager.import_asset(folder, source, params)
    return True


def import_animation(fbx, folder, name, skel):
    """(Animation Sequence or None, note). The FBX's animation only, onto `skel`, as folder/name."""
    target = f"{folder}/{name}"
    remove_asset(target)
    before = anims_in(folder)
    task = unreal.AssetImportTask()
    for key, value in (("filename", fbx), ("destination_path", folder), ("destination_name", name),
                       ("replace_existing", True), ("automated", True), ("save", False)):
        task.set_editor_property(key, value)
    task.set_editor_property("options", fbx_options(skel))
    how = "FBX import"
    try:
        unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks([task])
    except Exception as err:
        warn(f"FBX import of {os.path.basename(fbx)}: {type(err).__name__}: {err}")
    made = sorted(anims_in(folder) - before)
    if not made and not attempt(lambda: ASSETS.does_asset_exist(target), False):
        try:                                          # this Unreal ignored the classic options: Interchange's own
            how = "Interchange import" if interchange_import(fbx, folder, skel) else how
        except Exception as err:
            warn(f"Interchange import of {os.path.basename(fbx)}: {type(err).__name__}: {err}")
        made = sorted(anims_in(folder) - before)
    anim = attempt(lambda: unreal.load_asset(target)) if attempt(lambda: ASSETS.does_asset_exist(target), False) else None
    if anim is None and made:
        anim = attempt(lambda: unreal.load_asset(made[0]))
        if anim is not None and asset_path(anim) != target:
            attempt(lambda: ASSETS.rename_asset(asset_path(anim), target))
            anim = attempt(lambda: unreal.load_asset(target)) or anim
    if anim is None or not isinstance(anim, unreal.AnimSequence):
        return None, f"{how} made no Animation Sequence (the Output Log above says why)"
    frames = attempt(lambda: int(anim.get_editor_property("number_of_sampled_frames")))
    return anim, f"{asset_path(anim)}" + (f", {frames} frames" if frames else "")


# ------------------------------------------------------------------ the Level Sequence
def editor_world():
    sub = attempt(lambda: unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem))
    return attempt(sub.get_editor_world) if sub else attempt(unreal.EditorLevelLibrary.get_editor_world)


def new_sequence(folder, name, fps, frames):
    path = f"{folder}/{name}"
    if SEQ_LIB is not None:
        current = attempt(SEQ_LIB.get_current_level_sequence)
        if current is not None and asset_path(current) == path:
            attempt(SEQ_LIB.close_level_sequence)
    remove_asset(path)
    sequence = attempt(lambda: unreal.load_asset(path)) if attempt(lambda: ASSETS.does_asset_exist(path), False) else None
    if sequence is None:
        sequence = unreal.AssetToolsHelpers.get_asset_tools().create_asset(name, folder, unreal.LevelSequence,
                                                                          unreal.LevelSequenceFactoryNew())
    rate = unreal.FrameRate(int(round(fps)), 1)
    sequence.set_display_rate(rate)
    sequence.set_playback_start(0)
    sequence.set_playback_end(max(1, frames))
    return sequence


def bind(sequence, actor_binding, comp):
    """The component's binding, under the actor's."""
    comp_binding = sequence.add_possessable(comp)
    parent = attempt(lambda: comp_binding.get_parent())
    if parent is None or attempt(lambda: parent.get_id() != actor_binding.get_id(), True):
        attempt(lambda: comp_binding.set_parent(actor_binding))
    return comp_binding


def add_animation(binding, anim, frames):
    track = binding.add_track(unreal.MovieSceneSkeletalAnimationTrack)
    section = track.add_section()
    section.set_range(0, max(1, frames))
    params = section.get_editor_property("params")
    params.set_editor_property("animation", anim)
    section.set_editor_property("params", params)
    return section


def face_board_class():
    """The face control board's Control Rig class, or (None, why)."""
    blueprint = attempt(lambda: unreal.load_asset(FACE_BOARD_RIG)) if FACE_BOARD_RIG else None
    if blueprint is None:
        blueprint = find_asset(FACE_BOARD_RIG_NAME, ("ControlRigBlueprint", "RigVMBlueprint", "ControlRigBlueprintGeneratedClass"))
    if blueprint is not None:
        cls = attempt(blueprint.generated_class) or attempt(lambda: blueprint.get_editor_property("generated_class"))
        if cls is not None:
            return cls, asset_path(blueprint)
    # the face board sequences export_face_board.py made hold the class too
    registry = unreal.AssetRegistryHelpers.get_asset_registry()
    for data in attempt(lambda: list(registry.get_assets_by_path("/Game", True)), []) or []:
        if class_name(data) == "LevelSequence" and str(data.asset_name).startswith("LS_FaceBoard_"):
            sequence = attempt(data.get_asset)
            for proxy in attempt(lambda: list(LIB.get_control_rigs(sequence)), []) or []:
                rig = getattr(proxy, "control_rig", None)
                cls = attempt(rig.get_class) if rig is not None else None
                if cls is not None and "face" in cls.get_name().lower():
                    return cls, f"{cls.get_name()} (from {data.asset_name})"
    return None, f"no Control Rig named {FACE_BOARD_RIG_NAME} in the project (set FACE_BOARD_RIG to its asset path)"


def channel_name(channel):
    return attempt(lambda: str(channel.channel_name)) or attempt(lambda: str(channel.get_editor_property("channel_name")), "")


def key_face_board(sequence, face_binding, rig_class, document, task=None):
    """Key the face board's controls from the Blender face board numbers. (channels keyed, note)."""
    world = editor_world()
    track = None
    for call in (lambda: LIB.find_or_create_control_rig_track(world, sequence, rig_class, face_binding, False),
                 lambda: LIB.find_or_create_control_rig_track(world, sequence, rig_class, face_binding)):
        track = attempt(call)
        if track is not None:
            break
    if track is None:
        return 0, "the face board's Control Rig track couldn't be made (find_or_create_control_rig_track)"
    sections = list(track.get_sections()) or [track.add_section()]
    section = sections[0]
    attempt(lambda: section.set_range(0, max(1, len(document["frames"]))))
    channels = {}
    for channel in list(section.get_all_channels()):
        channels[channel_name(channel).lower()] = channel
    curves, frames = document["curves"], document["frames"]
    keyed, missing = 0, []
    for name in sorted(curves):
        control, axis = name.rsplit(".", 1) if "." in name else (name, "Y")
        channel = channels.get(name.lower())
        slider = channels.get(control.lower())             # a slider control: one channel, keyed from Y
        if channel is None and axis.upper() == "Y":
            channel = slider
        if channel is None:
            known = slider is not None or any(f"{control}.{a}".lower() in channels for a in ("x", "y"))
            if not known and control not in missing:
                missing.append(control)
            continue
        values = curves[name]
        for key in list(attempt(channel.get_keys, []) or []):
            attempt(lambda: channel.remove_key(key))
        for frame, value in zip(frames, values):
            channel.add_key(unreal.FrameNumber(int(frame)), float(value))
        keyed += 1
        if task is not None and task.should_cancel():
            break
    note = f"{keyed} face board channels keyed"
    if missing:
        note += f"; {len(missing)} not on this face board, e.g. {', '.join(missing[:5])}"
    return keyed, note


# ------------------------------------------------------------------ the window
def choose_tk(takes, humans):
    """The take picker as a tkinter window. Returns the choices, None when cancelled; raises ImportError without tkinter."""
    import tkinter as tk
    from tkinter import ttk

    root = tk.Tk()
    root.title("Import Blender takes")
    root.geometry("900x560")
    root.minsize(520, 380)
    result = {}
    picked = set()                                   # indexes into takes
    shown = []                                       # indexes shown in the list, in order

    frame = ttk.Frame(root, padding=10)
    frame.pack(fill="both", expand=True)
    ttk.Label(frame, text=f"Takes finished in Blender ({len(takes)}, newest first) - pick one or more "
                          "(Ctrl-click or Shift-click for several):").pack(anchor="w")
    search = tk.StringVar()
    row = ttk.Frame(frame)
    row.pack(fill="x", pady=(6, 4))
    ttk.Label(row, text="Search").pack(side="left")
    entry = ttk.Entry(row, textvariable=search)
    entry.pack(side="left", fill="x", expand=True, padx=6)

    holder = ttk.Frame(frame)
    holder.pack(fill="both", expand=True)
    listbox = tk.Listbox(holder, selectmode="extended", activestyle="none", exportselection=False)
    bar = ttk.Scrollbar(holder, orient="vertical", command=listbox.yview)
    listbox.configure(yscrollcommand=bar.set)
    listbox.pack(side="left", fill="both", expand=True)
    bar.pack(side="right", fill="y")
    count = ttk.Label(frame, text="")
    count.pack(anchor="w", pady=(4, 0))

    def refresh(*_):
        words = search.get().lower().split()
        listbox.delete(0, "end")
        shown[:] = [i for i, t in enumerate(takes)
                    if all(w in (t["name"] + " " + t["folder"]).lower() for w in words)]
        for i in shown:
            t = takes[i]
            listbox.insert("end", ("[imported]  " if t.get("imported") else "") + f"{t['name']}    ({describe(t)})")
            if i in picked:
                listbox.selection_set("end")
        count.configure(text=f"{len(picked)} picked, {len(shown)} of {len(takes)} shown")

    def selected(*_):
        visible = set(shown)
        chosen = {shown[i] for i in listbox.curselection()}
        picked.difference_update(visible - chosen)
        picked.update(chosen)
        count.configure(text=f"{len(picked)} picked, {len(shown)} of {len(takes)} shown")

    def pick_all(on):
        for i in shown:
            (picked.add if on else picked.discard)(i)
        refresh()

    search.trace_add("write", refresh)
    listbox.bind("<<ListboxSelect>>", selected)
    buttons = ttk.Frame(frame)
    buttons.pack(fill="x", pady=(2, 8))
    ttk.Button(buttons, text="Pick all shown", command=lambda: pick_all(True)).pack(side="left")
    ttk.Button(buttons, text="Clear shown", command=lambda: pick_all(False)).pack(side="left", padx=6)

    body = tk.BooleanVar(value=DEFAULT_BODY)
    face = tk.BooleanVar(value=DEFAULT_FACE)
    add = tk.BooleanVar(value=DEFAULT_ADD and bool(humans))
    options = ttk.Frame(frame)
    options.pack(fill="x")
    ttk.Checkbutton(options, text="Import body", variable=body).pack(side="left")
    ttk.Checkbutton(options, text="Import face", variable=face).pack(side="left", padx=12)
    add_box = ttk.Checkbutton(options, text="Add to MetaHuman:", variable=add)
    add_box.pack(side="left")
    names = [label for label, _ in humans] or ["(no MetaHuman in the level)"]
    who = tk.StringVar(value=names[0])
    combo = ttk.Combobox(options, textvariable=who, values=names, state="readonly", width=32)
    combo.pack(side="left", padx=6)
    hint = ttk.Label(frame, text="", foreground="#a05a00")
    hint.pack(anchor="w", pady=(6, 0))

    def update_options(*_):
        if not humans:
            add.set(False)
            add_box.state(["disabled"])
        combo.state(["!disabled" if add.get() and humans else "disabled"])
        if face.get() and not add.get():
            hint.configure(text="Without a MetaHuman the face board can't be keyed: the face skeleton's FBX is "
                                "imported instead.")
        elif not humans:
            hint.configure(text="No MetaHuman in the open level: drag its BP_<name> into the level to have takes "
                                "added to it.")
        else:
            hint.configure(text="")
    for var in (face, add):
        var.trace_add("write", update_options)
    update_options()

    def go(*_):
        if not picked:
            hint.configure(text="Pick at least one take.")
            return
        if not (body.get() or face.get()):
            hint.configure(text="Tick Import body, Import face or both.")
            return
        result.update({"takes": [takes[i] for i in sorted(picked)], "body": body.get(), "face": face.get(),
                       "actor": humans[names.index(who.get())][1] if add.get() and humans else None})
        root.destroy()

    end = ttk.Frame(frame)
    end.pack(fill="x", pady=(10, 0))
    ttk.Button(end, text="Cancel", command=root.destroy).pack(side="right")
    ttk.Button(end, text="Import", command=go).pack(side="right", padx=6)
    root.bind("<Return>", go)
    root.bind("<Escape>", lambda *_: root.destroy())
    refresh()
    if takes:
        listbox.selection_set(0)
        selected()
    root.attributes("-topmost", True)
    root.after(400, lambda: root.attributes("-topmost", False))
    entry.focus_force()
    root.mainloop()
    return result or None


def choose_details(takes, humans):
    """The same choices in an Unreal details panel, for an Unreal without tkinter. None when cancelled."""
    if getattr(unreal, "SGMBlenderTakeChoice1", None) is None:
        @unreal.ustruct()
        class SGMBlenderTakeChoice1(unreal.StructBase):
            import_it = unreal.uproperty(bool)
            take = unreal.uproperty(str)
            about = unreal.uproperty(str)

        @unreal.ustruct()
        class SGMMetaHumanChoice1(unreal.StructBase):
            use = unreal.uproperty(bool)
            metahuman = unreal.uproperty(str)

        @unreal.uclass()
        class SGMImportBlenderTakes1(unreal.Object):
            import_body = unreal.uproperty(bool)
            import_face = unreal.uproperty(bool)
            add_to_metahuman = unreal.uproperty(bool)
            metahumans = unreal.uproperty(unreal.Array(SGMMetaHumanChoice1))
            takes = unreal.uproperty(unreal.Array(SGMBlenderTakeChoice1))
    form = unreal.new_object(unreal.SGMImportBlenderTakes1)
    form.set_editor_property("import_body", DEFAULT_BODY)
    form.set_editor_property("import_face", DEFAULT_FACE)
    form.set_editor_property("add_to_metahuman", DEFAULT_ADD and bool(humans))
    form.set_editor_property("metahumans", [unreal.SGMMetaHumanChoice1(use=(i == 0), metahuman=label)
                                            for i, (label, _) in enumerate(humans)])
    form.set_editor_property("takes", [unreal.SGMBlenderTakeChoice1(import_it=(i == 0), take=t["name"], about=describe(t))
                                       for i, t in enumerate(takes)])
    options = attempt(unreal.EditorDialogLibraryObjectDetailsViewOptions)
    if options is not None:
        attempt(lambda: options.set_editor_property("show_object_name", False))
        attempt(lambda: options.set_editor_property("allow_search", True))
    shown = unreal.EditorDialog.show_object_details_view("Import Blender takes (tick Import It on each take)", form,
                                                         options) if options is not None else \
        unreal.EditorDialog.show_object_details_view("Import Blender takes", form)
    if not shown:
        return None
    chosen = [takes[i] for i, row in enumerate(form.get_editor_property("takes")) if row.import_it]
    human = next((humans[i][1] for i, row in enumerate(form.get_editor_property("metahumans")) if row.use), None)
    return {"takes": chosen, "body": form.get_editor_property("import_body"),
            "face": form.get_editor_property("import_face"),
            "actor": human if form.get_editor_property("add_to_metahuman") else None}


# ------------------------------------------------------------------ run
def import_take(entry, choice, finished, rig_class):
    """Import one take. Returns (ok, line for the log)."""
    name = unreal_name(entry)
    folder = f"{finished}/{name}"
    ensure_folder(folder)
    actor = choice["actor"]
    body_comp = component(actor, "Body") if actor is not None else None
    face_comp = component(actor, "Face") if actor is not None else None
    notes, ok = [], True
    frames = entry["frames"]
    body_anim = face_anim = None
    if choice["body"]:
        if entry["body_fbx"]:
            skel, where = skeleton(BODY_SKELETON, BODY_SKELETON_NAME, body_comp)
            if skel is None:
                ok = False
                notes.append(f"body NOT imported: no body skeleton ({BODY_SKELETON_NAME}) found - set BODY_SKELETON")
            else:
                body_anim, why = import_animation(entry["body_fbx"], folder, f"AS_{name}_Body", skel)
                ok = ok and body_anim is not None
                notes.append(f"body: {why}" + ("" if body_anim is None else f" on {where}"))
                if body_anim is not None and not frames:
                    frames = attempt(lambda: int(body_anim.get_editor_property("number_of_sampled_frames")), 0) or 0
        else:
            notes.append("body: this take has no body FBX")
    board = None
    if choice["face"]:
        if entry["face_board"] and actor is not None:
            try:
                with open(entry["face_board"], "r", encoding="utf-8") as fh:
                    board = json.load(fh)
            except (OSError, ValueError) as err:
                notes.append(f"face board file couldn't be read: {err}")
        if board is None and entry["face_fbx"]:
            skel, where = skeleton(FACE_SKELETON, FACE_SKELETON_NAME, face_comp)
            if skel is None:
                ok = False
                notes.append(f"face NOT imported: no face skeleton ({FACE_SKELETON_NAME}) found - set FACE_SKELETON")
            else:
                face_anim, why = import_animation(entry["face_fbx"], folder, f"AS_{name}_Face", skel)
                ok = ok and face_anim is not None
                notes.append(f"face skeleton: {why}" + ("" if face_anim is None else f" on {where}")
                             + (" (the face board needs a MetaHuman: tick Add to MetaHuman)" if entry["face_board"] else ""))
        elif board is None:
            notes.append("face: nothing to import (no face board file or face FBX)")
    if actor is not None and (body_anim is not None or board is not None or face_anim is not None):
        frames = frames or (len(board["frames"]) if board else 0) or 1
        sequence = new_sequence(folder, f"LS_{name}", entry["fps"], frames)
        if SEQ_LIB is not None:
            attempt(lambda: SEQ_LIB.open_level_sequence(sequence))
        actor_binding = sequence.add_possessable(actor)
        if body_anim is not None:
            add_animation(bind(sequence, actor_binding, body_comp), body_anim, frames)
        if board is not None or face_anim is not None:
            face_binding = bind(sequence, actor_binding, face_comp)
            if face_anim is not None:
                add_animation(face_binding, face_anim, frames)
            elif rig_class is None:
                ok = False
                notes.append("face board NOT keyed: " + face_board_class()[1])
            else:
                keyed, why = key_face_board(sequence, face_binding, rig_class, board)
                ok = ok and keyed > 0
                notes.append(why)
        attempt(lambda: SEQ_LIB.refresh_current_level_sequence())
        if SAVE_ASSETS:
            attempt(lambda: ASSETS.save_loaded_asset(sequence, False))
        notes.append(f"{asset_path(sequence)} with '{attempt(actor.get_actor_label, actor.get_name())}', "
                     f"{frames} frames at {entry['fps']:g} fps")
    if SAVE_ASSETS:
        for anim in (body_anim, face_anim):
            if anim is not None:
                attempt(lambda: ASSETS.save_loaded_asset(anim, False))
    return ok, f"  {entry['name']}: " + "; ".join(notes)


def import_blender_takes():
    del _lines[:]
    started = time.perf_counter()
    root, finished, logs = load_settings()
    out("Import Blender takes")
    out(f"Takes from:  {root or '(no workspace found: run create_workspace.py, or set TAKES_FOLDER)'}")
    out(f"Into:        {finished}")
    takes = find_takes(root)
    if not takes:
        return False, (f"No takes exported from Blender in {root}. In Blender, press Export FBX in the Metahuman Rig "
                       "tab (it writes '<name> Export.json' next to the take), then run this again.")
    for t in takes:
        t["imported"] = bool(attempt(lambda: ASSETS.does_directory_exist(f"{finished}/{unreal_name(t)}"), False))
    humans = metahumans()
    out(f"MetaHumans in the level: {', '.join(label for label, _ in humans) or 'none'}")
    try:
        choice = choose_tk(takes, humans)
    except ImportError:
        out("(this Unreal's Python has no tkinter: showing the choices in a details panel)")
        choice = choose_details(takes, humans)
    if not choice or not choice["takes"]:
        return None, "Nothing imported."
    out(f"Importing {len(choice['takes'])} take(s): body {'on' if choice['body'] else 'off'}, face "
        f"{'on' if choice['face'] else 'off'}, MetaHuman: "
        f"{attempt(choice['actor'].get_actor_label, '?') if choice['actor'] is not None else 'none'}")
    rig_class, rig_note = (None, "")
    if choice["face"] and choice["actor"] is not None and any(t["face_board"] for t in choice["takes"]):
        if LIB is None:
            warn("ControlRigSequencerLibrary isn't available (enable the Control Rig and Sequencer Scripting "
                 "plugins): the face board can't be keyed.")
        else:
            rig_class, rig_note = face_board_class()
            out(f"Face board:  {rig_note}")
    results = []
    last_sequence = None
    with unreal.ScopedSlowTask(len(choice["takes"]), "Importing Blender takes") as task:
        task.make_dialog(True)
        for entry in choice["takes"]:
            if task.should_cancel():
                out("Cancelled.")
                break
            task.enter_progress_frame(1, entry["name"])
            try:
                ok, line = import_take(entry, choice, finished, rig_class)
            except Exception as err:
                ok, line = False, f"  {entry['name']}: FAILED - {type(err).__name__}: {err}"
            results.append(ok)
            (out if ok else warn)(line)
            if choice["actor"] is not None:
                last_sequence = f"{finished}/{unreal_name(entry)}/LS_{unreal_name(entry)}"
    if OPEN_IN_SEQUENCER and last_sequence and SEQ_LIB is not None:
        sequence = attempt(lambda: unreal.load_asset(last_sequence))
        if sequence is not None:
            attempt(lambda: SEQ_LIB.open_level_sequence(sequence))
    done = sum(1 for r in results if r)
    message = (f"{done} of {len(results)} take(s) imported into {finished}"
               + (f", {len(results) - done} with problems (see the Output Log)" if done < len(results) else "")
               + f", in {time.perf_counter() - started:.1f} s.")
    out(message)
    if logs:
        try:
            os.makedirs(logs, exist_ok=True)
            with open(os.path.join(logs, REPORT_NAME), "w", encoding="utf-8") as fh:
                fh.write("\n".join(_lines) + "\n")
        except OSError:
            pass
    return done == len(results), message


def _main():
    ok, message = import_blender_takes()
    if ok is not None and getattr(unreal, "EditorDialog", None) is not None:
        attempt(lambda: unreal.EditorDialog.show_message("Import Blender takes", message, unreal.AppMsgType.OK))


if __name__ != "import_blender_takes":
    _main()
