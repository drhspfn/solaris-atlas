import asyncio
import copy
import hashlib
import importlib.util
import io
import json
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

SCRIPT = Path(__file__).resolve().parents[1] / "recover-story-imports.py"
spec = importlib.util.spec_from_file_location("story_recovery", SCRIPT)
recovery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recovery)


class RecoveryTests(unittest.TestCase):
    def verifiers(self):
        namespace = {"json": json, "hashlib": hashlib}
        code = recovery.INSPECT.split("def verify_bundle", 1)[1].split("async def main", 1)[0]
        exec("def verify_bundle" + code, namespace)  # noqa: S102 -- trusted, checked-in remote helper
        return namespace

    def pilot_fixture(self, *, corrupt=False, incomplete=False):
        prefix = 'media/Client/Content/Aki/Movies/LevelA_Seq/03_1/'
        files = {prefix + name + '.mp4': b'video:' + name.encode() for name in
                 ('M0334_Cam003', 'M0334_Cam003d', 'M0334_Cam005_Nanzhu', 'M0334_Cam005_Nvzhu')}
        sections = [{'source': f'/Game/Aki/M0334_All.{410+i}', 'kind': 'movie_fragment',
                     'status': 'extracted', 'file': name, 'archive_path': name.removeprefix('media/'),
                     'clock': {'numerator': 30, 'denominator': 1}, 'range': {'frames': [152, 374]},
                     'parameters': {'StartFrameOffset': {'Value': 102}}} for i, name in enumerate(files)]
        controls = {'media': {'complete_video': False, 'sections': sections[:-1] if incomplete else sections}}
        files['presentation.json'] = json.dumps({'source': {'StateKey': recovery.PILOT_STATE},
            'cues': [{'kind': 'sequence', 'asset': '/Game/Aki/Sequence/LevelA_Seq/Main/03_1/M0334/DA_M0334.DA_M0334'}]}).encode()
        files['controls.json'] = json.dumps(controls).encode()
        manifest = {'asset_version': '3.7.1', 'download_id': 'client',
                    'presentation': {'file': 'presentation.json', 'sha256': 'proof'},
                    'controls': {'file': 'controls.json'}, 'files': {name: {
                        'size_bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()} for name, data in files.items()}}
        metadata = {'game_source': {'presentation_sha256': 'proof', 'asset_version': '3.7.1', 'download_id': 'client'}}
        media = copy.deepcopy(controls['media'])
        media.update(schema_version=1, game_source=copy.deepcopy(metadata['game_source']), components=[])
        for i, section in enumerate(media['sections']):
            proof = manifest['files'][section['file']]
            sha = proof['sha256']
            part = {'file_id': i + 1, 'object_key': f'objects/{sha[:2]}/{sha[2:4]}/{sha}',
                    'size_bytes': proof['size_bytes']}
            section.update(part, sha256=sha, mime_type='video/mp4', status='published')
            media['components'].append(part)
        metadata['sequence_media'] = media
        if corrupt:
            name = next(iter(files))
            files[name] = b'x' * len(files[name])
        data = io.BytesIO()
        with zipfile.ZipFile(data, 'w') as archive:
            archive.writestr('manifest.json', json.dumps(manifest))
            for name, contents in files.items():
                archive.writestr(name, contents)
        return data, metadata

    def test_stored_pilot_reads_controls_file_and_rejects_corrupt_fragments(self):
        namespace = self.verifiers()
        for corrupt in (False, True):
            data, metadata = self.pilot_fixture(corrupt=corrupt)
            with zipfile.ZipFile(data) as archive:
                if not corrupt:
                    self.assertEqual(namespace['verify_bundle'](archive, metadata, recovery.PILOT_STATE), 4)
                else:
                    with self.assertRaisesRegex(AssertionError, 'checksum'):
                        namespace['verify_bundle'](archive, metadata, recovery.PILOT_STATE)

    def test_partial_fragment_coverage_and_wrong_client_are_rejected(self):
        namespace = self.verifiers()
        data, metadata = self.pilot_fixture(incomplete=True)
        with zipfile.ZipFile(data) as archive, self.assertRaisesRegex(AssertionError, 'all four'):
            namespace['verify_bundle'](archive, metadata, recovery.PILOT_STATE)
        data, metadata = self.pilot_fixture()
        metadata['game_source']['download_id'] = 'different-client'
        with zipfile.ZipFile(data) as archive, self.assertRaisesRegex(AssertionError, 'client provenance'):
            namespace['verify_bundle'](archive, metadata, recovery.PILOT_STATE)

    def test_published_fragment_heads_and_timing_are_verified(self):
        namespace = self.verifiers()
        for failure in (None, 'missing', 'truncated', 'timing', 'unpublished', 'components'):
            data, metadata = self.pilot_fixture()
            sizes = {part['object_key']: part['size_bytes'] for part in metadata['sequence_media']['components']}
            if failure == 'timing':
                metadata['sequence_media']['sections'][0]['parameters'] = {}
            elif failure == 'unpublished':
                metadata.pop('sequence_media')
            elif failure == 'components':
                metadata['sequence_media']['components'] = []

            async def stat(key, failure=failure, sizes=sizes):
                return None if failure == 'missing' else SimpleNamespace(size_bytes=sizes[key] - int(failure == 'truncated'))

            storage = SimpleNamespace(stat=AsyncMock(side_effect=stat))
            with self.subTest(failure=failure), zipfile.ZipFile(data) as archive:
                if failure is None:
                    self.assertEqual(asyncio.run(namespace['verify_published'](storage, archive, metadata)), 4)
                    self.assertEqual(storage.stat.await_count, 4)
                else:
                    with self.assertRaises(AssertionError):
                        asyncio.run(namespace['verify_published'](storage, archive, metadata))

    def test_real_saved_m0334_bundle_matches_verifier_contract(self):
        path = SCRIPT.parents[0].parent / 'scratch/sigillum-recording/m0334-pipeline-pilot/M0334-fragments.zip'
        if not path.exists():
            self.skipTest('real retained pilot artifact is local evidence, not a tracked fixture')
        with zipfile.ZipFile(path) as archive:
            manifest = json.loads(archive.read('manifest.json'))
            metadata = {'game_source': {key: manifest[key] for key in ('asset_version', 'download_id')}}
            metadata['game_source']['presentation_sha256'] = manifest['presentation']['sha256']
            self.assertEqual(self.verifiers()['verify_bundle'](archive, metadata, recovery.PILOT_STATE), 4)

    def test_zip_metadata_without_api_visible_fragment_references_is_rejected(self):
        _, metadata = self.pilot_fixture()
        visible = {7: copy.deepcopy(metadata['sequence_media'])}
        for section in visible[7]['sections']:
            section['url'] = 'https://cdn.test/' + section['object_key']
        verify = self.verifiers()['verify_api_fragments']
        verify(7, metadata, visible)
        stale = copy.deepcopy(visible)
        stale[7]['game_source']['presentation_sha256'] = 'stale-raw-flow'
        for invalid in ({}, {8: visible[7]}, stale):
            with self.assertRaisesRegex(AssertionError, 'API-visible|raw-flow'):
                verify(7, metadata, invalid)

    def args(self, root, **changes):
        values = {"state_dir": Path(root), "expected_tag": "a" * 40, "pilot_run_id": 123,
                      "max_seconds": 100, "step_seconds": 10, "project": "solaris-atlas",
                      "max_links": 3000, "apply": True, "max_releases": 1, "first": "1.0.0", "last": "3.0.0",
                      "min_disk_gib": 20, "min_memory_gib": 3, "max_load_per_cpu": 1.5}
        return SimpleNamespace(**(values | changes))

    def test_range_includes_latest_and_rejects_future_or_ambiguous_versions(self):
        rows = [{"id": 1, "version": "1.0.0", "sequence": 1},
                {"id": 21, "version": "3.7.0", "sequence": 21}]
        self.assertEqual(recovery.select_releases(rows, (1, 0, 0), (3, 6, 0)), rows[:1])
        self.assertEqual(recovery.select_releases(rows, (1, 0, 0), (3, 7, 0)), rows)
        for invalid in [rows + rows[:1], []]:
            with self.assertRaises(ValueError):
                recovery.select_releases(invalid, (1, 0, 0), (3, 6, 0))
        with self.assertRaises(ValueError):
            recovery.select_releases(rows, (1, 0, 0), (3, 8, 0))

    def test_all_21_imported_releases_can_be_selected(self):
        rows = [{'id': i, 'version': f'1.{i}.0', 'sequence': i} for i in range(1, 21)]
        rows.append({'id': 21, 'version': '3.7.0', 'sequence': 21})
        self.assertEqual(len(recovery.select_releases(rows, (1, 0, 0), (3, 7, 0))), 21)

    def test_cli_accepts_21_release_bound_and_rejects_22(self):
        import sys

        with tempfile.TemporaryDirectory() as root:
            args = ['recover-story-imports.py', '--first', '1.0.0', '--last', '3.7.0',
                    '--expected-tag', 'a' * 40, '--state-dir', root, '--max-releases']
            runner = Mock(root=Path(root))
            fcntl = SimpleNamespace(flock=Mock(), LOCK_EX=1, LOCK_NB=2)
            with patch.object(sys, 'argv', [*args, '21']), patch.dict(sys.modules, {'fcntl': fcntl}), patch.object(recovery, 'Recovery', return_value=runner):
                recovery.main()
            runner.run.assert_called_once()
            with patch.object(sys, 'argv', [*args, '22']), patch('sys.stderr', io.StringIO()), self.assertRaises(SystemExit):
                recovery.main()

    def test_unresolved_owners_and_incomplete_postcheck_stop(self):
        report = {"release_id": 15, "uncompiled_owners": 0, "candidate_links": 1,
                      "missing_links": 0, "missing_asset_nodes": 0, "missing_asset_revisions": 0}
        recovery.require_backfill(report, 15, clean=True, pilot=True)
        for change in [{"uncompiled_owners": 1}, {"missing_asset_revisions": 1},
                       {"release_id": 21}, {"candidate_links": 0}]:
            with self.assertRaises(ValueError):
                recovery.require_backfill(report | change, 15, clean=True, pilot=True)

    def test_nested_broker_failure_is_detected(self):
        self.assertEqual(recovery.enqueue_errors({"client_index": {
            "enqueue_failed": 0, "story_media": {"enqueue_failed": 2}}}), 2)

    def test_write_checkpoint_is_resumed_without_duplicate_command(self):
        with tempfile.TemporaryDirectory() as root:
            runner = recovery.Recovery(self.args(root))
            runner.save("release.1.enqueue", status="done", result={"parent_id": 100})
            resumed = recovery.Recovery(self.args(root))
            with patch.object(resumed, "guard", side_effect=AssertionError("must not execute")):
                self.assertEqual(resumed.command("release.1.enqueue", ["never"], write=True),
                                 {"parent_id": 100})

    def test_uncertain_write_does_not_retry_or_kill_job(self):
        with tempfile.TemporaryDirectory() as root:
            runner = recovery.Recovery(self.args(root))
            process = Mock(pid=12345)
            process.wait.side_effect = subprocess.TimeoutExpired("docker exec", 10)
            with patch.object(runner, "guard"), patch.object(recovery.subprocess, "Popen", return_value=process), self.assertRaisesRegex(RuntimeError, "still running"):
                runner.command("write", ["docker", "exec"], write=True)
            self.assertEqual(runner.state["steps"]["write"]["status"], "uncertain")
            process.kill.assert_not_called()
            process.terminate.assert_not_called()
            with self.assertRaisesRegex(RuntimeError, "reconcile"):
                recovery.Recovery(self.args(root)).command("write", ["never"], write=True)

    def test_journal_cannot_be_reused_for_another_deployment(self):
        with tempfile.TemporaryDirectory() as root:
            runner = recovery.Recovery(self.args(root))
            runner.save("test", status="done", result={})
            with self.assertRaises(ValueError):
                recovery.Recovery(self.args(root, expected_tag="b" * 40))

    def test_pilot_precedes_enqueue_and_release_budget_is_one(self):
        with tempfile.TemporaryDirectory() as root:
            runner = recovery.Recovery(self.args(root))
            rows = [{"id": 1, "version": "1.0.0", "sequence": 1},
                    {"id": 2, "version": "1.1.0", "sequence": 2},
                    {"id": 15, "version": "3.1.0", "sequence": 15},
                    {"id": 21, "version": "3.7.0", "sequence": 21}]
            events = []

            def inspect(name, *args):
                events.append(name)
                return rows if name == "releases" else {"ready": True}

            def module(name, module, arguments, **kwargs):
                events.append(name)
                self.assertEqual(module, recovery.STORY_REPAIR)
                self.assertIn("--complete", arguments)
                self.assertEqual(arguments[arguments.index("--first") + 1], "1.0.0")
                self.assertEqual(arguments[arguments.index("--last") + 1], "1.0.0")
                return {"release_id": 1, "candidate_links": 2, "client_index": {"enqueue_failed": 0}}

            with patch.object(runner, "inspect", side_effect=inspect), patch.object(runner, "module", side_effect=module), patch.object(runner, "backfill") as backfill, patch.object(runner, 'refresh_quests', side_effect=lambda: events.append('quests.refresh')):
                runner.run()
            self.assertEqual(events, ["releases", "quests.refresh", "pilot.media", "release.1.1.0.0.story.dry", "release.1.1.0.0.enqueue"])
            self.assertEqual(backfill.call_args_list[0].args[:2], ("pilot.3.1.M0334", rows[2]))
            self.assertEqual(backfill.call_count, 2)

    def test_failed_pilot_prevents_any_broad_enqueue(self):
        with tempfile.TemporaryDirectory() as root:
            runner = recovery.Recovery(self.args(root))
            rows = [{"id": 1, "version": "1.0.0", "sequence": 1},
                    {"id": 15, "version": "3.1.0", "sequence": 15},
                    {"id": 21, "version": "3.7.0", "sequence": 21}]
            with patch.object(runner, "inspect", side_effect=[rows, {"ready": False, "status": "failed"}]), patch.object(runner, "backfill"), patch.object(runner, 'refresh_quests') as refresh, patch.object(runner, "module") as module, self.assertRaisesRegex(RuntimeError, "pilot failed"):
                runner.run()
            module.assert_not_called()
            refresh.assert_called_once()  # Encounter cache refresh does not depend on MP4 success.

    def test_cached_ready_receipt_does_not_bypass_live_pilot_verification(self):
        with tempfile.TemporaryDirectory() as root:
            runner = recovery.Recovery(self.args(root))
            runner.save('pilot.media', status='done', result={'ready': True})
            rows = [{'id': 1, 'version': '1.0.0', 'sequence': 1},
                    {'id': 15, 'version': '3.1.0', 'sequence': 15},
                    {'id': 21, 'version': '3.7.0', 'sequence': 21}]
            with patch.object(runner, 'inspect', side_effect=[rows, {'ready': False, 'status': 'failed'}]) as inspect, patch.object(runner, 'backfill'), patch.object(runner, 'refresh_quests'), patch.object(runner, 'module') as module, self.assertRaisesRegex(RuntimeError, 'pilot failed'):
                runner.run()
            self.assertEqual(inspect.call_count, 2)
            module.assert_not_called()

    def test_reviewed_quest_refresh_is_current_only_and_checkpointed(self):
        with tempfile.TemporaryDirectory() as root:
            runner = recovery.Recovery(self.args(root))
            report = {'quest_node_ids': [4688653, 5389571], 'snapshot_key': 'current', 'marked': 2}
            runner.save('reviewed.quests.refresh', status='done', result=report)
            resumed = recovery.Recovery(self.args(root))
            with patch.object(resumed, 'guard', side_effect=AssertionError('must not execute again')):
                resumed.refresh_quests()
            with self.assertRaisesRegex(ValueError, 'refresh scope'):
                recovery.Recovery(self.args(root, refresh_quest_node_id=[4688653]))

    def test_reviewed_quest_refresh_dry_run_cannot_write(self):
        with tempfile.TemporaryDirectory() as root:
            runner = recovery.Recovery(self.args(root, apply=False, refresh_quest_node_id=[5389571]))
            report = {'quest_node_ids': [5389571], 'snapshot_key': 'current', 'marked': 0}
            with patch.object(runner, 'command', return_value=report) as command:
                runner.refresh_quests()
            self.assertIs(command.call_args.kwargs['write'], False)
            self.assertEqual(command.call_args.args[1][-2:], ['[5389571]', 'dry'])
            self.assertEqual(command.call_args.kwargs['stdin'], recovery.QUEST_REFRESH)

    def test_low_disk_stops_before_any_docker_execution(self):
        with tempfile.TemporaryDirectory() as root:
            runner = recovery.Recovery(self.args(root))
            with patch.object(recovery.shutil, "disk_usage", return_value=SimpleNamespace(free=1)), patch.object(recovery.subprocess, "check_output") as docker, self.assertRaisesRegex(RuntimeError, "disk"):
                runner.guard()
            docker.assert_not_called()

    def test_deployment_checks_image_ids_all_pools_and_extra_replicas(self):
        with tempfile.TemporaryDirectory() as root:
            runner = recovery.Recovery(self.args(root))
            def container(service, replica=1):
                repo = 'ghcr.io/test/solaris-atlas-api' if service == 'api' else 'ghcr.io/test/solaris-atlas-worker'
                return {'Name': f'/solaris-atlas-{service}-{replica}', 'Image': 'sha256:' + ('api' if service == 'api' else 'worker'),
                        'Config': {'Image': repo + ':main', 'Labels': {'com.docker.compose.service': service}},
                        'State': {'Running': True, 'OOMKilled': False}, 'HostConfig': {'Memory': 3 * 1024**3}}
            containers = [container(service) for service in ('api', 'media-worker', 'projection-worker',
                'asset-worker', 'image-worker', 'audio-worker', 'video-worker')]

            def docker(command, **kwargs):
                if command[1] == 'ps':
                    return b'container1\ncontainer2\n'
                if command[1] == 'image':
                    self.assertTrue(command[-1].endswith(':' + 'a' * 40))
                    return b'sha256:api' if 'solaris-atlas-api:' in command[-1] else b'sha256:worker'
                return '\n'.join(json.dumps(c) for c in containers).encode()

            with patch.object(recovery.shutil, "disk_usage", return_value=SimpleNamespace(free=100 * 1024**3)), patch.object(Path, "read_text", return_value="MemAvailable: 10000000 kB"), patch.object(recovery.os, "getloadavg", return_value=(0, 0, 0), create=True), patch.object(recovery.subprocess, "check_output", side_effect=docker):
                # A :main alias is valid only when its actual ID equals the local immutable SHA image.
                runner.guard()
                extra = container('audio-worker', 9)
                containers.append(extra)
                extra['Image'] = 'sha256:old-worker'
                with self.assertRaisesRegex(RuntimeError, 'image ID'):
                    runner.guard()
                extra['Image'] = 'sha256:worker'
                extra['State']['OOMKilled'] = True
                with self.assertRaisesRegex(RuntimeError, "OOM"):
                    runner.guard()
                containers.pop()
                containers[:] = [c for c in containers if 'video-worker' not in c['Name']]
                with self.assertRaisesRegex(RuntimeError, 'service missing'):
                    runner.guard()

    def test_journal_is_loaded_only_after_exclusive_lock(self):
        import sys

        with tempfile.TemporaryDirectory() as root:
            args = ['recover-story-imports.py', '--first', '1.0.0', '--last', '3.7.0',
                    '--expected-tag', 'a' * 40, '--state-dir', root]

            def acquire(*_):
                (Path(root) / 'journal.json').write_text(json.dumps({'expected_tag': 'a' * 40,
                    'pilot_run_id': None, 'steps': {'prior.enqueue': {'status': 'done', 'result': {}}}}))

            seen = []
            with patch.object(sys, 'argv', args), patch.dict(sys.modules, {'fcntl': SimpleNamespace(
                    flock=acquire, LOCK_EX=1, LOCK_NB=2)}), patch.object(recovery.Recovery, 'run',
                    lambda instance: seen.append(instance.state['steps']['prior.enqueue']['status'])):
                recovery.main()
            self.assertEqual(seen, ['done'])


if __name__ == "__main__":
    unittest.main()

