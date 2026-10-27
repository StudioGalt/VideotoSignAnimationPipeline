"""
Capture the neutral pose for unreal_procedural_fixes.py.

The procedural fixes start every clip in the neutral pose and bring it back to that pose at
the end. This script saves the pose once; the same file is then used for every clip.

How to use:
  1. Run unreal_procedural_fixes.py on a clip (so the arms and legs are in IK), then duplicate
     that Level Sequence in the Content Browser (for example LS_Neutral_Pose) and open the copy.
     Posing in a copy keeps the clip itself untouched, and the copy stays around as the editable
     source of the pose.
  2. In the copy, go to frame 0 and pose the character into the neutral pose. Key what you move
     (with auto-key on, moving a control keys it). Pose on the bottom section of the Control Rig
     track, not on a layer.
  3. Leave the playhead on that frame and run this script (File > Execute Python Script).

It saves neutral_pose.json next to this script and in <project>/Saved; unreal_procedural_fixes.py
looks in both places. Nothing in the sequence is changed. The file holds the value of every
animatable control on that frame, so controls you didn't pose keep the value they had there.
"""

import json
import os
import time

import unreal

POSE_FRAME = None                   # frame to capture; None = where the playhead is
OUTPUT_NAME = "neutral_pose.json"
SHOW_DIALOG = True


def _current_frame(sequence):
    if POSE_FRAME is not None:
        return int(POSE_FRAME)
    lib = unreal.LevelSequenceEditorBlueprintLibrary
    for getter in ("get_local_position", "get_current_time"):
        fn = getattr(lib, getter, None)
        if fn is not None:
            try:
                return int(fn())
            except Exception:
                continue
    return int(sequence.get_playback_start())


def _value_at(keys, frame):
    """(value, exact) of the key at `frame`, else of the last key before it, else of the first key."""
    best = None
    first = None
    for key in keys:
        f = int(key.get_time().frame_number.value)
        if f == frame:
            return key.get_value(), True
        if f < frame and (best is None or f > best[0]):
            best = (f, key)
        if first is None or f < first[0]:
            first = (f, key)
    pick = best or first
    return (pick[1].get_value(), False) if pick else (None, False)


def capture_neutral_pose():
    lib = unreal.ControlRigSequencerLibrary
    sequence = unreal.LevelSequenceEditorBlueprintLibrary.get_current_level_sequence()
    if sequence is None:
        return False, "No Level Sequence is open in Sequencer. Open the sequence and run again."

    best = None
    for proxy in list(lib.get_control_rigs(sequence)):
        rig, track = proxy.control_rig, proxy.track
        if rig is None or track is None:
            continue
        hierarchy = rig.get_hierarchy()
        names = set()
        for getter in (lambda: hierarchy.get_controls(True), lambda: hierarchy.get_controls()):
            try:
                names = {str(getattr(k, "name", k)) for k in getter()}
                break
            except Exception:
                continue
        score = ("hand_l_ik_ctrl" in names, len(names))
        if best is None or score > best[0]:
            best = (score, rig, track, names)
    if best is None:
        return False, ("This sequence has no Control Rig track. Bake the Body animation to "
                       "MetaHuman_ControlRig first.")
    _, rig, track, controls = best

    sections = list(track.get_sections())
    if not sections:
        return False, "The Control Rig track has no section."
    base = min(sections, key=lambda s: s.get_row_index())
    frame = _current_frame(sequence)

    values, inexact = {}, 0
    for channel in list(base.get_all_channels()):
        name = str(channel.channel_name)
        control = name.split(".", 1)[0]
        if controls and control not in controls:
            continue                                    # e.g. the section's own Weight channel
        kind = channel.get_class().get_name()
        if "Float" not in kind and "Double" not in kind:
            continue                                    # on/off and integer switches aren't part of a pose
        keys = list(channel.get_keys())
        value, exact = _value_at(keys, frame)
        if value is None:
            try:
                value = channel.get_default() if channel.has_default() else None
            except Exception:
                value = None
        if value is None:
            continue
        inexact += 0 if exact or not keys else 1
        values[name] = float(value)
    if not values:
        return False, "No control values found on the Control Rig track. Is the animation baked to it?"

    data = {
        "format": 1,
        "rig": rig.get_name(),
        "sequence": sequence.get_path_name(),
        "frame": frame,
        "engine": unreal.SystemLibrary.get_engine_version(),
        "captured": time.strftime("%Y-%m-%d %H:%M:%S"),
        "values": values,
    }
    folders = []
    if "__file__" in globals():
        folders.append(os.path.dirname(os.path.abspath(__file__)))
    folders.append(os.path.normpath(unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_saved_dir())))
    saved = []
    for folder in folders:
        try:
            path = os.path.join(folder, OUTPUT_NAME)
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(data, handle, indent=1, sort_keys=True)
            saved.append(path)
        except OSError as err:
            unreal.log_warning(f"Couldn't save {OUTPUT_NAME} in {folder}: {err}")
    if not saved:
        return False, "Couldn't save the pose file anywhere (see the Output Log)."

    controls_saved = len({n.split('.', 1)[0] for n in values})
    message = (f"Neutral pose captured from frame {frame}: {len(values)} channels on "
               f"{controls_saved} controls of {rig.get_name()}.\nSaved to {saved[0]}")
    if len(sections) > 1:
        message += (f"\nNote: the track has {len(sections)} sections (layers); only the bottom one "
                    "was read. Pose on the bottom section, not on a layer.")
    if inexact:
        message += (f"\nNote: {inexact} channels have no key on frame {frame}; "
                    "their last key before it was used.")
    message += ("\nIf you posed in the clip itself rather than in a copy, undo the posing now."
                "\nThen run unreal_procedural_fixes.py on each clip.")
    return True, message


def _main():
    ok, message = capture_neutral_pose()
    for line in message.splitlines():
        (unreal.log if ok else unreal.log_error)(line)
    if SHOW_DIALOG and getattr(unreal, "EditorDialog", None) is not None:
        try:
            unreal.EditorDialog.show_message("Capture neutral pose", message, unreal.AppMsgType.OK)
        except Exception:
            pass
    return ok


if __name__ != "capture_neutral_pose":
    _main()
