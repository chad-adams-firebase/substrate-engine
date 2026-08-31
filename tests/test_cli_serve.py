"""engine serve: the same composition recipe as engine ask, handed to
the app factory; app.run is stubbed so the test never binds a port."""

from engine.cli import main


def test_serve_builds_the_app_from_the_pack_and_runs_it(monkeypatch, capsys, tool_pack):
    runs = []

    def fake_run(self, **kwargs):
        runs.append((self, kwargs))

    monkeypatch.setattr("flask.Flask.run", fake_run)
    assert main(["serve", "--pack", str(tool_pack), "--port", "5055"]) == 0

    [(app, kwargs)] = runs
    assert kwargs == {
        "host": "127.0.0.1", "port": 5055, "threaded": True, "use_reloader": False,
    }
    assert app.config["ENGINE_PACK_NAME"] == "toolpack"
    assert app.config["ENGINE_UI"].starter_prompts == ["How many invoices had findings?"]
    assert "serving toolpack on http://127.0.0.1:5055" in capsys.readouterr().err

    # The app is live: config route answers from the pack's identity.
    config = app.test_client().get("/api/config").get_json()
    assert config["app_name"] == "toolpack" and config["user"] == "Test User"
