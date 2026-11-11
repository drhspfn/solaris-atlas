# Retained local animation pack recovery (2026-11-10)

`scripts/resume_retained_animation_packs.py` is narrowly pinned to the Hsin and Suisui retained root packs and the exact importer and tools used by their interrupted runs. It verifies the full saved plan and GLB digests, manifest, clip identities and names, and importer/tool hashes. During a resumed build it independently compares the fresh root model hash, skeleton, rest rig and ordered animation identities before requiring complete reuse. It rebuilds the other forms and components normally; it does not reuse retained extraction or incomplete components.

Run inside the locally built `solaris-atlas-models:hsin-suisui-resume-20261111` image, based on the original image and containing the component and fan fixes. Its exact importer and tool hashes are pinned in the script; the new plan is also checked against every saved clip and the saved rig. Mount the retained volume read-only. The default invocation only checks; it does not need game files and does not start an import. From PowerShell in this worktree:

```powershell
$script = (Resolve-Path scripts/resume_retained_animation_packs.py).Path.Replace('\','/')
$base = @('run','--rm','--user','0','--network','none','--mount','type=volume,source=solaris-model-local-20261110,target=/saved,readonly','--mount',"type=bind,source=$script,target=/resume.py,readonly",'--entrypoint','python','solaris-atlas-models:hsin-suisui-resume-20261111','/resume.py')
docker @base hsin --retained /saved/hsin/.solaris-model-x3i9vp4y
docker @base suisui --retained /saved/suisui/.solaris-model-uwwulgp_
```

To actually resume, after both checks pass, use a separate writable output directory with sufficient space and mount the game, keys, and recipes read-only. For example, create `E:/Caches/solaris-model-lab/resumed` first, then run the following for each character (change `$name` and `$retained` for Suisui):

```powershell
$name = 'hsin'
$retained = '/saved/hsin/.solaris-model-x3i9vp4y'
$script = (Resolve-Path scripts/resume_retained_animation_packs.py).Path.Replace('\','/')
docker run --rm --user 0 --network none --memory 14g --memory-swap 14g --cpus 6 --pids-limit 512 `
  --mount 'type=volume,source=solaris-model-local-20261110,target=/saved,readonly' `
  --mount "type=bind,source=$script,target=/resume.py,readonly" `
  --mount 'type=bind,source=E:/Games/Wuthering Waves/Wuthering Waves Game/Client,target=/game,readonly' `
  --mount 'type=bind,source=E:/Caches/solaris-model-lab/keys.txt,target=/keys.txt,readonly' `
  --mount 'type=bind,source=E:/Caches/solaris-model-lab/publishable-20261110,target=/recipes,readonly' `
  --mount 'type=bind,source=E:/Caches/solaris-model-lab/resumed,target=/output' `
  --entrypoint python solaris-atlas-models:hsin-suisui-resume-20261111 `
  /resume.py $name --retained $retained --run --recipe "/recipes/$name-recipe.json" `
  --game-dir /game --keys-file /keys.txt --output "/output/$name-resumed.zip" --asset-version 3.7.2
```

For Suisui set `$name = 'suisui'` and `$retained = '/saved/suisui/.solaris-model-uwwulgp_'`. The output must not exist. Scratch is retained on failure and removed after success. The final archive is manifest- and CRC-checked and atomically linked without overwriting an existing file. A successful check alone cannot guarantee a later build: changed client assets, recipe, rig, or animation sources abort the run when the fresh root plan is checked. The run requires a filesystem supporting hard links in the output directory.
