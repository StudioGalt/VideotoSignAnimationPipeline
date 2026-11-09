"""
Probe: what can Unreal's Python do with Control Rig animation layers in this engine version?

Before running:
  1. Open a Level Sequence that has your MetaHuman with animation baked to its Control Rig
     (a Control Rig track with keys). The sequence must be open in Sequencer.
  2. File > Execute Python Script > this file.

It reports to the Output Log and saves the full report to
    <project>/Saved/unreal_probe.txt     <- send this file back

What it does:
  A. Lists the Python classes / functions related to Control Rig, animation layers, poses
     and snapping that exist in this engine.
  B. Describes the open sequence: bindings, tracks, sections and their blend types.
  C. Lists every control on each Control Rig (name, type), and flags the likely IK/FK
     switches, IK controls and pole controls.
  D. Layer test: adds an additive section to each Control Rig track, checks it, and
     removes it again. The sequence is left as it was (and the test is one undo step).
     Set TEST_ADD_LAYER = False to skip this.
  E. Lists the Control Rig assets in the project (the rigs Bake To Control Rig can target).

Nothing else in the project is changed.
"""

import os
import traceback

import unreal

TEST_ADD_LAYER = True
OUTPUT_NAME = "unreal_probe.txt"

# Names worth finding in the unreal module (case-insensitive substrings)
MODULE_KEYWORDS = ("animlayer", "anim_layer", "mixer", "controlrigsequencer", "controlrigpose",
                   "controlrigsnap", "tween", "movieScenecontrolrig".lower(), "animdetail",
                   "levelsequenceeditor", "controlrigblueprint")
# Control names that suggest IK / FK machinery
CONTROL_KEYWORDS = ("ik", "fk", "switch", "pole", "pv", "foot", "hand", "global", "root", "body")

_lines = []


def out(text=""):
    _lines.append(str(text))


def section_header(title):
    out()
    out("=" * 78)
    out(title)
    out("=" * 78)


def attempt(label, func, default=None):
    """Run func(); on failure record the error under `label` and return `default`."""
    try:
        return func()
    except Exception as err:
        out(f"  !! {label}: {type(err).__name__}: {err}")
        return default


def public_names(obj):
    return sorted(n for n in dir(obj) if not n.startswith("_"))


def full(path):
    return os.path.normpath(unreal.Paths.convert_relative_path_to_full(path))


# ---------------------------------------------------------------- A. API inventory
def probe_api():
    section_header("A. Python API in this engine")
    out(f"Engine version: {attempt('engine version', unreal.SystemLibrary.get_engine_version, '?')}")

    names = [n for n in dir(unreal) if any(k in n.lower() for k in MODULE_KEYWORDS)]
    out(f"\nClasses matching {MODULE_KEYWORDS}:")
    for n in sorted(names):
        out(f"  {n}")

    # Full method lists for the ones the cleanup would rely on
    important = ["ControlRigSequencerLibrary", "LevelSequenceEditorBlueprintLibrary",
                 "MovieSceneControlRigParameterTrack", "MovieSceneControlRigParameterSection",
                 "ControlRigPoseAsset", "ControlRigSnapSettings", "ControlRigTweenLibrary"]
    important += [n for n in names if "layer" in n.lower() or "mixer" in n.lower()]
    seen = set()
    for n in important:
        if n in seen:
            continue
        seen.add(n)
        cls = getattr(unreal, n, None)
        out(f"\n--- unreal.{n}: {'present' if cls is not None else 'NOT PRESENT'}")
        if cls is not None:
            for m in public_names(cls):
                out(f"    {m}")

    out("\nBlend types (unreal.MovieSceneBlendType):")
    blend = getattr(unreal, "MovieSceneBlendType", None)
    out(f"  {public_names(blend) if blend else 'NOT PRESENT'}")


# ---------------------------------------------------------------- B. the open sequence
def describe_section(section, indent="      "):
    bits = [section.get_class().get_name()]
    for label, getter in (("row", "get_row_index"), ("start", "get_start_frame"),
                          ("end", "get_end_frame"), ("blend", "get_blend_type")):
        fn = getattr(section, getter, None)
        if fn is not None:
            try:
                value = fn()
                value = getattr(value, "blend_type", value)
                bits.append(f"{label}={value}")
            except Exception as err:
                bits.append(f"{label}=<{type(err).__name__}>")
    channels = attempt("get_all_channels", lambda: section.get_all_channels(), None)
    if channels is not None:
        bits.append(f"channels={len(channels)}")
    out(indent + ", ".join(str(b) for b in bits))


def _rate(rate):
    num, den = getattr(rate, "numerator", None), getattr(rate, "denominator", None)
    return f"{num}/{den} fps" if num is not None else str(rate)


def probe_sequence():
    section_header("B. Open Level Sequence")
    lib = getattr(unreal, "LevelSequenceEditorBlueprintLibrary", None)
    sequence = attempt("get_current_level_sequence", lambda: lib.get_current_level_sequence()) if lib else None
    if sequence is None:
        out("No Level Sequence is open in Sequencer. Open one with the MetaHuman's Control Rig")
        out("track and run the probe again.")
        return None

    out(f"Sequence: {sequence.get_path_name()}")
    out(f"Playback: {attempt('start', sequence.get_playback_start)} .. "
        f"{attempt('end', sequence.get_playback_end)}  "
        f"display rate {attempt('rate', lambda: _rate(sequence.get_display_rate()))}")

    for binding in attempt("get_bindings", sequence.get_bindings, []) or []:
        parent = attempt("get_parent", binding.get_parent, None)
        parent_name = attempt("parent name", lambda: parent.get_display_name(), "") if parent else ""
        out(f"\nBinding: {attempt('display name', binding.get_display_name, '?')}"
            + (f"   (child of {parent_name})" if parent_name else ""))
        for track in attempt("get_tracks", binding.get_tracks, []) or []:
            out(f"  Track: {track.get_class().get_name()}  '{attempt('track name', track.get_display_name, '')}'")
            for section in attempt("get_sections", track.get_sections, []) or []:
                describe_section(section)
    return sequence


# ---------------------------------------------------------------- C. control rigs and controls
def _enum_name(value):
    """'EULER_TRANSFORM' from an Unreal enum value (or its string form)."""
    name = getattr(value, "name", None)
    if name:
        return str(name)
    text = str(value).split(".")[-1]
    return text.split(":")[0].strip("<> ")


def control_rows(control_rig):
    """[(name, type string)] for every control, as far as this engine exposes it."""
    rows = []
    hierarchy = attempt("get_hierarchy", control_rig.get_hierarchy, None)
    if hierarchy is None:
        return rows
    keys = None
    for getter in (lambda: hierarchy.get_controls(True), lambda: hierarchy.get_controls(),
                   lambda: [k for k in hierarchy.get_all_keys() if str(k.type).upper().endswith("CONTROL")]):
        try:
            keys = list(getter())
            break
        except Exception:
            continue
    if keys is None:
        out("  !! couldn't list controls from the hierarchy")
        return rows
    for key in keys:
        name = str(getattr(key, "name", key))
        ctype = ""
        try:
            getter = getattr(hierarchy, "get_control_settings", None)
            settings = getter(key) if getter else hierarchy.find_control(key).settings
            ctype = _enum_name(settings.control_type)
            anim = getattr(settings, "animation_type", None)
            if anim is not None:
                ctype += f" / {_enum_name(anim)}"
        except Exception:
            pass
        rows.append((name, ctype))
    return rows


def probe_control_rigs(sequence):
    section_header("C. Control Rigs and their controls")
    lib = getattr(unreal, "ControlRigSequencerLibrary", None)
    if lib is None or sequence is None:
        out("ControlRigSequencerLibrary not available or no sequence open.")
        return []
    proxies = attempt("get_control_rigs", lambda: lib.get_control_rigs(sequence), []) or []
    if not proxies:
        out("No Control Rig tracks in this sequence. Bake the animation to the Control Rig first")
        out("(right-click the body track > Bake To Control Rig), then run the probe again.")
        return []

    for proxy in proxies:
        rig = attempt("proxy.control_rig", lambda: proxy.control_rig, None)
        track = attempt("proxy.track", lambda: proxy.track, None)
        out(f"\nControl Rig: {rig.get_name() if rig else '?'}   class {rig.get_class().get_name() if rig else '?'}")
        out(f"  track: {track.get_class().get_name() if track else '?'}, "
            f"sections: {len(track.get_sections()) if track else '?'}")
        if rig is None:
            continue
        rows = control_rows(rig)
        out(f"  {len(rows)} controls")

        flagged = [(n, t) for n, t in rows if any(k in n.lower() for k in CONTROL_KEYWORDS)]
        switches = [(n, t) for n, t in rows
                    if ("switch" in n.lower() or ("ik" in n.lower() and "fk" in n.lower()))
                    or t.upper().startswith(("BOOL", "INTEGER"))]
        out("\n  Likely IK/FK switches (bool/integer controls, or 'ik'+'fk'/'switch' in the name):")
        for n, t in switches:
            out(f"    {n:<40} {t}")
        out("\n  Controls with ik / fk / pole / foot / hand / root in the name:")
        for n, t in flagged:
            out(f"    {n:<40} {t}")
        out("\n  All controls:")
        for n, t in rows:
            out(f"    {n:<40} {t}")
    return proxies


# ---------------------------------------------------------------- D. layer test
def probe_layers(sequence, proxies):
    section_header("D. Layer test (add an additive section, then remove it)")
    if not TEST_ADD_LAYER:
        out("Skipped (TEST_ADD_LAYER = False).")
        return
    if not proxies:
        out("Skipped: no Control Rig track to test on.")
        return

    additive = getattr(getattr(unreal, "MovieSceneBlendType", None), "ADDITIVE", None)
    out(f"MovieSceneBlendType.ADDITIVE: {additive}")

    for proxy in proxies:
        track = attempt("proxy.track", lambda: proxy.track, None)
        rig = attempt("proxy.control_rig", lambda: proxy.control_rig, None)
        if track is None:
            continue
        out(f"\nTrack of {rig.get_name() if rig else '?'}:")
        out(f"  has set_section_to_key: {hasattr(track, 'set_section_to_key')}, "
            f"get_section_to_key: {hasattr(track, 'get_section_to_key')}")
        before = list(track.get_sections())
        out(f"  sections before: {len(before)}")
        for s in before:
            describe_section(s, "    ")
        key_section_before = attempt("get_section_to_key", lambda: track.get_section_to_key(), None) \
            if hasattr(track, "get_section_to_key") else None

        new_section = None
        try:
            with unreal.ScopedEditorTransaction("Probe: additive Control Rig section test"):
                new_section = track.add_section()
                out(f"  add_section() -> {new_section.get_class().get_name() if new_section else None}")
                if new_section is not None:
                    if additive is not None:
                        attempt("set_blend_type(ADDITIVE)", lambda: new_section.set_blend_type(additive))
                    attempt("set_row_index", lambda: new_section.set_row_index(len(before)))
                    attempt("set_range", lambda: new_section.set_range(sequence.get_playback_start(),
                                                                        sequence.get_playback_end()))
                    out("  new section:")
                    describe_section(new_section, "    ")
                    if hasattr(track, "set_section_to_key"):
                        attempt("set_section_to_key(new)", lambda: track.set_section_to_key(new_section))
                        out(f"  section to key is now the new one: "
                            f"{attempt('get_section_to_key', lambda: track.get_section_to_key() == new_section)}")
                    out(f"  sections after add: {len(track.get_sections())}")
        except Exception:
            out("  !! layer test failed:")
            for line in traceback.format_exc().splitlines():
                out("     " + line)
        finally:
            # Put everything back
            if new_section is not None:
                attempt("remove_section", lambda: track.remove_section(new_section))
            if key_section_before is not None and hasattr(track, "set_section_to_key"):
                attempt("restore section to key", lambda: track.set_section_to_key(key_section_before))
            after = len(track.get_sections())
            out(f"  sections after cleanup: {after} "
                f"({'restored' if after == len(before) else 'NOT RESTORED - undo once (Ctrl+Z)'})")

    lib = getattr(unreal, "LevelSequenceEditorBlueprintLibrary", None)
    if lib is not None and hasattr(lib, "refresh_current_level_sequence"):
        attempt("refresh sequencer", lib.refresh_current_level_sequence)


# ---------------------------------------------------------------- E. control rig assets
def probe_rig_assets():
    section_header("E. Control Rig assets available (what 'Bake To Control Rig' can target)")
    helpers = getattr(unreal, "AssetRegistryHelpers", None)
    if helpers is None:
        out("AssetRegistryHelpers not available.")
        return
    registry = helpers.get_asset_registry()
    assets = attempt("get_all_assets", lambda: registry.get_all_assets(), []) or []
    rows = []
    for a in assets:
        try:
            cls = str(a.asset_class_path.asset_name)
        except Exception:
            cls = str(getattr(a, "asset_class", ""))
        if "controlrig" in cls.lower():
            rows.append((str(a.asset_name), cls, str(a.package_name)))
    rows.sort(key=lambda r: (0 if "metahuman" in (r[0] + r[2]).lower() else 1, r[0].lower()))
    out(f"{len(rows)} Control Rig assets (MetaHuman ones first):")
    for name, cls, package in rows:
        out(f"  {name:<40} {cls:<24} {package}")
    if not any("metahuman" in (r[0] + r[2]).lower() for r in rows):
        out("  (none with 'MetaHuman' in the name or path)")


# ---------------------------------------------------------------- run
def main():
    for step in (probe_api,):
        attempt(step.__name__, step)
    sequence = attempt("probe_sequence", probe_sequence, None)
    proxies = attempt("probe_control_rigs", lambda: probe_control_rigs(sequence), []) or []
    attempt("probe_layers", lambda: probe_layers(sequence, proxies))
    attempt("probe_rig_assets", probe_rig_assets)

    path = os.path.join(full(unreal.Paths.project_saved_dir()), OUTPUT_NAME)
    try:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(_lines) + "\n")
        saved = f"Probe report saved to {path}"
    except OSError as err:
        saved = f"Couldn't save the report ({err}); copy it from the Output Log instead"

    # Sections B-D to the log (A is long; it is in the file)
    start = next((i for i, l in enumerate(_lines) if l.startswith("B. ")), 0)
    for line in _lines[max(start - 1, 0):]:
        unreal.log(line)
    unreal.log(f"{len(_lines)} lines. {saved}")


main()
