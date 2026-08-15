import tempfile
from pathlib import Path
from dj_digger.config import AppConfig, DEFAULT_COMMENTS, DEFAULT_EMAIL, DEFAULT_NAME

def test_app_config_defaults():
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "config.json"
        config = AppConfig(path)
        assert config.user_name == DEFAULT_NAME
        assert config.user_email == DEFAULT_EMAIL
        assert len(config.custom_comments) > 0
        assert config.random_comment() in DEFAULT_COMMENTS

def test_app_config_save_load():
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "config.json"
        config = AppConfig(path)
        config.user_name = "DJ Test"
        config.user_email = "test@dj.com"
        config.custom_comments = ["Super tune!", "Mega banger!"]
        config.save()

        loaded = AppConfig(path)
        assert loaded.user_name == "DJ Test"
        assert loaded.user_email == "test@dj.com"
        assert loaded.custom_comments == ["Super tune!", "Mega banger!"]
        assert loaded.random_comment() in ["Super tune!", "Mega banger!"]


def test_the_default_email_is_not_deliverable_to_anybody():
    """gates.py posts this to third parties; RFC 2606 reserves .invalid for it."""

    assert DEFAULT_EMAIL.endswith(".invalid")


def test_a_fresh_profile_does_not_claim_to_have_a_real_email(tmp_path):
    assert AppConfig(tmp_path / "config.json").has_real_email() is False


def test_the_retired_default_address_is_not_kept(tmp_path):
    """Nobody chose it, and it belongs to a stranger, so it does not survive a load."""

    import json

    path = tmp_path / "config.json"
    path.write_text(json.dumps({"user_email": "music.listener@yahoo.com"}), encoding="utf-8")

    assert AppConfig(path).user_email == DEFAULT_EMAIL


def test_an_address_the_user_chose_is_left_alone(tmp_path):
    import json

    path = tmp_path / "config.json"
    path.write_text(json.dumps({"user_email": "dj@example.com"}), encoding="utf-8")

    config = AppConfig(path)
    assert config.user_email == "dj@example.com"
    assert config.has_real_email() is True
