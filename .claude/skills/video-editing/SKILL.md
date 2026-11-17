---
name: video-editing
description: Read first whenever editing, cutting, trimming, captioning, resizing, re-encoding, or downloading any video for the user (Reels, TikTok, Shorts, YouTube). Sets the three-tool workflow - FFmpeg for cuts and mixing, HyperFrames for captions and graphics, yt-dlp for saving Reels and online videos - and how to get files in and out from the user's phone via Google Drive.
---

# Video editing workflow

Always use these three tools for the user's video work:

| Tool | Use it for |
|---|---|
| **FFmpeg** (`ffmpeg`, `ffprobe`) | Cuts, trims, joins, speed, crop/resize (9:16, 1:1, 16:9), audio mixing, music beds, loudness, compression, format conversion |
| **HyperFrames** (`hyperframes` CLI + the `hyperframes`, `hyperframes-cli` and `media-use` skills in this folder) | Captions (word-timed), titles, lower thirds, text overlays, animated graphics, branded intros/outros |
| **yt-dlp** (`yt-dlp`) | Downloading Reels, TikToks, Shorts, YouTube and other online videos as source or reference |

## Setup check (run first)

```bash
command -v ffmpeg  || apt-get install -y ffmpeg
command -v yt-dlp  || pip install -U yt-dlp
command -v hyperframes || npm install -g hyperframes
hyperframes telemetry disable
```

## Getting the video in and out

The user works from their phone; files over ~30 MB cannot be attached in chat.
1. The user uploads the video to Google Drive and gives the file name.
2. Find it with the Google Drive connector (`search_files`), then download it (`download_file_content`) into the scratchpad.
3. If the connector cannot handle the size, ask for a public "Anyone with the link" share link and propose allowing `drive.google.com` and `drive.usercontent.google.com` on the environment's network, then download with `curl -L` or `yt-dlp`.
4. Deliver the finished file back to Google Drive (`create_file`) and tell the user its name. Keep raw inputs and renders out of git.

## Work loop

1. **Probe first:** `ffprobe -v error -show_entries format=duration:stream=codec_name,width,height,r_frame_rate -of json in.mp4`
2. **Cut / assemble with FFmpeg.** Re-encode for frame-accurate cuts. Defaults for social: `-c:v libx264 -preset medium -crf 20 -pix_fmt yuv420p -c:a aac -b:a 192k -movflags +faststart`.
3. **Captions and graphics with HyperFrames:** read the `hyperframes` skill first. Transcribe with `hyperframes transcribe`, build the composition over the cut video, run `hyperframes check`, then `hyperframes render -o out.mp4`.
4. **Verify before delivering:** grab stills with `ffmpeg -ss <t> -i out.mp4 -frames:v 1 frame.png` (or `hyperframes snapshot`) and look at them; check duration and audio levels.

## Handy FFmpeg recipes

- Trim: `ffmpeg -i in.mp4 -ss 00:00:05 -to 00:00:20 -c:v libx264 -crf 20 -c:a aac out.mp4`
- Vertical 9:16 (1080x1920) with crop: `-vf "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920"`
- Vertical with blurred background: `-filter_complex "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=20[bg];[0:v]scale=1080:-2[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2"`
- Join clips: write `file 'a.mp4'` lines to `list.txt`, then `ffmpeg -f concat -safe 0 -i list.txt -c copy out.mp4` (re-encode if codecs differ)
- Music under voice: `-filter_complex "[1:a]volume=0.15[m];[0:a][m]amix=inputs=2:duration=first[a]" -map 0:v -map "[a]"`
- Speed up 1.25x: `-filter_complex "[0:v]setpts=PTS/1.25[v];[0:a]atempo=1.25[a]" -map "[v]" -map "[a]"`
- Loudness for social: `-af loudnorm=I=-14:TP=-1:LRA=11`
- Shrink file: raise `-crf` (23-28) or scale down to 720p.

## yt-dlp

- Best MP4: `yt-dlp -f "bv*+ba/b" --merge-output-format mp4 -o "%(title).80s.%(ext)s" <url>`
- Audio only: `yt-dlp -x --audio-format mp3 <url>`
- Only download content the user owns or has rights to use. Instagram/TikTok may need cookies and network access to those hosts.
