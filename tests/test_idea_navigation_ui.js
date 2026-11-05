const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../src/idea/web.js'), 'utf8');
async function check() {
  let next, saved, form;
  const state = {project_id:'first', idea_draft:{topic:'Existing draft'}};
  const context = vm.createContext({
    state, ideaDraft:undefined, $:()=>form,
    FormData: class { *[Symbol.iterator]() { yield ['topic', form.topic]; yield ['goal', form.goal]; } },
    localStorage:{setItem:()=>{}},
    window:{location:{assign:url=>{next=url;}}},
    action:async (route, payload)=>{ saved={route,payload}; return true; }
  });
  vm.runInContext(source, context);
  // Direct history visits have no form; preserve the server draft.
  await vm.runInContext('newIdea()', context);
  assert.equal(next, '/projects');
  assert.equal(saved.payload.idea.topic, 'Existing draft');
  assert.equal(state.idea_draft.topic, 'Existing draft');
  form = {topic:'Edited topic',goal:'Edited content'};
  next = undefined;
  await vm.runInContext('saveIdea()', context);
  assert.equal(saved.route, 'idea-draft');
  assert.equal(saved.payload.idea.topic, 'Edited topic');
  assert.equal(saved.payload.idea.goal, 'Edited content');
  assert.equal(next, '/projects');
  next = undefined;
  context.action = async ()=>false;
  await vm.runInContext('saveIdea()', context);
  assert.equal(next, undefined, 'Failed saves must keep the user on the form');
  assert.ok(!source.includes('idea-reference'));
  console.log('PASS: save and return, new idea from history, existing draft preserved, failed save stays, reference removed');
}
check().catch(error=>{console.error(error);process.exitCode=1;});
