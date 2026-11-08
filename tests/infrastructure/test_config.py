import pytest
from pydantic import ValidationError


def test_password_is_hidden_in_settings_representations(config_module):
    password = "synthetic-password@:/?#%"
    settings = config_module.Settings(_env_file=None, DB_PASSWORD=password)

    for representation in (
        str(settings),
        repr(settings),
        repr(settings.model_dump()),
        settings.model_dump_json(),
    ):
        assert password not in representation

    # Masking must preserve the exact value passed to the database driver.
    assert settings.database_url.password == password
    assert settings.database_url.drivername == "postgresql+psycopg"
    assert settings.database_url.port == 55432


def test_invalid_password_input_is_hidden_in_validation_message(config_module):
    password = "synthetic-invalid-password"
    with pytest.raises(ValidationError) as caught:
        config_module.Settings(_env_file=None, DB_PASSWORD=[password])

    assert caught.value.errors(include_input=False)[0]["loc"] == ("DB_PASSWORD",)
    assert password not in str(caught.value)
    assert "input_value" not in str(caught.value)


def test_empty_password_is_rejected(config_module):
    with pytest.raises(ValidationError) as caught:
        config_module.Settings(_env_file=None, DB_PASSWORD="")

    error = caught.value.errors(include_input=False)[0]
    assert error["loc"] == ("DB_PASSWORD",)
    assert error["type"] == "too_short"


def test_missing_password_has_no_default(config_module, monkeypatch):
    monkeypatch.delenv("DB_PASSWORD")
    with pytest.raises(ValidationError) as caught:
        config_module.Settings(_env_file=None)

    error = caught.value.errors(include_input=False)[0]
    assert error["loc"] == ("DB_PASSWORD",)
    assert error["type"] == "missing"


def test_unsupported_driver_is_rejected(config_module):
    with pytest.raises(ValidationError) as caught:
        config_module.Settings(_env_file=None, DB_DRIVER="sqlite")
    error = caught.value.errors(include_input=False)[0]
    assert error["loc"] == ("DB_DRIVER",)
    assert error["type"] == "literal_error"


@pytest.mark.parametrize(
    "port, error_type", [(0, "greater_than_equal"), (65536, "less_than_equal")]
)
def test_port_outside_tcp_range_is_rejected(config_module, port, error_type):
    with pytest.raises(ValidationError) as caught:
        config_module.Settings(_env_file=None, DB_PORT=port)

    error = caught.value.errors(include_input=False)[0]
    assert error["loc"] == ("DB_PORT",)
    assert error["type"] == error_type


@pytest.mark.parametrize("port", ["1", "65535"])
def test_port_boundaries_accept_environment_strings(config_module, port):
    settings = config_module.Settings(_env_file=None, DB_PORT=port)
    assert settings.database_url.port == int(port)
