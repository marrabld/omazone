"""The named workflow steps and the page each one shows.

Steps are referred to by name everywhere instead of by tab position. Navigation
is reordered as the workflow consolidates, so anything that persisted or compared
a bare integer index would silently point at the wrong step.
"""

from dataclasses import dataclass

# The agreed learner workflow. Output review and Export stay in it even when the
# processors around them are skipped.
WORKFLOW = ("listen", "repair", "match", "eq", "dynamics", "output", "export")


@dataclass(frozen=True)
class Step:
    """One navigable step.

    ``stage`` is the processing stage it explains, or ``None`` for steps that are
    not processors. ``part_of`` groups the secondary pages that belong to a step,
    such as reference targets inside Match.
    """

    key: str
    title: str
    stage: str | None = None
    part_of: str | None = None
    spectral: bool = False
    skippable: bool = False

    @property
    def workflow_index(self):
        return WORKFLOW.index(self.part_of or self.key)


# Navigation order today. Reordering this tuple is the whole of a navigation
# change; nothing else needs to move.
STEPS = (
    Step("match", "Matching", "match", spectral=True, skippable=True),
    Step("listen", "Listen and Mark"),
    Step("reference", "Reference targets", "match", part_of="match", skippable=True),
    Step("sections", "Mix sections", "match", part_of="match", skippable=True),
    Step("repair", "Repair", "repair", skippable=True),
    Step("eq", "Manual EQ", "eq", spectral=True, skippable=True),
    Step("dynamics", "Dynamics", "dynamics", spectral=True, skippable=True),
    Step("output", "Output", "output", spectral=True, skippable=True),
)

# Tab order as it stood before steps were named. Projects saved at the time
# recorded one of these positions, so they must keep resolving to the same page
# even after the live navigation is reordered.
LEGACY_ORDER = tuple(step.key for step in STEPS)

BY_KEY = {step.key: step for step in STEPS}
KEYS = tuple(step.key for step in STEPS)


def step_for(key):
    """Look up a step by name. An unknown key is a bug, not a silent fallback."""
    return BY_KEY[key]


def is_step_key(value):
    return isinstance(value, str) and value in BY_KEY


def is_legacy_step_key(value):
    """Projects saved before steps were named used a tab position as the key."""
    return isinstance(value, str) and value.isdigit() and int(value) < len(LEGACY_ORDER)


def legacy_step_key(value):
    """Resolve a saved selection to a step name, or None when unusable.

    Positions resolve through the frozen legacy order, never the live one, so a
    saved session cannot drift when navigation is reordered.
    """
    if is_step_key(value):
        return value
    if type(value) is int and 0 <= value < len(LEGACY_ORDER):
        return LEGACY_ORDER[value]
    if is_legacy_step_key(value):
        return LEGACY_ORDER[int(value)]
    return None


def clamp(value):
    """Return a valid saved selection, or the first step when it is unusable."""
    if is_step_key(value):
        return value
    return STEPS[0].key
