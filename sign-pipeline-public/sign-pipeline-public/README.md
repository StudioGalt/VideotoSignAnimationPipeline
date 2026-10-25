# Sign Pipeline

Tools for turning labelled sign-language video into clean character animation on a MetaHuman, in two phases:

1. **Unreal Engine** – the labelled videos are solved into MetaHuman Performances and animation (MetaHuman Animator / the Performance Capture Toolkit), then exported for Blender: each take's body animation, face and video, and the MetaHuman character itself.
2. **MetaHuman in Blender** – the MetaHuman is built in Blender with a control rig, each take is put on it and cleaned up, and the motion is handed on as a Reference FBX.

Every path and name that changes from one setup to another lives in one settings file, `pipeline_config.json`, in your workspace folder, so nothing in the scripts is tied to one machine.

**New here? Start with [docs/GETTING_STARTED.md](docs/GETTING_STARTED.md)**: the whole pipeline once, from a new Unreal project to a cleaned-up take in Blender, step by step. This README is the reference for each script and setting.

---

## Requirements

| | |
|---|---|
| Python | 3.8 or newer, only to run `create_workspace.py` (nothing to install) |
| Unreal Engine | 5.8 with the MetaHuman plugins and the **Performance Capture Toolkit** (Fab) |
| Blender | 5.x (developed on 5.1 and 5.2) |
| Blender add-ons | **Animation Layers** (by Tal); the **Character DNA** add-on (Pro or standard; optional, for a face that runs RigLogic) |

---

## Setup: the workspace

The workspace is the folder everything is done in. Make it with:

```bash
git clone <this repository>
cd sign-pipeline
python create_workspace.py
```

It asks for the workspace folder and a few paths and names; press Enter to take each default. To skip the questions:

```bash
python create_workspace.py "D:/Sign Workspace" --yes
python create_workspace.py "D:/Sign Workspace" --video-source "E:/Videos/Labelled" --unreal-project "D:/Unreal/MyProject/MyProject.uproject" --yes
```

It makes this layout, writes `pipeline_config.json` and copies the scripts in:

```
Sign Workspace/
  pipeline_config.json      your settings
  Scripts/                  every pipeline script, in one folder (they find each other there)
  Videos/Labelled/          the labelled source videos, waiting for Unreal
  Videos/Unlabelled/        videos not labelled yet
  Videos/Holding/           videos set aside (the Unreal export only looks in Labelled)
  Capture Media/            Capture Manager's converted footage (Ingest.py)
  Unreal Exports/           phase 1: one folder per take (animation, face, video)
  MetaHuman Export/         phase 1: the MetaHuman character (meshes, textures, hair, DNA)
  MetaHuman/                phase 2: MetaHuman_character.blend, neutral_pose.json, Xsens_reference.fbx
  Storage/                  pose libraries: Storage/MH_rig (the MetaHuman rig's poses) …
  Logs/
  <take>/                   one per take, made by phase 2
```

It also notes the workspace on this computer (`~/.sign_pipeline.json`), so a script finds the settings even when it is run from somewhere outside the workspace.

Run `create_workspace.py` again at any time, for example after `git pull`: it adds missing folders and updates the scripts. Your settings are kept, and a script you changed in the workspace is saved as `.bak` before it is replaced (`--no-copy` leaves the scripts alone). All options: `python create_workspace.py --help`.

Any folder can live elsewhere (another drive, say): give it on the command line, or edit its path in `pipeline_config.json`.

---

## Settings: `pipeline_config.json`

Paths inside the workspace are stored relative to it, so the workspace can be moved; a path outside it is stored in full.

| Key | Default | Used for |
|---|---|---|
| `paths.scripts` | `Scripts` | the pipeline scripts |
| `paths.video_source` | `Videos/Labelled` | the labelled videos; `export_animations.py` moves each take's video into its export folder |
| `paths.video_unlabelled`, `paths.video_holding` | `Videos/Unlabelled`, `Videos/Holding` | made with the workspace; no script reads them |
| `paths.capture_media` | `Capture Media` | `Ingest.py` points Capture Manager's converted footage here |
| `paths.unreal_exports` | `Unreal Exports` | `export_animations.py` writes a folder per take here |
| `paths.metahuman_export` | `MetaHuman Export` | `export_metahuman.py` writes `<blueprint>/` here |
| `paths.metahuman` | `MetaHuman` | the Blender MetaHuman files (phase 2) |
| `paths.character_blend` | `MetaHuman/MetaHuman_character.blend` | `build_metahuman_rig.py` saves the character here |
| `paths.neutral_pose` | `MetaHuman/neutral_pose.json` | written by `save_neutral_pose.py`, read by `metahuman_procedural_fixes.py` |
| `paths.reference_template` | `MetaHuman/Xsens_reference.fbx` | any Xsens MVN FBX: `export_reference_fbx.py` takes the Reference skeleton from it (you supply this file) |
| `paths.takes` | `.` (the workspace) | `import_unreal_take.py` files each take as `<take>/<take>.blend` here |
| `paths.storage`, `paths.logs` | as in the layout | pose libraries; logs |
| `unreal.project` | empty | your `.uproject`, for the record |
| `unreal.wip_folder` | `/Game/WIP` | the Content folder the takes are exported from, one sub-folder per take |
| `unreal.completed_folder` | `/Game/Completed` | exported takes are moved here |
| `unreal.character_folder` | `/Game/MetaHumans` | where the MetaHuman blueprint and Character assets are kept |
| `unreal.metahuman_blueprint` | empty | the MetaHuman the takes are put on; empty = the one in the character folder |
| `unreal.identity` | empty | a MetaHuman Identity for every take; empty = none, each take solved from its video alone (for clips of different people) |
| `unreal.identities` | `{}` | `{text in a take's name: Identity}` for performers who have one, e.g. `{"SGM": "/Game/Identities/MHI_SGM"}` |
| `unreal.output_path` | `/Game/PCap` | where processed performances and animation sequences go |
| `unreal.required_plugins` | MetaHuman, Performance Capture … (12) | plugins checked by `check_plugins.py`, by internal name |
| `video.action` | `move` | what happens to a take's video: `move`, `copy`, or `""` (leave it) |
| `video.extra_folders` | `[]` | more folders to look for videos in (`import_unreal_take.py` looks in the take's export folder, then `video_source`, `video_holding` and these) |
| `video.slate_drop_words` | `[]` | words `Ingest.py` leaves out of take names, e.g. `["SGM"]` |
| `tools.*` | empty | optional program locations |

An example with every key is in [`config/pipeline_config.example.json`](config/pipeline_config.example.json). Keys missing from your file fall back to the defaults, so an older config keeps working after an update. A setting written at the top of a script (left as `None` there by default) wins over the config.

---

## Phase 1: Unreal Engine

Run each script in the Unreal Editor with **Tools ▸ Execute Python Script** (**File ▸ Execute Python Script** in older versions), from the workspace's `Scripts` folder.

1. `check_plugins.py` – checks the MetaHuman and Performance Capture plugins in `unreal.required_plugins` are installed and enabled. (`list_plugins.py` lists everything enabled, with internal names.)
2. Put the labelled videos in `Videos/Labelled` and run `Ingest.py`: each new video becomes a take folder, `/Game/WIP/<take>`, with its Capture Data and MetaHuman Performance.
3. Run `Process.py`: it solves the Performances one after the other (several minutes each) and exports each take's Animation Sequence into its folder. No MetaHuman Identity is needed: a mono video take is solved from the video alone, which is what clips of different people need. For a performer who has one, `unreal.identities` gives it to their takes by a word in the take's name (or `unreal.identity` to every take).
4. `export_metahuman.py` – once per character: the MetaHuman's meshes, textures, hair cards and head DNA, to `MetaHuman Export/<blueprint>/`.
5. `export_animations.py` – the takes: for each take folder under `/Game/WIP`, the body animation (FBX + curves), the face on the face board (it runs `export_face_board.py`) and the take's video, to `Unreal Exports/<take>/`. Takes exported completely are moved to `/Game/Completed`, and any MetaHuman Character asset in a take folder is moved to `/Game/MetaHumans` first (`move_metahuman_character.py` does that on its own).

Each script writes a report to the Output Log and shows a summary when it finishes. See [`scripts/unreal/README.md`](scripts/unreal/README.md) for every script and its settings.

---

## Phase 2: the MetaHuman in Blender

Run each script in Blender's **Scripting** workspace: **Text ▸ Open…** the script from the workspace's `Scripts` folder, then **Run Script** (▶). The scripts find `pipeline_config.json` above the open .blend, above the script, or through the workspace `create_workspace.py` noted on this computer; a setting written at the top of a script (`None` there by default) wins.

Add-ons: Animation Layers (procedural fixes), and the Character DNA add-on (Pro or standard) for a face that runs RigLogic and follows the face board. `build_metahuman_rig.py` checks for it first: without it, it warns (console and a popup) and builds the body rig, textures and hair with the plain FBX face; `REQUIRE_DNA_ADDON = True` makes it stop instead. `import_unreal_take.py` checks the same way: without the add-on the take is still imported (the face board keyed from the exported numbers, the expression curves on the shape keys), but the face doesn't move in Blender. `metahuman_procedural_fixes.py` needs Animation Layers: without it, it says so (console and a popup, and whether the add-on is installed but not ticked) and changes nothing.

| Step | Where | Script | Result |
|---|---|---|---|
| 1. Build the character (once per MetaHuman) | an empty .blend | `build_metahuman_rig.py` | `MetaHuman/MetaHuman_character.blend`, from `MetaHuman Export/<blueprint>/` (the one in `unreal.metahuman_blueprint`, else the newest): the MetaHuman with a control rig, `MH_rig`, its skin from Unreal's textures and its hair cards (`apply_unreal_materials.py`, which also runs on its own), lights and the Metahuman Rig tab |
| 2. Save a neutral pose (once) | the character file | `save_neutral_pose.py` | `MetaHuman/neutral_pose.json`: pose `MH_rig` the way a sign starts and ends, then run it |
| 3. Import a take (once per take) | the character file | `import_unreal_take.py` | the next take in `Unreal Exports` keyed on `MH_rig`, retimed to its video, saved as `<workspace>/<take>/<take>.blend`; the take's exported files and video move in with it. Run it again for the next take |
| 4. Procedural fixes | the take file | `metahuman_procedural_fixes.py` | `<take> Working.blend`. In its dialog, **Smooth Legs** (frames, default 4) takes the capture's jitter out of the hips and legs; **Leg Pin** (on by default, every 1 frame) holds them in their frame-1 pose for the whole take - untick it to keep the captured hips and legs, or set a larger number to pin them only every N frames; **Plant feet** holds the IK feet |
| 5. Clean up by hand | `<take> Working.blend` | the Metahuman Rig tab | trims, rest hand, Pose Magic … |
| 6. Reference FBX | the take file | `export_reference_fbx.py` | `<stamp> Reference.fbx` in the take's folder: the take on an Xsens 'Reference' skeleton, for tools set up for Xsens motion (fixes flipped rotation keys first, `FIX_FLIPS`) |

**Poses from other rigs.** `convert_poses_to_metahuman.py`, run in the character file, turns pose assets made on another rig into pose assets for `MH_rig`. It puts each pose on the rig it was made on (brought in for the run, so its own controls and IK pose the body), turns `MH_rig`'s FK controls until the MetaHuman's bones point the same way (rest-pose and bone-axis differences taken out), and saves a pose asset of the same name, keeping the source pose's thumbnail (`THUMBNAILS = "NEW"` draws new ones of the MetaHuman instead). Any rig whose bones carry the MetaHuman's names (`pelvis`, `spine_01`, `upperarm_l` …) works, in A-pose or T-pose: set `SOURCE` (the pose library), `SOURCE_RIG_FILE` (the .blend with the rig they were made on) and `SOURCE_RIG` (that rig's object name) at the top of the script. The poses are saved in `Storage/MH_rig`, which is added to the Asset Browser as the library `MH_rig`. Poses already converted are skipped; `MAX_POSES = 5` makes a trial run. The poses are in FK: after applying one, Match IK to FK puts the IK controls on it.

**Sharing the poses.** A pose library of one file per pose is large (about 400 KB a pose, nearly all of it the overhead every .blend file carries). `pack_pose_library.py` packs `Storage/MH_rig` into compressed files, one per sign (`SG ASL Dancer 2025-12-27.blend` holds its P1, P2 …), and zips them as `Pose Packs/MH_rig poses.zip` — on test poses a fourteenth of the size, names, thumbnails and asset details kept. Run it in Blender's Text Editor, or for a big library without the window: `blender --background --python pack_pose_library.py`. Share the zip as a file of a GitHub Release (it is kept out of the repo by `.gitignore`). Whoever downloads it installs it with `python create_workspace.py "<workspace>" --poses "MH_rig poses.zip"` (or by putting the zip next to `create_workspace.py`): the poses go into `Storage/MH_rig`, files already there are left alone, and a workspace with a pose library of its own there isn't touched. `build_metahuman_rig.py` adds `Storage/MH_rig` to the Asset Browser's libraries.

`export_reference_fbx.py` needs an Xsens MVN FBX at `MetaHuman/Xsens_reference.fbx` (any Xsens export; only its skeleton is used). Put one there, or point `paths.reference_template` at one.

**The control rig.** `MH_rig` is the MetaHuman's own skeleton with controls added; the MetaHuman bones keep deforming the mesh. The controls: `hip`, `chest_1`..`chest_5`, `neckfk_01`, `neckfk`, `headfk`, `collar_L`, `bicepfk_L`, `forearmfk_L`, `handfk_L`, `<finger>fk_metacarpal_L`, `<finger>fk_01_L`..`_03_L`, `thighfk_L`, `shinfk_L`, `footfk_L`, `toefk_L` (and `_R`). IK: `handik_L` with `elbow_target_L`, `footik_L` with `knee_target_L`. The `FKIK Switch` bone holds `ArmIKFK_L/R` and `LegIKFK_L/R` (0 = FK, 1 = IK). Fingers and toes follow the hand and foot in FK and IK. `master` moves everything.

**Metahuman Rig tab.** `metahuman_rig_panel.py` adds a "Metahuman Rig" tab to the 3D Viewport sidebar (N): the four FK/IK switches, Match IK to FK and Match FK to IK per limb or for all limbs (on the current frame, the scene range or a set range, keyed), and buttons to show the bone collections. `build_metahuman_rig.py` puts it in the character file, registered, so every take filed from that file has it.

The tab's **Cleanup** box: **Trim Start** (the frame you choose becomes frame 0; everything moves earlier, and frame 1 eases from the neutral pose by frame 20), **Trim End** (the frame you choose becomes the last; the take eases into the neutral pose over the last 20 frames) - both on every animation layer, the face board and the shape keys alike - and **Rest Hand** (one hand and its fingers held in their frame-1 pose over a range, for one-handed signs). Each writes a line to `<take> Framelog.txt`. **Video** box: **Import Video** puts the take's video (from the take's folder; the folder button, or Shift-click, chooses another) on a plane behind the character, or to its left or right (the **Left / Right / Behind** buttons, Behind by default; changing them moves the plane), as tall as it, playing in step with the timeline at the video's own speed; **Starts at** moves it along the timeline (after a Trim Start, say), and the plane can be hidden or removed. **Pose Magic** makes a pose asset (and Pose Manager JSON, CSV and TXT files) for every timeline marker, reading arms and legs on IK as the IK shows them; it runs `pose_magic_lib.py` from `<workspace>\Scripts`. Thumbnails are drawn through *SGM Pose Camera*, which Pose Magic places in front of the character so the whole body (or, if you choose, the hips up) fits in every marked pose; everything else is hidden while they are drawn, then your own scene camera is put back. With **Face board poses** on (the default), each marker also gives a face pose, `<name> Face P1` ..., holding every face board control, in the same library; its thumbnail is a picture of the face on that frame from in front, or from the face's left or right side.

**Procedural fixes** on the MetaHuman: layers, rest on frame 0, the neutral pose on frame 1 and the last frame, leg and hip pin, torso hold, then the IK hands, feet, elbow and knee targets are put on the FK limbs on every frame and the limbs are switched to IK. It needs the Animation Layers add-on.

**In IK, the forearm's own roll comes from the IK plane.** Hands and feet keep their exact orientation; any forearm roll in the capture moves into the wrist.

**The face in Blender** needs RigLogic, which an FBX doesn't carry: export the head DNA from MetaHuman Creator, import it into the character file with the MetaHuman DNA add-on, and run `build_metahuman_rig.py` again. It moves and scales the DNA head onto the body's head, attaches it (through helper bones when its bone axes differ), and hides the FBX face. The import script then keys the add-on's `*_face_gui` face board, and RigLogic moves the face.

---

## The pose library

A library of poses for the MetaHuman rig comes as a separate download: **`MH_rig poses.zip`** on this project's [Releases](../../releases) page (one compressed .blend per sign, with each pose's thumbnail). To install it, put the zip next to `create_workspace.py` and run that again, or give it by name:

```bash
python create_workspace.py "D:/Sign Workspace" --poses "MH_rig poses.zip"
```

The poses go into `Storage/MH_rig` in your workspace, and `build_metahuman_rig.py` adds that folder to Blender's Asset Browser as the library `MH_rig`. Poses already there are left alone, so a newer pack only adds what is new. In a take file, apply a pose from the Asset Browser to `MH_rig`; the poses are in FK, and **Match IK to FK** in the Metahuman Rig tab puts the IK controls on them.

The poses are CC0.

## Using the settings in your own scripts

```python
import pipeline_config as pc

cfg = pc.load_config(__file__)              # Blender: pc.load_config(bpy.data.filepath)
videos = pc.project_path(cfg, "video_source")
wip = pc.get(cfg, "unreal.wip_folder")
```

To point every script at a settings file somewhere else, set the environment variable `SIGN_PIPELINE_CONFIG` to its full path.

---

## Troubleshooting

| Message | Fix |
|---|---|
| A script writes to `<project>/Saved/...` (Unreal) or says *No pipeline_config.json found* (Blender) | Run `create_workspace.py` on this computer, and run the scripts from the workspace's `Scripts` folder (or keep the .blend inside the workspace, or set `SIGN_PIPELINE_CONFIG` to the config's path). |
| `Ingest.py`: *Capture Manager's media folder … has no {tokens}* | Reset Editor Preferences ▸ Plugins ▸ Capture Manager ▸ Media Directory (the reset arrow), or restart Unreal. |
| `export_animations.py`: a take *stays* in `/Game/WIP` | Unreal asked *Continue with rename?* and it was cancelled: run the script again and choose **Yes**. |
| A popup: *Character DNA add-on missing* / *Animation Layers add-on missing* | Enable the add-on in Edit ▸ Preferences ▸ Add-ons and run the script again. |
| Fingers or limbs spin round in a frame or two | **Fix Flips** in the Metahuman Rig tab; `check_finger_flips.py` reports any left. |
| `export_reference_fbx.py`: *No Xsens template FBX* | Put any Xsens MVN FBX at `MetaHuman/Xsens_reference.fbx`. |

More, with what each step should print: [docs/GETTING_STARTED.md](docs/GETTING_STARTED.md#when-something-doesnt-work).
