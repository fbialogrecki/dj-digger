"""Previewing tracks: the frame ticker, prefetching the next one, and the transport keys.

Mixed into ``DiggerApp``; the attributes these reach for are set up in its
``__init__``.
"""

import logging

from textual import work
from textual.widgets import DataTable

from ..models import Track
from ..player import (
    SEEK_STEP,
    VOLUME_STEP,
    PlaybackUnavailable,
    PlayerBar,
    Stream,
    fetch_waveform,
    open_source,
    resolve_stream,
)
from ..soundcloud import SoundCloudError
from .keymap import (
    CALM_TICK,
    PREFETCH_LEAD,
    TICK,
)
from .rows import Prepared

LOGGER = logging.getLogger(__name__)


class PlaybackMixin:
    """Previewing tracks: the frame ticker, prefetching the next one, and the transport keys."""

    def _player_bar(self) -> PlayerBar:
        return self.query_one("#player", PlayerBar)

    def _tick(self) -> None:
        # The timer belongs to the app and the bar to the screen, so on the way
        # out a tick can arrive after the bar has already gone.
        if not self.query("#player"):
            return
        self._frame += 1
        if self._digging:
            self._spin()
        if self.player.take_finished():
            # Auditioning a crate means hearing all of it, not pressing a key
            # between every track.
            self._advance_playback()
            return
        if self.player.playing:
            self._player_bar().refresh_bar()
            self._prepare_next()
        elif not self._digging:
            self._sleep()

    @property
    def frame_interval(self) -> float:
        return TICK if self.animation_level == "full" else CALM_TICK

    def _wake(self) -> None:
        if self._ticker is not None:
            self._ticker.resume()

    def _sleep(self) -> None:
        if self._ticker is not None:
            self._ticker.pause()

    def _prepare_next(self) -> None:
        """Get the next track ready while this one plays it out.

        Everything a track needs - a signed URL, a waveform, the audio itself -
        used to be fetched after the previous one ended, which put a second of
        "Loading" between every pair of tracks in the crate.
        """

        duration = self.player.duration
        if not duration or duration - self.player.position > PREFETCH_LEAD:
            return
        index = self._step_from_playing(1)
        if index is None:
            return
        track = self.visible_rows[index].track
        if not track.id or self._preparing == track.key:
            return
        if self._prepared is not None and self._prepared.key == track.key:
            return
        self._discard_prepared()
        self._preparing = track.key
        self.prepare_track(track)

    @work(thread=True, exclusive=True, group="prefetch")
    def prepare_track(self, track: Track) -> None:
        try:
            stream = resolve_stream(self.client, track.id)
            samples = fetch_waveform(self.client, stream.waveform_url)
            source = open_source(self.client.session, stream.url)
        except Exception as exc:
            # Nothing is owed here: if this fails the track loads the ordinary
            # way in its own time, and says so then.
            LOGGER.debug("Could not prepare %s: %s", track.label, exc)
            self.call_from_thread(self._preparation_done, track.key, None)
            return
        prepared = Prepared(track=track, stream=stream, waveform=samples, source=source)
        self.call_from_thread(self._preparation_done, track.key, prepared)

    def _preparation_done(self, key: str, prepared: Prepared | None) -> None:
        if self._preparing != key:
            # The list moved under it while it was working.
            if prepared is not None:
                prepared.close()
            return
        self._preparing = ""
        self._prepared = prepared

    def _discard_prepared(self) -> None:
        if self._prepared is not None:
            self._prepared.close()
            self._prepared = None

    def _drop_stale_preparation(self) -> None:
        """A filter that changes what comes next makes the prepared track useless."""

        if self._prepared is None:
            return
        index = self._step_from_playing(1)
        following = self.visible_rows[index].track.key if index is not None else None
        if self._prepared.key != following:
            self._discard_prepared()

    def _take_prepared(self, track: Track) -> Prepared | None:
        if self._prepared is None or self._prepared.key != track.key:
            return None
        prepared, self._prepared = self._prepared, None
        return prepared

    def _advance_playback(self) -> None:
        """Roll on by itself, taking the cursor only if it was keeping up.

        Asking the question here, rather than watching every cursor move, keeps
        it out of the way of the redraw - which moves the cursor too.
        """

        table = self.query_one("#tracks", DataTable)
        self._cursor_follows = self._playing_index() == table.cursor_row
        self._play_at(self._step_from_playing(1))

    def _playing_index(self) -> int | None:
        loaded = self.player.loaded
        if loaded is None:
            return None
        for index, row in enumerate(self.visible_rows):
            if row.track.key == loaded.track.key:
                return index
        return None

    def _player_op(self, operation) -> None:
        """Run a player call. Every one of them can hit a missing audio device."""

        try:
            operation()
        except PlaybackUnavailable as exc:
            self._playback_failed(str(exc))
            return
        self._player_bar().refresh_bar()

    def action_play_pause(self) -> None:
        row = self.current_row()
        if row is None:
            return
        loaded = self.player.loaded
        if loaded is not None and loaded.track.key == row.track.key:
            self._player_op(self.player.toggle)
            self._wake()
            return
        # Playing what the cursor is on re-couples the two.
        self._cursor_follows = True
        self._start_playback(row.track)

    def _start_playback(self, track: Track) -> None:
        if not track.id:
            self.notify("No track id, so there is nothing to stream", timeout=4)
            return
        self._wake()
        prepared = self._take_prepared(track)
        if prepared is not None:
            self._audio_ready(track, prepared.stream, prepared.waveform, prepared.source)
            return
        bar = self._player_bar()
        bar.message = f"Loading {track.label}"
        bar.refresh_bar()
        self.fetch_audio(track)

    @work(thread=True, exclusive=True, group="audio")
    def fetch_audio(self, track: Track) -> None:
        """Only resolves the URL - the audio itself is decoded off the socket."""

        try:
            stream = resolve_stream(self.client, track.id)
            samples = fetch_waveform(self.client, stream.waveform_url)
        except (SoundCloudError, PlaybackUnavailable, OSError) as exc:
            self.call_from_thread(self._playback_failed, str(exc))
            return
        self.call_from_thread(self._audio_ready, track, stream, samples)

    def _audio_ready(
        self, track: Track, stream: Stream, samples: list[int], source=None
    ) -> None:
        bar = self._player_bar()
        bar.message = ""
        try:
            self.player.load(track, stream, self.client.session, samples, source)
            self.player.play()
        except PlaybackUnavailable as exc:
            self._playback_failed(str(exc))
            return
        except Exception as exc:  # a bad stream must not take the app down
            self._playback_failed(f"Could not start the stream ({exc})")
            return
        # Resolving the stream takes about half a second, and a frame landing in
        # the middle of it finds nothing playing and puts the timer back to
        # sleep - so this is where it has to be woken, not where it was asked for.
        self._wake()
        # Redraw first so the play marker lands on the new row, then chase it.
        self.refresh_rows()
        self._focus_playing_track()
        bar.refresh_bar()

    def _playback_failed(self, message: str) -> None:
        bar = self._player_bar()
        bar.message = message
        bar.refresh_bar()
        self.notify(message, severity="warning", timeout=6)

    def _focus_playing_track(self) -> None:
        """Drag the cursor to what is playing, unless you steered it away.

        Wandering down the list while something plays is normal, and having the
        cursor yanked back on every auto-advance would make it impossible.
        """

        if not self._cursor_follows:
            return
        index = self._playing_index()
        if index is not None:
            self.query_one("#tracks", DataTable).move_cursor(row=index)

    def action_seek(self, direction: int) -> None:
        if self.player.loaded is None:
            return
        self._player_op(lambda: self.player.nudge(direction * SEEK_STEP))

    def _step_from_playing(self, step: int) -> int | None:
        if not self.visible_rows:
            return None
        playing = self._playing_index()
        # Nothing of ours is playing, so step from wherever you are looking.
        start = playing if playing is not None else self.query_one("#tracks", DataTable).cursor_row
        index = start + step
        return index if 0 <= index < len(self.visible_rows) else None

    def _play_at(self, index: int | None) -> None:
        if index is None:
            if self.visible_rows:
                self.notify("End of the list", timeout=2)
            return
        self._start_playback(self.visible_rows[index].track)

    def action_play_step(self, step: int) -> None:
        # Asking for the next track means you want to be taken there.
        self._cursor_follows = True
        self._play_at(self._step_from_playing(step))

    def action_volume(self, direction: int) -> None:
        self._player_op(lambda: self.player.change_volume(direction * VOLUME_STEP))

    def action_mute(self) -> None:
        self._player_op(self.player.toggle_mute)
