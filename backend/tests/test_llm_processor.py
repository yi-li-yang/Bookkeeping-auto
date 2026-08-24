"""
LLM plumbing tests: JSON extraction and backend selection.

No network and no CLI -- the point is the parsing and dispatch, not the model.
"""
import json
import subprocess

import pytest

from backend.ingestion import llm_processor


# -- _extract_json ----------------------------------------------------------

def test_an_array_of_objects_parses():
    """
    The regression that broke categorisation entirely. The old scan preferred
    `{` over `[` whenever a brace appeared anywhere, so `[{"id": 0}]` began
    parsing at the brace and died on the trailing `]` with "Extra data".
    """
    assert llm_processor._extract_json('[{"id": 0, "category": "Rent"}]') == [
        {"id": 0, "category": "Rent"}
    ]


def test_trailing_prose_is_ignored():
    """
    The Claude Code backend is an agent and adds a sentence after the payload.
    Parsing must stop at the end of the first complete value.
    """
    raw = '[{"id": 0}]\n\nLet me know if you want these adjusted.'
    assert llm_processor._extract_json(raw) == [{"id": 0}]


def test_leading_prose_is_ignored():
    assert llm_processor._extract_json('Here you go:\n[{"id": 2}]') == [{"id": 2}]


def test_a_fenced_block_is_preferred():
    raw = '```json\n[{"id": 1}]\n```\nDone!'
    assert llm_processor._extract_json(raw) == [{"id": 1}]


def test_a_broken_fence_falls_through_to_the_body():
    """A malformed fence must not mask valid JSON further down."""
    raw = '```json\nnot actually json\n```\n[{"id": 3}]'
    assert llm_processor._extract_json(raw) == [{"id": 3}]


def test_a_plain_object_parses():
    assert llm_processor._extract_json('{"date_col": "Date"}') == {"date_col": "Date"}


def test_no_json_raises_with_the_response_in_the_message():
    with pytest.raises(ValueError) as excinfo:
        llm_processor._extract_json("I could not do that, sorry.")
    assert "could not do that" in str(excinfo.value)


# -- backend dispatch -------------------------------------------------------

class FakeSettings:
    def __init__(self, **kw):
        self.llm_backend = kw.get("llm_backend", "api")
        self.llm_model = kw.get("llm_model", "claude-sonnet-5")
        self.claude_cli_path = kw.get("claude_cli_path")
        self.claude_cli_timeout = 300


def test_the_api_backend_is_the_default(monkeypatch):
    monkeypatch.setattr(llm_processor, "settings", FakeSettings())
    monkeypatch.setattr(llm_processor, "_call_api", lambda p, m=2048: "from-api")
    monkeypatch.setattr(llm_processor, "_call_claude_code",
                        lambda p: pytest.fail("used the CLI when backend was api"))
    assert llm_processor._call("hi") == "from-api"


def test_the_claude_code_backend_is_selected_by_config(monkeypatch):
    monkeypatch.setattr(llm_processor, "settings",
                        FakeSettings(llm_backend="claude_code"))
    monkeypatch.setattr(llm_processor, "_call_claude_code", lambda p: "from-cli")
    monkeypatch.setattr(llm_processor, "_call_api",
                        lambda p, m=2048: pytest.fail("used the API when backend was cli"))
    assert llm_processor._call("hi") == "from-cli"


def test_the_configured_model_reaches_the_api(monkeypatch):
    seen = {}

    class FakeMessages:
        def create(self, **kw):
            seen.update(kw)
            class R:
                content = [type("T", (), {"text": "ok"})()]
            return R()

    monkeypatch.setattr(llm_processor, "settings",
                        FakeSettings(llm_model="claude-haiku-4-5-20251001"))
    monkeypatch.setattr(llm_processor, "_get_client",
                        lambda: type("C", (), {"messages": FakeMessages()})())

    assert llm_processor._call_api("hi") == "ok"
    assert seen["model"] == "claude-haiku-4-5-20251001"


def test_a_missing_cli_names_the_install_command(monkeypatch):
    monkeypatch.setattr(llm_processor, "settings",
                        FakeSettings(llm_backend="claude_code"))
    monkeypatch.setattr(llm_processor.shutil, "which", lambda name: None)

    with pytest.raises(RuntimeError) as excinfo:
        llm_processor._call_claude_code("hi")
    assert "npm i -g @anthropic-ai/claude-code" in str(excinfo.value)


def test_the_cli_result_field_is_returned(monkeypatch):
    def fake_run(cmd, **kw):
        return subprocess.CompletedProcess(
            cmd, 0,
            stdout='Ignoring 7 permissions entries.\n'
                   + json.dumps({"is_error": False, "result": "PONG"}),
            stderr="",
        )

    monkeypatch.setattr(llm_processor, "settings",
                        FakeSettings(llm_backend="claude_code", claude_cli_path="claude"))
    monkeypatch.setattr(llm_processor.subprocess, "run", fake_run)

    # The CLI prints warnings before the JSON, so it cannot be parsed whole.
    assert llm_processor._call_claude_code("hi") == "PONG"


def test_a_cli_error_is_raised_not_returned_as_text(monkeypatch):
    def fake_run(cmd, **kw):
        return subprocess.CompletedProcess(
            cmd, 0, stdout=json.dumps({"is_error": True, "result": "rate limited"}),
            stderr="",
        )

    monkeypatch.setattr(llm_processor, "settings",
                        FakeSettings(llm_backend="claude_code", claude_cli_path="claude"))
    monkeypatch.setattr(llm_processor.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError) as excinfo:
        llm_processor._call_claude_code("hi")
    assert "rate limited" in str(excinfo.value)


def test_a_nonzero_exit_is_raised(monkeypatch):
    def fake_run(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="not logged in")

    monkeypatch.setattr(llm_processor, "settings",
                        FakeSettings(llm_backend="claude_code", claude_cli_path="claude"))
    monkeypatch.setattr(llm_processor.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError) as excinfo:
        llm_processor._call_claude_code("hi")
    assert "not logged in" in str(excinfo.value)


def test_the_prompt_asks_the_categoriser_to_separate_rent_from_deposits(monkeypatch):
    """
    Guards the two prompt properties that fixed the rent figures: the date must
    be sent (without it a recurring charge cannot be told from a one-off), and
    the housing rules must be present.
    """
    captured = {}

    def fake_call(prompt, max_tokens=2048):
        captured["prompt"] = prompt
        return "[]"

    monkeypatch.setattr(llm_processor, "_call", fake_call)
    llm_processor._categorise_batch(
        [{"id": 0, "date": "2026-03-13", "amount": -1950.0, "description": "LANDLORD"}]
    )

    prompt = captured["prompt"]
    assert "2026-03-13" in prompt, "date was dropped from the payload"
    assert "Rental Deposit" in prompt
    assert "recurring" in prompt.lower()
    assert "joint account" in prompt
