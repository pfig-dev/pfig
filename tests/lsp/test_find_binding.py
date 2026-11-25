"""Tests for go-to-definition of included bindings."""

from __future__ import annotations

from pathlib import Path

from lsprotocol.types import Position, Range

from pfig.lsp.analysis.pfig_adapter import index_document
from pfig.lsp.server import (
    _find_overridden_definition,
    _resolve_provider_ref,
)
from pfig.lsp.state import DocumentState, WorkspaceState


def test_goto_definition_included_binding_not_open(tmp_path: Path):
    """Go-to-definition for @alias/name loads included file on demand."""
    consts_path = tmp_path / "consts.pfig"
    consts_path.write_text("batch_size = 32\nnum_workers = 4\n")
    main_path = tmp_path / "main.pfig"
    main_path.write_text('include "consts.pfig" as consts\nx = @consts/batch_size\n')

    main_uri = f"file://{main_path}"
    consts_uri = f"file://{consts_path}"

    main_doc = DocumentState(uri=main_uri, text=main_path.read_text())
    main_doc.index = index_document(main_doc.text, main_uri)

    ws = WorkspaceState()
    ws.documents[main_uri] = main_doc
    # consts.pfig is NOT in documents - simulates user never opening it

    result = _resolve_provider_ref("consts/batch_size", main_uri, main_doc.index, ws)
    assert result is not None
    uri, _ = result
    assert uri == consts_uri


def test_goto_definition_included_external(tmp_path: Path):
    """Go-to-definition for @alias/name works when target is an external."""
    consts_path = tmp_path / "consts.pfig"
    consts_path.write_text("external data_dir  # comment\nx = 1\n")
    main_path = tmp_path / "main.pfig"
    main_path.write_text('include "consts.pfig" as consts\nroot = @consts/data_dir\n')

    main_uri = f"file://{main_path}"
    consts_uri = f"file://{consts_path}"

    main_doc = DocumentState(uri=main_uri, text=main_path.read_text())
    main_doc.index = index_document(main_doc.text, main_uri)

    ws = WorkspaceState()
    ws.documents[main_uri] = main_doc

    result = _resolve_provider_ref("consts/data_dir", main_uri, main_doc.index, ws)
    assert result is not None
    uri, _ = result
    assert uri == consts_uri


def test_goto_definition_prefers_override(tmp_path: Path):
    """GTD from @alias/name goes to override first when present."""
    consts_path = tmp_path / "consts.pfig"
    consts_path.write_text("epochs = 5\nbatch_size = 32\n")
    main_path = tmp_path / "main.pfig"
    main_path.write_text(
        'include "consts.pfig" as consts\noverride consts/epochs = 10\nx = @consts/epochs\n'
    )

    main_uri = f"file://{main_path}"
    consts_uri = f"file://{consts_path}"

    main_doc = DocumentState(uri=main_uri, text=main_path.read_text())
    main_doc.index = index_document(main_doc.text, main_uri)

    ws = WorkspaceState()
    ws.documents[main_uri] = main_doc

    # GTD from @consts/epochs: should go to override in main.pfig
    result = _resolve_provider_ref("consts/epochs", main_uri, main_doc.index, ws)
    assert result is not None
    uri, _ = result
    assert uri == main_uri


def test_goto_definition_from_override_to_original(tmp_path: Path):
    """GTD from override goes to the overridden definition (next in chain)."""
    consts_path = tmp_path / "consts.pfig"
    consts_path.write_text("epochs = 5\nbatch_size = 32\n")
    main_path = tmp_path / "main.pfig"
    main_path.write_text('include "consts.pfig" as consts\noverride consts/epochs = 10\n')

    main_uri = f"file://{main_path}"
    consts_uri = f"file://{consts_path}"

    main_doc = DocumentState(uri=main_uri, text=main_path.read_text())
    main_doc.index = index_document(main_doc.text, main_uri)

    ws = WorkspaceState()
    ws.documents[main_uri] = main_doc

    # GTD from override consts/epochs: should go to original in consts.pfig
    bdef = _find_overridden_definition(ws, main_uri, "consts/epochs")
    assert bdef is not None
    assert bdef.uri == consts_uri
    assert bdef.name == "epochs"


def test_goto_definition_override_chain_nested(tmp_path: Path):
    """GTD from override in included file goes to next in chain (override or original)."""
    base_path = tmp_path / "base.pfig"
    base_path.write_text("epochs = 3\n")
    mid_path = tmp_path / "mid.pfig"
    mid_path.write_text('include "base.pfig" as base\noverride base/epochs = 7\n')

    mid_uri = f"file://{mid_path}"
    base_uri = f"file://{base_path}"

    mid_doc = DocumentState(uri=mid_uri, text=mid_path.read_text())
    mid_doc.index = index_document(mid_doc.text, mid_uri)

    ws = WorkspaceState()
    ws.documents[mid_uri] = mid_doc

    # GTD from override base/epochs in mid: should go to original in base
    bdef = _find_overridden_definition(ws, mid_uri, "base/epochs")
    assert bdef is not None
    assert bdef.uri == base_uri
    assert bdef.name == "epochs"


def test_goto_definition_no_override_goes_to_original(tmp_path: Path):
    """GTD from @alias/name without override goes to original definition."""
    consts_path = tmp_path / "consts.pfig"
    consts_path.write_text("epochs = 5\n")
    main_path = tmp_path / "main.pfig"
    main_path.write_text('include "consts.pfig" as consts\nx = @consts/epochs\n')

    main_uri = f"file://{main_path}"
    consts_uri = f"file://{consts_path}"

    main_doc = DocumentState(uri=main_uri, text=main_path.read_text())
    main_doc.index = index_document(main_doc.text, main_uri)

    ws = WorkspaceState()
    ws.documents[main_uri] = main_doc

    # No override: should go directly to consts.pfig
    result = _resolve_provider_ref("consts/epochs", main_uri, main_doc.index, ws)
    assert result is not None
    uri, _ = result
    assert uri == consts_uri


def test_goto_definition_provider_method_ref(tmp_path: Path):
    """GTD from @trainer.fit resolves base 'trainer' to its binding."""
    main_path = tmp_path / "main.pfig"
    main_path.write_text(
        "from collections import OrderedDict\n"
        "trainer = OrderedDict()\n"
        "__entrypoint__ = @trainer.fit\n"
    )
    main_uri = f"file://{main_path}"
    main_doc = DocumentState(uri=main_uri, text=main_path.read_text())
    main_doc.index = index_document(main_doc.text, main_uri)
    ws = WorkspaceState()
    ws.documents[main_uri] = main_doc

    # Resolve base "trainer" (as on_definition does for @trainer.fit)
    result = _resolve_provider_ref("trainer", main_uri, main_doc.index, ws)
    assert result is not None
    uri, rng = result
    assert uri == main_uri
    assert rng.start.line == 1  # trainer = OrderedDict() is line 1
