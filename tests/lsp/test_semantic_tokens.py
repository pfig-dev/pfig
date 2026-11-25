"""Tests for LSP semantic tokens."""

from __future__ import annotations

from lsprotocol import types as lsp

from pfig.lsp.analysis.pfig_adapter import index_document
from pfig.lsp.server import (
    SEMANTIC_TOKEN_TYPES,
    _add_callee_tokens,
    _build_semantic_tokens,
    _encode_semantic_tokens,
    create_server,
)
from pfig.lsp.state import CallSite, DocumentState


def _doc(uri: str, text: str) -> DocumentState:
    doc = DocumentState(uri=uri, text=text)
    doc.index = index_document(text, uri)
    return doc


def _decoded_tokens(data: list[int]) -> list[tuple[int, int, int, str]]:
    out: list[tuple[int, int, int, str]] = []
    line = 0
    char = 0
    for idx in range(0, len(data), 5):
        delta_line, delta_char, length, token_type_idx, _mods = data[idx : idx + 5]
        line += delta_line
        char = char + delta_char if delta_line == 0 else delta_char
        out.append((line, char, length, SEMANTIC_TOKEN_TYPES[token_type_idx]))
    return out


def _token_texts(text: str, tokens: list[tuple[int, int, int, str]]) -> dict[str, list[str]]:
    lines = text.splitlines()
    by_type: dict[str, list[str]] = {}
    for line, char, length, token_type in tokens:
        by_type.setdefault(token_type, []).append(lines[line][char : char + length])
    return by_type


def test_initialize_advertises_semantic_tokens_provider() -> None:
    server = create_server()
    initialize = server.lsp.fm.features[lsp.INITIALIZE]

    result = initialize(lsp.InitializeParams(capabilities=lsp.ClientCapabilities()))

    provider = result.capabilities.semantic_tokens_provider
    assert provider is not None
    assert provider.legend.token_types == SEMANTIC_TOKEN_TYPES
    assert provider.full is True
    assert provider.range is False


def test_semantic_tokens_classify_bindings_refs_and_kwargs() -> None:
    text = "input_size = 784\nmodel = dict(input_size=@input_size)\ncopy = @model\n"
    uri = "file:///tmp/semantic_tokens.pfig"
    server = create_server()
    doc = _doc(uri, text)
    server.workspace_state.documents[uri] = doc

    tokens = _decoded_tokens(_build_semantic_tokens(server, uri, doc))
    by_type = _token_texts(text, tokens)

    assert "model" in by_type["pfigProvider"]
    assert "input_size" in by_type["pfigValue"]
    assert "copy" in by_type["pfigValue"]
    assert "model" in by_type["pfigProviderRef"]
    assert "input_size" in by_type["pfigValueRef"]
    assert "@model" not in by_type["pfigProviderRef"]
    assert "@input_size" not in by_type["pfigValueRef"]
    assert "input_size" in by_type["parameter"]
    assert "dict" in by_type["function"]


def test_semantic_tokens_classify_provider_calls_as_provider_refs() -> None:
    text = "dataset = 'train'\ndataloader = dict(dataset=@dataset)\nloader = @dataloader(dataset=@dataset)\n"
    uri = "file:///tmp/semantic_provider_call_tokens.pfig"
    server = create_server()
    doc = _doc(uri, text)
    server.workspace_state.documents[uri] = doc

    tokens = _decoded_tokens(_build_semantic_tokens(server, uri, doc))
    by_type = _token_texts(text, tokens)

    assert "dataloader" in by_type["pfigProviderRef"]
    assert "@dataloader" not in by_type["pfigProviderRef"]
    assert "dataloader" not in by_type.get("function", [])
    assert "dataset" in by_type["parameter"]
    assert "dataset" in by_type["pfigValueRef"]


def test_semantic_tokens_classify_external_refs_as_values() -> None:
    text = "external input_size\ncopy = @input_size\n"
    uri = "file:///tmp/semantic_external_tokens.pfig"
    server = create_server()
    doc = _doc(uri, text)
    server.workspace_state.documents[uri] = doc

    tokens = _decoded_tokens(_build_semantic_tokens(server, uri, doc))
    by_type = _token_texts(text, tokens)

    assert "input_size" in by_type["pfigValue"]
    assert "input_size" in by_type["pfigValueRef"]


def test_semantic_tokens_classify_import_modules_and_aliases() -> None:
    text = "import torch.optim as optim\nfrom pathlib import Path as P\n"
    uri = "file:///tmp/semantic_import_tokens.pfig"
    server = create_server()
    doc = _doc(uri, text)
    server.workspace_state.documents[uri] = doc

    tokens = _decoded_tokens(_build_semantic_tokens(server, uri, doc))
    by_type = _token_texts(text, tokens)

    assert "torch" in by_type["namespace"]
    assert "optim" in by_type["namespace"]
    assert "pathlib" in by_type["namespace"]
    assert "optim" in by_type["pfigValue"]
    assert "P" in by_type["pfigValue"]


def test_semantic_tokens_handler_returns_empty_for_unknown_document() -> None:
    server = create_server()
    handler = server.lsp.fm.features[lsp.TEXT_DOCUMENT_SEMANTIC_TOKENS_FULL]

    result = handler(
        lsp.SemanticTokensParams(
            text_document=lsp.TextDocumentIdentifier(uri="file:///tmp/missing.pfig")
        )
    )

    assert result.data == []


def test_semantic_tokens_classify_included_refs(tmp_path) -> None:
    consts = tmp_path / "consts.pfig"
    consts.write_text("input_size = 784\nfactory = dict()\n")
    main = tmp_path / "main.pfig"
    main.write_text(
        'include "consts.pfig" as consts\n'
        "size = @consts/input_size\n"
        "factory_copy = @consts/factory\n"
    )

    uri = main.as_uri()
    server = create_server()
    doc = _doc(uri, main.read_text())
    server.workspace_state.documents[uri] = doc

    tokens = _decoded_tokens(_build_semantic_tokens(server, uri, doc))
    by_type = _token_texts(doc.text, tokens)

    assert "consts" in by_type["pfigValueRef"]
    assert "input_size" in by_type["pfigValueRef"]
    assert "consts" in by_type["pfigProviderRef"]
    assert "factory" in by_type["pfigProviderRef"]
    assert "@consts/input_size" not in by_type["pfigValueRef"]
    assert "@consts/factory" not in by_type["pfigProviderRef"]


def test_semantic_tokens_classify_include_path_and_alias(tmp_path) -> None:
    consts = tmp_path / "consts.pfig"
    consts.write_text("input_size = 784\n")
    main = tmp_path / "main.pfig"
    main.write_text('include "consts.pfig" as consts\n')

    uri = main.as_uri()
    server = create_server()
    doc = _doc(uri, main.read_text())
    server.workspace_state.documents[uri] = doc

    tokens = _decoded_tokens(_build_semantic_tokens(server, uri, doc))
    by_type = _token_texts(doc.text, tokens)

    assert '"consts.pfig"' in by_type["namespace"]
    assert "consts" in by_type["namespace"]


def test_semantic_tokens_classify_included_external_refs_as_values(tmp_path) -> None:
    consts = tmp_path / "consts.pfig"
    consts.write_text("external input_size\n")
    main = tmp_path / "main.pfig"
    main.write_text('include "consts.pfig" as consts\nsize = @consts/input_size\n')

    uri = main.as_uri()
    server = create_server()
    doc = _doc(uri, main.read_text())
    server.workspace_state.documents[uri] = doc

    tokens = _decoded_tokens(_build_semantic_tokens(server, uri, doc))
    by_type = _token_texts(doc.text, tokens)

    assert "consts" in by_type["pfigValueRef"]
    assert "input_size" in by_type["pfigValueRef"]


def test_dotted_callee_segments_namespace_and_function_tokens() -> None:
    tokens = []
    _add_callee_tokens(
        tokens,
        CallSite(
            callee="pkg.module.Constructor",
            callee_range=lsp.Range(
                start=lsp.Position(line=0, character=4),
                end=lsp.Position(line=0, character=26),
            ),
        ),
    )

    assert tokens == [
        (0, 4, 3, "namespace"),
        (0, 8, 6, "namespace"),
        (0, 15, 11, "function"),
    ]


def test_semantic_tokens_are_sorted_and_delta_encoded() -> None:
    data = _encode_semantic_tokens(
        [
            (2, 4, 1, "pfigValue"),
            (0, 3, 2, "pfigProvider"),
            (0, 9, 4, "parameter"),
        ]
    )

    assert data == [
        0,
        3,
        2,
        SEMANTIC_TOKEN_TYPES.index("pfigProvider"),
        0,
        0,
        6,
        4,
        SEMANTIC_TOKEN_TYPES.index("parameter"),
        0,
        2,
        4,
        1,
        SEMANTIC_TOKEN_TYPES.index("pfigValue"),
        0,
    ]
