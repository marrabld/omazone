"""Read-only stage measurements on raw, aligned comparison arrays. No Qt."""

import weakref
from collections import OrderedDict
from dataclasses import dataclass

import numpy as np

from .loudness import MINIMUM_SECONDS, integrated_lufs
from .project import STAGES
from .workflow_status import RenderState


@dataclass(frozen=True)
class Reading:
    peak_db: float
    lufs: float | None
    loudness_note: str = ""


class MeasurementCache:
    """Bounded scalar cache. Weak references prevent id reuse and retained songs.

    Used by one measurement worker at a time. Array contents, like the render
    prefix caches, are immutable after publication to the GUI.
    """

    def __init__(self, limit=64):
        self.entries = OrderedDict()
        self.limit = limit

    def measure(self, audio, rate, start, end, known_loudness=None):
        if rate <= 0 or not 0 <= start < end <= len(audio):
            raise ValueError("Measurement requires a nonempty passage within the recording.")
        key = (id(audio), rate, start, end)
        entry = self.entries.get(key)
        if entry is not None and entry[0]() is audio:
            self.entries.move_to_end(key)
            return entry[1]
        segment = audio[start:end]
        if segment.ndim != 2 or segment.shape[1] not in (1, 2):
            raise ValueError("Measurement requires mono or stereo audio.")
        # Bound the scratch space for the peak/finite scan, not a full-song abs copy.
        peak = 0.0
        for offset in range(0, len(segment), 65536):
            block = segment[offset : offset + 65536]
            if not np.all(np.isfinite(block)):
                raise ValueError("Audio contains non-finite samples.")
            peak = max(peak, float(np.max(np.abs(block))))
        lufs = None
        note = ""
        if peak == 0:
            note = "Silence"
        elif len(segment) < MINIMUM_SECONDS * rate:
            note = "Under 400 ms"
        else:
            if (
                start == 0
                and end == len(audio)
                and known_loudness is not None
                and id(audio) in known_loudness
            ):
                lufs = known_loudness[id(audio)]
            else:
                lufs = integrated_lufs(segment, rate)
            if lufs is None:
                note = "Below gate"
            elif not np.isfinite(lufs):
                raise ValueError("Loudness measurement is non-finite.")
        result = Reading(20 * np.log10(peak) if peak else float("-inf"), lufs, note)
        self.entries[key] = (weakref.ref(audio), result)
        self.entries.move_to_end(key)
        while len(self.entries) > self.limit:
            self.entries.popitem(last=False)
        return result


@dataclass(frozen=True, eq=False)
class MeasurementRequest:
    comparison_key: str
    title: str
    before: object | None
    after: object | None
    rate: int
    start: int
    end: int
    scope: str
    status: str

    @property
    def key(self):
        return (
            self.comparison_key,
            id(self.before),
            id(self.after),
            self.rate,
            self.start,
            self.end,
            self.scope,
            self.status,
        )


def request_for(state, statuses, selection=None):
    """Use the viewer's comparison, never its fallback as an unrendered input.

    The viewer can retain older upstream context while a stage's true input is
    unavailable. Do not mislabel that context as a measured stage boundary.
    """
    before, after = state.before, state.after
    status = "Raw levels; audition matching excluded."
    if state.key == "band-solo":
        before = after = None
        status = "Band solo is audition-only. Exit solo to measure full compression."
    elif state.stage is not None:
        upstream = STAGES[: STAGES.index(state.stage)]
        input_ready = all(
            statuses[key].state in (RenderState.READY, RenderState.SKIPPED) for key in upstream
        )
        if not input_ready:
            before = after = None
            status = "Stage input unavailable. Render or resolve upstream stages first."
        else:
            stage_status = statuses[state.stage]
            if stage_status.state is RenderState.SKIPPED:
                # Repair can keep an explicitly auditionable preview while its
                # chain stage is bypassed. Describe it rather than invent unity.
                status = (
                    "Skipped in chain; readings describe the repair preview."
                    if state.stage == "repair" and after is not None and after is not before
                    else "Skipped stage; input passes unchanged."
                )
            elif stage_status.state is not RenderState.READY:
                after = None
                status = stage_status.reason
    if before is None and state.before is None:
        status = "Recording unavailable."
    length = (
        len(before) if before is not None else len(state.before) if state.before is not None else 0
    )
    start, end = (selection.start, selection.end) if selection is not None else (0, length)
    scope = (
        f"Selected passage {start / state.rate:.3f}-{end / state.rate:.3f} s"
        if selection is not None
        else "Whole recording"
    )
    return MeasurementRequest(
        state.key, state.spec.title, before, after, state.rate, start, end, scope, status
    )
