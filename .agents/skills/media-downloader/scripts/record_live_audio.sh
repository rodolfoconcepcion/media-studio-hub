#!/bin/bash
# Record live desktop / browser stream via Virtual Audio Cable

if [ "$1" = "-h" ] || [ "$1" = "--help" ]; then
    echo "Usage: record-live-audio [output_file.mp3] [duration_seconds]"
    echo "Example: record-live-audio ~/Music/song.mp3 180"
    exit 0
fi

OUTPUT_FILE="${1:-$HOME/Music/live_recording_$(date +%Y%m%d_%H%M%S).mp3}"
DURATION="$2" # Optional duration in seconds

mkdir -p "$(dirname "$OUTPUT_FILE")"

# Ensure virtual audio cable is active
if ! pactl list short sources | grep -q "Virtual_Audio_Cable"; then
    pactl load-module module-null-sink sink_name=Virtual_Audio_Cable sink_properties=device.description="Virtual_Audio_Cable" >/dev/null 2>&1
fi

echo "🎙️  Recording live stream from Virtual Audio Cable -> $OUTPUT_FILE"
echo "👉 Press 'q' or Ctrl+C anytime to stop and save."

if [ -n "$DURATION" ]; then
    ffmpeg -y -f pulse -i Virtual_Audio_Cable.monitor -t "$DURATION" -c:a libmp3lame -b:a 320k "$OUTPUT_FILE"
else
    ffmpeg -y -f pulse -i Virtual_Audio_Cable.monitor -c:a libmp3lame -b:a 320k "$OUTPUT_FILE"
fi

echo "✅ Saved live recording to: $OUTPUT_FILE"
