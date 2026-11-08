"""
Probe: where is the facial data in this Unreal project, and how can it be exported?

Run in the Unreal Editor: File > Execute Python Script. It changes nothing.
Everything is printed to the Output Log and saved to <project>/Saved/face_probe.txt
(and next to this script).

It reports:
  A. The Animation Sequences on MetaHuman skeletons: how many curves each has, what the
     curves are called and how many of them actually move. Face animation from MetaHuman
     Animator is stored as curves, so this shows whether an animation carries a face.
  B. The MetaHuman Performance assets (the solved takes) and what they hold.
  C. What this engine version offers for exporting animation from a Performance.
  D. The face skeletons and face meshes in the project (what a face export would target).
"""

import os
import shutil

import unreal

OUTPUT_NAME = "face_probe.txt"
MAX_ANIMATIONS = 12
FACE_WORDS = ("jaw", "brow", "eye", "mouth", "lip", "cheek", "nose", "tongue", "blink", "squint", "smile")
SKELETON_HINTS = ("metahuman", "face_archetype", "face")
PERFORMANCE_KEYWORDS = ("export", "anim", "body", "face", "head", "solve", "track", "frame", "identity", "capture",
                        "input", "skeleton", "rig", "process", "can_", "audio", "footage", "camera", "contains")

_lines = []


def out(text=""):
    _lines.append(str(text))
    unreal.log(str(text))


def header(title):
    out("=" * 78)
    out(title)
    out("=" * 78)


def attempt(func, default=None):
    try:
        return func()
    except Exception as err:
        return default if default is not None else f"<{type(err).__name__}: {err}>"


def class_name(asset_data):
    try:
        return str(asset_data.asset_class_path.asset_name)
    except Exception:
        return str(getattr(asset_data, "asset_class", ""))


def all_assets():
    registry = unreal.AssetRegistryHelpers.get_asset_registry()
    return list(registry.get_assets_by_path("/Game", True))


def own_names(obj, base=None):
    """Public names of obj that aren't on every Unreal object."""
    common = set(dir(base if base is not None else unreal.Object))
    return sorted(n for n in dir(obj) if not n.startswith("_") and n not in common)


# ------------------------------------------------------------------ A. animations
def skeleton_of(data):
    try:
        return str(data.get_tag_value("Skeleton") or "").rstrip("'\"").rsplit("/", 1)[-1].split(".")[0]
    except Exception:
        return ""


def probe_animations(assets):
    header("A. Animation Sequences on MetaHuman skeletons")
    rows = [(d, skeleton_of(d)) for d in assets if class_name(d) == "AnimSequence"]
    rows = [(d, s) for d, s in rows if any(h in s.lower() for h in SKELETON_HINTS)
            and not str(d.package_path).startswith("/Game/MetaHumans/")]
    if not rows:
        out("None found outside /Game/MetaHumans.")
        return False
    library = unreal.AnimationLibrary
    any_face = False
    for data, skeleton in rows[:MAX_ANIMATIONS]:
        animation = data.get_asset()
        if animation is None:
            continue
        names = [str(n) for n in attempt(
            lambda: library.get_animation_curve_names(animation, unreal.RawCurveTrackTypes.RCT_FLOAT), [])]
        frames = attempt(lambda: library.get_num_frames(animation), "?")
        bones = attempt(lambda: len(library.get_animation_track_names(animation)), "?")
        out(f"{data.package_path}/{data.asset_name}")
        out(f"  skeleton {skeleton}, {frames} frames, {bones} bone tracks, {len(names)} curves")
        if not names:
            continue
        groups = {}
        for name in names:
            parts = name.split("_")
            key = "_".join(parts[:2]) if len(parts) > 2 else parts[0]
            groups[key] = groups.get(key, 0) + 1
        top = sorted(groups.items(), key=lambda item: -item[1])[:8]
        out("  curve names start with: " + ", ".join(f"{k} ({v})" for k, v in top))
        out("  first names: " + ", ".join(names[:14]))
        face_like = [n for n in names if "ctrl_expressions" in n.lower() or any(w in n.lower() for w in FACE_WORDS)]
        moving, biggest = 0, []
        for name in names:
            try:
                _, values = library.get_float_keys(animation, name)
                values = [float(v) for v in values]
                spread = (max(values) - min(values)) if values else 0.0
            except Exception:
                spread = 0.0
            if spread > 1e-4:
                moving += 1
                biggest.append((spread, name))
        out(f"  curves that look like face controls: {len(face_like)}"
            + (f", e.g. {', '.join(face_like[:6])}" if face_like else ""))
        out(f"  curves whose value changes during the clip: {moving} of {len(names)}"
            + (f"; most active: {', '.join(n for _, n in sorted(biggest, reverse=True)[:6])}" if biggest else ""))
        any_face = any_face or bool(face_like)
    if len(rows) > MAX_ANIMATIONS:
        out(f"... and {len(rows) - MAX_ANIMATIONS} more")
    return any_face


# ------------------------------------------------------------------ B. performances
def show_value(value):
    try:
        if hasattr(value, "get_path_name"):
            return value.get_path_name().split(".")[0]
    except Exception:
        pass
    text = str(value)
    return text if len(text) < 110 else text[:107] + "..."


def probe_performances(assets):
    header("B. MetaHuman Performance assets")
    rows = [d for d in assets if "performance" in class_name(d).lower()]
    if not rows:
        out("None found in this project. Classes of the assets that are here:")
        counts = {}
        for d in assets:
            if not str(d.package_path).startswith(("/Game/MetaHumans/", "/Game/Characters/")):
                counts[class_name(d)] = counts.get(class_name(d), 0) + 1
        out("  " + ", ".join(f"{k} ({v})" for k, v in sorted(counts.items(), key=lambda item: -item[1])[:25]))
        return []
    performances = []
    for data in rows[:MAX_ANIMATIONS]:
        performance = data.get_asset()
        out(f"{data.package_path}/{data.asset_name}   ({class_name(data)})")
        if performance is None:
            out("  couldn't be loaded")
            continue
        performances.append(performance)
        names = [n for n in own_names(performance) if any(k in n.lower() for k in PERFORMANCE_KEYWORDS)]
        values, methods = [], []
        for name in names:
            member = attempt(lambda: getattr(performance, name))
            if callable(member):
                methods.append(name)
                if name.startswith(("contains_", "can_", "is_", "has_", "get_number")):
                    values.append(f"{name}() = {show_value(attempt(member))}")
            else:
                values.append(f"{name} = {show_value(member)}")
        for line in values[:45]:
            out(f"  {line}")
        if performances and performance is performances[0]:
            out("  methods: " + ", ".join(methods[:60]))
    if len(rows) > MAX_ANIMATIONS:
        out(f"... and {len(rows) - MAX_ANIMATIONS} more")
    return performances


# ------------------------------------------------------------------ C. export API
def probe_export_api():
    header("C. Export functions in this engine")
    names = sorted(n for n in dir(unreal) if "performance" in n.lower() and "metahuman" in n.lower())
    out("MetaHuman Performance classes: " + (", ".join(names) or "none"))
    utils = getattr(unreal, "MetaHumanPerformanceExportUtils", None)
    if utils is None:
        out("unreal.MetaHumanPerformanceExportUtils: NOT PRESENT")
    else:
        for name in own_names(utils):
            member = getattr(utils, name, None)
            doc = [l.strip() for l in (getattr(member, "__doc__", "") or "").strip().splitlines() if l.strip()]
            out(f"  {name}: " + (" | ".join(doc[:9]) if doc else ""))
    for class_name_ in names:
        if "setting" not in class_name_.lower():
            continue
        cls = getattr(unreal, class_name_)
        out(f"unreal.{class_name_}:")
        try:
            instance = cls()
        except Exception as err:
            out(f"  couldn't create one: {err}")
            continue
        base = getattr(unreal, "StructBase", unreal.Object) if not isinstance(instance, unreal.Object) else unreal.Object
        for name in own_names(instance, base):
            member = attempt(lambda: getattr(instance, name))
            if not callable(member):
                out(f"  {name} = {show_value(member)}")
    enums = [n for n in dir(unreal) if "performance" in n.lower() and "export" in n.lower() and n not in names]
    enums += ["PerformanceExportRange", "PerformanceHeadMovementMode", "DataInputType", "SolveType"]
    for enum_name in sorted(set(enums)):
        enum = getattr(unreal, enum_name, None)
        values = [n for n in dir(enum) if n.isupper()] if enum is not None else []
        if values:
            out(f"unreal.{enum_name}: {', '.join(values)}")


# ------------------------------------------------------------------ D. face skeletons and meshes
def probe_face_assets(assets):
    header("D. Face skeletons and face meshes")
    rows = [d for d in assets if class_name(d) in ("Skeleton", "SkeletalMesh") and "face" in str(d.asset_name).lower()]
    if not rows:
        out("None with 'face' in the name under /Game.")
    for d in rows[:20]:
        out(f"  {class_name(d):<13} {d.package_path}/{d.asset_name}")
    control_rigs = [d for d in assets if "controlrig" in class_name(d).lower() and "face" in str(d.asset_name).lower()]
    for d in control_rigs[:10]:
        out(f"  {class_name(d):<13} {d.package_path}/{d.asset_name}")


# ------------------------------------------------------------------ run
def main():
    del _lines[:]
    out(f"Face data probe - engine {attempt(unreal.SystemLibrary.get_engine_version)}")
    assets = attempt(all_assets, [])
    if isinstance(assets, str):
        out(f"Couldn't list the project's assets: {assets}")
        assets = []
    has_face = False
    for step in (lambda: probe_animations(assets), lambda: probe_performances(assets),
                 probe_export_api, lambda: probe_face_assets(assets)):
        try:
            result = step()
            if result is True:
                has_face = True
        except Exception as err:
            out(f"  !! this part failed: {type(err).__name__}: {err}")
    header("Summary")
    out("At least one animation carries curves that look like face controls." if has_face else
        "No animation here carries curves that look like face controls.")

    saved = os.path.normpath(unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_saved_dir()))
    path = os.path.join(saved, OUTPUT_NAME)
    try:
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("\n".join(_lines) + "\n")
        if "__file__" in globals():
            shutil.copy2(path, os.path.join(os.path.dirname(os.path.abspath(__file__)), OUTPUT_NAME))
        unreal.log(f"Probe saved to {path}")
    except OSError as err:
        unreal.log_warning(f"Couldn't save the probe: {err}")


main()
