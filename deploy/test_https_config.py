"""Check HTTPS overlays without starting containers or requesting certificates."""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Any


class HTTPSConfigTest(unittest.TestCase):
    def config(self, mode: str, project: str) -> dict[str, Any]:
        result = subprocess.run(
            [
                "docker",
                "compose",
                "--env-file",
                "/dev/null",
                "-f",
                "compose.yml",
                "-f",
                "deploy/compose.prod.yml",
                "-f",
                f"deploy/compose.{mode}.yml",
                "config",
                "--format",
                "json",
            ],
            cwd=Path(__file__).resolve().parents[1],
            env={
                **os.environ,
                "COMPOSE_PROJECT_NAME": project,
                "PROJECT_NAME": "HTTPS test",
                "SECRET_KEY": "synthetic-test-secret",
                "POSTGRES_PASSWORD": "synthetic-test-password",
                "PI_SERVICE_TOKEN": "synthetic-test-token",
                "DOMAIN": "profiles.example.com",
                "ACME_EMAIL": "admin@example.com",
                "HTTP_PORT": "80",
            },
            check=True,
            capture_output=True,
            text=True,
        )
        return json.loads(result.stdout)

    def test_project_isolation_and_streaming(self) -> None:
        for mode in ("https-selfsigned", "https-letsencrypt"):
            for project in ("nere781", "nere781-review"):
                with self.subTest(mode=mode, project=project):
                    services = self.config(mode, project)["services"]
                    self.assertIn(
                        f"--providers.docker.constraints=Label(`com.docker.compose.project`, `{project}`)",
                        services["proxy"]["command"],
                    )
                    backend = services["backend"]
                    self.assertFalse(backend.get("ports"))
                    self.assertEqual(
                        backend["environment"]["FRONTEND_HOST"],
                        "https://profiles.example.com",
                    )
                    labels = backend["labels"]
                    service = f"traefik.http.services.{project}-backend"
                    router = f"traefik.http.routers.{project}-backend-https"
                    self.assertEqual(
                        labels[f"{service}.loadbalancer.server.port"], "8000"
                    )
                    self.assertEqual(
                        labels[
                            f"{service}.loadbalancer.responseforwarding.flushinterval"
                        ],
                        "-1ms",
                    )
                    self.assertEqual(labels[f"{router}.service"], f"{project}-backend")
                    self.assertEqual(labels[f"{router}.tls"], "true")
                    self.assertTrue(
                        all(
                            project in key
                            for key in labels
                            if key.startswith("traefik.http.")
                        )
                    )

    def test_acme_http_challenge_and_storage(self) -> None:
        config = self.config("https-letsencrypt", "nere781")
        proxy = config["services"]["proxy"]
        for flag in (
            "--certificatesresolvers.le.acme.email=admin@example.com",
            "--certificatesresolvers.le.acme.storage=/certificates/acme.json",
            "--certificatesresolvers.le.acme.httpchallenge.entrypoint=http",
            "--entrypoints.http.http.redirections.entrypoint.to=https",
        ):
            self.assertIn(flag, proxy["command"])
        self.assertFalse(any("tlschallenge" in flag for flag in proxy["command"]))
        self.assertTrue(
            any(
                port["target"] == 80 and port["published"] == "80"
                for port in proxy["ports"]
            )
        )
        volume = next(
            volume for volume in proxy["volumes"] if volume["target"] == "/certificates"
        )
        self.assertEqual(volume["type"], "volume")
        self.assertEqual(
            config["volumes"][volume["source"]]["name"], "nere781_traefik-certificates"
        )


class InstallPortTest(unittest.TestCase):
    def test_acme_port_validation(self) -> None:
        cases = [
            ("https-letsencrypt", False, "8080", False, False, False),
            ("https-letsencrypt", False, "80", True, False, False),
            ("https-letsencrypt", False, "80", False, False, True),
            ("https-letsencrypt", True, "8080", False, False, False),
            ("https-letsencrypt", True, "80", True, False, False),
            ("https-letsencrypt", True, "80", False, False, True),
            ("https-letsencrypt", True, "80", True, True, True),
            ("http", False, "18781", False, False, True),
            ("https-selfsigned", False, "18781", False, False, True),
            ("http", True, "18781", False, False, True),
            ("https-selfsigned", True, "18781", False, False, True),
        ]
        for mode, existing, port, busy, own_proxy, succeeds in cases:
            with (
                self.subTest(
                    mode=mode,
                    existing=existing,
                    port=port,
                    busy=busy,
                    own_proxy=own_proxy,
                ),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory)
                source = Path(__file__).resolve().parents[1]
                # Keep real host listeners out of the test; lsof supplies port state.
                (root / "install.sh").write_text(
                    (source / "install.sh")
                    .read_text()
                    .replace("/dev/tcp/127.0.0.1/$1", "/dev/null/disabled-probe")
                )
                (root / "deploy").mkdir()
                shutil.copy(source / "deploy/common.sh", root / "deploy")
                binaries = root / "bin"
                binaries.mkdir()
                docker = binaries / "docker"
                docker.write_text("""#!/bin/sh
echo "$*" >> docker-calls
case "$*" in
  'compose version --short') echo 2.40.0 ;;
  'compose config --environment') cat .env ;;
  'compose port proxy 80')
    [ "$OWN_PROXY" = true ] || exit 1
    echo 0.0.0.0:80 ;;
esac
""")
                # Stub port probes on both macOS and Linux; no Docker daemon is used.
                lsof = binaries / "lsof"
                lsof.write_text("""#!/bin/sh
[ "$BUSY_PORT" = true ] && [ "$2" = -iTCP:80 ]
""")
                curl = binaries / "curl"
                curl.write_text("#!/bin/sh\nexit 0\n")
                ss = binaries / "ss"
                ss.write_text("#!/bin/sh\nexit 0\n")
                for binary in (docker, lsof, curl, ss):
                    binary.chmod(0o755)
                env = {
                    **os.environ,
                    "PATH": f"{binaries}:{os.environ['PATH']}",
                    "EPP_ACCESS": mode,
                    "EPP_ADDRESS": "profiles.example.com",
                    "EPP_ACME_EMAIL": "admin@example.com",
                    "EPP_HTTP_PORT": port,
                    "COMPOSE_PROJECT_NAME": "nere781",
                    "BUSY_PORT": str(busy).lower(),
                    "OWN_PROXY": str(own_proxy).lower(),
                }
                if existing:
                    (root / ".env").write_text(
                        "COMPOSE_FILE=compose.yml:deploy/compose.prod.yml"
                        + (f":deploy/compose.{mode}.yml" if mode != "http" else "")
                        + "\n"
                        f"HTTP_PORT={port}\nFRONTEND_HOST=https://profiles.example.com\n"
                        "DOMAIN=profiles.example.com\nPI_SERVICE_TOKEN=synthetic-token\n"
                    )
                    # Re-running uses the saved configuration, not new input defaults.
                    env["EPP_ACCESS"], env["EPP_HTTP_PORT"] = "http", "80"
                result = subprocess.run(
                    ["bash", "install.sh", "--non-interactive"],
                    cwd=root,
                    env=env,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                calls = (root / "docker-calls").read_text()
                self.assertEqual(result.returncode == 0, succeeds, result.stderr)
                self.assertEqual("compose build\n" in calls, succeeds)
                if not succeeds:
                    self.assertIn("域名证书", result.stderr)
                    self.assertNotIn("例如 8080", result.stderr)
                    self.assertNotIn("compose up", calls)
                    if not existing:
                        self.assertFalse((root / ".env").exists())


if __name__ == "__main__":
    unittest.main()
