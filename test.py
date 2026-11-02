import json, subprocess, re
from pathlib import Path

root = Path("/home/hoang/experiments/AI_Create_video")
index = json.loads((root / "index.json").read_text())
audio_preview = index["audio"]

total_tts_dur = 0.0
total_artificial_pause = 0.0
total_hold_sec = 0.0
total_voice_wait_sec = 0.0
total_native_video_sec = 0.0

for scene in audio_preview["scenes"]:
    for b in scene.get("beats", []):
        moving = (b["source_end_frame"] - b["source_start_frame"]) / 30.0
        length = (b["end_frame"] - b["start_frame"]) / 30.0
        hold = b["hold_frames"] / 30.0
        voice_and_pauses = length - hold if hold > 0 else None
        total_native_video_sec += moving
        total_hold_sec += hold

print(f"Tổng thời lượng bản dựng: {audio_preview['duration']:.2f}s")
print(f"Thời lượng hình gốc (Manim): {total_native_video_sec:.2f}s")
print(f"Thời gian đóng băng hình chờ voice (hold_seconds): {total_hold_sec:.2f}s")