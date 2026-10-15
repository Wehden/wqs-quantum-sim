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
  for (const id of ['requests','review','operations','nav-jobs','nav-approvals','service-jobs','native-jobs','nav-service-jobs','nav-native-jobs','notice','who','refresh']) { const e=new Element('div');e.id=id; }
  const id='a'.repeat(32);
  const row={id,status:'PENDING',email:'caller@example.com',name:'Caller',purpose:'Testing',created:1,notification_state:'SENT',notification_attempts:1,delivery_state:'NONE',delivery_attempts:0};
  const context=vm.createContext({
    document:{getElementById:id=>elements.get(id),createElement:tag=>new Element(tag),get activeElement(){return active;}},
    location:{hash:'#approvals'},window:{addEventListener(){},dispatchEvent(){}},Event:class {},setInterval(){return 1;},clearInterval(){},confirm(){throw Error('refresh must never approve');},
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
  for(const id of ['hostname-rows','hostname-status','operations','job-metrics','resource-metrics','operations-status','resource-detail','job-rows','job-page','jobs-prev','jobs-next','worker-rows','native-job-rows','native-status','operations-refresh']) elements.set(id,new Element());
  let paths=[], missing=false;
  const data={jobs_by_hostname:{hosts:[{hostname:'node-a',launching:1,running:2,total:3}],unplaced:4,unknown:1,total_active:8,observed_at:100,stale:false},observed_at:100,counts:{QUEUED:3,SUCCEEDED:102},total:105,jobs:[{name:'<script>test</script>',id:'test',status:'RUNNING',hostname:'node-a',cluster:'qs-worker-id'}],skypilot:{observed_at:100,stale:false,current:{cpus:4,memory_gib:8,up_workers:2},history:{cpu_hours:12,memory_gib_hours:24,worker_hours:6,history_records:2},workers:[],jobs:[],queues_complete:true,queues_observed_at:100}};
  const context=vm.createContext({csrf:'ready',sessionReady:true,consolePage:()=> 'jobs',$:id=>elements.get(id),node:(tag,text)=>{const e=new Element();e.textContent=text;return e;},call:async path=>{paths.push(path);return {...data,skypilot:missing?{stale:true}:data.skypilot};},window:{addEventListener(){}},setInterval(){},Date});
  vm.runInContext(fs.readFileSync('qsim/admin_static/operations.js','utf8'),context);
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(elements.get('hostname-rows').children[0].children[0].textContent,'node-a');
  assert.equal(elements.get('hostname-rows').children[0].children[3].textContent,'3');
  assert.match(elements.get('hostname-status').textContent,/not yet assigned: 4/);
  assert.equal(elements.get('resource-metrics').children[0].children[1].textContent,'4');
  assert.equal(elements.get('job-rows').children[0].children[0].textContent,'<script>test</script>');
  assert.equal(elements.get('job-rows').children[0].children[4].textContent,'node-a');
  assert.equal(elements.get('jobs-prev').disabled,true);
  elements.get('jobs-next').onclick();await new Promise(resolve=>setImmediate(resolve));
  assert.equal(paths.at(-1),'operations?offset=100');
  missing=true;await vm.runInContext('refreshOperations()',context);
  assert.match(elements.get('operations-status').textContent,/unavailable or stale/);
  assert.equal(elements.get('resource-metrics').children[0].children[1].textContent,'Unavailable');
});

test('console pages preserve navigation during pending jobs and open approval deep links', async () => {
  const elements=new Map(), listeners=new Map(), paths=[];
  class Element {
    constructor(){this.children=[];this.attributes={};this.hidden=true;}
    set id(id){this._id=id;elements.set(id,this);} get id(){return this._id;}
    append(...children){this.children.push(...children);}
    replaceChildren(...children){this.children=children;}
    setAttribute(key,value){this.attributes[key]=value;}
    scrollIntoView(){} focus(){this.focused=true;}
  }
  const html=fs.readFileSync('qsim/admin_static/index.html','utf8');
  for(const match of html.matchAll(/id="([^"]+)"/g)){const e=new Element();e.id=match[1];}
  const id='b'.repeat(32), row={id,name:'Caller',email:'caller@example.com',purpose:'Testing',status:'APPROVED'};
  let release;const held=new Promise(resolve=>release=resolve);
  const context=vm.createContext({
    document:{getElementById:id=>elements.get(id),createElement:()=>new Element(),activeElement:null},
    location:{hash:''},Event:class {constructor(type){this.type=type;}},
    window:{addEventListener(type,fn){listeners.set(type,[...(listeners.get(type)||[]),fn]);},dispatchEvent(e){for(const fn of listeners.get(e.type)||[])fn();}},
    setInterval(){},clearInterval(){},confirm(){throw Error('Navigation must not approve');},
    fetch:async path=>{paths.push(path);let value;
      if(path==='./session')value={csrf:'test'};
      else if(path.startsWith('./operations')){await held;value={counts:{},jobs:[],total:0,skypilot:{stale:true}};}
      else value=path==='./requests'?[row]:row;
      return {ok:true,text:async()=>JSON.stringify(value)};
    }
  });
  for(const file of ['admin.js','operations.js'])vm.runInContext(fs.readFileSync('qsim/admin_static/'+file,'utf8'),context);
  const settle=()=>new Promise(resolve=>setImmediate(resolve));await settle();
  assert.equal(elements.get('operations').hidden,false);
  assert.equal(elements.get('review').hidden,true);
  assert.equal(paths.includes('./requests'),false);
  assert.equal(elements.get('service-jobs').hidden,false);
  assert.equal(elements.get('native-jobs').hidden,true);
  vm.runInContext("location.hash='#jobs-skypilot';window.dispatchEvent(new Event('hashchange'))",context);await settle();
  assert.equal(elements.get('operations').hidden,false);
  assert.equal(elements.get('service-jobs').hidden,true);
  assert.equal(elements.get('native-jobs').hidden,false);
  assert.equal(elements.get('nav-native-jobs').attributes['aria-current'],'page');
  vm.runInContext("location.hash='#approvals';window.dispatchEvent(new Event('hashchange'))",context);await settle();
  assert.equal(elements.get('review').hidden,false);
  release();await settle();
  assert.equal(elements.get('operations').hidden,true,'a pending job response must not reopen the jobs page');
  assert.equal(elements.get('nav-approvals').attributes['aria-current'],'page');
  const before=paths.length;await vm.runInContext('refreshOperations()',context);assert.equal(paths.length,before);
  vm.runInContext("location.hash='#jobs';window.dispatchEvent(new Event('hashchange'))",context);await settle();
  assert.equal(elements.get('service-jobs').hidden,false);
  assert.equal(elements.get('native-jobs').hidden,true);
  const after=paths.length;await vm.runInContext('refresh(true)',context);assert.equal(paths.length,after);
  vm.runInContext(`location.hash='#request-${id}';window.dispatchEvent(new Event('hashchange'))`,context);await settle();
  assert.equal(elements.get('review').hidden,false);
  assert.equal(elements.get('operations').hidden,true);
  assert.equal(elements.get('request-'+id).focused,true);
});
