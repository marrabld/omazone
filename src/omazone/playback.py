"""Sample-aligned audition transport, independent of Qt and audio devices.

Configure the cursor directly only while the audio stream is stopped. Live
seek/pause requests go through AuditionProcessor. The callback owns the cursor
during playback and fills the supplied output buffer in-place.
"""

from dataclasses import dataclass

import numpy as np

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


class AuditionProcessor:
    """Callback-owned transitions around a shared sample cursor.

    A/B uses a 10 ms complementary linear fade, avoiding the gain boost an
    equal-power fade causes on correlated material. Seek and pause duck to zero
    over 5 ms; seeking then fades in over 5 ms at the requested sample. Commands
    are immutable tuples published by the UI. Only the callback mutates ramp
    state or a running cursor. No locks, Qt calls or device operations run here.
    """

    def __init__(self, transition_ms=10):
        if not np.isfinite(transition_ms) or transition_ms < 0:
            raise ValueError("Transition duration must be finite and nonnegative.")
        self.transition_ms = transition_ms
        self.command = None

    @property
    def enabled(self):
        return self.transition_ms > 0

    @property
    def pause_requested(self):
        return self.command is not None and self.command[0] == "pause"

    def prepare(self, rate, channels, max_frames, processed=False):
        if (
            any(type(value) is not int for value in (rate, channels, max_frames))
            or min(rate, channels, max_frames) <= 0
            or channels not in (1, 2)
        ):
            raise ValueError("Invalid audition preparation format.")
        self.max_frames = max_frames
        self.ab_frames = max(2, round(rate * self.transition_ms / 1000)) if self.enabled else 0
        self.gain_frames = max(2, round(rate * self.transition_ms / 2000)) if self.enabled else 0
        self.ab_phase = np.linspace(0, 1, self.ab_frames)
        self.gain_phase = np.linspace(0, 1, self.gain_frames)
        self.scratch = np.empty((max_frames, channels), dtype=np.float32)
        self.weights = np.empty(max_frames)
        self.indices = np.arange(max_frames)
        self.mix = self.mix_start = self.mix_target = float(processed)
        self.mix_index = self.ab_frames
        self.gain = self.gain_start = 0.0 if self.enabled else 1.0
        self.gain_target = 1.0
        self.gain_index = 0
        self.command = self.handled_command = None
        self.pending = None
        self.paused = False

    def request_seek(self, position, region=None, loop=False):
        if position < 0 or (region is not None and not region.start <= position <= region.end):
            raise ValueError("Seek position is outside its bounds.")
        self.command = ("seek", int(position), region, bool(loop))

    def seek_bounds(self, cursor):
        """Use the latest requested bounds even before the callback applies them."""
        if self.command is not None and self.command[0] == "seek":
            return self.command[2], self.command[3]
        return cursor.region, cursor.loop

    def request_pause(self):
        self.command = ("pause",)

    def request_resume(self):
        self.command = ("resume",)

    def ramp_gain(self, target):
        self.gain_start, self.gain_target, self.gain_index = self.gain, target, 0

    def consume_command(self):
        command = self.command
        if command is None or command is self.handled_command:
            return
        self.handled_command = command
        if command[0] == "resume":
            self.pending = None
            self.ramp_gain(1.0)
        else:
            self.pending = command
            # Keep a fade already under way moving towards silence. Frequent
            # seeks update the destination rather than postponing it forever.
            if self.gain_target != 0.0:
                self.ramp_gain(0.0)

    def finish_command(self, cursor):
        command, self.pending = self.pending, None
        if command[0] == "pause":
            self.paused = True
            return
        _, position, region, loop = command
        cursor.position, cursor.region, cursor.loop = position, region, loop
        self.ramp_gain(1.0)

    def mix_block(self, cursor, pair, output):
        frames = len(output)
        if self.mix_index == self.ab_frames:
            return cursor.fill(pair[int(self.mix_target)], output)
        position = cursor.position
        finished = cursor.fill(pair[0], output)
        cursor.position = position
        after = self.scratch[:frames]
        cursor.fill(pair[1], after)
        weights = self.weights[:frames]
        count = min(frames, self.ab_frames - self.mix_index)
        if count:
            np.multiply(
                self.ab_phase[self.mix_index : self.mix_index + count],
                self.mix_target - self.mix_start,
                out=weights[:count],
            )
            weights[:count] += self.mix_start
            self.mix_index += count
            self.mix = float(weights[count - 1])
        blend_count = count
        if self.mix_index == self.ab_frames:
            # Copy the settled side, including the last fade sample, rather
            # than leave cancellation roundoff in otherwise untouched audio.
            blend_count -= 1
            if self.mix_target:
                output[blend_count:] = after[blend_count:]
        # before + weight * (after - before) is the complementary linear
        # law, but keeps identical sides bit-exact instead of rounding two
        # separately scaled copies before summing them.
        before_blend, after_blend = output[:blend_count], after[:blend_count]
        np.subtract(after_blend, before_blend, out=after_blend)
        np.multiply(after_blend, weights[:blend_count, None], out=after_blend)
        np.add(before_blend, after_blend, out=before_blend)
        return finished

    def fill(self, cursor, pair, processed, output):
        """Fill without allocating audio buffers; True means stop after this block."""
        if pair[0].shape != pair[1].shape:
            raise ValueError("Comparison sides must have matching shapes.")
        self.consume_command()
        target = float(processed)
        if target != self.mix_target:
            self.mix_start, self.mix_target, self.mix_index = self.mix, target, 0
        if not self.enabled:
            if self.pending is not None:
                self.finish_command(cursor)
            if self.paused:
                output[:] = 0
                return True
            return cursor.fill(pair[int(processed)], output)
        written = 0
        while written < len(output):
            if self.pending is not None and self.gain_index == self.gain_frames:
                self.finish_command(cursor)
            if self.paused:
                output[written:] = 0
                return True
            count = min(len(output) - written, self.max_frames)
            if self.gain_index < self.gain_frames:
                count = min(count, self.gain_frames - self.gain_index)
            block = output[written : written + count]
            start = cursor.position
            end = cursor.region.end if cursor.region else len(pair[0])
            looping = cursor.loop and cursor.region is not None
            finished = self.mix_block(cursor, pair, block)
            weights = self.weights[:count]
            if self.gain_index < self.gain_frames:
                np.multiply(
                    self.gain_phase[self.gain_index : self.gain_index + count],
                    self.gain_target - self.gain_start,
                    out=weights,
                )
                weights += self.gain_start
                self.gain_index += count
                self.gain = float(weights[-1])
            else:
                weights[:] = self.gain_target
            np.multiply(block, weights[:, None], out=block)
            if not looping:
                # Fade the final samples, including a selection's exclusive
                # endpoint. No extra frames or tail are added to the recording.
                np.subtract(end - start - 1, self.indices[:count], out=weights)
                weights /= self.gain_frames - 1
                np.clip(weights, 0, 1, out=weights)
                np.multiply(block, weights[:, None], out=block)
            written += count
            if finished:
                # A queued seek at EOF must still reach its destination rather
                # than stopping the stream with the request unconsumed.
                if self.pending is not None:
                    self.finish_command(cursor)
                    continue
                output[written:] = 0
                return True
        if self.pending is not None and self.gain_index == self.gain_frames:
            self.finish_command(cursor)
        return self.paused
