"""Read the definitions out of one version of a Python file.

This is the bottom of v0.3 and it knows nothing about Git, SQLite or the CLI: it
takes the bytes of a file and returns what a reader can see in them. Every fact
the later units store is produced here.

Three things in it were measured rather than assumed, and each one is a trap that
was hit for real while the design was frozen:

* **``ast.parse`` is handed bytes, never a decoded string.** A file carrying a
  ``# -*- coding: latin-1 -*-`` cookie parses from its bytes and raises
  ``UnicodeDecodeError`` when it is decoded as UTF-8 first, so decoding would
  turn a readable file into a crash.
* **The parse runs with warnings suppressed.** An invalid escape sequence is a
  ``DeprecationWarning`` on 3.11 and a ``SyntaxWarning`` on 3.13, and the second
  one prints by default: a scan over a large repository would print one per
  occurrence.
* **The fingerprint is built from a rendering written here, not from
  ``ast.dump``.** ``ast.dump`` prints every field on 3.11 (``decorator_list=[]``,
  ``posonlyargs=[]``) and omits the empty ones on 3.13, so the same source has two
  different dumps — a fingerprint built on it would mark every definition in a
  repository as modified the first time it was read by another interpreter. The
  rendering below omits empty fields, which is exactly that difference, and it was
  measured byte-identical on 3.11.16 and 3.13.5.

Nothing here recurses. The parser refuses a tree deeper than it can build — a
5,000-long attribute chain raises ``RecursionError``, not ``SyntaxError`` — so
whatever tree it did accept is already at the edge of what the interpreter can
walk, and a recursive walk of our own would fail on trees the parser had just
accepted.
"""

import ast
import hashlib
import sys
import warnings
from collections.abc import Iterator
from dataclasses import dataclass

FUNCTION = "function"
ASYNC_FUNCTION = "async function"
CLASS = "class"

# Matched on the exact type rather than with isinstance, so a node type that a
# later interpreter adds is not quietly treated as one of these three.
KINDS = {
    ast.FunctionDef: FUNCTION,
    ast.AsyncFunctionDef: ASYNC_FUNCTION,
    ast.ClassDef: CLASS,
}

# A definition inside a function is reached through ``<locals>`` and one inside a
# class through a plain dot, which is what Python itself puts in ``__qualname__``.
# Following the language's own convention means a name this tool prints is the
# name the runtime would report, rather than a second naming scheme.
FUNCTION_SCOPE = ".<locals>."
CLASS_SCOPE = "."


@dataclass(frozen=True, slots=True)
class Definition:
    """One function or class, as one version of one file contained it."""

    kind: str
    name: str
    qualname: str
    lineno: int
    end_lineno: int | None
    col_offset: int
    decorators: tuple[str, ...]
    fingerprint: str
    # The interpreter that produced these facts. The fingerprint is *measured* to
    # be independent of it, but a measurement is not a proof, and a surprise is
    # only explainable if the record says who reported it.
    parsed_at_version: str


@dataclass(frozen=True, slots=True)
class ParseFailure:
    """Why one version of a file could not be read as Python.

    Recorded rather than raised. A repository's history is full of files that
    were not valid Python at the time — Python 2 sources, conflict markers, a
    half-finished rebase — and a scan has to say so and carry on.

    ``offset`` is 1-based, because it is ``SyntaxError.offset`` verbatim, while a
    definition's ``col_offset`` is 0-based, because it is ``ast``'s. Each keeps
    the base of the thing that produced it rather than being adjusted to match
    the other.
    """

    reason: str
    lineno: int | None
    offset: int | None
    parsed_at_version: str


def definitions_of(source: bytes) -> tuple[Definition, ...] | ParseFailure:
    """Return the definitions *source* contains, or why it could not be read.

    The tuple is in the order the definitions appear in the file, a definition
    before the ones inside it. That order is what tells two definitions apart
    within one version of one file: two of them can share a qualified name — a
    name defined again under an ``if``, a fallback in an ``except`` — so the name
    alone does not identify one, and the later units record the position.

    Only ``def``, ``async def`` and ``class`` are definitions. A lambda has no
    name, so there is nothing to follow across versions.
    """
    version = interpreter_version()
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            tree = ast.parse(source)
    except SyntaxError as error:
        return ParseFailure(
            reason=f"{type(error).__name__}: {error.msg}",
            lineno=error.lineno,
            offset=error.offset,
            parsed_at_version=version,
        )
    except RecursionError as error:
        # A source deep enough to exhaust the parser is a fact about the source.
        # Anything else — a MemoryError, an interrupted read — is a fact about
        # the machine, and it is allowed to stop the run instead of being filed
        # as a property of somebody's file.
        return ParseFailure(
            reason=f"{type(error).__name__}: {error}",
            lineno=None,
            offset=None,
            parsed_at_version=version,
        )

    return _definitions(tree, version)


def interpreter_version() -> str:
    """The interpreter doing the parsing, as ``3.13.5``.

    Public because the AST pass has to record it for a file version that
    contained no definitions at all, where there is no :class:`Definition` to
    read it from — and two copies of this string would be two things to keep in
    step.
    """
    return "%d.%d.%d" % sys.version_info[:3]


def _definitions(tree: ast.AST, version: str) -> tuple[Definition, ...]:
    """Collect every definition in *tree*, in the order they appear."""
    found: list[Definition] = []
    stack: list[tuple[ast.AST, str]] = [(tree, "")]

    while stack:
        node, prefix = stack.pop()
        kind = KINDS.get(type(node))
        if kind is not None:
            qualname = prefix + node.name
            found.append(
                Definition(
                    kind=kind,
                    name=node.name,
                    qualname=qualname,
                    lineno=node.lineno,
                    end_lineno=node.end_lineno,
                    col_offset=node.col_offset,
                    decorators=tuple(
                        _decorator_name(item) for item in node.decorator_list
                    ),
                    fingerprint=_fingerprint(kind, node),
                    parsed_at_version=version,
                )
            )
            prefix = qualname + (CLASS_SCOPE if kind == CLASS else FUNCTION_SCOPE)

        # Reversed so that the stack hands the children back in source order.
        for child in reversed(list(ast.iter_child_nodes(node))):
            stack.append((child, prefix))

    return tuple(found)


def _decorator_name(node: ast.expr) -> str:
    """The dotted name of a decorator, or its structure when it has no name.

    ``@app.route("/x")`` is ``app.route``. The argument is not part of the name,
    but it is part of the fingerprint, so changing it is still a modification.
    An expression with no dotted name of its own — ``@f().g``, ``@(lambda f: f)``
    — is rendered structurally instead of being given a name it does not have.
    """
    target = node.func if isinstance(node, ast.Call) else node
    name = _dotted(target)
    return name if name is not None else "".join(_canonical(node))


def _dotted(node: ast.expr) -> str | None:
    """The dotted name of *node*, or ``None`` when it is not one."""
    parts: list[str] = []
    while True:
        if isinstance(node, ast.Name):
            parts.append(node.id)
            break
        if isinstance(node, ast.Attribute):
            parts.append(node.attr)
            node = node.value
            continue
        return None
    return ".".join(reversed(parts))


def _fingerprint(kind: str, node: ast.AST) -> str:
    """The sha256 of everything about the definition except its name and place.

    What is in it: the kind, the decorators, the arguments and return annotation
    of a function (the bases and keywords of a class), and every statement of the
    body. What is not: the name, the line numbers, the column, and the qualified
    name — so a definition that was renamed, moved, or both keeps its
    fingerprint, and that is the only reason a rename can be *seen* at all
    without being inferred.
    """
    digest = hashlib.sha256()
    for token in _material(kind, node):
        # Length-prefixed so that no two token sequences can run together into
        # the same bytes, whatever a string constant in the source contains.
        payload = token.encode("utf-8")
        digest.update(f"{len(payload)}:".encode("ascii"))
        digest.update(payload)
    return digest.hexdigest()


def _material(kind: str, node: ast.AST) -> Iterator[str]:
    """Everything the fingerprint covers, as canonical tokens."""
    yield kind
    for decorator in node.decorator_list:
        yield from _canonical(decorator)
    if isinstance(node, ast.ClassDef):
        for base in node.bases:
            yield from _canonical(base)
        for keyword in node.keywords:
            yield from _canonical(keyword)
    else:
        yield from _canonical(node.args)
        if node.returns is not None:
            yield from _canonical(node.returns)
    for statement in node.body:
        yield from _canonical(statement)


def _canonical(node) -> Iterator[str]:
    """Yield a rendering of *node* that does not depend on the interpreter.

    A field that is empty or absent contributes nothing at all, which is the one
    difference between ``ast.dump`` on 3.11 and on 3.13. Positions are not fields
    and are not in here either, so a definition that moved keeps its rendering.
    """
    stack: list[tuple[str, object]] = [("visit", node)]

    while stack:
        action, payload = stack.pop()
        if action == "text":
            yield payload
        elif isinstance(payload, ast.AST):
            yield type(payload).__name__
            yield "("
            stack.append(("text", ")"))
            fields = [
                (name, value)
                for name, value in ast.iter_fields(payload)
                if not _is_empty(value)
            ]
            for index, (name, value) in reversed(list(enumerate(fields))):
                stack.append(("visit", value))
                stack.append(("text", f"{name}="))
                if index:
                    stack.append(("text", ", "))
        elif isinstance(payload, list):
            yield "["
            stack.append(("text", "]"))
            for index, element in reversed(list(enumerate(payload))):
                stack.append(("visit", element))
                if index:
                    stack.append(("text", ", "))
        else:
            yield repr(payload)


def _is_empty(value) -> bool:
    """Whether a field contributes nothing, the way both interpreters agree on.

    A field is a node, a list of them, a scalar or ``None``. A list holds no
    contribution exactly when it holds no element, which covers the run of
    ``None`` defaults that ``ast`` uses for absent optional arguments.
    """
    if value is None:
        return True
    if isinstance(value, list):
        return all(item is None for item in value)
    return False
