"""Cấu hình hình ảnh cho khung hình mẫu ở bước 2.2."""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
PREVIEW_DIR = PROJECT_ROOT / "output" / "dao_ham" / "previews"

VIDEO_WIDTH = 1920
VIDEO_HEIGHT = 1080
FRAME_WIDTH = 128 / 9
FRAME_HEIGHT = 8
FONT = "DejaVu Sans"
TITLE_SIZE = 38
SECTION_SIZE = 25
BODY_SIZE = 20
VALUE_SIZE = 36

BACKGROUND = "#0D1726"
PANEL = "#142338"
BORDER = "#2B4059"
FOREGROUND = "#F2F6FC"
MUTED = "#A6B8CD"
ACCENT = "#56DFC5"  # Dùng chung cho xe và điểm trên đồ thị.
CURVE = "#8AB8FF"
ROAD = "#293B51"
