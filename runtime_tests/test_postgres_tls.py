"""Verify driver TLS with synthetic certificates on the disposable server only."""

import json
import os
import subprocess
from time import monotonic, sleep
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from infrastructure.config import Settings
from infrastructure.database.session import Database
from runtime_tests.test_startup import docker


def certificate(tmp_path, name):
    cert, key = tmp_path / f"{name}.crt", tmp_path / f"{name}.key"
    result = subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            "1",
            "-subj",
            "/CN=cotaflow-test.local",
            "-addext",
            "subjectAltName=DNS:cotaflow-test.local",
            "-keyout",
            str(key),
            "-out",
            str(cert),
        ],
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, "Could not generate synthetic TLS certificate"
    return cert, key


def test_driver_verifies_tls_ca_hostname_and_rejects_invalid_mode(
    postgres_engine, tmp_path, monkeypatch
):
    container = os.environ.get("TEST_RUNTIME_DATABASE_CONTAINER")
    if not container:
        raise pytest.UsageError(
            "TLS runtime check requires disposable TEST_RUNTIME_DATABASE_CONTAINER"
        )
    # Confirm the supplied container owns the explicitly disposable database.
    url = postgres_engine.url
    assert (
        docker(
            "exec",
            container,
            "psql",
            "-U",
            url.username,
            "-d",
            url.database,
            "-Atc",
            "SELECT current_database()",
        )
        == url.database
    )
    cert, key = certificate(tmp_path, "server")
    untrusted, _ = certificate(tmp_path, "untrusted")
    prefix = f"/tmp/cotaflow-tls-{uuid4().hex}"
    for path, target in ((cert, f"{prefix}.crt"), (key, f"{prefix}.key")):
        uploaded = subprocess.run(
            [
                "docker",
                "exec",
                "-i",
                "--user",
                "root",
                container,
                "sh",
                "-c",
                f"umask 077; cat > {target}; chown postgres:postgres {target}",
            ],
            input=path.read_bytes(),
            capture_output=True,
            timeout=10,
            check=False,
        )
        assert uploaded.returncode == 0, "Could not upload synthetic TLS material"
    previous = {}
    with postgres_engine.connect().execution_options(
        isolation_level="AUTOCOMMIT"
    ) as admin:
        for name in ("ssl", "ssl_cert_file", "ssl_key_file"):
            previous[name] = admin.scalar(text(f"SHOW {name}"))
        try:
            for name, value in {
                "ssl": "on",
                "ssl_cert_file": f"{prefix}.crt",
                "ssl_key_file": f"{prefix}.key",
            }.items():
                admin.exec_driver_sql(f"ALTER SYSTEM SET {name} = '{value}'")
            assert admin.scalar(text("SELECT pg_reload_conf()"))
            monkeypatch.setenv("PGSSLMODE", "verify-full")
            monkeypatch.setenv("PGSSLROOTCERT", str(cert))
            monkeypatch.setenv("PGHOSTADDR", url.host)
            monkeypatch.setenv("PGCONNECT_TIMEOUT", "3")
            settings = Settings(
                _env_file=None,
                DB_HOST="cotaflow-test.local",
                DB_PORT=url.port,
                DB_NAME=url.database,
                DB_USER=url.username,
                DB_PASSWORD=url.password,
            )
            database = Database(settings)
            try:
                deadline = monotonic() + 5
                while True:
                    try:
                        with database.engine.connect() as connection:
                            tls = connection.execute(
                                text(
                                    "SELECT ssl, version, bits FROM pg_stat_ssl WHERE pid=pg_backend_pid()"
                                )
                            ).one()
                        break
                    except OperationalError:
                        if monotonic() >= deadline:
                            raise
                        sleep(0.05)  # Wait for the observed server reload.
                assert tls.ssl is True
                assert tls.version in {"TLSv1.2", "TLSv1.3"}
                assert tls.bits >= 128
            finally:
                database.dispose()
            image = os.environ.get("TEST_RUNTIME_IMAGE")
            if not image:
                raise pytest.UsageError(
                    "TLS check in the runtime image requires TEST_RUNTIME_IMAGE"
                )
            # Exercise the image's actual musl/libpq/OpenSSL stack, not only the host driver.
            script = """
import json
import tempfile
from pathlib import Path
import psycopg
import sys

values = json.load(sys.stdin)
with tempfile.TemporaryDirectory() as directory:
    root = Path(directory) / 'root.crt'
    root.write_text(values.pop('certificate'))
    wrong_root = Path(directory) / 'wrong.crt'
    wrong_root.write_text(values.pop('wrong_certificate'))
    with psycopg.connect(**values, sslmode='verify-full', sslrootcert=str(root)) as connection:
        tls = connection.execute('SELECT ssl, version, bits FROM pg_stat_ssl WHERE pid=pg_backend_pid()').fetchone()
        assert tls[0] is True and tls[1] in {'TLSv1.2', 'TLSv1.3'} and tls[2] >= 128
    for override, message in [
        ({'sslrootcert': str(wrong_root)}, 'certificate verify failed'),
        ({'host': 'wrong-host.test'}, 'does not match host name'),
    ]:
        try:
            psycopg.connect(**(values | {'sslmode': 'verify-full', 'sslrootcert': str(root)} | override))
        except psycopg.OperationalError as error:
            assert message in str(error)
        else:
            raise AssertionError('Runtime accepted invalid server identity')
    print('Runtime verify-full CA/hostname checks passed')
"""
            values = {
                "host": "cotaflow-test.local",
                "hostaddr": "127.0.0.1",
                "port": 5432,
                "dbname": url.database,
                "user": url.username,
                "password": url.password,
                "connect_timeout": 3,
                "certificate": cert.read_text(),
                "wrong_certificate": untrusted.read_text(),
            }
            result = subprocess.run(
                [
                    "docker",
                    "run",
                    "--rm",
                    "-i",
                    "--network",
                    f"container:{container}",
                    "--entrypoint",
                    "python",
                    image,
                    "-c",
                    script,
                ],
                input=json.dumps(values),
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
            assert result.returncode == 0, result.stderr
            assert (
                result.stdout.strip() == "Runtime verify-full CA/hostname checks passed"
            )
            for mode, root, host, message in (
                ("invalid-mode", cert, "cotaflow-test.local", "invalid sslmode"),
                (
                    "verify-full",
                    untrusted,
                    "cotaflow-test.local",
                    "certificate verify failed",
                ),
                ("verify-full", cert, "wrong-host.test", "does not match host name"),
            ):
                monkeypatch.setenv("PGSSLMODE", mode)
                monkeypatch.setenv("PGSSLROOTCERT", str(root))
                invalid = Database(settings.model_copy(update={"DB_HOST": host}))
                try:
                    with (
                        pytest.raises(OperationalError) as caught,
                        invalid.engine.connect(),
                    ):
                        pytest.fail("Driver accepted invalid TLS configuration")
                    assert message in str(caught.value)
                finally:
                    invalid.dispose()
        finally:
            for name, value in previous.items():
                escaped = value.replace("'", "''")
                admin.exec_driver_sql(f"ALTER SYSTEM SET {name} = '{escaped}'")
            admin.exec_driver_sql("SELECT pg_reload_conf()")
