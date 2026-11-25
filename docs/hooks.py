"""MkDocs hooks to register pfig Pygments lexer."""

import sys
from pathlib import Path

# Add docs directory to Python path (we're already in docs/, so this is just for imports)
docs_dir = Path(__file__).parent
docs_dir_str = str(docs_dir)
if docs_dir_str not in sys.path:
    sys.path.insert(0, docs_dir_str)


def on_config(config, **kwargs):
    """Register the lexer when MkDocs loads config."""
    # Ensure docs directory is in path (in case it wasn't added at module level)
    docs_dir = Path(__file__).parent
    docs_dir_str = str(docs_dir)
    if docs_dir_str not in sys.path:
        sys.path.insert(0, docs_dir_str)

    # Import the lexer using importlib to be more explicit
    import importlib.util

    lexer_path = docs_dir / "pygments_lexer.py"
    spec = importlib.util.spec_from_file_location("pygments_lexer", lexer_path)
    pygments_lexer_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pygments_lexer_module)
    PfigLexer = pygments_lexer_module.PfigLexer

    # Ensure __all__ exists for Pygments to properly load the module
    if not hasattr(pygments_lexer_module, "__all__"):
        pygments_lexer_module.__all__ = ["PfigLexer"]

    # Store the lexer class in the module for Pygments to find
    setattr(pygments_lexer_module, "PfigLexer", PfigLexer)

    # Register in _mapping.LEXERS (the proper Pygments way)
    try:
        from pygments.lexers import _mapping

        _mapping.LEXERS["PfigLexer"] = (
            pygments_lexer_module.__name__,
            "PfigLexer",
            ("pfig",),
            ("*.pfig",),
            ("text/x-pfig",),
        )

        # Also add to the lexer cache directly
        from pygments.lexers import _lexer_cache

        _lexer_cache["pfig"] = lambda **options: PfigLexer(**options)
        _lexer_cache["PfigLexer"] = lambda **options: PfigLexer(**options)
    except Exception as e:
        print(f"Warning: Could not register in _mapping.LEXERS: {e}", file=sys.stderr)

    # Also ensure it's available via get_lexer_by_name
    from pygments import lexers

    # Monkey-patch get_lexer_by_name to include our lexer
    if not hasattr(lexers, "_original_get_lexer_by_name"):
        lexers._original_get_lexer_by_name = lexers.get_lexer_by_name

        def get_lexer_by_name(_alias, **options):
            """Monkey-patched version that includes our custom lexer."""
            alias_lower = str(_alias).lower() if _alias else ""
            if alias_lower == "pfig":
                return PfigLexer(**options)
            try:
                return lexers._original_get_lexer_by_name(_alias, **options)
            except Exception:
                if "pfig" in alias_lower:
                    return PfigLexer(**options)
                raise

        lexers.get_lexer_by_name = get_lexer_by_name

    # Verify registration worked
    try:
        test_lexer = lexers.get_lexer_by_name("pfig")
        print(f"✓ pfig lexer registered successfully: {test_lexer.name}", file=sys.stderr)
    except Exception as e:
        print(f"✗ Failed to verify lexer registration: {e}", file=sys.stderr)
        import traceback

        traceback.print_exc(file=sys.stderr)

    return config
