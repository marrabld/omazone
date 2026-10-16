"""Export-only rational polyphase conversion with bounded context blocks.

The caller keeps processing/audition at the original rate. Integer output length
is ceil(input_frames * output_rate / input_rate), with zero-padded file edges and
the symmetric FIR delay compensated by resample_poly. Chunk origins are aligned
to the reduced denominator so each call has the same global polyphase phase.
"""

from functools import lru_cache
from math import gcd

from scipy import signal

INPUT_RATES = (
    8000,
    11025,
    12000,
    16000,
    22050,
    24000,
    32000,
    44100,
    48000,
    64000,
    88200,
    96000,
    176400,
    192000,
)
OUTPUT_RATES = (44100, 48000, 88200, 96000)
BLOCK_FRAMES = 65536


def validate_rates(source, target):
    if any(type(rate) is not int or rate <= 0 for rate in (source, target)):
        raise ValueError("Delivery sample rates must be positive integers.")
    if source != target and (source not in INPUT_RATES or target not in OUTPUT_RATES):
        raise ValueError(
            "Unsupported delivery conversion. Keep the source rate or use a supported input and 44.1, 48, 88.2 or 96 kHz output."
        )


def output_frames(frames, source, target):
    validate_rates(source, target)
    return (frames * target + source - 1) // source


@lru_cache(maxsize=4)
def conversion_filter(up, down):
    scale = max(up, down)
    coefficients = signal.firwin(256 * scale + 1, 0.95 / scale, window=("kaiser", 14.0))
    coefficients.setflags(write=False)
    return coefficients


def converted_blocks(audio, source, target, block_frames=BLOCK_FRAMES):
    """Yield output blocks, never a full-track converted copy.

    Audio is already validated by the delivery caller. Same-rate blocks are
    views with no filtering, float conversion or sample modifications.
    """
    validate_rates(source, target)
    if type(block_frames) is not int or block_frames <= 0:
        raise ValueError("Conversion block size must be a positive integer.")
    if source == target:
        for start in range(0, len(audio), block_frames):
            yield audio[start : start + block_frames]
        return
    if audio.ndim != 2 or audio.shape[1] not in (1, 2):
        raise ValueError("Sample-rate conversion supports mono and stereo recordings.")
    divisor = gcd(source, target)
    up, down = target // divisor, source // divisor
    coefficients = conversion_filter(up, down)
    budget = min(block_frames, block_frames * down // up)
    step = max(down, budget // down * down)
    context = (len(coefficients) - 1 + 2 * up - 1) // (2 * up) + down
    for start in range(0, len(audio), step):
        stop = min(len(audio), start + step)
        low = max(0, start - context) // down * down
        high = min(len(audio), stop + context)
        converted = signal.resample_poly(
            audio[low:high], up, down, axis=0, window=coefficients, padtype="constant"
        )
        first = (start - low) * up // down
        count = output_frames(stop, source, target) - start * up // down
        yield converted[first : first + count]
