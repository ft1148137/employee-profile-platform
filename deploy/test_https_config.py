"""Check HTTPS overlays without starting containers or requesting certificates."""

import json
import os
import subprocess
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


if __name__ == "__main__":
    unittest.main()
