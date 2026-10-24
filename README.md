# AI Lyric Sync for Windows

This local tool aligns official, untimed lyrics to detected vocals and burns the synchronized lines into a video. The official text is preserved; Whisper is used only to locate the words in time.

## One-time setup

Open PowerShell in this folder and run:

```powershell
powershell -ExecutionPolicy Bypass -File .\setup.ps1
```

The setup downloads `whisper.cpp` and its multilingual small model (about 466 MB). Audio and lyrics remain on your computer.

## Prepare the lyrics

Save a UTF-8 file such as `lyrics.txt`. Put each desired subtitle line on its own line and write repeated choruses each time they occur:

```text
This is the first lyric line
This is the next lyric line
And this is the chorus
```

Section labels such as `[Verse 1]`, `[Chorus]`, and `[Instrumental]` are ignored.

## Create the lyric video

```powershell
python .\lyric_sync.py "C:\Videos\song.mp4" "C:\Videos\lyrics.txt"
```

The tool creates `song-with-lyrics.srt` and `song-with-lyrics.mp4` beside the original video. It never changes the input video.

## Process separate song folders

Create one folder per song inside `input`. Each song folder should contain exactly one media file and its lyrics text file:

```text
input/
  Song One/
    song-one.mp4
    lyrics.txt
    title.txt
  Song Two/
    song-two.mp3
    song-two-lyrics.txt
  Song Three/
    cover.jpg
    song-three.mp3
    lyrics.txt
    style.txt
```

Double-click `run_all.bat`, or run:

```powershell
python .\batch_sync.py
```

Completed videos and SRT files are placed in `finished`. Existing up-to-date results are skipped. Audio-only songs receive a 1080p dark background automatically.

When a song folder contains one still image (`JPG`, `PNG`, `WebP`, or `BMP`), one audio file, and lyrics, the image becomes the 1080p background. An audio-reactive equalizer with a soft glow and reflection is drawn above the synchronized lyrics. This visual layer does not change lyric alignment or SRT timestamps.

Choose a design by placing one of these names in `style.txt`: `dots` (default), `neon-blocks`, `neon-particles`, or `mirror-bars`. `neon-particles` adds restrained, slowly moving cyan sparks near the baseline of a translucent cyan-to-violet block spectrum. The equalizer is positioned above a dedicated lyric area for clear separation.

The visualizer automatically samples two prominent colors from the cover image and boosts them for visibility. If the cover is too dark, neutral, or cannot be sampled, it falls back to cyan-to-violet.

When a song folder contains both a video and an audio file, the video supplies the visuals and the separate audio file replaces the video's soundtrack. Replacement audio is encoded to high-quality 320 kbps AAC in the finished MP4.

The contents of `title.txt` are displayed in the center for the first three seconds. If `title.txt` is absent or empty, the selected media filename without its extension is used.

Useful options:

```powershell
# Specify a language when automatic detection is wrong
python .\lyric_sync.py "song.mp4" "lyrics.txt" --language en

# Choose the output name and font size
python .\lyric_sync.py "song.mp4" "lyrics.txt" -o "finished.mp4" --font-size 34

# Generate only the timed SRT file
python .\lyric_sync.py "song.mp4" "lyrics.txt" --subtitles-only

# Build an equalizer lyric video from a still image and audio
python .\lyric_sync.py "cover.jpg" "lyrics.txt" --audio "song.mp3"

# Choose a blue/purple block design
python .\lyric_sync.py "cover.jpg" "lyrics.txt" --audio "song.mp3" --equalizer-style neon-blocks
```

Singing, heavy effects, overlapping vocals, and very loud backing tracks can reduce timing accuracy. The tool reports low-confidence lines so they can be reviewed in the generated SRT.

## Numbered storyboard images

To assemble a sequence of poses, add **`storyboard.json`** to a song folder.
This explicitly enables storyboard mode; multiple images without this file
continue to be rejected. No character library or external pose directory is
required. Export each numbered pose as a separate image first; the tool does
not split a numbered contact sheet into individual poses.

```text
input/
  Jiameng Night Battle/
    song.wav
    lyrics.txt
    title.txt                 # optional
    storyboard.json
    poses/
      01-ma-chao.png
      02-zhang-fei.png
      10-crossed-spears.jpg
```

Example `storyboard.json` (UTF-8, with or without BOM):

```json
{
  "default_duration": 4,
  "transition": "fade",
  "transition_duration": 0.5,
  "shots": [
    {"number": 10, "image": "poses/10-crossed-spears.jpg", "duration": 6},
    {"number": 1, "image": "poses/01-ma-chao.png"},
    {"number": 2, "image": "poses/02-zhang-fei.png", "duration": 3}
  ]
}
```

- Shots play by ascending **`number`**, so this example plays 1, 2, then 10,
  regardless of filenames, directory order, or JSON list order. Numbers must
  be unique positive integers; gaps are allowed. Only listed images are used.
- Paths are relative to the song folder and must stay inside it. Forward
  slashes are recommended on Windows; escaped backslashes also work.
  JPG/JPEG, PNG, WebP and BMP are supported. Spaces and Chinese filenames work.
- `duration` and `default_duration` are positive seconds for the intended
  pacing. The complete sequence plays once: all shot lengths are scaled
  proportionally to fit the soundtrack, whether shorter or longer than the
  requested total. At least one frame is reserved for each shot, and remaining
  frames are distributed by relative duration with deterministic rounding.
  The silent intermediate ends at the first 30 fps frame boundary at or after
  the audio end (less than one frame of padding). The lyric pipeline uses the
  original audio and `-shortest` for the final MP4; the song is never stretched.
- `transition` defaults to `cut`. Optional `fade` fades **through black** at
  internal shot boundaries; it is not a cross dissolve. Each adjacent shot
  uses `transition_duration` seconds (default 0.5) within its allocated slot.
  Fades do not add time or shift subtitles. If scaled shots cannot fit their
  fades plus a visible frame, the song fails with an actionable error.
- Storyboard folders require exactly one audio file and no video. A supplied
  video together with `storyboard.json` is rejected to avoid ambiguous intent.
  Remove the configuration to use the conventional video workflow.

Run `python .\batch_sync.py` or double-click `run_all.bat` as usual. FFmpeg and
ffprobe must be available in `tools/` as Windows `.exe` files or on `PATH`;
FFmpeg needs the `libx264` encoder. No additional Python packages are needed
(Python 3.10 or later). Each shot is encoded separately at 1920×1080, 30 fps,
H.264/yuv420p with aspect ratio preserved and black padding. Temporary shots
are concatenated, then passed to `lyric_sync.py` with `--audio song.wav`.
Rendering uses temporary disk space roughly comparable to the intermediate
video and encodes visuals twice (assembly and subtitle burn-in).

Alignment, SRT generation, title rendering, and subtitle burn-in remain in
the existing lyric pipeline. Audio-only, single-cover equalizer, video-only,
and video-with-replacement-audio inputs retain their behavior. An omitted
title defaults to the soundtrack filename for storyboards.

Results still go to `finished/`. Storyboard cache metadata is written only
after successful processing. Changes to the configuration, listed images,
soundtrack, lyrics, style or title trigger a rebuild; removing a title or
style also invalidates the storyboard cache. Deleting the MP4, SRT, or
`*-with-lyrics.storyboard-cache.json` forces a rebuild. Invalid configuration
or an FFmpeg failure is reported per song, and the batch continues.

For a standalone silent intermediate, choose an output outside the song folder
(so the next batch run does not mistake it for an input video):

```powershell
python .\storyboard.py "input\Jiameng Night Battle\storyboard.json" "input\Jiameng Night Battle\song.wav" -o "work\battle-storyboard.mp4"
python .\lyric_sync.py "work\battle-storyboard.mp4" "input\Jiameng Night Battle\lyrics.txt" --audio "input\Jiameng Night Battle\song.wav"
```

## Regression tests

```powershell
python -m unittest discover -s tests -v
```

Tests generate their own numbered images and short WAV soundtrack; they do
not use a local pose library or download Whisper models. They cover numeric
ordering, configuration errors, duration scaling, batch handoff and caching,
legacy input modes, failure handling, and subtitle timestamps with a stubbed
Whisper transcript. When FFmpeg and ffprobe are on `PATH`, integration tests
also render real 1080p cut/fade videos and check duration, shot colors/order,
and a non-frame-aligned soundtrack. Otherwise these render tests are skipped.
