"""Named render-worker payload shared by matching and saved-recipe actions."""

from typing import NamedTuple

import numpy as np

from .engine import MatchFilter, Spectrum, analyse, audition_pair
from .pipeline import ChainResult


class RenderPayload(NamedTuple):
    output: np.ndarray
    spectrum: Spectrum
    spec: MatchFilter
    matching_preview: tuple
    chain: ChainResult | None = None
    eq_preview: tuple | None = None
    filters_preview: tuple | None = None
    dynamics_preview: tuple | None = None
    output_preview: tuple | None = None
    limiter_preview: tuple | None = None

    @classmethod
    def from_chain(cls, chain, spec, rate):
        def pair(before, after):
            return audition_pair(before, after, rate, chain.loudness)

        return cls(
            output=chain.output,
            spectrum=analyse(chain.output, rate),
            spec=spec,
            matching_preview=pair(chain.repaired, chain.matched),
            chain=chain,
            eq_preview=pair(chain.matched, chain.equalized),
            # Old projects bypass filters. Do not allocate two full-song preview
            # buffers for this identity pair unless someone auditions it.
            filters_preview=(
                pair(chain.equalized, chain.filtered)
                if chain.filtered is not chain.equalized else None
            ),
            dynamics_preview=pair(chain.filtered, chain.pre_output),
            output_preview=pair(chain.pre_output, chain.pre_limiter),
            limiter_preview=pair(chain.pre_limiter, chain.output),
        )
