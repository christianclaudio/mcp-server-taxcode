"""Tests for error handling and secret redaction."""

import json

import pytest
from fastmcp.exceptions import ToolError

from taxcode_mcp.errors import (
    MASK,
    AuthenticationError,
    RateLimitError,
    ResourceNotFoundError,
    SafetyViolationError,
    TemplateError,
    redact_message,
    redact_secrets,
    tool_error,
    tool_failure,
)


def test_redact_secrets() -> None:
    """Verify regex secret redaction across standard sensitive key patterns."""
    assert redact_secrets("") == ""
    assert "Bearer [REDACTED]" in redact_secrets("Authorization: Bearer my-secret-token-12345")
    assert "api_key=[REDACTED]" in redact_secrets("api_key=secret-key-12345678")
    assert "client_secret: [REDACTED]" in redact_secrets("client_secret: secret-value-99999")
    assert "password: [REDACTED]" in redact_secrets("password: supersecret123")


def test_custom_exceptions() -> None:
    """Verify TemplateError and its specialized subclasses redact secrets and store details."""
    err = TemplateError("Bearer secret-token-abcdefgh", details={"code": 100})
    assert "Bearer [REDACTED]" in err.message
    assert err.details["code"] == 100

    auth_err = AuthenticationError("Auth failed")
    assert isinstance(auth_err, TemplateError)

    not_found = ResourceNotFoundError("Not found")
    assert isinstance(not_found, TemplateError)

    rate_err = RateLimitError("Rate limit")
    assert isinstance(rate_err, TemplateError)

    safety_err = SafetyViolationError("Safety violated")
    assert isinstance(safety_err, TemplateError)


def test_tool_error_redacts_the_exception_message() -> None:
    """tool_error() redacts the message itself; a plain exception carries the raw secret."""
    secret = "sk-live-tool-error-0123456789"
    err = tool_error(RuntimeError(f"upstream 401 for Authorization: Bearer {secret}"))
    assert isinstance(err, ToolError)
    assert secret not in str(err)
    assert str(err) == "upstream 401 for Authorization: Bearer [REDACTED]"


def test_tool_failure_is_redacted_json() -> None:
    """tool_failure() carries the {"error": {...}} JSON, redacted as a whole document."""
    err = tool_failure(
        "batch_failed", "Bearer secret-token-abc", errors=[{"detail": "Bearer secret-token-xyz"}]
    )
    assert isinstance(err, ToolError)
    payload = json.loads(str(err))["error"]
    assert payload == {
        "type": "batch_failed",
        "message": "Bearer [REDACTED]",
        "errors": [{"detail": "Bearer [REDACTED]"}],
    }


def test_redact_secrets_token_forms() -> None:
    """api/access/refresh tokens and a bare token= query parameter are redacted in every form."""
    cases = {
        "api_token=SECRET1": "api_token=[REDACTED]",
        "api-token: SECRET1": "api-token: [REDACTED]",
        "access_token: SECRET2": "access_token: [REDACTED]",
        '{"refresh_token": "SECRET4"}': '{"refresh_token": "[REDACTED]"}',
        '{"m": "{\\"access_token\\": \\"SECRET5\\"}"}': (
            '{"m": "{\\"access_token\\": \\"[REDACTED]\\"}"}'
        ),
        "GET https://api.example.com/x?token=SECRET3&page=2": (
            "GET https://api.example.com/x?token=[REDACTED]&page=2"
        ),
        "url=/x?a=1&TOKEN=SECRET6": "url=/x?a=1&TOKEN=[REDACTED]",
        "refresh_token=a.b-c_d/e+f==": "refresh_token=[REDACTED]",
    }
    for raw, expected in cases.items():
        assert redact_secrets(raw) == expected, raw


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        pytest.param("auth_token=SECRET7", "auth_token=[REDACTED]", id="auth_token"),
        pytest.param('{"id_token": "SECRET8"}', '{"id_token": "[REDACTED]"}', id="id_token"),
        pytest.param(
            "session-token: SECRET9&x=1", "session-token: [REDACTED]&x=1", id="session_token"
        ),
        pytest.param(
            "X-Auth-Token: SECRET10\nAccept: */*",
            "X-Auth-Token: [REDACTED]\nAccept: */*",
            id="x_auth_token_header",
        ),
        pytest.param(
            "Authorization: Token SECRET11 rejected",
            "Authorization: Token [REDACTED] rejected",
            id="authorization_token",
        ),
        pytest.param(
            "{'Authorization': 'Token SECRET12'}",
            "{'Authorization': 'Token [REDACTED]'}",
            id="authorization_token_dict",
        ),
        pytest.param('{"token": "SECRET13"}', '{"token": "[REDACTED]"}', id="json_token"),
        pytest.param(
            '{"m": "{\\"token\\": \\"SECRET14\\"}"}',
            '{"m": "{\\"token\\": \\"[REDACTED]\\"}"}',
            id="json_token_escaped",
        ),
        pytest.param(
            "cb=https%3A%2F%2Fh%2Fx%3Faccess_token%3DSECRET15%26x%3D1%23frag",
            "cb=https%3A%2F%2Fh%2Fx%3Faccess_token%3D[REDACTED]%26x%3D1%23frag",
            id="url_encoded_access_token",
        ),
        pytest.param(
            "cb=https%3A%2F%2Fh%2Fx%3Faccess_token%3DSECRET21%23frag",
            "cb=https%3A%2F%2Fh%2Fx%3Faccess_token%3D[REDACTED]%23frag",
            id="url_encoded_access_token_fragment",
        ),
        pytest.param(
            "q=api_token%3DS16%26refresh_token%3DS17%26auth_token%3DS18"
            "%26id_token%3DS19%26session_token%3DS20",
            "q=api_token%3D[REDACTED]%26refresh_token%3D[REDACTED]%26auth_token%3D[REDACTED]"
            "%26id_token%3D[REDACTED]%26session_token%3D[REDACTED]",
            id="url_encoded_other_keys",
        ),
    ],
)
def test_redact_secrets_more_token_forms(raw: str, expected: str) -> None:
    """auth/id/session tokens, X-Auth-Token, Authorization: Token, JSON "token" and %3D."""
    assert redact_secrets(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        pytest.param("token: SECRET22", "token: [REDACTED]", id="token_colon_space"),
        pytest.param("token:SECRET23", "token:[REDACTED]", id="token_colon"),
        pytest.param("token = SECRET24", "token = [REDACTED]", id="token_spaced_equals"),
        pytest.param("Token: abcdefgh12345", "Token: [REDACTED]", id="token_capitalized"),
        pytest.param('token: "SECRET25"', 'token: "[REDACTED]"', id="token_colon_double_quote"),
        pytest.param("token='SECRET26'", "token='[REDACTED]'", id="token_equals_single_quote"),
    ],
)
def test_redact_secrets_bare_token_colon_and_spaced(raw: str, expected: str) -> None:
    """A bare token key takes ``:`` or ``=``, optional spaces and a quote; all are kept."""
    assert redact_secrets(raw) == expected


def test_redact_secrets_bearer_base64_tail() -> None:
    """A bearer value with ``~``, ``/``, ``+`` and ``=`` padding is redacted with no tail left."""
    assert redact_secrets("Bearer abc.def~ghi/jk+l==") == "Bearer [REDACTED]"


def test_redact_secrets_token_query_stops_at_fragment() -> None:
    """A bare ``token=`` query value stops at a literal ``#``, so the fragment survives."""
    assert redact_secrets("/x?token=SECRET#frag") == "/x?token=[REDACTED]#frag"


def test_redact_secrets_leaves_token_words_alone() -> None:
    """Ordinary words and pagination fields that contain "token" are not redacted."""
    for text in (
        "tokenizer failed on input",
        "next_page_token_count=5",
        "page_token=abc123 is a pagination cursor",
        "next_token=abc123&x=1",
        "csrf_token=abc123#frag",
        "refresh_token_expires_in=3600",
        "the token expired",
        "max_tokens=1024",
        '{"page_token": "x", "next_token": "x", "csrf_token": "x", "max_tokens": 5}',
        "X-Auth-Token-Expires: 2026-10-09T00:00:00Z",
        "session_token_ttl=3600",
        "id_token_hint_count=2",
        "Token x is invalid",
        "Authorization failed: token expired",
        "max_tokens: 5",
        "next_token: abc",
        "page_token: abc",
        "X-Auth-Token-Expires: 5",
        '{"token": null}',
    ):
        assert redact_secrets(text) == text, text


def test_tool_failure_with_password_stays_valid_json() -> None:
    """key=value inside a JSON message is redacted whole on the decoded string, before
    ``json.dumps``, so no part of the password is left and the message still parses."""
    err = tool_failure(
        "batch_failed",
        'login failed password=hun"ter,2}',
        errors=[{"detail": "password=a\\b,c}d", "next": "login failed password=hunter2"}],
    )
    payload = json.loads(str(err))
    rendered = str(err)
    for part in ("hun", "ter", "a\\b", "c}d", "hunter2"):
        assert part not in rendered, part
    assert payload["error"]["message"] == "login failed password=[REDACTED]"
    assert payload["error"]["errors"][0]["next"] == "login failed password=[REDACTED]"


def test_tool_failure_masks_values_under_credential_keys() -> None:
    """A detail under a credential key is masked outright; other keys are walked."""
    err = tool_failure(
        "batch_failed",
        "x",
        password='p,}\\"w',
        access_token="t0k",
        nested={"api_key": "k", "items": ["password=z,}", 3, None]},
    )
    payload = json.loads(str(err))["error"]
    assert payload["password"] == "[REDACTED]"
    assert payload["access_token"] == "[REDACTED]"
    assert payload["nested"] == {"api_key": "[REDACTED]", "items": ["password=[REDACTED]", 3, None]}


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        pytest.param("password=pw,99}x y", "password=[REDACTED]", id="kv_comma_brace"),
        pytest.param("password=a\\b\\c\nnext", "password=[REDACTED]\nnext", id="kv_backslash"),
        pytest.param("{password: FAKE,PW}", "{password: [REDACTED]", id="kv_unquoted_object"),
        pytest.param(
            '{"password": "p,w}\\\\x\\"y"}', '{"password": "[REDACTED]"}', id="json_punct"
        ),
        pytest.param("{'password': 'p,w}\\'x'}", "{'password': '[REDACTED]'}", id="python_repr"),
        pytest.param(
            '{\\"password\\": \\"p,w}\\\\\\\\x\\\\\\"y\\"}',
            '{\\"password\\": \\"[REDACTED]\\"}',
            id="escaped_json_punct",
        ),
        pytest.param("token=SECRET1,x=1", "token=[REDACTED],x=1", id="token_comma"),
        pytest.param("{token: SECRET2}", "{token: [REDACTED]}", id="token_brace"),
    ],
)
def test_redact_secrets_whole_value(raw: str, expected: str) -> None:
    """Passwords with commas, braces, backslashes and quotes are redacted whole."""
    assert redact_secrets(raw) == expected


def test_redact_secrets_json_password_with_punctuation_still_parses() -> None:
    """Redacting raw JSON text keeps it valid and drops every part of the value."""
    secret = 'a,b}c\\d"e'
    raw = json.dumps({"password": secret, "n": 1})
    out = redact_secrets(raw)
    assert json.loads(out) == {"password": "[REDACTED]", "n": 1}
    nested = json.dumps({"m": raw})
    out2 = redact_secrets(nested)
    assert json.loads(json.loads(out2)["m"]) == {"password": "[REDACTED]", "n": 1}
    for part in ("a,b", "c\\d", 'e"'):
        assert part not in out and part not in out2


def test_redact_secrets_pem_block() -> None:
    """A multi-line PEM block is redacted from BEGIN to END, also inside JSON text."""
    pem = "-----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBg\nkqhkiG9w0BAQEF\n-----END PRIVATE KEY-----"
    out = redact_secrets(f"key load failed: {pem} (pkcs8)")
    assert out == "key load failed: [REDACTED] (pkcs8)"
    body = json.dumps({"private_key": pem})
    assert json.loads(redact_secrets(body)) == {"private_key": "[REDACTED]"}
    for part in ("MIIEvQ", "kqhkiG", "BEGIN", "END"):
        assert part not in out


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        pytest.param(
            '{\\"api_key\\": \\"FAKEKEY12345\\"}',
            '{\\"api_key\\": \\"[REDACTED]\\"}',
            id="api_key",
        ),
        pytest.param(
            '{\\"password\\": \\"fakepw99\\"}',
            '{\\"password\\": \\"[REDACTED]\\"}',
            id="password",
        ),
        pytest.param(
            '{\\"client_secret\\": \\"FAKESEC12345\\"}',
            '{\\"client_secret\\": \\"[REDACTED]\\"}',
            id="client_secret",
        ),
        pytest.param(
            '{\\"private_key\\": \\"FAKEPRIVKEY123\\"}',
            '{\\"private_key\\": \\"[REDACTED]\\"}',
            id="private_key",
        ),
    ],
)
def test_redact_secrets_escaped_json_keys(raw: str, expected: str) -> None:
    """Template #68: keys inside an already-serialized JSON string keep their escaped quotes."""
    assert redact_secrets(raw) == expected


def test_tool_failure_redacts_nested_json_password() -> None:
    """A per-item error holding a JSON body is escaped by json.dumps and still redacted."""
    err = tool_failure("batch_failed", "x", errors=[{"detail": '{"password": "fakepw99"}'}])
    assert "fakepw99" not in str(err)
    assert json.loads(str(err))["error"]["errors"][0]["detail"] == '{"password": "[REDACTED]"}'


@pytest.mark.parametrize(
    ("template", "redacted"),
    [
        pytest.param("password={}", "password=[REDACTED]", id="password_equals"),
        pytest.param('{{"password": "{}"}}', '{"password": "[REDACTED]"}', id="password_json"),
        pytest.param("api_key={}", "api_key=[REDACTED]", id="api_key"),
        pytest.param("client_secret: {}", "client_secret: [REDACTED]", id="client_secret"),
        pytest.param(
            "Authorization: Bearer {}", "Authorization: Bearer [REDACTED]", id="authorization"
        ),
    ],
)
def test_redact_secrets_short_values_use_a_fixed_mask(template: str, redacted: str) -> None:
    """Template #67: 1-, 3- and 20-character secrets give the identical masked output."""
    outputs = {redact_secrets(template.format(secret)) for secret in ("x", "abc", "a" * 20)}
    assert outputs == {redacted}


def test_redact_secrets_short_value_near_misses() -> None:
    """Prose and whitespace-separated short words are not treated as keyed secrets."""
    for text in (
        "Bearer of bad news",
        "api key missing",
        "client secret rotated",
        "password is required",
        "invalid password",
        "the api_key field",
        "set a password and retry",
    ):
        assert redact_secrets(text) == text, text


def test_redact_secrets_long_input_is_linear() -> None:
    """Long hostile inputs finish quickly: no catastrophic backtracking in the patterns."""
    import time

    hostile = [
        "password" + ' \\"' * 20000,
        "api_key" + ":=" * 20000 + "!",
        "Authorization: Bearer " + "a" * 50000 + "!",
        "token=" + "a" * 50000 + "}",
    ]
    start = time.perf_counter()
    for text in hostile:
        redact_secrets(text)
    assert time.perf_counter() - start < 2.0


def test_tool_error_redacts_embedded_json_value_by_value() -> None:
    """JSON after a prefix is parsed, redacted per value and re-serialized; it still parses."""
    err = tool_error(
        RuntimeError('HTTP 400: {"message": "login failed password=hunter2", "code": 7}')
    )
    text = str(err)
    assert "hunter2" not in text and "hunt" not in text
    assert text.startswith("HTTP 400: ")
    body = json.loads(text[len("HTTP 400: ") :])
    assert body == {"message": "login failed password=[REDACTED]", "code": 7}


def test_tool_error_masks_credential_keys_in_embedded_json() -> None:
    """A credential key inside parsed JSON is masked outright; text after the JSON is redacted."""
    text = str(tool_error(RuntimeError('[{"api_key": "abc"}] then token=zzz9 end')))
    assert "abc" not in text and "zzz9" not in text
    assert json.loads(text.split(" then ")[0]) == [{"api_key": "[REDACTED]"}]


def test_tool_error_malformed_json_falls_back_to_full_redaction() -> None:
    """Unparseable JSON falls back to the regex: the secret goes whole, shape may break."""
    text = str(tool_error(RuntimeError('HTTP 400: {"message": "password=hunter2", "code": 7')))
    assert "hunter2" not in text and "hunt" not in text
    assert text.startswith("HTTP 400: ")


def test_tool_error_json_after_credential_key_is_masked_whole() -> None:
    """``password={...}`` parses, and the whole value after the key is masked."""
    text = str(tool_error(RuntimeError('password={"a": "s3cretvalue"}')))
    assert "s3cret" not in text


def test_redact_message_empty_and_plain() -> None:
    """Empty text stays empty; text with no JSON goes straight through the regex."""
    from taxcode_mcp.errors import redact_message

    assert redact_message("") == ""
    assert redact_message("a [ b { c") == "a [ b { c"
    assert redact_message('[1] x "5"') == '[1] x "5"'


_NON_STRING_SECRETS = [
    (7777777, "7777777"),
    (["hunter2"], "hunter2"),
    ({"v": "hunter2"}, "hunter2"),
    ([{"deep": 31337.5}], "31337"),
]


@pytest.mark.parametrize(("value", "fragment"), _NON_STRING_SECRETS)
def test_tool_error_masks_non_string_credential_values(value: object, fragment: str) -> None:
    text = str(tool_error(ValueError(json.dumps({"password": value, "ok": 1}))))
    assert fragment not in text
    assert json.loads(text) == {"password": "[REDACTED]", "ok": 1}


@pytest.mark.parametrize(("value", "fragment"), _NON_STRING_SECRETS)
def test_tool_failure_masks_non_string_credential_values(value: object, fragment: str) -> None:
    text = str(tool_failure("auth", "bad login", password=value, api_key=value))
    assert fragment not in text
    error = json.loads(text)["error"]
    assert error["password"] == "[REDACTED]"
    assert error["api_key"] == "[REDACTED]"


def test_none_under_credential_key_is_kept() -> None:
    error = json.loads(str(tool_failure("auth", "missing", password=None)))["error"]
    assert error["password"] is None


# ── #69 item 2: an unquoted or unparseable value after a credential key ───────


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        pytest.param(
            'password={"a": broken pass word}', "password=[REDACTED]", id="broken_json_object"
        ),
        pytest.param(
            'password={"a": broken pass word} tail', "password=[REDACTED] tail", id="balanced_tail"
        ),
        pytest.param("password=pass word123", "password=[REDACTED]", id="unquoted_space"),
        pytest.param(
            "password=pass word123\nnext line", "password=[REDACTED]\nnext line", id="to_eol"
        ),
        pytest.param("password: two words\nok", "password: [REDACTED]\nok", id="colon_form"),
        pytest.param("x password=[1, two three] tail", "x password=[REDACTED] tail", id="list"),
        pytest.param(
            'password={"a": {"b": "}]"}, c d} after',
            "password=[REDACTED] after",
            id="brackets_inside_a_string",
        ),
        pytest.param(
            "password={never closed\nnext", "password=[REDACTED]\nnext", id="unbalanced_to_eol"
        ),
        pytest.param("api_key={x: oops} next", "api_key=[REDACTED] next", id="api_key_object"),
    ],
)
def test_redact_unparseable_value_after_key(raw: str, expected: str) -> None:
    assert redact_secrets(raw) == expected
    assert redact_message(raw) == expected


def test_bracket_value_scan_is_linear_on_long_input() -> None:
    """Hostile bracket input finishes quickly: no catastrophic backtracking."""
    import time

    hostile = [
        "password=" + "{" * 50000,
        "password=" + "[" * 25000 + "]" * 25000,
        "password={" + '"\\' * 30000,
        "password=" + "{a" * 20000 + "\n" + "x" * 50000,
        "password={ " * 20000,
        "password=x " * 20000,
    ]
    start = time.perf_counter()
    for text in hostile:
        assert redact_secrets(text).startswith("password=[REDACTED]")
    assert time.perf_counter() - start < 2.0


def test_template_error_redacts_embedded_json_by_value() -> None:
    """#69 item 3: ``TemplateError`` keeps a JSON body valid, closing ``"}`` included."""
    err = TemplateError('HTTP 401: {"error": "bad", "password": "p w", "hint": "password=x y"}')
    body = json.loads(err.message.removeprefix("HTTP 401: "))
    assert body == {"error": "bad", "password": "[REDACTED]", "hint": "password=[REDACTED]"}
    assert str(err) == err.message


# ── #69 item 4: quoted token values with spaces are redacted to the closing quote ─


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        pytest.param('{"token": "FAKE part2"}', '{"token": "[REDACTED]"}', id="json_token"),
        pytest.param("{'token': 'FAKE part2'}", "{'token': '[REDACTED]'}", id="repr_token"),
        pytest.param('token="FAKE part2" next', 'token="[REDACTED]" next', id="bare_token"),
        pytest.param('token: "a \\" b" n', 'token: "[REDACTED]" n', id="escaped_quote_inside"),
        pytest.param(
            '{"access_token": "FAKE part2", "n": 1}',
            '{"access_token": "[REDACTED]", "n": 1}',
            id="access_token",
        ),
        pytest.param(
            "session_token='FAKE part2'&x=1", "session_token='[REDACTED]'&x=1", id="session"
        ),
        pytest.param(
            '{\\"token\\": \\"FAKE part2\\"}', '{\\"token\\": \\"[REDACTED]\\"}', id="escaped_json"
        ),
        pytest.param(
            '{\\"refresh_token\\": \\"FAKE part2\\", \\"n\\": 1}',
            '{\\"refresh_token\\": \\"[REDACTED]\\", \\"n\\": 1}',
            id="escaped_refresh",
        ),
        pytest.param('"page_token": "keep me"', '"page_token": "keep me"', id="page_token_kept"),
        pytest.param("token=SECRET1 tail", "token=[REDACTED] tail", id="unquoted_unchanged"),
    ],
)
def test_redact_quoted_token_with_spaces(raw: str, expected: str) -> None:
    assert redact_secrets(raw) == expected


def test_quoted_token_json_still_parses() -> None:
    raw = json.dumps({"token": "FAKE part2", "id_token": 'x "y" z', "n": 1})
    assert json.loads(redact_secrets(raw)) == {
        "token": "[REDACTED]",
        "id_token": "[REDACTED]",
        "n": 1,
    }
    nested = json.dumps({"m": raw})
    inner = json.loads(json.loads(redact_secrets(nested))["m"])
    assert inner == {"token": "[REDACTED]", "id_token": "[REDACTED]", "n": 1}


def test_quoted_token_scan_is_linear() -> None:
    import time

    hostile = ['"token": "' + "a \\" * 30000, 'access_token="' + "\\x " * 30000]
    start = time.perf_counter()
    for text in hostile:
        redact_secrets(text)
    assert time.perf_counter() - start < 2.0


@pytest.mark.parametrize(
    "raw",
    ['{"a":' * 2000, "{" * 8000, "[" * 8000, "x { " * 8000],
    ids=["2000-key-colon", "8000-brace", "8000-bracket", "8000-spaced-brace"],
)
def test_redact_message_bracket_flood_is_fast(raw: str) -> None:
    """Unparseable brackets no longer cost quadratic time (each try re-read the prefix)."""
    import time

    began = time.perf_counter()
    out = redact_message(raw)
    assert time.perf_counter() - began < 1.0
    assert out.endswith(MASK)
    assert len(out) < len(raw)


def test_redact_message_past_cap_masks_the_rest() -> None:
    """Past the try cap the message fails closed: everything from there on is MASK."""
    from taxcode_mcp.errors import _MAX_BRACKET_TRIES

    head = '{"password": 1} '
    junk = "{ " * _MAX_BRACKET_TRIES
    tail = 'password={"x": 1} token=abc12345 {"api_key": 7} "api_key": "k9" plain words'
    out = redact_message(head + junk + tail)
    assert out == '{"password": "[REDACTED]"} ' + "{ " * (_MAX_BRACKET_TRIES - 1) + MASK
    for leak in ("abc12345", '"x": 1', "7", "k9", "plain words"):
        assert leak not in out
    # One try fewer stays on the structured path: JSON after it is still parsed.
    below = "{ " * (_MAX_BRACKET_TRIES - 1) + '{"api_key": 7} tail'
    assert redact_message(below) == (
        "{ " * (_MAX_BRACKET_TRIES - 1) + '{"api_key": "[REDACTED]"} tail'
    )


def test_pem_without_end_is_fast_and_masked_to_the_end() -> None:
    """An unterminated BEGIN masks the rest (fail closed) in linear time."""
    import time

    raw = "-----BEGIN A-----\nMIIEkeybody\n" * 4000
    began = time.perf_counter()
    out = redact_secrets(raw)
    assert time.perf_counter() - began < 1.0
    assert out == MASK
    assert redact_secrets("x -----BEGIN RSA PRIVATE KEY-----\nMIIEsecret") == "x " + MASK
    # A complete block followed by an unterminated one: both bodies go.
    two = "-----BEGIN K-----\naaa\n-----END K----- mid -----BEGIN K-----\nbbb"
    assert redact_secrets(two) == MASK + " mid " + MASK


@pytest.mark.parametrize("opener", ["{", "["])
def test_keyed_unbalanced_brackets_are_fast_and_masked(opener: str) -> None:
    """Many unbalanced keyed brackets cost one scan, each still masked to its line end."""
    import time

    line = "password=" + opener + "a" * 100
    raw = (line + "\n") * 5000
    began = time.perf_counter()
    out = redact_secrets(raw)
    assert time.perf_counter() - began < 1.0
    assert out == ("password=" + MASK + "\n") * 5000
    began = time.perf_counter()
    assert redact_message(raw) == out
    assert time.perf_counter() - began < 1.0


def test_keyed_bracket_cache_keeps_balanced_and_nested_values() -> None:
    """A cached scan answers an inner opener exactly as a fresh scan would."""
    raw = 'password={"a": [1, 2], "b": {"c": "}"}} ok\napi_key=[x, [y]] ok\npassword={open\nnext'
    assert redact_secrets(raw) == (
        "password=" + MASK + " ok\napi_key=" + MASK + " ok\npassword=" + MASK + "\nnext"
    )
    # The first scan never closes, but closes the second line's opener: that answer is
    # read back from the cache.
    reused = "password={open\npassword={x} tail"
    assert redact_secrets(reused) == "password=" + MASK + "\npassword=" + MASK + " tail"
    multi = 'x password={\n  "inner": "v"\n} after'
    assert redact_secrets(multi) == "x password=" + MASK + " after"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("-----BEGIN private key-----\nMIIEsecret\n-----END private key----- y", " y"),
        ("-----BEGIN Rsa Private Key-----\nMIIEsecret\n-----END Rsa Private Key----- y", " y"),
        ("-----begin private key-----\nMIIEsecret\n-----end private key----- y", " y"),
        ("-----BEGIN private key-----\nMIIEsecret y", ""),
        ("-----BEGIN Rsa Private Key-----\nMIIEsecret y", ""),
    ],
    ids=["lower", "mixed", "lower-markers", "lower-open", "mixed-open"],
)
def test_pem_labels_any_case_are_masked(raw: str, expected: str) -> None:
    """PEM labels match in any case; an unterminated block masks to the end of the text."""
    assert redact_secrets("x " + raw) == "x " + MASK + expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("{'api_key': ['S3CRET2','b']}", "{'api_key': [REDACTED]}"),
        ("{'api_key': 'S3CRET2'}", "{'api_key': '[REDACTED]'}"),
        ("{'password': 12345, 'user': 'bob'}", "{'password': [REDACTED], 'user': 'bob'}"),
        ("{'password': 1.5e3}", "{'password': [REDACTED]}"),
        ("{'password': True, 'n': None}", "{'password': [REDACTED], 'n': None}"),
        ("{'private_key': False}", "{'private_key': [REDACTED]}"),
        ("{'password': {'a': 'S3CRET2', 'b': [1]}}", "{'password': [REDACTED]}"),
        (
            "{'cfg': {'client_secret': {'k': ['S3CRET2']}}}",
            "{'cfg': {'client_secret': [REDACTED]}}",
        ),
        ('{"api_key": [\'S3CRET2\', "b"]}', '{"api_key": [REDACTED]}'),
        ("{'password': \"S3CRET2\"}", "{'password': \"[REDACTED]\"}"),
        (
            "upstream failed: {'client_secret': ['a]S3CRET2', 'c'], 'user': 'bob'} retrying",
            "upstream failed: {'client_secret': [REDACTED], 'user': 'bob'} retrying",
        ),
        ("KeyError({'token': 'S3CRET2'})", "KeyError({'token': '[REDACTED]'})"),
        ("KeyError({'token': 12345})", "KeyError({'token': [REDACTED]})"),
        ("KeyError({'access_token': ['S3CRET2']})", "KeyError({'access_token': [REDACTED]})"),
        (
            "ValueError(\"bad: {'api_key': ['S3CRET2']}\")",
            "ValueError(\"bad: {'api_key': [REDACTED]}\")",
        ),
        (
            "{'page_token': 5, 'next_token': ['x'], 'max_tokens': 9}",
            "{'page_token': 5, 'next_token': ['x'], 'max_tokens': 9}",
        ),
        ("{'password': None, 'token': null}", "{'password': None, 'token': null}"),
        ("{'password': NoneS3CRET2}", "{'password': [REDACTED]}"),
    ],
    ids=[
        "list",
        "single-quoted-string",
        "number",
        "float",
        "true",
        "false",
        "nested-dict",
        "deeper-key",
        "mixed-quotes-list",
        "double-quoted-value",
        "repr-in-text-bracket-in-string",
        "exception-token",
        "exception-token-number",
        "exception-access-token-list",
        "repr-inside-exception-string",
        "pagination-keys-untouched",
        "none-stays",
        "nonesuch-masked",
    ],
)
def test_redact_message_python_repr(raw: str, expected: str) -> None:
    """A Python ``repr`` (single-quoted keys) is masked like JSON, value form by value form."""
    assert redact_message(raw) == expected
    assert "S3CRET2" not in redact_message(raw)


def test_redact_message_python_repr_unbalanced_fails_closed() -> None:
    """An unbalanced or stray-quoted ``repr`` value is masked to the end of its line."""
    assert (
        redact_message("{'api_key': ['S3CRET2', 'b'\nnext line")
        == "{'api_key': [REDACTED]\nnext line"
    )
    assert redact_message("{'password': ['it's S3CRET2']} tail") == "{'password': [REDACTED]"


@pytest.mark.parametrize(
    "raw",
    [
        "{'api_key': ['" * 8000,
        "{'password': ['S3CRET2', 'b'\n" * 8000,
        "'api_key': " * 8000,
        "{'a': 'x', " * 8000,
        "'" * 8000,
        "password=['\n" * 8000,
    ],
    ids=[
        "keyed-list-flood",
        "keyed-list-lines",
        "keys-no-value",
        "plain-repr-flood",
        "quotes",
        "apostrophe-lines",
    ],
)
def test_python_repr_flood_is_linear(raw: str) -> None:
    """8,000 single-quoted repeats are redacted in under a second."""
    import time

    start = time.perf_counter()
    out = redact_message(raw)
    assert time.perf_counter() - start < 1.0
    assert "S3CRET2" not in out


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("password={'v':'}', 'data':'S3CRET2'} tail", "password=[REDACTED] tail"),
        ("password=['a]', 'S3CRET2'] tail", "password=[REDACTED] tail"),
        ("password={'v':'a\\'}', 'data':'S3CRET2'} tail", "password=[REDACTED] tail"),
        ("password={\"v\":\"'\", 'data':'S3CRET2'} tail", "password=[REDACTED] tail"),
    ],
    ids=[
        "brace-in-single-quotes",
        "bracket-in-single-quotes",
        "escaped-single-quote",
        "apostrophe-in-double-quotes",
    ],
)
def test_bracket_value_skips_single_quoted_closers(raw: str, expected: str) -> None:
    """A ``}`` or ``]`` inside a single-quoted string does not end a credential value."""
    assert redact_message(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (
            "a -----BEGIN PRIVATE KEY-----\nAAA\n-----END CERTIFICATE-----\nS3CRET2\n"
            "-----END PRIVATE KEY----- b",
            "a [REDACTED] b",
        ),
        (
            "a -----BEGIN PRIVATE KEY-----\nAAA\n-----END CERTIFICATE-----\nS3CRET2\n"
            "-----end private key----- b",
            "a [REDACTED] b",
        ),
        ("a -----BEGIN PRIVATE KEY-----\nAAA\n-----END CERTIFICATE-----\nS3CRET2", "a [REDACTED]"),
        (
            "-----BEGIN CERTIFICATE-----\nC\n-----END CERTIFICATE----- ok "
            "-----BEGIN RSA PRIVATE KEY-----\nK\n"
            "-----END RSA PRIVATE KEY----- end",
            "[REDACTED] ok [REDACTED] end",
        ),
    ],
    ids=["mismatched-end-inside", "matching-end-any-case", "mismatched-end-only", "two-blocks"],
)
def test_pem_end_must_match_begin_label(raw: str, expected: str) -> None:
    """A PEM block ends only at an END with its own label, or at the end of the text."""
    assert redact_secrets(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "-----BEGIN A-----" * 4000,
        "-----BEGIN PRIVATE KEY-----" + "-----END CERTIFICATE-----" * 4000,
        "-----BEGIN A-----x-----END B-----" * 4000,
    ],
    ids=["begin-flood", "mismatched-end-flood", "begin-mismatched-pairs"],
)
def test_pem_label_match_is_linear(raw: str) -> None:
    """4,000 BEGINs or mismatched ENDs are redacted in under a second, masked to the end."""
    import time

    start = time.perf_counter()
    out = redact_secrets(raw)
    assert time.perf_counter() - start < 1.0
    assert out == MASK


def test_tuple_under_single_quoted_key_is_masked() -> None:
    """A Python ``repr`` tuple under a credential key is masked to its balanced ``)``."""
    assert redact_secrets("{'api_key': ('a', 'X')} tail") == "{'api_key': " + MASK + "} tail"
    assert redact_secrets('{"password": ("a", "X")}') == '{"password": ' + MASK + "}"
    assert redact_secrets("password=(a, X) ok") == "password=" + MASK + " ok"
    assert redact_secrets("{'api_key': ('a)', 'X')}") == "{'api_key': " + MASK + "}"


def test_nested_tuple_in_list_is_masked() -> None:
    """A tuple nested in a tuple inside a list is masked whole."""
    raw = "[{'api_key': ('a', ('b', 'X'))}, 1]"
    assert redact_secrets(raw) == "[{'api_key': " + MASK + "}, 1]"
    assert redact_message(raw) == "[{'api_key': " + MASK + "}, 1]"


def test_tuple_through_tool_error_is_masked() -> None:
    """``tool_error`` masks a tuple value in the exception message."""
    err = tool_error(ValueError("bad config {'client_secret': ('a', 'X')}"))
    assert str(err) == "bad config {'client_secret': " + MASK + "}"
    assert "X" not in str(err)


def test_unbalanced_paren_masks_to_line_end() -> None:
    """A ``(`` that never balances masks to the end of its line, like ``{`` and ``[``."""
    assert redact_secrets("password=(open\nnext") == "password=" + MASK + "\nnext"
    assert redact_secrets("{'api_key': ('a', 'X'\nok") == "{'api_key': " + MASK + "\nok"


def test_plain_text_parentheses_stay_readable() -> None:
    """Parentheses outside a credential value are left alone."""
    text = "call f(x) (see docs) failed: password is required (retry)"
    assert redact_secrets(text) == text
    assert redact_message(text) == text


@pytest.mark.parametrize("count", [8000, 20000])
def test_keyed_paren_flood_is_fast(count: int) -> None:
    """Thousands of keyed or stray ``(`` stay linear and fail closed."""
    import time

    keyed = ("password=(" + "a" * 10 + "\n") * count
    stray = "api_key=" + "(" * count
    for raw, expected in (
        (keyed, ("password=" + MASK + "\n") * count),
        (stray, "api_key=" + MASK),
    ):
        for redact in (redact_secrets, redact_message):
            began = time.perf_counter()
            assert redact(raw) == expected
            assert time.perf_counter() - began < 1.0


def test_redact_message_masks_tuple_with_nested_list_exactly() -> None:
    """A tuple holding a list under a credential key is one mask, with no stray closer."""
    assert redact_message("{'client_secret': ('X', ['b'])}") == "{'client_secret': " + MASK + "}"


def test_redact_message_masks_tuple_containing_dict() -> None:
    """A tuple holding a dict under a credential key is masked whole."""
    out = redact_message("{'api_key': ({'a': 'X'}, 1)} done")
    assert out == "{'api_key': " + MASK + "} done"


def test_redact_message_masks_nested_tuple_in_tuple() -> None:
    """Nested tuples under a credential key are masked whole."""
    out = redact_message("{'password': (('X',), ('Y',))} tail")
    assert out == "{'password': " + MASK + "} tail"


def test_tool_error_masks_tuple_with_nested_brackets() -> None:
    """tool_error() masks a tuple value with nested brackets."""
    text = str(tool_error(RuntimeError("bad config {'client_secret': ('X', ['b'])}")))
    assert "X" not in text.replace("bad", "")
    assert text.endswith("{'client_secret': " + MASK + "}")


def test_redact_message_keeps_prose_with_many_parens() -> None:
    """Parentheses in prose, without a credential key, stay readable and uncapped."""
    text = "f(x) (see docs) Error (code 5) token (expired) " * 100
    assert redact_message(text) == text


def test_redact_message_paren_flood_stays_fast() -> None:
    """20k ``(`` after a credential key context stays linear."""
    import time

    text = "api_key: " + "(" * 20_000
    began = time.perf_counter()
    redact_message(text)
    assert time.perf_counter() - began < 1.0
    text = "x (" * 20_000
    began = time.perf_counter()
    assert redact_message(text) == text
    assert time.perf_counter() - began < 1.0


_PAIRS = {"(": ")", "[": "]", "{": "}"}
_NEST_CASES = [
    (key, a, b, c)
    for key in ("'password': ", '"password": ', "password=")
    for a in "([{"
    for b in "([{"
    for c in "([{"
]


@pytest.mark.parametrize(("key", "a", "b", "c"), _NEST_CASES)
def test_redact_message_masks_two_level_nested_brackets(key: str, a: str, b: str, c: str) -> None:
    """Every 3x3x3 nesting of ``( [ {`` under a credential key is one whole mask."""
    value = f"{a}'S1', {b}'S2', {c}'S3'{_PAIRS[c]}{_PAIRS[b]}{_PAIRS[a]}"
    out = redact_message(f"cfg {key}{value} tail")
    assert out == f"cfg {key}{MASK} tail"


# ── #80 perf: only look for a credential key before ``(`` after ``:`` or ``=`` ─


def test_prose_parens_skip_the_key_window(monkeypatch: pytest.MonkeyPatch) -> None:
    """A ``(`` without ``:`` or ``=`` before it never searches the 256-character window."""
    from taxcode_mcp import errors

    calls: list[int] = []
    real = errors._keyed_before

    def counting(text: str, floor: int, sep: int) -> bool:
        calls.append(sep)
        return real(text, floor, sep)

    monkeypatch.setattr(errors, "_keyed_before", counting)
    text = "f(x) and g(y) see (docs) " * 50
    assert errors.redact_message(text) == text
    assert calls == []
    errors.redact_message("password= ('a', 'b') and x = (1)")
    assert len(calls) == 2


@pytest.mark.parametrize(
    "gap",
    [" " * 300, "\t" * 300, " \t" * 150, " " * 5000, "\t \t" * 2000],
    ids=["300-spaces", "300-tabs", "300-mixed", "5000-spaces", "6000-mixed"],
)
@pytest.mark.parametrize("key", ["password=", "'api_key':", '"client_secret":'])
def test_long_gap_before_keyed_tuple_is_still_masked(key: str, gap: str) -> None:
    """A gap of any length between the key and the tuple still masks the tuple whole."""
    out = redact_message(f"{key}{gap}('s3cret', ['b']) tail")
    assert "s3cret" not in out and "'b'" not in out
    assert out.endswith(MASK + " tail")


@pytest.mark.parametrize(
    "raw",
    [
        "password=(x\n" * 20000,
        "f(x) and g(y) see (docs) " * 20000,
        "(   " * 100000,
        "( \t " * 100000,
        (" " * 300 + "(") * 1000,
        ("=" + " " * 300 + "(") * 1000,
    ],
    ids=["keyed-flood-20k", "prose-20k", "spaced-100k", "mixed-100k", "long-gap", "eq-long-gap"],
)
def test_redact_message_paren_floods_stay_fast(raw: str) -> None:
    """Parenthesis floods, with or without whitespace gaps, stay linear and quick."""
    import time

    began = time.perf_counter()
    redact_message(raw)
    assert time.perf_counter() - began < 1.0
