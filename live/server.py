"""直播弹幕长连客户端.

协议要点（公开且稳定）：
- 每个包 16 字节定长头：包长(4) 头长(2)=16 协议版本(2) 操作码(4) 序号(4)
- 操作码：2=心跳 3=心跳回复(人气值) 5=业务消息 7=认证 8=认证回复
- 协议版本：0=明文 JSON 1=人气值(int32) 2=zlib 3=brotli
- 认证包必须是连接后的第一个包，key 取自 getDanmuInfo 的 token
- 心跳 30 秒一次，断了服务端会主动关连接
"""

import json
import struct
import sys
import threading
import time
import zlib

import brotli
from websocket import WebSocketApp

from apis.bili_live_apis import BiliLiveApi
from builder.header import HeaderBuilder

# Windows 控制台默认 GBK，弹幕里的 emoji 会 UnicodeEncodeError
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

HEADER_STRUCT = struct.Struct('>IHHII')
HEADER_LEN = 16

OP_HEARTBEAT = 2
OP_HEARTBEAT_REPLY = 3
OP_MESSAGE = 5
OP_AUTH = 7
OP_AUTH_REPLY = 8

PROTO_JSON = 0
PROTO_POPULARITY = 1
PROTO_ZLIB = 2
PROTO_BROTLI = 3

HEARTBEAT_INTERVAL = 30


def pack(body: bytes, operation: int, protover: int = 1) -> bytes:
    """打一个协议包."""
    return HEADER_STRUCT.pack(HEADER_LEN + len(body), HEADER_LEN,
                              protover, operation, 1) + body


def unpack(data: bytes) -> list:
    """拆包，压缩包会递归展开，返回 (operation, payload) 列表."""
    packets = []
    offset = 0
    while offset < len(data):
        length, header_len, protover, operation, _ = HEADER_STRUCT.unpack_from(data, offset)
        body = data[offset + header_len:offset + length]
        if protover == PROTO_ZLIB:
            packets.extend(unpack(zlib.decompress(body)))
        elif protover == PROTO_BROTLI:
            packets.extend(unpack(brotli.decompress(body)))
        elif protover == PROTO_POPULARITY:
            packets.append((operation, int.from_bytes(body[:4], 'big') if body else 0))
        else:
            packets.append((operation, json.loads(body.decode('utf-8')) if body else {}))
        offset += length
    return packets


class BiliLiveDanmaku:
    """直播间弹幕监听.

    用法::

        client = BiliLiveDanmaku(auth, room_id=1)
        client.on('DANMU_MSG', lambda msg: print(msg))
        client.start(listen=60)
    """

    def __init__(self, auth, room_id):
        """
        :param auth: BiliAuth object，匿名即可，登录态会带上自己的 uid.
        :param room_id: 直播间短号或真实房间号，内部会自动转真实房间号.
        """
        self.auth = auth
        self.room_id = int(BiliLiveApi.get_room_init(auth, room_id)['data']['room_id'])
        self.ws = None
        self.handlers = {}
        self._stop = threading.Event()
        self._popularity = 0

    def on(self, cmd: str, handler):
        """注册消息回调.

        :param cmd: 业务消息的 cmd 字段，如 DANMU_MSG / SEND_GIFT / INTERACT_WORD；
                    传 '*' 表示兜底处理全部消息.
        :param handler: 单参可调用对象，收到的是解析后的 dict.
        """
        self.handlers.setdefault(cmd, []).append(handler)
        return self

    @property
    def popularity(self) -> int:
        """最近一次心跳回复里的人气值."""
        return self._popularity

    def start(self, listen: int = 0, print_danmaku: bool = True):
        """建立连接并开始收消息.

        :param listen: 监听秒数，0 表示一直听到 Ctrl+C.
        :param print_danmaku: 是否把弹幕打到控制台.
        """
        info = BiliLiveApi.get_danmu_info(self.auth, self.room_id)
        if info.get('code') != 0:
            raise RuntimeError(f'getDanmuInfo 失败: {info}')
        data = info['data']
        host = data['host_list'][0]
        url = f"wss://{host['host']}:{host['wss_port']}/sub"

        if print_danmaku:
            self.on('DANMU_MSG', self._print_danmaku)

        auth_body = json.dumps({
            'uid': int(self.auth.mid or 0),
            'roomid': self.room_id,
            'protover': PROTO_BROTLI,
            'buvid': self.auth.cookie.get('buvid3', ''),
            'platform': 'web',
            'type': 2,
            'key': data['token'],
        }, separators=(',', ':')).encode()

        self.ws = WebSocketApp(
            url,
            header={'User-Agent': HeaderBuilder.ua},
            cookie=self.auth.cookies_str,
            on_open=lambda ws: self._on_open(ws, auth_body),
            on_message=self._on_message,
            on_error=lambda ws, err: print(f'[ws error] {err}'),
            on_close=lambda ws, code, msg: print(f'[ws closed] {code} {msg}'),
        )
        if listen:
            threading.Timer(listen, self.stop).start()
        self.ws.run_forever(origin=BiliLiveApi.live)

    def stop(self):
        """主动断开."""
        self._stop.set()
        if self.ws:
            self.ws.close()

    # -------------------------------------------------------------- 内部实现

    def _on_open(self, ws, auth_body):
        ws.send(pack(auth_body, OP_AUTH, PROTO_JSON), opcode=0x02)
        threading.Thread(target=self._heartbeat, args=(ws,), daemon=True).start()

    def _heartbeat(self, ws):
        while not self._stop.is_set():
            try:
                ws.send(pack(b'[object Object]', OP_HEARTBEAT), opcode=0x02)
            except Exception:
                break
            self._stop.wait(HEARTBEAT_INTERVAL)

    def _on_message(self, ws, message):
        for operation, payload in unpack(message):
            if operation == OP_HEARTBEAT_REPLY:
                self._popularity = payload
            elif operation == OP_AUTH_REPLY:
                print(f'[ws auth] {payload}')
            elif operation == OP_MESSAGE:
                self._dispatch(payload)

    def _dispatch(self, payload: dict):
        cmd = (payload.get('cmd') or '').split(':')[0]
        for handler in self.handlers.get(cmd, []) + self.handlers.get('*', []):
            try:
                handler(payload)
            except Exception as e:
                print(f'[handler error] {cmd}: {e}')

    @staticmethod
    def _print_danmaku(payload: dict):
        info = payload.get('info') or []
        if len(info) > 2:
            print(f'{info[2][1]}: {info[1]}')
