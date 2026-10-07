"""What a captured command line said, with the colour taken out of it.

Typer renders a usage error through Rich, and Rich styles the parts of the
message — ``--json`` in ``No such option: --json`` gets its own escape sequences,
so the sentence a person reads is not a substring of the bytes the tool wrote.
Whether colour is on is the environment's business and not the tool's: a CI
runner has it forced on (the escapes are in the logs on both runners), a
developer's shell may have it off, and an assertion on the raw text therefore
passes on one machine and fails on the next. That is the worst of both, and it is
what this module exists to prevent: the tests read the words.
"""

import re

# CSI sequences, which is all Rich emits: ESC [ parameters letter.
ESCAPE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def plain(text: str) -> str:
    """The text as a person reads it: escape sequences removed."""
    return ESCAPE.sub("", text)
