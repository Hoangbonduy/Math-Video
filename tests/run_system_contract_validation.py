"""Reproducible evidence, kept separate from provider-mocked pytest results.

history: replay saved sources into isolated artifact folders, never rewrite reviews.
examples: hand-authored positive/negative renders, not model competence evidence.
live: configured Codex, existing scene gates and ledger; no approval/retry bypass.
"""
import argparse
import copy
import json
import os
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from src.shared import system_contract as sc, _workflow_store as store
from src.shared import workflow as wf
from src.scene_design import manim_runtime,scene_theme,scene_generation as sg
from src.scene_design.render import render_scene
PROJECT=ROOT/'data/projects/video_584bae774f49440daec7efefa80a35f5'
OUTPUT=ROOT/'artifacts/system-contract'


def history():
    p,reviews=wf._load(PROJECT);rows=[]
    jobs=[store.read_json(PROJECT/path) for path in store.read_json(PROJECT/'generation/checkpoint_5/ledger.json')['jobs']]
    for sid in p['scene_ids']:
        a=wf._latest(p,'scene_design',sid);ref=a.get('manim_render') or {}
        row=dict(scene_id=sid,review_status=wf._request(reviews,a)['status'],artifact=a['path'],
                 historical_issues=[],replay=None)
        for j in jobs:
            if j['input'].get('scene_id')!=sid:continue
            folder=PROJECT/sg.BASE/j['job_id']
            for f in sorted(folder.glob('round_*/result.json')):
                for issue in store.read_json(f).get('issues',[]):
                    row['historical_issues'].append(dict(path=str(f.relative_to(PROJECT)),issue=issue))
        row['historical_issue_count']=len(row['historical_issues'])
        if ref:
            meta=store.read_json(PROJECT/ref['metadata_path'])
            source=PROJECT/ref['code_path'];folder=OUTPUT/'history'/sid;folder.mkdir(parents=True,exist_ok=True)
            target=folder/'source.py'
            store.materialize(folder,'source.py',content=source.read_bytes())
            settings={k:v for k,v in meta['render_config'].items() if k!='renderer'}
            # Historical coordinate contract is preserved; do not relabel it v1.
            result_path=folder/'result.json'
            if result_path.exists():row['replay']=store.read_json(result_path)
            else:
                result=render_scene(folder,sid,'history',target,meta['scene_name'],settings)
                store.atomic_write(result_path,result);row['replay']=result
            row.update(source_sha256=store.digest(source),copied_source_sha256=store.digest(target),
                       legacy_contract=True,meaning_reverified=False)
        else:row['replay']=dict(status='insufficient_evidence',reason='No saved source/render reference')
        rows.append(row);print(sid,'historical issues',row['historical_issue_count'],'replay',row['replay']['status'],flush=True)
    store.atomic_write(OUTPUT/'history.json',rows)
    return rows


def examples():
    effective=sc.load_contract(runtime=manim_runtime.info());rows=[]
    prefix='from manim import *\nfrom manim_kit import vn_text,track,finish_scene\nclass GeneratedScene(Scene):\n    def construct(self):\n'
    cases={
      'graph':("p=track(self,'p',Dot([-1,-1,0]),'shape',layout_role='focal')\n"
          "label=track(self,'label',vn_text('P',28).next_to(p,UP,buff=.3),'text',layout_role='attached_label')\n"
          "label.add_updater(lambda m:m.next_to(p,UP,buff=.3))\nself.add(p,label)\nself.play(p.animate.shift(2*RIGHT),run_time=2)"),
      'geometry':("a=track(self,'a',Square(side_length=1).shift(LEFT),'shape')\n"
          "b=track(self,'b',Square(side_length=1).shift(RIGHT),'shape')\nself.add(a,b)\nself.play(a.animate.shift(DOWN),b.animate.shift(UP),run_time=2)"),
      'comparison':("a=track(self,'a',Rectangle(width=1,height=1).move_to([-1,-1,0]),'shape')\n"
          "b=track(self,'b',Rectangle(width=1,height=2).move_to([1,-.5,0]),'shape')\n"
          "label=track(self,'unit',vn_text('m / same scale',28).move_to([0,5,0]),'text',layout_role='hud')\n"
          "self.add(a,b,label)\nself.play(a.animate.set_fill(BLUE,opacity=.8),b.animate.set_fill(BLUE,opacity=.8),run_time=2)"),
      'closeup':("body=track(self,'body',Rectangle(width=6,height=3),'shape',layout_role='context',overflow_windows=[dict(start=.2,end=2.8,reason='closeup')])\n"
          "p=track(self,'focus',Dot(),'shape',layout_role='focal')\nself.add(body,p)\n"
          "self.play(body.animate.scale(2),run_time=1)\nself.wait(.5)\nself.play(body.animate.scale(.5),run_time=1)"),
      'process':("nodes=VGroup(*(Circle(radius=.3).move_to([x,0,0]) for x in (-2,0,2)))\n"
          "track(self,'steps',nodes,'shape')\npoint=track(self,'token',Dot([-2,-1,0]),'shape')\n"
          "self.add(nodes,point)\nself.play(point.animate.move_to([0,-1,0]),run_time=1)\nself.play(point.animate.move_to([2,-1,0]),run_time=1)"),
      'hud':("hud=track(self,'hud',vn_text('2',28).move_to([0,5,0]),'text',layout_role='hud')\n"
          "a=track(self,'a',Dot([-1,0,0]),'shape')\nb=track(self,'b',Dot([1,0,0]),'shape')\n"
          "self.add(hud,a,b)\nself.play(a.animate.shift(DOWN),b.animate.shift(UP),run_time=2)"),
      'question':("q=track(self,'q',vn_text('What happens next?',28).move_to([0,5,0]),'text',layout_role='hud')\n"
          "p=track(self,'p',Dot([-1,0,0]),'shape')\nself.add(q,p)\nself.play(p.animate.shift(2*RIGHT),run_time=2)")}
    for name,body in cases.items():
        for valid in (True,False):
            folder=OUTPUT/'examples'/name/('valid' if valid else 'invalid');folder.mkdir(parents=True,exist_ok=True)
            # The negative pair has a known unreadable text independent of topic.
            extra='' if valid else "\nsmall=track(self,'unreadable',vn_text('12pt',12).move_to([0,-3,0]),'text')\nself.add(small)"
            source=prefix+'\n'.join('        '+line for line in (body+extra+'\nfinish_scene(self,3)').splitlines())+'\n'
            store.materialize(folder,'source.py',content=source.encode())
            result_path=folder/'result.json'
            if result_path.exists():result=store.read_json(result_path)
            else:
                result=render_scene(folder,name,'case',folder/'source.py','GeneratedScene',
                    dict(scene_theme.render_profile(sg.DEFAULT_CONFIG['render']),target_duration=3,collect_layout=True,system_contract=effective))
                store.atomic_write(result_path,result)
            issues=[]
            if result['status']=='success':issues=store.read_json(Path(result['log_path']).with_suffix('.json'))['video']['layout_telemetry']['issues']
            observed=result['status']=='success' and not issues
            row=dict(case=name,expected_pass=valid,observed_pass=observed,matched=observed==valid,result=result,issues=issues,
                     provider='hand-authored source; real Manim render; no model')
            rows.append(row);print(name,valid,'matched',row['matched'],flush=True)
    store.atomic_write(OUTPUT/'examples.json',rows)
    return rows


def live():
    from src.shared.ai_config import get_backend,get_model
    if get_backend()!='codex':raise RuntimeError('Configured backend must be Codex; refusing to substitute')
    rows=[];quota=False
    for sid in store.read_json(PROJECT/'project.json')['scene_ids']:
        p,reviews=wf._load(PROJECT);a=wf._latest(p,'scene_design',sid)
        row=dict(scene_id=sid,backend=get_backend(),model=get_model())
        if a and wf._request(reviews,a)['status']=='approved':
            row.update(status='human_gate',reason='Existing run_scenes cannot replace an approved design. Approval and artifact preserved.')
        elif quota:row.update(status='not_run',reason='Configured Codex quota exhausted earlier in this trial')
        else:
            try:
                sg.run_scenes(PROJECT,scene_id=sid,trial_mode=True,attempt_id='system-contract-v1-20261106')
                row['status']='attempt_finished'
            except store.WorkflowError as exc:row.update(status='attempt_failed',error_code=exc.code,error=exc.message)
            jobs=sg._jobs(PROJECT);selected=[j for j in jobs if j['input'].get('scene_id')==sid and j['input'].get('attempt_id')=='system-contract-v1-20261106']
            row['jobs']=[dict(job_id=j['job_id'],status=j['status'],last_error=j.get('last_error'),progress=j.get('progress'),
                calls=[dict(call_id=c['call_id'],phase=c['phase'],status=c['status'],error_code=c.get('error_code'),usage=c.get('usage')) for c in j['calls']]) for j in selected]
            quota=any(c.get('error_code')=='CODEX_USAGE_LIMIT' for j in selected for c in j['calls'])
        rows.append(row);store.atomic_write(OUTPUT/'live.json',rows);print(sid,row['status'],flush=True)
    # Representative non-project scene: own durable Session, no FakeRouter.
    if not quota:
        from src.shared.job_session import Session
        from src.shared.ai_client import create_text_client
        from src.scene_design import scene_code_pipeline
        for name,narration,intent in [
            ('graph','Một điểm chạy trên đường cong và nhãn luôn đi theo điểm.', 'Use one function y=x*x; dependent point and attached label share a tracker. No HUD movement.'),
            ('comparison','Hai cột cùng đơn vị cho thấy hai giá trị khác nhau.', 'Compare two lengths using matching units and matching scale, with pinned upper HUD.'),
            ('closeup','Phóng cận chi tiết rồi trở về toàn cảnh.', 'Zoom by group presentation transform; explicit context object/time overflow window; focal object remains safe. No camera control.')]:
            directory=OUTPUT/'live_examples'/name;directory.mkdir(parents=True,exist_ok=True)
            jobpath=directory/'job.json';client=create_text_client()
            job=store.read_json(jobpath) if jobpath.exists() else dict(job_id='contract-'+name,input=dict(stage='scene_design',max_repairs=2,contract_schema_version=1),status='working',calls=[],pricing=client.preflight(search=False))
            s=Session(directory,job,client,max_requests=24)
            context=dict(scene=dict(scene_id=name,narration=narration,visual_intent=intent),script={},brief={},config=copy.deepcopy(sg.DEFAULT_CONFIG),target_seconds=4)
            row=dict(case=name,backend=get_backend(),model=get_model())
            try:
                path,extra=scene_code_pipeline.generate(OUTPUT,directory,s,context)
                row.update(status='rendered_reviewed',path=str(path),quality_passed=extra.get('quality_passed'))
            except store.WorkflowError as exc:row.update(status='failed',error_code=exc.code,error=exc.message)
            rows.append(row);store.atomic_write(OUTPUT/'live.json',rows);print(name,row['status'],flush=True)
            if any(c.get('error_code')=='CODEX_USAGE_LIMIT' for c in job['calls']):break
    return rows


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('mode',choices=['history','examples','live']);parser.add_argument('--run-id');args=parser.parse_args()
    if args.run_id:
        if not args.run_id.replace('-','').replace('_','').isalnum():raise ValueError('Invalid run ID')
        OUTPUT=OUTPUT/args.run_id
    OUTPUT.mkdir(parents=True,exist_ok=True)
    globals()[args.mode]()
