"""Reproduce reference-matching EQ with generated audio and no GUI or audio device.

Run from the repository root with ``uv run python examples/reference_matching.py``.
"""

import argparse
from pathlib import Path

import numpy as np
from scipy import signal

from omazone.engine import MatchSettings, analyse, design_match, render

SAMPLE_RATE = 48_000
SEED = 42
SECONDS = 8
MEASUREMENT_FREQUENCIES = np.geomspace(100, 15_000, 512)


def spectral_shape_error_db(spectrum, reference):
    """RMS dB difference on a log grid, after removing overall level offset."""
    measured_db = np.interp(
        MEASUREMENT_FREQUENCIES, spectrum.frequency, spectrum.relative_db
    )
    reference_db = np.interp(
        MEASUREMENT_FREQUENCIES, reference.frequency, reference.relative_db
    )
    difference = measured_db - reference_db
    return float(np.sqrt(np.mean(np.square(difference - np.mean(difference)))))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Optional directory for generated 32-bit float source, reference, and processed WAVs",
    )
    args = parser.parse_args()

    rng = np.random.default_rng(SEED)
    source = rng.normal(0, 0.05, (SAMPLE_RATE * SECONDS, 2))
    reference = signal.lfilter([1], [1, -0.8], source, axis=0)

    source_spectrum = analyse(source, SAMPLE_RATE)
    reference_spectrum = analyse(reference, SAMPLE_RATE)
    settings = MatchSettings(
        amount=1.0, smoothing_octaves=0.33, max_boost_db=18, max_cut_db=18, taps=513
    )
    match_filter = design_match(source_spectrum, reference_spectrum, SAMPLE_RATE, settings)
    processed = render(source, match_filter)
    processed_spectrum = analyse(processed, SAMPLE_RATE)

    before = spectral_shape_error_db(source_spectrum, reference_spectrum)
    after = spectral_shape_error_db(processed_spectrum, reference_spectrum)
    response_frequency, actual_response_db = match_filter.response()
    requested_db = np.interp(
        MEASUREMENT_FREQUENCIES, match_filter.frequency, match_filter.requested_db
    )
    actual_db = np.interp(MEASUREMENT_FREQUENCIES, response_frequency, actual_response_db)
    response_error = float(np.sqrt(np.mean(np.square(requested_db - actual_db))))

    print(f"Seed: {SEED}; {SECONDS} s stereo at {SAMPLE_RATE:,} Hz")
    print(
        "Match: amount {:.0%}, smoothing {:.2f} octaves, boost/cut limits ±{:.0f} dB, {} taps".format(
            settings.amount, settings.smoothing_octaves, settings.max_boost_db, settings.taps
        )
    )
    print(f"Spectral shape error before: {before:.2f} dB RMS")
    print(f"Spectral shape error after:  {after:.2f} dB RMS")
    print(f"Error reduction: {100 * (1 - after / before):.1f}%")
    print(f"Requested vs actual FIR response: {response_error:.2f} dB RMS")

    if args.output_dir is not None:
        import soundfile as sf

        args.output_dir.mkdir(parents=True, exist_ok=True)
        for name, audio in (
            ("source", source),
            ("reference", reference),
            ("processed", processed),
        ):
            sf.write(args.output_dir / f"{name}.wav", audio, SAMPLE_RATE, subtype="FLOAT")
        print(f"Generated WAVs: {args.output_dir}")


if __name__ == "__main__":
    main()
