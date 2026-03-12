from __future__ import annotations

import logging
import sys
import types
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def _load_handlers_module(monkeypatch):
    config = yaml.safe_load((ROOT / "k8s/optional-slack-a2a/slack-bot-configmap.yaml").read_text())
    source = config["data"]["handlers.py"]

    a2a_pkg = types.ModuleType("a2a")
    a2a_client = types.ModuleType("a2a.client")
    a2a_types = types.ModuleType("a2a.types")
    slack_bolt = types.ModuleType("slack_bolt")
    slack_sdk = types.ModuleType("slack_sdk")
    slack_sdk_errors = types.ModuleType("slack_sdk.errors")
    httpx_module = types.ModuleType("httpx")

    class DummyAsyncClient:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class DummyA2AClient:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

        async def send_message(self, request):
            raise AssertionError("send_message should be stubbed in tests that reach A2A")

    class DummyStruct:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs
            for key, value in kwargs.items():
                setattr(self, key, value)

    class DummySlackApiError(Exception):
        pass

    class DummyWebClient:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

    a2a_client.A2AClient = DummyA2AClient
    a2a_types.Message = DummyStruct
    a2a_types.MessageSendParams = DummyStruct
    a2a_types.SendMessageRequest = DummyStruct
    a2a_types.TextPart = DummyStruct
    slack_bolt.Ack = object
    slack_bolt.App = object
    slack_bolt.Say = object
    slack_sdk.WebClient = DummyWebClient
    slack_sdk_errors.SlackApiError = DummySlackApiError
    httpx_module.AsyncClient = DummyAsyncClient

    monkeypatch.setitem(sys.modules, "a2a", a2a_pkg)
    monkeypatch.setitem(sys.modules, "a2a.client", a2a_client)
    monkeypatch.setitem(sys.modules, "a2a.types", a2a_types)
    monkeypatch.setitem(sys.modules, "slack_bolt", slack_bolt)
    monkeypatch.setitem(sys.modules, "slack_sdk", slack_sdk)
    monkeypatch.setitem(sys.modules, "slack_sdk.errors", slack_sdk_errors)
    monkeypatch.setitem(sys.modules, "httpx", httpx_module)

    module = types.ModuleType("slack_handlers")
    exec(source, module.__dict__)
    return module


def _body(bot_user_id: str = "UBOT", bot_app_id: str = "AAGENT") -> dict:
    return {
        "api_app_id": bot_app_id,
        "authorizations": [{"user_id": bot_user_id}],
        "event": {
            "channel": "C123",
            "ts": "171.0001",
            "thread_ts": "171.0001",
        },
    }


def _build_fake_history_client(message_factory):
    class FakeWebClient:
        def __init__(self, token):
            self.token = token

        def conversations_replies(self, channel, ts, limit, **kwargs):
            return message_factory(channel=channel, ts=ts, limit=limit, **kwargs)

    return FakeWebClient


def test_alert_thread_root_routes_vague_reply_to_alert(monkeypatch) -> None:
    module = _load_handlers_module(monkeypatch)
    monkeypatch.setenv("SLACK_USER_TOKEN", "xoxp-test")

    def message_factory(**kwargs):
        return {
                "messages": [
                    {
                        "ts": "171.0001",
                        "subtype": "bot_message",
                        "app_id": "AALERTBOT",
                        "text": "PodCrashLooping firing for pod/crashy in namespace kagent-smoke",
                    },
                    {
                        "ts": "171.0002",
                        "user": "U123",
                        "text": "@kagent-kind-demo investigate this",
                    },
                ]
            }

    monkeypatch.setattr(module, "WebClient", _build_fake_history_client(message_factory))
    prompt = module.build_thread_aware_prompt("investigate this", _body(), logging.getLogger("test"))

    assert prompt.startswith("[INVESTIGATION_ENTRYPOINT]=alert\n")
    assert "PodCrashLooping firing for pod/crashy in namespace kagent-smoke" in prompt
    assert "Latest user request:\ninvestigate this" in prompt


def test_non_alert_thread_stays_generic(monkeypatch) -> None:
    module = _load_handlers_module(monkeypatch)
    monkeypatch.setenv("SLACK_USER_TOKEN", "xoxp-test")

    def message_factory(**kwargs):
        return {
                "messages": [
                    {"ts": "171.0001", "user": "U111", "text": "prod is broken"},
                    {"ts": "171.0002", "user": "U222", "text": "seeing errors"},
                ]
            }

    monkeypatch.setattr(module, "WebClient", _build_fake_history_client(message_factory))
    prompt = module.build_thread_aware_prompt("investigate this", _body(), logging.getLogger("test"))

    assert prompt.startswith("[INVESTIGATION_ENTRYPOINT]=generic\n")
    assert "prod is broken" in prompt


def test_explicit_alert_payload_in_thread_routes_to_alert(monkeypatch) -> None:
    module = _load_handlers_module(monkeypatch)
    monkeypatch.setenv("SLACK_USER_TOKEN", "xoxp-test")

    def message_factory(**kwargs):
        return {
                "messages": [
                    {"ts": "171.0001", "user": "U111", "text": "please take a look"},
                    {
                        "ts": "171.0002",
                        "subtype": "bot_message",
                        "app_id": "AALERTBOT",
                        "text": "status: firing\nstartsAt: 2026-03-11T03:00:00Z\ngeneratorURL: http://alertmanager.example.local",
                    },
                    {"ts": "171.0003", "user": "U222", "text": "what is going on here?"},
                ]
            }

    monkeypatch.setattr(module, "WebClient", _build_fake_history_client(message_factory))
    prompt = module.build_thread_aware_prompt("investigate this", _body(), logging.getLogger("test"))

    assert prompt.startswith("[INVESTIGATION_ENTRYPOINT]=alert\n")
    assert "status: firing" in prompt
    assert "generatorURL" in prompt


def test_long_thread_keeps_root_alert_summary(monkeypatch) -> None:
    module = _load_handlers_module(monkeypatch)
    monkeypatch.setenv("SLACK_USER_TOKEN", "xoxp-test")

    messages = [
        {
            "ts": "171.0001",
            "subtype": "bot_message",
            "app_id": "AALERTBOT",
            "text": "PodCrashLooping firing for pod/crashy in namespace kagent-smoke",
        }
    ]
    for index in range(2, 16):
        messages.append(
            {
                "ts": f"171.00{index}",
                "user": f"U{index}",
                "text": f"follow-up message {index}",
            }
        )

    def message_factory(**kwargs):
        return {"messages": messages}

    monkeypatch.setattr(module, "WebClient", _build_fake_history_client(message_factory))
    prompt = module.build_thread_aware_prompt("investigate this", _body(), logging.getLogger("test"))

    assert prompt.startswith("[INVESTIGATION_ENTRYPOINT]=alert\n")
    assert "PodCrashLooping firing for pod/crashy in namespace kagent-smoke" in prompt
    assert "follow-up message 15" in prompt


def test_labels_and_annotations_without_alert_fields_stays_generic(monkeypatch) -> None:
    module = _load_handlers_module(monkeypatch)
    monkeypatch.setenv("SLACK_USER_TOKEN", "xoxp-test")

    def message_factory(**kwargs):
        return {
            "messages": [
                {
                    "ts": "171.0001",
                    "user": "U111",
                    "text": "Labels:\n- app=demo\nAnnotations:\n- note=container restarted after deploy",
                },
                {"ts": "171.0002", "user": "U222", "text": "please investigate this"},
            ]
        }

    monkeypatch.setattr(module, "WebClient", _build_fake_history_client(message_factory))
    prompt = module.build_thread_aware_prompt("investigate this", _body(), logging.getLogger("test"))

    assert prompt.startswith("[INVESTIGATION_ENTRYPOINT]=generic\n")
    assert "Labels:" in prompt


def test_current_bot_authored_alert_like_root_is_ignored(monkeypatch) -> None:
    module = _load_handlers_module(monkeypatch)
    monkeypatch.setenv("SLACK_USER_TOKEN", "xoxp-test")

    def message_factory(**kwargs):
        return {
            "messages": [
                {
                    "ts": "171.0001",
                    "subtype": "bot_message",
                    "app_id": "AAGENT",
                    "text": "PodCrashLooping firing for pod/crashy in namespace kagent-smoke",
                },
                {"ts": "171.0002", "user": "U222", "text": "please investigate this"},
            ]
        }

    monkeypatch.setattr(module, "WebClient", _build_fake_history_client(message_factory))
    prompt = module.build_thread_aware_prompt("investigate this", _body(bot_app_id="AAGENT"), logging.getLogger("test"))

    assert prompt.startswith("[INVESTIGATION_ENTRYPOINT]=generic\n")
    assert "PodCrashLooping firing for pod/crashy in namespace kagent-smoke" not in prompt


def test_long_thread_fetches_root_and_recent_window(monkeypatch) -> None:
    module = _load_handlers_module(monkeypatch)
    monkeypatch.setenv("SLACK_USER_TOKEN", "xoxp-test")

    messages = [
        {
            "ts": "171.0001",
            "subtype": "bot_message",
            "app_id": "AALERTBOT",
            "text": "PodCrashLooping firing for pod/crashy in namespace kagent-smoke",
        }
    ]
    for index in range(2, 75):
        messages.append(
            {
                "ts": f"171.{index:04d}",
                "user": f"U{index}",
                "text": f"follow-up message {index}",
            }
        )

    def message_factory(**kwargs):
        oldest = float(kwargs.get("oldest", "0"))
        latest = float(kwargs.get("latest", "999999"))
        if oldest == float("171.0001") and latest == float("171.0001"):
            return {"messages": [messages[0]]}

        batch = [
            message
            for message in messages
            if oldest <= float(message["ts"]) <= latest
        ]
        batch = batch[-kwargs["limit"] :]
        return {"messages": batch}

    monkeypatch.setattr(module, "WebClient", _build_fake_history_client(message_factory))
    prompt = module.build_thread_aware_prompt(
        "investigate this",
        {
            **_body(),
            "event": {
                "channel": "C123",
                "ts": "171.0074",
                "thread_ts": "171.0001",
            },
        },
        logging.getLogger("test"),
    )

    assert prompt.startswith("[INVESTIGATION_ENTRYPOINT]=alert\n")
    assert "PodCrashLooping firing for pod/crashy in namespace kagent-smoke" in prompt
    assert "follow-up message 74" in prompt
    assert "follow-up message 2" not in prompt


def test_missing_user_token_falls_back_to_prefixed_generic(monkeypatch) -> None:
    module = _load_handlers_module(monkeypatch)
    monkeypatch.delenv("SLACK_USER_TOKEN", raising=False)

    prompt = module.build_thread_aware_prompt("investigate this", _body(), logging.getLogger("test"))

    assert prompt == "[INVESTIGATION_ENTRYPOINT]=generic\n\nLatest user request:\ninvestigate this"


def test_generic_history_read_failure_falls_back_to_prefixed_mode(monkeypatch) -> None:
    module = _load_handlers_module(monkeypatch)
    monkeypatch.setenv("SLACK_USER_TOKEN", "xoxp-test")

    class FakeWebClient:
        def __init__(self, token):
            self.token = token

        def conversations_replies(self, channel, ts, limit, **kwargs):
            raise RuntimeError("transport timeout")

    monkeypatch.setattr(module, "WebClient", FakeWebClient)
    prompt = module.build_thread_aware_prompt("investigate this", _body(), logging.getLogger("test"))

    assert prompt == "[INVESTIGATION_ENTRYPOINT]=generic\n\nLatest user request:\ninvestigate this"


def test_explicit_latest_request_routes_to_alert_without_thread_history(monkeypatch) -> None:
    module = _load_handlers_module(monkeypatch)
    monkeypatch.delenv("SLACK_USER_TOKEN", raising=False)

    prompt = module.build_thread_aware_prompt(
        "Investigate alert PodCrashLooping for pod/crashy in namespace kagent-smoke",
        _body(),
        logging.getLogger("test"),
    )

    assert prompt.startswith("[INVESTIGATION_ENTRYPOINT]=alert\n")
    assert "Latest user request:\nInvestigate alert PodCrashLooping" in prompt


def test_slash_command_uses_prefixed_alert_prompt(monkeypatch) -> None:
    module = _load_handlers_module(monkeypatch)
    captured = {}

    def fake_run_a2a(agent_url, user_input, logger, context_id=None):
        captured["agent_url"] = agent_url
        captured["user_input"] = user_input
        return "ok", "", None

    monkeypatch.setattr(module, "run_a2a", fake_run_a2a)

    class FakeClient:
        def __init__(self):
            self.ephemeral = []
            self.messages = []

        def chat_postEphemeral(self, **kwargs):
            self.ephemeral.append(kwargs)

        def chat_postMessage(self, **kwargs):
            self.messages.append(kwargs)

    acked = {"value": False}

    def ack():
        acked["value"] = True

    client = FakeClient()
    module._run_slack_command(
        client,
        ack,
        {"text": "alertname=PodCrashLooping for crashy"},
        logging.getLogger("test"),
        {"user_id": "U1", "channel_id": "C1"},
        "http://example.test/agent",
        "Usage",
    )

    assert acked["value"] is True
    assert captured["agent_url"] == "http://example.test/agent"
    assert captured["user_input"].startswith("[INVESTIGATION_ENTRYPOINT]=alert\n")
