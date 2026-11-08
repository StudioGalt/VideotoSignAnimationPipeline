# VideotoSignAnimationPipeline

Setup Phase
1.	Download git repo in desired folder (git clone, or Code > Download ZIP and unzip it)
2.	Open a command prompt in that folder
3.	Find “create_workspace.py” then run “python create_workspace.py "D:/Sign Workspace" --yes”
4.	Create Unreal engine project
    a.	Recommended setting scaleable and third person
5.	Install the following addons
    a.	MetaHuman
    b.	MetaHumanBodyTracker
    c.	MetaHumanCalibrationProcessing
    d.	MetaHumanCharacter
    e.	MetaHumanCoreTech
    f.	MetaHumanDepthProcessing
    g.	MetaHumanGenerator
    h.	MetaHumanLiveLink
    i.	PerformanceCaptureCore
    j.  PerformanceCaptureToolkit
    k.	PerformanceCaptureWorkflow
    l.	You can check_plugins by running ‘py "D:\Sign Workspace\Scripts\check_plugins.py"’
6.	Create a Metahuman Character
    a.	I always use default
    b.	Customize as much as you like
    c.	Only customization I do, is setting clothing to black to aid in contrast.
    d.	Download textures
    e.	Create fullrig
    f.	Under assembly update name if you want
    g.	Assemble
    h.	Save
Unreal Phase
1.	Download files
    a.	I use pexel, ensure you check the rights for the file
    b.	Download into unlabelled folder
    c.	Label them, they can be whatever, but I use Customer, Language. Sign, (IE SG ASL “Hello”)
    d.	Put them into the labelled folder. The next process will take all of them from labelled, so if you don’t want to do that many keep them in holding.
2.  Run Ingest.py
    a.	Can take a while 18 minutes for 4
    b.	And sometime fails to get facial animation or body animation
3.	Run Process.py
    a.	Solves each take, several minutes each
4.	Run export_metahuman.py
5.	Run export_animations.py
Blender/Cleanup Phase
1.	In a new blender file delete default cube, run “build_metahuman_rig.py”
2.	Run import_unreal_take.py
3.	Run metahuman_procedural_fixes.py
4.  Export and it can be used where ever
    a. If you want to get it back into Unreal use import_blender_takes.py

