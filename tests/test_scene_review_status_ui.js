// Render the real UI functions with paused tasks and quality warnings.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
class Element {
  constructor(tag, text='', className='') { this.tag=tag; this.textContent=text; this.className=className; this.children=[]; this.attributes={}; this.dataset={}; this.classList={toggle:()=>{}}; }
  append(node) { this.children.push(node); }
  replaceChildren() { this.children=[]; }
  setAttribute(key,value) { this.attributes[key]=value; }
  addEventListener() {}
  querySelector(selector) { return flatten(this).find(n=>n.className.split(' ').includes(selector.slice(1))); }
}
function flatten(node) { return [node, ...node.children.flatMap(flatten)]; }
const nodes = new Map();
const $ = id => { if(!nodes.has(id))nodes.set(id,new Element('div')); return nodes.get(id); };
const artifact = {scene_id:'scene_3',stage:'scene_design',version:'v004',sha256:'design',quality_passed:false,manim_render:{renderer:'manim',path:'approved.mp4',sha256:'video'}};
const draft = {reference:{renderer:'manim',path:'draft.mp4'}, review:{summary:'Cảnh báo bố cục'}};
const sandbox = {
  $, sid:'scene_3',currentPage:'design',workflowBusy:false,selection:'scene_design:v004', feedbackDrafts:new Map(),
  state:{stage:'scene_design',scene_ids:['scene_3'],scene_generation:{jobs:[{scene_id:'scene_3',stage:'scene_design',status:'paused',quality_issues:[{time:1,problem:'Hai nhãn chồng nhau',fix:'Tách nhãn'}]}]},scene_drafts:{scene_3:draft},scene_revisions:[],design_previews:{},idea:{}},
  workflowTasks:{'generate:scene_3':{state:'paused'}}, review:{status:'pending'},
  latest:()=>artifact, reviewFor:()=>sandbox.review, label:value=>({approved:'Đã duyệt',pending:'Chờ duyệt'}[value] || value),
  el:(tag,text,parent,className='')=>{const node=new Element(tag,text,className);if(parent)parent.append(node);return node;},
  button:(parent,text,fn,className='')=>{const node=new Element('button',text,className);node.onclick=fn;parent.append(node);return node;},
  badge:()=>{},shortTitle:()=> 'Cảnh 3',timing:()=>'',chooseScene:()=>{},clock:()=>'',media:p=>p,designFor:()=>({}),sceneData:()=>({}),
};
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(path.join(__dirname,'../src/scene_design/web.js'),'utf8'),sandbox);
const render = () => { nodes.clear(); vm.runInContext('renderScenes();renderSceneProgress(latest());renderFeedback(latest(),[]);renderViewer(latest());',sandbox); };
const buttons = id => flatten($(id)).filter(n=>n.tag==='button');
render();
assert.ok(flatten($('scene-progress')).some(n=>n.textContent==='1 giây · Hai nhãn chồng nhau'), 'Warnings remain visible after pause');
assert.ok(buttons('feedback').some(n=>n.textContent==='Gửi góp ý' && !n.disabled));
assert.ok(buttons('feedback').some(n=>n.textContent==='Duyệt thiết kế' && !n.disabled));
sandbox.review.status='approved';
render();
assert.equal($('scenes').children[0].attributes['aria-label'],'scene_3 · Đã duyệt');
assert.ok(flatten($('scene-progress')).some(n=>n.tag==='strong' && n.textContent==='Đã duyệt'));
assert.ok(flatten($('scene-progress')).some(n=>n.textContent==='1 giây · Hai nhãn chồng nhau'), 'Approval preserves warnings');
assert.ok(buttons('feedback').some(n=>n.textContent==='Gửi góp ý' && !n.disabled), 'Approved designs can receive new feedback while design step is open');
assert.ok(flatten($('viewer')).some(n=>n.className==='viewer-kind' && n.textContent==='Đã duyệt'));
assert.equal(flatten($('viewer')).find(n=>n.tag==='video').src,'approved.mp4', 'Approved viewer uses the reviewed artifact, not an unrelated draft');
sandbox.state.scene_generation.jobs[0].status='failed';
sandbox.workflowTasks['generate:scene_3'].state='failed';
render();
assert.equal($('scenes').children[0].attributes['aria-label'],'scene_3 · Đã duyệt');
sandbox.workflowTasks['generate:scene_3'].state='working';
render();
assert.notEqual($('scenes').children[0].attributes['aria-label'],'scene_3 · Đã duyệt','Active new generation must retain progress');
console.log('PASS: paused/warned designs allow feedback and approval; approved status wins over stopped jobs; warnings persist; approved video matches selection');

// A newer failed run must not reuse an older draft's assessment.
sandbox.review.status='pending';
sandbox.workflowTasks['generate:scene_3'].state='failed';
sandbox.state.scene_generation.jobs[0].job_id='new_job';
sandbox.state.scene_generation.jobs[0].render_status='success';
draft.job_id='old_job';
draft.review={summary:'OLD_REVIEW',criteria_passed:6,criteria_total:8};
render();
assert.ok(!flatten($('scene-progress')).some(n=>String(n.textContent).includes('OLD_REVIEW')));
assert.ok(!flatten($('scene-progress')).some(n=>String(n.textContent).includes('6/8')));
assert.ok(flatten($('scene-progress')).some(n=>n.textContent==='Đã render · Chưa đạt kiểm tra'));
