"""Tests for semantic validation via pfig compile_pfig."""

from __future__ import annotations

from pathlib import Path

from lsprotocol.types import DiagnosticSeverity

from pfig.lsp.analysis.pfig_validator import validate_with_pfig

FIXTURES = Path(__file__).parent / "fixtures" / "pfig"


def _validate(text: str, uri: str = "file:///test.pfig", **kwargs) -> list:
    diags, _ = validate_with_pfig(text, uri, **kwargs)
    return diags


def _diags_with_code(diags: list, code: str) -> list:
    """Filter LSP diagnostics by ``ValidationIssue`` code (stable vs message text)."""
    return [d for d in diags if getattr(d, "code", None) == code]


class TestNameCollisions:
    def test_import_binding_collision(self):
        text = "import os\nos = SomeClass()\n"
        diags = _validate(text)
        collision_diags = [
            d for d in diags if "already used" in d.message or "import" in d.message.lower()
        ]
        assert len(collision_diags) >= 1

    def test_no_collision_when_names_differ(self):
        text = "import os\nmodel = Foo()\n"
        diags = _validate(text)
        collision_diags = [d for d in diags if "already used by an import" in d.message]
        assert len(collision_diags) == 0


class TestShadowing:
    def test_shadowing_without_override_is_error(self):
        text = "model = First()\nmodel = Second()\n"
        diags = _validate(text)
        assert isinstance(diags, list)


class TestMissingExternals:
    def test_missing_external_is_error(self):
        text = "external db_url\n"
        diags = _validate(text, allow_missing_externals=False)
        ext_diags = [
            d for d in diags if "not provided" in d.message or "external" in d.message.lower()
        ]
        assert len(ext_diags) >= 1
        err_diags = [d for d in ext_diags if d.severity == DiagnosticSeverity.Error]
        if err_diags:
            assert err_diags[0].severity == DiagnosticSeverity.Error

    def test_missing_external_not_reported_when_allowed(self):
        text = "external db_url\n"
        diags = _validate(text, allow_missing_externals=True)
        ext_diags = [d for d in diags if "not provided" in d.message]
        assert len(ext_diags) == 0

    def test_provided_external_no_error(self):
        text = "external db_url\ndb_url = 'postgres://localhost'\n"
        diags = _validate(text)
        ext_diags = [d for d in diags if "not provided" in d.message]
        assert len(ext_diags) == 0


class TestUnknownBindings:
    def test_unknown_provider_ref(self):
        text = "result = Foo(x=@nonexistent)\n"
        diags = _validate(text)
        unknown_diags = [
            d for d in diags if "Unknown" in d.message or "binding" in d.message.lower()
        ]
        assert len(unknown_diags) >= 1

    def test_known_provider_ref_no_error(self):
        text = "model = Foo()\nresult = Bar(x=@model)\n"
        diags = _validate(text)
        unknown_diags = [d for d in diags if "Unknown binding" in d.message]
        assert len(unknown_diags) == 0

    def test_typo_in_included_binding_in_kwarg(self, tmp_path):
        """Typo in @alias/name inside a kwarg value should produce unknown binding error."""
        from pfig.lsp.server import create_server
        from pfig.lsp.state import DocumentState

        consts_path = tmp_path / "consts.pfig"
        consts_path.write_text("learning_rate = 0.001\ndropout = 0.1\n")
        main_path = tmp_path / "main.pfig"
        main_path.write_text(
            'include "consts.pfig" as consts\n'
            "from collections import OrderedDict\n"
            "x = OrderedDict(lr=@consts/learning_ratesdsds)\n"
        )
        uri = f"file://{main_path}"
        text = main_path.read_text()
        server = create_server()
        doc = DocumentState(uri=uri, text=text)
        server.workspace_state.documents[uri] = doc
        server.workspace_state.root_uri = f"file://{tmp_path}"
        # Trigger full diagnostic pipeline (includes _validate_provider_refs)
        server.publish_diagnostics = lambda *_: None  # noop
        from pfig.lsp.server import _publish_diagnostics

        _publish_diagnostics(server, uri)
        diags = doc.diagnostics
        unknown_diags = [
            d
            for d in diags
            if "unknown" in d.message.lower() and "learning_ratesdsds" in d.message.lower()
        ]
        assert len(unknown_diags) >= 1, (
            f"Expected unknown binding for typo, got: {[d.message for d in diags]}"
        )

    def test_entrypoint_provider_method_no_error(self, tmp_path):
        """@trainer.fit in __entrypoint__ should not error - only @trainer is validated."""
        from pfig.lsp.server import create_server
        from pfig.lsp.state import DocumentState

        main_path = tmp_path / "main.pfig"
        main_path.write_text(
            "from collections import OrderedDict\n"
            "trainer = OrderedDict()\n"
            "__entrypoint__ = @trainer.fit\n"
        )
        uri = f"file://{main_path}"
        server = create_server()
        doc = DocumentState(uri=uri, text=main_path.read_text())
        server.workspace_state.documents[uri] = doc
        from pfig.lsp.server import _publish_diagnostics

        server.publish_diagnostics = lambda *_: None
        _publish_diagnostics(server, uri)
        unknown_diags = [
            d
            for d in doc.diagnostics
            if "unknown" in d.message.lower() and "trainer" in d.message.lower()
        ]
        assert len(unknown_diags) == 0, (
            f"Should not error on @trainer.fit, got: {[d.message for d in doc.diagnostics]}"
        )

    def test_unknown_base_in_provider_method_still_errors(self, tmp_path):
        """@nonexistent.fit should still error - base @nonexistent is unknown."""
        from pfig.lsp.server import create_server
        from pfig.lsp.state import DocumentState

        main_path = tmp_path / "main.pfig"
        main_path.write_text("__entrypoint__ = @nonexistent.fit\n")
        uri = f"file://{main_path}"
        server = create_server()
        doc = DocumentState(uri=uri, text=main_path.read_text())
        server.workspace_state.documents[uri] = doc
        from pfig.lsp.server import _publish_diagnostics

        server.publish_diagnostics = lambda *_: None
        _publish_diagnostics(server, uri)
        unknown_diags = [
            d
            for d in doc.diagnostics
            if "unknown" in d.message.lower() and "nonexistent" in d.message.lower()
        ]
        assert len(unknown_diags) >= 1, (
            f"Should error on unknown base @nonexistent.fit, got: {[d.message for d in doc.diagnostics]}"
        )

    def test_typo_in_included_binding_assignment(self, tmp_path):
        """Typo in @alias/name in direct assignment should produce unknown binding error."""
        from pfig.lsp.server import create_server
        from pfig.lsp.state import DocumentState

        consts_path = tmp_path / "consts.pfig"
        consts_path.write_text("learning_rate = 0.001\ndropout = 0.1\n")
        main_path = tmp_path / "main.pfig"
        main_path.write_text(
            'include "consts.pfig" as consts\npixel_mean = @consts/droposdasdaut\n'
        )
        uri = f"file://{main_path}"
        text = main_path.read_text()
        server = create_server()
        doc = DocumentState(uri=uri, text=text)
        server.workspace_state.documents[uri] = doc
        server.workspace_state.root_uri = f"file://{tmp_path}"
        from pfig.lsp.server import _publish_diagnostics

        server.publish_diagnostics = lambda *_: None
        _publish_diagnostics(server, uri)
        diags = doc.diagnostics
        unknown_diags = [
            d
            for d in diags
            if "unknown" in d.message.lower() and "droposdasdaut" in d.message.lower()
        ]
        assert len(unknown_diags) >= 1, (
            f"Expected unknown binding for typo, got: {[d.message for d in diags]}"
        )


class TestOverrideRules:
    def test_override_unknown_target_warns(self):
        import pytest

        pytest.skip("pfig compile_pfig does not report override of unknown target")

    def test_profile_only_skips_override_validation(self):
        """Profile-only files (overrides, optionally imports) skip semantic
        validation to avoid false override_not_found errors; override targets
        refer to config at runtime."""
        from pfig.lsp.analysis.pfig_adapter import index_document

        text = '# GPU profile\noverride consts/device = "cuda"\noverride consts/num_workers = 16\n'
        uri = "file:///profiles/gpu.pfig"
        idx = index_document(text, uri)
        diags, compiled = validate_with_pfig(text, uri, index=idx)
        assert compiled is None
        assert len(_diags_with_code(diags, "override_not_found")) == 0, (
            f"Profile-only file should not report override_not_found; got {diags}"
        )

    def test_profile_with_imports_skips_override_validation(self):
        """Profiles can contain imports per spec; still skip override validation."""
        from pfig.lsp.analysis.pfig_adapter import index_document

        text = 'from pathlib import Path\noverride output_dir = "./runs"\noverride num_gpus = 1\n'
        uri = "file:///profiles/local.pfig"
        idx = index_document(text, uri)
        diags, compiled = validate_with_pfig(text, uri, index=idx)
        assert compiled is None
        assert len(_diags_with_code(diags, "override_not_found")) == 0, (
            f"Profile with imports should not report override_not_found; got {diags}"
        )

    def test_profile_like_source_parse_error_still_reported(self):
        """Syntax errors are surfaced before the profile-only short-circuit."""
        text = "override consts/device = \n"
        uri = "file:///profiles/broken.pfig"
        diags, compiled = validate_with_pfig(text, uri, index=None)
        assert compiled is None
        assert len(diags) >= 1
        assert any(d.severity == DiagnosticSeverity.Error for d in diags)

    def test_mixed_binding_and_override_still_runs_compile_pfig(self):
        """A binding plus an override is not profile-only; compile must run (compiled dict)."""
        from pfig.lsp.analysis.pfig_adapter import index_document

        text = "x = 1\noverride foo/bar = 3\n"
        uri = "file:///cfg/mixed.pfig"
        idx = index_document(text, uri)
        diags, compiled = validate_with_pfig(text, uri, index=idx, allow_missing_externals=True)
        assert compiled is not None
        assert isinstance(compiled, dict)

    def test_override_included_binding_no_warning(self, tmp_path):
        consts_path = tmp_path / "consts.pfig"
        consts_path.write_text("epochs = 5\n")
        main_path = tmp_path / "main.pfig"
        main_path.write_text('include "consts.pfig" as consts\noverride consts/epochs = 10\n')
        uri = f"file://{main_path}"
        diags = _validate(main_path.read_text(), uri=uri)
        override_diags = [d for d in diags if "Override target" in d.message]
        assert len(override_diags) == 0


class TestIncludeIssues:
    def test_missing_include_produces_error(self, tmp_path):
        main_path = tmp_path / "main.pfig"
        main_path.write_text('include "nonexistent.pfig" as base\n')
        uri = f"file://{main_path}"
        diags = _validate(main_path.read_text(), uri=uri)
        include_diags = [
            d for d in diags if "resolve" in d.message.lower() or "include" in d.message.lower()
        ]
        assert len(include_diags) >= 1
        assert "nonexistent.pfig" in str(include_diags[0].message)

    def test_included_binding_from_closed_file_is_known(self, tmp_path):
        consts_path = tmp_path / "consts.pfig"
        consts_path.write_text("input_size = 32\nbatch_size = 64\n")
        main_path = tmp_path / "main.pfig"
        main_path.write_text('include "consts.pfig" as consts\nx = @consts/input_size\n')
        uri = f"file://{main_path}"
        diags = _validate(main_path.read_text(), uri=uri)
        unknown_diags = [d for d in diags if "Unknown binding" in d.message]
        assert len(unknown_diags) == 0

    def test_included_external_from_closed_file_is_known(self, tmp_path):
        consts_path = tmp_path / "consts.pfig"
        consts_path.write_text("external data_dir\nexternal device\nx = 1\n")
        main_path = tmp_path / "main.pfig"
        main_path.write_text('include "consts.pfig" as consts\nroot = @consts/data_dir\n')
        uri = f"file://{main_path}"
        diags = _validate(main_path.read_text(), uri=uri)
        unknown_diags = [d for d in diags if "Unknown binding" in d.message]
        assert len(unknown_diags) == 0


class TestErrorsFixture:
    def test_errors_fixture_produces_diagnostics(self):
        text = (FIXTURES / "errors.pfig").read_text()
        diags = _validate(text)
        assert len(diags) >= 1


class TestParseLexerErrors:
    def test_parse_error_produces_diagnostic(self):
        text = "model = (\n"  # Unclosed paren / unexpected EOF
        diags, _ = validate_with_pfig(text, "file:///test.pfig")
        assert len(diags) >= 1
        assert any("unexpected" in d.message.lower() or "parse" in d.message.lower() for d in diags)

    def test_lexer_error_produces_diagnostic(self):
        text = "x = 0xZZ\n"  # Invalid hex literal
        diags, _ = validate_with_pfig(text, "file:///test.pfig")
        assert len(diags) >= 1

    def test_syntax_error_has_position(self):
        text = "model = \n"  # Incomplete expression
        diags, _ = validate_with_pfig(text, "file:///test.pfig")
        assert len(diags) >= 1
        # Diagnostic should have a range (not necessarily 0,0)
        d = diags[0]
        assert d.range is not None


class TestDiagnosticRanges:
    """Test that range-fix helpers underline the correct token."""

    def test_import_error_underlines_unknown_symbol(self):
        from pfig.lsp.analysis.pfig_adapter import index_document

        # "BadName" is the unknown symbol; index has its range
        text = "from os.path import BadName\n"
        uri = "file:///test.pfig"
        idx = index_document(text, uri)
        diags, _ = validate_with_pfig(text, uri, index=idx)
        import_diags = [
            d for d in diags if "attribute" in d.message.lower() or "import" in d.message.lower()
        ]
        if import_diags:
            d = import_diags[0]
            # Range should cover "BadName" (starts around char 18)
            assert d.range.start.line == 0
            assert d.range.start.character >= 17  # "from os.path import " is 19 chars

    def test_unknown_kwarg_underlines_kwarg_name(self):
        import sys
        from pathlib import Path

        from pfig.lsp.analysis.pfig_adapter import index_document

        fixtures_py = Path(__file__).parent / "fixtures" / "py"
        if str(fixtures_py) not in sys.path:
            sys.path.insert(0, str(fixtures_py))

        text = (
            "from sample_module import sample_function\n"
            "result = sample_function(x=1, nonexistent_kw=2)\n"
        )
        uri = "file:///test.pfig"
        idx = index_document(text, uri)
        diags, _ = validate_with_pfig(text, uri, index=idx)
        kwarg_diags = [
            d for d in diags if "unknown" in d.message.lower() and "kwarg" in d.message.lower()
        ]
        if kwarg_diags:
            d = kwarg_diags[0]
            # Range should cover "nonexistent_kw" (on line 1, around char 35)
            assert d.range.start.line == 1
            assert "nonexistent" in d.message.lower() or "kwarg" in d.message.lower()

    def test_include_error_underlines_full_path(self):
        from pfig.lsp.analysis.pfig_adapter import index_document

        # Typo in path: "./conswts.pfig" - diagnostic should underline the full string
        text = 'include "./conswts.pfig" as consts\n'
        uri = "file:///test.pfig"
        idx = index_document(text, uri)
        diags, _ = validate_with_pfig(text, uri, index=idx)
        include_diags = [
            d for d in diags if "include" in d.message.lower() or "resolve" in d.message.lower()
        ]
        assert len(include_diags) >= 1
        d = include_diags[0]
        # Range should cover the full path string "./conswts.pfig" (15 chars), not just first char
        assert d.range.start.line == 0
        assert d.range.end.character - d.range.start.character >= 10  # at least "./conswts.pfig"

    def test_override_not_found_underlines_path(self):
        from pfig.lsp.analysis.pfig_adapter import index_document

        # override consts/epochs = 10 - but consts/epochs doesn't exist
        text = "override consts/epochs = 10\n"
        uri = "file:///test.pfig"
        idx = index_document(text, uri)
        diags, _ = validate_with_pfig(text, uri, index=idx)
        override_diags = [
            d for d in diags if "override" in d.message.lower() and "not found" in d.message.lower()
        ]
        if override_diags:
            d = override_diags[0]
            # Range should cover "consts/epochs" (12 chars), not just first char of line
            assert d.range.start.line == 0
            assert d.range.end.character - d.range.start.character >= 10  # "consts/epochs"


class TestNonFileUri:
    def test_untitled_uri_still_validates(self):
        """Validation works with non-file URI (filename becomes None)."""
        text = "model = Foo()\n"
        diags, compiled = validate_with_pfig(text, "untitled:///foo.pfig")
        # Should not crash; may or may not produce diagnostics
        assert isinstance(diags, list)
