"""The Flask app factory. Takes resolved objects, never builds them:
composition stays in engine.runtime and the CLI (`engine serve`)."""

from pathlib import Path

from flask import Flask, send_from_directory

from engine.config.models import ContextSettings, UiSettings
from engine.web.routes_ask import bp as ask_bp
from engine.web.routes_work import bp as work_bp

STATIC_DIR = Path(__file__).parent / "static"


def create_app(
    session,
    work_store,
    identity,
    *,
    ui: UiSettings,
    pack_name: str,
    sse_keepalive_seconds: float = 15.0,
    context: ContextSettings | None = None,
) -> Flask:
    app = Flask(
        "engine.web",
        static_folder=str(STATIC_DIR),
        static_url_path="/static",
    )
    app.config.update(
        ENGINE_SESSION=session,
        ENGINE_WORK_STORE=work_store,
        ENGINE_IDENTITY=identity,
        ENGINE_UI=ui,
        ENGINE_PACK_NAME=pack_name,
        ENGINE_SSE_KEEPALIVE_SECONDS=sse_keepalive_seconds,
        # The pack's context settings: the page reads the nudge
        # threshold from them (Brief §10.3).
        ENGINE_CONTEXT=context if context is not None else ContextSettings(),
    )
    app.register_blueprint(ask_bp)
    app.register_blueprint(work_bp)

    @app.get("/")
    def index():
        return send_from_directory(STATIC_DIR, "index.html")

    @app.get("/favicon.ico")
    def favicon():
        # Browsers ask for this path unprompted; the page also links
        # the SVG, so both answer (the play sessions logged a 404 on
        # every load).
        return send_from_directory(
            STATIC_DIR, "favicon.svg", mimetype="image/svg+xml"
        )

    return app
