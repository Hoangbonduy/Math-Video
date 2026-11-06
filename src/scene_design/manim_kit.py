"""Standardized Manim typography, UI, axes and visual prefabs."""
import numpy as np
from manim import (
    DOWN, LEFT, Circle, DashedLine, Line, Polygon, RoundedRectangle, Text, VGroup, Axes,
)

BACKGROUND = "#0D1726"
FOREGROUND = "#F2F6FC"
ACCENT = CURVE = "#56DFC5"
MUTED = "#8A99AD"
PANEL = "#132238"
BORDER = "#23395B"
ROAD = "#1C2E4A"
WARNING = "#F59E0B"
HIGHLIGHT_COLOR = "#FACC15"
FONT = "DejaVu Sans"
from src.scene_design.scene_theme import MIN_FONT_SIZE

TITLE_SIZE, SECTION_SIZE, BODY_SIZE, VALUE_SIZE = 32, 22, 28, 26

__all__ = [
    "BACKGROUND", "FOREGROUND", "ACCENT", "CURVE", "MUTED", "PANEL", "BORDER",
    "ROAD", "WARNING", "HIGHLIGHT_COLOR", "FONT", "TITLE_SIZE", "SECTION_SIZE", "BODY_SIZE", "VALUE_SIZE",
    "fit_to_safe_area", "formula_lines", "track", "FRAME_WIDTH", "FRAME_HEIGHT",
    "SAFE_WIDTH", "SAFE_HEIGHT", "MIN_FONT_SIZE", "vn_text", "place_left", "make_panel", "make_metric_card", "make_car",
    "make_road", "make_radar_officer", "make_axes_with_grid", "finish_scene",
]


def vn_text(content, size=None, color=FOREGROUND, max_width=None, max_height=None, *, font_size=None):
    """Vietnamese Text; size is canonical, font_size is a compatibility alias.

    Default text meets MIN_FONT_SIZE. Explicit scaling still needs layout checks.
    """
    if size is not None and font_size is not None and size != font_size:
        raise ValueError('vn_text: size and font_size disagree; use size=28')
    size = size if size is not None else font_size if font_size is not None else BODY_SIZE
    mob = Text(str(content), font=FONT, font_size=size, color=color)
    if max_width and mob.width > max_width:
        mob.scale(max_width / mob.width)
    if max_height and mob.height > max_height:
        mob.scale(max_height / mob.height)
    return mob


def place_left(mobject, x, y):
    return mobject.move_to([x + mobject.width / 2, y, 0])


def make_panel(width, height, center, title=None, subtitle=None):
    box = RoundedRectangle(
        width=width, height=height, corner_radius=min(0.18, width/2, height/2),
        fill_color=PANEL, fill_opacity=1, stroke_color=BORDER, stroke_width=1.5,
    ).move_to(center)
    parts = [box]
    left, top = center[0] - width/2 + 0.30, center[1] + height/2 - 0.38
    if title:
        parts.append(place_left(vn_text(title, SECTION_SIZE, max_width=width-0.6), left, top))
    if subtitle:
        parts.append(place_left(vn_text(subtitle, MIN_FONT_SIZE, MUTED, max_width=width-0.6), left, top-0.52))
    return VGroup(*parts)


def make_metric_card(x, y, title, value_text, value_color=FOREGROUND, width=4.12, height=1.08):
    bg = RoundedRectangle(
        width=width, height=height, corner_radius=0.16,
        fill_color=PANEL, fill_opacity=1, stroke_color=BORDER, stroke_width=1.2,
    ).move_to([x, y, 0])
    label = vn_text(title, MIN_FONT_SIZE, MUTED, max_width=width-0.4).move_to([x, y+0.26, 0])
    value = vn_text(value_text, VALUE_SIZE, value_color, max_width=width-0.4).move_to([x, y-0.18, 0])
    return VGroup(bg, label, value)


def make_car(color=ACCENT, scale_factor=1.0):
    body = RoundedRectangle(width=1.32, height=0.38, corner_radius=0.10,
                            fill_color=color, fill_opacity=1, stroke_width=0)
    cabin = Polygon([-0.40,0.17,0], [-0.22,0.48,0], [0.25,0.48,0], [0.48,0.17,0],
                    fill_color=color, fill_opacity=1, stroke_width=0)
    window = Polygon([-0.25,0.22,0], [-0.15,0.39,0], [0.18,0.39,0], [0.31,0.22,0],
                     fill_color=BACKGROUND, fill_opacity=1, stroke_width=0)
    wheels = VGroup(*[
        VGroup(Circle(radius=0.145, fill_color=BACKGROUND, fill_opacity=1,
                      stroke_color=MUTED, stroke_width=2),
               Circle(radius=0.055, fill_color=MUTED, fill_opacity=1, stroke_width=0)
        ).move_to([x,-0.19,0]) for x in (-0.40,0.40)
    ])
    return VGroup(body, cabin, window, wheels).scale(scale_factor)


def make_road(center=(-3.38,-0.12,0), width=5.44, height=0.78):
    cx, cy = center[:2]
    surface = RoundedRectangle(width=width, height=height, corner_radius=min(0.12,width/2,height/2),
                               fill_color=ROAD, fill_opacity=1, stroke_width=0).move_to(center)
    lane = DashedLine([cx-width*0.48,cy-0.08,0], [cx+width*0.48,cy-0.08,0],
                      dash_length=0.22, color=MUTED, stroke_width=2)
    return VGroup(surface, lane)


def make_radar_officer(color=FOREGROUND, accent=ACCENT, scale_factor=1.0):
    head = Circle(radius=0.16, fill_color=PANEL, fill_opacity=1,
                  stroke_color=color, stroke_width=2).move_to([0,0.62,0])
    torso = RoundedRectangle(width=0.42, height=0.62, corner_radius=0.08,
                             fill_color=PANEL, fill_opacity=1,
                             stroke_color=color, stroke_width=2).move_to([0,0.12,0])
    legs = VGroup(Line([-0.10,-0.19,0],[-0.16,-0.62,0],color=color,stroke_width=2.5),
                  Line([0.10,-0.19,0],[0.16,-0.62,0],color=color,stroke_width=2.5))
    arm = Line([0.08,0.26,0],[0.42,0.22,0],color=color,stroke_width=2.5)
    gun = RoundedRectangle(width=0.34,height=0.16,corner_radius=0.04,
                           fill_color=accent,fill_opacity=0.9,
                           stroke_color=color,stroke_width=1.5).move_to([0.52,0.24,0])
    return VGroup(legs,torso,head,arm,gun).scale(scale_factor)


def make_axes_with_grid(x_range, y_range, x_length=4.74, y_length=2.35,
                        origin_pos=(1.10,-1.23,0), x_ticks=(), y_ticks=()):
    axes = Axes(x_range=list(x_range), y_range=list(y_range),
                x_length=x_length, y_length=y_length,
                axis_config={"color":MUTED,"stroke_width":2,"include_tip":False,"include_numbers":False})
    axes.shift(np.array([origin_pos[0],origin_pos[1],0]) - axes.c2p(x_range[0],y_range[0]))
    grid = VGroup()
    for y in y_ticks:
        grid.add(Line(axes.c2p(x_range[0],y),axes.c2p(x_range[1],y),color=BORDER,stroke_width=1))
        grid.add(vn_text(str(y),MIN_FONT_SIZE,MUTED).next_to(axes.c2p(x_range[0],y),LEFT,buff=0.14))
    for x in x_ticks:
        grid.add(vn_text(str(x),MIN_FONT_SIZE,MUTED).next_to(axes.c2p(x,y_range[0]),DOWN,buff=0.14))
    return axes, grid


def finish_scene(scene, target_duration):
    """Fill a positive remainder without issuing a zero-duration wait."""
    remaining = float(target_duration) - float(scene.time)
    if remaining > 1e-3:
        scene.wait(remaining)


# Scene coordinates remain identical at 360x640 and 1080x1920.
from src.scene_design.scene_theme import (
    FRAME_WIDTH, FRAME_HEIGHT, SAFE_WIDTH, SAFE_HEIGHT, MIN_FONT_SIZE,
)


def fit_to_safe_area(mobject, max_width=SAFE_WIDTH, max_height=SAFE_HEIGHT,
                     min_scale=0.65):
    """Shrink only within the readability limit; a crowded scene needs replanning."""
    factor = min(1.0, max_width/max(mobject.width, 1e-8),
                 max_height/max(mobject.height, 1e-8))
    if factor < min_scale:
        raise ValueError('LAYOUT_REPLAN_REQUIRED: content cannot fit legibly; wrap or split the visual plan')
    return mobject.scale(factor)


def formula_lines(*lines, size=28, color=FOREGROUND, buff=0.28):
    """Use explicit semantic line breaks without requiring a LaTeX installation."""
    group = VGroup(*(vn_text(line, size, color) for line in lines)).arrange(DOWN, buff=buff)
    return fit_to_safe_area(group, min_scale=MIN_FONT_SIZE/size)


def track(scene, object_id, mobject, role='shape', overlap_with=(), *, layout_role=None, overflow_windows=()):
    """Name semantic render objects for actual frame geometry inspection.

    Track labels separately from panels/axes. Declare only intentional overlap.
    """
    if not hasattr(scene, 'layout_registry'):
        scene.layout_registry = {}
    if object_id in scene.layout_registry:
        raise ValueError('Duplicate tracked object: '+object_id)
    from src.shared.system_contract import TRACK_ROLES, LAYOUT_ROLES
    if role not in TRACK_ROLES:
        raise ValueError('Unknown tracked object role')
    plan=getattr(scene,'contract_plan',{})
    declared=next((o for o in plan.get('objects',[]) if o['id']==object_id),{})
    if plan.get('objects') and not declared and overflow_windows:
        raise ValueError('SC.LAYOUT: unplanned object cannot claim overflow: '+object_id)
    if declared:
        if layout_role is not None and declared.get('layout_role') and layout_role!=declared['layout_role']:
            raise ValueError('SC.LAYOUT: layout role differs from plan: '+object_id)
        if role!=declared['role']:raise ValueError('SC.LAYOUT: tracked role differs from plan: '+object_id)
        layout_role=declared.get('layout_role',layout_role)
        overflow_windows=[w for w in plan.get('constraints',{}).get('overflow_windows',[]) if w['object_id']==object_id]
    if layout_role is not None and layout_role not in LAYOUT_ROLES:raise ValueError('Unknown layout role')
    scene.layout_registry[object_id] = dict(mobject=mobject, role=role,
        overlap_with=list(overlap_with),layout_role=layout_role,overflow_windows=list(overflow_windows),enforce_hud=bool(layout_role))
    return mobject
