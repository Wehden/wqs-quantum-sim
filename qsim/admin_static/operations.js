let operationsOffset = 0, operationsBusy = false;
const metric = (label, value) => {
  const card=node('div','','metric');card.append(node('span',label),node('strong',value));return card;
};
const amount = value => value == null ? 'Unavailable' : Number(value).toLocaleString(undefined,{maximumFractionDigits:2});
const date = value => value ? new Date(value*1000).toLocaleString() : 'Not yet observed';
function tableRows(target, rows, columns) {
  target.replaceChildren();
  for(const row of rows) {const tr=node('tr','');for(const column of columns) tr.append(node('td',String(column(row) ?? '—')));target.append(tr);}
}
async function refreshOperations() {
  if(!sessionReady || consolePage() !== 'jobs' || operationsBusy) return;
  operationsBusy=true;
  try {
    const data=await call('operations?offset='+operationsOffset), sky=data.skypilot, counts=data.counts;
    $('job-metrics').replaceChildren(...['QUEUED','LAUNCHING','RUNNING','SUCCEEDED','FAILED','CANCELLED'].map(s=>metric(s,amount(counts[s] || 0))));
    $('resource-metrics').replaceChildren(
      metric('CPUs allocated · UP workers',amount(sky.current?.cpus)),
      metric('GiB allocated · UP workers',amount(sky.current?.memory_gib)),
      metric('CPU-hours · history',amount(sky.history?.cpu_hours)),
      metric('GiB-hours · history',amount(sky.history?.memory_gib_hours)));
    $('operations-status').textContent=`Jobs updated ${date(data.observed_at)}. SkyPilot observed ${date(sky.observed_at)}.${sky.stale ? ' SkyPilot data unavailable or stale; values may be outdated.' : ''}${sky.last_error ? ' Last collection failed: '+sky.last_error+'.' : ''}`;
    $('operations-status').className=sky.stale?'error':'status';
    $('resource-detail').textContent=`${sky.current ? sky.current.up_workers+' UP workers. ' : ''}SkyPilot 30-day history: ${sky.history ? sky.history.history_records+' worker records; '+amount(sky.history.worker_hours)+' worker-hours. ' : 'unavailable. '}${sky.history?.unknown_resource_records || sky.current?.unknown_resource_workers ? 'Some resource values are unknown; totals are partial. ' : ''}Allocated resource time includes idle workers. It is not measured CPU or RAM utilization; Kubernetes billing cost is not inferred.`;
    tableRows($('job-rows'),data.jobs,[r=>r.name,r=>r.id,r=>r.owner,r=>r.status,r=>r.cluster,r=>date(r.updated),r=>r.error]);
    $('job-page').textContent=`${data.total ? operationsOffset+1 : 0}–${Math.min(operationsOffset+data.jobs.length,data.total)} of ${data.total} saved jobs; active jobs first.`;
    $('jobs-prev').disabled=operationsOffset===0;$('jobs-next').disabled=operationsOffset+data.jobs.length>=data.total;
    tableRows($('worker-rows'),sky.workers || [],[r=>r.name,r=>r.status,r=>amount(r.cpus),r=>amount(r.memory_gib)]);
    tableRows($('native-job-rows'),sky.jobs || [],[r=>r.cluster,r=>r.job_id,r=>r.status,r=>date(r.start_at)]);
    $('native-status').textContent=!sky.observed_at ? 'Waiting for SkyPilot.' : !sky.queues_complete ? 'Native job queues are still being collected.' : `Native queues observed ${date(sky.queues_observed_at)}. ${(sky.queue_errors || []).length} worker queues unavailable. ${sky.jobs?.length ? '' : 'No active native jobs reported by responding workers.'}`;
  } catch(error) {$('operations-status').textContent=error.message;$('operations-status').className='error';}
  finally {operationsBusy=false;}
}
$('jobs-prev').onclick=()=>{operationsOffset=Math.max(0,operationsOffset-100);refreshOperations();};
$('jobs-next').onclick=()=>{operationsOffset+=100;refreshOperations();};
$('operations-refresh').onclick=refreshOperations;
let operationsStarted=false;
function startOperations(){if(operationsStarted)return;operationsStarted=true;refreshOperations();setInterval(refreshOperations,15000);}
window.addEventListener('qsim-session',startOperations);
window.addEventListener('qsim-page',refreshOperations);
if(csrf)startOperations();
