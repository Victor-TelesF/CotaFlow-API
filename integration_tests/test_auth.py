"""Rotas e segurança reais sobre PostgreSQL de schema exclusivo por teste."""

from concurrent.futures import ThreadPoolExecutor
from functools import partial
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from pwdlib import PasswordHash
from sqlalchemy import event, func, select, text
from sqlalchemy.exc import IntegrityError

from infrastructure.config import SecuritySettings, Settings
from infrastructure.database.models import UserModel
from infrastructure.database.unit_of_work import SqlAlchemyUnitOfWork
from main import create_app
from presentation.http.dependencies import CurrentUser


@pytest.fixture
def auth_client(session_factory):
    # Lifespan resources remain real; only the UoW factory points at the isolated schema.
    app = create_app(
        Settings(
            _env_file=None,
            DB_HOST="127.0.0.1",
            DB_PORT=1,
            DB_PASSWORD="synthetic-password",
        ),
        SecuritySettings(
            _env_file=None,
            JWT_SECRET_KEY="synthetic-integration-key-at-least-32-characters",
        ),
    )

    @app.get("/test-identity")
    def identity(user: CurrentUser):
        return {"id": str(user.id), "email": user.email}

    with TestClient(app) as client:
        app.state.uow_factory = partial(SqlAlchemyUnitOfWork, session_factory)
        yield client
        assert session_factory.kw["bind"].pool.checkedout() == 0


def test_register_login_and_identity_use_real_hash_and_persisted_user(
    auth_client, session_factory, monkeypatch
):
    passwords = auth_client.app.state.passwords
    real_hash = passwords.hash
    real_verify = passwords.verify

    def hash_without_connection(password):
        assert session_factory.kw["bind"].pool.checkedout() == 0
        return real_hash(password)

    def verify_without_connection(password, password_hash):
        assert session_factory.kw["bind"].pool.checkedout() == 0
        return real_verify(password, password_hash)

    monkeypatch.setattr(passwords, "hash", hash_without_connection)
    monkeypatch.setattr(passwords, "verify", verify_without_connection)
    password = " synthetic-password-with-spaces "
    response = auth_client.post(
        "/api/v1/auth/register",
        json={"email": " Alice@Example.COM ", "password": password},
    )
    assert response.status_code == 201
    identity = response.json()
    assert set(identity) == {"id", "email"}
    assert identity["email"] == "alice@example.com"
    user_id = UUID(identity["id"])
    with session_factory() as session:
        row = session.get(UserModel, user_id)
        original_hash = row.password_hash
        assert row.email == "alice@example.com"
        assert original_hash.startswith("$argon2id$")
        assert password not in original_hash
        assert PasswordHash.recommended().verify(password, original_hash)
        assert not PasswordHash.recommended().verify(password.strip(), original_hash)
    assert session_factory.kw["bind"].pool.checkedout() == 0

    login = auth_client.post(
        "/api/v1/auth/login", json={"email": "ALICE@example.com", "password": password}
    )
    assert login.status_code == 200
    assert set(login.json()) == {"access_token", "token_type"}
    assert login.json()["token_type"] == "bearer"
    assert login.headers["cache-control"] == "no-store"
    token = login.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    resolved = auth_client.get("/test-identity", headers=headers)
    assert resolved.status_code == 200
    assert resolved.json() == identity
    for result in (response, login, resolved):
        assert password not in result.text
        assert original_hash not in result.text

    # The signed token alone cannot authenticate a UUID that no longer exists.
    with session_factory.begin() as session:
        session.delete(session.get(UserModel, user_id))
    deleted = auth_client.get("/test-identity", headers=headers)
    assert deleted.status_code == 401
    assert deleted.json()["code"] == "invalid_token"


def test_duplicate_email_rolls_back_and_does_not_overwrite_password(
    auth_client, session_factory
):
    first = auth_client.post(
        "/api/v1/auth/register",
        json={"email": "alice@example.com", "password": "first-password"},
    )
    assert first.status_code == 201
    with session_factory() as session:
        original_hash = session.get(UserModel, UUID(first.json()["id"])).password_hash
    duplicate = auth_client.post(
        "/api/v1/auth/register",
        json={"email": " ALICE@EXAMPLE.COM ", "password": "other-password"},
    )
    assert duplicate.status_code == 409
    assert duplicate.json() == {
        "code": "email_already_registered",
        "message": "Email already registered",
    }
    assert "alice" not in duplicate.text.lower()
    assert "other-password" not in duplicate.text
    assert "uq_users_email" not in duplicate.text
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(UserModel)) == 1
        row = session.get(UserModel, UUID(first.json()["id"]))
        assert row.password_hash == original_hash
    for password, expected in [("first-password", 200), ("other-password", 401)]:
        response = auth_client.post(
            "/api/v1/auth/login",
            json={"email": "alice@example.com", "password": password},
        )
        assert response.status_code == expected


def test_wrong_password_missing_email_and_corrupt_hash_are_same_rejection(
    auth_client, session_factory
):
    registered = auth_client.post(
        "/api/v1/auth/register",
        json={"email": "alice@example.com", "password": "correct-password"},
    )
    assert registered.status_code == 201
    responses = []
    for email, password in [
        ("alice@example.com", "incorrect-password"),
        ("alice@example.com", "wrong"),
        ("missing@example.com", "correct-password"),
    ]:
        response = auth_client.post(
            "/api/v1/auth/login", json={"email": email, "password": password}
        )
        responses.append(response.json())
        assert response.status_code == 401
        assert response.headers["www-authenticate"] == "Bearer"
    with session_factory.begin() as session:
        assert session.scalar(select(func.count()).select_from(UserModel)) == 1
        session.get(
            UserModel, UUID(registered.json()["id"])
        ).password_hash = "corrupt-synthetic-hash"
    corrupt = auth_client.post(
        "/api/v1/auth/login",
        json={"email": "alice@example.com", "password": "correct-password"},
    )
    assert corrupt.status_code == 401
    assert all(body == corrupt.json() for body in responses)
    assert corrupt.json() == {
        "code": "invalid_credentials",
        "message": "Authentication failed",
    }


def test_two_users_resolve_own_identity_despite_client_supplied_owner(auth_client):
    users = []
    for name in ("alice", "bob"):
        body = {
            "email": f"{name}@example.com",
            "password": f"{name}-synthetic-password",
        }
        registration = auth_client.post("/api/v1/auth/register", json=body)
        assert registration.status_code == 201
        login = auth_client.post("/api/v1/auth/login", json=body)
        assert login.status_code == 200
        users.append((registration.json(), login.json()["access_token"]))
    for (identity, token), (other, _) in [(users[0], users[1]), (users[1], users[0])]:
        response = auth_client.get(
            f"/test-identity?user_id={other['id']}",
            headers={"Authorization": f"Bearer {token}", "X-User-Id": other["id"]},
        )
        assert response.status_code == 200
        assert response.json() == identity


def test_concurrent_registration_has_one_winner_and_one_safe_conflict(
    auth_client, session_factory
):
    engine = session_factory.kw["bind"]
    barrier = Barrier(2)

    def coordinate(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO users"):
            barrier.wait(timeout=5)

    def register(password):
        return auth_client.post(
            "/api/v1/auth/register",
            json={"email": "race@example.com", "password": password},
        )

    event.listen(engine, "before_cursor_execute", coordinate)
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(register, value)
                for value in ("first-password", "second-password")
            ]
            results = [future.result(timeout=15) for future in futures]
    finally:
        event.remove(engine, "before_cursor_execute", coordinate)
    assert sorted(result.status_code for result in results) == [201, 409]
    winner = next(result.json() for result in results if result.status_code == 201)
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(UserModel)) == 1
        assert str(session.scalar(select(UserModel)).id) == winner["id"]
    assert engine.pool.checkedout() == 0


def test_other_integrity_errors_are_not_mislabeled_as_duplicate_email(session_factory):
    from application.types import User

    # A different unique constraint (UUID) must remain an infrastructure failure.
    user = User("alice@example.com", "synthetic-hash")
    with SqlAlchemyUnitOfWork(session_factory) as uow:
        uow.users.add(user)
        uow.commit()
    with (
        pytest.raises(IntegrityError) as caught,
        SqlAlchemyUnitOfWork(session_factory) as uow,
    ):
        uow.users.add(User("bob@example.com", "other-hash", id=user.id))
        uow.commit()
    assert caught.value.orig.diag.constraint_name == "users_pkey"
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(UserModel)) == 1
        assert session.get(UserModel, user.id).email == user.email


def test_raw_postgresql_email_constraint_still_rejects_duplicates(session_factory):
    with session_factory.begin() as session:
        session.add(
            UserModel(
                id=uuid4(), email="alice@example.com", password_hash="synthetic-hash"
            )
        )
    with pytest.raises(IntegrityError) as caught, session_factory.begin() as session:
        session.add(
            UserModel(id=uuid4(), email="alice@example.com", password_hash="other-hash")
        )
    assert caught.value.orig.diag.constraint_name == "uq_users_email"
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(UserModel)) == 1


def test_real_database_failure_does_not_expose_sql_or_credentials(
    auth_client, session_factory
):
    with session_factory.begin() as session:
        session.execute(text("ALTER TABLE users RENAME TO unavailable_users"))
    response = auth_client.post(
        "/api/v1/auth/login",
        json={"email": "private@example.com", "password": "private-password"},
    )
    assert response.status_code == 503
    assert response.json() == {
        "code": "persistence_unavailable",
        "message": "Persistence unavailable",
    }
    for private in (
        "private@example.com",
        "private-password",
        "SELECT",
        "UndefinedTable",
        "password_hash",
    ):
        assert private not in response.text
