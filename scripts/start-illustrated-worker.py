#!/usr/bin/env python3
"""Start the isolated illustrated-story consumer without restarting any live worker."""
import argparse
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path


def docker(*args):
    return subprocess.check_output(["docker", *args], text=True).strip()


def main(args):
    if not re.fullmatch(r"[a-zA-Z0-9_.-]+", args.name):
        raise ValueError("Invalid container name")
    source = json.loads(docker("inspect", args.source_container))[0]
    if not source["State"]["Running"]:
        raise ValueError("Source worker is not running")
    networks = list(source["NetworkSettings"]["Networks"])
    if len(networks) != 1:
        raise ValueError("Expected one explicitly inherited worker network")
    # Pull only the reviewed immutable commit tag, then run by resolved image ID.
    if not re.search(r":[0-9a-f]{40}$", args.image):
        raise ValueError("Image must use an immutable full commit tag")
    command = ["run", "--detach", "--name", args.name, "--restart", "unless-stopped",
               "--network", networks[0], "--volumes-from", source["Id"],
               "--memory", "3g", "--cpus", "2"]
    report = {"source_container_id": source["Id"], "source_image": source["Image"],
              "new_container": args.name, "image_tag": args.image,
              "queue": "wuwa.illustrated-backgrounds.v1", "apply": args.apply}
    if args.apply:
        if os.name != "posix":
            raise ValueError("Run on the deployment Linux host")
        subprocess.run(["docker", "pull", args.image], check=True)
        image_id = docker("image", "inspect", "--format", "{{.Id}}", args.image)
        env = source["Config"]["Env"]
        if any("\n" in value or "\r" in value for value in env):
            raise ValueError("Multiline environment values cannot be safely inherited")
        # Credentials stay in a private temporary file and are never printed.
        with tempfile.NamedTemporaryFile(mode="w", prefix="illustrated-env-", delete=False) as file:
            path = Path(file.name)
            os.chmod(path, 0o600)
            file.write("\n".join(env) + "\nWUWA_RIGLYPH_ENABLED=true\n")
        try:
            identity = docker(*command, "--env-file", str(path), image_id,
                              "wuwa-story-worker", "run", "--queue", "illustrated_backgrounds")
        finally:
            path.unlink(missing_ok=True)
        report.update(container_id=identity, image_id=image_id)
        current = json.loads(docker("inspect", source["Id"]))[0]
        if current["State"]["Pid"] != source["State"]["Pid"]:
            raise RuntimeError("Source worker identity changed during isolated startup")
    args.receipt.parent.mkdir(parents=True, exist_ok=True)
    args.receipt.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-container", required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    main(parser.parse_args())
