"""Local HTTP server for the pfig graph visualizer."""

from __future__ import annotations

import json
import mimetypes
import posixpath
import threading
import urllib.request
import webbrowser
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlencode, urlparse

from pfig.diff import AlignmentMode
from pfig.lsp.analysis.pfig_adapter import index_document
from pfig.lsp.analysis.resolver import _path_to_uri, _uri_to_path
from pfig.lsp.graph_plan_diff import compare_graph_plans
from pfig.lsp.graph_view_model import (
    build_graph_view_model,
    compile_graph_plan,
    graph_error,
    render_graph_view_model,
)
from pfig.lsp.state import WorkspaceState


def _target_to_uri(target: str | Path) -> str:
    raw = str(target)
    if raw.startswith("file://"):
        return raw
    return _path_to_uri(str(Path(raw).expanduser().resolve()))


def build_graph_view_model_for_uri(
    uri: str | Path,
    *,
    mode: str = "reachable",
    profiles: list[str] | None = None,
    set_overrides: dict[str, str] | None = None,
    allow_missing_externals: bool = True,
    strict_signatures: bool = False,
    compare_to: str | Path | None = None,
    alignment: AlignmentMode = "auto",
) -> dict[str, Any]:
    """Build graph JSON for a file URI or path using the same VM code as the LSP."""

    def load(target: str | Path) -> dict[str, Any]:
        normalized_uri = _target_to_uri(target)
        path = _uri_to_path(normalized_uri)
        if not path:
            raise ValueError(f"Only file:// graph URIs are supported: {normalized_uri}")
        text = Path(path).read_text(encoding="utf-8")
        return {
            "text": text,
            "uri": normalized_uri,
            "index": index_document(text, normalized_uri),
            "ws": WorkspaceState(
                root_uri=_path_to_uri(str(Path(path).parent)),
                profiles=profiles or [],
                set_overrides=set_overrides or {},
                allow_missing_externals=allow_missing_externals,
                strict_signatures=strict_signatures,
            ),
        }

    if compare_to is None:
        return build_graph_view_model(**load(uri), mode=mode)

    rendered = []
    compiled = []
    labels = []
    for side, target in (("Proposed", uri), ("Base", compare_to)):
        label = _uri_to_path(_target_to_uri(target)) or str(target)
        labels.append(label)
        try:
            inputs = load(target)
            plan, error, _ = compile_graph_plan(**inputs, comparison=True)
            if plan is None:
                return graph_error(
                    mode, f"{side} ({label}): {error or 'Compile produced no data.'}"
                )
            compiled.append(plan)
            rendered.append(
                render_graph_view_model(**inputs, mode="all", compiled=plan, comparison=True)
            )
        except Exception as exc:
            return graph_error(mode, f"{side} ({label}): {exc}")
    return compare_graph_plans(
        rendered[1],
        rendered[0],
        compiled[1],
        compiled[0],
        mode=mode,
        base_label=labels[1],
        proposed_label=labels[0],
        alignment=alignment,
    )


@dataclass
class GraphVisServer:
    """Handle returned by :func:`serve_graph`."""

    httpd: ThreadingHTTPServer
    url: str
    thread: threading.Thread | None = None

    def serve_forever(self) -> None:
        self.httpd.serve_forever()

    def shutdown(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=2)


def _assets_root() -> Path:
    return Path(str(files("pfig.vis") / "assets"))


def _json_response(handler: BaseHTTPRequestHandler, status: int, payload: object) -> None:
    body = json.dumps(payload).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _safe_asset_path(path: str) -> Path | None:
    rel = posixpath.normpath(unquote(path.lstrip("/")))
    if rel in ("", "."):
        rel = "index.html"
    candidate = (_assets_root() / rel).resolve()
    root = _assets_root().resolve()
    if root not in candidate.parents and candidate != root:
        return None
    if candidate.is_dir():
        candidate = candidate / "index.html"
    return candidate


def _make_handler(
    *,
    uri: str,
    default_mode: str,
    profiles: list[str] | None,
    set_overrides: dict[str, str] | None,
    allow_missing_externals: bool,
    strict_signatures: bool,
    reveal_url: str | None,
    compare_to: str | None,
    alignment: AlignmentMode,
) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            return

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path == "/api/graph":
                qs = parse_qs(parsed.query)
                mode = qs.get("mode", [default_mode])[0]
                if mode not in {"reachable", "all"}:
                    _json_response(self, HTTPStatus.BAD_REQUEST, {"error": "invalid graph mode"})
                    return
                try:
                    vm = build_graph_view_model_for_uri(
                        uri,
                        mode=mode,
                        profiles=profiles,
                        set_overrides=set_overrides,
                        allow_missing_externals=allow_missing_externals,
                        strict_signatures=strict_signatures,
                        compare_to=compare_to,
                        alignment=alignment,
                    )
                except Exception as e:
                    vm = {
                        "ok": False,
                        "error": str(e),
                        "nodes": [],
                        "edges": [],
                        "mode": mode,
                        "reachableUnavailable": True,
                        "entrypoint": None,
                    }
                vm["supportsSourceReveal"] = bool(reveal_url)
                _json_response(self, HTTPStatus.OK, vm)
                return

            if parsed.path == "/api/reveal":
                qs = parse_qs(parsed.query)
                target_uri = qs.get("uri", [uri])[0]
                line = qs.get("line", ["0"])[0]
                character = qs.get("character", ["0"])[0]
                path = _uri_to_path(target_uri)
                if path:
                    if reveal_url:
                        separator = "&" if urlparse(reveal_url).query else "?"
                        callback_url = (
                            reveal_url
                            + separator
                            + urlencode({"uri": target_uri, "line": line, "character": character})
                        )
                        try:
                            with urllib.request.urlopen(callback_url, timeout=2):
                                pass
                        except Exception as e:
                            _json_response(
                                self,
                                HTTPStatus.BAD_GATEWAY,
                                {"error": f"reveal callback failed: {e}"},
                            )
                            return
                        self.send_response(HTTPStatus.NO_CONTENT)
                        self.end_headers()
                        return
                    self.send_response(HTTPStatus.FOUND)
                    self.send_header("Location", Path(path).as_uri())
                    self.end_headers()
                else:
                    _json_response(self, HTTPStatus.BAD_REQUEST, {"error": "invalid file URI"})
                return

            asset = _safe_asset_path(parsed.path)
            if asset is None or not asset.exists() or not asset.is_file():
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            data = asset.read_bytes()
            content_type = mimetypes.guess_type(asset.name)[0] or "application/octet-stream"
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    return Handler


def serve_graph(
    uri: str | Path,
    *,
    mode: str = "reachable",
    host: str = "127.0.0.1",
    port: int = 0,
    open_browser: bool = True,
    block: bool = False,
    profiles: list[str] | None = None,
    set_overrides: dict[str, str] | None = None,
    allow_missing_externals: bool = True,
    strict_signatures: bool = False,
    reveal_url: str | None = None,
    compare_to: str | Path | None = None,
    alignment: AlignmentMode = "auto",
) -> GraphVisServer:
    """Serve the browser graph viewer for a pfig file."""
    if mode not in {"reachable", "all"}:
        raise ValueError("mode must be 'reachable' or 'all'")
    normalized_uri = _target_to_uri(uri)
    handler = _make_handler(
        uri=normalized_uri,
        default_mode=mode,
        profiles=profiles,
        set_overrides=set_overrides,
        allow_missing_externals=allow_missing_externals,
        strict_signatures=strict_signatures,
        reveal_url=reveal_url,
        compare_to=_target_to_uri(compare_to) if compare_to is not None else None,
        alignment=alignment,
    )
    httpd = ThreadingHTTPServer((host, port), handler)
    bound_host, bound_port = httpd.server_address[:2]
    url = f"http://{bound_host}:{bound_port}/"
    server = GraphVisServer(httpd=httpd, url=url)
    if block:
        if open_browser:
            webbrowser.open(url)
        server.serve_forever()
    else:
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        server.thread = thread
        if open_browser:
            webbrowser.open(url)
    return server
