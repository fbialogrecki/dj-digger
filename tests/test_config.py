import json
import tempfile
from pathlib import Path

from dj_digger.config import DEFAULT_COMMENTS, DEFAULT_EMAIL, DEFAULT_NAME, AppConfig


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


def test_first_run_is_flagged_only_when_there_was_no_config_file(tmp_path):
    """The TUI opens Settings on the strength of this flag."""

    # Not config.json: conftest already wrote one there to isolate user data.
    path = tmp_path / "fresh-profile.json"
    assert AppConfig(path).first_run is True
    assert AppConfig(path).first_run is False, "the first load wrote the file"


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


def test_gate_email_rejects_whitespace_and_malformed_addresses(tmp_path):
    config = AppConfig(tmp_path / "config.json")

    for address in ("listener", "listener @example.com", "@example.com", "listener@"):
        config.user_email = address
        assert config.has_real_email() is False

    config.user_email = "listener+gates@example.com"
    assert config.has_real_email() is True


def test_the_browser_choice_round_trips(tmp_path):
    path = tmp_path / "config.json"
    config = AppConfig(path)
    assert config.browser == "", "empty means the system default"

    config.browser = "firefox"
    config.save()

    assert AppConfig(path).browser == "firefox"


def test_the_column_choice_round_trips_and_ignores_unknown_names(tmp_path):
    path = tmp_path / "config.json"
    config = AppConfig(path)
    assert config.columns == []

    config.columns = ["year", "bpm"]
    config.save()
    path.write_text(path.read_text().replace('"year"', '"year", "nonsense"'))

    assert AppConfig(path).columns == ["bpm", "year"], "canonical order, unknown names dropped"


def test_the_download_directory_round_trips(tmp_path):
    """It was ~/Downloads written into the download code in two places."""

    path = tmp_path / "config.json"
    config = AppConfig(path)
    assert config.download_directory.endswith("Downloads")

    config.download_directory = str(tmp_path / "crates" / "incoming")
    config.save()

    assert AppConfig(path).download_directory == str(tmp_path / "crates" / "incoming")


def test_an_older_config_without_a_download_directory_still_gets_one(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"user_name": "DJ Test"}), encoding="utf-8")

    config = AppConfig(path)

    assert config.user_name == "DJ Test"
    assert config.download_directory.endswith("Downloads")
