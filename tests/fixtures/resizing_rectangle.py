"""A second input scene with different content and animation."""

from manim import BLUE, Rectangle, Scene, linear


class ResizingRectangle(Scene):
    def construct(self):
        rectangle = Rectangle(width=1, height=1, color=BLUE, fill_opacity=0.6)
        self.add(rectangle)
        self.play(rectangle.animate.stretch(4, dim=0), run_time=1, rate_func=linear)
        self.wait(0.2)
