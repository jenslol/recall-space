#!/usr/bin/env bash
# recall-capture.sh — Quick capture script for desktop (CachyOS/KDE)
# Bind this to a keyboard shortcut in KDE System Settings.
#
# Dependencies: spectacle (KDE screenshot), curl, kdialog (optional for note)
# Usage:
#   recall-capture.sh              → screenshot only
#   recall-capture.sh --note       → screenshot + note dialog
#   recall-capture.sh --voice      → screenshot + voice note (5s)
#   recall-capture.sh --text       → text note only (from clipboard)
#
# Configuration:
RECALL_URL="${RECALL_URL:-http://your-gmktec-ip:8400}"

set -euo pipefail

MODE="${1:-screenshot}"
TMPDIR=$(mktemp -d)
trap 'rm -rf "$TMPDIR"' EXIT

case "$MODE" in
    --note)
        # Screenshot + note dialog
        SCREENSHOT="$TMPDIR/capture.png"
        spectacle -r -b -n -o "$SCREENSHOT" 2>/dev/null || \
        spectacle -a -b -n -o "$SCREENSHOT" 2>/dev/null

        if [ -f "$SCREENSHOT" ]; then
            NOTE=$(kdialog --inputbox "Add a note to this memory:" "" 2>/dev/null || echo "")
            curl -s -X POST "$RECALL_URL/api/memories" \
                -F "file=@$SCREENSHOT" \
                -F "user_note=$NOTE" > /dev/null
            notify-send "Recall Space" "Memory captured with note" -i dialog-information -t 2000
        fi
        ;;

    --text)
        # Capture clipboard text
        TEXT=$(xclip -selection clipboard -o 2>/dev/null || wl-paste 2>/dev/null || echo "")
        if [ -n "$TEXT" ]; then
            NOTE=$(kdialog --inputbox "Note for this text:" "" 2>/dev/null || echo "")
            curl -s -X POST "$RECALL_URL/api/memories" \
                -F "raw_text=$TEXT" \
                -F "user_note=$NOTE" > /dev/null
            notify-send "Recall Space" "Text captured" -i dialog-information -t 2000
        else
            notify-send "Recall Space" "Clipboard is empty" -i dialog-warning -t 2000
        fi
        ;;

    --voice)
        # Screenshot + 10s voice recording
        SCREENSHOT="$TMPDIR/capture.png"
        AUDIO="$TMPDIR/voice.webm"
        spectacle -r -b -n -o "$SCREENSHOT" 2>/dev/null || true

        notify-send "Recall Space" "Recording voice note... (press again to stop)" -i audio-input-microphone -t 3000

        # Record for up to 30 seconds, or until the script is killed
        timeout 30 ffmpeg -f pulse -i default -c:a libopus "$AUDIO" -y 2>/dev/null || true

        ARGS=()
        [ -f "$SCREENSHOT" ] && ARGS+=(-F "file=@$SCREENSHOT")
        [ -f "$AUDIO" ] && ARGS+=(-F "file=@$AUDIO")

        if [ ${#ARGS[@]} -gt 0 ]; then
            curl -s -X POST "$RECALL_URL/api/memories" "${ARGS[@]}" > /dev/null
            notify-send "Recall Space" "Voice note captured" -i dialog-information -t 2000
        fi
        ;;

    *)
        # Default: screenshot only (fastest path)
        SCREENSHOT="$TMPDIR/capture.png"
        spectacle -r -b -n -o "$SCREENSHOT" 2>/dev/null || \
        spectacle -a -b -n -o "$SCREENSHOT" 2>/dev/null

        if [ -f "$SCREENSHOT" ]; then
            curl -s -X POST "$RECALL_URL/api/memories" \
                -F "file=@$SCREENSHOT" > /dev/null
            notify-send "Recall Space" "Screenshot captured" -i dialog-information -t 2000
        fi
        ;;
esac
