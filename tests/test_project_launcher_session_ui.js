const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname,'../src/shared/project_launcher.py'),'utf8');
const script = source.match(/JAVASCRIPT = '''([\s\S]*?)'''/)[1];
new vm.Script(script);
function setup(fetch) {
  const nodes = new Map();
  const context = vm.createContext({fetch,document:{getElementById(id){
    if (!nodes.has(id)) nodes.set(id,{}); return nodes.get(id);
  }},window:{addEventListener(){}}});
  vm.runInContext(script,context);
  vm.runInContext("token='old-page-session'",context);
  return context;
}
const response = (status,result) => ({status,ok:status>=200&&status<300,json:async()=>result});
async function main() {
  let calls=[];
  let context=setup(async (url,options={})=>{
    calls.push({url,options});
    if (url==='/api/projects') return response(200,{token:'current-session',projects:[]});
    assert.equal(options.headers['X-Review-Token'],'current-session');
    return response(200,{id:'created'});
  });
  assert.equal((await vm.runInContext("action('/api/projects/create',{request_id:'same-request',topic:'Test'})",context)).id,'created');
  assert.equal(calls[0].url,'/api/projects');
  assert.equal(calls[0].options.cache,'no-store');
  assert.equal(calls.length,2);

  calls=[];let posts=0;let sessions=0;
  context=setup(async (url,options={})=>{
    calls.push({url,options});
    if (url==='/api/projects') return response(200,{token:'session-'+(++sessions),projects:[]});
    if (++posts===1) return response(403,{code:'LOCAL_SESSION_EXPIRED',error:'expired'});
    assert.equal(options.headers['X-Review-Token'],'session-2');
    return response(200,{id:'created-once'});
  });
  await vm.runInContext("action('/api/projects/create',{request_id:'same-request',topic:'Test'})",context);
  const mutations=calls.filter(c=>c.options.method==='POST');
  assert.equal(mutations.length,2);
  assert.equal(mutations[0].options.body,mutations[1].options.body);

  calls=[];
  context=setup(async (url,options={})=>{
    calls.push({url,options});
    return url==='/api/projects' ? response(200,{token:'current',projects:[]}) :
      response(403,{code:'INVALID_LOCAL_ORIGIN',error:'origin rejected'});
  });
  await assert.rejects(vm.runInContext("action('/api/projects/open',{id:'existing'})",context),/origin rejected/);
  assert.equal(calls.length,2,'Origin rejection must never trigger mutation retries');
  console.log('PASS: stale page tokens refresh; restart races retry once with identical request; origin checks stay enforced.');
}
main();
