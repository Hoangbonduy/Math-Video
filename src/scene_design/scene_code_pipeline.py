"""Script + style + capabilities -> visual plan -> direct Manim -> frame review.

V3 keeps JSON as a review manifest, never as a fixed geometry language.
Old v2 rounds remain readable and resumable through scene_pipeline.
"""
import copy
import ast
import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
from PIL import Image, ImageDraw, ImageFont
from src.idea.content import _object, parse_object
from src.shared import _workflow_store as store
from src.shared._workflow_store import WorkflowError
from src.shared.media_tools import binary, run
from src.shared.openrouter_client import response_text, redact
from src.scene_design import scene_contract as contract, scene_references, scene_theme
from src.scene_design import scene_pipeline as legacy, scene_vision
from src.scene_design.design import CODE_SCHEMA, validate_code
from src.scene_design.scene_telemetry import select_frames

VERSION = 3
PLAN_SCHEMA = copy.deepcopy(contract.STORY_SCHEMA)
PLAN_SCHEMA['properties']['beats']['items']['properties']['required_capabilities'] = contract.array(contract.S, 0, 16)
PLAN_SCHEMA['properties'].update(
    objects=contract.array(_object(dict(id=contract.IDENTIFIER,
        role=contract.enum('text','formula','shape','background'))),1,60),
    recipe_ids=contract.array(contract.enum(*scene_references.RECIPES),1,5),
    required_apis=contract.array(contract.S,1,16))
PLAN_SCHEMA['required'] += ['objects','recipe_ids','required_apis']


def _progress(session, phase, index):
    from src.shared.task_control import checkpoint
    checkpoint()
    session.job.update(progress=phase, design_pipeline=VERSION, repair_round=index,
                       max_repairs=session.job.get('input',{}).get('max_repairs',2))
    session.save()


def _ask(session, phase, instruction, context, schema):
    return parse_object(response_text(session.call(phase, legacy._messages(instruction,context),schema=schema)))


def validate_plan(plan, context):
    contract.structure(plan, PLAN_SCHEMA, 'visual_plan')
    core={key:copy.deepcopy(plan[key]) for key in contract.STORY_SCHEMA['properties']}
    for beat in core['beats']:
        beat['required_capabilities']=[]
    contract.validate_story(core,context['scene'],context['target_seconds'],
                            is_opening_scene=bool(context.get('is_opening_scene')))
    scene_references.validate_request(plan['required_apis'],plan['recipe_ids'])
    ids=[o['id'] for o in plan['objects']]
    if len(ids)!=len(set(ids)):
        raise WorkflowError('INVALID_SCENE_DESIGN','Visual object IDs must be unique')
    from src.scene_design.script_visuals import validate_plan_criteria
    return validate_plan_criteria(plan,context)


def _plan(session, context, index, previous, issues):
    result=_ask(session,f'v3_plan_{index}',
        'Start from the approved SCRIPT + STYLE + SYSTEM CAPABILITIES supplied together. '
        'Plan the visual explanation and motion before writing code. Preserve scene_id, narration and target duration exactly. '
        'Partition narration verbatim into consecutive beats covering the entire duration. Prefer 1..3 beats. '
        'State what viewers see, how it moves, what they understand and concrete acceptance criteria. '
        'For cutting/rearrangement, specify piece boundaries, exact translations/rotations, conserved area and the expected final outline. '
        'Explain how the remaining real pieces assemble; do not assume a small arbitrary shift reconstructs the original outline. '
        'Show a concrete anchor at t=0 and start meaningful motion before 0.8 seconds. '
        'Choose recipe_ids and at most 16 required_apis from actual Manim needs; a targeted reference lookup follows. '
        'Accepted names include VGroup, Axes.plot, manim.VGroup, manim_kit.vn_text and src.scene_design.manim_kit.vn_text. '
        'objects lists semantic object IDs/roles for track(), including individual text labels. '
        'Use the shared portrait frame and safe bounds. Formula/text must stay readable at both resolutions. '
        'For overflow despite minimum readable fonts, split the explanation into sequential visual states or reduce simultaneous content; '
        'keep narration/duration and all unaffected acceptance criteria. Do not solve density by shrinking indefinitely. '
        'Plan within the installed runtime; local reference may be newer. Do not restrict the plan to the old JSON renderer primitives. '
        'When repairing, preserve unaffected parts of the plan and every existing criterion unless meaning feedback explicitly requires a correction.',
        dict(context,previous_visual_plan=previous,issues=issues),PLAN_SCHEMA)
    result=validate_plan(result,context)
    if previous and issues and not any(i['category']=='meaning' for i in issues):
        old={c for beat in previous['beats'] for c in beat['criteria']}
        new={c for beat in result['beats'] for c in beat['criteria']}
        if not old<=new:
            raise WorkflowError('INVALID_SCENE_DESIGN','Replanning cannot remove acceptance criteria')
    return result


def validate_helper_calls(source,references):
    """Catch unsupported helper keywords before spending a render attempt."""
    apis=references.get('component_apis',{})
    tree=ast.parse(source)
    aliases={}
    modules=set()
    for node in ast.walk(tree):
        if isinstance(node,ast.ImportFrom) and node.module in ('manim_kit','src.scene_design.manim_kit'):
            for name in node.names:
                if name.name=='*':aliases.update({key:key for key in apis})
                else:aliases[name.asname or name.name]=name.name
        if isinstance(node,ast.Import):
            modules.update(n.asname or n.name for n in node.names if n.name=='manim_kit')
    for node in ast.walk(tree):
        if not isinstance(node,ast.Call):continue
        name=aliases.get(node.func.id) if isinstance(node.func,ast.Name) else None
        if isinstance(node.func,ast.Attribute) and isinstance(node.func.value,ast.Name) and node.func.value.id in modules:
            name=node.func.attr
        info=apis.get(name,{})
        if not info.get('available') or info.get('accepts_kwargs',True):continue
        unknown={k.arg for k in node.keywords if k.arg is not None}-set(info.get('keywords',[]))
        if unknown:
            raise WorkflowError('INVALID_SCENE_CODE',f'Line {node.lineno}: {name} has unsupported keyword(s) {sorted(unknown)}. '
                f'Installed helper signature: {name}{info["signature"]}. Preserve readable size >= 22 after scaling.')
    return source


def _code(session, context, plan, references, index, previous, issues):
    result=_ask(session,f'v3_code_{index}',
        'Write runnable direct Python Manim code from the visual plan, style and installed API references. '
        'Return one GeneratedScene(Scene) with construct; import only manim, math, numpy, manim_kit or src.scene_design.manim_kit. '
        'Use shared components and theme. Use track(self, object_id, mobject, role, overlap_with=()) '
        'for EVERY planned semantic object, especially each text/formula separately from backgrounds and axes. '
        'Only intentional semantic overlap may be declared. Labels must remain clear for the entire motion path. '
        'Use vn_text(content, size=28), formula_lines, fit_to_safe_area and MIN_FONT_SIZE; wrap formulas at semantic boundaries. '
        'Helper signatures in component_apis are authoritative. Effective font size after every scale must stay >= MIN_FONT_SIZE. '
        'repair_history contains earlier failures: preserve fixes already made and do not reintroduce them while fixing the latest issue. '
        'Text and geometry use the same 9 x 16 frame at 360x640 preview and 1080x1920 final, both 30 FPS. '
        'Never multiply coordinates, font sizes, stroke widths or timing by pixel resolution. Never change global config or camera dimensions. '
        'Set self.camera.background_color from the supplied style. Embed style/duration as Python literals. '
        'Keep all visible content within frame_contract.safe_bounds, reserving the bottom region for captions. '
        'Use actual Manim animations, geometry, Axes, ValueTracker and updaters; dependent mathematical values use a single shared source. '
        'For cutting/rearranging objects, derive pieces from the original geometry and conserve area; never create a duplicate piece to fake extraction. '
        'Start with visible anchor objects at t=0, meaningful motion by 0.8s, no blank opening. '
        'Budget each self.play explicitly; call finish_scene(self, target_seconds). Never wait with nonpositive duration. '
        'Preserve narration exactly; no sound or narration walls. Use DejaVu Sans Text for Vietnamese; '
        'MathTex is allowed ONLY if system_capabilities.runtime.latex_available is true. '
        'ImageMobject may use only literal approved asset paths; use Group for images. '
        'No filesystem, networking, subprocess, environment, introspection or external imports. '
        'Repair normal layout/timing/math/runtime issues DIRECTLY IN THE CODE (buff, scale, shift, paths, run_time). '
        'Set requires_design_change=true ONLY when meaning or density cannot be repaired while preserving readable minimum fonts; '
        'Prefix reason with LAYOUT_REPLAN_REQUIRED for density or MEANING_REPLAN_REQUIRED for meaning errors. This returns to visual planning. '
        'Do not change visual criteria merely to pass review. Use exact installed API signatures over newer local documentation.',
        dict(context,visual_plan=plan,selected_references=references,
             previous_code=previous,issues=issues),CODE_SCHEMA)
    contract.structure(result,CODE_SCHEMA,'manim_code')
    if result['requires_design_change']:
        code='MEANING_REPLAN_REQUIRED' if result['reason'].startswith('MEANING_REPLAN_REQUIRED') else 'LAYOUT_REPLAN_REQUIRED'
        raise WorkflowError(code,result['reason'] or 'Visual plan needs splitting')
    return validate_helper_calls(validate_code(result['source'],context['assets']['assets']),references)


def _manifest(plan):
    return dict(schema_version=VERSION,scene_id=plan['scene_id'],narration=plan['narration'],
        purpose=plan['goal'],duration=plan['duration'],visual_strategy=plan['visual_strategy'],
        objects=plan['objects'],groups=[],graphs=[],render_profile=scene_theme.profile())


def render_code(root, work, session, design, source, config):
    from src.scene_design import scene_generation as sg
    checksum=hashlib.sha256(source.encode()).hexdigest()
    path=work/'code_render.json'
    record=store.read_json(path) if path.exists() else None
    if record and record['source_sha256']!=checksum:
        raise WorkflowError('CACHE_CORRUPT','Saved v3 source changed')
    if record and record['status']=='success':
        ref=record['reference']
        for key,digest in [('path','sha256'),('code_path','code_sha256')]:
            if store.digest(root/ref[key])!=ref[digest]:
                raise WorkflowError('ARTIFACT_CHANGED','Saved Manim candidate changed')
        return ref
    # Recover a completed worker after an interruption without another render.
    metadata=root/f"logs/{design['scene_id']}_{record['version']}.json" if record else None
    report=store.read_json(metadata) if metadata and metadata.exists() else {}
    if report.get('status')=='success':
        output=report
    else:
        version=sg._clip_version(root,design['scene_id'])
        code_path=f"scenes/{design['scene_id']}_{version}.py"
        store.materialize(root,code_path,content=source.encode())
        record=dict(status='running',version=version,code_path=code_path,source_sha256=checksum)
        store.atomic_write(path,record)
        settings=dict(scene_theme.render_profile(config['render']),
                      target_duration=design['duration'],collect_layout=True)
        output=sg.RENDERER(root,design['scene_id'],version,code_path,'GeneratedScene',settings)
        if output['status']!='success':
            raise WorkflowError('RENDER_FAILED',output.get('error') or 'Manim failed')
        report=store.read_json(Path(output['log_path']).with_suffix('.json'))
    video=report['video']
    if abs(video['duration_seconds']-design['duration'])>2/scene_theme.FPS+1e-6:
        raise WorkflowError('SCENE_DURATION_MISMATCH','Manim duration differs from approved narration timing')
    # The worker already validates FPS with encoding tolerance. Do not reject
    # a valid movie here because its measured average is slightly above 30.
    if (video['width'],video['height'])!=(360,640):
        raise WorkflowError('RENDER_FAILED','Preview must be 360 x 640')
    ref=sg.render_reference(root,design['scene_id'],Path(output['video_path']),
        dict(clip_version=record['version'],code_path=record['code_path']))
    ref['video'].pop('layout_telemetry',None)
    ref.update(preview_engine='direct_code',frame_contract=scene_theme.profile(),
        component_sha256=store.digest(Path(__file__).with_name('manim_kit.py')),
        theme_sha256=store.digest(Path(scene_theme.__file__)))
    store.atomic_write(path,dict(record,status='success',reference=ref))
    return ref


def evidence(root, work, ref, design, plan):
    metadata=store.read_json(root/ref['metadata_path'])
    video=metadata['video'];telemetry=video.get('layout_telemetry')
    if not telemetry or not telemetry['checked_frames']:
        raise WorkflowError('RENDER_FAILED','Actual render layout telemetry is missing')
    issues=copy.deepcopy(telemetry['issues'])
    missing={o['id'] for o in design['objects']}-set(telemetry['object_ids'])
    if missing:
        issues.append(dict(category='layout',time=0,object_ids=sorted(missing),
            problem='Planned semantic objects were not tracked',fix='Register every planned semantic object with track()'))
    # IDs not in the visual plan are still audited, but feedback must refer to
    # stable known IDs or an empty list accepted by the review contract.
    known={o['id'] for o in design['objects']}
    for issue in issues:
        issue['object_ids']=[oid for oid in issue['object_ids'] if oid in known]
    ordered=select_frames(telemetry,video['frame_count'])
    store.atomic_write(work/'layout_telemetry.json',telemetry)
    checks=dict(passed=not issues,issues=issues,checked_frames=telemetry['checked_frames'],
                source='actual Manim Cairo geometry',early_motion=telemetry['early_motion'],
                visual_candidates=telemetry.get('candidates',[]),
                motion_events=telemetry.get('events',[]))
    store.atomic_write(work/'motion_checks.json',checks)
    render_checks=dict(passed=True,issues=[],video=ref['video'],layout_checks=checks,
        target_duration_seconds=design['duration'],duration_tolerance_seconds=2/scene_theme.FPS)
    store.atomic_write(work/'render_checks.json',render_checks)
    folder=work/'frames';folder.mkdir(exist_ok=True)
    frames=[]
    with TemporaryDirectory(prefix='.extract-',dir=folder) as temporary:
        batch=Path(temporary)
        select_expr='+'.join(f'eq(n\\,{frame})' for frame in ordered)
        run([binary('ffmpeg'),'-y','-v','error','-i',str(root/ref['path']),
            '-vf','select='+select_expr,'-vsync','0',str(batch/'frame_%03d.png')])
        for i,frame in enumerate(ordered,1):
            path=folder/f'frame_{frame:05}.png'
            candidate=batch/f'frame_{i:03}.png'
            if not candidate.is_file():
                raise WorkflowError('RENDER_FAILED','Missing encoded evidence frame')
            candidate.replace(path)
            frames.append(dict(frame=frame,time=frame/30,path=str(path.relative_to(root)),sha256=store.digest(path)))
    width,height=360,640
    canvas=Image.new('RGB',(width*len(frames),height+32),'#F3F6F8')
    draw=ImageDraw.Draw(canvas)
    font=ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',16)
    for i,item in enumerate(frames):
        with Image.open(root/item['path']) as im:
            canvas.paste(im.convert('RGB'),(i*width,32))
        draw.text((i*width+8,6),f"{item['time']:.3f}s · #{item['frame']}",font=font,fill='#173746')
    sheet=work/'contact_sheet_00.jpg';canvas.save(sheet,quality=90)
    manifest=dict(frames=frames,contact_sheets=[dict(path=str(sheet.relative_to(root)),
        sha256=store.digest(sheet),frames=frames)],video_sha256=ref['sha256'],
        sampling=dict(limit=5,review_frame_count=len(frames),
        strategy='Actual geometry anomalies, opening, self.play midpoints/endpoints, final encoded frame'))
    store.atomic_write(work/'evidence.json',manifest)
    return checks,render_checks,manifest


def _publish(root,directory,session,work,design,plan,assets,ref,review):
    from src.scene_design import scene_generation as sg
    sg._design_files(directory,design,assets,dict(schema_version=VERSION,datasets=[]))
    for name,data in [('assets.json',assets),('storyboard.json',plan),('manim_render.json',ref)]:
        legacy._write(directory/name,data)
    files=[p for p in work.rglob('*') if p.is_file() and p.suffix in ('.json','.png','.jpg','.py')]
    files += [directory/'design.json',directory/'storyboard.json',root/ref['path'],root/ref['code_path']]
    quality=dict(passed=review['passed'],pipeline_version=VERSION,
        design_sha256=store.digest(directory/'design.json'),video_sha256=ref['sha256'],
        storyboard_sha256=store.digest(directory/'storyboard.json'),
        review_model=session.job['vision_pricing']['model'],summary=review['summary'],
        repairs=session.job.get('repair_round',0),issues=review['issues'],
        evidence_files=[dict(path=str(p.relative_to(root)),sha256=store.digest(p)) for p in files])
    legacy._write(directory/'quality.json',quality)
    session.job.update(progress='ready_for_review',quality_issues=review['issues']);session.save()
    return directory/'design.json',dict(review_path=str(directory/'design.md'),
        render_engine='manim',manim_render=ref,design_pipeline=VERSION,quality_passed=review['passed'],
        quality_path=str((directory/'quality.json').relative_to(root)),
        storyboard_path=str((directory/'storyboard.json').relative_to(root)))


def generate(root,directory,session,context):
    from src.scene_design.script_visuals import with_visual_handoff
    context=with_visual_handoff(context)
    if context.get('visual_handoff'):
        legacy._write(directory/'visual_handoff.json',context['visual_handoff'])
    session.max_requests=24
    job=session.job
    max_repairs=job.get('input',{}).get('max_repairs',2)
    if type(max_repairs) is not int or not 0<=max_repairs<=3:
        raise WorkflowError('INVALID_INPUT','Invalid scene repair limit')
    final=directory/'quality.json'
    if final.exists():
        quality=store.read_json(final);ref=store.read_json(directory/'manim_render.json')
        legacy.verify_quality(root,dict(sha256=store.digest(directory/'design.json'),
            quality_path=str(final.relative_to(root)),manim_render=ref))
        return directory/'design.json',dict(review_path=str(directory/'design.md'),render_engine='manim',
            manim_render=ref,design_pipeline=VERSION,quality_passed=quality['passed'],
            quality_path=str(final.relative_to(root)),storyboard_path=str((directory/'storyboard.json').relative_to(root)))
    cap_path=directory/'system_capabilities.json'
    if cap_path.exists():
        capabilities=store.read_json(cap_path)
        if capabilities['runtime']['version']!=scene_references.manim_runtime.info()['version']:
            raise WorkflowError('DEPENDENCY_ERROR','Installed Manim changed during a saved job')
    else:
        capabilities=scene_references.capabilities();legacy._write(cap_path,capabilities)
    assets=context.get('shared_assets') or dict(assets=[],failures=[],fallback_required=False)
    legacy._write(directory/'assets.json',assets)
    context=dict(context,system_capabilities=capabilities,frame_contract=scene_theme.profile(),assets=assets)
    vision=scene_vision.CLIENT_FACTORY()
    if 'vision_pricing' not in job:
        job['vision_pricing']=vision.preflight(search=False);session.save()
    if job['vision_pricing']['model']!=scene_vision.review_model():
        raise WorkflowError('MODEL_MISMATCH','Restore the configured visual review model')
    plan=None;source=context.get('previous_code');issues=[]
    previous=context.get('previous_design') or {}
    if previous.get('schema_version')==3:
        plan=context.get('previous_storyboard')
    for index in range(max_repairs+1):
        work=directory/f'round_{index:02}';work.mkdir(exist_ok=True)
        result_path=work/'result.json'
        if result_path.exists():
            result=store.read_json(result_path)
            if (work/'storyboard.json').exists():plan=store.read_json(work/'storyboard.json')
            if (work/'scene.py').exists():source=(work/'scene.py').read_text(encoding='utf-8')
            issues=result['issues']
            if result.get('reviewable'):
                return _publish(root,directory,session,work,store.read_json(work/'design.json'),
                    plan,assets,result['reference'],result['review'])
            continue
        try:
            needs_plan=plan is None or any(i['category'] in ('meaning','unsupported') or 'LAYOUT_REPLAN_REQUIRED' in i['problem'] for i in issues)
            if needs_plan:
                _progress(session,'planning_visuals',index)
                plan=_plan(session,context,index,plan,issues)
            legacy._write(work/'storyboard.json',plan)
            _progress(session,'reading_references',index)
            ref_path=work/'references.json'
            references=store.read_json(ref_path) if ref_path.exists() else {}
            if references.get('schema_version')!=scene_references.REFERENCE_VERSION:
                references=scene_references.lookup(plan['required_apis'],plan['recipe_ids'])
            legacy._write(ref_path,references)
            if references['missing']:
                raise WorkflowError('LAYOUT_REPLAN_REQUIRED','Unavailable installed APIs: '+', '.join(references['missing']))
            design=_manifest(plan);legacy._write(work/'design.json',design)
            _progress(session,'writing_code' if source is None else 'repairing_code',index)
            history=[dict(round=path.parent.name,issues=store.read_json(path).get('issues',[]))
                for path in sorted(directory.glob('round_*/result.json')) if path.parent.name<work.name]
            source=_code(session,dict(context,repair_history=history),plan,references,index,source,issues)
            store.materialize(work,'scene.py',content=source.encode())
            _progress(session,'rendering_draft',index)
            ref=render_code(root,work,session,design,source,context['config'])
            checks,render_checks,manifest=evidence(root,work,ref,design,plan)
            if not checks['passed']:
                issues=checks['issues']
                legacy._write(result_path,dict(passed=False,issues=issues));continue
            from src.scene_design import scene_drafts
            scene_drafts.publish(root,work,session,ref)
            _progress(session,'evaluating_frames',index)
            review=legacy._review(root,session,vision,plan,design,manifest,render_checks,index,phase_prefix='v3',visual_handoff=context.get('visual_handoff'))
            legacy._write(work/'visual_review.json',review)
            issues=review['issues']
            job['draft']['review']=dict(passed=review['passed'],summary=review['summary'],
                criteria_passed=sum(c['passed'] for c in review['criteria_results']),criteria_total=len(review['criteria_results']))
            session.save()
            reviewable=review['passed'] or index==max_repairs
            legacy._write(result_path,dict(passed=review['passed'],issues=issues,reviewable=reviewable,reference=ref,review=review))
            if reviewable:
                return _publish(root,directory,session,work,design,plan,assets,ref,review)
        except WorkflowError as exc:
            if exc.code not in ('INVALID_SCENE_DESIGN','INVALID_SCENE_REFERENCE','INVALID_SCENE_CODE','RENDER_FAILED','SCENE_DURATION_MISMATCH','INVALID_MODEL_OUTPUT','INVALID_VISUAL_REVIEW','LAYOUT_REPLAN_REQUIRED','MEANING_REPLAN_REQUIRED'):
                raise
            reference_failure=exc.code=='INVALID_SCENE_REFERENCE' or job.get('progress')=='reading_references'
            planning_failure=job.get('progress')=='planning_visuals'
            category='unsupported' if reference_failure else 'meaning' if planning_failure or plan is None or exc.code=='MEANING_REPLAN_REQUIRED' else 'layout' if exc.code=='LAYOUT_REPLAN_REQUIRED' else 'relation'
            issues=[dict(category=category,time=0,object_ids=[],problem=redact(str(exc)),
                         fix='Correct required_apis or recipe_ids in the visual plan; no scene code has been rendered for this reference error.' if reference_failure else 'Repair the visual plan' if planning_failure or exc.code=='LAYOUT_REPLAN_REQUIRED' or plan is None else 'Repair the Manim code; preserve the visual plan and criteria')]
            legacy._write(result_path,dict(passed=False,issues=issues))
    job.update(progress='quality_failed',quality_issues=issues);session.save()
    raise WorkflowError('SCENE_QUALITY_FAILED','Cảnh cần sửa sau các vòng kiểm tra. '+ '; '.join(i['problem'] for i in issues)[:800])


def render_final(root, artifact, config=None):
    """Rerender approved source at native final resolution without model calls."""
    from src.scene_design import scene_generation as sg
    root=Path(root)
    legacy.verify_quality(root,artifact)
    ref=artifact['manim_render']
    if store.digest(root/ref['code_path'])!=ref['code_sha256']:
        raise WorkflowError('ARTIFACT_CHANGED','Approved source changed')
    design=store.read_json(root/artifact['path'])
    for name,checksum in [('manim_kit.py',ref.get('component_sha256')),
                          ('scene_theme.py',ref.get('theme_sha256'))]:
        if checksum and store.digest(Path(__file__).with_name(name))!=checksum:
            raise WorkflowError('ARTIFACT_CHANGED','Shared component/theme changed after scene review')
    runtime=scene_references.manim_runtime.info()
    if runtime['version']!=ref['runtime']['version']:
        raise WorkflowError('DEPENDENCY_ERROR','Final render requires the reviewed Manim runtime')
    # Keep the source and logical camera fixed; only increase pixel density.
    version=sg._clip_version(root,design['scene_id'])
    output=sg.RENDERER(root,design['scene_id'],version,ref['code_path'],'GeneratedScene',
        dict(scene_theme.render_profile((config or sg._config(root))['render'],final=True),
             target_duration=design['duration'],collect_layout=True))
    if output['status']!='success':
        return output
    metadata_path=Path(output['log_path']).with_suffix('.json')
    metadata=store.read_json(metadata_path)
    preview=store.read_json(root/ref['metadata_path'])['video']['layout_telemetry']
    final=metadata['video'].get('layout_telemetry',{})
    checks=compare_layouts(preview,final,design)
    path=metadata_path.with_suffix('.layout.json');store.atomic_write(path,checks)
    output['layout_checks_path']=str(path)
    if not checks['passed']:
        output.update(status='failed',error='FINAL_LAYOUT_MISMATCH: '+str(checks['issues'])[:1000])
    return output


def compare_layouts(preview,final,design,tolerance=.03):
    issues=list(final.get('issues',[]))
    known={o['id'] for o in design['objects']}
    if not final.get('checked_frames') or not known<=set(final.get('object_ids',[])):
        issues.append(dict(problem='Final layout telemetry is incomplete'))
    expected={row['frame']:row for row in preview.get('samples',[])}
    actual={row['frame']:row for row in final.get('samples',[])}
    checked=0
    for frame,a in expected.items():
        b=actual.get(frame)
        if not b:
            issues.append(dict(frame=frame,problem='Final render is missing a preview layout milestone'))
            continue
        for oid in known:
            x,y=a['objects'].get(oid),b['objects'].get(oid)
            if x is None and y is None:
                continue
            if x is None or y is None or any(abs(v-w)>tolerance for v,w in zip(x['box'],y['box'])):
                issues.append(dict(frame=frame,object_ids=[oid],problem='Final geometry differs from reviewed preview'))
            checked+=1
    return dict(passed=not issues,issues=issues,compared_objects=checked,
        tolerance_scene_units=tolerance,preview_resolution=[360,640],final_resolution=[1080,1920],
        frame_contract=scene_theme.profile())
