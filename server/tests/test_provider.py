import json

import httpx
import pytest

from campus.config import Settings
from campus.errors import Problem
from campus.planner import provider


def settings(**changes):
    return Settings(
        _env_file=None,
        llm_provider="deepseek",
        llm_base_url="https://provider.invalid",
        llm_model="test-model",
        llm_api_key=changes.pop("llm_api_key", "test-secret-never-log"),
        **changes,
    )


def reply(content=None, **message):
    return {
        "choices": [
            {
                "finish_reason": "stop",
                "message": {
                    "content": content
                    or json.dumps({"question": {"question_id": "data", "prompt": "Which data?"}}),
                    **message,
                },
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }


def mock_client(monkeypatch, handler):
    client = httpx.Client
    monkeypatch.setattr(
        provider.httpx,
        "Client",
        lambda **kwargs: client(**kwargs, transport=httpx.MockTransport(handler)),
    )
    monkeypatch.setattr(provider.time, "sleep", lambda seconds: None)


def test_retry_limit_and_redacted_provider_audit(monkeypatch, caplog):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(429 if len(requests) < 3 else 200, json=reply())

    mock_client(monkeypatch, handler)
    caplog.set_level("INFO", logger="campus.planner.provider")
    answer = provider.invoke(settings(), {"prompt": "private project content"}, 10)
    assert answer.question.question_id == "data"
    assert len(requests) == 3
    sent = json.loads(requests[-1].content)
    assert sent["response_format"] == {"type": "json_object"}
    assert sent["thinking"] == {"type": "disabled"}
    assert "test-secret-never-log" not in caplog.text
    assert "private project content" not in caplog.text
    assert "input_tokens=10" in caplog.text


@pytest.mark.parametrize(
    "status,code",
    [(429, "PLANNER_RATE_LIMIT"), (503, "PLANNER_UNAVAILABLE"), (401, "PLANNER_PROVIDER_ERROR")],
)
def test_provider_http_errors_are_bounded(monkeypatch, status, code):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status)

    mock_client(monkeypatch, handler)
    with pytest.raises(Problem) as caught:
        provider.invoke(settings(), {}, 10)
    assert caught.value.code == code
    assert len(calls) == (1 if status == 401 else 3)


def test_missing_key_never_calls_network(monkeypatch):
    def unexpected(request):
        pytest.fail("An unconfigured provider must not be called")

    mock_client(monkeypatch, unexpected)
    with pytest.raises(Problem, match="not configured"):
        provider.invoke(settings(llm_api_key=None), {}, 10)


@pytest.mark.parametrize(
    "body,code",
    [
        (reply("not JSON"), "PLANNER_SCHEMA_INVALID"),
        (reply('{"plan": {}}'), "PLANNER_SCHEMA_INVALID"),
        (reply(refusal="declined"), "PLANNER_REFUSED"),
        ({"choices": [{"finish_reason": "length", "message": {"content": "{}"}}]}, "PLANNER_INCOMPLETE"),
    ],
)
def test_provider_rejects_unusable_results(body, code):
    with pytest.raises(Problem) as caught:
        provider.parse_response(settings(), json.dumps(body), {})
    assert caught.value.code == code


def test_transport_timeout_is_bounded(monkeypatch):
    calls = []

    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout("provider unavailable", request=request)

    mock_client(monkeypatch, handler)
    with pytest.raises(Problem) as caught:
        provider.invoke(settings(), {}, 10)
    assert caught.value.code == "PLANNER_TIMEOUT"
    assert len(calls) == 3
