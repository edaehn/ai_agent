"""Token accounting and an append-only action log for AI agent runs.

Two pieces that always travel together:

* ``CostTracker`` - adds up token usage and estimated cost across every model
  call in a run, broken down per agent and per model.
* ``CSVLogger``   - writes one CSV row per agent action, so a finished run has a
  readable paper trail that survives a crash.

No dependencies beyond the standard library. Prices are editable data, not code:
pass your own mapping, because every provider changes them.

Version 1.0.0 - see README.md.
"""

from __future__ import annotations

import csv
import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

__all__ = [
    "Usage",
    "ModelPrice",
    "normalise_pricing",
    "estimate_cost",
    "AgentSpend",
    "CostTracker",
    "CSVLogger",
    "HeaderMismatch",
    "__version__",
]

__version__ = "1.0.0"


# --------------------------------------------------------------------------- #
# Token counts
# --------------------------------------------------------------------------- #
# Providers spell the same four numbers differently. We read whichever name is
# present rather than committing to one SDK's schema, so Usage.from_obj() works
# with the Anthropic SDK, OpenAI-shaped responses and most agent frameworks'
# usage objects without a per-library adapter.
_ALIASES: dict[str, tuple[str, ...]] = {
    "input_tokens": ("input_tokens", "prompt_tokens", "request_tokens"),
    "output_tokens": ("output_tokens", "completion_tokens", "response_tokens"),
    "cache_read_tokens": (
        "cache_read_tokens",
        "cache_read_input_tokens",
        "cached_tokens",
    ),
    "cache_write_tokens": (
        "cache_write_tokens",
        "cache_creation_input_tokens",
    ),
}


@dataclass(frozen=True)
class Usage:
    """Token counts for one model call.

    ``cache_read_tokens`` and ``cache_write_tokens`` are counted separately from
    ``input_tokens`` because they are priced differently: a cache read is far
    cheaper than fresh input, and writing the cache costs a little more. An agent
    that re-reads the same files every round spends most of its input on cache
    reads, so folding them into ``input_tokens`` overstates the bill several
    times over.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(
            self.input_tokens + other.input_tokens,
            self.output_tokens + other.output_tokens,
            self.cache_read_tokens + other.cache_read_tokens,
            self.cache_write_tokens + other.cache_write_tokens,
        )

    @property
    def total_tokens(self) -> int:
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_read_tokens
            + self.cache_write_tokens
        )

    @classmethod
    def from_obj(cls, obj: Any) -> "Usage":
        """Build a Usage from any provider's usage object or a plain dict.

        Reads the first attribute (or key) present from each alias group, so it
        copes with ``input_tokens``/``prompt_tokens``/``request_tokens`` and the
        cache-token spellings used by different SDKs. Missing numbers count as 0,
        and a ``None`` is treated as 0 - some providers report ``None`` rather
        than omitting the field.
        """
        if obj is None:
            return cls()

        def read(names: tuple[str, ...]) -> int:
            for name in names:
                value = (
                    obj.get(name)
                    if isinstance(obj, Mapping)
                    else getattr(obj, name, None)
                )
                if value is not None:
                    return int(value)
            return 0

        return cls(**{field_name: read(names) for field_name, names in _ALIASES.items()})


# --------------------------------------------------------------------------- #
# Pricing
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ModelPrice:
    """USD per 1,000,000 tokens for one model.

    Cache rates default to the common industry shape - a read at a tenth of the
    input rate, a write at 1.25x - so a two-number price still prices a cached
    run roughly right. Set them explicitly when your provider differs.
    """

    input_usd: float
    output_usd: float
    cache_read_usd: Optional[float] = None
    cache_write_usd: Optional[float] = None

    @property
    def read_rate(self) -> float:
        return (
            self.cache_read_usd
            if self.cache_read_usd is not None
            else self.input_usd * 0.1
        )

    @property
    def write_rate(self) -> float:
        return (
            self.cache_write_usd
            if self.cache_write_usd is not None
            else self.input_usd * 1.25
        )


def normalise_pricing(pricing: Mapping[str, Any]) -> dict[str, ModelPrice]:
    """Accept prices as a 2- or 4-tuple, a dict or a ModelPrice, and return ModelPrices.

    Lets a caller keep the terse ``{"model": (3.0, 15.0)}`` form from a config
    file while the tracker works with one type internally.
    """
    out: dict[str, ModelPrice] = {}
    for model, price in pricing.items():
        if isinstance(price, ModelPrice):
            out[model] = price
        elif isinstance(price, Mapping):
            out[model] = ModelPrice(**price)
        elif isinstance(price, Sequence) and not isinstance(price, (str, bytes)):
            out[model] = ModelPrice(*price)
        else:
            raise TypeError(
                f"price for {model!r} must be a ModelPrice, mapping or tuple, "
                f"not {type(price).__name__}"
            )
    return out


def estimate_cost(
    model: str, usage: Usage, pricing: Mapping[str, ModelPrice]
) -> tuple[float, float]:
    """Return ``(input_cost, output_cost)`` in USD for one call.

    Cache reads and writes are folded into the input figure, since that is what
    they are. An unpriced model costs 0 - ``CostTracker`` records the name so the
    run summary can say the total is incomplete rather than quietly wrong.
    """
    price = pricing.get(model)
    if price is None:
        return 0.0, 0.0
    input_cost = (
        usage.input_tokens * price.input_usd
        + usage.cache_read_tokens * price.read_rate
        + usage.cache_write_tokens * price.write_rate
    ) / 1_000_000
    output_cost = usage.output_tokens * price.output_usd / 1_000_000
    return input_cost, output_cost


# --------------------------------------------------------------------------- #
# Cost tracking
# --------------------------------------------------------------------------- #
@dataclass
class AgentSpend:
    """Running token and cost totals for one agent or one model."""

    usage: Usage = field(default_factory=Usage)
    input_cost: float = 0.0
    output_cost: float = 0.0
    calls: int = 0

    @property
    def cost(self) -> float:
        return self.input_cost + self.output_cost


class CostTracker:
    """Accumulates token usage and cost across every model call in a run.

    Totals are kept overall, per agent and per model. Per-model matters once a
    run mixes a cheap model for the reading-heavy work with an expensive one for
    the hard part: it is the only way to see whether that split actually paid.
    """

    def __init__(self, pricing: Optional[Mapping[str, Any]] = None) -> None:
        self.pricing: dict[str, ModelPrice] = normalise_pricing(pricing or {})
        self.total = Usage()
        self.input_cost = 0.0
        self.output_cost = 0.0
        self.calls = 0
        self.unknown_models: set[str] = set()
        self.by_agent: dict[str, AgentSpend] = {}
        self.by_model: dict[str, AgentSpend] = {}

    def add(self, model: str, usage: Usage, agent: str = "") -> float:
        """Record one call and return its cost in USD."""
        self.total += usage
        self.calls += 1
        if model not in self.pricing:
            self.unknown_models.add(model)
        in_cost, out_cost = estimate_cost(model, usage, self.pricing)
        self.input_cost += in_cost
        self.output_cost += out_cost

        for bucket, key in ((self.by_agent, agent or "unknown"), (self.by_model, model)):
            spend = bucket.setdefault(key, AgentSpend())
            spend.usage += usage
            spend.input_cost += in_cost
            spend.output_cost += out_cost
            spend.calls += 1

        return in_cost + out_cost

    @property
    def total_cost(self) -> float:
        return self.input_cost + self.output_cost

    @property
    def is_complete(self) -> bool:
        """False when any call ran on a model with no price set."""
        return not self.unknown_models

    def summary(self, run_id: str = "") -> str:
        """A human-readable end-of-run block."""
        title = f"Token usage summary (run {run_id})" if run_id else "Token usage summary"
        lines = [
            "=" * 64,
            title,
            f"  calls         : {self.calls:>12,}",
            f"  input tokens  : {self.total.input_tokens:>12,}  (${self.input_cost:.4f})",
            f"  output tokens : {self.total.output_tokens:>12,}  (${self.output_cost:.4f})",
        ]
        if self.total.cache_read_tokens or self.total.cache_write_tokens:
            lines.append(
                f"  cache r/w     : {self.total.cache_read_tokens:>12,} read / "
                f"{self.total.cache_write_tokens:,} written"
            )
        lines.append(f"  TOTAL cost    : ${self.total_cost:.4f}")

        for label, bucket in (("by agent", self.by_agent), ("by model", self.by_model)):
            if not bucket:
                continue
            lines.append(f"  {label}:")
            for key in sorted(bucket):
                spend = bucket[key]
                lines.append(
                    f"    {key:<24}: {spend.usage.input_tokens:>8,} in / "
                    f"{spend.usage.output_tokens:>8,} out  (${spend.cost:.4f})"
                )

        if self.unknown_models:
            models = ", ".join(sorted(self.unknown_models))
            lines.append(
                f"  note: no price set for {models}; counted as $0. "
                "Add them to the pricing mapping for an accurate figure."
            )
        lines.append("=" * 64)
        return "\n".join(lines)

    def to_dict(self, run_id: str = "") -> dict:
        """A machine-readable summary, for writing next to the CSV log."""

        def bucket_to_dict(bucket: Mapping[str, AgentSpend]) -> dict:
            return {
                key: {
                    "calls": spend.calls,
                    "input_tokens": spend.usage.input_tokens,
                    "output_tokens": spend.usage.output_tokens,
                    "cache_read_tokens": spend.usage.cache_read_tokens,
                    "cache_write_tokens": spend.usage.cache_write_tokens,
                    "cost_usd": round(spend.cost, 6),
                }
                for key, spend in bucket.items()
            }

        return {
            "run_id": run_id,
            "calls": self.calls,
            "input_tokens": self.total.input_tokens,
            "output_tokens": self.total.output_tokens,
            "cache_read_tokens": self.total.cache_read_tokens,
            "cache_write_tokens": self.total.cache_write_tokens,
            "input_cost_usd": round(self.input_cost, 6),
            "output_cost_usd": round(self.output_cost, 6),
            "total_cost_usd": round(self.total_cost, 6),
            "cost_is_complete": self.is_complete,
            "by_agent": bucket_to_dict(self.by_agent),
            "by_model": bucket_to_dict(self.by_model),
            "unknown_models": sorted(self.unknown_models),
        }


# --------------------------------------------------------------------------- #
# CSV action log
# --------------------------------------------------------------------------- #
class HeaderMismatch(RuntimeError):
    """Raised when appending to a CSV whose header is not the expected one."""


class CSVLogger:
    """Append-only log of every agent action, one row per action.

    Works for any artifact an agent produces - code, prose or data - because the
    artifact type and path are columns rather than assumptions. Each row is
    flushed immediately, so an interrupted run still leaves a usable trail.

    Opening a file whose header differs from ``FIELDS`` raises ``HeaderMismatch``
    instead of appending misaligned rows: silently writing 18 values under a
    14-column header produces a file that looks fine and reads wrong.
    """

    FIELDS = [
        "timestamp",
        "run_id",
        "round",
        "agent",
        "action",
        "artifact_type",
        "artifact_path",
        "tool",
        "ok",
        "verdict",
        "score",
        "failures",
        "provider",
        "model",
        "input_tokens",
        "output_tokens",
        "cache_read_tokens",
        "cache_write_tokens",
        "cost_usd",
        "duration_ms",
        "chars",
        "content",
    ]

    def __init__(
        self,
        path: Path | str,
        run_id: str,
        *,
        max_content_chars: int = 2000,
    ) -> None:
        """Open (or create) the log at ``path``.

        ``max_content_chars`` truncates the free-text ``content`` column with a
        marker saying how much was dropped. Agents that log whole files will
        otherwise produce a log far larger than the work it describes; set it to
        0 to keep everything.
        """
        self._path = Path(path)
        self._run_id = run_id
        self._max_content = max_content_chars

        existing_header = self._read_header()
        if existing_header is not None and existing_header != self.FIELDS:
            raise HeaderMismatch(
                f"{self._path} has a different header "
                f"({len(existing_header)} columns, expected {len(self.FIELDS)}). "
                "Write to a new file rather than appending to this one."
            )

        # newline="" is required by the csv module; without it you get a blank
        # row between every entry on Windows and some other platforms.
        self._fh = self._path.open("a", newline="", encoding="utf-8")
        self._writer = csv.DictWriter(self._fh, fieldnames=self.FIELDS)
        if existing_header is None:
            self._writer.writeheader()
            self._fh.flush()

    def _read_header(self) -> Optional[list[str]]:
        """The header of an existing non-empty file, else None."""
        if not self._path.exists() or self._path.stat().st_size == 0:
            return None
        with self._path.open("r", newline="", encoding="utf-8") as fh:
            return next(csv.reader(fh), None)

    def log(
        self,
        *,
        agent: str,
        action: str,
        content: str = "",
        round_no: int = 0,
        artifact_type: str = "",
        artifact_path: str = "",
        tool: str = "",
        ok: Optional[bool] = None,
        verdict: str = "",
        score: Optional[float] = None,
        failures: Sequence[str] | str = (),
        provider: str = "",
        model: str = "",
        usage: Optional[Usage] = None,
        cost_usd: float = 0.0,
        duration_ms: Optional[int] = None,
    ) -> None:
        """Append one row.

        ``ok`` is the deterministic verdict - a test exit code, a schema check, an
        HTTP status - and ``score`` is an advisory model opinion. They are
        separate columns on purpose: a run where ``ok`` is false and ``score`` is
        9 is exactly the disagreement worth being able to find later.
        """
        usage = usage or Usage()
        if isinstance(failures, str):
            failure_text = failures
        else:
            failure_text = "; ".join(str(f) for f in failures)

        self._writer.writerow(
            {
                "timestamp": dt.datetime.now().isoformat(timespec="seconds"),
                "run_id": self._run_id,
                "round": round_no,
                "agent": agent,
                "action": action,
                "artifact_type": artifact_type,
                "artifact_path": artifact_path,
                "tool": tool,
                "ok": "" if ok is None else int(bool(ok)),
                "verdict": verdict,
                "score": "" if score is None else f"{score:g}",
                "failures": failure_text,
                "provider": provider,
                "model": model,
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
                "cache_read_tokens": usage.cache_read_tokens,
                "cache_write_tokens": usage.cache_write_tokens,
                "cost_usd": f"{cost_usd:.6f}",
                "duration_ms": "" if duration_ms is None else int(duration_ms),
                "chars": len(content),
                "content": self._clip(content),
            }
        )
        self._fh.flush()  # flush so the log survives a crash mid-run

    def _clip(self, content: str) -> str:
        if self._max_content and len(content) > self._max_content:
            dropped = len(content) - self._max_content
            return f"{content[: self._max_content]}…[truncated {dropped} chars]"
        return content

    @property
    def path(self) -> Path:
        return self._path

    def close(self) -> None:
        if not self._fh.closed:
            self._fh.close()

    def __enter__(self) -> "CSVLogger":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
