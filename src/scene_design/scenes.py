"""Bước 2.2: khung hình tĩnh để duyệt bố cục, màu sắc và cỡ chữ.

Các số và điểm đồ thị dưới đây chỉ là dữ liệu minh họa cố định cho bố cục.
Chưa có mô hình toán, bộ điều khiển thời gian hoặc chuyển động.
"""

from manim import (
    DOWN, LEFT, RIGHT, UP, Arrow, Axes, Circle, DashedLine, Dot, Line,
    Polygon, RoundedRectangle, Scene, Text, VGroup, VMobject, config,
)

from config import (
    ACCENT, BACKGROUND, BODY_SIZE, BORDER, CURVE, FONT, FOREGROUND,
    FRAME_HEIGHT, FRAME_WIDTH, MUTED, PANEL, ROAD, SECTION_SIZE,
    TITLE_SIZE, VALUE_SIZE, VIDEO_HEIGHT, VIDEO_WIDTH,
)

config.pixel_width = VIDEO_WIDTH
config.pixel_height = VIDEO_HEIGHT
config.frame_width = FRAME_WIDTH
config.frame_height = FRAME_HEIGHT
config.background_color = BACKGROUND


def label(text, size=BODY_SIZE, color=FOREGROUND):
    return Text(text, font=FONT, font_size=size, color=color)


def place_left(mobject, x, y):
    """Đặt theo mép trái để các dòng chữ thẳng hàng."""
    return mobject.move_to([x + mobject.width / 2, y, 0])


def panel(width, height, center):
    return RoundedRectangle(
        width=width, height=height, corner_radius=0.18,
        fill_color=PANEL, fill_opacity=1, stroke_color=BORDER, stroke_width=1,
    ).move_to(center)


def make_car():
    body = RoundedRectangle(
        width=1.32, height=0.38, corner_radius=0.10,
        fill_color=ACCENT, fill_opacity=1, stroke_width=0,
    )
    cabin = Polygon(
        [-0.40, 0.17, 0], [-0.22, 0.48, 0],
        [0.25, 0.48, 0], [0.48, 0.17, 0],
        fill_color=ACCENT, fill_opacity=1, stroke_width=0,
    )
    window = Polygon(
        [-0.25, 0.22, 0], [-0.15, 0.39, 0],
        [0.18, 0.39, 0], [0.31, 0.22, 0],
        fill_color=BACKGROUND, fill_opacity=1, stroke_width=0,
    )
    wheels = VGroup(*[
        VGroup(
            Circle(radius=0.145, fill_color=BACKGROUND, fill_opacity=1,
                   stroke_color=MUTED, stroke_width=2),
            Circle(radius=0.055, fill_color=MUTED, fill_opacity=1, stroke_width=0),
        ).move_to([x, -0.19, 0])
        for x in (-0.40, 0.40)
    ])
    return VGroup(body, cabin, window, wheels)


class BoCucMau(Scene):
    """Ảnh mẫu 16:9; render bằng tùy chọn -s để lưu PNG."""

    def construct(self):
        self.add(place_left(label("TỪ CHUYỂN ĐỘNG ĐẾN ĐỒ THỊ", TITLE_SIZE), -6.42, 3.22))
        self.add(place_left(label("Quan sát vị trí của xe theo thời gian", 22, MUTED), -6.42, 2.64))

        self.add(panel(6.25, 4.25, [-3.38, 0.12, 0]))
        self.add(panel(6.25, 4.25, [3.38, 0.12, 0]))
        self.add(place_left(label("01   XE VÀ ĐƯỜNG", SECTION_SIZE), -6.12, 1.80))
        self.add(place_left(label("02   ĐỒ THỊ VỊ TRÍ – THỜI GIAN", 22), 0.63, 1.80))

        self.add(place_left(label("Xe chuyển động trên đường thẳng", 19, MUTED), -6.12, 1.20))
        road = RoundedRectangle(
            width=5.44, height=0.78, corner_radius=0.12,
            fill_color=ROAD, fill_opacity=1, stroke_width=0,
        ).move_to([-3.38, -0.12, 0])
        self.add(road)
        self.add(DashedLine([-6.00, -0.21, 0], [-0.76, -0.21, 0],
                            dash_length=0.22, color=MUTED, stroke_width=2))
        # Vị trí cố định để xem mẫu; không tính từ thời gian.
        self.add(make_car().shift([-4.62, 0.08, 0]))
        self.add(Arrow([-3.65, 0.51, 0], [-2.53, 0.51, 0], buff=0,
                       color=ACCENT, stroke_width=3, max_tip_length_to_length_ratio=0.15))
        self.add(label("Chiều chuyển động", 17, MUTED).move_to([-2.55, 0.92, 0]))

        self.add(Line([-5.92, -1.12, 0], [-0.86, -1.12, 0], color=MUTED, stroke_width=2))
        self.add(Line([-5.92, -1.02, 0], [-5.92, -1.22, 0], color=MUTED))
        self.add(Line([-4.62, -1.02, 0], [-4.62, -1.22, 0], color=ACCENT))
        self.add(label("0 m", 18, MUTED).move_to([-5.92, -1.49, 0]))
        self.add(label("4 m", 20, ACCENT).move_to([-4.62, -1.49, 0]))
        self.add(label("Vị trí s (m)", 18, MUTED).move_to([-1.75, -1.49, 0]))

        axes = Axes(
            x_range=[0, 4, 1], y_range=[0, 16, 4],
            x_length=4.74, y_length=2.35,
            axis_config={"color": MUTED, "stroke_width": 2, "include_tip": False},
        )
        axes.shift([1.10, -1.23, 0] - axes.c2p(0, 0))
        for value in (4, 8, 12, 16):
            self.add(Line(axes.c2p(0, value), axes.c2p(4, value),
                          color=BORDER, stroke_width=1))
            self.add(label(str(value), 16, MUTED).next_to(axes.c2p(0, value), LEFT, buff=0.14))
        for value in (0, 1, 2, 3, 4):
            self.add(label(str(value), 16, MUTED).next_to(axes.c2p(value, 0), DOWN, buff=0.14))
        self.add(axes)
        self.add(place_left(label("Vị trí s (m)", 18, MUTED), 0.82, 1.43))
        self.add(label("Thời gian t (s)", 18, MUTED).move_to([3.48, -1.80, 0]))

        # Đường cong minh họa bằng các điểm cố định; mô hình ở bước 2.3.
        curve = VMobject(color=CURVE, stroke_width=4).set_points_smoothly([
            axes.c2p(0, 0), axes.c2p(1, 1), axes.c2p(2, 4),
            axes.c2p(3, 9), axes.c2p(4, 16),
        ])
        point = axes.c2p(2, 4)
        self.add(curve)
        self.add(DashedLine(axes.c2p(2, 0), point, color=ACCENT, stroke_width=2, dash_length=0.07))
        self.add(DashedLine(axes.c2p(0, 4), point, color=ACCENT, stroke_width=2, dash_length=0.07))
        self.add(Dot(point, radius=0.17, color=ACCENT, fill_opacity=0.15))
        self.add(Dot(point, radius=0.075, color=ACCENT))
        self.add(label("(2 s; 4 m)", 19, ACCENT).next_to(point, UP + LEFT, buff=0.20))

        for x, title, value, color in [
            (-4.48, "THỜI GIAN", "t = 2 s", FOREGROUND),
            (0, "VỊ TRÍ", "s = 4 m", ACCENT),
            (4.48, "VẬN TỐC", "v = 4 m/s", FOREGROUND),
        ]:
            self.add(panel(4.12, 1.08, [x, -2.85, 0]))
            self.add(label(title, 17, MUTED).move_to([x, -2.57, 0]))
            self.add(label(value, VALUE_SIZE, color).move_to([x, -3.02, 0]))

        self.add(label("BƯỚC 2.2  •  BẢN MẪU BỐ CỤC  •  SỐ LIỆU MINH HỌA", 15, MUTED)
                 .move_to([0, -3.69, 0]))
