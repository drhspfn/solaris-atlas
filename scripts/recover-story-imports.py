#!/usr/bin/env python3
"""Run ON THE DOCKER HOST after deployment; dry-run unless --apply.

Example (do not run before final tests/deploy):
  python3 scripts/recover-story-imports.py --first 1.0.0 --last 3.7.0 \
    --expected-tag FULL_40_CHARACTER_SHA --state-dir /durable/story-recovery \
    --pilot-run-id REVIEWED_M0334_MEDIA_RUN \
    --refresh-quest-node-id 4688653 --refresh-quest-node-id 5389571 --apply

Default: enqueue at most ONE imported release, then exit. Repeat the same
command to resume the next release, or explicitly set --max-releases. For an
independent run use nohup ... > /durable/story-recovery/runner.log 2>&1 < /dev/null &.
No queue consumption/replay, worker creation, deployment, or job cancellation.
Deployment is checked by actual container image ID against each repository's
locally pulled :FULL_SHA image, so an identical :main alias is accepted.

The pilot media run must already exist: an exact image-only MediaRequest for
flow_state:剧情_3_1_拉海洛主线_上半_3_28_1 in release 3.1.0. This runner does not
create a second pilot. It verifies all four ZIP fragments and separately
published MP4s on every invocation, including resumed journal checkpoints.
The two reviewed quest node IDs above are the default targeted CURRENT read
refresh, independent of fragment availability or reused tree-order tasks. This
does not reindex all quests or historical snapshots. Override the repeated
--refresh-quest-node-id only with reviewed graph quest IDs; use a new state-dir
when changing that scope. Apply queues these dirty marks before the media gate,
so a failed/incomplete pilot cannot leave the new encounter UI hidden in cache.
An interrupted/failed WRITE step requires operator reconciliation: check the
recorded host PID, backend state and log, then archive journal.json to a new
filename before resuming. Existing services remain the idempotency authority.
"""

import argparse
import json
import math
import os
import re
import shutil
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

PILOT_STATE = "剧情_3_1_拉海洛主线_上半_3_28_1"
SHOW_TALK = "wuwa_story.ingestion.show_talk_sequence_backfill"
STORY_REPAIR = "wuwa_story.ingestion.story_repair"
REVIEWED_QUEST_NODES = (4688653, 5389571)

QUEST_REFRESH = r'''
import asyncio,json,sys
from sqlalchemy import select,text
from wuwa_story.db.models.core import Quest
from wuwa_story.db.session import SessionFactory,engine
from wuwa_story.read_models.refresh import mark_dirty
async def main():
 ids=json.loads(sys.argv[1]);apply=sys.argv[2]=='apply'
 assert ids and len(ids)<=100 and all(type(i)is int and i>0 for i in ids),'invalid explicit quest scope'
 async with SessionFactory() as s:
  if not apply:await s.execute(text('SET TRANSACTION READ ONLY'))
  await s.execute(text("SET LOCAL statement_timeout='8s'"))
  await s.execute(text("SET LOCAL lock_timeout='2s'"))
  found=sorted(set(await s.scalars(select(Quest.node_id).where(Quest.node_id.in_(ids)))))
  assert found==sorted(set(ids)),'explicit quest IDs are missing or not graph quest roots'
  if apply:
   await mark_dirty(s,'quest',found,'current')
   await s.commit()
  print(json.dumps({'quest_node_ids':found,'snapshot_key':'current','marked':len(found) if apply else 0,'dry_run':not apply}),flush=True)
async def run():
 try:await asyncio.wait_for(main(),timeout=30)
 finally:await engine.dispose()
asyncio.run(run())
'''

# Only discovery and pilot inspection run here. Database access is READ ONLY.
INSPECT = r'''
import asyncio,hashlib,json,sys,tempfile,zipfile
from pathlib import Path
from sqlalchemy import select,text
from wuwa_story.config.settings import get_settings
from wuwa_story.db.session import SessionFactory,engine
from wuwa_story.db.models.ops import GameRelease,ImportRun,ProcessingRun
from wuwa_story.db.models.graph import Node
from wuwa_story.db.models.storage import FileReference,FileLocation
from wuwa_story.storage.s3 import S3Storage
def verify_bundle(archive,metadata,state):
 def document(name,expected=None):
  assert archive.getinfo(name).file_size<=8*1024**2,'oversized JSON entry'
  data=archive.read(name)
  if expected:
   assert len(data)==expected['size_bytes'] and hashlib.sha256(data).hexdigest()==expected['sha256'],'JSON entry checksum mismatch'
  return json.loads(data)
 manifest=document('manifest.json')
 presentation=document(manifest['presentation']['file'],manifest['files'][manifest['presentation']['file']])
 assert presentation['source']['StateKey']==state,'wrong pilot source state'
 assert any(c.get('asset')=='/Game/Aki/Sequence/LevelA_Seq/Main/03_1/M0334/DA_M0334.DA_M0334'
  for c in presentation['cues'] if c.get('kind')=='sequence'),'wrong sequence pilot'
 assert manifest['presentation']['sha256']==metadata['game_source']['presentation_sha256'],'presentation hash differs'
 for key in ('asset_version','download_id'):
  assert manifest[key]==metadata['game_source'][key],'bundle client provenance differs'
 controls=document(manifest['controls']['file'],manifest['files'][manifest['controls']['file']])
 media=controls.get('media') or {}
 sections=[x for x in media.get('sections',[]) if x.get('kind')=='movie_fragment']
 assert sections and media.get('complete_video') is False,'pilot has no explicit movie fragments'
 expected_paths={'Client/Content/Aki/Movies/LevelA_Seq/03_1/'+name+'.mp4' for name in
  ('M0334_Cam003','M0334_Cam003d','M0334_Cam005_Nanzhu','M0334_Cam005_Nvzhu')}
 assert {section.get('archive_path') for section in sections}==expected_paths,'pilot must contain all four M0334 fragments'
 assert sum(manifest['files'][section['file']]['size_bytes'] for section in sections)<=256*1024**2,'oversized fragment payload'
 for section in sections:
  path=section.get('file','');assert section.get('status')=='extracted' and path=='media/'+section['archive_path'],'pilot fragments incomplete'
  expected=manifest['files'][path]
  assert archive.getinfo(path).file_size==expected['size_bytes']>0,'pilot fragment size mismatch'
  with archive.open(path) as stream:
   assert hashlib.file_digest(stream,'sha256').hexdigest()==expected['sha256'],'pilot fragment checksum mismatch'
 return len(expected_paths)
async def verify_published(storage,archive,metadata):
 manifest=json.loads(archive.read('manifest.json'))
 controls=json.loads(archive.read(manifest['controls']['file']))
 expected=[s for s in controls['media']['sections'] if s.get('kind')=='movie_fragment']
 media=metadata.get('sequence_media') or {}
 assert media.get('schema_version')==1 and media.get('complete_video') is False,'pilot fragments not published individually'
 assert media.get('game_source')==metadata['game_source'],'fragment provenance differs'
 sections=[s for s in media.get('sections',[]) if s.get('kind')=='movie_fragment']
 by_source={s.get('source'):s for s in sections}
 assert len(by_source)==len(sections)==len(expected),'published section coverage differs'
 parts=set()
 for original in expected:
  section=by_source.get(original['source'],{})
  assert all(section.get(k)==original.get(k) for k in
   ('source','reference','scene_source','clock','range','parameters','archive_path','file')),'published timing/source differs'
  proof=manifest['files'][original['file']];sha=proof['sha256']
  assert section.get('status')=='published' and type(section.get('file_id')) is int and section['file_id']>0,'published fragment identity missing'
  assert section.get('object_key')==f'objects/{sha[:2]}/{sha[2:4]}/{sha}' and section.get('sha256')==sha,'published fragment hash/key differs'
  assert section.get('size_bytes')==proof['size_bytes'] and section.get('mime_type')=='video/mp4','published fragment size/type differs'
  parts.add((section['file_id'],section['object_key'],section['size_bytes']))
 assert {(p['file_id'],p['object_key'],p['size_bytes']) for p in media.get('components',[])}==parts,'fragment components differ'
 for _,key,size in sorted(parts):
  info=await storage.stat(key)
  assert info is not None and info.size_bytes==size,'published fragment missing or truncated'
 return len(parts)
def verify_api_fragments(owner,metadata,visible):
 sections=[s for s in (metadata.get('sequence_media') or {}).get('sections',[]) if s.get('kind')=='movie_fragment']
 assert len(sections)==4,'pilot publication metadata lacks four fragments'
 media=visible.get(owner,{})
 assert media.get('game_source')==metadata['game_source'],'runtime encounter client/raw-flow hash differs'
 available={(s.get('file_id'),s.get('source')):s for s in media.get('sections',[])}
 assert all(available.get((s.get('file_id'),s.get('source')),{}).get('url')
  and available[(s['file_id'],s['source'])].get('object_key')==s.get('object_key')
  for s in sections),'pilot fragments are not API-visible for exact owner/release/bundle'
async def runtime_pilot(session,owner,release_id):
 from wuwa_story.db.models.core import Quest
 from wuwa_story.db.repositories.quest_scope import quests_for_state
 from wuwa_story.story.tree_order import authored_order
 from wuwa_story.story.runtime_service import sequence_playback
 roots=await quests_for_state(session,owner,release_id)
 assert 0<len(roots)<=10,'pilot quest ownership missing or exceeds bound'
 games=list(await session.scalars(select(Quest.game_quest_id).where(Quest.node_id.in_(roots))))
 visible={}
 for game in games:
  order=await authored_order(session,game,release_id)
  if order:
   result=await sequence_playback(session,order.get('blocks',[]),release_id)
   if owner in result:
    if owner in visible:assert visible[owner]==result[owner],'conflicting runtime encounter proofs'
    visible[owner]=result[owner]
 return visible
async def main():
 storage=S3Storage(get_settings())
 async with SessionFactory() as s:
  await s.execute(text('SET TRANSACTION READ ONLY'))
  await s.execute(text("SET LOCAL statement_timeout='8s'"))
  if sys.argv[1]=='releases':
   rows=await s.execute(select(GameRelease.id,GameRelease.game_version,GameRelease.sequence).where(
    select(ImportRun.id).where(ImportRun.release_id==GameRelease.id,ImportRun.status=='succeeded').exists()
   ).order_by(GameRelease.sequence))
   print(json.dumps([{'id':i,'version':v,'sequence':seq} for i,v,seq in rows]),flush=True);return
  run_id,release_id=int(sys.argv[2]),int(sys.argv[3]);state=sys.argv[4]
  r=await s.get(ProcessingRun,run_id)
  assert r is not None,'pilot run missing'
  run_status=r.status
  request=(r.metadata_json or {}).get('request',{})
  assert request.get('release_id')==release_id and request.get('game_version')=='3.1.0','wrong pilot release'
  assert request.get('entities')==['flow_state:'+state] and request.get('kinds')==['image'],'pilot scope differs'
  if r.status not in {'completed','partial'}:
   print(json.dumps({'ready':False,'status':r.status}),flush=True);return
  owner=await s.scalar(select(Node.id).where(Node.canonical_key=='flow_state:'+state))
  targets=(r.metadata_json or {}).get('payload',{}).get('targets',[])
  matches=[t for t in targets if t.get('owner')==owner and t.get('release_id')==release_id and t.get('kind')=='spine' and t.get('sequence_media_schema')==1]
  assert len(matches)==1,'pilot sequence target scope differs'
  target=matches[0]
  refs=list(await s.execute(select(FileReference.metadata_json,FileLocation.object_key,FileReference.file_id)
   .join(FileLocation,FileLocation.file_id==FileReference.file_id).where(
    FileReference.owner_node_id==owner,FileReference.release_id==release_id,
    FileReference.reference_type.in_(['story_presentation','story_spine']),
    FileReference.source_path==target['path'],FileReference.source_name==target.get('language'),
    FileReference.metadata_json['game_source']['sequence_media_schema'].as_integer()==1,
    FileReference.metadata_json['game_source']['download_id'].astext==request['download_id'],
    FileReference.metadata_json['game_source']['asset_version'].astext==request['asset_version'],
    FileReference.metadata_json['game_source']['presentation_sha256'].astext==target['presentation_sha256'],
    FileLocation.backend==storage.backend,FileLocation.bucket==storage.bucket,
    FileLocation.available.is_(True),FileLocation.is_primary.is_(True)).order_by(FileReference.id.desc()).limit(1)))
  assert refs,'pilot has no schema1 source bundle'
  visible=await runtime_pilot(s,owner,release_id)
  for metadata,_,file_id in refs:
   media=metadata.get('sequence_media') or {}
   sections=[part for part in media.get('sections',[]) if part.get('kind')=='movie_fragment']
   assert len(sections)==4 and all(type(part.get('file_id')) is int for part in sections),'pilot fragments not published'
   verify_api_fragments(owner,metadata,visible)
  await s.rollback()
 verified=[]
 for metadata,key,file_id in refs:
  with tempfile.TemporaryDirectory(prefix='story-pilot-') as tmp:
   bundle=Path(tmp)/'source.zip';size=0
   with bundle.open('wb') as output:
    async for chunk in storage.get(key):
     size+=len(chunk);assert size<=256*1024**2,'pilot bundle exceeds 256 MiB inspection bound'
     output.write(chunk)
   with zipfile.ZipFile(bundle) as archive:
    count=verify_bundle(archive,metadata,state)
    published=await verify_published(storage,archive,metadata)
    verified.append({'source_file_id':file_id,'fragments':count,'published_fragments':published})
 print(json.dumps({'ready':True,'run_id':run_id,'status':run_status,'bundles':verified}),flush=True)
async def run():
 try:await asyncio.wait_for(main(),timeout=120)
 finally:await engine.dispose()
asyncio.run(run())
'''


def version(value):
    if not re.fullmatch(r"\d+\.\d+\.\d+", value):
        raise argparse.ArgumentTypeError("use a full version, e.g. 3.1.0")
    return tuple(map(int, value.split(".")))


def select_releases(rows, first, last):
    if not rows or len({r["version"] for r in rows}) != len(rows):
        raise ValueError("missing releases or ambiguous version identities")
    latest = max(rows, key=lambda r: r["sequence"])
    if first > last or last > version(latest["version"]):
        raise ValueError("select imported releases through the latest imported version")
    selected = [r for r in rows if first <= version(r["version"]) <= last]
    if not selected:
        raise ValueError("no imported releases in range")
    return sorted(selected, key=lambda r: r["sequence"])


def require_backfill(report, release_id, *, clean=False, pilot=False):
    if report.get("release_id") != release_id or report.get("uncompiled_owners") != 0:
        raise ValueError("backfill scope mismatch or uncompiled owners")
    if pilot and not report.get("candidate_links", 0):
        raise ValueError("M0334 pilot produced no candidates")
    if clean and any(report.get(k, -1) != 0 for k in
                     ("missing_links", "missing_asset_nodes", "missing_asset_revisions")):
        raise ValueError("backfill verification still has missing evidence/assets")


def enqueue_errors(value):
    if isinstance(value, dict):
        return value.get("enqueue_failed", 0) + sum(enqueue_errors(v) for v in value.values())
    if isinstance(value, list):
        return sum(enqueue_errors(v) for v in value)
    return 0


class Recovery:
    def __init__(self, args):
        self.args = args
        self.root = args.state_dir.resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.root / "journal.json"
        self.state = json.loads(self.path.read_text()) if self.path.exists() else {
            "expected_tag": args.expected_tag, "pilot_run_id": args.pilot_run_id, "steps": {}}
        if self.state["expected_tag"] != args.expected_tag or self.state["pilot_run_id"] not in (None, args.pilot_run_id):
            raise ValueError("journal belongs to a different deployment/pilot; use a new state directory")
        self.state["pilot_run_id"] = args.pilot_run_id
        self.refresh_ids = sorted(set(getattr(args, 'refresh_quest_node_id', None) or REVIEWED_QUEST_NODES))
        if self.state.get('refresh_quest_node_ids', self.refresh_ids) != self.refresh_ids:
            raise ValueError('journal belongs to a different quest refresh scope; use a new state directory')
        self.state['refresh_quest_node_ids'] = self.refresh_ids
        self.deadline = time.monotonic() + args.max_seconds

    def save(self, name, **fields):
        self.state["steps"][name] = {"time": datetime.now(UTC).isoformat(), **fields}
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.state, ensure_ascii=False, indent=2) + "\n")
        temporary.replace(self.path)

    def guard(self):
        a = self.args
        if time.monotonic() >= self.deadline:
            raise RuntimeError("runtime budget reached; rerun to resume completed checkpoints")
        if shutil.disk_usage(self.root).free < a.min_disk_gib * 1024**3:
            raise RuntimeError("insufficient free disk")
        memory = dict(line.split(":", 1) for line in Path("/proc/meminfo").read_text().splitlines())
        if int(memory["MemAvailable"].split()[0]) * 1024 < a.min_memory_gib * 1024**3:
            raise RuntimeError("insufficient available host memory")
        if os.getloadavg()[0] > a.max_load_per_cpu * (os.cpu_count() or 1):
            raise RuntimeError("host load exceeds recovery budget")
        identities = sorted({identity for selector in (f'name=^/{a.project}-', f'label=com.docker.compose.project={a.project}')
                             for identity in subprocess.check_output(['docker', 'ps', '--filter', selector, '-q'], timeout=15).decode().split()})
        if not identities:
            raise RuntimeError('deploy gate: no project containers')
        template = '{"Name":{{json .Name}},"Image":{{json .Image}},"Config":{"Image":{{json .Config.Image}},"Labels":{{json .Config.Labels}}},"State":{{json .State}},"HostConfig":{"Memory":{{json .HostConfig.Memory}}}}'
        containers = [json.loads(line) for line in subprocess.check_output(
            ['docker', 'inspect', '--format', template, *identities], timeout=15).decode().splitlines()]
        seen, expected = set(), {}
        for c in containers:
            service = (c['Config'].get('Labels') or {}).get('com.docker.compose.service', '')
            image = c['Config']['Image'].split('@', 1)[0]
            repository = image.rsplit(':', 1)[0] if ':' in image.rsplit('/', 1)[-1] else image
            backend = service == 'api' or service.endswith('-worker') or service in {'story-agent', 'cutscene-vision'}
            if not backend and not repository.endswith(('/solaris-atlas-api', '/solaris-atlas-worker', '/solaris-atlas-models')):
                continue
            seen.add(service)
            if repository not in expected:
                expected[repository] = subprocess.check_output(
                    ['docker', 'image', 'inspect', '--format', '{{.Id}}', repository + ':' + a.expected_tag], timeout=15).decode().strip()
            if not expected[repository] or c.get('Image') != expected[repository]:
                raise RuntimeError('deploy gate: backend image ID differs from pulled immutable SHA image')
            if not c["State"]["Running"] or c["State"].get("OOMKilled"):
                raise RuntimeError("deploy gate: unhealthy/stopped/OOM container")
            if c["State"].get("Health", {}).get("Status", "healthy") != "healthy":
                raise RuntimeError("deploy gate: unhealthy container")
            if (service == 'image-worker' or "image-worker" in c["Name"]) and c["HostConfig"]["Memory"] < 3 * 1024**3:
                raise RuntimeError("deploy gate: image worker memory below 3 GiB")
        if not {'api','media-worker','projection-worker','asset-worker','image-worker','audio-worker','video-worker'} <= seen:
            raise RuntimeError('deploy gate: required backend producer/consumer service missing')

    def command(self, name, command, *, write=False, stdin=None):
        old = self.state["steps"].get(name, {})
        if write and old.get("status") == "done":
            return old["result"]
        if write and old:
            raise RuntimeError(f"reconcile interrupted/failed write step {name}; see journal and logs")
        self.guard()
        stem = re.sub(r"[^a-zA-Z0-9_.-]", "_", name)
        stdout, stderr = self.root / (stem + ".jsonl"), self.root / (stem + ".stderr")
        with stdout.open("w") as out, stderr.open("w") as err:
            process = subprocess.Popen(command, stdin=subprocess.PIPE if stdin else subprocess.DEVNULL,
                                       stdout=out, stderr=err, text=True, start_new_session=True)
            self.save(name, status="started", write=write, host_pid=process.pid, command=command,
                      stdout=str(stdout), stderr=str(stderr))
            if stdin:
                process.stdin.write(stdin)
                process.stdin.close()
            try:
                code = process.wait(timeout=min(self.args.step_seconds, max(1, self.deadline - time.monotonic())))
            except subprocess.TimeoutExpired:
                # Do NOT kill docker exec or the active backend operation.
                self.save(name, status="uncertain", write=write, host_pid=process.pid,
                          stdout=str(stdout), stderr=str(stderr))
                raise RuntimeError(f"step {name} still running; reconcile logs/PID before resuming") from None
        if code:
            self.save(name, status="failed", write=write, exit_code=code,
                      stdout=str(stdout), stderr=str(stderr))
            raise RuntimeError(f"step {name} failed ({code}); diagnostic: {stderr}")
        if stdout.stat().st_size > 2 * 1024**2:
            raise RuntimeError(f"unexpectedly large report: {stdout}")
        values = [json.loads(line) for line in stdout.read_text().splitlines() if line.strip()]
        if len(values) != 1:
            raise RuntimeError(f"expected one exact-scope JSON report: {stdout}")
        self.save(name, status="done", write=write, result=values[0])
        return values[0]

    def inspect(self, name, *arguments):
        return self.command(name, ["docker", "exec", "-i", f"{self.args.project}-api-1",
                                   "python", "-", *map(str, arguments)], stdin=INSPECT)

    def module(self, name, module, arguments, *, write=False):
        return self.command(name, ["docker", "exec", f"{self.args.project}-api-1", "python", "-m",
                                   module, *map(str, arguments)], write=write)

    def backfill(self, name, release, *, pilot=False):
        arguments = ["--release-id", release["id"], "--max-links", self.args.max_links]
        if pilot:
            arguments += ["--state-key", PILOT_STATE]
        result = self.module(name + ".dry", SHOW_TALK, arguments)
        require_backfill(result, release["id"], pilot=pilot)
        if self.args.apply:
            if any(result[k] for k in ("missing_links", "missing_asset_nodes", "missing_asset_revisions")):
                self.module(name + ".apply", SHOW_TALK, [*arguments, "--apply"], write=True)
            result = self.module(name + ".verify", SHOW_TALK, arguments)
            require_backfill(result, release["id"], clean=True, pilot=pilot)

    def refresh_quests(self):
        report = self.command('reviewed.quests.refresh' if self.args.apply else 'reviewed.quests.dry',
            ['docker', 'exec', '-i', f'{self.args.project}-api-1', 'python', '-',
             json.dumps(self.refresh_ids), 'apply' if self.args.apply else 'dry'],
            write=self.args.apply, stdin=QUEST_REFRESH)
        if (report.get('quest_node_ids') != self.refresh_ids or report.get('snapshot_key') != 'current'
                or report.get('marked') != (len(self.refresh_ids) if self.args.apply else 0)):
            if self.args.apply:
                self.save('reviewed.quests.refresh', status='failed', write=True, result=report)
            raise RuntimeError('quest refresh scope/result differs; inspect journal before resuming')

    def run(self):
        rows = self.inspect("releases", "releases")
        selected = select_releases(rows, version(self.args.first), version(self.args.last))
        pilot, = [r for r in rows if r["version"] == "3.1.0"]
        self.backfill("pilot.3.1.M0334", pilot, pilot=True)
        # Do not depend on media success to expose the deployed encounter tree.
        # The durable write checkpoint prevents repeated generation increments.
        self.refresh_quests()
        if self.args.apply:
            # A journal checkpoint cannot prove the separately stored MP4s still
            # exist. Revalidate the pilot before every new batch of releases.
            while True:
                report = self.inspect("pilot.media", "pilot", self.args.pilot_run_id, pilot["id"], PILOT_STATE)
                if report.get("ready"):
                    break
                if report.get("status") not in {"pending", "queued", "running", "waiting_dependency"}:
                    raise RuntimeError("pilot failed; inspect before any further enqueue")
                self.guard()
                time.sleep(min(30, max(1, self.deadline - time.monotonic())))
        processed = 0
        for release in selected:
            name = f"release.{release['id']}.{release['version']}"
            if self.args.apply and self.state["steps"].get(name + ".enqueue", {}).get("status") == "done":
                continue
            self.backfill(name + ".sequence", release)
            arguments = ["--first", release["version"], "--last", release["version"], "--complete"]
            plan = self.module(name + ".story.dry", STORY_REPAIR, [*arguments, "--dry-run"])
            if plan.get("release_id") != release["id"] or plan.get("candidate_links", self.args.max_links + 1) > self.args.max_links:
                raise RuntimeError("story repair scope/bound differs; narrow selection or explicitly revise max-links")
            if self.args.apply:
                result = self.module(name + ".enqueue", STORY_REPAIR, arguments, write=True)
                if result.get("release_id") != release["id"] or enqueue_errors(result):
                    self.save(name + ".enqueue", status="failed", write=True, result=result)
                    raise RuntimeError("story publication scope differs or broker confirmation failed; reconcile journal")
                print(json.dumps({"event": "enqueued_not_completed", "release_id": release["id"], "result": result}), flush=True)
            processed += 1
            if processed >= self.args.max_releases:
                break


def parser():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--first", required=True, type=str)
    p.add_argument("--last", required=True, type=str)
    p.add_argument("--expected-tag", required=True, help="deployed full 40-character commit SHA")
    p.add_argument("--state-dir", type=Path, required=True, help="durable directory on worker/DB filesystem")
    p.add_argument("--project", default="solaris-atlas")
    p.add_argument("--apply", action="store_true")
    p.add_argument("--pilot-run-id", type=int)
    p.add_argument('--refresh-quest-node-id', type=int, action='append',
                   help='reviewed graph Quest.node_id; defaults to 4688653 and 5389571, current snapshot only')
    p.add_argument("--max-links", type=int, default=3000)
    p.add_argument("--max-releases", type=int, default=1)
    p.add_argument("--max-seconds", type=int, default=3600)
    p.add_argument("--step-seconds", type=int, default=180)
    p.add_argument("--min-disk-gib", type=int, default=20)
    p.add_argument("--min-memory-gib", type=int, default=3)
    p.add_argument("--max-load-per-cpu", type=float, default=1.5)
    return p


def main():
    p = parser()
    args = p.parse_args()
    version(args.first)
    version(args.last)
    if not re.fullmatch(r"[a-f0-9]{40}", args.expected_tag):
        p.error("expected-tag must be the deployed full commit SHA")
    if args.apply and (args.pilot_run_id is None or args.pilot_run_id < 1):
        p.error("--apply requires an existing, exactly scoped --pilot-run-id")
    if not 1 <= args.max_links <= 10000 or not 1 <= args.max_releases <= 21:
        p.error("max-links must be 1..10000; max-releases 1..21")
    if args.refresh_quest_node_id and (len(args.refresh_quest_node_id) > 100 or any(i < 1 for i in args.refresh_quest_node_id)):
        p.error('select at most 100 positive, explicitly reviewed graph quest node IDs')
    if not math.isfinite(args.max_load_per_cpu) or min(args.max_seconds, args.step_seconds, args.min_disk_gib, args.min_memory_gib, args.max_load_per_cpu) <= 0:
        p.error("resource/runtime bounds must be positive")
    os.umask(0o077)
    import fcntl  # Linux Docker host only; keep pure helpers testable elsewhere.
    root = args.state_dir.resolve()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (root / "runner.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            recovery = Recovery(args)
            recovery.run()
        except (RuntimeError, ValueError, OSError, subprocess.SubprocessError) as error:
            p.exit(1, f"Recovery stopped: {error}\n")


if __name__ == "__main__":
    main()
