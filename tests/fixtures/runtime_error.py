from manim import Scene


class BrokenScene(Scene):
    def construct(self):
        raise RuntimeError("intentional scene failure")
