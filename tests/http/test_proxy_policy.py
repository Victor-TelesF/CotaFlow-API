"""Uvicorn instalado, ASGI e TCP local: fronteira confiável explícita."""

import json
import os
import socket
import subprocess
from pathlib import Path
from threading import Event, Thread

import httpx2
import pytest
import uvicorn
from fastapi import Request
from fastapi.testclient import TestClient

from infrastructure.config import AuthSettings, MarketSettings, Settings
from main import create_app


def proxy_app(security_settings):
    app = create_app(
        Settings(_env_file=None, DB_PASSWORD="synthetic", DB_PORT=1),
        security_settings,
        MarketSettings(_env_file=None),
        AuthSettings(_env_file=None, AUTH_LOGIN_PER_MINUTE=2),
    )

    @app.get("/test-peer")
    async def peer(request: Request):
        return {"peer": request.client.host, "scheme": request.url.scheme}

    return app


def test_untrusted_forwarding_cannot_change_ip_quota(security_settings):
    config = uvicorn.Config(
        proxy_app(security_settings),
        proxy_headers=True,
        forwarded_allow_ips="",
        log_config=None,
    )
    config.load()
    with TestClient(config.loaded_app, client=("192.0.2.10", 1234)) as client:
        for fake in ("203.0.113.1", "203.0.113.2"):
            assert (
                client.post(
                    "/api/v1/auth/login", json={}, headers={"X-Forwarded-For": fake}
                ).status_code
                == 422
            )
        response = client.post(
            "/api/v1/auth/login",
            json={},
            headers={
                "X-Forwarded-For": "203.0.113.3",
                "Forwarded": "for=203.0.113.4",
                "X-Real-IP": "203.0.113.5",
            },
        )
        assert response.status_code == 429
        assert client.get(
            "/test-peer",
            headers={"X-Forwarded-For": "203.0.113.3", "X-Forwarded-Proto": "https"},
        ).json() == {"peer": "192.0.2.10", "scheme": "http"}


def test_verified_proxy_uses_last_untrusted_peer_and_ignores_spoofed_prefix(
    security_settings,
):
    config = uvicorn.Config(
        proxy_app(security_settings),
        proxy_headers=True,
        forwarded_allow_ips="192.0.2.10,192.0.2.11",
        log_config=None,
    )
    config.load()
    with TestClient(config.loaded_app, client=("192.0.2.10", 1234)) as client:
        for fake in ("198.51.100.1", "198.51.100.2"):
            headers = {"X-Forwarded-For": f"{fake}, 203.0.113.1, 192.0.2.11"}
            assert (
                client.post("/api/v1/auth/login", json={}, headers=headers).status_code
                == 422
            )
        assert (
            client.post(
                "/api/v1/auth/login",
                json={},
                headers={"X-Forwarded-For": "198.51.100.3, 203.0.113.1, 192.0.2.11"},
            ).status_code
            == 429
        )
        assert (
            client.post(
                "/api/v1/auth/login",
                json={},
                headers={"X-Forwarded-For": "203.0.113.2"},
            ).status_code
            == 422
        )
        assert client.get(
            "/test-peer",
            headers={
                "X-Forwarded-For": "203.0.113.1, 192.0.2.11",
                "X-Forwarded-Proto": "https",
            },
        ).json() == {"peer": "203.0.113.1", "scheme": "https"}
    # A direct untrusted connection cannot claim to be behind the verified proxy.
    with TestClient(config.loaded_app, client=("192.0.2.12", 1234)) as client:
        assert (
            client.get(
                "/test-peer", headers={"X-Forwarded-For": "192.0.2.10, 203.0.113.1"}
            ).json()["peer"]
            == "192.0.2.12"
        )


@pytest.mark.parametrize("trusted", ["", "127.0.0.1"])
def test_real_uvicorn_tcp_respects_proxy_setting_and_reuses_connection(
    security_settings, trusted
):
    ready = Event()

    class ReadyServer(uvicorn.Server):
        async def startup(self, sockets=None):
            await super().startup(sockets)
            ready.set()

    app = proxy_app(security_settings)
    config = uvicorn.Config(
        app,
        proxy_headers=True,
        forwarded_allow_ips=trusted,
        log_config=None,
        access_log=False,
        lifespan="on",
    )
    server = ReadyServer(config)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen(128)
        port = sock.getsockname()[1]
        thread = Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
        thread.start()
        try:
            assert ready.wait(timeout=5), "Local Uvicorn startup did not complete"
            with httpx2.Client(
                base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=3
            ) as client:
                result = client.get(
                    "/test-peer", headers={"X-Forwarded-For": "203.0.113.1"}
                )
                assert result.json()["peer"] == (
                    "203.0.113.1" if trusted else "127.0.0.1"
                )
                # Early body rejection leaves the server able to answer subsequent requests.
                assert (
                    client.post("/api/v1/auth/login", content=b"x" * 16385).status_code
                    == 413
                )
                assert client.get("/health").json() == {"status": "ok"}
                for fake, expected in [
                    ("203.0.113.2", 422),
                    ("203.0.113.3", 422 if trusted else 429),
                ]:
                    assert (
                        client.post(
                            "/api/v1/auth/login",
                            json={},
                            headers={"X-Forwarded-For": fake},
                        ).status_code
                        == expected
                    )
        finally:
            server.should_exit = True
            thread.join(timeout=5)
            assert not thread.is_alive()
    assert not hasattr(app.state, "auth_limits")


def test_image_command_passes_only_explicit_proxy_peers(tmp_path):
    # Execute the actual CMD shell with harmless stubs, no build/migration/database.
    dockerfile = Path(__file__).resolve().parents[2] / "Dockerfile"
    command = json.loads(
        next(
            line[4:]
            for line in dockerfile.read_text().splitlines()
            if line.startswith("CMD ")
        )
    )
    for program in ("alembic", "uvicorn"):
        stub = tmp_path / program
        stub.write_text(
            "#!/bin/sh\n"
            + ("exit 0\n" if program == "alembic" else "printf '%s\\n' \"$@\"\n")
        )
        stub.chmod(0o700)
    for peers in ("", "192.0.2.1,192.0.2.0/24"):
        result = subprocess.run(
            ["/bin/sh", *command[1:]],
            env=os.environ
            | {"PATH": str(tmp_path), "PORT": "12345", "FORWARDED_ALLOW_IPS": peers},
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        assert result.returncode == 0, result.stderr
        args = result.stdout.splitlines()
        assert args == [
            "main:app",
            "--host",
            "0.0.0.0",
            "--port",
            "12345",
            "--workers",
            "1",
            "--proxy-headers",
            "--forwarded-allow-ips",
            peers,
        ]
