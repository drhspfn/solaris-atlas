# Manual selection of facial animation in Blender 4.5

The generated `.blend` stores facial Actions and their slots, not the Python
function `select_facial_action`. The following uses only Blender's own Python
console. Nothing runs when the file opens and no repository is needed.

1. Open a copy of the `.blend`, switch an area to **Python Console**, and list
   available facial actions:

   ```python
   [(a.name, a.get('solaris_facial_clip'), a.get('solaris_body_clip')) for a in bpy.data.actions if a.get('solaris_facial_clip') is not None]
   ```

2. Pick an action name from that list and replace the example below. Run each
   line in the console. This assigns the correct slot to every Shape Keys
   datablock addressed by the action. It leaves the body action untouched.

   ```python
   a = bpy.data.actions['Face: Action01']
   exec("for s in a.slots:\n k = bpy.data.shape_keys[s.name_display]\n k.animation_data_create()\n k.animation_data.action = a\n k.animation_data.action_slot = s")
   ```

3. For a body-linked action, select the body action named in the list's third
   column on the armature in Blender's Action Editor. For a standalone
   expression the third column is `None`; keep whichever body action you want.
   Frame **0** is time zero for the exported clips. Play the timeline.

Repeat step 2 to select a different face action. If an older action has slots
for Shape Keys that the new one does not address, clear those old assignments
in the Action Editor; likewise reset individual morph values if necessary.
The console commands do not automatically select a body action, loop clips,
or reset other shape key values. Save a new file if you want to keep the choice.
