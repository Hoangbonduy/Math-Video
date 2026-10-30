"""Scenes overriding render to simulate missing or corrupt renderer output."""

from manim import Scene, config


class MissingOutput(Scene):
    def render(self):
        pass


class CorruptOutput(Scene):
    def render(self):
        directory = config.get_dir("video_dir")
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"{config.output_file}.mp4").write_bytes(b"not a video")
