# 🎙️ Auto-Transcribe Watcher

Automatically transcribes `.mp3` files the moment they finish syncing to a local iCloud-connected folder on your Mac — then notifies you on your iPhone via iMessage when transcription starts and again when it's done.

No third-party apps. No cloud services. Runs silently in the background and survives reboots.

---

## How It Works

```
iPhone → records audio
    ↓
Saves .mp3 to iCloud Drive folder  (e.g. Voice Memos or Files app)
    ↓
iCloud syncs the file to your Mac
    ↓
watcher.py detects the new file
    ↓
Waits for the file to fully download (handles slow connections gracefully)
    ↓
Copies the file to /tmp (avoids transcribing directly from iCloud Drive)
    ↓
iMessage sent: "🎙️ Transcription started — File: filename.mp3, Mode: Duo"
    ↓
Runs transcribe.py on the local temp copy
    ↓
transcribe.py creates <filename>_transcript.txt
    ↓
Transcript moved back next to the original .mp3 in iCloud Drive
    ↓
iMessage sent: "✅ Transcription complete! File: filename.mp3"
```

The watcher is registered as a **launchd agent** — macOS's native background service manager — so it starts at login, runs silently, and automatically restarts if anything goes wrong.

---

## Prerequisites

### 1. Transcription script
This watcher is designed to work alongside [apple-silicon-transcribe](https://github.com/alamontagne/apple-silicon-transcribe), which handles the actual speech-to-text using WhisperX with speaker diarization.

Make sure `transcribe.py` is set up and working before proceeding. Test it manually first:

```bash
cd /Users/alamontagne/Documents/Trancscribe
/Users/alamontagne/whisperx-env/bin/python3 transcribe.py your-audio-file.mp3
```

If that produces a `_transcript.txt` file, you're ready.

### 2. Python 3 (in a virtual environment)
The watcher uses the Python interpreter from the `whisperx-env` virtual environment. See [apple-silicon-transcribe](https://github.com/alamontagne/apple-silicon-transcribe) for setup instructions.

### 3. watchdog library
Install `watchdog` into your virtual environment:
```bash
source ~/whisperx-env/bin/activate
pip install watchdog
```

> **What is watchdog?**  
> A Python library that listens to the macOS file system for changes using the native `FSEvents` API. Far more efficient than polling a directory in a loop.

---

## Folder Structure — Speaker Modes

The watcher reads the **subfolder** an MP3 lands in to determine how many speakers to tell pyannote to expect. This directly improves diarization accuracy.

```
/Users/alamontagne/Documents/Trancscribe/
    solo/      ← 1 speaker  (monologue, voicemail, lecture)
    duo/       ← 2 speakers (interview, phone call, podcast)
    group/     ← 3+ speakers, auto-detected by pyannote
```

Create these once:
```bash
mkdir -p "/Users/alamontagne/Documents/Trancscribe/solo"
mkdir -p "/Users/alamontagne/Documents/Trancscribe/duo"
mkdir -p "/Users/alamontagne/Documents/Trancscribe/group"
```

Just drop your `.mp3` into the appropriate subfolder and the watcher handles the rest.

---

## Installation

### Step 1 — Copy files into your Transcribe directory

Place both files in `/Users/alamontagne/Documents/Trancscribe/`:

```
/Users/alamontagne/Documents/Trancscribe/
├── transcribe.py                               ← from apple-silicon-transcribe
├── watcher.py                                  ← this repo
└── com.alamontagne.transcribe-watcher.plist    ← this repo
```

### Step 2 — Edit `watcher.py` configuration

Open `watcher.py` and update the config block near the top:

```python
WATCH_DIR    = "/Users/alamontagne/Documents/Trancscribe"  # path to your folder
VENV_PYTHON  = "/Users/alamontagne/whisperx-env/bin/python3"
SCRIPT_PATH  = os.path.join(WATCH_DIR, "transcribe.py")
PHONE_NUMBER = "+15141234567"   # your phone number in international format
TEMP_DIR     = "/tmp/transcribe_processing"
```

`PHONE_NUMBER` should be your phone number in international format (e.g. `+15141234567`). Sending to yourself works perfectly — it shows up in your own iMessage thread on iPhone.

### Step 3 — Edit the plist — add your HuggingFace token

Open `com.alamontagne.transcribe-watcher.plist` and replace the placeholder with your actual HF token:

```xml
<key>HF_TOKEN</key>
<string>hf_REPLACE_WITH_YOUR_TOKEN</string>
```

Your token is at https://huggingface.co/settings/tokens. This ensures the token is always available to the background service even when your shell profile hasn't been sourced.

### Step 4 — Test the watcher manually

Before registering it as a background service, run it directly in a terminal:

```bash
cd /Users/alamontagne/Documents/Trancscribe
/Users/alamontagne/whisperx-env/bin/python3 watcher.py
```

Drop an `.mp3` into `solo/`, `duo/`, or `group/` and watch the output. You should receive two iMessages — one when transcription starts, and one when it completes.

Press `Ctrl+C` to stop once confirmed.

### Step 5 — Grant Automation permission (first-time only)

When `watcher.py` sends its first iMessage via `osascript`, macOS will show a permission prompt:

> *"Terminal" wants to control "Messages".*

Click **OK**. This only happens once. If you miss it:  
`System Settings → Privacy & Security → Automation → Terminal → Messages ✓`

### Step 6 — Register as a launchd background service

```bash
cp /Users/alamontagne/Documents/Trancscribe/com.alamontagne.transcribe-watcher.plist \
   ~/Library/LaunchAgents/

launchctl load ~/Library/LaunchAgents/com.alamontagne.transcribe-watcher.plist
```

The watcher is now running in the background and will start automatically at every login.

---

## Managing the Service

### Check if it's running
```bash
launchctl list | grep transcribe-watcher
```
A PID in the first column means it's active. The middle number is the exit code — `0` means running cleanly.

### View live logs
```bash
# Watcher activity
tail -f /Users/alamontagne/Documents/Trancscribe/watcher.log

# Startup errors
tail -f ~/Library/Logs/transcribe-watcher.err
```

### Reload after making changes
```bash
launchctl unload ~/Library/LaunchAgents/com.alamontagne.transcribe-watcher.plist
launchctl load   ~/Library/LaunchAgents/com.alamontagne.transcribe-watcher.plist
```

### Stop permanently
```bash
launchctl unload ~/Library/LaunchAgents/com.alamontagne.transcribe-watcher.plist
```

---

## iCloud Handling — Why We Copy to /tmp

When iCloud syncs a file to your Mac, it can appear in Finder as a small **stub placeholder** before the full content has downloaded. Attempting to transcribe a stub produces garbage output or an outright error.

`watcher.py` handles this with a two-stage approach:

1. **Stability check** — polls the file size every 2 seconds, waits until it has been non-zero and unchanged for 15 consecutive seconds (confirming the download is complete). Timeout: 3 minutes.
2. **Materialisation** — attempts to read the first 4 KB of the file, which forces iCloud to fully flush it to local disk.
3. **Local copy** — copies the file to `/tmp/transcribe_processing/` before transcribing. This avoids any mid-transcription iCloud interference and ensures the temp file is cleaned up regardless of outcome.

The final transcript is always moved back to sit next to the original `.mp3` in iCloud Drive.

---

## Notifications

All notifications are sent via **iMessage using `osascript`** — no third-party services required. Messages.app must be signed in on the Mac.

| Event | Message |
|---|---|
| Transcription started | `🎙️ Transcription started` with filename and speaker mode |
| Success | `✅ Transcription complete!` with filename, mode, and transcript name |
| Timeout (iCloud) | `❌ iCloud sync timed out` — file never fully downloaded |
| Timeout (processing) | `⏰ Transcription timed out` — job exceeded 30-minute limit |
| HF_TOKEN missing | `❌ HF_TOKEN not set` — diarization cannot run |
| Script error | `❌ Transcription failed` — check watcher.log |
| Unexpected error | `❌ Unexpected error` — check watcher.log |

---

## File Structure

```
Trancscribe/
├── transcribe.py                               ← transcription script
├── watcher.py                                  ← file watcher + notification logic
├── com.alamontagne.transcribe-watcher.plist    ← launchd service definition
├── watcher.log                                 ← auto-created: activity log
├── solo/
│   ├── interview.mp3                           ← dropped in by you
│   └── interview_transcript.txt               ← auto-generated
├── duo/
└── group/
```

---

## Troubleshooting

| Symptom | Where to look / what to do |
|---|---|
| Watcher not starting at login | `~/Library/Logs/transcribe-watcher.err` |
| File detected but transcription fails | `watcher.log` in the watch directory |
| No iMessage received | `System Settings → Privacy & Security → Automation → Terminal → Messages` |
| HF_TOKEN errors | Confirm the token in the plist starts with `hf_` and is valid |
| iCloud file keeps timing out | Increase `timeout=` in `wait_for_file_ready()` (default: 180 s) |
| Two files with the same name processed incorrectly | Already handled — dedup is keyed on the full absolute path, not just the filename |

---

## Related

- [apple-silicon-transcribe](https://github.com/alamontagne/apple-silicon-transcribe) — the WhisperX-based transcription script this watcher is built around
- [watchdog documentation](https://python-watchdog.readthedocs.io/) — Python file system events library
- [launchd reference](https://developer.apple.com/library/archive/documentation/MacOSX/Conceptual/BPSystemStartup/Chapters/CreatingLaunchdJobs.html) — Apple's background service documentation
