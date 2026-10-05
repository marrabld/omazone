# Reference-matching EQ with generated signals

This example runs the project's analysis, filter design, and offline renderer
without a GUI, audio device, or commercial recording. From the repository root:

```bash
uv sync --python 3.13
uv run python examples/reference_matching.py
```

For optional listening files, add
`--output-dir outputs/reference-match-example`. The three generated 32-bit float
WAVs go under `outputs/`, which is ignored by Git. Nothing is written by the
default command.

The script makes eight seconds of seeded, two-channel white noise. It filters
the same noise with a one-pole low-pass response to create a reference. Using
the same samples isolates the intentional tonal difference from random noise
variation. `analyse()` obtains channel-averaged Welch **power** spectra;
`design_match()` compares their level-independent shapes, smooths the
correction on a logarithmic frequency grid, and designs a linear-phase FIR;
`render()` applies that FIR and compensates its delay.

The difference between two power spectra in decibels is
`10 × log10(reference power / source power)`. An FIR's amplitude response is
`20 × log10(|H|)`; because output power scales with `|H|²`, both expressions
describe the same dB gain. The match amount scales the requested correction:
at 50%, a requested 6 dB boost becomes 3 dB. Smoothing, measured in octaves,
suppresses narrow noise-specific differences before the gain limits and amount
are applied. This demonstration uses 100% amount and generous ±18 dB limits
to make the result clear. It also uses 513 FIR taps so the requested and
actual responses visibly differ; the desktop application uses 2049 taps by
default and more conservative matching settings.

The reported spectral shape error is the RMS difference in dB on 512
log-spaced frequencies from 100 Hz to 15 kHz, after removing the mean
difference. Removing that constant avoids treating a level change as a tonal
improvement. On one run, the **before** and **after** values were about
5.49 dB and 0.06 dB RMS, respectively, or a 98.8% reduction; exact decimals
can vary with numerical library versions.
The script also compares the requested EQ curve with the **actual** response
measured from the FIR coefficients over the same range. These differ because
a finite-length filter cannot realize every requested curve exactly.

This is a controlled spectral-shape experiment. A lower error does not mean
the processed audio sounds better, and the method does not match dynamics,
arrangement, or loudness. The optional WAVs let you audition that distinction
with generated material.
