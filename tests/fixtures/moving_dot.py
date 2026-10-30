"""Minimal render input; independent of the old layout example."""

from manim import Dot, LEFT, RIGHT, Scene, linear


class MovingDot(Scene):
    def construct(self):
        dot = Dot(LEFT * 2)
        self.add(dot)
        self.play(dot.animate.move_to(RIGHT * 2), run_time=1, rate_func=linear)
        self.wait(0.2)
