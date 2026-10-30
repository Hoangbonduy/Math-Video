"""Compile validated design JSON into a fixed Manim preview program (no AI)."""
from src.scene_design.design import validate_design, validate_code

VERSION = 1

# Only data literals are inserted above this trusted program. JSON never becomes
# executable expressions; mathematical curves use the design's sampled points.
PROGRAM = r"""
class GeneratedScene(Scene):
    def construct(self):
        self.camera.background_color = STYLE['background']
        frame_width = 8 * STYLE['render']['resolution'][0] / STYLE['render']['resolution'][1]
        frame_height = 8
        clock = ValueTracker(0)
        self.add(clock)

        def fit_text(text, width, height):
            factor = min(1, width / max(text.width, 1e-6), height / max(text.height, 1e-6))
            text.scale(factor)
            return text

        def make_object(spec):
            width, height = spec['width'] * frame_width, spec['height'] * frame_height
            center = np.array([(spec['x'] - .5) * frame_width, (.5 - spec['y']) * frame_height, 0])
            color = spec['color']
            kind = spec['kind']
            if kind == 'text':
                item = fit_text(Text(spec['label'], font=STYLE['font'], font_size=26, color=color), width, height)
            elif kind == 'rectangle':
                item = Rectangle(width=width, height=height, color=color, fill_opacity=.15)
            elif kind == 'circle':
                item = Ellipse(width=width, height=height, color=color, fill_opacity=.15)
            elif kind == 'line':
                item = Line([-width/2, height/2, 0], [width/2, -height/2, 0], color=color)
            elif kind == 'plot':
                points = [[(x-.5)*width, (y-.5)*height, 0] for x,y in spec['points']]
                item = VMobject(color=color).set_points_as_corners(points)
                # Coordinates refer to the declared box, not the curve's bounds.
                item.shift(center)
            else:
                item = IMAGES[spec['id']]
                item.scale(min(width / max(item.width, 1e-6), height / max(item.height, 1e-6)))
            if kind != 'plot':
                item.move_to(center)
            if kind != 'text' and spec['label']:
                title = fit_text(Text(spec['label'], font=STYLE['font'], font_size=20, color=color),
                                 max(width*.9, .1), max(height*.45, .2))
                title.move_to(center)
                item = Group(item, title)
            return item

        def attach_timeline(item, beats):
            origin = item.get_center().copy()
            strokes = [(part, part.get_fill_opacity(), part.get_stroke_opacity())
                       for part in item.family_members_with_points() if isinstance(part, VMobject)]
            pictures = [(part, part.pixel_array[:, :, 3].copy()) for part in item.get_family() if isinstance(part, ImageMobject)]
            previous = {'scale': 1, 'opacity': None}
            hidden = any(beat['action'] == 'appear' for beat in beats)

            def update(mob, dt):
                t = clock.get_value()
                dx, dy, scale = 0, 0, 1
                opacity = 0 if hidden else 1
                for beat in beats:
                    if t < beat['start']:
                        continue
                    progress = min(1, max(0, (t - beat['start']) / beat['duration']))
                    if beat['action'] == 'move':
                        dx += beat['dx'] * progress
                        dy += beat['dy'] * progress
                    elif beat['action'] == 'appear':
                        opacity = progress
                    elif beat['action'] == 'fade':
                        opacity = 1-progress
                    elif beat['action'] == 'highlight':
                        scale *= 1 + .12 * math.sin(math.pi * progress)
                mob.scale(scale / previous['scale'])
                previous['scale'] = scale
                mob.move_to(origin + np.array([dx * frame_width, -dy * frame_height, 0]))
                if opacity != previous['opacity']:
                    for part, fill, stroke in strokes:
                        part.set_fill(opacity=fill * opacity)
                        part.set_stroke(opacity=stroke * opacity)
                    for picture, alpha in pictures:
                        picture.pixel_array[:, :, 3] = (alpha * opacity).astype('uint8')
                    previous['opacity'] = opacity
            update(item, 0)
            item.add_updater(update)

        for spec in DESIGN['objects']:
            item = make_object(spec)
            beats = sorted([beat for beat in DESIGN['timeline'] if beat['object_id'] == spec['id']],
                           key=lambda beat: beat['start'])
            attach_timeline(item, beats)
            self.add(item)
        self.play(clock.animate.set_value(DESIGN['duration']), run_time=RUN_SECONDS, rate_func=linear)
"""


def source_for(design, assets, style):
    """Return reproducible Python for the supported object/timeline contract."""
    validate_design(design,dict(scene_id=design['scene_id'],narration=design['narration']),design['duration'],assets)
    paths={asset['asset_id']:asset['path'] for asset in assets}
    images=', '.join(repr(obj['id'])+': ImageMobject('+repr(paths[obj['asset_id']])+')'
                     for obj in design['objects'] if obj['kind']=='image')
    source=('from manim import *\nimport math\nimport numpy as np\n\n'
            +'DESIGN = '+repr(design)+'\nSTYLE = '+repr(style)+'\nIMAGES = {'+images+'}\n'
            +PROGRAM.replace('RUN_SECONDS',repr(design['duration'])))
    return validate_code(source,assets)
