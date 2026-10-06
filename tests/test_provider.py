"""Tests for the provider: the one module allowed to reach a network.

Nothing here reaches the network. Every test points the adapter at a local HTTP
server that answers from a script, which is what makes a networked feature
testable on a machine with no network at all — the same reason the command tests
hand the command a provider that never leaves the process.

What is under test is the freeze's policy rather than the HTTP itself: which
failures are repeated and which are not, what the deadline bounds, what happens
to a key inside an error message, and what an oversized context does.
"""

import contextlib
import http.server
import json
import threading
import urllib.error

import pytest

from codearchaeology import provider as provider_module
from codearchaeology.provider import (
    DEFAULT_MAX_RETRIES,
    DEFAULT_TIMEOUT,
    KEY_VARIABLE,
    MAX_RESPONSE_BYTES,
    ProviderError,
    Settings,
    OpenAICompatibleProvider,
    estimate_tokens,
)

KEY = "sk-not-a-real-key-12345"


class _Endpoint(http.server.BaseHTTPRequestHandler):
    """A scripted endpoint: one reply per request, in order."""

    def do_POST(self) -> None:  # noqa: N802 - the name http.server calls
        length = int(self.headers.get("Content-Length", 0))
        self.server.requests.append(
            {
                "path": self.path,
                "headers": dict(self.headers),
                "body": json.loads(self.rfile.read(length).decode("utf-8")),
            }
        )
        reply = self.server.replies.pop(0)
        if reply.get("delay"):
            import time as clock

            clock.sleep(reply["delay"])
        body = reply.get("body", b"{}")
        self.send_response(reply["status"])
        for name, value in reply.get("headers", {}).items():
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *arguments: object) -> None:
        """Keep the server's own logging out of the test output."""


class _Server(http.server.HTTPServer):
    def __init__(self, replies):
        super().__init__(("127.0.0.1", 0), _Endpoint)
        self.replies = list(replies)
        self.requests: list[dict] = []

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.server_port}/v1"


@contextlib.contextmanager
def _endpoint(*replies):
    """A local endpoint that answers with *replies*, one per request."""
    server = _Server(replies)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()


def _completion(text: str, finish_reason: str = "stop") -> bytes:
    return json.dumps(
        {
            "choices": [
                {
                    "message": {"role": "assistant", "content": text},
                    "finish_reason": finish_reason,
                }
            ]
        }
    ).encode("utf-8")


def _settings(base_url: str, **overrides) -> Settings:
    values = {
        "base_url": base_url,
        "model": "a-model",
        "api_key": KEY,
        "timeout": 5,
        "max_retries": DEFAULT_MAX_RETRIES,
    }
    values.update(overrides)
    return Settings(**values)


@contextlib.contextmanager
def _no_waiting(monkeypatch):
    """Keep the real backoff schedule but record it instead of sleeping it.

    The schedule itself is patched rather than ``time.sleep``, because
    ``time.sleep`` is the same module object the test's own endpoint uses — a
    monkeypatch of it silently turns the endpoint's delays into no-ops, and a
    deadline test against an endpoint that no longer delays proves nothing.
    """
    waits: list[float] = []
    real = provider_module._backoff

    def recorded(attempt: int, retry_after: float | None) -> float:
        waits.append(real(attempt, retry_after))
        return 0.0

    monkeypatch.setattr(provider_module, "_backoff", recorded)
    yield waits


# What the environment says.


def test_no_endpoint_means_no_settings():
    """The offline path, and it is not an error: it is a machine with no model."""
    assert Settings.from_environment({}) is None
    assert Settings.from_environment({"CODEARCHAEOLOGY_AI_BASE_URL": "http://x"}) is None
    assert Settings.from_environment({"CODEARCHAEOLOGY_AI_MODEL": "m"}) is None


def test_the_settings_are_read_from_the_environment():
    settings = Settings.from_environment(
        {
            "CODEARCHAEOLOGY_AI_BASE_URL": "http://127.0.0.1:9/v1",
            "CODEARCHAEOLOGY_AI_MODEL": "a-model",
            "CODEARCHAEOLOGY_AI_API_KEY": KEY,
            "CODEARCHAEOLOGY_AI_TIMEOUT": "7",
            "CODEARCHAEOLOGY_AI_MAX_RETRIES": "0",
            "CODEARCHAEOLOGY_AI_MAX_CONTEXT_TOKENS": "1000",
            "CODEARCHAEOLOGY_AI_MAX_OUTPUT_TOKENS": "500",
        }
    )

    assert settings == Settings(
        base_url="http://127.0.0.1:9/v1",
        model="a-model",
        api_key=KEY,
        timeout=7,
        max_retries=0,
        max_context_tokens=1000,
        max_output_tokens=500,
    )


def test_the_vendors_own_key_variable_is_the_fallback():
    """A machine already configured for another tool needs no second copy."""
    settings = Settings.from_environment(
        {
            "CODEARCHAEOLOGY_AI_BASE_URL": "http://127.0.0.1:9/v1",
            "CODEARCHAEOLOGY_AI_MODEL": "a-model",
            "OPENAI_API_KEY": KEY,
        }
    )

    assert settings.api_key == KEY


def test_the_defaults_are_the_frozen_ones():
    settings = Settings.from_environment(
        {
            "CODEARCHAEOLOGY_AI_BASE_URL": "http://127.0.0.1:9/v1",
            "CODEARCHAEOLOGY_AI_MODEL": "a-model",
        }
    )

    assert settings.timeout == DEFAULT_TIMEOUT
    assert settings.max_retries == DEFAULT_MAX_RETRIES
    assert settings.max_context_tokens is None
    assert settings.max_output_tokens is None


def test_a_nonsense_number_falls_back_rather_than_failing():
    """A typo in an environment variable is not a reason to refuse to run."""
    settings = Settings.from_environment(
        {
            "CODEARCHAEOLOGY_AI_BASE_URL": "http://127.0.0.1:9/v1",
            "CODEARCHAEOLOGY_AI_MODEL": "a-model",
            "CODEARCHAEOLOGY_AI_TIMEOUT": "soon",
        }
    )

    assert settings.timeout == DEFAULT_TIMEOUT


@pytest.mark.parametrize("value", ["0", "-1", "0.5"])
def test_a_deadline_of_zero_or_less_falls_back_too(value):
    """Zero is not a setting for a deadline, it is a typo.

    Left alone it produces "did not finish within 0 seconds" on every call, which
    names the symptom and not the mistake. Retries keep their zero, because no
    retries is a real choice.
    """
    settings = Settings.from_environment(
        {
            "CODEARCHAEOLOGY_AI_BASE_URL": "http://127.0.0.1:9/v1",
            "CODEARCHAEOLOGY_AI_MODEL": "a-model",
            "CODEARCHAEOLOGY_AI_TIMEOUT": value,
        }
    )

    assert settings.timeout == DEFAULT_TIMEOUT


def test_no_retries_is_still_a_setting():
    settings = Settings.from_environment(
        {
            "CODEARCHAEOLOGY_AI_BASE_URL": "http://127.0.0.1:9/v1",
            "CODEARCHAEOLOGY_AI_MODEL": "a-model",
            "CODEARCHAEOLOGY_AI_MAX_RETRIES": "0",
        }
    )

    assert settings.max_retries == 0


def test_the_estimate_is_about_a_token_per_four_characters():
    assert estimate_tokens("") == 1
    assert estimate_tokens("a" * 40) == 10


# What is sent.


def test_the_request_is_the_shape_the_endpoints_share():
    with _endpoint({"status": 200, "body": _completion("hello")}) as server:
        answer = OpenAICompatibleProvider(_settings(server.base_url)).explain("EVIDENCE")

    assert answer == "hello"
    sent = server.requests[0]
    assert sent["path"] == "/v1/chat/completions"
    assert sent["headers"]["Authorization"] == f"Bearer {KEY}"
    assert sent["headers"]["Content-Type"] == "application/json"
    assert sent["body"] == {
        "model": "a-model",
        "messages": [{"role": "user", "content": "EVIDENCE"}],
    }


def test_no_key_means_no_authorization_header():
    """A local endpoint needs no key, and sending an empty one would be a lie."""
    with _endpoint({"status": 200, "body": _completion("hello")}) as server:
        OpenAICompatibleProvider(_settings(server.base_url, api_key="")).explain("E")

    assert "Authorization" not in server.requests[0]["headers"]


def test_the_output_ceiling_is_asked_for_when_one_is_set():
    with _endpoint({"status": 200, "body": _completion("hello")}) as server:
        OpenAICompatibleProvider(
            _settings(server.base_url, max_output_tokens=256)
        ).explain("E")

    assert server.requests[0]["body"]["max_tokens"] == 256


def test_an_oversized_context_is_refused_before_anything_is_sent():
    """Truncating would let the model answer about a bundle it does not have."""
    with _endpoint({"status": 200, "body": _completion("hello")}) as server:
        provider = OpenAICompatibleProvider(
            _settings(server.base_url, max_context_tokens=10)
        )
        with pytest.raises(ProviderError) as raised:
            provider.explain("a" * 4000)

    assert "not sent truncated" in str(raised.value)
    assert server.requests == [], "nothing was sent"


# What is repeated, and what is not.


def test_a_server_error_is_repeated_and_the_second_attempt_can_succeed(monkeypatch):
    with _no_waiting(monkeypatch):
        with _endpoint(
            {"status": 503, "body": b"busy"},
            {"status": 200, "body": _completion("second time")},
        ) as server:
            answer = OpenAICompatibleProvider(_settings(server.base_url)).explain("E")

    assert answer == "second time"
    assert len(server.requests) == 2


def test_a_bad_key_is_not_repeated():
    """401 is the credentials being wrong; repeating it fails the same way."""
    with _endpoint({"status": 401, "body": b"no"}) as server:
        with pytest.raises(ProviderError) as raised:
            OpenAICompatibleProvider(_settings(server.base_url)).explain("E")

    assert len(server.requests) == 1
    assert "401" in str(raised.value)


def test_giving_up_says_how_many_attempts_were_made(monkeypatch):
    """'Failed after 3 attempts' is a different situation from 'failed'."""
    with _no_waiting(monkeypatch):
        with _endpoint(
            {"status": 500, "body": b"no"},
            {"status": 500, "body": b"no"},
            {"status": 500, "body": b"no"},
        ) as server:
            with pytest.raises(ProviderError) as raised:
                OpenAICompatibleProvider(_settings(server.base_url)).explain("E")

    assert "after 3 attempts" in str(raised.value)
    assert len(server.requests) == 3


def test_the_backoff_is_the_frozen_schedule(monkeypatch):
    with _no_waiting(monkeypatch) as waits:
        with _endpoint(
            {"status": 500, "body": b"no"},
            {"status": 500, "body": b"no"},
            {"status": 500, "body": b"no"},
        ) as server:
            with pytest.raises(ProviderError):
                OpenAICompatibleProvider(_settings(server.base_url)).explain("E")

    assert waits == [1, 2]


def test_retry_after_wins_over_the_schedule(monkeypatch):
    """The endpoint saying what it can take beats a schedule we chose."""
    with _no_waiting(monkeypatch) as waits:
        with _endpoint(
            {"status": 429, "headers": {"Retry-After": "9"}, "body": b"slow down"},
            {"status": 200, "body": _completion("ok")},
        ) as server:
            OpenAICompatibleProvider(_settings(server.base_url)).explain("E")

    assert waits == [9]


def test_no_retries_means_one_attempt(monkeypatch):
    with _no_waiting(monkeypatch):
        with _endpoint({"status": 500, "body": b"no"}) as server:
            with pytest.raises(ProviderError) as raised:
                OpenAICompatibleProvider(
                    _settings(server.base_url, max_retries=0)
                ).explain("E")

    assert len(server.requests) == 1
    assert "after 1 attempts" in str(raised.value)


# What the answer has to be.


def test_an_envelope_that_is_not_a_completion_is_a_provider_failure():
    with _endpoint({"status": 200, "body": b"not json at all"}) as server:
        with pytest.raises(ProviderError) as raised:
            OpenAICompatibleProvider(_settings(server.base_url)).explain("E")

    assert "not a completion" in str(raised.value)


def test_an_answer_with_no_text_is_refused():
    body = json.dumps({"choices": [{"message": {"content": None}}]}).encode()
    with _endpoint({"status": 200, "body": body}) as server:
        with pytest.raises(ProviderError) as raised:
            OpenAICompatibleProvider(_settings(server.base_url)).explain("E")

    assert "no text" in str(raised.value)


def test_a_cut_off_answer_names_the_ceiling_rather_than_the_model():
    """The core would see broken JSON; the ceiling is what actually broke it."""
    with _endpoint(
        {"status": 200, "body": _completion('{"summary": "half', "length")}
    ) as server:
        with pytest.raises(ProviderError) as raised:
            OpenAICompatibleProvider(
                _settings(server.base_url, max_output_tokens=64)
            ).explain("E")

    assert "output ceiling of 64 tokens" in str(raised.value)


# What must never leak.


def test_the_key_never_reaches_a_message():
    """An endpoint that echoes the request would otherwise put the key in a
    traceback, and a key in a traceback has to be rotated."""
    echoed = json.dumps({"error": f"bad request with {KEY} in it"}).encode()
    with _endpoint({"status": 400, "body": echoed}) as server:
        with pytest.raises(ProviderError) as raised:
            OpenAICompatibleProvider(_settings(server.base_url)).explain("E")

    assert KEY not in str(raised.value)
    assert "***" in str(raised.value)


def test_the_key_never_reaches_a_message_from_a_retryable_failure(monkeypatch):
    with _no_waiting(monkeypatch):
        with _endpoint({"status": 500, "body": f"busy {KEY}".encode()}) as server:
            with pytest.raises(ProviderError) as raised:
                OpenAICompatibleProvider(
                    _settings(server.base_url, max_retries=0)
                ).explain("E")

    assert KEY not in str(raised.value)


# The deadline.


def test_a_call_that_outlives_the_deadline_is_given_up_on(monkeypatch):
    """The whole call is bounded, not each read.

    The endpoint is made to hold the answer open forever, which is the case a
    socket timeout alone cannot catch: a read that keeps receiving bytes never
    times out, so an endpoint dribbling them keeps the command hanging. The
    joined worker is what ends the wait, and the message says which limit it was.
    """
    holding = threading.Event()

    def never_answers(request, timeout=None):
        holding.wait()

    monkeypatch.setattr(provider_module.urllib.request, "urlopen", never_answers)
    provider = OpenAICompatibleProvider(
        _settings("http://127.0.0.1:1/v1", timeout=1, max_retries=0)
    )

    try:
        with pytest.raises(ProviderError) as raised:
            provider.explain("E")
        assert "did not finish within 1 seconds" in str(raised.value)
    finally:
        holding.set()


@pytest.mark.parametrize(
    "failure",
    [
        urllib.error.URLError("connection refused"),
        TimeoutError("the read timed out"),
        ConnectionResetError("connection reset"),
    ],
)
def test_a_call_that_never_arrived_is_reported_rather_than_raised_raw(
    monkeypatch, failure
):
    """A refused connection, a stalled read and a reset socket are one answer.

    All three are the call not producing an answer, so all three are the
    provider's failure to report — and all three are worth repeating, which is
    what keeps them out of the model-failure path in the core.
    """

    def refuses(request, timeout=None):
        raise failure

    monkeypatch.setattr(provider_module.urllib.request, "urlopen", refuses)
    provider = OpenAICompatibleProvider(
        _settings("http://127.0.0.1:1/v1", timeout=2, max_retries=0)
    )

    with pytest.raises(ProviderError) as raised:
        provider.explain("E")

    assert "could not be reached" in str(raised.value)


def test_a_urlerror_is_not_confused_with_an_http_error():
    """``HTTPError`` is a subclass of ``URLError``; the order of the handlers is
    what keeps a 401 from being reported as an unreachable host."""
    assert issubclass(urllib.error.HTTPError, urllib.error.URLError)


# Configuration that is wrong rather than unavailable.


def test_a_base_url_with_no_scheme_is_a_sentence_not_a_traceback():
    """The most likely thing to go wrong on a first run.

    ``api.example.com/v1`` is how a host is written everywhere else, and without
    this it reaches the user as a ``ValueError`` raised inside urllib — a Python
    traceback for a typo in a configuration variable.
    """
    provider = OpenAICompatibleProvider(_settings("api.example.com/v1"))

    with pytest.raises(ProviderError) as raised:
        provider.explain("E")

    assert "CODEARCHAEOLOGY_AI_BASE_URL" in str(raised.value)
    assert "needs a scheme" in str(raised.value)


def test_a_base_url_with_no_scheme_never_reaches_the_network(monkeypatch):
    """It is refused while the request is being built, not while it is sent."""

    def refuse(*arguments: object, **keywords: object) -> None:
        raise AssertionError("a request was sent")

    monkeypatch.setattr(provider_module.urllib.request, "urlopen", refuse)
    provider = OpenAICompatibleProvider(_settings("api.example.com/v1"))

    with pytest.raises(ProviderError):
        provider.explain("E")


def test_an_oversized_response_is_refused_rather_than_read():
    """An endpoint sending a megabyte is not answering the question.

    The ceiling is far above any answer a model can produce inside the output
    limit, so reaching it means what came back is not a completion — a proxy's
    error page, or something worse — and reading it to the end is reading
    whatever the endpoint decided to send.
    """
    huge = b"x" * (MAX_RESPONSE_BYTES + 1)
    with _endpoint({"status": 200, "body": huge}) as server:
        with pytest.raises(ProviderError) as raised:
            OpenAICompatibleProvider(_settings(server.base_url)).explain("E")

    assert "more than" in str(raised.value)
    assert len(server.requests) == 1, "and it is not worth repeating"
