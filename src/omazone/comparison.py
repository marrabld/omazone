"""One model for what the current step contributes to the chain.

The viewer, the transport, and the A/B control all resolve their pair, side,
availability, labels, and arrays from here, so they cannot disagree about which
two signals are being compared or what those signals contain.

Arrays come in two flavours on purpose. ``before`` and ``after`` are the raw
processing levels used by graphs, measurements, and export. ``rendered_playback``
is the level-matched pair the transport reads. Preview matching never reaches the
arrays used for export.
"""

from dataclasses import dataclass
from enum import Enum

from .engine import audition_pair
from .project import STAGES

ORIGINAL = "original"


class Side(str, Enum):
    """Which side of a comparison the user is hearing and looking at."""

    BEFORE = "before"
    AFTER = "after"
    ORIGINAL = "original"

    @property
    def processed(self):
        """Keep ``listen_processed`` semantics: After is the only processed side."""
        return self is Side.AFTER


@dataclass(frozen=True)
class ComparisonSpec:
    """Identity of one comparison, the stage it explains, and its labels."""

    key: str
    stage: str | None
    title: str
    before_label: str
    after_label: str
    before_short: str = ""
    after_short: str = ""

    def short_label(self, side, input_label="original"):
        if self.stage is None:
            return "original"
        if side is Side.AFTER:
            return self.after_short or self.after_label
        return self.before_short or input_label


SPECS = (
    ComparisonSpec(
        "mastering",
        "match",
        "Matching",
        "Step input",
        "Matching output",
        "",
        "processed",
    ),
    ComparisonSpec(
        "repair",
        "repair",
        "Repair",
        "Original recording",
        "Repaired recording",
        "original",
        "repaired",
    ),
    ComparisonSpec(
        ORIGINAL,
        None,
        "Original recording",
        "Original recording",
        "Original recording",
        "original",
        "original",
    ),
    ComparisonSpec(
        "eq",
        "eq",
        "Manual EQ",
        "Chain before EQ",
        "Chain after EQ",
        "before EQ",
        "after EQ",
    ),
    ComparisonSpec(
        "dynamics",
        "dynamics",
        "Dynamics",
        "Chain before compression",
        "Chain after compression",
        "before compression",
        "after compression",
    ),
    ComparisonSpec(
        "output-gain",
        "output",
        "Output gain",
        "Chain before output gain",
        "Final output",
        "before output gain",
        "after output gain",
    ),
)

BY_KEY = {spec.key: spec for spec in SPECS}


def spec_for(key):
    """Look up one comparison. An unknown key is a bug, not a silent fallback."""
    return BY_KEY[key]


def stage_for(key):
    return spec_for(key).stage


@dataclass(frozen=True)
class StagePair:
    """Raw before/after arrays for one stage and its rendered audible pair.

    ``after`` is ``None`` until that stage has been rendered. ``before`` is kept
    deliberately: editing a stage retains its valid input so the user keeps
    context while that stage's output is stale.
    """

    before: object | None = None
    after: object | None = None
    rendered_playback: tuple | None = None


def upstream_prefix(pairs, stage, bypassed=None):
    """The latest available upstream output, which is this stage's input.

    A skipped stage is transparent: it passes its input through unchanged, so the
    prefix walks past it instead of reporting audio that is no longer in the chain.
    """
    for earlier in reversed(STAGES[: STAGES.index(stage)]):
        if bypassed and bypassed.get(earlier):
            continue
        candidate = pairs.get(earlier)
        if candidate is not None and candidate.after is not None:
            return candidate.after
    return None


def stage_pair(pairs, stage, fallback=None, bypassed=None):
    """Resolve one stage's own arrays, filling a missing input from upstream."""
    pair = pairs.get(stage) or StagePair()
    before = pair.before
    if before is None:
        before = upstream_prefix(pairs, stage, bypassed)
    if before is None:
        before = fallback
    return StagePair(before, pair.after, pair.rendered_playback)


@dataclass(frozen=True)
class ComparisonState:
    """Everything the UI needs to show, play, and explain one comparison."""

    key: str
    side: Side
    before: object | None
    after: object | None
    rendered_playback: tuple | None
    rate: int
    bypassed: bool = False
    reason: str = ""
    input_label: str = "original"

    @property
    def spec(self):
        return spec_for(self.key)

    @property
    def stage(self):
        return self.spec.stage

    @property
    def available(self):
        """After exists, so the comparison can actually switch sides."""
        return self.after is not None

    @property
    def aligned(self):
        """Both sides are the same length, so one cursor addresses both."""
        return self.before is None or self.after is None or len(self.before) == len(self.after)

    @property
    def identity_arrays(self):
        """Fall back to the stage input so a stale output still plays sensibly."""
        if self.before is None:
            return None
        return audition_pair(self.before, self.before)

    @property
    def playback_arrays(self):
        """What the transport should read, so it never invents its own choice."""
        return self.rendered_playback or self.identity_arrays

    @property
    def side_label(self):
        return self.spec.short_label(self.side, self.input_label)

    @property
    def labels(self):
        return self.spec.before_label, self.spec.after_label

    def arrays_for(self, side):
        """Raw arrays for one side, so graphs never show level-matched audio."""
        if side is Side.ORIGINAL or self.after is None:
            return self.before
        return self.after if side is Side.AFTER else self.before


def resolve(
    key,
    pairs,
    *,
    side=Side.BEFORE,
    original=None,
    fallback=None,
    rate=1,
    bypassed=None,
    reason="",
    input_label="original",
):
    """Resolve the selected comparison into one explicit state.

    ``pairs`` maps stage name to :class:`StagePair`. Original-only listening is
    separate from every before/after pair, so it resolves to no After at all.
    """
    spec = spec_for(key)
    if spec.stage is None:
        return ComparisonState(
            ORIGINAL, Side.ORIGINAL, original, None, None, rate, False, reason, input_label
        )
    flags = bypassed if isinstance(bypassed, dict) else None
    resolved = stage_pair(pairs, spec.stage, fallback, flags)
    return ComparisonState(
        spec.key,
        side,
        resolved.before,
        resolved.after,
        resolved.rendered_playback,
        rate,
        bool(flags.get(spec.stage)) if flags else bool(bypassed),
        reason,
        input_label,
    )
