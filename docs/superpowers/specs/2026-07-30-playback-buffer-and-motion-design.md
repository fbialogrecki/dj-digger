# Buffered playback, prefetch, and a player bar that moves with the music

## Why

Two complaints, one cause.

Seeking stalls. `Player.seek` closes the socket and reopens it with a `Range`
header, so every `[` and `]` costs a fresh CloudFront connection and about half a
second of silence - even seeking ten seconds back into audio that just played.

Gaps between tracks. Nothing about the next track is known until the current one
ends, so every auto-advance shows "Loading" while the stream URL and the waveform
are fetched. On an 83-track crate that is 83 pauses.

Both are the same missing thing: no copy of what already came through. And with
no copy, the interface has nothing to be lively about either - the player bar
redraws four times a second and shows a static picture of a track that is
playing.

## What changes

### An in-memory buffer behind the decoder

`HttpSource` gains a background thread that pulls the rest of the response into a
`bytearray` as fast as the connection allows, in parallel with playback.

- `read()` serves from the buffer, blocking only when the decoder has outrun the
  download - in practice only during the first half second.
- `seek()` becomes an index move rather than an HTTP request.
- First audio is unchanged: playback starts off the socket immediately, it is not
  waiting for the download to finish.

`Player.seek` currently drops the source, which would throw the buffer away. The
source now survives a seek and only the decoder is rebuilt at the new frame, so a
seek costs the tens of milliseconds it takes to find an MP3 frame instead of the
half second it takes to open a connection.

Failure is quiet and non-fatal. If the download thread dies mid-file, `seek()`
falls back to today's behaviour and issues a `Range` request for what it does not
have. Playback continues either way.

Memory is capped by holding exactly two buffers - the playing track and the
prepared one - and by refusing to buffer a response larger than 50 MB, which is a
two hour set rather than a track. An unbuffered response behaves exactly as it
does today.

### Prefetching the next track

When less than 20 seconds of the current track remain, the work that today
happens after it ends starts in the background instead: signed stream URL,
waveform, and the buffer.

"Next" means the next visible row - the same one auto-advance would move to - so
changing the store filter, the search, or the hidden-handled toggle invalidates
what was prepared. When the track actually ends, everything is ready and the
change is seamless.

Prefetch failures go to the log and nowhere else. If preparing fails, the track
plays through the normal path in its own time and any error surfaces then.

### A level that comes from the audio itself

`Player._feed` already has every decoded sample in its hands. It records the peak
of each frame's worth - two C-level calls on a slice of an `array`, which is
nothing on a callback thread that must not be made to wait - onto a short queue
the interface drains one reading per frame. Per callback would be too coarse: a
callback carries about a tenth of a second, and the loudest sample in a tenth of
a second of techno is a kick every time, which is a meter that never moves.

The measurement is taken before the volume scaling, so turning the app down does
not dim the picture - it is the music that should pulse, not the fader.

The shaping is applied by the UI when it draws a frame, where it costs nothing
and can be tested without an audio device. Fast attack and slow release, floored
by whatever is arriving, turns a raw peak into a breath that follows the kick.
The result is scaled against a window that follows the loudest and quietest of
the last second or two, not against full scale - measured on a real crate, a
brickwalled master sits between 0.92 and 1.00 from beginning to end and would
never move otherwise. When that window closes to nothing, nothing is happening,
and it reads as dark rather than as its own hiss stretched to full height.

### A waveform that is cheap enough to animate

The shape of a track does not change while it plays, so both rows of block glyphs
are built once per (waveform, width) pair and cached. A frame then assembles the
cached strings with a handful of style spans instead of several hundred
character-by-character appends. This is what makes thirty frames a second cost
less than today's four.

The played region stays a steady cyan, because it is history and flicker there
only tires the eye. The leading edge moves: the dozen or so columns just before
the playhead brighten with the level, and the playhead itself steps from dim cyan
through bright to white on a strong hit. Brightness is quantised to four steps so
the colour changes only when something actually changed.

The ticker goes from four to thirty ticks a second, sleeps entirely when nothing
is playing, and drops back to four under `TEXTUAL_ANIMATIONS=none` - the thing
that repaints most has no business ignoring the setting that says not to. Only
the player bar is refreshed; the table is not told anything is happening.

### Motion for what your hands do

- Marking a row with `g` or `s` lights it for a quarter second, then it settles.
  This requires rewriting only the cells that changed rather than clearing the
  whole table, so the redraw flicker goes with it - otherwise the flash would
  itself be a flicker.
- The player bar grows from zero to three rows over 200 ms rather than appearing
  from nothing, and collapses the same way.
- Digging a playlist shows a turning indicator beside the progress text, so
  "working" is distinguishable from "hung".

### A BPM column - looked at and dropped

The plan assumed SoundCloud's track payload carried a `bpm` field. It does not:
checked against the live API on six tracks from a real crate, the key is not
merely null, it is absent, and there is no `key_signature` or tempo field either.

That leaves only what the artist wrote down, and only where they said it was a
tempo - `165BPM` in the tags, `(150 BPM)` in the title, `BPM: 145` in the
description. A bare `150` cannot count; it is as likely to be a catalogue number
or a year, and a wrong tempo is worse than none. On an 83 track hard techno crate
that finds four.

A conditional column - present only when the crate has any tempos - was built and
then removed. Four rows in eighty-three does not carry a column, even one that
hides itself, and every reader of the table has to learn a column that is usually
not there. Recorded here so nobody reaches for the `bpm` field again.

## Testing

- The buffer runs against a fake session that hands back bytes in pieces: seeking
  once buffered issues no second request, seeking before the download finished
  waits rather than failing, and a download thread that dies falls back to a
  `Range` request.
- Prefetch: a prepared track starts without a single extra API call, and changing
  a filter throws the preparation away.
- Level smoothing and the choice of brightness step are pure functions and are
  tested directly, with no sound card involved.
- The flash is tested through Textual's timer: the style right after the key, and
  the style once the time is up.

## Order of work

1. Buffer and seeking. Independent of everything else and felt immediately.
2. Prefetching the next track.
3. The visual layer: level, waveform render, pulse, frame rate, flash, player bar
   growth, digging indicator.
