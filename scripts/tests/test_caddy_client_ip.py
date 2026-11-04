"""Live Caddy -> Uvicorn checks. Run with server Python and CADDY_BINARY set.

No Docker, database or Cloudflare credentials are needed. Only the trusted-peer
list/listening addresses/upstream are substituted in the production Caddyfile.
"""

import json
import os
import re
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[2]
CADDY = os.environ.get("CADDY_BINARY") or shutil.which("caddy")


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


async def echo_client(scope, receive, send):
    body = json.dumps({"client": scope["client"][0], "scheme": scope["scheme"],
                       "path": scope["path"]}).encode()
    await send({"type": "http.response.start", "status": 200,
                "headers": [(b"content-type", b"application/json")]})
    await send({"type": "http.response.body", "body": body})


@unittest.skipUnless(CADDY, "Set CADDY_BINARY or install caddy to run live proxy tests")
class ClientIPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import uvicorn

        cls.backend_port = free_port()
        cls.backend = uvicorn.Server(uvicorn.Config(
            echo_client, host="127.0.0.1", port=cls.backend_port, lifespan="off",
            proxy_headers=True, forwarded_allow_ips="*", log_level="error"))
        cls.thread = threading.Thread(target=cls.backend.run, daemon=True)
        cls.thread.start()
        for _ in range(100):
            if cls.backend.started:
                return
            time.sleep(0.05)
        raise RuntimeError("Uvicorn did not start")

    @classmethod
    def tearDownClass(cls):
        cls.backend.should_exit = True
        cls.thread.join(timeout=5)

    def check_proxy(self, trusted, cases):
        port = free_port()
        config = (ROOT / "infrastructure/prod/Caddyfile").read_text(encoding="utf-8")
        if trusted:
            # Simulate the Cloudflare TCP peer locally; production trusts no loopback/private ranges.
            config = re.sub(r"trusted_proxies static [^\n]+", "trusted_proxies static 127.0.0.1/32", config)
        config = config.replace("api.{$DOMAIN}", f"http://api.atlas.test:{port}")
        config = config.replace("{$DOMAIN}", f"http://atlas.test:{port}")
        config = config.replace("api:8000", f"127.0.0.1:{self.backend_port}")
        config = config.replace("web:3000", f"127.0.0.1:{self.backend_port}")
        config = config.replace("{\n", "{\n\tadmin off\n\tpersist_config off\n", 1)
        with tempfile.TemporaryDirectory(prefix="caddy-client-ip-") as directory:
            path = Path(directory) / "Caddyfile"
            path.write_text(config, encoding="utf-8")
            with (Path(directory) / "caddy.log").open("w+") as log:
                process = subprocess.Popen([CADDY, "run", "--config", str(path), "--adapter", "caddyfile"],
                    stdout=log, stderr=log,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
                try:
                    for _ in range(100):
                        if process.poll() is not None:
                            log.seek(0)
                            self.fail(log.read())
                        try:
                            with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                                break
                        except OSError:
                            time.sleep(0.05)
                    else:
                        self.fail("Caddy did not start")
                    for headers, expected in cases:
                        for host, route, upstream_path in [
                            ("atlas.test", "/api/chat", "/chat"),
                            ("api.atlas.test", "/chat", "/chat"),
                            ("atlas.test", "/console/", "/console/"),
                            ("atlas.test", "/", "/"),
                        ]:
                            with self.subTest(trusted=trusted, host=host, route=route, headers=headers):
                                request = Request(f"http://127.0.0.1:{port}{route}",
                                    headers={"Host": host, **headers})
                                with urlopen(request, timeout=5) as response:
                                    result = json.load(response)
                                self.assertEqual(result["client"], expected)
                                self.assertEqual(result["path"], upstream_path)
                                # Only the trusted edge may supply the visitor's HTTPS scheme.
                                self.assertEqual(result["scheme"],
                                    headers.get("X-Forwarded-Proto", "http") if trusted else "http")
                finally:
                    process.terminate()
                    process.wait(timeout=10)

    def test_cloudflare_peer_passes_ipv4_and_ipv6_ignoring_spoofed_xff(self):
        self.check_proxy(True, [
            ({"CF-Connecting-IP": "198.51.100.23", "X-Forwarded-For": "192.0.2.66",
              "X-Forwarded-Proto": "https"}, "198.51.100.23"),
            ({"CF-Connecting-IP": "2001:db8::23", "X-Forwarded-For": "192.0.2.66"}, "2001:db8::23"),
        ])

    def test_direct_peer_cannot_spoof_cloudflare_or_forwarded_headers(self):
        self.check_proxy(False, [
            ({"CF-Connecting-IP": "198.51.100.23", "X-Forwarded-For": "192.0.2.66",
              "X-Forwarded-Proto": "https", "X-Real-IP": "192.0.2.66"}, "127.0.0.1"),
        ])

    def test_missing_or_invalid_cloudflare_ip_does_not_fall_back_to_spoofed_xff(self):
        self.check_proxy(True, [
            ({"X-Forwarded-For": "192.0.2.66"}, "127.0.0.1"),
            ({"CF-Connecting-IP": "invalid", "X-Forwarded-For": "192.0.2.66"}, "127.0.0.1"),
        ])


if __name__ == "__main__":
    unittest.main()
