"""Fixed-order original -> repair -> learned matching -> manual EQ rendering.

Prefix caches depend on numerical recipes and upstream inputs, not GUI selection.
Matching never relearns during rendering. Reserved stages fail if enabled.
"""

from dataclasses import dataclass
from typing import Protocol

import numpy as np

from .engine import render, validate_audio
from .manual_eq import render_eq, settings_from_parameters
from .project import digest_json, filter_dict, replay_repairs
from .sections import render_sections


class BlockProcessor(Protocol):
    """Common streaming core contract for FIR, bell EQ, and later dynamics.

    Configuration is processor-specific and happens while rendering is stopped.
    Render wrappers own delay compensation and finite/infinite-tail policy.
    """

    latency_samples: int

    def prepare(self, sample_rate: int, channels: int, max_block_size: int) -> None: ...

    def reset(self) -> None: ...

    def process_block(self, block: np.ndarray) -> np.ndarray: ...


@dataclass(frozen=True)
class ChainResult:
    original: np.ndarray
    repaired: np.ndarray
    matched: np.ndarray
    output: np.ndarray
    analysis_stale: bool


class ChainRenderer:
    def __init__(self, audio, rate, repair_preview=None):
        self.original = validate_audio(audio)
        self.rate = rate
        self.repair_preview = repair_preview
        if repair_preview is not None:
            self.repair_preview = validate_audio(repair_preview)
            if self.repair_preview.shape != self.original.shape:
                raise ValueError("Repair preview and original formats differ.")
        self.cache = {}
        self.computations = {key: 0 for key in ("repair", "match", "eq")}

    def prefix(self, stage, key, function):
        entry = self.cache.get(stage)
        if entry is None or entry[0] != key:
            audio = function()
            self.cache[stage] = (key, audio)
            self.computations[stage] += 1
        return self.cache[stage][1]

    def render(self, project, block_size=4096):
        for stage in ("dynamics", "output"):
            if not project.stages[stage].bypassed:
                raise ValueError(
                    f"{stage.capitalize()} is not implemented; its settings are retained."
                )
        if project.source and (
            project.source.sample_rate != self.rate
            or project.source.frames != len(self.original)
            or project.source.channels != self.original.shape[1]
        ):
            raise ValueError("Project and renderer source formats differ.")
        repair_key = project.input_key()

        def repair():
            if project.stages["repair"].bypassed:
                return self.original
            if project.repairs:
                return replay_repairs(self.original, self.rate, project.repairs).audio
            return self.repair_preview if self.repair_preview is not None else self.original

        repaired = self.prefix("repair", repair_key, repair)
        bypass = project.stages["match"].bypassed or project.match_mode == "none"
        calibration = project.calibration
        if not bypass and not project.can_render_saved_match:
            raise ValueError(
                "Matching needs explicit analysis. Saved targets and choices are retained."
            )
        if not bypass and project.match_mode == "whole" and calibration.whole is None:
            raise ValueError("Whole-song calibration has no learned filter.")
        filters = (
            None
            if bypass
            else {
                "whole": filter_dict(calibration.whole) if calibration.whole else None,
                "sections": [filter_dict(item.filter) for item in calibration.sections],
            }
        )
        match_key = digest_json(
            {
                "input": repair_key,
                "bypass": bypass,
                "config": project.config_key(),
                "filters": filters,
            }
        )

        def match():
            if bypass:
                return repaired
            if project.match_mode == "whole":
                return render(repaired, calibration.whole, block_size)
            return render_sections(
                repaired,
                self.rate,
                project.sections,
                project.targets,
                project.transition_ms,
                block_size,
                learned_curves=calibration.sections,
            ).audio

        matched = self.prefix("match", match_key, match)
        eq = project.stages["eq"]
        eq_key = digest_json(
            {
                "input": match_key,
                "bypass": eq.bypassed,
                "parameters": eq.parameters,
                "regions": [
                    {"id": item.id, "bounds": {"start": item.bounds.start, "end": item.bounds.end}}
                    for item in project.regions
                ],
            }
        )
        output = self.prefix(
            "eq",
            eq_key,
            lambda: (
                matched
                if eq.bypassed
                else render_eq(
                    matched,
                    self.rate,
                    settings_from_parameters(eq.parameters),
                    project.regions,
                    block_size,
                )
            ),
        )
        return ChainResult(
            self.original, repaired, matched, output, not bypass and project.needs_reanalysis
        )
