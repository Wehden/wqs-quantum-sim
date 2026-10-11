const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');

test('identity drafts survive refresh, editing defers polling, and rollout errors recover', async () => {
  const elements = new Map();
  let active = null, fetches = 0, unavailable = false, hold = null;
  class Element {
    constructor(tag) { this.tag=tag; this.children=[]; this.textContent=''; }
    set id(value) { this._id=value; elements.set(value,this); }
    get id() { return this._id; }
    append(...children) { this.children.push(...children); }
    replaceChildren(...children) { this.children=children; }
    setAttribute() {}
    focus() { active=this; }
    scrollIntoView() {}
  }
  for (const id of ['requests','review','notice','who','refresh']) { const e=new Element('div');e.id=id; }
  const id='a'.repeat(32);
  const row={id,status:'PENDING',email:'caller@example.com',name:'Caller',purpose:'Testing',created:1,notification_state:'SENT',notification_attempts:1,delivery_state:'NONE',delivery_attempts:0};
  const context=vm.createContext({
    document:{getElementById:id=>elements.get(id),createElement:tag=>new Element(tag),get activeElement(){return active;}},
    location:{hash:''},window:{addEventListener(){},dispatchEvent(){}},Event:class {},setInterval(){return 1;},clearInterval(){},confirm(){throw Error('refresh must never approve');},
    fetch:async path=>{
      fetches++;
      if(hold && path==='./requests') await hold;
      return {ok:!unavailable,status:unavailable?503:200,text:async()=>unavailable?'no available server':JSON.stringify(path==='./session'?{csrf:'test'}:[row])};
    }
  });
  vm.runInContext(fs.readFileSync('qsim/admin_static/admin.js','utf8'),context);
  await new Promise(resolve=>setImmediate(resolve));
  let input=elements.get('identity-'+id);
  input.value='chosen-identity';input.oninput();input.focus();
  const before=fetches;
  await vm.runInContext('refresh(true)',context);
  assert.equal(fetches,before);
  assert.equal(elements.get('identity-'+id),input);
  active=null;
  await vm.runInContext('refresh()',context);
  assert.equal(elements.get('identity-'+id).value,'chosen-identity');
  // Starting to edit during a pending network request also preserves the node/focus.
  let release;hold=new Promise(resolve=>release=resolve);
  const pending=vm.runInContext('refresh(true)',context);
  input=elements.get('identity-'+id);input.focus();release();await pending;hold=null;
  assert.equal(elements.get('identity-'+id),input);
  active=null;unavailable=true;
  await vm.runInContext("refresh().catch(error=>$('notice').textContent=error.message)",context);
  assert.match(elements.get('notice').textContent,/temporarily unavailable/);
  assert.doesNotMatch(elements.get('notice').textContent,/Unexpected token|no available server/);
  unavailable=false;
  await vm.runInContext('refresh()',context);
  assert.equal(elements.get('notice').textContent,'');
  assert.equal(elements.get('identity-'+id).value,'chosen-identity');
});

test('operations shows native totals, missing-data warning and paginates jobs', async () => {
  const elements=new Map();
  class Element {
    constructor(){this.children=[];this.textContent='';}
    append(...children){this.children.push(...children);}
    replaceChildren(...children){this.children=children;}
  }
  for(const id of ['operations','job-metrics','resource-metrics','operations-status','resource-detail','job-rows','job-page','jobs-prev','jobs-next','worker-rows','native-job-rows','native-status','operations-refresh']) elements.set(id,new Element());
  let paths=[], missing=false;
  const data={observed_at:100,counts:{QUEUED:3,SUCCEEDED:102},total:105,jobs:[{name:'<script>test</script>',id:'test',status:'QUEUED'}],skypilot:{observed_at:100,stale:false,current:{cpus:4,memory_gib:8,up_workers:2},history:{cpu_hours:12,memory_gib_hours:24,worker_hours:6,history_records:2},workers:[],jobs:[],queues_complete:true,queues_observed_at:100}};
  const context=vm.createContext({csrf:'ready',$:id=>elements.get(id),node:(tag,text)=>{const e=new Element();e.textContent=text;return e;},call:async path=>{paths.push(path);return {...data,skypilot:missing?{stale:true}:data.skypilot};},window:{addEventListener(){}},setInterval(){},Date});
  vm.runInContext(fs.readFileSync('qsim/admin_static/operations.js','utf8'),context);
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(elements.get('resource-metrics').children[0].children[1].textContent,'4');
  assert.equal(elements.get('job-rows').children[0].children[0].textContent,'<script>test</script>');
  assert.equal(elements.get('jobs-prev').disabled,true);
  elements.get('jobs-next').onclick();await new Promise(resolve=>setImmediate(resolve));
  assert.equal(paths.at(-1),'operations?offset=100');
  missing=true;await vm.runInContext('refreshOperations()',context);
  assert.match(elements.get('operations-status').textContent,/unavailable or stale/);
  assert.equal(elements.get('resource-metrics').children[0].children[1].textContent,'Unavailable');
});
