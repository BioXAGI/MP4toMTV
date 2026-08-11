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
