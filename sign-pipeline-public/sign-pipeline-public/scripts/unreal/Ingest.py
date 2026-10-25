"""
Ingest: the labelled videos -> one take folder each in Unreal, ready for Process.py.

For every video in the video folder (paths.video_source in the workspace's pipeline_config.json,
Videos/Labelled) that hasn't been ingested yet, it:
  1. ingests it with Capture Manager as a mono video take (Capture Data, CD_<take>)
  2. puts what Capture Manager makes into its own take folder, /Game/WIP/<take> (Capture Manager's import
     folder is pointed there for the take; when that can't be set, the assets are moved there afterwards)
  3. makes the take's MetaHuman Performance there (MHP_<take>), with body tracking set (and an Identity, only when
     one is set for that take: unreal.identities / unreal.identity), ready for Process.py to solve

The take is named after the video: "Hello How Are You 2026-10-15.mp4" -> Hello_How_Are_You_tk1 (dates and
the words in video.slate_drop_words are left out; a second video with the same name becomes _tk2).
Which take each video became is kept in <workspace>/Logs/ingested_videos.json, so running this again only
ingests new videos; export_animations.py later finds each take's video by the same name.

The videos stay where they are (export_animations.py moves each one into its take's export folder).
Capture Manager's converted footage goes to paths.capture_media (Capture Media) when SET_MEDIA_FOLDER is on.

Run in the Unreal Editor: Tools > Execute Python Script (needs the Capture Manager Editor, MetaHuman and
Python Editor Script plugins). Then run Process.py.
"""

import json
import os
import re
import shutil
import sys
import time

import unreal

# ------------------------------------------------------------------ settings (None = from pipeline_config.json)
VIDEO_FOLDER = None          # where the labelled videos are. None = paths.video_source
WIP_FOLDER = None            # the take folders go in here. None = unreal.wip_folder (/Game/WIP)
COMPLETED_FOLDER = None      # finished takes (export_animations.py moves them here). None = unreal.completed_folder
IDENTITY = None              # a MetaHuman Identity for every take. None = unreal.identity ("" = none: each mono video
                             #   take is solved from the video alone - right when the clips are of different people)
IDENTITIES = None            # {text in the take's name: Identity} for takes of known performers, e.g.
                             #   {"SGM": "/Game/Identities/MHI_SGM"}. None = unreal.identities
BODY_TRACKING = None         # solve the body too. None = unreal.body_tracking (True)
DROP_WORDS = None            # words left out of take names, e.g. ["SGM"]. None = video.slate_drop_words
SET_MEDIA_FOLDER = True      # point Capture Manager's media folder at paths.capture_media before ingesting
ON_EXISTING = "reuse"        # a video already ingested: "reuse" = leave it (make its Performance if it's missing);
                             #   "redo" = delete its take folder and converted footage and ingest it again
ONLY = ""                    # only videos whose name contains this text (case-insensitive); "" = all
VIDEO_EXTS = (".mp4", ".mov")
SHOW_DIALOG = True
LOG_NAME = "ingested_videos.json"

ASSETS = unreal.EditorAssetLibrary
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


def settings():
    pc, cfg = pipeline_settings()
    get = (lambda key, default: pc.get(cfg, key, default)) if cfg else (lambda key, default: default)
    s = {
        "videos": VIDEO_FOLDER or (pc.project_path(cfg, "video_source") if cfg else ""),
        "wip": (WIP_FOLDER or get("unreal.wip_folder", "/Game/WIP")).rstrip("/"),
        "completed": (COMPLETED_FOLDER or get("unreal.completed_folder", "/Game/Completed")).rstrip("/"),
        "identity": IDENTITY if IDENTITY is not None else get("unreal.identity", ""),
        "identities": IDENTITIES if IDENTITIES is not None else get("unreal.identities", {}),
        "body": BODY_TRACKING if BODY_TRACKING is not None else bool(get("unreal.body_tracking", True)),
        "drop": list(DROP_WORDS if DROP_WORDS is not None else get("video.slate_drop_words", [])),
        "media": pc.project_path(cfg, "capture_media") if cfg and SET_MEDIA_FOLDER else "",
        "log": os.path.join(pc.project_path(cfg, "logs"), LOG_NAME) if cfg
               else os.path.join(_full(unreal.Paths.project_saved_dir()), LOG_NAME),
        "config": (cfg or {}).get("_config_path"),
    }
    return s


# ------------------------------------------------------------------ names
_DATE = re.compile(r"\d{4}[-_. ]\d{1,2}[-_. ]\d{1,2}")


def slate_of(video, drop):
    """'Hello, How are you 2026-10-15.mp4' -> 'Hello_How_are_you' (dates and the drop words left out)."""
    stem = _DATE.sub(" ", os.path.splitext(os.path.basename(video))[0])
    words = [w for w in re.split(r"[^A-Za-z0-9]+", stem) if w]
    dropped = {d.lower() for d in drop}
    kept = [w for w in words if w.lower() not in dropped] or words or ["Take"]
    return "_".join(kept)


def take_name(slate, number):
    return f"{slate}_tk{number}"


def load_log(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_log(path, data):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, sort_keys=True)
    except OSError as err:
        warn(f"Couldn't write {path}: {err}")


def folder_has_assets(path):
    return bool(attempt(lambda: ASSETS.does_directory_exist(path)) and attempt(lambda: ASSETS.list_assets(path, True, False)))


def next_take(slate, used, wip, completed):
    """The first take number for this slate that no video and no take folder has yet."""
    n = 1
    while (take_name(slate, n) in used or folder_has_assets(f"{wip}/{take_name(slate, n)}")
           or folder_has_assets(f"{completed}/{take_name(slate, n)}")):
        n += 1
    return n


# ------------------------------------------------------------------ Capture Manager
def _media_setting():
    """(settings object, its media folder as written - a path with {tokens} for the take - or "")."""
    cls = getattr(unreal, "CaptureManagerEditorSettings", None)
    obj = attempt(lambda: unreal.get_default_object(cls)) if cls else None
    path = attempt(lambda: str(obj.get_editor_property("media_directory").path), "") if obj else ""
    return obj, path.replace("\\", "/")


def _token_tail(path):
    """The part of a media folder setting from its first {token} on: '.../{Device}/{Slate}_{Take}' -> that tail."""
    parts = path.split("/")
    for i, part in enumerate(parts):
        if "{" in part:
            return "/".join(parts[i:])
    return ""


def _token_base(path):
    tail = _token_tail(path)
    return path[:len(path) - len(tail)].rstrip("/") if tail else path


def set_media_folder(folder):
    """None, or why ingesting can't go on. Capture Manager's converted footage goes under `folder`, for this editor session (its own setting in
    Editor Preferences is left as it is). Its media folder setting ends in {tokens} that give each take a folder of
    its own (e.g. {Device}/{Slate}_{Take}); only the part before them is changed."""
    if not folder:
        return
    obj, current = _media_setting()
    if obj is None:
        warn("CaptureManagerEditorSettings isn't available; the converted footage goes where Capture Manager's "
             "own setting says (Editor Preferences > Plugins > Capture Manager)")
        return
    tail = _token_tail(current)
    if not current:
        warn("Couldn't read Capture Manager's media folder setting; the converted footage goes where it says")
        return None
    if not tail:
        return (f"Capture Manager's media folder is '{current}', with no {{tokens}} in it to give each take a folder "
                "of its own, so every take after the first would fail ('Output directory already exists'). Reset it: "
                "Editor Preferences > Plugins > Capture Manager > Media Directory, the reset arrow (or restart Unreal "
                "if an earlier Ingest.py set it), then run Ingest.py again.")
    wanted = folder.replace("\\", "/").rstrip("/") + "/" + tail
    if current == wanted:
        out(f"Footage to:    {wanted}")
        return
    os.makedirs(folder, exist_ok=True)
    try:
        obj.set_editor_property("media_directory", unreal.DirectoryPath(wanted))
        out(f"Footage to:    {wanted}   (Capture Manager's media folder, for this session; was {current})")
    except Exception as err:
        warn(f"Couldn't set Capture Manager's media folder ({err}); it uses its own: {current}")


def check_media_folder():
    """None, or why ingesting can't go on (Capture Manager's own media folder setting, when it isn't changed here)."""
    obj, current = _media_setting()
    if obj is not None and current and not _token_tail(current) and os.path.isdir(current):
        return (f"Capture Manager's media folder, '{current}', has no {{tokens}} to give each take a folder of its own "
                "and already exists, so the ingest would fail. Reset it in Editor Preferences > Plugins > Capture "
                "Manager > Media Directory.")
    return None


def media_folders_of(take):
    """Converted-footage folders Capture Manager made for this take (named <slate>_tk<n>), up to 4 folders down."""
    root = os.path.normpath(_token_base(_media_setting()[1]) or ".")
    found = []
    if _token_base(_media_setting()[1]) and os.path.isdir(root):
        for base, dirs, _ in os.walk(root):
            found += [os.path.join(base, d) for d in dirs if d.lower() == take.lower()]
            if os.path.relpath(base, root).count(os.sep) >= 3:
                dirs[:] = []
    return found


def set_import_folder(folder):
    """Capture Manager makes its assets in `folder` (Content Browser path) from now on, this session. Returns the
    setting as it was (to put back), or None when it can't be set (the assets are then moved there afterwards)."""
    obj = _media_setting()[0]
    if obj is None:
        return None
    try:
        before = obj.get_editor_property("import_directory")
        obj.set_editor_property("import_directory", unreal.DirectoryPath(folder))
        return before
    except Exception:
        return None


def restore_import_folder(before):
    obj = _media_setting()[0]
    if obj is not None and before is not None:
        attempt(lambda: obj.set_editor_property("import_directory", before))


def ingest(video, slate, number):
    """Capture Data for the video, or (None, why)."""
    cmi = getattr(unreal, "CaptureManagerIngestBlueprintLibrary", None)
    if cmi is None:
        return None, ("unreal.CaptureManagerIngestBlueprintLibrary isn't there: turn on the Capture Manager Editor "
                      "plugin (Edit > Plugins) and restart")
    stem = os.path.splitext(video)[0]
    audio = next((stem + e for e in (".wav", ".WAV") if os.path.isfile(stem + e)), "")
    result = cmi.ingest_mono_video_sync(video_file_path=video.replace("\\", "/"),
                                        audio_file_path=audio.replace("\\", "/"),
                                        slate=slate, take_number=number,
                                        params=unreal.CaptureManagerConversionParams())
    footage, err = result if isinstance(result, tuple) else (result, "")
    return (footage, None) if footage else (None, str(err) or "Capture Manager made no Capture Data")


def asset_folder(asset):
    return str(asset.get_path_name()).split(".")[0].rsplit("/", 1)[0]


def move_into(source_folder, target_folder):
    """Every asset in source_folder into target_folder (made when missing). Returns the problems."""
    problems = []
    if source_folder == target_folder:
        return problems
    if not ASSETS.does_directory_exist(target_folder) and ASSETS.rename_directory(source_folder, target_folder):
        return problems
    ASSETS.make_directory(target_folder)
    for path in ASSETS.list_assets(source_folder, True, False):
        path = str(path).split(".")[0]
        target = f"{target_folder}/{path.rsplit('/', 1)[1]}"
        if not ASSETS.rename_asset(path, target):
            problems.append(f"couldn't move {path} to {target_folder}")
    if not problems:
        attempt(lambda: ASSETS.delete_directory(source_folder))
    return problems


def identity_for(take, single, by_name):
    """(Identity asset or None, note) for a take: the first unreal.identities entry whose text is in the take's
    name, else unreal.identity, else none - a mono video take is solved without one (MetaHuman Animator only needs
    an Identity for depth footage), which is what clips of different people need."""
    for text, path in (by_name or {}).items():
        if text and text.lower() in take.lower():
            asset = attempt(lambda: unreal.load_asset(path))
            return (asset, f"{path} (unreal.identities: '{text}')") if asset else \
                   (None, f"{path} (unreal.identities: '{text}') isn't in this project - solved without an Identity")
    if single:
        asset = attempt(lambda: unreal.load_asset(single))
        return (asset, single) if asset else (None, f"{single} isn't in this project - solved without an Identity")
    return None, "none (solved from the video alone)"


def make_performance(take, folder, footage, identity, body):
    path = f"{folder}/MHP_{take}"
    if ASSETS.does_asset_exist(path):
        perf = unreal.load_asset(path)
        made = False
    else:
        perf = unreal.AssetToolsHelpers.get_asset_tools().create_asset(
            f"MHP_{take}", folder, unreal.MetaHumanPerformance, unreal.MetaHumanPerformanceFactoryNew())
        made = True
    if perf is None:
        return None, "couldn't make the Performance"
    perf.set_editor_property("footage_capture_data", footage)
    if identity is not None:
        perf.set_editor_property("identity", identity)
    attempt(lambda: perf.set_editor_property("body_tracking", bool(body)))
    ASSETS.save_loaded_asset(perf)
    return perf, "made" if made else "updated"


# ------------------------------------------------------------------ run
def run():
    del _lines[:]
    started = time.perf_counter()
    s = settings()
    out("Ingest the labelled videos")
    if s["config"]:
        out(f"Settings:      {s['config']}")
    if not s["videos"] or not os.path.isdir(s["videos"]):
        message = (f"No video folder: {s['videos'] or 'none set'}. Set paths.video_source in pipeline_config.json "
                   "(create_workspace.py), or VIDEO_FOLDER at the top of Ingest.py.")
        warn(message)
        return message
    videos = sorted(os.path.join(root, f) for root, _, files in os.walk(s["videos"]) for f in files
                    if f.lower().endswith(VIDEO_EXTS) and (not ONLY or ONLY.lower() in f.lower()))
    out(f"Videos:        {len(videos)} in {s['videos']}")
    out(f"Take folders:  {s['wip']}/<take>")
    out("Identity:      " + (f"{s['identity']} for every take" if s["identity"] else "none set (each take is solved "
                             "from its video alone)") + (f"; by name: {s['identities']}" if s["identities"] else ""))
    out(f"Body tracking: {'on' if s['body'] else 'off'}")
    stop = set_media_folder(s["media"]) if s["media"] else check_media_folder()
    if stop:
        warn(stop)
        return stop
    if not ASSETS.does_directory_exist(s["wip"]):
        ASSETS.make_directory(s["wip"])
        out(f"(created {s['wip']})")

    log = load_log(s["log"])
    used = {entry.get("take") for entry in log.values() if isinstance(entry, dict)}
    done, reused, failed = [], [], []
    for video in videos:
        name = os.path.basename(video)
        entry = log.get(name)
        if entry and ON_EXISTING == "reuse":
            take = entry.get("take")
            folder = f"{s['wip']}/{take}"
            if folder_has_assets(f"{s['completed']}/{take}"):
                continue                                         # exported already
            cd = attempt(lambda: next(str(p).split(".")[0] for p in ASSETS.list_assets(folder, True, False)
                                      if str(p).split(".")[0].rsplit("/", 1)[1].startswith("CD_")))
            footage = attempt(lambda: unreal.load_asset(cd)) if cd else None
            if footage is not None:
                identity = identity_for(take, s["identity"], s["identities"])[0]
                perf, how = make_performance(take, folder, footage, identity, s["body"])
                reused.append(take)
                out(f"[reuse]  {name} -> {folder} (Performance {how})")
                continue
            out(f"[again]  {name}: {take}'s Capture Data isn't in {folder}; ingesting it again")
        if entry and ON_EXISTING == "redo":
            take = entry.get("take")
            attempt(lambda: ASSETS.delete_directory(f"{s['wip']}/{take}"))
            for media in media_folders_of(take):
                shutil.rmtree(media, ignore_errors=True)
        slate = slate_of(video, s["drop"])
        number = int(re.search(r"_tk(\d+)$", entry["take"]).group(1)) if entry and entry.get("take", "").startswith(slate + "_tk") \
            else next_take(slate, used, s["wip"], s["completed"])
        take = take_name(slate, number)
        for media in media_folders_of(take):                   # left by an earlier, unfinished ingest
            shutil.rmtree(media, ignore_errors=True)
        before = set_import_folder(f"{s['wip']}/{take}")      # made straight in the take folder: nothing to move
        try:
            footage, why = ingest(video, slate, number)
        finally:
            restore_import_folder(before)
        if footage is None:
            failed.append(f"{name}: {why}")
            warn(f"[FAIL]   {name}: {why}")
            if "plugin" in why:
                break
            continue
        folder = f"{s['wip']}/{take}"
        problems = move_into(asset_folder(footage), folder)
        footage = unreal.load_asset(f"{folder}/{footage.get_name()}") or footage
        identity, identity_note = identity_for(take, s["identity"], s["identities"])
        perf, how = make_performance(take, folder, footage, identity, s["body"])
        ASSETS.save_directory(folder, False, True)
        log[name] = {"take": take, "video": video.replace("\\", "/"), "ingested": time.strftime("%Y-%m-%d %H:%M")}
        used.add(take)
        save_log(s["log"], log)
        done.append(take)
        out(f"[ingest] {name} -> {folder}" + (f" ({'; '.join(problems)})" if problems else "")
            + (f"   Identity: {identity_note}" if identity is not None or "isn't" in identity_note else ""))

    seconds = time.perf_counter() - started
    summary = (f"Ingested {len(done)} video(s)" + (f", {len(reused)} already in" if reused else "")
               + (f", {len(failed)} failed" if failed else "") + f" ({seconds:.0f} s).")
    if done or reused:
        summary += " Next: Process.py."
    out("")
    out(summary)
    for line in failed:
        out(f"  {line}")
    return summary


def _main():
    message = run()
    if SHOW_DIALOG and getattr(unreal, "EditorDialog", None) is not None:
        attempt(lambda: unreal.EditorDialog.show_message("Ingest", message, unreal.AppMsgType.OK))


if __name__ != "Ingest":
    _main()
