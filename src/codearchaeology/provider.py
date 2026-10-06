"""Reach a model, and return what it said. Nothing else.

This is the only module in the tool that reaches a network. Everything above it
— the whole of v0.1 to v0.3, the context builder and the command that renders an
answer — runs with no networking module in its import graph, which is what makes
the offline path work on a machine with no network, no key and no provider
configured. ``tests/test_context.py`` holds that side of the line; this side is
the one place allowed to cross it.

The rules implemented here are the architecture freeze's, and each is a decision
with a reason rather than a preference:

* **The deadline is wall clock around the whole call**, not a socket timeout. A
  socket timeout fires per read, so an endpoint dribbling a byte every 30 seconds
  keeps it from ever firing while the command hangs. The socket timeout is still
  set — it is what actually ends a stalled read — and a worker thread joined with
  the deadline is the backstop for everything else.
* **Retry only where repeating can help**: connection and DNS failures, a read
  that timed out, ``429`` and ``5xx``. Every other ``4xx`` is the request or the
  credentials being wrong, and repeating it spends the user's money to fail
  identically. A ``200`` whose body is not the expected envelope is not retried
  either, because the freeze enumerated the retryable set and this is not in it.
* **A provider failure is the call not producing an answer.** What the model
  *said* is never this module's business: the text goes back as it arrived, and
  the core decides whether it is usable. The one exception is an answer the
  output ceiling cut off, which is reported here because the ceiling is this
  module's and the failure has to name it rather than blame the model.
* **The key never reaches a message.** Every error is scrubbed before it is
  built, because an endpoint that echoes the failed request would otherwise put
  the key in a traceback.
"""

import json
import os
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Protocol

DEFAULT_TIMEOUT = 60
DEFAULT_MAX_RETRIES = 2

# The seconds to wait before each retry. The list is the policy: a third attempt
# waits the third entry, and a list shorter than the retry count waits the last
# one again.
BACKOFF_SECONDS = (1, 2)

# The statuses worth repeating. 408 is a request timeout, which is the same
# situation as a read that timed out; 429 is the endpoint asking for less.
RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})

# Roughly four characters per token. The standard library has no tokeniser and
# shipping one would add a dependency that is model-specific besides, so this is
# an estimate — and it is called one wherever a number derived from it is shown.
CHARACTERS_PER_TOKEN = 4

# How much of a response is read. Far above any answer a model can produce inside
# the output ceiling, so a response past it is not a completion — a proxy's error
# page or something worse — and reading it to the end is reading whatever the
# endpoint decided to send.
MAX_RESPONSE_BYTES = 1_000_000

BASE_URL_VARIABLE = "CODEARCHAEOLOGY_AI_BASE_URL"
MODEL_VARIABLE = "CODEARCHAEOLOGY_AI_MODEL"
KEY_VARIABLE = "CODEARCHAEOLOGY_AI_API_KEY"
FALLBACK_KEY_VARIABLE = "OPENAI_API_KEY"
TIMEOUT_VARIABLE = "CODEARCHAEOLOGY_AI_TIMEOUT"
RETRIES_VARIABLE = "CODEARCHAEOLOGY_AI_MAX_RETRIES"
CONTEXT_LIMIT_VARIABLE = "CODEARCHAEOLOGY_AI_MAX_CONTEXT_TOKENS"
OUTPUT_LIMIT_VARIABLE = "CODEARCHAEOLOGY_AI_MAX_OUTPUT_TOKENS"

REDACTED = "***"


class ProviderError(RuntimeError):
    """Raised when a call did not produce an answer, or produced a cut-off one."""


class ExplanationProvider(Protocol):
    """Reach a model and return what it said. Nothing else.

    The interface is one method and its narrowness is the design: parsing,
    validating the schema and deriving ``confidence`` belong to the core, so
    every provider gets the identical check and a new one cannot quietly
    disagree with the others about what a valid answer is.

    *context* is the whole of what the model is sent — the evidence as the
    context builder wrote it, wrapped in the instructions that ask for the
    schema. A provider does not add to it, does not read the repository and does
    not see a database.
    """

    def explain(self, context: str) -> str:
        """Return the model's answer as text, or raise :class:`ProviderError`."""


def estimate_tokens(text: str) -> int:
    """About how many tokens *text* is, for the ceiling and for nothing else.

    An estimate, deliberately: a real count needs the model's own tokeniser, and
    a wrong-but-conservative number that refuses a call is better than a
    confident one that lets a truncated bundle through.
    """
    return max(1, len(text) // CHARACTERS_PER_TOKEN)


@dataclass(frozen=True, slots=True)
class Settings:
    """Where the model is, and the limits the call runs under."""

    base_url: str
    model: str
    api_key: str = ""
    timeout: int = DEFAULT_TIMEOUT
    max_retries: int = DEFAULT_MAX_RETRIES
    max_context_tokens: int | None = None
    max_output_tokens: int | None = None

    @classmethod
    def from_environment(cls, environ=None) -> "Settings | None":
        """Read the settings, or ``None`` when no endpoint is configured.

        ``None`` is the offline path, not an error: a machine with no endpoint
        set is a machine that gets the evidence and no prose, which is what Core
        First requires. Both the endpoint and the model have to be named for a
        call to be possible at all.
        """
        source = os.environ if environ is None else environ
        base_url = (source.get(BASE_URL_VARIABLE) or "").strip()
        model = (source.get(MODEL_VARIABLE) or "").strip()
        if not base_url or not model:
            return None

        return cls(
            base_url=base_url,
            model=model,
            api_key=(
                source.get(KEY_VARIABLE) or source.get(FALLBACK_KEY_VARIABLE) or ""
            ).strip(),
            timeout=_positive_number(source, TIMEOUT_VARIABLE, DEFAULT_TIMEOUT),
            max_retries=_number(source, RETRIES_VARIABLE, DEFAULT_MAX_RETRIES),
            max_context_tokens=_optional_number(source, CONTEXT_LIMIT_VARIABLE),
            max_output_tokens=_optional_number(source, OUTPUT_LIMIT_VARIABLE),
        )


class OpenAICompatibleProvider:
    """One adapter for every endpoint that speaks the OpenAI chat shape.

    OpenAI, DeepSeek, Ollama, vLLM and LM Studio all take the same request and
    answer with the same envelope, so what separates them is configuration —
    the endpoint, the model, the key — rather than a class each. Pointing
    ``BASE_URL`` at a local Ollama is the Local First case and needs no code of
    its own, and the whole adapter is the standard library: the tool keeps
    ``typer`` and ``rich`` and nothing else.
    """

    def __init__(self, settings: Settings):
        self.settings = settings

    def explain(self, context: str) -> str:
        self._refuse_if_over_the_ceiling(context)
        request = self._request(context)
        attempts = max(1, self.settings.max_retries + 1)

        for attempt in range(attempts):
            try:
                return self._send(request)
            except _Retryable as error:
                if attempt + 1 == attempts:
                    raise ProviderError(
                        self._scrub(f"{error}; gave up after {attempts} attempts")
                    ) from None
                time.sleep(_backoff(attempt, error.retry_after))

        raise AssertionError("the retry loop returned nothing")

    # What is sent.

    def _refuse_if_over_the_ceiling(self, context: str) -> None:
        """Refuse an oversized context rather than let the endpoint truncate it.

        A model answering about a partial bundle believes it has the whole one,
        and the result looks exactly like a well-supported answer. That is the
        same failure as a silently truncated diff, so the refusal names the size
        and the limit and stops.

        The message says the evidence was already reduced, because by the time a
        context reaches here it has been: ``selection`` cuts a large commit down
        to its largest entries before this is asked. What is left over the limit
        is a limit that is too low for the model, not a commit that is too large —
        which is why the remedy named is the window rather than a smaller commit.
        """
        limit = self.settings.max_context_tokens
        if limit is None:
            return
        size = estimate_tokens(context)
        if size > limit:
            raise ProviderError(
                f"the evidence for this commit is about {size} tokens, already"
                f" reduced to its largest entries, and the configured limit is"
                f" {limit}; raise {CONTEXT_LIMIT_VARIABLE} or use a model with a"
                f" larger window — the evidence is not sent truncated"
            )

    def _request(self, context: str) -> urllib.request.Request:
        body = {
            "model": self.settings.model,
            "messages": [{"role": "user", "content": context}],
        }
        if self.settings.max_output_tokens is not None:
            body["max_tokens"] = self.settings.max_output_tokens

        headers = {"Content-Type": "application/json"}
        if self.settings.api_key:
            headers["Authorization"] = f"Bearer {self.settings.api_key}"

        url = f"{self.settings.base_url.rstrip('/')}/chat/completions"
        try:
            return urllib.request.Request(
                url,
                data=json.dumps(body).encode("utf-8"),
                headers=headers,
                method="POST",
            )
        except ValueError as error:
            # A base URL with no scheme — `api.example.com/v1`, the way a host is
            # written everywhere else — raises here, and without this it reaches
            # the user as a Python traceback from inside urllib. A typo in a
            # configuration variable is the most likely thing to go wrong on a
            # first run, and it has to come back as a sentence naming the
            # variable rather than as a stack.
            raise ProviderError(
                f"{BASE_URL_VARIABLE} is not a URL this can call ({error});"
                f" it needs a scheme, like http://127.0.0.1:11434/v1"
            ) from None

    # What comes back.

    def _send(self, request: urllib.request.Request) -> str:
        """One attempt, bounded by the deadline rather than by a socket.

        The call runs on a worker thread the caller gives up on rather than
        kills — Python cannot kill a thread — so the socket timeout is also set,
        and it is what actually ends a stalled read. The thread is the backstop
        for an endpoint that keeps the read alive by dribbling bytes, which is
        the case a socket timeout alone cannot catch.
        """
        outcome: list[tuple[str, object]] = []

        def call() -> None:
            try:
                outcome.append(("answer", self._once(request)))
            except BaseException as error:  # noqa: BLE001 - handed back below
                outcome.append(("failure", error))

        worker = threading.Thread(target=call, daemon=True)
        worker.start()
        worker.join(self.settings.timeout)

        if not outcome:
            raise _Retryable(
                f"the call did not finish within {self.settings.timeout} seconds"
            )
        kind, value = outcome[0]
        if kind == "failure":
            raise value  # type: ignore[misc]
        return value  # type: ignore[return-value]

    def _once(self, request: urllib.request.Request) -> str:
        try:
            with urllib.request.urlopen(request, timeout=self.settings.timeout) as answer:
                payload = answer.read(MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as error:
            raise self._http_error(error) from None
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise _Retryable(f"the endpoint could not be reached: {error}") from None

        if len(payload) > MAX_RESPONSE_BYTES:
            # Not a retryable failure: an endpoint that sends this much sends it
            # again. The ceiling is far above any answer a model can produce
            # inside the output limit, so reaching it means the endpoint is not
            # answering with a completion at all.
            raise ProviderError(
                f"the endpoint sent more than {MAX_RESPONSE_BYTES} bytes, which is"
                f" not an answer to this request"
            )

        return self._answer(payload)

    def _http_error(self, error: urllib.error.HTTPError) -> Exception:
        detail = f"the endpoint answered {error.code}"
        if error.code in RETRYABLE_STATUS:
            return _Retryable(detail, retry_after=_retry_after(error))
        # Every other status is the request or the credentials being wrong, and
        # repeating it would spend the user's money to fail the same way.
        return ProviderError(self._scrub(f"{detail}: {_reason(error)}"))

    def _answer(self, payload: bytes) -> str:
        try:
            envelope = json.loads(payload.decode("utf-8"))
            choice = envelope["choices"][0]
            text = choice["message"]["content"]
        except (UnicodeDecodeError, json.JSONDecodeError, KeyError, IndexError, TypeError) as error:
            raise ProviderError(
                f"the endpoint answered with something that is not a completion: {error}"
            ) from None

        if not isinstance(text, str):
            raise ProviderError("the endpoint's answer held no text")

        if choice.get("finish_reason") == "length":
            # The answer is cut mid-sentence, and the core would report that as
            # the model producing something unusable. It is not the model's
            # doing, so the ceiling is named here.
            raise ProviderError(
                f"the answer was cut off at the output ceiling of"
                f" {self.settings.max_output_tokens} tokens;"
                f" raise {OUTPUT_LIMIT_VARIABLE} or ask for less"
            )

        return text

    def _scrub(self, message: str) -> str:
        """Take the key out of a message before it can be shown or logged.

        An endpoint that echoes the failed request puts the key in the text, and
        a key in a traceback is a key that has to be rotated.
        """
        key = self.settings.api_key
        return message if not key else message.replace(key, REDACTED)


class _Retryable(Exception):
    """A failure worth repeating, carrying what the endpoint asked to wait."""

    def __init__(self, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


def _backoff(attempt: int, retry_after: float | None) -> float:
    """How long to wait before attempt *attempt + 1*.

    A ``Retry-After`` the endpoint sent wins over the schedule: it is the
    endpoint saying what it can take, and ignoring it is how a client turns a
    throttle into a block.
    """
    if retry_after is not None:
        return max(0.0, retry_after)
    return float(BACKOFF_SECONDS[min(attempt, len(BACKOFF_SECONDS) - 1)])


def _retry_after(error: urllib.error.HTTPError) -> float | None:
    header = error.headers.get("Retry-After") if error.headers else None
    if header is None:
        return None
    try:
        return float(header)
    except ValueError:
        # The header may also be an HTTP date, which is not worth parsing for a
        # number that only decides how long to wait; the schedule is used then.
        return None


def _reason(error: urllib.error.HTTPError) -> str:
    try:
        body = error.read().decode("utf-8", errors="replace").strip()
    except OSError:
        return error.reason if isinstance(error.reason, str) else "no detail"
    return body[:400] if body else "no detail"


def _number(source, name: str, default: int) -> int:
    value = (source.get(name) or "").strip()
    if not value:
        return default
    try:
        return max(0, int(value))
    except ValueError:
        return default


def _positive_number(source, name: str, default: int) -> int:
    """Like :func:`_number`, but zero falls back too.

    For a deadline, zero is not a setting — it is a typo, and the failure it
    produces ("did not finish within 0 seconds") names the symptom rather than
    the mistake. Retries are different: zero retries is a real choice, which is
    why that one keeps ``_number``.
    """
    value = _number(source, name, default)
    return default if value < 1 else value


def _optional_number(source, name: str) -> int | None:
    value = (source.get(name) or "").strip()
    if not value:
        return None
    try:
        return max(0, int(value))
    except ValueError:
        return None
