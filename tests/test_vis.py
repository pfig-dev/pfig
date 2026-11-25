from __future__ import annotations

import json
import sys
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import quote

from pfig.vis import build_graph_view_model_for_uri, serve_graph


def test_build_graph_view_model_for_uri(tmp_path):
    config = tmp_path / "main.pfig"
    config.write_text(
        """
from collections import OrderedDict
model = OrderedDict()
__entrypoint__ = @model
""",
        encoding="utf-8",
    )

    vm = build_graph_view_model_for_uri(config, mode="reachable")

    assert vm["ok"] is True
    assert vm["mode"] == "reachable"
    assert {node["id"] for node in vm["nodes"]} == {"model", "__entrypoint__"}


def test_serve_graph_api_returns_graph_json(tmp_path):
    config = tmp_path / "main.pfig"
    config.write_text(
        """
from collections import OrderedDict
a = OrderedDict()
b = OrderedDict(x=@a)
__entrypoint__ = @b
""",
        encoding="utf-8",
    )
    server = serve_graph(config, mode="all", open_browser=False)
    try:
        with urllib.request.urlopen(f"{server.url}api/graph?mode=all", timeout=5) as res:
            payload = json.loads(res.read().decode("utf-8"))
    finally:
        server.shutdown()

    assert payload["ok"] is True
    assert payload["mode"] == "all"
    assert payload["supportsSourceReveal"] is False
    assert {"a", "b", "__entrypoint__"}.issubset({node["id"] for node in payload["nodes"]})


def test_serve_graph_reveal_forwards_to_callback(tmp_path):
    config = tmp_path / "main.pfig"
    config.write_text("__entrypoint__ = 1\n", encoding="utf-8")
    seen = {}

    class Callback(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            return

        def do_GET(self):
            seen["path"] = self.path
            self.send_response(204)
            self.end_headers()

    callback = ThreadingHTTPServer(("127.0.0.1", 0), Callback)
    thread = threading.Thread(target=callback.serve_forever, daemon=True)
    thread.start()
    server = serve_graph(
        config,
        open_browser=False,
        reveal_url=f"http://127.0.0.1:{callback.server_address[1]}/reveal?token=abc",
    )
    try:
        with urllib.request.urlopen(f"{server.url}api/graph", timeout=5) as res:
            payload = json.loads(res.read().decode("utf-8"))
        assert payload["supportsSourceReveal"] is True
        url = f"{server.url}api/reveal?uri={quote(config.as_uri(), safe='')}&line=2&character=4"
        with urllib.request.urlopen(url, timeout=5) as res:
            assert res.status == 204
    finally:
        server.shutdown()
        callback.shutdown()
        callback.server_close()
        thread.join(timeout=2)

    assert seen["path"].startswith("/reveal?token=abc&")
    assert "line=2" in seen["path"]
    assert "character=4" in seen["path"]


def test_vis_module_delegates_to_pfig_view(monkeypatch):
    from pfig.vis.__main__ import main

    seen = {}
    old_argv = sys.argv[:]

    def fake_cli_main():
        seen["argv"] = sys.argv[:]
        return 7

    monkeypatch.setattr("pfig.cli.main", fake_cli_main)

    assert main(["cfg.pfig", "--no-open"]) == 7
    assert seen["argv"][1:] == ["view", "cfg.pfig", "--no-open"]
    assert sys.argv == old_argv
