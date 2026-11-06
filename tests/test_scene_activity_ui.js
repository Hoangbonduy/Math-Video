const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const {setImmediate}=require('node:timers');
const {URLSearchParams}=require('node:url');
const nodes=new Map();
class Element {
  constructor(tag,text=''){this.tag=tag;this.textContent=text;this.children=[];this.handlers={};this.open=false;}
  set id(value){this.identifier=value;nodes.set(value,this);}
  append(node){this.children.push(node);}
  replaceChildren(){this.children=[];}
  addEventListener(name,handler){this.handlers[name]=handler;}
}
function flatten(node){return [node,...node.children.flatMap(flatten)];}
const root=new Element('div');
const timers=[];const requests=[];
const sandbox={root,currentPage:'design',sid:'s1',token:'session',
 state:{stage:'scene_design',scene_generation:{jobs:[{job_id:'job_1',scene_id:'s1',stage:'scene_design',current:true}]}},
 $:id=>nodes.get(id),el:(tag,text,parent)=>{const node=new Element(tag,text);if(parent)parent.append(node);return node;},
 URLSearchParams,setTimeout:(fn,ms)=>{timers.push({fn,ms});return timers.length;},clearTimeout:()=>{},
 fetch:async(url,options)=>{requests.push({url,options});return {ok:true,json:async()=>({summary:'Writing scene',
 calls:[{phase:'v3_code_0',status:'complete'}],references:[{name:'Polygon',available:true}],
 events:[{kind:'tool',name:'Render Manim / Cairo',status:'running',detail:'scenes/s1.py'}],
 source:'class GeneratedScene(Scene): pass',streaming:true})};}};
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(path.join(__dirname,'../src/scene_design/web.js'),'utf8'),sandbox);
async function main(){
 vm.runInContext('renderSceneActivity(root,sceneJob(sid))',sandbox);
 const details=root.children[0];
 assert.match(details.children[0].textContent,/Kế hoạch và tool/);
 assert.equal(requests.length,0,'Closed activity must not poll');
 details.open=true;details.handlers.toggle();
 await new Promise(setImmediate);
 assert.equal(requests.length,1);
 assert.match(requests[0].url,/scene_id=s1/);
 assert.equal(requests[0].options.headers['X-Review-Token'],'session');
 assert.ok(!flatten(root).some(n=>String(n.textContent).includes('class GeneratedScene')));
 assert.ok(flatten(root).some(n=>n.textContent==='Kế hoạch thực hiện'));
 assert.ok(!flatten(root).some(n=>String(n.textContent).includes('v3_code_0')));
 assert.ok(flatten(root).some(n=>String(n.textContent).includes('Render Manim / Cairo')));
 assert.ok(flatten(root).some(n=>String(n.textContent).includes('Dựng hoặc chỉnh cảnh · Lượt 1 · Đã trả kết quả')));
 assert.equal(timers[0].ms,2000,'Activity updates even if workflow revision is unchanged');
 root.replaceChildren();vm.runInContext('renderSceneActivity(root,sceneJob(sid))',sandbox);
 assert.equal(root.children[0].open,true,'Open state survives progress rerenders');
 root.children[0].open=false;root.children[0].handlers.toggle();
 assert.equal(vm.runInContext('sceneActivityView.open',sandbox),false);
 console.log('PASS: activity opens, polls independently, displays plans/tools without source code, survives rerenders and closes.');
}
main();
