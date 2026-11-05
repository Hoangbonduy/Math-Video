"""Resolution-independent portrait delivery contract (width, height)."""
FRAME_WIDTH = 9.0
FRAME_HEIGHT = 16.0
SAFE_X = 0.55
SAFE_TOP = 0.55
SAFE_BOTTOM = 1.5
SAFE_WIDTH = FRAME_WIDTH - 2 * SAFE_X
SAFE_HEIGHT = FRAME_HEIGHT - SAFE_TOP - SAFE_BOTTOM
MIN_FONT_SIZE = 22
PREVIEW_RESOLUTION = (360, 640)
FINAL_RESOLUTION = (1080, 1920)
FPS = 30


def profile():
    return dict(frame_width=FRAME_WIDTH, frame_height=FRAME_HEIGHT,
        safe_width=SAFE_WIDTH, safe_height=SAFE_HEIGHT,
        safe_bounds=[-FRAME_WIDTH/2+SAFE_X, FRAME_WIDTH/2-SAFE_X,
                     -FRAME_HEIGHT/2+SAFE_BOTTOM, FRAME_HEIGHT/2-SAFE_TOP],
        min_font_size=MIN_FONT_SIZE, preview_resolution=list(PREVIEW_RESOLUTION),
        final_resolution=list(FINAL_RESOLUTION), fps=FPS,
        rule='Same 9 x 16 scene coordinates, fonts, strokes, timing and camera in both renders; only pixel density changes. Final must be rerendered from approved source, never upscaled.')


def render_profile(config, final=False):
    return dict(config, resolution=list(FINAL_RESOLUTION if final else PREVIEW_RESOLUTION),
                fps=FPS, frame_height=FRAME_HEIGHT)
