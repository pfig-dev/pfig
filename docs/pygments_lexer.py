"""
Pygments lexer for pfig DSL syntax highlighting.
"""

from pygments.lexer import RegexLexer, bygroups, include
from pygments.token import (
    Comment,
    Keyword,
    Name,
    Number,
    Operator,
    Punctuation,
    String,
    Text,
)

__all__ = ["PfigLexer"]


class PfigLexer(RegexLexer):
    """Lexer for pfig configuration files."""

    name = "pfig"
    aliases = ["pfig", "pfig"]
    filenames = ["*.pfig"]

    tokens = {
        "root": [
            # Comments
            (r"#.*$", Comment),
            # Strings (must come before other patterns)
            (r'"(?:[^"\\]|\\.)*"', String.Double),
            (r"'(?:[^'\\]|\\.)*'", String.Single),
            # Numbers
            (r"\b0[xX][0-9A-Fa-f_]+\b", Number.Hex),
            (r"\b\d+\.\d+(?:[eE][\-+]?\d+)?\b", Number.Float),
            (r"\b\d+(?:[eE][\-+]?\d+)?\b", Number.Integer),
            # Magic variables (__entrypoint__, etc.)
            (r"\b(__\w+__)\b", Name.Builtin),
            # Keywords
            (r"\b(import|from|include|override|external|as)\b", Keyword),
            # Constants
            (r"\b(True|False|None)\b", Keyword.Constant),
            # Builtin functions
            (r"\b(len|min|max|range)\b", Name.Builtin),
            # Provider references: @name, @name(), @namespace/name
            (
                r"(@)([A-Za-z_]\w*(?:/[A-Za-z_]\w*)*)(\s*\()",
                bygroups(Operator, Name.Variable, Punctuation),
                "provider_call",
            ),
            (r"(@)([A-Za-z_]\w*(?:/[A-Za-z_]\w*)*)", bygroups(Operator, Name.Variable)),
            # Include statements: include "./file.pfig" as alias
            (r"\binclude\b", Keyword, "include_stmt"),
            # External statements: external name
            (r"\bexternal\b", Keyword, "external_stmt"),
            # Override statements: override name/param = value
            (r"\boverride\b", Keyword, "override_stmt"),
            # From import: from module import name
            (r"\bfrom\b", Keyword, "from_import"),
            # Import statements: import module
            (r"\bimport\b", Keyword, "import_stmt"),
            # Dotted symbols (module.class.method)
            (r"\b[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+", Name.Namespace),
            # Operators
            (r"\*\*", Operator),
            (r"//", Operator),
            (r"[+\-*/]", Operator),
            (r"=", Operator),
            (r":", Punctuation),
            (r",", Punctuation),
            (r"\.", Punctuation),
            (r"/", Punctuation),
            (r"[\[\{\(]", Punctuation),
            (r"[\]\}\)]", Punctuation),
            # Identifiers
            (r"\b[A-Za-z_]\w*\b", Name),
            # Whitespace
            (r"\s+", Text),
        ],
        "include_stmt": [
            (r"\s+", Text),
            (r'"(?:[^"\\]|\\.)*"', String.Double),
            (r"'(?:[^'\\]|\\.)*'", String.Single),
            (r"\bas\b", Keyword),
            (r"\b[A-Za-z_]\w*\b", Name.Namespace),
            (r"$", Text, "#pop"),
        ],
        "external_stmt": [
            (r"\s+", Text),
            (r"[A-Za-z_]\w*(?:/[A-Za-z_]\w*)*", Name.Variable),
            (r"$", Text, "#pop"),
        ],
        "override_stmt": [
            (r"\s+", Text),
            (r"[A-Za-z_]\w*(?:/[A-Za-z_]\w*)*", Name.Variable),
            (r"=", Operator),
            include("root"),
        ],
        "from_import": [
            (r"\s+", Text),
            (r"\b[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*", Name.Namespace),
            (r"\bimport\b", Keyword),
            (r"\b[A-Za-z_]\w*\b", Name),
            (r"\bas\b", Keyword),
            (r",", Punctuation),
            (r"$", Text, "#pop"),
        ],
        "import_stmt": [
            (r"\s+", Text),
            (r"\b[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*", Name.Namespace),
            (r"\bas\b", Keyword),
            (r"\b[A-Za-z_]\w*\b", Name),
            (r",", Punctuation),
            (r"$", Text, "#pop"),
        ],
        "provider_call": [
            (r"\s+", Text),
            (r"\b([A-Za-z_]\w*)(\s*=)", bygroups(Name.Attribute, Operator), "kwarg"),
            (r"\)", Punctuation, "#pop"),
            include("root"),
        ],
        "kwarg": [
            include("root"),
            (r",", Punctuation, "#pop"),
            (r"\)", Punctuation, "#pop"),
        ],
    }
