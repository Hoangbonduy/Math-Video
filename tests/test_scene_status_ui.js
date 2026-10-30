// Regression for a failed batch leaking the first scene's error to unstarted scenes.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const app = fs.readFileSync(path.join(__dirname,'../src/scene_design/web.js'),'utf8');
const helpers = app.slice(app.indexOf('function sceneJob('), app.indexOf('function scenePhase('));
const context = vm.createContext({state:{stage:'scene_design',scene_ids:['s1','s2'],scene_generation:{jobs:[]}},workflowTasks:{},latest:()=>null});
vm.runInContext(helpers,context);
function task(scene){return vm.runInContext(`sceneTask('${scene}')`,context);}
const first = {scene_id:'s1',stage:'scene_design',current:true,status:'failed',last_error:'first-scene-quality',updated_at:'2026-10-29'};
context.state.scene_generation.jobs = [first];
context.workflowTasks = {workflow:{state:'failed',error:'first-scene-quality'}};
assert.equal(task('s2'),undefined,'Unstarted scene must not inherit a batch failure');
assert.equal(task('s1'),undefined,'Scene failure comes from its own saved job');
assert.equal(vm.runInContext('stoppedScene().scene_id',context),'s1');
context.state.scene_generation.jobs.push({scene_id:'s2',stage:'scene_design',current:true,status:'awaiting_review'});
assert.equal(task('s2'),undefined,'Finished scene must not inherit another scene failure');
context.workflowTasks['generate:s2']={state:'failed',error:'second-scene-specific'};
assert.equal(task('s2').error,'second-scene-specific','Keep errors from an explicit scene task');
context.workflowTasks={workflow:{state:'working'}};
first.status='working';
assert.equal(task('s1').state,'working');
assert.equal(task('s2'),undefined,'Only the active batch scene should show a spinner');
context.state.scene_generation.jobs=[];
context.workflowTasks={workflow:{state:'queued'}};
assert.equal(task('s1').state,'queued');
assert.equal(task('s2'),undefined,'Queued workflow belongs to the first unstarted scene');
context.state.scene_generation.jobs=[{...first,status:'failed',current:false}];
context.workflowTasks={workflow:{state:'failed',error:'obsolete'}};
assert.equal(task('s1'),undefined,'Ignore errors from an obsolete script/model');
assert.equal(vm.runInContext('stoppedScene()',context),null);
console.log('PASS: batch errors stay on their scene; explicit errors preserved; active/queued tasks scoped; stale jobs ignored.');

assert.match(vm.runInContext('sceneRepairLabel({trial_mode:true,max_repairs:2,repair_round:1})',context),/1 \/ 2/);
assert.match(vm.runInContext('sceneRepairLabel({trial_mode:true,max_repairs:0})',context),/Kh\u00f4ng t\u1ef1 s\u1eeda/);
assert.match(vm.runInContext('sceneRepairLabel({trial_mode:true})',context),/Kh\u00f4ng t\u1ef1 s\u1eeda/);
console.log('PASS: UI reports actual repair limits and preserves legacy single-pass labels.');
