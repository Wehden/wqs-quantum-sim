"""Resumable job events. Watching a job never starts or cancels computation."""
import asyncio
import json
import time
import uuid
from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import StreamingResponse
from . import settings, store
from .auth import identity

router=APIRouter()
connections={}
POLL_SECONDS=1
HEARTBEAT_SECONDS=10
MAX_STREAM_SECONDS=300


def snapshot(job_id,user,authorization,after):
    if identity(authorization)!=user:
        raise HTTPException(401,'Authentication required')
    with store.connect() as db:
        row=db.execute('SELECT * FROM jobs WHERE id=? AND owner=?',(job_id,user)).fetchone()
        if row is None:raise HTTPException(404,'Job not found')
        events=db.execute('SELECT sequence,payload FROM job_events WHERE job_id=? AND sequence>? ORDER BY sequence',(job_id,after)).fetchall()
        latest=db.execute('SELECT COALESCE(MAX(sequence),0) FROM job_events WHERE job_id=?',(job_id,)).fetchone()[0]
    return dict(row),[dict(event) for event in events],latest


def frame(event,data,sequence=None):
    prefix='' if sequence is None else f'id: {sequence}\n'
    return prefix+f'event: {event}\ndata: '+json.dumps(data,separators=(',',':'))+'\n\n'


def release(connection):
    connections.pop(connection,None)


async def event_stream(job_id,user,authorization,after,connection):
    started=time.monotonic();heartbeat=started
    try:
        yield ': connected\n\n'
        while True:
            try:
                row,events,_=await asyncio.to_thread(snapshot,job_id,user,authorization,after)
            except HTTPException as error:
                yield frame('error',{'code':error.status_code,'message':'Authentication required' if error.status_code==401 else 'Job no longer available'})
                return
            for event in events:
                payload=json.loads(event['payload']);status=payload['status']
                if status=='SUCCEEDED':
                    payload['result_url']='/api/jobs/'+job_id+'/result'
                    name='result'
                elif status in settings.TERMINAL:name='terminal'
                else:name='status'
                yield frame(name,payload,event['sequence'])
                after=event['sequence']
            if row['status'] in settings.TERMINAL:return
            now=time.monotonic()
            if now-started>=max(0,MAX_STREAM_SECONDS-2):
                yield frame('reconnect',{'message':'Reconnect with Last-Event-ID to continue watching this job'})
                return
            if now-heartbeat>=HEARTBEAT_SECONDS:
                yield ': keep-alive\n\n';heartbeat=now
            await asyncio.sleep(POLL_SECONDS)
    finally:
        release(connection)


class BoundedEventResponse(StreamingResponse):
    def __init__(self,*args,connection,**kwargs):
        super().__init__(*args,**kwargs)
        self.connection=connection

    async def __call__(self,scope,receive,send):
        try:
            # This includes network backpressure, not just generator polling time.
            async with asyncio.timeout(MAX_STREAM_SECONDS):
                await super().__call__(scope,receive,send)
        except TimeoutError:
            # A stalled client must reconnect using its last processed event ID.
            pass
        finally:
            try:
                await self.body_iterator.aclose()
            finally:
                release(self.connection)


@router.get('/api/jobs/{job_id}/events')
async def events(job_id:str,user=Depends(identity),authorization:str=Header(default=''),last_event_id:str=Header(default='0')):
    if not last_event_id.isascii() or not last_event_id.isdigit() or len(last_event_id)>10:
        raise HTTPException(400,'Last-Event-ID must be a nonnegative event sequence number')
    after=int(last_event_id)
    _,_,latest=await asyncio.to_thread(snapshot,job_id,user,authorization,after)
    if after>latest:raise HTTPException(400,'Last-Event-ID exceeds this job’s latest event')
    if len(connections)>=64 or sum(owner==user for owner in connections.values())>=8:
        raise HTTPException(429,'Too many open event streams')
    connection=uuid.uuid4().hex;connections[connection]=user
    return BoundedEventResponse(event_stream(job_id,user,authorization,after,connection),media_type='text/event-stream',
                             headers={'Cache-Control':'no-cache, no-transform','X-Accel-Buffering':'no'},
                             connection=connection)
