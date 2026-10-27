"""Bounded adapters over cv-cat/BilibiliApis; no automatic posting."""
import base64
import io
import json
import os
from pathlib import Path
import re
import secrets
import time
from html import unescape
from urllib.parse import urlsplit
from apis.bili_apis import BiliApi
from apis.bili_login_apis import BiliLoginApi
from apis.bili_creator_apis import BiliCreatorApi
from builder.auth import BiliAuth
from .service import PlatformError, UncertainError


def text(value): return unescape(re.sub('<[^>]+>','',str(value or '')))


def image(value):
    value=str(value or '')
    if value.startswith('//'): value='https:'+value
    return value if value.startswith('https://') else ''


def checked(raw):
    if not isinstance(raw,dict) or raw.get('code') != 0:
        code=raw.get('code') if isinstance(raw,dict) else 'unknown'
        raise PlatformError(f'B站接口未确认请求（{code}），请检查登录或稍后再试')
    return raw.get('data') or {}


def projection(raw):
    owner=raw.get('owner') or {}; stats=raw.get('stat') or {}; bvid=str(raw.get('bvid') or '')
    return {'id':bvid,'aid':str(raw.get('aid') or raw.get('id') or ''),'title':text(raw.get('title')),
        'text':text(raw.get('desc') or raw.get('description')),'author':text(owner.get('name') or raw.get('author')),
        'uid':str(owner.get('mid') or raw.get('mid') or ''),'cover':image(raw.get('pic')),
        'url':'https://www.bilibili.com/video/'+bvid,'views':stats.get('view',raw.get('play',0)),
        'likes':stats.get('like',0),'comments':stats.get('reply',raw.get('review',0)),
        'created':raw.get('pubdate') or raw.get('created') or '', 'tags':'','note':''}


class Platform:
    def __init__(self,directory):
        self.path=Path(directory)/'account.json'
        self.auth=None
        self.qr={}

    def identity(self):
        if not self.path.exists(): return None
        return json.loads(self.path.read_text())['account']

    def persist(self,auth,account):
        payload={'cookie':auth.cookies_str,'refresh_token':auth.refresh_token,'account':account}
        temporary=self.path.with_suffix('.tmp')
        fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
        with os.fdopen(fd,'w') as out: json.dump(payload,out,ensure_ascii=False)
        os.replace(temporary,self.path)
        self.auth=auth
        return account

    @staticmethod
    def bounded(auth):
        auth.workspace_no_retry=True
        return auth

    def client(self):
        if self.auth is None:
            if self.path.exists():
                value=json.loads(self.path.read_text())
                self.auth=self.bounded(BiliAuth.from_cookie(value['cookie']))
                self.auth.refresh_token=value.get('refresh_token','')
            else:
                # Cookie factory performs device setup without login or posting.
                self.auth=self.bounded(BiliAuth.from_cookie(''))
        return self.auth

    def connect(self,cookie):
        auth=self.bounded(BiliAuth.from_cookie(cookie))
        nav=checked(BiliApi.get_nav(auth))
        if not nav.get('isLogin'): raise PlatformError('B站 Cookie 未登录或已失效')
        return self.persist(auth,{'id':str(nav['mid']),'name':text(nav.get('uname')),'status':'connected','checked_at':time.time()})

    def check(self):
        if not self.identity(): raise PlatformError('请先扫码或导入 Cookie')
        auth=self.client(); nav=checked(BiliApi.get_nav(auth))
        if not nav.get('isLogin'): raise PlatformError('登录已失效，请重新扫码')
        return self.persist(auth,{'id':str(nav['mid']),'name':text(nav.get('uname')),'status':'connected','checked_at':time.time()})

    def disconnect(self):
        self.path.unlink(missing_ok=True); self.auth=None; self.qr.clear()

    def search(self,keyword,page,order):
        data=checked(BiliApi.search_type(self.client(),keyword,order if order!='default' else 'totalrank',page))
        items=[projection(row) for row in data.get('result',[]) if row.get('bvid') and row.get('type','video')=='video']
        return {'items':items,'has_more':page<int(data.get('numPages') or page),'total':data.get('numResults',len(items))}

    @staticmethod
    def resolve(value):
        if re.fullmatch(r'BV[A-Za-z0-9]{10}',value): return value
        parsed=urlsplit(value)
        if parsed.scheme!='https' or parsed.hostname not in ('www.bilibili.com','bilibili.com') or parsed.port not in (None,443) or parsed.username or parsed.password:
            raise PlatformError('请输入 BV 号或 https://www.bilibili.com/video/BV… 链接')
        match=re.fullmatch(r'/video/(BV[A-Za-z0-9]{10})/?',parsed.path)
        if not match: raise PlatformError('链接中没有有效 BV 号')
        return match.group(1)

    def detail(self,value): return projection(checked(BiliApi.get_video_info(self.client(),bvid=self.resolve(value))))

    def comments(self,item):
        data=checked(BiliApi.get_replies(self.client(),item['aid']))
        return {'comments':[{'id':str(row.get('rpid','')),'author':text((row.get('member') or {}).get('uname')),
            'text':text((row.get('content') or {}).get('message')),'likes':row.get('like',0)} for row in data.get('replies') or []],
            'notice':'展示当前页评论'}

    def user(self,uid,page):
        data=checked(BiliApi.get_user_videos(self.client(),uid,page=page,page_size=30))
        items=[projection(row) for row in (data.get('list') or {}).get('vlist',[])]
        return {'items':items,'page':page,'has_more':page*30<int((data.get('page') or {}).get('count',0))}

    def publish(self,content):
        if not self.identity(): raise PlatformError('请先连接B站账号')
        ok,_,raw=BiliCreatorApi.post_dynamic(self.client(),content)
        if not ok: raise UncertainError()
        data=checked(raw); did=str(data.get('dyn_id_str') or data.get('dyn_id') or '')
        if not did: raise UncertainError()
        return {'id':did,'url':'https://t.bilibili.com/'+did}

    def post_comment(self, item, body, parent_id=''):
        from apis.bili_interact_apis import BiliInteractApi
        if not self.identity(): raise PlatformError('请先连接B站账号')
        aid=str(item.get('aid',''))
        if not aid.isdigit() or (parent_id and not str(parent_id).isdigit()): raise PlatformError('评论目标无效')
        try:
            raw=BiliInteractApi.add_reply(self.client(),aid,body,root=int(parent_id or 0))
        except Exception as exc:
            raise UncertainError() from exc
        data=checked(raw)
        rpid=str(data.get('rpid') or (data.get('reply') or {}).get('rpid') or '')
        if not rpid: raise UncertainError()
        return {'id':rpid,'url':item.get('url',''),'notice':'平台接受了评论，请在原文检查可见性'}

    def qr_start(self):
        import qrcode
        # A separate auth object prevents an unfinished login from changing the current account.
        auth=self.bounded(BiliAuth.from_cookie(''))
        data=checked(BiliLoginApi.qrcode_generate(auth))
        buffer=io.BytesIO(); qrcode.make(data['url']).save(buffer,format='PNG')
        token=secrets.token_urlsafe(24)
        self.qr={token:{'auth':auth,'key':data['qrcode_key'],'expires':time.time()+180,'last_poll':0}}
        return {'token':token,'image':'data:image/png;base64,'+base64.b64encode(buffer.getvalue()).decode(),'status':'waiting'}

    def qr_poll(self,token):
        value=self.qr.get(token)
        if not value or time.time()>value['expires']: return {'status':'expired'}
        if time.time()-value['last_poll']<2: return {'status':'waiting'}
        value['last_poll']=time.time()
        raw,cookies=BiliLoginApi.qrcode_poll(value['auth'],value['key'])
        data=checked(raw); code=data.get('code')
        if code==0:
            auth=value['auth']; auth.update_cookies(cookies); auth.refresh_token=data.get('refresh_token','')
            nav=checked(BiliApi.get_nav(auth))
            if not nav.get('isLogin'): raise PlatformError('扫码后未确认登录状态，请重新扫码')
            account=self.persist(auth,{'id':str(nav['mid']),'name':text(nav.get('uname')),'status':'connected','checked_at':time.time()})
            self.qr.clear()
            return {'status':'connected','account':account}
        return {'status':{86090:'scanned',86101:'waiting',86038:'expired'}.get(code,'expired')}

    def tool(self,name,data):
        if name=='profile':
            uid=str(data.get('uid','')).strip()
            if not re.fullmatch(r'\d{1,24}',uid): raise PlatformError('请输入数字 UID')
            raw=checked(BiliApi.get_user_info(self.client(),uid))
            if not raw.get('mid'): raise PlatformError('未读取到 UP 主画像')
            official=raw.get('official') or {}
            return {'items':[{'id':'profile-'+uid,'kind':'profile','title':text(raw.get('name')),'author':text(raw.get('name')),
                'uid':uid,'text':text(raw.get('sign'))+'\n等级：'+str(raw.get('level','—'))+' · '+text(official.get('title')),
                'url':'https://space.bilibili.com/'+uid,'cover':image(raw.get('face')),'views':'—','likes':'—','comments':'—'}],
                'notice':'UP 主公开画像；可点击作者作品继续研究'}
        if name=='live':
            from apis.bili_live_apis import BiliLiveApi
            room=str(data.get('room_id','')).strip()
            if not re.fullmatch(r'\d{1,24}',room): raise PlatformError('请输入数字房间号')
            raw=checked(BiliLiveApi.get_room_info(self.client(),room))
            info=raw.get('room_info') or {}; anchor=(raw.get('anchor_info') or {}).get('base_info') or {}
            if not info.get('room_id'): raise PlatformError('未读取到直播间信息')
            return {'items':[{'id':'live-'+str(info['room_id']),'kind':'live','title':text(info.get('title')),
                'author':text(anchor.get('uname')),'uid':str(info.get('uid') or ''),'cover':image(info.get('cover')),
                'url':'https://live.bilibili.com/'+str(info['room_id']),'views':info.get('online',0),'likes':'—','comments':'—',
                'text':('正在直播' if info.get('live_status')==1 else '当前未开播')+' · '+text(info.get('area_name'))}],
                'notice':'人气为平台返回的 online 指标，不等同于独立观众数'}
        if name=='recommend':
            raw=checked(BiliApi.get_rcmd_feed(self.client()))
            return {'items':[projection(x) for x in raw.get('item') or [] if x.get('bvid')], 'notice':'当前会话推荐视频，单次读取'}
        try: page=int(data.get('page',1))
        except (ValueError,TypeError):raise PlatformError('请输入有效页码')
        if not 1<=page<=50:raise PlatformError('页码需要 1–50')
        if name=='popular':
            raw=checked(BiliApi.get_popular(self.client(),page=page,page_size=20))
            return {'items':[projection(x) for x in raw.get('list') or []],'notice':'B站热门视频 · 第 '+str(page)+' 页'}
        if name=='archives':
            if not self.identity():raise PlatformError('请先连接B站账号')
            raw=checked(BiliCreatorApi.get_my_archives(self.client(),page=page,page_size=20))
            items=[]
            for row in raw.get('arc_audits') or []:
                archive=row.get('Archive') or row.get('archive') or row
                if archive.get('bvid'):items.append(projection(archive))
            return {'items':items,'notice':'当前账号投稿列表；稿件管理请使用官方创作者中心'}
        raise PlatformError('工具不存在')
