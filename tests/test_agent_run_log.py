"""Tests for agent_run_log. Run with: python3 -m pytest agent_run_log/ -q"""

from __future__ import annotations

import csv
from dataclasses import dataclass

import pytest

from ai_agent.agent_run_log import (
    CostTracker,
    CSVLogger,
    HeaderMismatch,
    ModelPrice,
    Usage,
    estimate_cost,
    normalise_pricing,
)

PRICING = {
    "claude-opus-5-5": (4.00, 20.00),
    "claude-haiku-5-5": (0.10, 0.50),
}


# --------------------------------------------------------------------------- #
# Usage
# --------------------------------------------------------------------------- #
def test_usage_adds_all_four_counts():
    a = Usage(10, 20, 30, 40)
    b = Usage(1, 2, 3, 4)
    assert a + b == Usage(11, 22, 33, 44)


def test_usage_total_tokens():
    assert Usage(10, 20, 30, 40).total_tokens == 100


def test_usage_is_immutable():
    with pytest.raises(Exception):
        Usage(1, 2).input_tokens = 5  # type: ignore[misc]


@dataclass
class _AnthropicShaped:
    input_tokens: int = 100
    output_tokens: int = 50
    cache_read_input_tokens: int = 900
    cache_creation_input_tokens: int = 10


@dataclass
class _OpenAIShaped:
    prompt_tokens: int = 100
    completion_tokens: int = 50


def test_from_obj_reads_anthropic_names():
    assert Usage.from_obj(_AnthropicShaped()) == Usage(100, 50, 900, 10)


def test_from_obj_reads_openai_names():
    assert Usage.from_obj(_OpenAIShaped()) == Usage(100, 50, 0, 0)


def test_from_obj_reads_a_plain_dict():
    assert Usage.from_obj({"input_tokens": 7, "output_tokens": 3}) == Usage(7, 3)


def test_from_obj_treats_none_as_zero():
    """Some providers report None rather than omitting the field."""
    assert Usage.from_obj({"input_tokens": 5, "output_tokens": None}) == Usage(5, 0)


def test_from_obj_of_none_is_empty():
    assert Usage.from_obj(None) == Usage()


# --------------------------------------------------------------------------- #
# Pricing
# --------------------------------------------------------------------------- #
def test_normalise_pricing_accepts_tuple_mapping_and_modelprice():
    out = normalise_pricing(
        {
            "a": (1.0, 2.0),
            "b": {"input_usd": 1.0, "output_usd": 2.0},
            "c": ModelPrice(1.0, 2.0),
        }
    )
    assert out["a"] == out["b"] == out["c"] == ModelPrice(1.0, 2.0)


def test_normalise_pricing_rejects_a_bare_number():
    with pytest.raises(TypeError):
        normalise_pricing({"a": 3.0})


def test_cache_rates_default_to_tenth_and_1_25x():
    price = ModelPrice(input_usd=4.00, output_usd=20.00)
    assert price.read_rate == pytest.approx(0.40)
    assert price.write_rate == pytest.approx(5.00)


def test_explicit_cache_rates_win():
    price = ModelPrice(4.00, 20.00, cache_read_usd=0.20, cache_write_usd=4.40)
    assert price.read_rate == pytest.approx(0.20)
    assert price.write_rate == pytest.approx(4.40)


def test_estimate_cost_prices_the_four_counts_separately():
    pricing = normalise_pricing({"m": ModelPrice(4.00, 20.00, 0.40, 5.00)})
    # 1M fresh input, 1M output, 1M cache reads, 1M cache writes.
    usage = Usage(1_000_000, 1_000_000, 1_000_000, 1_000_000)
    input_cost, output_cost = estimate_cost("m", usage, pricing)
    assert input_cost == pytest.approx(4.00 + 0.40 + 5.00)
    assert output_cost == pytest.approx(20.00)


def test_cache_reads_are_much_cheaper_than_fresh_input():
    """The whole reason cache tokens are a separate field."""
    pricing = normalise_pricing(PRICING)
    fresh = estimate_cost("claude-opus-5-5", Usage(input_tokens=1_000_000), pricing)
    cached = estimate_cost(
        "claude-opus-5-5", Usage(cache_read_tokens=1_000_000), pricing
    )
    assert cached[0] == pytest.approx(fresh[0] * 0.1)


def test_unpriced_model_costs_nothing():
    assert estimate_cost("nope", Usage(1_000, 1_000), normalise_pricing(PRICING)) == (
        0.0,
        0.0,
    )


# --------------------------------------------------------------------------- #
# CostTracker
# --------------------------------------------------------------------------- #
def test_tracker_totals_and_per_call_cost():
    t = CostTracker(PRICING)
    cost = t.add("claude-opus-5-5", Usage(1_000_000, 1_000_000), agent="coder")
    assert cost == pytest.approx(24.00)
    assert t.total_cost == pytest.approx(24.00)
    assert t.calls == 1


def test_tracker_splits_by_agent_and_by_model():
    t = CostTracker(PRICING)
    t.add("claude-haiku-5-5", Usage(1_000_000, 0), agent="planner")
    t.add("claude-opus-5-5", Usage(1_000_000, 0), agent="coder")

    assert t.by_agent["planner"].cost == pytest.approx(0.10)
    assert t.by_agent["coder"].cost == pytest.approx(4.00)
    assert t.by_model["claude-haiku-5-5"].calls == 1
    assert t.by_model["claude-opus-5-5"].cost == pytest.approx(4.00)
    assert t.total_cost == pytest.approx(4.10)


def test_tracker_flags_an_unpriced_model_instead_of_lying():
    t = CostTracker(PRICING)
    t.add("some-local-model", Usage(5_000, 5_000), agent="coder")
    assert t.is_complete is False
    assert t.unknown_models == {"some-local-model"}
    assert "no price set" in t.summary("r1")


def test_tracker_with_only_priced_models_is_complete():
    t = CostTracker(PRICING)
    t.add("claude-opus-5-5", Usage(10, 10))
    assert t.is_complete is True


def test_missing_agent_name_is_bucketed_as_unknown():
    t = CostTracker(PRICING)
    t.add("claude-opus-5-5", Usage(10, 10))
    assert "unknown" in t.by_agent


def test_summary_mentions_cache_only_when_there_is_cache_traffic():
    plain = CostTracker(PRICING)
    plain.add("claude-opus-5-5", Usage(10, 10))
    assert "cache r/w" not in plain.summary()

    cached = CostTracker(PRICING)
    cached.add("claude-opus-5-5", Usage(10, 10, cache_read_tokens=99))
    assert "cache r/w" in cached.summary()


def test_to_dict_is_json_serialisable_and_complete():
    import json

    t = CostTracker(PRICING)
    t.add("claude-opus-5-5", Usage(10, 20, 30, 40), agent="coder")
    data = t.to_dict("run-7")

    assert data["run_id"] == "run-7"
    assert data["cache_read_tokens"] == 30
    assert data["cost_is_complete"] is True
    assert data["by_model"]["claude-opus-5-5"]["calls"] == 1
    json.loads(json.dumps(data))  # must round-trip


# --------------------------------------------------------------------------- #
# CSVLogger
# --------------------------------------------------------------------------- #
def _rows(path):
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def test_logger_writes_a_header_once_and_appends(tmp_path):
    log = tmp_path / "run.csv"

    with CSVLogger(log, "r1") as logger:
        logger.log(agent="coder", action="edit", content="x")
    with CSVLogger(log, "r1") as logger:
        logger.log(agent="verifier", action="run_tests", content="y")

    rows = _rows(log)
    assert len(rows) == 2
    assert log.read_text(encoding="utf-8").count("timestamp,run_id") == 1


def test_logger_records_the_deterministic_and_advisory_verdicts_apart(tmp_path):
    log = tmp_path / "run.csv"
    with CSVLogger(log, "r1") as logger:
        logger.log(
            agent="verifier",
            action="gate",
            ok=False,
            score=9.0,
            failures=["pytest: 1 failed", "link 404: /a"],
        )

    row = _rows(log)[0]
    assert row["ok"] == "0"
    assert row["score"] == "9"
    assert row["failures"] == "pytest: 1 failed; link 404: /a"


def test_ok_none_stays_blank_not_false(tmp_path):
    """A row that made no claim must not read as a failure."""
    log = tmp_path / "run.csv"
    with CSVLogger(log, "r1") as logger:
        logger.log(agent="planner", action="plan")
    assert _rows(log)[0]["ok"] == ""


def test_logger_records_artifact_type_and_path(tmp_path):
    log = tmp_path / "run.csv"
    with CSVLogger(log, "r1") as logger:
        logger.log(
            agent="writer", action="draft", artifact_type="text", artifact_path="post.md"
        )
    row = _rows(log)[0]
    assert row["artifact_type"] == "text"
    assert row["artifact_path"] == "post.md"


def test_logger_writes_token_and_cost_columns(tmp_path):
    log = tmp_path / "run.csv"
    with CSVLogger(log, "r1") as logger:
        logger.log(
            agent="coder",
            action="edit",
            usage=Usage(10, 20, 30, 40),
            cost_usd=0.001234,
            model="claude-opus-5-5",
            duration_ms=1500,
        )
    row = _rows(log)[0]
    assert row["input_tokens"] == "10"
    assert row["cache_read_tokens"] == "30"
    assert row["cost_usd"] == "0.001234"
    assert row["duration_ms"] == "1500"


def test_content_is_clipped_but_true_length_is_kept(tmp_path):
    log = tmp_path / "run.csv"
    with CSVLogger(log, "r1", max_content_chars=10) as logger:
        logger.log(agent="coder", action="write", content="a" * 100)

    row = _rows(log)[0]
    assert row["chars"] == "100"  # the real size, not the clipped one
    assert "truncated 90 chars" in row["content"]


def test_zero_max_content_keeps_everything(tmp_path):
    log = tmp_path / "run.csv"
    with CSVLogger(log, "r1", max_content_chars=0) as logger:
        logger.log(agent="coder", action="write", content="a" * 100)
    assert _rows(log)[0]["content"] == "a" * 100


def test_no_blank_rows_between_entries(tmp_path):
    """The newline="" detail: without it every other row is empty."""
    log = tmp_path / "run.csv"
    with CSVLogger(log, "r1") as logger:
        logger.log(agent="a", action="x")
        logger.log(agent="b", action="y")

    lines = log.read_text(encoding="utf-8").splitlines()
    assert "" not in lines


def test_appending_to_a_foreign_header_raises(tmp_path):
    log = tmp_path / "old.csv"
    log.write_text("timestamp,agent,action\n2026-01-01,a,b\n", encoding="utf-8")

    with pytest.raises(HeaderMismatch):
        CSVLogger(log, "r1")


def test_an_empty_file_gets_a_fresh_header(tmp_path):
    log = tmp_path / "empty.csv"
    log.touch()
    with CSVLogger(log, "r1") as logger:
        logger.log(agent="a", action="x")
    assert _rows(log)[0]["agent"] == "a"


def test_multiline_content_survives_the_round_trip(tmp_path):
    """Code and prose both contain newlines, quotes and commas."""
    log = tmp_path / "run.csv"
    nasty = 'def f():\n    return "a, b"\n'
    with CSVLogger(log, "r1") as logger:
        logger.log(agent="coder", action="edit", content=nasty)
    assert _rows(log)[0]["content"] == nasty


def test_close_is_idempotent(tmp_path):
    logger = CSVLogger(tmp_path / "run.csv", "r1")
    logger.close()
    logger.close()
