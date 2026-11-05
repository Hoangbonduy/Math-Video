"""Small recipe catalog plus targeted, version-labelled local API references."""
import json
import re
import subprocess
from pathlib import Path
from src.scene_design import manim_runtime, scene_theme
from src.shared._workflow_store import WorkflowError

RECIPES = {
    'parameter_geometry': 'ValueTracker + always_redraw: derive curve points, secant/tangent and readout from the same function and tracker; animate only the tracker.',
    'group_motion': 'VGroup/Group: assemble semantic parts, then animate the group. Track labels separately. Avoid independent paths for attached parts.',
    'comparison': 'Stack two compact panels vertically in the portrait safe area, using matching scales. Reveal the comparison with a shared parameter.',
    'formula_steps': 'formula_lines with explicit semantic breaks; TransformMatchingShapes or ReplacementTransform between steps. Never shrink below MIN_FONT_SIZE.',
    'explanatory_motion': 'Show a concrete anchor at t=0; start a meaningful Create/Transform/parameter change before 0.8s; budget each play and wait against target_seconds.',
}


def capabilities():
    return dict(runtime=manim_runtime.info(), frame_contract=scene_theme.profile(),
        recipes=RECIPES, components=['vn_text','fit_to_safe_area','formula_lines','track',
        'make_panel','make_metric_card','make_car','make_road','make_axes_with_grid','finish_scene'],
        imports=['manim','numpy','math','manim_kit','src.scene_design.manim_kit'],
        reference_rule='Installed API signatures are authoritative. Local reference version may differ; do not assume newer APIs exist. No filesystem/network/global config access in scene code.')


def lookup(api_names, recipe_ids):
    if len(api_names)>16 or any(not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)?', n) for n in api_names):
        raise WorkflowError('INVALID_SCENE_DESIGN','Use at most 16 explicit public API names')
    if any(name not in RECIPES for name in recipe_ids):
        raise WorkflowError('INVALID_SCENE_DESIGN','Unknown Manim recipe')
    program = """import inspect,json,sys,manim
result={}
for name in json.loads(sys.argv[1]):
    value=manim
    try:
        for part in name.split('.'): value=getattr(value,part)
        try: signature=str(inspect.signature(value))
        except (TypeError,ValueError): signature=''
        result[name]=dict(available=True,signature=signature,doc=(inspect.getdoc(value) or '')[:1800])
    except AttributeError: result[name]=dict(available=False)
print(json.dumps(result))
"""
    result=subprocess.run([manim_runtime.python_executable(),'-c',program,json.dumps(api_names)],
                          capture_output=True,text=True,timeout=20)
    if result.returncode:
        raise WorkflowError('DEPENDENCY_ERROR','Cannot inspect installed Manim APIs: '+result.stderr[-500:])
    signatures=json.loads(result.stdout.strip().splitlines()[-1])
    root=Path(__file__).resolve().parents[2]/'references'
    docs=[]
    # Paths are selected from our catalog, never constructed from model paths.
    wanted={name.split('.')[0] for name in api_names}
    for path in sorted(root.glob('manim-*')):
        for doc in sorted(path.rglob('*.md')):
            if doc.stem in wanted and len(docs)<12:
                docs.append(dict(path=str(doc.relative_to(root)), version=path.name,
                                 content=doc.read_text(encoding='utf-8')[:6000]))
    return dict(installed_apis=signatures, recipes={name:RECIPES[name] for name in recipe_ids},
                references=docs, missing=[name for name,value in signatures.items() if not value['available']])
