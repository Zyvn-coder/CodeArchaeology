"""Tests for reading a file version's definitions out of its bytes.

The module under test knows nothing about Git, SQLite or the CLI, and neither do
these tests: every input is a bytes literal and every expectation is about what
comes back from it.
"""

import ast
import sys
from pathlib import Path

import pytest

from codearchaeology import definitions
from codearchaeology.definitions import (
    ASYNC_FUNCTION,
    CLASS,
    FUNCTION,
    Definition,
    ParseFailure,
    definitions_of,
)


def _definitions(source: bytes) -> tuple[Definition, ...]:
    """The definitions of *source*, failing the test if it could not be read."""
    result = definitions_of(source)
    assert not isinstance(result, ParseFailure), result
    return result


def _failure(source: bytes) -> ParseFailure:
    """Why *source* could not be read, failing the test if it could."""
    result = definitions_of(source)
    assert isinstance(result, ParseFailure), result
    return result


def _only(source: bytes) -> Definition:
    """The single definition of *source*."""
    found = _definitions(source)
    assert len(found) == 1, found
    return found[0]


def test_a_plain_function() -> None:
    definition = _only(b"def foo(): ...\n")
    assert definition.kind == FUNCTION
    assert definition.name == "foo"
    assert definition.qualname == "foo"


def test_an_async_function() -> None:
    definition = _only(b"async def bar():\n    pass\n")
    assert definition.kind == ASYNC_FUNCTION
    assert definition.name == "bar"


def test_a_class() -> None:
    definition = _only(b"class Parser:\n    pass\n")
    assert definition.kind == CLASS
    assert definition.qualname == "Parser"


def test_a_method_is_a_function_reached_through_its_class() -> None:
    parser, parse = _definitions(b"class Parser:\n    def parse(self):\n        pass\n")
    assert parser.kind == CLASS
    assert parse.kind == FUNCTION
    assert parse.name == "parse"
    assert parse.qualname == "Parser.parse"


def test_a_nested_function_is_reached_through_locals() -> None:
    outer, inner = _definitions(b"def outer():\n    def inner():\n        pass\n")
    assert outer.qualname == "outer"
    assert inner.qualname == "outer.<locals>.inner"


def test_a_nested_class_is_reached_through_its_parent() -> None:
    outer, inner = _definitions(b"class A:\n    class B:\n        pass\n")
    assert outer.qualname == "A"
    assert inner.qualname == "A.B"


def test_a_definition_inside_a_method_is_reached_through_both() -> None:
    _, _, inner = _definitions(
        b"class A:\n    def m(self):\n        def inner():\n            pass\n"
    )
    assert inner.qualname == "A.m.<locals>.inner"


def test_decorators_are_recorded_by_their_dotted_name() -> None:
    assert _only(b"@property\ndef f(self):\n    pass\n").decorators == ("property",)
    assert _only(b"@app.route('/x')\ndef f():\n    pass\n").decorators == ("app.route",)
    assert _only(b"def f():\n    pass\n").decorators == ()


def test_a_decorator_without_a_dotted_name_is_rendered_structurally() -> None:
    """No name is invented for an expression that does not have one."""
    decorator = _only(b"@f().g\ndef h():\n    pass\n").decorators[0]
    assert decorator != "f.g"
    assert decorator.startswith("Attribute(")
    assert "attr='g'" in decorator


def test_a_decorated_definition_starts_at_its_def_line() -> None:
    definition = _only(b"@property\ndef f(self):\n    pass\n")
    assert definition.lineno == 2


def test_a_multi_line_definition_spans_its_lines() -> None:
    source = (
        b"def outer():\n"
        b"    value = 1\n"
        b"\n"
        b"    def inner():\n"
        b"        return value\n"
        b"\n"
        b"    return inner\n"
    )
    outer, inner = _definitions(source)
    assert (outer.lineno, outer.end_lineno) == (1, 7)
    assert (inner.lineno, inner.end_lineno) == (4, 5)


def test_a_one_line_definition_ends_on_the_line_it_starts() -> None:
    definition = _only(b"def foo(): ...\n")
    assert (definition.lineno, definition.end_lineno) == (1, 1)


def test_source_location_records_the_column() -> None:
    parser, parse = _definitions(b"class Parser:\n    def parse(self):\n        pass\n")
    assert (parser.col_offset, parse.col_offset) == (0, 4)


def test_definitions_are_returned_in_the_order_they_appear() -> None:
    source = (
        b"def first(): ...\n"
        b"\n"
        b"\n"
        b"class Second:\n"
        b"    def method(self): ...\n"
        b"\n"
        b"\n"
        b"def third(): ...\n"
    )
    assert [item.qualname for item in _definitions(source)] == [
        "first",
        "Second",
        "Second.method",
        "third",
    ]


def test_two_definitions_can_share_a_qualified_name() -> None:
    """Which is why a position, and not the name alone, identifies one."""
    source = (
        b"try:\n"
        b"    def f():\n"
        b"        return 1\n"
        b"except ImportError:\n"
        b"    def f():\n"
        b"        return 2\n"
    )
    first, second = _definitions(source)
    assert first.qualname == second.qualname == "f"
    assert (first.lineno, second.lineno) == (2, 5)


def test_a_lambda_is_not_a_definition() -> None:
    assert definitions_of(b"f = lambda x: x\n") == ()


def test_the_fingerprint_ignores_the_name_and_the_place() -> None:
    here = _only(b"def login(user):\n    return check(user)\n")
    renamed = _only(b"\n\ndef authenticate(user):\n    return check(user)\n")
    assert here.fingerprint == renamed.fingerprint


def test_the_fingerprint_ignores_a_comment() -> None:
    """A comment is not in the tree, so it is not in the fingerprint either."""
    plain = _only(b"def f():\n    return 1\n")
    commented = _only(b"def f():\n    # why\n    return 1\n")
    assert plain.fingerprint == commented.fingerprint


def test_the_fingerprint_changes_when_the_definition_changes() -> None:
    base = _only(b"def f(a):\n    return a\n")
    for changed in (
        b"def f(a, b):\n    return a\n",
        b"def f(a=1):\n    return a\n",
        b"def f(a) -> int:\n    return a\n",
        b"def f(a):\n    return a + 1\n",
        b"async def f(a):\n    return a\n",
        b"@decorated\ndef f(a):\n    return a\n",
    ):
        assert _only(changed).fingerprint != base.fingerprint, changed


def test_the_fingerprint_covers_a_decorator_argument() -> None:
    first = _only(b"@app.route('/a')\ndef f():\n    pass\n")
    second = _only(b"@app.route('/b')\ndef f():\n    pass\n")
    assert first.decorators == second.decorators == ("app.route",)
    assert first.fingerprint != second.fingerprint


def test_the_fingerprint_of_a_class_covers_its_bases() -> None:
    assert (
        _only(b"class A:\n    pass\n").fingerprint
        != _only(b"class A(Base):\n    pass\n").fingerprint
    )


# The only check that the rendering does not depend on the interpreter: the suite
# runs on 3.11 and 3.13 in CI, so a rendering that differs between them fails one
# of the four jobs. Update a constant only when the rendering is deliberately
# changed, and say why in the commit that does it.
GOLDEN_FINGERPRINTS = (
    (b"def foo(): ...\n", ("0eae404dd779d0bf4475c395bbce4f51c411ab65260140ab661b91845dc042f6",)),
    (b"async def bar():\n    pass\n", ("25ab95726df40d590632e1faa3b06f2dc7e80c2fc627c4bd520701476200caa5",)),
    (
        b"class Parser:\n    def parse(self):\n        pass\n",
        (
            "7c654aed7785b4359a1ef7cd73fb0fdddb4a2daffd7bf07abbac1dd4627579e3",
            "2cf1f2a74ccdf6c473d542364a5bb8225f8ee27bab40e8a95851d76632d0568f",
        ),
    ),
    (
        b"def outer():\n    def inner():\n        pass\n",
        (
            "75d080cae8aa73ee936d7c0745e847b431a9282e21f08997462c517226f899ae",
            "4afe896419dd97a6ad2dc207118b57fa312b974fa20b2c9a495017bfebb0d59f",
        ),
    ),
    (
        b"@app.route('/x')\ndef handler(request):\n    return request\n",
        ("aba7e0ec57d596e692207125bc9b555b77a1404a30691f02d0fd8310f2b81eaf",),
    ),
    (
        b"def f(a, b=1, *args, c=2, **kw) -> int:\n    return a\n",
        ("febd9c0419dfd8d3ec57b26013f7060859d4f244b6cbd48a92369030e0b906c4",),
    ),
    (
        b"# -*- coding: latin-1 -*-\ndef f():\n    x = '\xe9'\n",
        ("9555d419cf539424cef2d4d1b0989df54a3bd187ec5849256a3d0efc5ff7d785",),
    ),
)


def test_the_fingerprint_is_the_same_on_every_interpreter() -> None:
    for source, expected in GOLDEN_FINGERPRINTS:
        found = _definitions(source)
        assert tuple(item.fingerprint for item in found) == expected, source


def test_the_interpreter_that_parsed_is_recorded() -> None:
    expected = "%d.%d.%d" % sys.version_info[:3]
    assert _only(b"def f(): ...\n").parsed_at_version == expected
    assert _failure(b"def f(:\n").parsed_at_version == expected


def test_an_empty_file_has_no_definitions() -> None:
    assert definitions_of(b"") == ()


def test_a_file_of_only_a_docstring_has_no_definitions() -> None:
    assert definitions_of(b'"""Only a docstring."""\n') == ()


def test_a_syntax_error_is_recorded_not_raised() -> None:
    failure = _failure(b"def f(:\n")
    assert failure.reason.startswith("SyntaxError: ")
    assert failure.lineno == 1


def test_a_nul_byte_is_recorded() -> None:
    failure = _failure(b"x = 1\n\x00\n")
    assert "null bytes" in failure.reason


def test_python_2_syntax_is_recorded() -> None:
    failure = _failure(b"print 'hello'\n")
    assert failure.reason.startswith("SyntaxError: ")
    assert "Missing parentheses" in failure.reason
    assert (failure.lineno, failure.offset) == (1, 1)


def test_a_source_too_deep_for_the_parser_is_recorded() -> None:
    """Not a SyntaxError, which is why the failure set is more than one type.

    How deep is too deep belongs to the machine rather than to the source. The
    parser stops when the C stack runs out, and ``sys.setrecursionlimit`` does
    not move that line — measured on 3.13: the same 2,995 nested attributes at a
    limit of 100 and of 1,000. Measured across the two CI runners: Windows gives
    up below 5,000, Ubuntu reads 5,000 without complaint. So the source is
    deepened until the parser gives up instead of asserting a depth that holds
    on one platform only, and a machine whose stack is large enough to read
    every depth tried is skipped rather than failed.
    """
    depth = 5_000
    while depth <= 320_000:
        failure = definitions_of(b"x = a" + b".a" * depth + b"\n")
        if isinstance(failure, ParseFailure):
            assert failure.reason.startswith("RecursionError: ")
            assert failure.lineno is None
            return
        depth *= 2

    pytest.skip(f"this machine's parser read {depth // 2} nested attributes")


def test_indentation_beyond_the_limit_is_recorded() -> None:
    source = "".join("    " * level + "if True:\n" for level in range(100))
    source += "    " * 100 + "pass\n"
    failure = _failure(source.encode())
    assert failure.reason.startswith("IndentationError: ")


def test_a_coding_cookie_is_honoured_because_the_bytes_are_parsed() -> None:
    """The reason this function takes bytes and not ``str``."""
    source = b"# -*- coding: latin-1 -*-\ndef f():\n    x = '\xe9'\n"
    with pytest.raises(UnicodeDecodeError):
        source.decode("utf-8")
    assert _only(source).name == "f"


def test_the_same_bytes_give_the_same_answer() -> None:
    source = b"class A:\n    def m(self):\n        pass\n"
    assert definitions_of(source) == definitions_of(source)


def test_the_module_does_not_reach_for_git_sqlite_or_the_cli() -> None:
    """Unit 1's promise, checked against the module's own imports."""
    tree = ast.parse(Path(definitions.__file__).read_bytes())
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add((node.module or "").split(".")[0])

    assert imported <= {"ast", "hashlib", "sys", "warnings", "collections", "dataclasses"}
