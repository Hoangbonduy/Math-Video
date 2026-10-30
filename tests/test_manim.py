from manim import *


class TestMoiTruong(Scene):
    def construct(self):
        title = Text(
            "Đạo hàm giúp ta hiểu sự thay đổi",
            font_size=34,
        ).to_edge(UP)

        ball = Dot(LEFT * 4, radius=0.18, color=YELLOW)
        road = Line(LEFT * 5, RIGHT * 5).shift(DOWN * 0.2)

        self.play(Write(title))
        self.play(Create(road), FadeIn(ball))
        self.play(
            ball.animate.shift(RIGHT * 8),
            run_time=3,
            rate_func=linear,
        )
        self.wait()


class TestCongThuc(Scene):
    def construct(self):
        formula = MathTex(r"s(t)=t^2", r"\qquad", r"v(t)=2t")
        self.play(Write(formula))
        self.wait(2)