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
