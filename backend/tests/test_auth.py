from rahband.services.auth import AuthStore


def test_user_can_login_and_logout(tmp_path):
    store = AuthStore(tmp_path / "auth.sqlite3")
    user_id = store.upsert_user("operator", "a-strong-password")
    authenticated = store.authenticate("operator", "a-strong-password")
    assert authenticated is not None
    token, user = authenticated
    assert user["id"] == user_id
    assert isinstance(user["expires_at"], str)
    assert store.user_for_session(token)["username"] == "operator"
    store.logout(token)
    assert store.user_for_session(token) is None


def test_wrong_password_and_disabled_user_are_rejected(tmp_path):
    store = AuthStore(tmp_path / "auth.sqlite3")
    store.upsert_user("operator", "a-strong-password")
    assert store.authenticate("operator", "wrong-password") is None
    assert store.set_enabled("operator", False) is True
    assert store.authenticate("operator", "a-strong-password") is None


def test_password_reset_revokes_existing_sessions(tmp_path):
    store = AuthStore(tmp_path / "auth.sqlite3")
    store.upsert_user("operator", "original-password")
    token, _ = store.authenticate("operator", "original-password")
    store.upsert_user("operator", "replacement-password")
    assert store.user_for_session(token) is None
    assert store.authenticate("operator", "original-password") is None
    assert store.authenticate("operator", "replacement-password") is not None
