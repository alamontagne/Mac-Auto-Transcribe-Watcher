# 🎙️ Auto-Transcribe Watcher

Automatically transcribes `.mp3` files the moment they finish syncing to a local iCloud-connected folder on your Mac — then notifies you on your iPhone via iMessage when the transcription is done.

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
Runs transcribe.py <filename.mp3>
    ↓
transcribe.py creates <filename.txt>  ← see apple-silicon-transcribe below
    ↓
iMessage sent to your iPhone: "✅ Transcription complete: filename.txt"
```

The watcher is registered as a **launchd agent** — macOS's native background service manager — so it starts at login, runs silently, and automatically restarts if anything goes wrong.

---

## Prerequisites

### 1. Transcription script
This watcher is designed to work alongside [apple-silicon-transcribe](https://github.com/alamontagne/apple-silicon-transcribe), which handles the actual speech-to-text using Whisper optimised for Apple Silicon.

Make sure `transcribe.py` is set up and working before proceeding. Test it manually first:

```bash
cd /Users/alamontagne/Documents/Trancscribe
python3 transcribe.py your-audio-file.mp3
```

If that produces a `.txt` file, you're ready.

### 2. Python 3
Comes pre-installed on macOS. Verify with:
```bash
python3 --version
```

### 3. watchdog library
Install the `watchdog` Python library — this is the only external dependency:
```bash
pip3 install watchdog
```

> **What is watchdog?**  
> `watchdog` is a Python library that listens to the macOS file system for changes — specifically, new files appearing in a folder. It's far more efficient than polling the directory in a loop. It uses macOS's native `FSEvents` API under the hood.

---

## Installation

### Step 1 — Copy files into your Transcribe directory

Place both files in `/Users/alamontagne/Documents/Trancscribe/`:

```
/Users/alamontagne/Documents/Trancscribe/
├── transcribe.py          ← from apple-silicon-transcribe
├── watcher.py             ← from this repo
└── com.alamontagne.transcribewatcher.plist  ← from this repo
```

### Step 2 — Edit `watcher.py` configuration

Open `watcher.py` and update the three config values near the top:

```python
WATCH_DIR    = "/Users/alamontagne/Documents/Trancscribe"  # path to your folder
SCRIPT       = "/Users/alamontagne/Documents/Trancscribe/transcribe.py"
IMESSAGE_TO  = "your@apple-id.com"   # your Apple ID email or phone number
```

`IMESSAGE_TO` can be your Apple ID email address or your phone number in international format (e.g. `+15141234567`). Sending a message to yourself works perfectly — it shows up in your own iMessage thread on iPhone.

### Step 3 — Test the watcher manually

Before registering it as a background service, run it directly in a terminal to make sure everything works:

```bash
cd /Users/alamontagne/Documents/Trancscribe
python3 watcher.py
```

Drop an `.mp3` into the folder (or copy one from iCloud) and watch the terminal output. You should see:
```
[Watcher] New MP3 detected: my-recording.mp3
[Watcher] Waiting for iCloud download to complete…
[Watcher] File ready (2048000 bytes): my-recording.mp3
[Watcher] Starting transcription: my-recording.mp3
[Watcher] ✅ Transcription complete: my-recording.txt
[iMessage] Sent: ✅ Transcription complete: my-recording.txt
```

Press `Ctrl+C` to stop once you've confirmed it works.

### Step 4 — Grant Automation permission (first-time only)

When `watcher.py` sends its first iMessage via `osascript`, macOS will show a permission prompt:

> *"Terminal" wants to control "Messages".*

Click **OK**. This only happens once. If you miss it, go to:  
`System Settings → Privacy & Security → Automation → Terminal → Messages ✓`

### Step 5 — Register as a launchd background service

Copy the plist to the LaunchAgents folder and load it:

```bash
cp /Users/alamontagne/Documents/Trancscribe/com.alamontagne.transcribewatcher.plist \
   ~/Library/LaunchAgents/

launchctl load ~/Library/LaunchAgents/com.alamontagne.transcribewatcher.plist
```

The watcher is now running in the background. It will start automatically every time you log in.

---

## Managing the Service

### Check if it's running
```bash
launchctl list | grep transcribewatcher
```
A PID number in the first column means it's active.

### View live logs
```bash
# Normal output
tail -f /Users/alamontagne/Documents/Trancscribe/watcher.log

# Errors (if any)
tail -f /Users/alamontagne/Documents/Trancscribe/watcher.err
```

### Restart after making changes to watcher.py
```bash
launchctl unload ~/Library/LaunchAgents/com.alamontagne.transcribewatcher.plist
launchctl load   ~/Library/LaunchAgents/com.alamontagne.transcribewatcher.plist
```

### Stop permanently
```bash
launchctl unload ~/Library/LaunchAgents/com.alamontagne.transcribewatcher.plist
```

---

## iCloud Timing — Why We Wait

When iCloud syncs a file to your Mac, the file can appear in Finder (and to the file system) as a tiny **stub placeholder** before the full content has downloaded. On a slow connection from your iPhone, this gap can be several minutes.

`watcher.py` handles this automatically:

- It detects the new file immediately
- It **polls the file size every second**
- It only proceeds once the file size has been **non-zero and unchanged for 5 consecutive seconds**
- It will wait up to **10 minutes** before timing out and sending a failure notification

This means it works correctly whether you're on the same WiFi network or uploading remotely over cellular.

---

## Notifications

Notifications are sent via **iMessage using `osascript`** — Apple's built-in scripting bridge. No third-party services, accounts, or apps required. Messages.app must be signed in on the Mac (it almost certainly is).

| Event | Message |
|-------|---------|
| Success | `✅ Transcription complete: filename.txt` |
| Timeout | `⚠️ Timed out waiting for filename.mp3 to download from iCloud.` |
| Script error | `❌ Transcription failed for filename.mp3: <error details>` |
| Watcher exception | `❌ Watcher exception on filename.mp3: <error details>` |

---

## File Structure

```
Trancscribe/
├── transcribe.py                          ← transcription script (apple-silicon-transcribe)
├── watcher.py                             ← this repo: file watcher + notification logic
├── com.alamontagne.transcribewatcher.plist ← this repo: launchd service definition
├── watcher.log                            ← auto-created: normal output log
├── watcher.err                            ← auto-created: error log
├── 2026-03-09_12_01_38.mp3               ← example: uploaded from iPhone
└── 2026-03-09_12_01_38.txt               ← example: auto-generated transcript
```

---

## Troubleshooting

**The watcher isn't detecting files**  
Run `launchctl list | grep transcribewatcher` — if there's no output, the service isn't loaded. Re-run the `launchctl load` command.

**iMessage isn't sending**  
Check `System Settings → Privacy & Security → Automation → Terminal → Messages`. Make sure the toggle is on.

**Transcription runs but produces an error**  
Check `watcher.err` for details. Also try running `transcribe.py` manually to isolate whether the issue is with the watcher or the transcription script itself.

**The file times out waiting for iCloud**  
The default timeout is 10 minutes. For very large files on slow connections, you can increase `timeout` in the `wait_until_complete()` function inside `watcher.py`.

---

## Related

- [apple-silicon-transcribe](https://github.com/alamontagne/apple-silicon-transcribe) — the Whisper-based transcription script this watcher is built around
- [watchdog documentation](https://python-watchdog.readthedocs.io/) — Python file system events library
- [launchd reference](https://developer.apple.com/library/archive/documentation/MacOSX/Conceptual/BPSystemStartup/Chapters/CreatingLaunchdJobs.html) — Apple's background service documentation
