"""
Process: solve every take's MetaHuman Performance and export its animation, one after the other.

It looks in the take folders under /Game/WIP (unreal.wip_folder in the workspace's pipeline_config.json),
which Ingest.py makes. For each MetaHuman Performance there (MHP_<take>) that isn't solved yet:
  1. solves it with MetaHuman Animator (face, and body when body tracking is on) - several minutes per take;
     MetaHuman Animator only solves one Performance at a time, so the next starts when one finishes
  2. saves it, and exports its Animation Sequence (AS_<take>) into the same take folder, on the
     performer skeleton (face curves and body bones)
Already-solved Performances only get the export, when their Animation Sequence is missing.

The editor stays usable while it runs; progress is in the Output Log ([solving] / [solved] / [exported]),
and a box says when the batch is finished. Then run export_metahuman.py (once) and export_animations.py.

Run in the Unreal Editor: Tools > Execute Python Script. Run it once per batch: while a batch is running,
running it again only reports that one is in progress.
"""

import os
import sys
import time

import unreal

# ------------------------------------------------------------------ settings (None = from pipeline_config.json)
WIP_FOLDER = None          # the take folders. None = unreal.wip_folder (/Game/WIP)
IDENTITY = None            # given to a Performance that has none. None = unreal.identity ("" = none: a mono video take
                           #   is solved from the video alone - right when the clips are of different people)
IDENTITIES = None          # {text in the take's name: Identity}, e.g. {"SGM": "/Game/Identities/MHI_SGM"}.
                           #   None = unreal.identities
BODY_TRACKING = None       # None = unreal.body_tracking (True)
REPROCESS = False          # True = solve again the Performances that are solved already
EXPORT = True              # export each solved Performance's Animation Sequence
HEAD_MOVEMENT = True       # the head's own movement goes into the animation
ONLY = ""                  # only takes whose name contains this text (case-insensitive); "" = all
SHOW_DIALOG = True
FORCE = False              # True = start even when an earlier batch looks unfinished (after an error stopped it)

ASSETS = unreal.EditorAssetLibrary
EXPORTER = getattr(unreal, "MetaHumanPerformanceExportUtils", None)

# kept on the unreal module, so a second run while a batch is going can see it
_STATE_KEY = "_sign_pipeline_process_state"


def out(text=""):
    unreal.log(str(text))


def warn(text):
    unreal.log_warning(str(text))


def attempt(func, default=None):
    try:
        return func()
    except Exception:
        return default


def _full(path):
    return os.path.normpath(unreal.Paths.convert_relative_path_to_full(path))


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
    return {
        "wip": (WIP_FOLDER or get("unreal.wip_folder", "/Game/WIP")).rstrip("/"),
        "identity": IDENTITY if IDENTITY is not None else get("unreal.identity", ""),
        "identities": IDENTITIES if IDENTITIES is not None else get("unreal.identities", {}),
        "body": BODY_TRACKING if BODY_TRACKING is not None else bool(get("unreal.body_tracking", True)),
        "config": (cfg or {}).get("_config_path"),
    }


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


def performances(wip):
    registry = unreal.AssetRegistryHelpers.get_asset_registry()
    rows = attempt(lambda: registry.get_assets_by_path(wip, True), []) or []
    paths = sorted(str(r.package_name) for r in rows if str(r.asset_class_path.asset_name) == "MetaHumanPerformance")
    return [p for p in paths if not ONLY or ONLY.lower() in p.rsplit("/", 1)[1].lower()]


def is_solved(perf):
    return bool(attempt(perf.contains_animation_data, False))


def anim_path(perf_path):
    folder, name = perf_path.rsplit("/", 1)
    return f"{folder}/{'AS_' + name[4:] if name.startswith('MHP_') else 'AS_' + name}"


def export_anim(perf, perf_path):
    """(ok, note)."""
    if not EXPORT:
        return True, "not exported (EXPORT = False)"
    target = anim_path(perf_path)
    if ASSETS.does_asset_exist(target):
        return True, f"{target} is there already"
    if EXPORTER is None:
        return False, "unreal.MetaHumanPerformanceExportUtils isn't there (MetaHuman plugin)"
    s = EXPORTER.get_export_animation_sequence_settings(perf)
    s.show_export_dialog = False
    s.package_path = target.rsplit("/", 1)[0]
    s.asset_name = target.rsplit("/", 1)[1]
    s.export_skeleton = unreal.PerformanceExportSkeleton.PERFORMER_SKELETON
    s.export_face = True
    s.export_body = True
    s.enable_head_movement = HEAD_MOVEMENT
    s.auto_save_anim_sequence = True
    anim = attempt(lambda: EXPORTER.export_animation_sequence(perf, s))
    if not anim:
        return False, f"export of {target} failed"
    return True, f"exported {anim.get_path_name().split('.')[0]} (face {'on' if s.export_face else 'off'}, " \
                 f"body {'on' if s.export_body else 'off'})"


class Batch:
    def __init__(self, queue, identity, identities, body):
        self.queue, self.identity, self.identities, self.body = list(queue), identity, identities, body
        self.total = len(queue)
        self.solved, self.exported, self.failed, self.skipped = [], [], [], []
        self.started = time.perf_counter()
        self.current = None                  # (path, perf, callback)

    def next(self):
        while self.queue:
            path = self.queue.pop(0)
            perf = unreal.load_asset(path)
            if perf is None:
                self.failed.append(f"{path}: couldn't be loaded")
                continue
            if is_solved(perf) and not REPROCESS:
                ok, note = export_anim(perf, path)
                (self.exported if ok else self.failed).append(f"{path.rsplit('/', 1)[1]}: {note}")
                self.skipped.append(path)
                out(f"[solved already] {path} - {note}")
                continue
            note = ""
            if perf.get_editor_property("identity") is None:
                take = path.rsplit("/", 2)[-2]
                identity, note = identity_for(take, self.identity, self.identities)
                if identity is not None:
                    perf.set_editor_property("identity", identity)
            else:
                note = perf.get_editor_property("identity").get_path_name().split(".")[0]
            attempt(lambda: perf.set_editor_property("body_tracking", bool(self.body)))

            on_done = self.callback(path, perf)      # no arguments: Unreal rejects a callback that has any,
            self.current = (path, perf, on_done)
            perf.on_processing_finished_dynamic.add_callable(on_done)
            done = self.total - len(self.queue)
            out(f"[solving] {path}   ({done} of {self.total}; {len(self.queue)} after this)   Identity: {note}")
            perf.start_pipeline()
            return
        self.finish()

    def callback(self, path, perf):
        def on_done():
            self.finished(path, perf)
        return on_done

    def finished(self, path, perf):
        attempt(lambda: perf.on_processing_finished_dynamic.remove_callable(self.current[2]))
        self.current = None
        if is_solved(perf):
            ASSETS.save_asset(path)
            self.solved.append(path)
            ok, note = export_anim(perf, path)
            (self.exported if ok else self.failed).append(f"{path.rsplit('/', 1)[1]}: {note}")
            out(f"[solved] {path} - {note}")
        else:
            self.failed.append(f"{path}: the solve gave no animation (see the LogMetaHuman lines above)")
            warn(f"[not solved] {path}")
        self.next()

    def finish(self):
        minutes = (time.perf_counter() - self.started) / 60
        summary = (f"Process finished in {minutes:.0f} min: {len(self.solved)} solved, "
                   f"{len(self.skipped)} solved already, {len(self.failed)} problem(s).")
        out("")
        out(summary)
        for line in self.failed:
            warn(f"  {line}")
        setattr(unreal, _STATE_KEY, None)
        if self.solved or self.skipped:
            summary += " Next: export_animations.py."
        if SHOW_DIALOG and getattr(unreal, "EditorDialog", None) is not None:
            text = summary + ("\n\nProblems:\n" + "\n".join(self.failed[:12]) if self.failed else "")
            attempt(lambda: unreal.EditorDialog.show_message("Process", text, unreal.AppMsgType.OK))


def run():
    running = getattr(unreal, _STATE_KEY, None)
    if running is not None and running.current is not None and not FORCE:
        message = (f"A Process batch is already running ({running.current[0]}; {len(running.queue)} after it). "
                   "Let it finish - the Output Log shows its progress. (If it stopped on an error, set FORCE = True and run "
                   "again.)")
        warn(message)
        if SHOW_DIALOG and getattr(unreal, "EditorDialog", None) is not None:
            attempt(lambda: unreal.EditorDialog.show_message("Process", message, unreal.AppMsgType.OK))
        return
    s = settings()
    out("Process the Performances")
    if s["config"]:
        out(f"Settings:      {s['config']}")
    queue = performances(s["wip"])
    out(f"Looks in:      {s['wip']}   ({len(queue)} Performance(s))")
    out("Identity:      " + (f"{s['identity']} for takes without one" if s["identity"] else "none set (takes without one "
                             "are solved from the video alone)") + (f"; by name: {s['identities']}" if s["identities"] else ""))
    out(f"Body tracking: {'on' if s['body'] else 'off'};  re-solve solved ones: {'yes' if REPROCESS else 'no'}")
    if not queue:
        message = f"No MetaHuman Performances under {s['wip']}. Run Ingest.py first."
        warn(message)
        if SHOW_DIALOG and getattr(unreal, "EditorDialog", None) is not None:
            attempt(lambda: unreal.EditorDialog.show_message("Process", message, unreal.AppMsgType.OK))
        return
    batch = Batch(queue, s["identity"], s["identities"], s["body"])
    setattr(unreal, _STATE_KEY, batch)
    batch.next()


if __name__ != "Process":
    run()
