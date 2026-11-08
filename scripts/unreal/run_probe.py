"""
Run probe_unreal_layers.py inside the Unreal Editor and copy its report next to it.

Put this file in the same folder as the probe, open your Level Sequence in Sequencer, then
either:
  - File > Execute Python Script > run_probe.py, or
  - Output Log, switch the box at the bottom-left from "Cmd" to "Python", and paste:
        import runpy; runpy.run_path(r"<your Scripts folder>\\run_probe.py", run_name="__main__")

The report ends up as unreal_probe.txt in that folder (and in the project's Saved folder).
"""

import glob
import os
import runpy
import shutil

import unreal

# Folder holding the probe script, when this script's own location isn't known ("" = the workspace's Scripts
# folder from pipeline_config.json, else the project's Saved folder)
FOLDER = ""
REPORT_NAME = "unreal_probe.txt"


def find_probe(folder):
    """Newest file in `folder` that looks like the probe (handles renamed downloads)."""
    candidates = [p for p in glob.glob(os.path.join(folder, "*.py"))
                  if "probe" in os.path.basename(p).lower()
                  and "layer" in os.path.basename(p).lower()
                  and os.path.basename(p).lower() != "run_probe.py"]
    return max(candidates, key=os.path.getmtime) if candidates else None


def main():
    folders = []
    if "__file__" in globals():
        folders.append(os.path.dirname(os.path.abspath(__file__)))
    if FOLDER:
        folders.append(FOLDER)
    try:                                     # the workspace's Scripts folder (create_workspace.py notes it)
        import pipeline_config as pc
        cfg = pc.load_config(folders[0] if folders else None)
        if cfg.get("_config_path"):
            folders.append(pc.project_path(cfg, "scripts"))
    except Exception:
        pass

    probe = None
    for folder in folders:
        if os.path.isdir(folder):
            probe = find_probe(folder)
            if probe:
                break
    if probe is None:
        unreal.log_error(f"Couldn't find the probe script (probe_unreal_layers.py) in: {folders}")
        return

    unreal.log(f"Running {probe}")
    runpy.run_path(probe, run_name="__main__")

    saved = os.path.join(os.path.normpath(unreal.Paths.convert_relative_path_to_full(
        unreal.Paths.project_saved_dir())), REPORT_NAME)
    if os.path.isfile(saved):
        target = os.path.join(os.path.dirname(probe), REPORT_NAME)
        try:
            shutil.copy2(saved, target)
            unreal.log(f"Report copied to {target}  <- send this file")
        except OSError as err:
            unreal.log_warning(f"Report is at {saved} (couldn't copy it next to the probe: {err})")
    else:
        unreal.log_warning("The probe didn't write a report; check the Output Log for errors.")


main()
