"""Bound request bodies and add headers without buffering response streams."""
from starlette.datastructures import MutableHeaders
from starlette.responses import JSONResponse
from . import settings


class HttpBoundary:
    def __init__(self,app):
        self.app=app

    async def __call__(self,scope,receive,send):
        if scope['type']!='http':
            return await self.app(scope,receive,send)

        async def guarded_send(message):
            if message['type']=='http.response.start':
                headers=MutableHeaders(scope=message)
                headers['X-Content-Type-Options']='nosniff'
                headers['Referrer-Policy']='no-referrer'
                headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self'; frame-ancestors 'none'; base-uri 'none'"
                headers.setdefault('Cache-Control','no-store')
            await send(message)

        chunks=[];size=0
        while True:
            message=await receive()
            if message['type']=='http.disconnect':return
            chunk=message.get('body',b'');size+=len(chunk)
            if size>settings.MAX_BODY:
                return await JSONResponse({'detail':'Request too large'},status_code=413)(scope,receive,guarded_send)
            chunks.append(chunk)
            if not message.get('more_body',False):break
        consumed=False

        async def buffered_receive():
            nonlocal consumed
            if not consumed:
                consumed=True
                return {'type':'http.request','body':b''.join(chunks),'more_body':False}
            return await receive()

        await self.app(scope,buffered_receive,guarded_send)
