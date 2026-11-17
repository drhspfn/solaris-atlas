"""Check rendered worker knobs and actual Compose resolution without starting Docker."""

import json
import os
import runpy
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RENDERER = runpy.run_path(str(ROOT / "scripts/render-production-env.py"))


class WorkerConfigurationTests(unittest.TestCase):
    def settings(self):
        return {name: {key: value for key, value in values.items() if value is not None}
                for name, values in RENDERER["GROUPS"].items()}

    def test_defaults_and_four_snapshot_compile_slots_are_valid(self):
        settings = self.settings()
        validate = RENDERER["validate_worker_config"]
        validate(settings)
        settings["compose.env"]["WUWA_SNAPSHOT_REPLICAS"] = "4"
        settings["worker.env"]["WUWA_QUEUE_CONCURRENCY"] = "snapshot_build=1,pool_audio=2"
        validate(settings)

    def test_projection_workers_can_be_disabled_and_scaled_independently(self):
        settings = self.settings()
        for count in ("0", "1", "3"):
            settings["compose.env"]["WUWA_PROJECTION_REPLICAS"] = count
            RENDERER["validate_worker_config"](settings)
        for count in ("-1", "0.5"):
            settings["compose.env"]["WUWA_PROJECTION_REPLICAS"] = count
            with self.assertRaises(SystemExit):
                RENDERER["validate_worker_config"](settings)

    def test_heavy_defaults_include_measured_export_headroom(self):
        settings = self.settings()
        self.assertEqual(settings["compose.env"]["WUWA_ASSET_MEMORY"], "1536m")
        for pool in ("AUDIO", "IMAGE"):
            self.assertEqual(settings["compose.env"][f"WUWA_{pool}_MEMORY"], "2g")
        self.assertEqual(settings["compose.env"]["WUWA_VIDEO_MEMORY"], "2560m")
        self.assertEqual(settings["compose.env"]["WUWA_VIDEO_CPUS"], "2")
        self.assertEqual(settings["compose.env"]["WUWA_IMAGE_REPLICAS"], "1")

    def test_projection_budget_covers_the_measured_event_catalog(self):
        settings = self.settings()["shared.env"]
        # The 666-event scope was rejected at 2 GiB before any keyset pages
        # were queued, leaving published artwork and metadata stale.
        self.assertGreater(int(settings["READ_PROJECTION_MAX_BYTES"]), 2_246_074_344)
        self.assertEqual(int(settings["READ_PROJECTION_MAX_BYTES"]), 4 * 1024**3)
        self.assertEqual(settings["READ_PROJECTION_MAX_ROWS"], "250000")
        self.assertGreaterEqual(int(settings["READ_PROJECTION_MIN_FREE_GIB"]), 20)

    def test_measured_replica_overrides_are_supported_without_assuming_reserved_ram(self):
        settings = self.settings()
        settings["compose.env"].update({"WUWA_IMAGE_REPLICAS": "2", "WUWA_AUDIO_REPLICAS": "2"})
        RENDERER["validate_worker_config"](settings)

    def test_invalid_replica_resources_concurrency_and_db_headroom_fail_early(self):
        for group, key, value in [
            ("compose.env", "WUWA_IMAGE_REPLICAS", "0"),
            ("compose.env", "WUWA_SNAPSHOT_REPLICAS", "5"),
            ("compose.env", "WUWA_VIDEO_CPUS", "NaN"),
            ("compose.env", "WUWA_AUDIO_MEMORY", "-1g"),
            ("worker.env", "WUWA_QUEUE_CONCURRENCY", "snapshot_build=5"),
            ("worker.env", "WUWA_QUEUE_CONCURRENCY", "pool_audio=0"),
            ("worker.env", "WUWA_SHUTDOWN_TIMEOUT_SECONDS", "120"),
            ("worker.env", "WUWA_WORKER_DB_POOL_SIZE", "0"),
            ("worker.env", "WUWA_WORKER_DB_MAX_OVERFLOW", "-1"),
            ("worker.env", "WUWA_QUEUE_CONCURRENCY", "pool_image=99"),
        ]:
            with self.subTest(key=key, value=value):
                settings = self.settings()
                settings[group][key] = value
                with self.assertRaises(SystemExit):
                    RENDERER["validate_worker_config"](settings)

    def test_all_worker_knobs_are_forwarded_by_deployment_workflow(self):
        workflow = (ROOT / ".github/workflows/production-deploy.yml").read_text()
        for values in RENDERER["GROUPS"].values():
            for key in values:
                if key.startswith("WUWA_"):
                    self.assertIn(f"{key}: ${{{{ vars.{key} }}}}", workflow)

    def test_vision_image_reuses_same_revision_cpu_dependencies(self):
        dockerfile = (ROOT / "packages/worker/Dockerfile.vision").read_text()
        images = (ROOT / ".github/workflows/images.yml").read_text()
        self.assertIn("FROM ${API_IMAGE} AS visual", dockerfile)
        self.assertIn("FROM ${WORKER_IMAGE}", dockerfile)
        self.assertIn("COPY --from=visual /opt/venv /opt/visual-venv", dockerfile)
        self.assertIn("PYTHONPATH=/opt/visual-venv/lib/python3.12/site-packages", dockerfile)
        self.assertIn("RUN --network=none python -c", dockerfile)
        self.assertIn("-worker:$GITHUB_SHA", images)
        self.assertIn("-api:$GITHUB_SHA", images)
        self.assertIn("file: packages/worker/Dockerfile.vision", images)

    @unittest.skipUnless(shutil.which("docker"), "Docker Compose CLI is required")
    def test_compose_resolves_independent_bounded_pools_without_starting_services(self):
        with tempfile.TemporaryDirectory() as directory:
            empty = Path(directory) / "empty.env"
            empty.write_text("")
            for variant in ("prod", "local", "dev"):
                with self.subTest(variant=variant):
                    files = ["infrastructure/prod/compose.yml"] if variant == "prod" else ["infrastructure/local/compose.yml"]
                    if variant == "dev":
                        files.append("infrastructure/dev/compose.yml")
                    command = ["docker", "compose", "--env-file", str(empty)]
                    for file in files:
                        command.extend(["-f", str(ROOT / file)])
                    command.extend(["--profile", "local-storage", "--profile", "workers", "--profile", "assets", "--profile", "worker", "--profile", "story-agent", "--profile", "cutscene-vision",
                                    "config", "--no-env-resolution", "--format", "json"])
                    environment = {**os.environ, "GHCR_OWNER": "test", "DOMAIN": "example.invalid",
                                   "POSTGRES_USER": "wuwa", "RABBITMQ_DEFAULT_USER": "wuwa",
                                   "POSTGRES_PASSWORD": "fake", "RABBITMQ_DEFAULT_PASS": "fake",
                                   "MINIO_ROOT_PASSWORD": "fake"}
                    result = subprocess.run(command, env=environment, text=True, capture_output=True, timeout=20, check=False)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    services = json.loads(result.stdout)["services"]
                    # Cache payload pressure must not evict active quotas/leases.
                    self.assertIn("noeviction", services["redis"]["command"])
                    self.assertEqual(services["redis"]["healthcheck"]["test"], ["CMD", "redis-cli", "ping"])
                    for name in ("api", "media-worker", "story-agent", "cutscene-vision"):
                        self.assertEqual(services[name]["depends_on"]["redis"]["condition"], "service_healthy")
                        if variant != "prod":
                            self.assertEqual(services[name]["environment"]["REDIS_URL"], "redis://redis:6379/0")
                    if variant == "prod":
                        vision = services["cutscene-vision"]
                        self.assertIn("solaris-atlas-vision:", vision["image"])
                        self.assertEqual(vision["environment"]["VISUAL_MEMORY_EMBEDDER"], "ccip")
                        self.assertEqual(vision["environment"]["VISUAL_MEMORY_CCIP_PATH"],
                                         "/models/ccip-24-4ea118d1")
                        self.assertEqual(vision["environment"]["VISUAL_MEMORY_DETECTOR_PATH"],
                                         "/models/anime-character-detector/character.onnx")
                        self.assertTrue(any(mount.get("target") == "/models" and mount.get("read_only")
                                            for mount in vision["volumes"]))
                        self.assertIn("solaris-atlas-worker:", services["story-agent"]["image"])
                        pooler = services["pgbouncer"]
                        self.assertNotIn("ports", pooler)
                        self.assertTrue(pooler["read_only"])
                        self.assertIn("@sha256:", pooler["image"])
                        self.assertIn("pgbouncer", services["api"]["depends_on"])
                        for name in ("snapshot-worker", "media-worker", "story-agent", "cutscene-vision"):
                            self.assertNotIn("pgbouncer", services[name]["depends_on"])
                            self.assertTrue(any(entry["path"].endswith("runtime.env") for entry in services[name]["env_file"]))
                    total_memory = 0
                    for service, pool, replicas in [("media-worker", "coordinator", 1), ("image-worker", "image", 1),
                                                     ("audio-worker", "audio", 1), ("video-worker", "video", 1)]:
                        config = services[service]
                        self.assertEqual(config["command"][-2:], ["--pool", pool])
                        self.assertEqual(config["deploy"]["replicas"], replicas)
                        self.assertGreater(float(config["cpus"]), 0)
                        self.assertEqual(config["stop_grace_period"], "2m0s")
                        if pool != "coordinator":
                            expected = 2560 if pool == "video" else 2048
                            self.assertEqual(int(config["mem_limit"]), expected * 1024**2)
                        if pool == "video":
                            self.assertEqual(float(config["cpus"]), 2)
                        total_memory += int(config["mem_limit"]) * replicas
                    self.assertEqual(total_memory, 7424 * 1024**2)
                    snapshot = services["snapshot-worker" if variant == "prod" else "worker"]
                    self.assertEqual(snapshot["command"][-1], "snapshot_build")
                    self.assertNotIn("asset_extract", snapshot["command"])
                    self.assertEqual(snapshot["deploy"]["replicas"], 1)
                    total_memory += int(snapshot["mem_limit"]) + int(services["asset-worker"]["mem_limit"])
                    self.assertEqual(int(services["asset-worker"]["mem_limit"]), 1536 * 1024**2)
                    self.assertEqual(total_memory, 11008 * 1024**2)


if __name__ == "__main__":
    unittest.main()
