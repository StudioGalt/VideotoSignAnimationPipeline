# Getting started: from labelled videos to a cleaned-up take in Blender

This walks through the whole pipeline once, from a new Unreal project to a take on the MetaHuman in Blender. Each step says what to run and what you should see. The [README](../README.md) has the reference for every script and setting.

What you need:

- **Windows**, **Python 3.8+** (only to run `create_workspace.py`; nothing to install with it)
- **Unreal Engine 5.8** with the **MetaHuman** plugins and the **Performance Capture Toolkit** (from Fab)
- **Blender 5.x** with the **Animation Layers** add-on (by Tal). Optional: the **Character DNA** add-on (Pro or standard), for a face that runs RigLogic in Blender
- Videos of signing, **one sign per video, named after the sign**: `Hello.mp4`, `Thank You 2026-10-15.mp4` (a date in the name is left out of the take's name)

---

## 1. Get the pipeline and make a workspace

Download this repository (**Code ▸ Download ZIP**, then unzip it, or `git clone`). In a command prompt, in the repository's folder:

```bash
python create_workspace.py "D:/Sign Workspace" --yes
```

(Any folder will do. Leave out `--yes` to be asked for each setting; Enter takes the default.)

You should see the folders it made, `Settings: D:\Sign Workspace\pipeline_config.json`, how many scripts were copied into `Scripts`, and the next steps. The workspace is where everything happens from now on: the scripts are run from its `Scripts` folder, and they find their settings by themselves.

If you have the pose libraries (`Body_Poses_Basic poses.zip` and `Body_Poses_Advanced poses.zip`, from the Releases page), put them next to `create_workspace.py` and run the command again: the poses go into `Storage\Body_Poses_Basic` and `Storage\Body_Poses_Advanced`.

---

## 2. Set up the Unreal project

1. Create a project (any template; **Blank** is enough) and open it.
2. **Edit ▸ Plugins**: turn on **Python Editor Script Plugin**, the **MetaHuman** plugins (MetaHuman Creator, MetaHuman Animator and their Capture Manager), and the **Performance Capture** plugins from the Performance Capture Toolkit. Restart when Unreal asks.
3. Open the **Output Log** (**Window ▸ Output Log**): every script reports there.
4. Run the plugin check: **Tools ▸ Execute Python Script…** (in some versions **File ▸ Execute Python Script…**), and pick `D:\Sign Workspace\Scripts\check_plugins.py`. It lists each plugin the pipeline needs as enabled or missing, and a box sums it up. Turn on anything missing and restart.
5. **Make the MetaHuman the takes are put on.** Create a MetaHuman Character in MetaHuman Creator (Content Browser ▸ **Add**, under MetaHuman), shape it, and assemble it, so that its blueprint (`BP_<name>`) is in `/Game/MetaHumans`. Any MetaHuman works: the takes are solved from the videos alone, so it doesn't have to look like the people signing.

Every Python script in the steps below is run the same way: **Tools ▸ Execute Python Script…** and the file from `Scripts`.

---

## 3. Phase 1: the videos into animation (Unreal)

1. **Put the videos in** `D:\Sign Workspace\Videos\Labelled`.
2. **Run `Ingest.py`.** Each new video becomes a take: a folder `/Game/WIP/<take>` with its Capture Data (`CD_…`) and a MetaHuman Performance (`MHP_<take>`). `Hello.mp4` becomes `Hello_tk1`; a second video of the same sign becomes `Hello_tk2`. The Output Log lists each take; running it again only does new videos.
3. **Run `Process.py`.** It solves the Performances with MetaHuman Animator, one after the other, several minutes each, and exports each take's animation (`AS_<take>`) into its folder. The editor stays usable meanwhile: progress is in the Output Log (`[solving]`, `[solved]`), and a box says when the batch is done. No MetaHuman Identity is needed.
4. **Run `export_metahuman.py`** (once per MetaHuman). It writes the MetaHuman's body, face and clothing as FBX, its textures, its hair cards and its head DNA to `D:\Sign Workspace\MetaHuman Export\BP_<name>`. The last line reads `exported to … in N s`.
5. **Run `export_animations.py`.** For each take it writes the body animation, the face, and moves the take's video along, into `D:\Sign Workspace\Unreal Exports\<take>`. Takes that are fully exported are moved to `/Game/Completed`.
   - If Unreal asks *"Continue with rename?"* while moving a take, choose **Yes**.

Lines you can ignore in the Output Log: *Track with name root already exists*, *Could not find Skeleton. Head Movement export will be disabled* (many times), and NNE *Input shape* messages.

---

## 4. Phase 2: the MetaHuman in Blender

Every Blender script below is run from the **Scripting** workspace: **Text ▸ Open…** the script from `D:\Sign Workspace\Scripts`, then **Run Script** (▶). The console (**Window ▸ Toggle System Console**) shows what each one did.

1. **Build the character.** In a new, empty file, run `build_metahuman_rig.py`. It builds the MetaHuman with a control rig (`MH_rig`), its skin and hair, lights, and the **Metahuman Rig** tab in the 3D Viewport's sidebar (**N**), and saves `D:\Sign Workspace\MetaHuman\MetaHuman_character.blend`. It warns if the Character DNA add-on is off (the face then doesn't follow the face board, but everything else is built).
2. **The neutral pose** (the pose every take starts and ends in): a default one is already in `MetaHuman\neutral_pose.json`. For your own, pose `MH_rig` (in the character file or a take) and press **Set Neutral Pose** in the Metahuman Rig tab's Cleanup box, or run `save_neutral_pose.py`. The old pose is kept as `neutral_pose <date> <time>.json`; takes done before keep theirs.
3. **Import a take.** In the character file, run `import_unreal_take.py`. With more than one take waiting in `Unreal Exports`, a list opens: type to search it and click the take to import. It keys it onto `MH_rig` at 60 fps, timed to its video, and saves it as `D:\Sign Workspace\<take>\<take>.blend` (the take's exported files and video move in with it). Run it again, from the character file, for each further take.
4. **Procedural fixes.** Open the take's `.blend`, run `metahuman_procedural_fixes.py`, and press **OK** in its dialog. It sets up the animation layers, eases the take out of and back into the neutral pose, holds the hips and legs still (**Leg Pin**), switches arms and legs to IK, and saves `<take> Working.blend`.
5. **Clean up by hand** in `<take> Working.blend`, with the **Metahuman Rig** tab:
   - **Import Video** puts the take's video behind the character, playing in step with the timeline, to compare against.
   - **Trim Start / Trim End** cut the take, and the reference video with it; **Save Trimmed Video** (Video box) writes the cut video as `<video> Trimmed.mp4`. **Rest Hand** holds one hand still for one-handed signs.
   - Poses from the Asset Browser (libraries `Body_Poses_Basic` and `Body_Poses_Advanced`) apply to the rig; **Match IK to FK** puts the IK controls on them.
   - **Fix Flips** if a finger or limb spins round in a frame or two.
   - **Rename File** saves the take under its archive name: `<Product> <Language> <Name> <Date> Unedited.blend`, in a folder of that name.
   - **Finish** box, when the cleanup is done:
     - **Bake Layers** puts every animation layer into one action per object, keyed on every frame, frame 0's rest pose included. The layers are kept, muted; Ctrl+Z undoes it.
     - **Export FBX** writes `<name>.fbx` (the body skeleton), `<name> Face.fbx` (the face skeleton), `<name> Face Board.json` (the face board's controls) and `<name> Export.json` (what was exported, for the way back into Unreal) next to the `.blend`, at Unreal's size. **Name** is filled in as `<take> MetaHuman`; change it as you like. **Body** and **Face** (both ticked) choose which files are written; tick **Meshes** to include the meshes.
     - **Render** writes `<take> Render.mp4` next to the `.blend`, through the scene camera or a camera it frames on the character (upper body or whole body). It renders the scene's range; untick **Use scene range** to set **From** and **To** (the file is then `<take> Render <from>-<to>.mp4`). Blender's render window shows it going; Esc stops it.
6. **Reference FBX** (optional, for tools that take Xsens motion): put any Xsens MVN FBX at `D:\Sign Workspace\MetaHuman\Xsens_reference.fbx` (only its skeleton is used), then run `export_reference_fbx.py` in the take file. It writes `<take> Reference.fbx` into the take's folder.

That's the pipeline: each take is now a cleaned-up animation on the MetaHuman, in its own folder of the workspace.

---

## 5. Back into Unreal

1. In each take file, press **Export FBX** (Metahuman Rig tab ▸ Finish).
2. In Unreal, put the MetaHuman in the level (drag `BP_<name>` from `/Game/MetaHumans` into the viewport), if you want the takes on it.
3. Run **`import_blender_takes.py`** (Tools ▸ Execute Python Script…, from `Scripts`). A window lists every take exported from Blender, newest first:
   - type in **Search** to narrow the list; click a take, or Ctrl-click / Shift-click several (**Pick all shown** picks every take the search shows);
   - **Import body**: the body animation, as `AS_<take>_Body` on the MetaHuman's body skeleton;
   - **Import face**: the face board, keyed onto the MetaHuman's face control board, so RigLogic moves the face as it did in Blender (without a MetaHuman, the face skeleton's FBX is imported instead);
   - **Add to MetaHuman**: pick the MetaHuman from the list of the ones in the level. Each take gets a Level Sequence, `LS_<take>`, with the MetaHuman playing it.
4. Press **Import**. Everything goes into `/Game/Finished/<take>/`; the last Level Sequence opens in Sequencer. A take imported again is replaced. The Output Log (and `Logs\import_blender_takes_report.txt`) says what was done for each take.

---

## When something doesn't work

The console or Output Log almost always says what to do. The common cases:

| What you see | What to do |
|---|---|
| A script writes to `<project>\Saved\…` instead of the workspace | It didn't find the settings: run `create_workspace.py` again (it notes the workspace on this computer), and run the scripts from the workspace's `Scripts` folder. |
| `Ingest.py`: *Capture Manager's media folder … has no {tokens}* | Reset it: Editor Preferences ▸ Plugins ▸ Capture Manager ▸ Media Directory, the reset arrow (or restart Unreal), then run `Ingest.py` again. |
| `export_animations.py`: a take *stays* in WIP | The Output Log line above says why; usually the *Continue with rename?* box was cancelled. Run it again and choose **Yes**. |
| `build_metahuman_rig.py`: *No *_body.fbx in …* | Run `export_metahuman.py` in Unreal first. |
| `import_unreal_take.py`: *No source video for …* | The take's video wasn't moved along: put it in the take's folder in `Unreal Exports`, or set `VIDEO_OVERRIDE` at the top of the script. |
| A popup: *Character DNA add-on missing* / *Animation Layers add-on missing* | Install / enable the add-on (Edit ▸ Preferences ▸ Add-ons) and run the script again. |
| The hips and legs jitter | Keep **Leg Pin** on (the default); **Smooth Legs** smooths them when the pin is off. |
| Fingers spin round in a frame or two | **Fix Flips** in the Metahuman Rig tab (the Reference export does it by itself). `check_finger_flips.py` lists any left. |
| `export_reference_fbx.py`: *No Xsens template FBX* | Put an Xsens MVN FBX at `MetaHuman\Xsens_reference.fbx`. |
