"""Fixed-order repair -> matching -> EQ -> filters -> dynamics -> output rendering.

Prefix caches depend on numerical recipes and upstream inputs, not GUI selection.
Matching never relearns during rendering. Reserved stages fail if enabled.
"""

from dataclasses import dataclass, field
from typing import Protocol

import numpy as np

from .compressor import CompressionResult
from .dynamics import render_dynamics, settings_from_parameters
from .engine import render, validate_audio
from .filters import filters_from_parameters, render_filters
from .filters import validate_settings as validate_filters
from .limiter import LimitingResult, render_limiter
from .limiter import settings_from_parameters as limiter_settings
from .loudness import integrated_lufs
from .manual_eq import eq_from_parameters, render_eq
from .multiband import MultibandResult
from .output_gain import apply_output_gain
from .output_gain import settings_from_parameters as output_settings
from .project import digest_json, filter_dict, replay_repairs
from .sections import render_sections
from .truepeak import true_peak_db as measured_true_peak


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
    equalized: np.ndarray | None = None
    compression: CompressionResult | MultibandResult | None = None
    pre_output: np.ndarray | None = None
    # Integrated LUFS per prefix, keyed by array identity. Measured once here,
    # in the render worker, so the preview pairs and the Output readouts share
    # one computation instead of each paying for it on the UI thread.
    loudness: dict = field(default_factory=dict)
    # True peak for the source, the pre-output signal and the delivered file.
    # Oversampling every prefix would cost more for no extra information:
    # inter-sample risk is only asked about at the boundaries of the chain.
    true_peak: dict = field(default_factory=dict)
    pre_limiter: np.ndarray | None = None
    limiting: LimitingResult | None = None
    filtered: np.ndarray | None = None


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
        self.computations = {key: 0 for key in ("repair", "match", "eq", "filters", "dynamics", "output")}

    def prefix(self, stage, key, function):
        entry = self.cache.get(stage)
        if entry is None or entry[0] != key:
            audio = function()
            self.cache[stage] = (key, audio)
            self.computations[stage] = self.computations.get(stage, 0) + 1
        return self.cache[stage][1]

    def render(self, project, block_size=4096):
        output_stage = project.stages["output"]
        settings = output_settings(output_stage.parameters) if not output_stage.bypassed else None
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
        equalized = self.prefix(
            "eq",
            eq_key,
            lambda: (
                matched
                if eq.bypassed
                else render_eq(
                    matched,
                    self.rate,
                    eq_from_parameters(eq.parameters),
                    project.regions,
                    block_size,
                )
            ),
        )
        filters = project.stages["filters"]
        filter_settings = None if filters.bypassed else filters_from_parameters(filters.parameters)
        if filter_settings is not None:
            validate_filters(filter_settings, self.rate)
        filters_key = digest_json(
            {"input": eq_key, "bypass": filters.bypassed, "parameters": filters.parameters}
        )
        filtered = self.prefix(
            "filters",
            filters_key,
            lambda: (
                equalized
                if filters.bypassed or not any(item.enabled for item in filter_settings.filters)
                else render_filters(equalized, self.rate, filter_settings, block_size)
            ),
        )
        dynamics = project.stages["dynamics"]
        dynamics_key = digest_json(
            {"input": filters_key, "bypass": dynamics.bypassed, "parameters": dynamics.parameters}
        )
        compressed = self.prefix(
            "dynamics",
            dynamics_key,
            lambda: (
                None
                if dynamics.bypassed
                else render_dynamics(
                    filtered, self.rate, settings_from_parameters(dynamics.parameters, self.rate), block_size
                )
            ),
        )
        pre_output = filtered if compressed is None else compressed.audio
        output_key = digest_json(
            {
                "input": dynamics_key,
                "bypass": output_stage.bypassed,
                "parameters": output_stage.parameters,
            }
        )
        pre_limiter = self.prefix(
            "output",
            output_key,
            lambda: (
                pre_output if output_stage.bypassed else apply_output_gain(pre_output, settings)
            ),
        )
        limiter = project.stages["limiter"]
        limiting = (
            None
            if limiter.bypassed
            else self.prefix(
                "limiter",
                digest_json({"input": output_key, "parameters": limiter.parameters}),
                lambda: render_limiter(
                    pre_limiter, self.rate, limiter_settings(limiter.parameters), block_size
                ),
            )
        )
        output = pre_limiter if limiting is None else limiting.audio
        loudness = _measure_loudness(
            [self.original, repaired, matched, equalized, filtered, pre_output, pre_limiter, output], self.rate
        )
        true_peak = {
            id(audio): measured_true_peak(audio, self.rate)
            for audio in (self.original, pre_output, pre_limiter, output)
        }
        return ChainResult(
            original=self.original,
            repaired=repaired,
            matched=matched,
            output=output,
            analysis_stale=not bypass and project.needs_reanalysis,
            equalized=equalized,
            compression=compressed,
            pre_output=pre_output,
            loudness=loudness,
            true_peak=true_peak,
            pre_limiter=pre_limiter,
            limiting=limiting,
            filtered=filtered,
        )


def _measure_loudness(arrays, rate):
    """Integrated LUFS for each chain prefix, keyed by array identity.

    Integrated loudness is a whole-programme measurement, so it is taken once
    per render. A six-minute stereo song costs well under a second each; doing
    it lazily on the UI thread would stall every redraw that showed a reading.
    Bypassed stages share an array, so keys may repeat.
    """
    measured = {}
    for audio in arrays:
        if audio is not None and id(audio) not in measured:
            measured[id(audio)] = integrated_lufs(audio, rate)
    return measured
