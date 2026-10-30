"""Exercise deployment orchestration with Docker commands recorded, not executed."""

import json
import os
import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GIT_BASH = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Git/bin/bash.exe"
BASH = str(GIT_BASH) if os.name == "nt" and GIT_BASH.is_file() else shutil.which("bash")


@unittest.skipUnless(BASH, "Bash is required to test deployment orchestration")
class DeploySshTests(unittest.TestCase):
    def test_keepalive_and_exit_status_for_long_deploy(self):
        workflow = (ROOT / ".github/workflows/production-deploy.yml").read_text()
        step = workflow.split("      - name: Deploy component\n", 1)[1].split(
            "      - name: Diagnose unhealthy containers\n", 1
        )[0]
        command = textwrap.dedent(step.split("        run: |\n", 1)[1])
        harness = r'''
ssh() {
  printf '%s\n' "$@" >&2
  IFS= read -r token
  [[ "$token" == test-token ]] || return 99
  printf '%s\n' 'remote deployment output'
  return "$SSH_RESULT"
}
'''
        for status in (0, 37, 255):
            with self.subTest(status=status):
                result = subprocess.run(
                    [BASH, "-e", "-o", "pipefail", "-s"], input=harness + command,
                    env={**os.environ, "USER": "test", "HOST": "example.test",
                         "GHCR_TOKEN": "test-token", "IMAGE_TAG": "abc1234",
                         "COMPONENT": "api", "OWNER": "owner", "SSH_RESULT": str(status)},
                    text=True, encoding="utf-8", capture_output=True, timeout=10, check=False,
                )
                self.assertEqual(result.returncode, status, result.stderr)
                self.assertIn("remote deployment output", result.stdout)
                args = result.stderr.splitlines()
                self.assertEqual(args[:4], ["-o", "ServerAliveInterval=20", "-o", "ServerAliveCountMax=6"])
                self.assertIn("test@example.test", args)
                self.assertTrue(args[-1].endswith("bash scripts/deploy-remote.sh"))


@unittest.skipUnless(BASH, "Bash is required to test deployment orchestration")
class DeploymentTests(unittest.TestCase):
    def deploy(self, component, fail_validation=False, worker_image="", fail_migration=False, empty_auth=False,
               fail_activation=False, migration_plan="done:more", fail_online=False, fail_lock=False,
               fail_client_listing=False):
        source = (ROOT / "scripts/deploy-remote.sh").read_text()
        lock_block = source.split("# Serialize deployments", 1)[1].split("\numask 077", 1)[0]
        tail = 'case "$DEPLOY_COMPONENT" in' + source.split('case "$DEPLOY_COMPONENT" in', 1)[1]
        harness = r'''
set -euo pipefail
DEPLOY_COMPONENT=$1
FAIL_VALIDATION=$2
WORKER_IMAGE=$3
FAIL_MIGRATION=$4
EMPTY_AUTH=$5
FAIL_ACTIVATION=$6
MIGRATION_PLAN=$7
FAIL_ONLINE=$8
FAIL_LOCK=$9
FAIL_CLIENT_LISTING=${10}
GHCR_OWNER=test
IMAGE_TAG=abc1234
USE_MINIO=false
ENV_DIR=$(mktemp -d)
APP_DIR=$ENV_DIR
mkdir "$APP_DIR/pgbouncer"
touch "$APP_DIR/pgbouncer/auth.sql"
trap 'rm -f "$ENV_DIR/pgbouncer/userlist.txt" "$ENV_DIR/pgbouncer/userlist.txt.next" "$ENV_DIR/pgbouncer/auth.sql" "$ENV_DIR/.migration-deploy.lock"; rmdir "$ENV_DIR/pgbouncer" "$ENV_DIR"' EXIT
chown() { :; }
install() { mkdir -p "${@: -1}"; }
flock() { [[ "$FAIL_LOCK" != 1 ]]; }
docker() { printf '%s\n' "$*"; printf '%s\n' "$*" >&2; }
record_compose() {
  if [[ "$*" == *"scripts/search_index_deploy_plan.py"* ]]; then
    printf '%s\n' "$*" >&2
    printf '%s\n' "$MIGRATION_PLAN"
    return
  fi
  if [[ "$*" == "pull model-worker" || "$*" == *"model_publication --activate" ]]; then
    printf 'activation-image=%s\n' "${MODEL_WORKER_IMAGE:-}" >&2
  fi
  if [[ "$*" == "exec -T postgres "* && "$EMPTY_AUTH" == 1 ]]; then return; fi
  if [[ "$*" == "ps -q --status running "* ]]; then
    printf '%s\n' "$*" >&2
    if [[ "$FAIL_CLIENT_LISTING" == 1 ]]; then return 1; fi
    printf '%s\n' api-container media-container-1 media-container-2 model-container
    return
  fi
  if [[ "$*" == *"alembic upgrade"* ]]; then printf '%s\n' "$*" >&2; fi
  printf '%s\n' "$*"
  if [[ "$*" == *"model_publication --activate" && "$FAIL_ACTIVATION" == 1 ]]; then
    return 1
  fi
  if [[ "$*" == *'alembic upgrade "$1"'* && "$FAIL_MIGRATION" == 1 ]]; then
    return 1
  fi
  if [[ "$*" == *"alembic upgrade 0023_search_body_trgm"* && "$FAIL_ONLINE" == 1 ]]; then
    return 1
  fi
  if [[ "$*" == *"caddy validate"* && "$FAIL_VALIDATION" == 1 ]]; then
    return 1
  fi
}
compose=(record_compose)
'''
        return subprocess.run(
            [BASH, "-s", "--", component, "1" if fail_validation else "0", worker_image,
             "1" if fail_migration else "0", "1" if empty_auth else "0", "1" if fail_activation else "0",
             migration_plan, "1" if fail_online else "0", "1" if fail_lock else "0",
             "1" if fail_client_listing else "0"],
            input=harness + "\n# Serialize deployments" + lock_block + "\n" + tail,
            text=True, encoding="utf-8", capture_output=True, timeout=10, check=False,
        )

    def test_web_and_full_deployments_reload_proxy_after_updating_services(self):
        for component in ("web", "all"):
            with self.subTest(component=component):
                result = self.deploy(component)
                self.assertEqual(result.returncode, 0, result.stderr)
                commands = result.stdout.splitlines()
                validate = next(i for i, command in enumerate(commands) if "caddy validate" in command)
                reload = next(i for i, command in enumerate(commands) if "caddy reload" in command)
                up = next(i for i, command in enumerate(commands) if command.startswith("up -d"))
                self.assertLess(up, validate)
                self.assertLess(validate, reload)
                self.assertLess(reload, commands.index("ps"))

    def test_invalid_proxy_configuration_fails_deployment_without_reloading(self):
        result = self.deploy("web", fail_validation=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("caddy reload", result.stdout)
        self.assertNotIn("\nps\n", result.stdout)

    def test_viewer_activation_uses_fresh_release_image_before_web_starts(self):
        activation = "run --rm --no-deps model-worker python -m wuwa_story_worker.model_publication --activate"
        for component in ("web", "all"):
            with self.subTest(component=component):
                result = self.deploy(component)
                self.assertEqual(result.returncode, 0, result.stderr)
                commands = result.stdout.splitlines()
                activate = commands.index(activation)
                pulls = [i for i, line in enumerate(commands) if line.startswith("pull ")]
                self.assertLess(max(pulls), activate)
                self.assertIn("pull model-worker", commands)
                web = next(i for i, line in enumerate(commands) if line.startswith("up -d ") and "web" in line)
                self.assertLess(activate, web)
                self.assertEqual(commands.count(activation), 1)
                self.assertEqual(result.stderr.count("activation-image=ghcr.io/test/solaris-atlas-models:abc1234"), 2)
                if component == "web":
                    self.assertNotIn("stop ", result.stdout)
                    self.assertNotIn("up -d model-worker", result.stdout)

    def test_activation_failure_prevents_new_web_start(self):
        for component in ("web", "all"):
            with self.subTest(component=component):
                result = self.deploy(component, fail_activation=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn("up -d", result.stdout)
                self.assertNotIn("caddy reload", result.stdout)

    def test_worker_deployment_does_not_touch_the_proxy(self):
        result = self.deploy("snapshot-worker")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("caddy", result.stdout)
        self.assertIn("up -d snapshot-worker asset-worker media-worker projection-worker image-worker audio-worker video-worker model-worker", result.stdout)
        commands = result.stdout.splitlines()
        policy = next(i for i, command in enumerate(commands) if "set_policy" in command)
        startup = next(i for i, command in enumerate(commands) if command.startswith("up -d"))
        self.assertLess(policy, startup)
        self.assertIn("pool-(coordinator|image|audio|video)", commands[policy])
        self.assertIn("character-model", commands[policy])
        pattern = commands[policy].split("wuwa-assets-long-jobs ", 1)[1].split(" ", 1)[0]
        self.assertRegex("wuwa.character-model.v1", pattern)

    def test_infrastructure_waits_for_broker_before_setting_job_policy(self):
        result = self.deploy("infrastructure")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("up -d --wait postgres rabbitmq redis", result.stdout)
        self.assertIn("character-model", result.stdout)
        pattern = result.stdout.split("wuwa-assets-long-jobs ", 1)[1].split(" ", 1)[0]
        self.assertRegex("wuwa.character-model.v1", pattern)

    def test_migrations_release_connections_and_restore_running_replicas(self):
        for fail in (False, True):
            with self.subTest(fail=fail):
                result = self.deploy("api", fail_migration=fail)
                self.assertEqual(result.returncode == 0, not fail, result.stderr)
                self.assertIn("model-worker story-agent cutscene-vision", result.stderr)
                self.assertIn("stop --time 120 api-container media-container-1 media-container-2 model-container", result.stdout)
                self.assertIn('run --rm --no-deps api sh -ec export DATABASE_URL="$MIGRATION_DATABASE_URL"', result.stdout)
                # docker start output is suppressed by the EXIT trap; record to stderr too.
                self.assertIn("start api-container media-container-1 media-container-2 model-container", result.stderr)
                if fail:
                    self.assertNotIn("up -d", result.stdout)

    def test_up_to_date_database_skips_migration_and_client_shutdown(self):
        for component in ("api", "all"):
            with self.subTest(component=component):
                result = self.deploy(component, migration_plan="done:last")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertNotIn("stop --time", result.stdout)
                self.assertNotIn("alembic upgrade", result.stdout)
                self.assertIn("up -d --force-recreate --wait pgbouncer", result.stdout)
                self.assertIn("up -d api", result.stdout)

    def test_pending_and_legacy_migrations_still_stop_and_restore_clients(self):
        for plan in ("done:more", "legacy"):
            for fail in (False, True):
                with self.subTest(plan=plan, fail=fail):
                    result = self.deploy("api", migration_plan=plan, fail_migration=fail)
                    self.assertEqual(result.returncode == 0, not fail, result.stderr)
                    self.assertIn("stop --time 120 api-container", result.stdout)
                    self.assertIn('alembic upgrade "$1"', result.stdout)
                    self.assertIn("sh head", result.stdout)
                    self.assertIn("start api-container", result.stderr)
                    if fail:
                        self.assertNotIn("up -d", result.stdout)

    def test_pooler_is_recreated_after_direct_migrations_before_api_start(self):
        result = self.deploy("api")
        self.assertEqual(result.returncode, 0, result.stderr)
        commands = result.stdout.splitlines()
        migration = next(i for i, line in enumerate(commands) if 'alembic upgrade "$1"' in line)
        pooler = commands.index("up -d --force-recreate --wait pgbouncer")
        self.assertLess(migration, pooler)
        self.assertLess(pooler, commands.index("up -d api"))

    def test_online_index_does_not_stop_clients_during_build(self):
        for plan in ("before:last", "ready:last", "before:more", "ready:more"):
            with self.subTest(plan=plan):
                result = self.deploy("api", migration_plan=plan)
                self.assertEqual(result.returncode, 0, result.stderr)
                commands = result.stdout.splitlines()
                index = next(i for i, line in enumerate(commands) if "alembic upgrade 0023_search_body_trgm" in line)
                stops = [i for i, line in enumerate(commands) if line.startswith("stop --time")]
                self.assertEqual(len(stops), int(plan.startswith("before")) + int(plan.endswith("more")))
                if plan.startswith("before"):
                    self.assertLess(stops[0], index)
                    self.assertIn("sh 0022_tree_index_schema_version", commands[stops[0] + 1])
                    self.assertIn("start api-container", result.stderr)
                    self.assertLess(result.stderr.index("start api-container"),
                                    result.stderr.index("alembic upgrade 0023_search_body_trgm"))
                if plan.endswith("more"):
                    self.assertGreater(stops[-1], index)
                    self.assertIn("sh head", commands[stops[-1] + 1])

    def test_online_failure_stops_deployment_without_stopping_running_clients(self):
        result = self.deploy("api", migration_plan="ready:last", fail_online=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("stop --time", result.stdout)
        self.assertNotIn("up -d", result.stdout)

    def test_previous_migration_failure_restores_clients_and_never_builds_online_index(self):
        result = self.deploy("api", migration_plan="before:last", fail_migration=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("start api-container", result.stderr)
        self.assertNotIn("alembic upgrade 0023_search_body_trgm", result.stdout)

    def test_failure_to_list_clients_never_runs_stopped_migrations(self):
        result = self.deploy("api", migration_plan="before:last", fail_client_listing=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("alembic upgrade", result.stdout)
        self.assertNotIn("up -d", result.stdout)

    def test_unknown_plan_and_competing_deployment_fail_closed(self):
        result = self.deploy("api", migration_plan="unknown")
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("alembic upgrade", result.stdout)
        result = self.deploy("api", fail_lock=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("pull api", result.stdout)

    def test_missing_scram_verifier_fails_before_starting_pooler_or_api(self):
        result = self.deploy("api", empty_auth=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("requires an existing valid SCRAM verifier", result.stderr)
        self.assertNotIn("up -d", result.stdout)

    def test_local_worker_image_is_not_pulled_from_a_registry(self):
        result = self.deploy("snapshot-worker", worker_image="solaris-atlas-worker:workers-test")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("pull snapshot-worker", result.stdout)
        self.assertIn("pull model-worker", result.stdout)
        self.assertIn("up -d snapshot-worker asset-worker media-worker projection-worker image-worker audio-worker video-worker model-worker", result.stdout)

    def test_full_deployment_pulls_and_starts_model_worker(self):
        result = self.deploy("all")
        self.assertEqual(result.returncode, 0, result.stderr)
        for command in ("pull ", "up -d api "):
            line = next(line for line in result.stdout.splitlines() if line.startswith(command) and "model-worker" in line)
            self.assertIn("model-worker", line)


class ModelWorkerConfigurationTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("docker"), "Docker Compose CLI is required")
    def test_model_image_limits_and_replicas_are_independent_of_normal_workers(self):
        with tempfile.TemporaryDirectory() as directory:
            empty = Path(directory) / "empty.env"
            empty.write_text("")
            environment = {**os.environ, "GHCR_OWNER": "test", "IMAGE_TAG": "abc1234",
                           "DOMAIN": "example.invalid", "POSTGRES_USER": "test", "POSTGRES_PASSWORD": "fake",
                           "RABBITMQ_DEFAULT_USER": "test", "RABBITMQ_DEFAULT_PASS": "fake",
                           "WORKER_IMAGE": "solaris-atlas-worker:local", "MODEL_WORKER_IMAGE": "",
                           "WUWA_MODEL_REPLICAS": ""}
            for overrides, expected_image, replicas in [
                ({}, "ghcr.io/test/solaris-atlas-models:abc1234", 1),
                ({"MODEL_WORKER_IMAGE": "ghcr.io/test/custom-models:pinned", "WUWA_MODEL_REPLICAS": "2"},
                 "ghcr.io/test/custom-models:pinned", 2),
            ]:
                with self.subTest(overrides=overrides):
                    result = subprocess.run(
                        ["docker", "compose", "--env-file", str(empty), "-f", str(ROOT / "infrastructure/prod/compose.yml"),
                         "--profile", "workers", "--profile", "local-storage",
                         "config", "--no-env-resolution", "--format", "json"],
                        env={**environment, **overrides}, text=True, capture_output=True, timeout=20, check=False,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    services = json.loads(result.stdout)["services"]
                    model = services["model-worker"]
                    self.assertEqual(model["image"], expected_image)
                    self.assertEqual(model["command"], ["wuwa-story-worker", "run", "--queue", "character_model"])
                    self.assertEqual(model["environment"]["WUWA_QUEUE_CONCURRENCY"], "character_model=1")
                    self.assertEqual(float(model["cpus"]), 1)
                    self.assertEqual(int(model["mem_limit"]), 4 * 1024**3)
                    self.assertEqual(model["deploy"]["replicas"], replicas)
                    self.assertEqual(model["volumes"], services["snapshot-worker"]["volumes"])
                    self.assertEqual(model["depends_on"], services["snapshot-worker"]["depends_on"])
                    for name in ("snapshot-worker", "asset-worker", "media-worker", "image-worker", "audio-worker", "video-worker"):
                        self.assertEqual(services[name]["image"], "solaris-atlas-worker:local")

    def test_published_models_have_immutable_worker_base_and_gate_deployment(self):
        workflow = (ROOT / ".github/workflows/images.yml").read_text()
        models = workflow.split("  model-tools:\n", 1)[1].split("  deploy:\n", 1)[0]
        self.assertIn("needs: build", models)
        self.assertIn("github.event_name == 'push'", models)
        self.assertIn("inputs.component == 'models'", models)
        self.assertIn('image="ghcr.io/${GITHUB_REPOSITORY,,}-models"', models)
        self.assertIn('worker=ghcr.io/${GITHUB_REPOSITORY,,}-worker:$GITHUB_SHA', models)
        self.assertIn('tags="$image:$GITHUB_SHA,$image:${GITHUB_SHA:0:7}"', models)
        self.assertIn('tags="$tags,$image:main"', models)
        self.assertIn("file: packages/worker/Dockerfile.models", models)
        self.assertIn("push: true", models)
        self.assertIn("cache-to: type=gha,mode=max,scope=models", models)
        self.assertIn("needs: [build, model-tools]", workflow.split("  deploy:\n", 1)[1])

    def test_production_validates_model_image_for_both_worker_deployment_groups(self):
        workflow = (ROOT / ".github/workflows/production-deploy.yml").read_text()
        self.assertIn("snapshot-worker) components=(worker models)", workflow)
        self.assertIn("all) components=(api web worker models)", workflow)
        self.assertIn("MODEL_WORKER_IMAGE: ${{ vars.MODEL_WORKER_IMAGE }}", workflow)
        self.assertIn("WUWA_MODEL_REPLICAS: ${{ vars.WUWA_MODEL_REPLICAS }}", workflow)


if __name__ == "__main__":
    unittest.main()
