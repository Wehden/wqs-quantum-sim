let csrf = '', timer, focusedRequest = '', sessionReady = false;
const consolePage = () => location.hash === '#approvals' || /^#request-[a-f0-9]{32}$/.test(location.hash) ? 'approvals' : 'jobs';
function showConsolePage() {
  const approvals = consolePage() === 'approvals';
  $('review').hidden = !sessionReady || !approvals;
  $('operations').hidden = !sessionReady || approvals;
  $('nav-jobs').setAttribute('aria-current', approvals ? 'false' : 'page');
  $('nav-approvals').setAttribute('aria-current', approvals ? 'page' : 'false');
  if (!sessionReady) return;
  if (approvals) refresh().catch(error => $('notice').textContent = error.message);
  window.dispatchEvent(new Event('qsim-page'));
}
const identityDrafts = new Map();
const editingIdentity = () => document.activeElement?.id?.startsWith('identity-');
const linkedRequest = () => /^#request-([a-f0-9]{32})$/.exec(location.hash)?.[1];
function focusLinkedRequest() {
  const id=linkedRequest(), card=id && document.getElementById('request-'+id);
  if(card && focusedRequest!==id) {card.scrollIntoView({block:'center'});card.focus({preventScroll:true});focusedRequest=id;}
}
const $ = id => document.getElementById(id);
const node = (tag, text, cls) => { const n = document.createElement(tag); n.textContent = text; if(cls) n.className = cls; return n; };
async function call(path, body) {
  const response = await fetch('./' + path, {method:body === undefined ? 'GET':'POST',headers:{'Content-Type':'application/json','X-Qsim-CSRF':csrf},...(body === undefined ? {}:{body:JSON.stringify(body)})});
  let value;
  try {value = JSON.parse(await response.text());}
  catch {throw new Error('Console temporarily unavailable. Please retry shortly.');}
  if(!response.ok) {
    if(response.status === 401) { sessionReady=false; clearInterval(timer);$('review').hidden=true;const panel=$('operations');if(panel)panel.hidden=true; }
    throw new Error(typeof value.detail === 'string' ? value.detail:'Request could not be completed');
  }
  return value;
}
async function action(id, name, body={}) {
  try { await call(`requests/${id}/${name}`,body);$('notice').textContent='Saved';await refresh(); }
  catch(error) { $('notice').textContent=error.message; }
}
async function refresh(automatic=false) {
  if(!sessionReady || consolePage() !== 'approvals') return;
  if(automatic && editingIdentity()) return;
  const rows=await call('requests'), linked=linkedRequest();
  let linkedError='';
  if(linked && !rows.some(row=>row.id===linked)) {
    try {rows.unshift(await call('requests/'+linked));}
    catch(error) {linkedError='Linked request: '+error.message;}
  }
  // The operator may have started editing while the fetch was in flight.
  if(automatic && editingIdentity()) return;
  $('notice').textContent=linkedError;
  $('requests').replaceChildren();
  if(!rows.length) $('requests').append(node('p','No access requests yet.'));
  for(const row of rows) {
    const card=node('article','','request');card.id='request-'+row.id;card.tabIndex=-1;
    card.append(node('h2',row.name),node('p',row.email),node('p',row.purpose,'purpose'));
    card.append(node('p',`${row.status} · ${new Date(row.created*1000).toLocaleString()} · ${row.id}`,'status'));
    card.append(node('p',`Review notice: ${row.notification_state} (${row.notification_attempts} attempts) · Key delivery: ${row.delivery_state} (${row.delivery_attempts} attempts)`));
    if(row.notification_error || row.delivery_error) card.append(node('p',[row.notification_error,row.delivery_error].filter(Boolean).join(' · '),'error'));
    const actions=node('div','','actions');
    if(['PENDING','APPROVING'].includes(row.status)) {
      const identity=node('input','');identity.value=row.identity || (identityDrafts.get(row.id) ?? row.email.split('@')[0].replace(/[^a-zA-Z0-9_.+-]/g,'-'));identity.oninput=()=>identityDrafts.set(row.id,identity.value);identity.maxLength=128;identity.setAttribute('aria-label',`New identity for ${row.email}`);if(row.status==='APPROVING') identity.readOnly=true;
      const identityField=node('div','','identity-field');identity.id='identity-'+row.id;
      const identityLabel=node('label','API identity');identityLabel.htmlFor=identity.id;
      const identityHelp=node('small','Groups this caller’s API jobs. Suggested from email; must be unique.');identityHelp.id='identity-help-'+row.id;identity.setAttribute('aria-describedby',identityHelp.id);
      identityField.append(identityLabel,identity,identityHelp);
      const approve=node('button',row.status==='APPROVING'?'Resume approval':'Approve & email key');
      approve.onclick=()=>{if(confirm(`Approve ${row.email} as ${identity.value} and email the issued key to that address?`)) action(row.id,'approve',{identity:identity.value});};actions.append(identityField,approve);
      if(row.status==='PENDING') {const reject=node('button','Reject');reject.onclick=()=>action(row.id,'reject');actions.append(reject);}
    }
    if(row.status==='PENDING' && ['FAILED','RETRY'].includes(row.notification_state)) { const retry=node('button','Retry review notice');retry.onclick=()=>action(row.id,'notify');actions.append(retry); }
    if(row.status==='APPROVED' && ['FAILED','RETRY','UNCERTAIN'].includes(row.delivery_state)) {const retry=node('button','Retry delivery of same key');retry.onclick=()=>action(row.id,'retry-delivery');actions.append(retry);}
    card.append(actions);$('requests').append(card);
  }
  focusLinkedRequest();
}
window.addEventListener('hashchange',()=>{focusedRequest='';$('notice').textContent='';showConsolePage();});
async function signedIn(user) {csrf=user.csrf;$('who').textContent='Internal network operator session';sessionReady=true;showConsolePage();clearInterval(timer);timer=setInterval(()=>refresh(true).catch(e=>$('notice').textContent=e.message),15000);window.dispatchEvent(new Event('qsim-session'));}
$('refresh').onclick=()=>refresh().catch(e=>$('notice').textContent=e.message);
call('session',{}).then(signedIn).catch(error=>{$('notice').textContent=error.message;});
