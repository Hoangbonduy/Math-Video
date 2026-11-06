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


REFERENCE_VERSION = 3
COMPONENTS = ['vn_text','fit_to_safe_area','formula_lines','track',
    'make_panel','make_metric_card','make_car','make_road','make_axes_with_grid','finish_scene']


def parse_api_name(name):
    """Resolve public API spelling to a fixed supported namespace, never import model paths."""
    if not isinstance(name,str) or not name or len(name)>160:
        raise WorkflowError('INVALID_SCENE_REFERENCE',f'Invalid API name: {name!r}. Use a public Manim or manim_kit API.')
    namespace=None
    for prefix,module in [('src.scene_design.manim_kit.','manim_kit'),('manim_kit.','manim_kit'),('manim.','manim')]:
        if name.startswith(prefix):namespace=module;name=name[len(prefix):];break
    parts=name.split('.')
    if not all(re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*',part) for part in parts) or len(parts)>3:
        raise WorkflowError('INVALID_SCENE_REFERENCE',f'Invalid public API path: {name!r}. '
            'Accepted examples: VGroup, Axes.plot, manim.Axes.plot, manim_kit.vn_text, src.scene_design.manim_kit.vn_text.')
    if parts[0] in ('src','os','sys','numpy','math'):
        raise WorkflowError('INVALID_SCENE_REFERENCE',f'Unsupported API namespace in {name!r}; use manim or manim_kit.')
    if namespace is None:namespace='auto'
    return namespace,parts


def validate_request(api_names,recipe_ids):
    if not isinstance(api_names,list) or not 1<=len(api_names)<=16:
        count=len(api_names) if isinstance(api_names,list) else 'not a list'
        raise WorkflowError('INVALID_SCENE_REFERENCE',f'Expected 1..16 API names; received {count}. Keep only APIs needed by the visual plan.')
    for name in api_names:parse_api_name(name)
    if not isinstance(recipe_ids,list) or any(name not in RECIPES for name in recipe_ids):
        raise WorkflowError('INVALID_SCENE_REFERENCE','Unknown Manim recipe; select recipe_ids from system_capabilities.recipes.')


def _inspect(api_names):
    program = """import inspect,json,sys,manim
sys.path.insert(0,sys.argv[2])
from src.scene_design import manim_kit
result={}
for name,namespace,parts in json.loads(sys.argv[1]):
    module=manim_kit if namespace=='manim_kit' or (namespace=='auto' and parts[0] in manim_kit.__all__) else manim
    value=module
    try:
        for part in parts: value=getattr(value,part)
        try:
            sig=inspect.signature(value)
            signature=str(sig)
            keywords=[key for key,p in sig.parameters.items() if p.kind in (p.POSITIONAL_OR_KEYWORD,p.KEYWORD_ONLY)]
            kwargs=any(p.kind==p.VAR_KEYWORD for p in sig.parameters.values())
        except (TypeError,ValueError): signature='';keywords=[];kwargs=True
        result[name]=dict(available=True,module=module.__name__,signature=signature,keywords=keywords,
            accepts_kwargs=kwargs,doc=(inspect.getdoc(value) or '')[:1800])
    except AttributeError: result[name]=dict(available=False)
print(json.dumps(result))
"""
    result=subprocess.run([manim_runtime.python_executable(),'-c',program,json.dumps([(name,*parse_api_name(name)) for name in api_names]),
        str(Path(__file__).resolve().parents[2])],capture_output=True,text=True,timeout=20)
    if result.returncode:
        raise WorkflowError('DEPENDENCY_ERROR','Cannot inspect installed scene APIs: '+result.stderr[-500:])
    return json.loads(result.stdout.strip().splitlines()[-1])


def capabilities():
    return dict(runtime=manim_runtime.info(), frame_contract=scene_theme.profile(),
        recipes=RECIPES, components=COMPONENTS,component_apis=_inspect(COMPONENTS),
        imports=['manim','numpy','math','manim_kit','src.scene_design.manim_kit'],
        reference_rule='Installed API signatures are authoritative. Helpers belong to manim_kit. '
        'Use vn_text(content, size=28); font_size is a compatibility alias. '
        'Keep effective font size at least MIN_FONT_SIZE after scaling. '
        'Local reference version may differ; do not assume newer APIs exist.')


def lookup(api_names, recipe_ids):
    validate_request(api_names,recipe_ids)
    inspected=_inspect(list(dict.fromkeys(api_names+COMPONENTS)))
    signatures={name:inspected[name] for name in api_names}
    root=Path(__file__).resolve().parents[2]/'references'
    docs=[]
    # Paths are selected from our catalog, never constructed from model paths.
    wanted={parse_api_name(name)[1][0] for name in api_names if signatures[name].get('module')=='manim'}
    for path in sorted(root.glob('manim-*')):
        for doc in sorted(path.rglob('*.md')):
            if doc.stem in wanted and len(docs)<12:
                docs.append(dict(path=str(doc.relative_to(root)), version=path.name,
                                 content=doc.read_text(encoding='utf-8')[:6000]))
    return dict(schema_version=REFERENCE_VERSION,installed_apis=signatures,
                component_apis={name:inspected[name] for name in COMPONENTS}, recipes={name:RECIPES[name] for name in recipe_ids},
                references=docs, missing=[name for name,value in signatures.items() if not value['available']])
