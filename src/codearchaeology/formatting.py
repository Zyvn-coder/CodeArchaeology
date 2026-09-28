"""Small helpers shared by the terminal views."""

from rich.text import Text

SHORT_SHA_LENGTH = 8


def shorten(text: str, width: int) -> Text:
    """Cut *text* down to *width* terminal cells, counting CJK as two wide.

    Rich measures a column that is not allowed to wrap at the full width of its
    own content, so a single long value makes it shrink every other column and
    the sha and the date come out as ``fa…`` and ``2026…``. Cutting the text
    here means every cell already fits and nothing has to be squeezed.

    ``Text.truncate`` shortens in place and returns ``None``, so the object has
    to be built first and returned afterwards.
    """
    shortened = Text(text)
    shortened.truncate(width, overflow="ellipsis")
    return shortened
