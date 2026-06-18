from server.api.database import resolve_database_url


def test_default_database_url_is_absolute_and_cwd_independent(monkeypatch, tmp_path):
    monkeypatch.delenv("MPC_DATABASE_URL", raising=False)
    monkeypatch.chdir(tmp_path)

    resolved = resolve_database_url()
    db_path = resolved.removeprefix("sqlite:///")

    assert resolved.startswith("sqlite:////")
    assert db_path.endswith("/data/mpc_online.db")


def test_database_url_env_override_is_preserved(monkeypatch, tmp_path):
    configured = f"sqlite:///{tmp_path / 'custom.db'}"
    monkeypatch.setenv("MPC_DATABASE_URL", configured)

    assert resolve_database_url() == configured
