import copy
from dataclasses import asdict, replace

import numpy as np
import pytest
import soundfile as sf
from scipy import signal

from omazone.engine import MatchSettings, analyse, design_match, render
from omazone.manual_eq import (
    MAX_BANDS,
    BellProcessor,
    BellSettings,
    EQSettings,
    bell_sos,
    eq_from_parameters,
    eq_parameters,
    frequency_response,
    render_eq,
    validate_eq,
)
from omazone.pipeline import ChainRenderer
from omazone.project import (
    AudioReference,
    MatchCalibration,
    NamedRegion,
    Project,
    load_project,
    save_project,
)
from omazone.sections import SectionAssignment, capture_target, render_sections
from omazone.waveform import SampleRegion


@pytest.mark.parametrize("rate", [16000, 44100, 48000, 96000])
@pytest.mark.parametrize("gain", [-12, -3, 0, 6])
def test_bell_center_gain_unity_endpoints_and_stable_poles(rate, gain):
    settings = BellSettings(frequency=2000, gain_db=gain, q=1.2)
    sos = bell_sos(settings, rate)
    _, response = signal.sosfreqz(sos, worN=[0, 2000, rate / 2], fs=rate)
    measured = 20 * np.log10(np.maximum(np.abs(response), 1e-12))
    np.testing.assert_allclose(measured, [0, gain, 0], atol=1e-10)
    assert np.all(np.abs(np.roots(sos[0, 3:])) < 1)
    frequency, db = frequency_response(settings, rate)
    assert np.all(np.isfinite(db)) and frequency[-1] < rate / 2


def test_block_history_matches_direct_filter_and_reset_is_reproducible():
    audio = np.random.default_rng(121).normal(0, 0.1, (8000, 2))
    settings = BellSettings(gain_db=-4)
    expected = signal.sosfilt(bell_sos(settings, 48000), audio, axis=0)
    for size in (1, 17, 513, 4096):
        processor = BellProcessor()
        processor.prepare(48000, 2, size)
        processor.set_settings(settings)
        blocks = [
            processor.process_block(audio[start : start + size])
            for start in range(0, len(audio), size)
        ]
        np.testing.assert_allclose(np.concatenate(blocks), expected, atol=1e-12)
        processor.reset()
        np.testing.assert_allclose(
            processor.process_block(audio[:size]), expected[:size], atol=1e-12
        )


def test_region_is_exactly_dry_outside_and_smooth_at_boundaries():
    audio = np.random.default_rng(122).normal(0, 0.1, (48000, 2))
    audio[:, 1] = -0.4 * audio[:, 0]
    regions = [NamedRegion("guitar", "Guitar", SampleRegion(12000, 36000))]
    settings = BellSettings(gain_db=-6, region_id="guitar", transition_ms=50)
    output = render_eq(audio, 48000, settings, regions, block_size=31)
    np.testing.assert_array_equal(output[:12000], audio[:12000])
    np.testing.assert_array_equal(output[36000:], audio[36000:])
    np.testing.assert_array_equal(output[[12000, 35999]], audio[[12000, 35999]])
    full = signal.sosfilt(bell_sos(settings, 48000), audio, axis=0)
    np.testing.assert_allclose(output[14400:33600], full[14400:33600], atol=1e-12)
    np.testing.assert_allclose(output[:, 1], -0.4 * output[:, 0], atol=1e-12)
    np.testing.assert_array_equal(render_eq(audio, 48000, BellSettings()), audio)


def test_short_region_fades_do_not_overlap_and_boundary_impulse_has_full_context():
    audio = np.zeros((1000, 1))
    audio[98] = 0.8
    regions = [NamedRegion("short", "Short", SampleRegion(100, 120))]
    settings = BellSettings(gain_db=-6, region_id="short", transition_ms=100)
    output = render_eq(audio, 48000, settings, regions)
    assert np.all(np.isfinite(output))
    assert np.any(output[101:119] != 0)  # History from before the region is present.
    np.testing.assert_array_equal(output[:100], audio[:100])
    np.testing.assert_array_equal(output[120:], audio[120:])
    assert output[100] == 0 and output[119] == 0


@pytest.mark.parametrize(
    "settings",
    [
        BellSettings(frequency=24000),
        BellSettings(q=0),
        BellSettings(gain_db=30),
        BellSettings(frequency=float("nan")),
        BellSettings(region_id="missing"),
    ],
)
def test_invalid_eq_is_rejected(settings):
    with pytest.raises(ValueError):
        render_eq(np.zeros((1000, 2)), 48000, settings)


def calibrated_project(audio, rate):
    project = Project(match_mode="whole")
    target_audio = signal.sosfilt(signal.butter(2, 3000, fs=rate, output="sos"), audio, axis=0)
    project.reference_target = capture_target(
        target_audio, rate, SampleRegion(0, len(audio)), "Reference", "ref", "generated"
    )
    spec = design_match(
        analyse(audio, rate), project.reference_target.spectrum, rate, MatchSettings(amount=0.4)
    )
    project.matching = MatchSettings(amount=0.4)
    project.calibration = MatchCalibration(project.config_key(), project.input_key(), whole=spec)
    return project


def test_chain_matches_direct_processing_caches_prefixes_and_retains_calibration():
    audio = np.random.default_rng(123).normal(0, 0.1, (16000, 2))
    before = audio.copy()
    project = calibrated_project(audio, 16000)
    renderer = ChainRenderer(audio, 16000)
    first = renderer.render(project)
    np.testing.assert_allclose(first.output, render(audio, project.calibration.whole), atol=1e-12)
    coefficients = project.calibration.whole.coefficients.copy()
    project.stages["eq"].bypassed = False
    project.stages["eq"].parameters = {"kind": "bell-v1", "band": asdict(BellSettings(gain_db=-4))}
    second = renderer.render(project)
    assert second.matched is first.matched
    assert renderer.computations == {"repair": 1, "match": 1, "eq": 2, "dynamics": 2}
    np.testing.assert_allclose(
        second.output, render_eq(first.matched, 16000, BellSettings(gain_db=-4)), atol=1e-12
    )
    assert not project.needs_reanalysis
    np.testing.assert_array_equal(coefficients, project.calibration.whole.coefficients)
    project.stages["eq"].bypassed = True
    np.testing.assert_array_equal(renderer.render(project).output, first.matched)
    project.stages["match"].bypassed = True
    np.testing.assert_array_equal(renderer.render(project).output, audio)
    assert renderer.computations["repair"] == 1 and renderer.computations["match"] == 2
    np.testing.assert_array_equal(audio, before)


def test_repair_prefix_precedes_matching_and_saved_filters_are_not_relearned():
    audio = np.random.default_rng(124).normal(0, 0.1, (16000, 1))
    repaired = audio.copy()
    repaired[2000:2010] *= 0.5
    project = calibrated_project(repaired, 16000)
    renderer = ChainRenderer(audio, 16000, repair_preview=repaired)
    result = renderer.render(project, block_size=71)
    np.testing.assert_allclose(
        result.matched, render(repaired, project.calibration.whole, 71), atol=1e-12
    )
    project.stages["repair"].bypassed = True
    changed = renderer.render(project)
    np.testing.assert_allclose(
        changed.matched, render(audio, project.calibration.whole), atol=1e-12
    )
    assert changed.analysis_stale


def test_sections_then_eq_roundtrip_preserves_recipe_and_output(tmp_path):
    rate = 16000
    audio = np.random.default_rng(125).normal(0, 0.1, (32000, 2))
    path = tmp_path / "source.wav"
    sf.write(path, audio, rate, subtype="DOUBLE")
    project = Project(source=AudioReference.from_path(path), match_mode="sections")
    target = capture_target(audio, rate, SampleRegion(0, 16000), "Reference", "ref", "generated")
    project.targets[target.id] = target
    project.import_sections(
        [
            SectionAssignment("verse", "Verse", SampleRegion(0, 16000), "ref"),
            SectionAssignment("chorus", "Chorus", SampleRegion(16000, 32000), "ref"),
        ]
    )
    matching = render_sections(audio, rate, project.sections, project.targets)
    project.calibration = MatchCalibration(
        project.config_key(), project.input_key(), sections=matching.curves
    )
    settings = BellSettings(gain_db=-3, region_id="verse")
    project.stages["eq"].bypassed = False
    project.stages["eq"].parameters = {"kind": "bell-v1", "band": asdict(settings)}
    result = ChainRenderer(audio, rate).render(project)
    np.testing.assert_allclose(
        result.output, render_eq(matching.audio, rate, settings, project.regions), atol=1e-12
    )
    file = tmp_path / "session.omazone.json"
    save_project(file, project)
    loaded = load_project(file)
    np.testing.assert_array_equal(ChainRenderer(audio, rate).render(loaded).output, result.output)
    assert loaded.sections == project.sections
    assert loaded.stages["eq"].parameters == project.stages["eq"].parameters


def test_missing_calibration_and_reserved_output_do_not_silently_process():
    audio = np.zeros((16000, 1))
    project = Project(match_mode="whole")
    with pytest.raises(ValueError, match="explicit analysis"):
        ChainRenderer(audio, 16000).render(project)
    project.match_mode = "none"
    altered = copy.deepcopy(project)
    altered.stages["output"].bypassed = False
    with pytest.raises(ValueError, match="not implemented"):
        ChainRenderer(audio, 16000).render(altered)
    project.stages["dynamics"].bypassed = False
    assert ChainRenderer(audio, 16000).render(project).compression is not None


@pytest.mark.parametrize("taps", [129, 4097])
def test_full_chain_compensates_fir_delay_before_causal_eq_at_irregular_blocks(taps):
    rate = 16000
    audio = np.random.default_rng(126).normal(0, 0.01, (16000, 2))
    audio[8000] += 0.8
    project = calibrated_project(audio, rate)
    project.matching = MatchSettings(amount=0.4, taps=taps)
    spec = design_match(
        analyse(audio, rate), project.reference_target.spectrum, rate, project.matching
    )
    project.calibration = MatchCalibration(project.config_key(), project.input_key(), whole=spec)
    band = BellSettings(gain_db=-5, frequency=2200)
    project.stages["eq"].bypassed = False
    project.stages["eq"].parameters = {"kind": "bell-v1", "band": asdict(band)}
    expected = signal.sosfilt(bell_sos(band, rate), render(audio, spec), axis=0)
    for block in (17, 333, 4096):
        actual = ChainRenderer(audio, rate).render(project, block_size=block)
        assert actual.output.shape == audio.shape
        np.testing.assert_allclose(actual.output, expected, atol=1e-12)


def test_multi_band_cascade_bypass_and_shared_region_fade():
    rate = 16000
    audio = np.random.default_rng(140).normal(0, 0.04, (16000, 2))
    region = NamedRegion("verse", "Verse", SampleRegion(3000, 12000))
    bands = (
        BellSettings(frequency=180, gain_db=3, q=0.8),
        BellSettings(frequency=2100, gain_db=-5, q=2),
        BellSettings(frequency=5000, gain_db=8, enabled=False),
    )
    settings = EQSettings(bands, "verse", 50)
    wet = signal.sosfilt(np.vstack([bell_sos(band, rate) for band in bands[:2]]), audio, axis=0)
    for size in (1, 137, 4096):
        output = render_eq(audio, rate, settings, [region], block_size=size)
        np.testing.assert_array_equal(output[:3000], audio[:3000])
        np.testing.assert_array_equal(output[12000:], audio[12000:])
        np.testing.assert_allclose(output[3800:11200], wet[3800:11200], atol=1e-12)
    frequency, combined = frequency_response(settings, rate)
    _, first = frequency_response(bands[0], rate)
    _, second = frequency_response(bands[1], rate)
    np.testing.assert_allclose(combined, first + second, atol=1e-10)
    assert frequency[0] == 0
    np.testing.assert_array_equal(
        render_eq(audio, rate, EQSettings((replace(bands[0], enabled=False),))), audio
    )


def test_legacy_recipe_migrates_to_v2_and_roundtrips_without_losing_scope(tmp_path):
    legacy = {"kind": "bell-v1", "band": asdict(BellSettings(gain_db=-3, region_id="verse"))}
    imported = eq_from_parameters(legacy)
    assert imported.region_id == "verse" and imported.bands[0].gain_db == -3
    parameters = eq_parameters(imported)
    assert parameters["kind"] == "multi-bell-v2" and len(parameters["bands"]) == 1
    assert eq_from_parameters(parameters).bands[0].gain_db == -3
    early = {"bands": [{"frequency": 1000, "region_id": "verse", "gain_db": -1}]}
    assert eq_from_parameters(early).region_id == "verse"
    with pytest.raises(ValueError, match="different scopes"):
        eq_from_parameters({"bands": early["bands"] + [{"region_id": "chorus"}]})
    for invalid in (
        {**parameters, "bands": parameters["bands"] * (MAX_BANDS + 1)},
        {**parameters, "bands": [{"q": "nonsense"}]},
        {**parameters, "bands": [{"frequency": 1000, "enabled": 1}]},
    ):
        with pytest.raises((ValueError, TypeError)):
            validate_eq(eq_from_parameters(invalid), 16000)
    audio = np.random.default_rng(141).normal(0, 0.02, (16000, 1))
    path = tmp_path / "song.wav"
    sf.write(path, audio, 16000, subtype="DOUBLE")
    project = Project(source=AudioReference.from_path(path), match_mode="none")
    project.regions.append(NamedRegion("verse", "Verse", SampleRegion(2000, 9000)))
    project.stages["eq"].bypassed = False
    project.stages["eq"].parameters = eq_parameters(
        replace(imported, bands=imported.bands + (BellSettings(frequency=500, gain_db=2),))
    )
    expected = ChainRenderer(audio, 16000).render(project).output
    session = tmp_path / "multiband.omazone.json"
    save_project(session, project)
    loaded = load_project(session)
    assert loaded.stages["eq"].parameters == project.stages["eq"].parameters
    np.testing.assert_array_equal(ChainRenderer(audio, 16000).render(loaded).output, expected)
