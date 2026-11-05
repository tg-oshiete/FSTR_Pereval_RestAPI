import pytest

from app import demo


@pytest.mark.parametrize("enabled,name", [(False, "show_demo"), (True, "production"), (False, "production")])
def test_demo_refuses_before_engine_creation(test_settings, monkeypatch, enabled, name):
    settings = test_settings.model_copy(update={"demo_mode": enabled, "db_name": name})
    monkeypatch.setattr(demo, "get_settings", lambda: settings)

    def forbidden(*args):
        pytest.fail("Disabled demo must not initialize a database")

    monkeypatch.setattr(demo, "build_engine", forbidden)
    assert demo.main(["seed"]) == 1
    assert demo.main(["run"]) == 1


@pytest.mark.parametrize("url", ["https://example.com", "http://example.com", "http://localhost@evil.invalid",
                                 "http://localhost/path", "http://127.0.0.1?redirect=example.com"])
def test_demo_rejects_remote_token_destinations(url):
    with pytest.raises(ValueError):
        demo.local_url(url)
