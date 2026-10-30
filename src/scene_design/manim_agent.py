"""Manim coding agent: generate, validate, render, inspect errors and repair durably."""
from pathlib import Path
from src.shared import _workflow_store as store
from src.shared._workflow_store import WorkflowError
from src.idea.content import parse_object
from src.scene_design.design import CODE_SCHEMA, schema_check, validate_code
from src.shared.openrouter_client import response_text, redact


def render_candidate(root,directory,session,context):
    from src.scene_design.scene_generation import _messages, _clip_version, RENDERER
    job=session.job
    design=context['approved_design']
    assets=context['assets']['assets']
    from src.scene_design.manim_runtime import info as runtime_info
    runtime=runtime_info()
    width,height=context['config']['render']['resolution']
    context=dict(context,manim_runtime=runtime,frame_width=8*width/height,frame_height=8)
    instruction=(
        'You are a Manim animation programmer creating an educational video, not a static slide. '
        'Write runnable Python for the installed Manim Community version in manim_runtime, Cairo renderer. '
        'Return exactly class GeneratedScene(Scene), with construct. Only import manim, math, numpy, manim_kit or src.scene_design.manim_kit. '
        'You may import helper prefabs from src.scene_design.manim_kit: '
        'vn_text, place_left, make_panel, make_metric_card, make_car, make_road, '
        'make_radar_officer, make_axes_with_grid, finish_scene, '
        'BACKGROUND, FOREGROUND, ACCENT, MUTED, PANEL, BORDER, ROAD, FONT. '
        'Prefer make_panel, make_car, make_road, make_axes_with_grid and vn_text '
        'for a polished, consistent visual style matching scenes.py. '
        'Call finish_scene(self, duration) at the end of construct(). '
        'Use the approved script visual_intent and the design purpose to explain the concept through coherent moving geometry. '
        'Implement all important objects, labels, mathematical data and animation beats in the design. '
        'Use real Manim Axes, plot, ValueTracker, always_redraw, Create, Transform and updaters where appropriate. '
        'A moving point must stay on its mathematical curve; secants/tangents and numeric values must update from the same data. '
        'Do not approximate a curve-following motion by a straight shift. Avoid decorative unrelated graphics. '
        'Keep narration EXACTLY; do not add sound, narration text walls or watermarks. '
        'Keep labels readable throughout the full motion path; moving geometry must not pass through unrelated text or numeric readouts. '
        'Honor design.duration exactly. Budget animation run_times explicitly. '
        'After animations compute remaining=duration-self.time; call self.wait(remaining) ONLY if remaining>1e-6. '
        'Never call self.wait(0), self.wait with a nonpositive duration, or self.wait(max(0, duration-self.time)); Manim rejects zero-duration waits. '
        'No frame may be blank throughout the explanation. Use a short Vietnamese title and legible labels. '
        'Use Text(font="DejaVu Sans") for all Vietnamese and formulas; avoid Tex/MathTex/DecimalNumber dependencies. '
        'For dynamic numbers use always_redraw(lambda: Text(...)) or DecimalNumber with mob_class=Text. '
        'Axes must have include_numbers=False unless numbers use Text. '
        'Embed style and duration values as Python literals; do not use an undefined variable named config. Set self.camera.background_color to the supplied background color. The frame size is frame_width by frame_height. '
        'Fit geometry and text within a 0.4-unit safe margin, at least 0.65 units at the bottom for player controls. Titles use font_size 28..36; labels 20..26. Scale text DOWN only if it exceeds the allowed width; never enlarge a title to fill the frame. '
        'Normalized design positions use centers, y DOWN; convert to centered Manim coordinates. '
        'When an object is a line, width/height may be zero. Manim line endpoints must still differ. '
        'Use literal listed assets only. ImageMobject is not a VMobject: use Group, not VGroup for images. '
        'No filesystem, network, subprocess, environment access, introspection, global config changes or external libraries. '
        'If implementation feedback needs a different design set requires_design_change=true; otherwise false. '
        'During initial design rendering you may elaborate the described motion using the script visual_intent while preserving content and timing.'
    )
    messages=_messages(instruction,context)
    error=None
    for attempt in range(3):
        phase='code_'+str(attempt)
        job['progress']='writing_code' if attempt==0 else 'repairing_code';session.save()
        response=session.call(phase,messages,schema=CODE_SCHEMA)
        result=dict(source='')
        try:
            result=parse_object(response_text(response)); schema_check(result,CODE_SCHEMA)
            if result['requires_design_change']:
                raise WorkflowError('DESIGN_CHANGE_REQUIRED',result['reason'] or 'Feedback requires a new design; C5 does not reopen approved designs')
            source=validate_code(result['source'],assets)
            saved_render=job.get('renders',{}).get(phase)
            if saved_render and saved_render['status']=='failed':
                raise WorkflowError(saved_render['error_code'],saved_render['error'])
            if phase not in job.setdefault('renders',{}):
                version=_clip_version(root,context['scene']['scene_id'])
                job['renders'][phase]=dict(version=version,status='planned'); session.save()
            render=job['renders'][phase]
            saved_metadata=root/f"logs/{context['scene']['scene_id']}_{render['version']}.json"
            completed_on_disk=saved_metadata.exists() and store.read_json(saved_metadata).get('status')=='success'
            if render['status']=='running' and not completed_on_disk:
                job.setdefault('interrupted_renders',[]).append(dict(render))
                render=dict(version=_clip_version(root,context['scene']['scene_id']),status='planned')
                job['renders'][phase]=render; session.save()
            version=render['version']
            relative=f"scenes/{context['scene']['scene_id']}_{version}.py"
            store.materialize(root,relative,content=source.encode())
            metadata=root/f"logs/{context['scene']['scene_id']}_{version}.json"
            if render['status']=='success':
                output=render['output']
                if store.digest(Path(output['video_path']))!=render['sha256']: raise WorkflowError('ARTIFACT_CHANGED','Cached clip changed')
            elif metadata.exists():
                cached=store.read_json(metadata)
                if cached['status']=='success':
                    output={k:cached[k] for k in ('status','scene_id','version','video_path','log_path','error')}
                elif cached['status']=='running':
                    raise WorkflowError('RENDER_FAILED','Interrupted render; preserved old attempt')
                else: raise WorkflowError('RENDER_FAILED',cached.get('error') or 'Saved render failed')
            else:
                if render['status']=='running':
                    raise WorkflowError('RENDER_INTERRUPTED','Render was interrupted; use --retry-render')
                render['status']='running'; job['progress']='rendering_manim'; session.save()
                output=RENDERER(root,context['scene']['scene_id'],version,relative,'GeneratedScene',dict(context['config']['render'],target_duration=design['duration']))
            if output['status']!='success':
                if (output.get('error') or '').startswith('DEPENDENCY_ERROR'):
                    raise WorkflowError('DEPENDENCY_ERROR',output['error'])
                raise WorkflowError('RENDER_FAILED',output['error'])
            measured=store.read_json(Path(output['log_path']).with_suffix('.json'))['video']['duration_seconds']
            if abs(measured-design['duration'])>2/context['config']['render']['fps']+1e-6:
                raise WorkflowError('SCENE_DURATION_MISMATCH',f'Actual {measured}s vs approved {design["duration"]}s')
            job['progress']='ready_for_review'
            render.update(status='success',output=output,sha256=store.digest(Path(output['video_path']))); session.save()
            return Path(output['video_path']),dict(clip_version=version,code_path=relative)
        except WorkflowError as exc:
            if exc.code in ('RENDER_INTERRUPTED','ARTIFACT_CHANGED','DEPENDENCY_ERROR','DESIGN_CHANGE_REQUIRED','MODEL_MISMATCH'): raise
            if phase in job.get('renders',{}):
                job['renders'][phase].update(status='failed',error_code=exc.code,error=exc.message)
                session.save()
            error=str(exc)
            store.atomic_write(directory/f'code_error_{attempt}.json',dict(error=error))
            if attempt==2: raise
            log=''
            if phase in job.get('renders',{}):
                p=root/f"logs/{context['scene']['scene_id']}_{job['renders'][phase]['version']}.log"
                if p.exists(): log=redact(p.read_text(errors='replace')[-12000:])
            messages += [dict(role='assistant',content=result.get('source','') if isinstance(result.get('source',''),str) else ''),dict(role='user',content='Fix implementation only. Error: '+error+'\nLog: '+log)]
    raise WorkflowError('RENDER_FAILED',error)

