"""Sample-aligned audition transport, independent of Qt and audio devices.

Configure or seek only while the audio stream is stopped. The callback owns
the cursor during playback and fills the supplied output buffer in-place.
"""

from dataclasses import dataclass

from .waveform import SampleRegion


@dataclass
class PlaybackCursor:
    position: int = 0
    region: SampleRegion | None = None
    loop: bool = False

    def fill(self, audio, output):
        """Fill a callback buffer; return True when one-shot playback completes.

        Loops use [start, end), never emit silence at a wrap, and duplicate an
        already-filled cycle for short regions instead of looping per sample.
        """
        start, end = (self.region.start, self.region.end) if self.region else (0, len(audio))
        if not 0 <= start < end <= len(audio):
            raise ValueError("Playback region must be nonempty and within the audio.")
        if not start <= self.position <= end:
            raise ValueError("Playback cursor is outside its bounds.")
        if output.shape[1:] != audio.shape[1:]:
            raise ValueError("Playback channel counts differ.")
        frames = len(output)
        if self.loop and self.region is not None:
            if self.position == end:
                self.position = start
            old_position = self.position
            first = min(frames, end - self.position)
            output[:first] = audio[self.position : self.position + first]
            if first < frames:
                cycle = min(frames - first, end - start)
                output[first : first + cycle] = audio[start : start + cycle]
                written = first + cycle
                while written < frames:
                    count = min(written - first, frames - written)
                    output[written : written + count] = output[first : first + count]
                    written += count
            self.position = start + (old_position - start + frames) % (end - start)
            return False
        available = min(frames, end - self.position)
        output[:available] = audio[self.position : self.position + available]
        output[available:] = 0
        self.position += available
        return self.position >= end
