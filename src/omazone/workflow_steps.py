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
    secondary: bool = False
    # The comparison model's key for the same stage. Navigation accepts either,
    # because "output" and "output-gain" naming the same thing is a trap.
    aliases: tuple[str, ...] = ()

    @property
    def workflow_index(self):
        return WORKFLOW.index(self.part_of or self.key)


# The workflow, in the order a learner moves through it. Reference targets and
# Mix sections are secondary pages inside Match, so they carry no bar entry.
STEPS = (
    Step("listen", "Listen and Mark"),
    Step("repair", "Repair", "repair", skippable=True),
    Step("match", "Match", "match", spectral=True, skippable=True, aliases=("mastering",)),
    Step("reference", "Reference targets", "match", part_of="match", secondary=True),
    Step("sections", "Mix sections", "match", part_of="match", secondary=True),
    Step("eq", "Manual EQ", "eq", spectral=True, skippable=True),
    Step("dynamics", "Dynamics", "dynamics", spectral=True, skippable=True),
    Step("output", "Output", "output", spectral=True, skippable=True, aliases=("output-gain",)),
    Step("export", "Export"),
)

# Only these appear in the navigation bar, and only these can be stepped through
# with Continue.
NAVIGATION = tuple(step for step in STEPS if not step.secondary)

# Tab order as it stood before steps were named. Projects saved at the time
# recorded one of these positions, so they must keep resolving to the same page
# even after the live navigation is reordered. This is a literal on purpose: it
# records history, so it must never be derived from the live navigation.
LEGACY_ORDER = (
    "match",
    "listen",
    "reference",
    "sections",
    "repair",
    "eq",
    "dynamics",
    "output",
)

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


def resolve_step_key(key):
    """The step a key names, accepting the comparison model's name for it.

    "output" and "output-gain" are the same place, and callers hold both
    spellings, so resolve them here instead of in each caller.
    """
    if is_step_key(key):
        return key
    for step in STEPS:
        if key in step.aliases:
            return step.key
    return None


def clamp(value):
    """Return a valid saved selection, or the first step when it is unusable."""
    return resolve_step_key(value) or NAVIGATION[0].key


def navigation_position(key):
    """Where a step sits in the bar. Secondary pages answer for their parent."""
    if not is_step_key(key):
        return -1
    step = step_for(key)
    if step.part_of is not None:
        return navigation_position(step.part_of)
    return NAVIGATION.index(step) if step in NAVIGATION else -1


def next_step(key):
    """The step Continue moves to, or None from the final step."""
    index = navigation_position(key)
    if index < 0 or index + 1 >= len(NAVIGATION):
        return None
    return NAVIGATION[index + 1]


def previous_step(key):
    """The step Back moves to, or None from the first step."""
    index = navigation_position(key)
    if index <= 0:
        return None
    return NAVIGATION[index - 1]
