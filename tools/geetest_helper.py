"""极验 v3 本地过验证助手.

B站 短信登录需要一次极验。本模块保留官方控件人工对照通路；默认纯算入口见
``tools.geetest_solve``。页面把 validate / seccode 回传给后续流程。

    python -m tools.geetest_helper --gt <gt> --challenge <challenge>

浏览器打开 http://127.0.0.1:8777 完成验证，脚本会把结果打印出来并退出。
"""

import argparse
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

PAGE = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>B站登录 - 人机验证</title>
{patched}<script src="https://static.geetest.com/static/tools/gt.js"></script>
<style>
  body {{ font-family: -apple-system, "Microsoft YaHei", sans-serif; background:#f6f7f9;
         display:flex; align-items:center; justify-content:center; height:100vh; margin:0; }}
  .card {{ background:#fff; padding:32px 40px; border-radius:12px; text-align:center;
           box-shadow:0 8px 30px rgba(0,0,0,.08); min-width:360px; }}
  h1 {{ font-size:18px; margin:0 0 6px; color:#18191c; }}
  p  {{ font-size:13px; color:#9499a0; margin:0 0 20px; }}
  #done {{ display:none; color:#00a1d6; font-size:15px; font-weight:600; margin-top:16px; }}
</style>
</head>
<body>
<div class="card">
  <h1>完成人机验证</h1>
  <p>验证通过后本页会自动回传结果，可直接关闭</p>
  <div id="captcha"></div>
  <div id="done">验证已通过，结果已回传，可以关闭本页</div>
</div>
<script>
initGeetest({{
  gt: "{gt}",
  challenge: "{challenge}",
  offline: false,
  new_captcha: true,
  product: "bind",
  https: true
}}, function (captchaObj) {{
  captchaObj.onReady(function () {{ captchaObj.verify(); }});
  captchaObj.onSuccess(function () {{
    var r = captchaObj.getValidate();
    window.__geetest_result = r;
    fetch("/result", {{ method: "POST", body: JSON.stringify(r) }});
    document.getElementById("done").style.display = "block";
  }});
}});
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    page = ''
    result = {}
    patched_js = b''
    done = threading.Event()

    def do_GET(self):
        path = urlparse(self.path).path
        if path == '/poll':
            self._json(Handler.result)
            return
        if path == '/fp.js':
            # 本地打过探针的 fullpage.js，先于 gt.js 执行以抢占 window.Geetest.fullpage
            self.send_response(200)
            self.send_header('Content-Type', 'application/javascript; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', str(len(Handler.patched_js)))
            self.end_headers()
            self.wfile.write(Handler.patched_js)
            return
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        # 每轮 challenge 只能用一次，页面被缓存会导致 GeetestError: old challenge
        self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate')
        self.send_header('Pragma', 'no-cache')
        self.end_headers()
        self.wfile.write(Handler.page.encode('utf-8'))

    def do_POST(self):
        length = int(self.headers.get('Content-Length') or 0)
        Handler.result = json.loads(self.rfile.read(length) or b'{}')
        Handler.done.set()
        self._json({'ok': True})

    def _json(self, payload):
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class SingleBindServer(HTTPServer):
    # Windows 下默认的 SO_REUSEADDR 会让两个进程同时绑同一端口，
    # 旧进程抢走流量后新 challenge 永远送不出去（表现为 GeetestError: old challenge）
    allow_reuse_address = False


# 只有在要抓明文载荷时才需要把 CDN 的 fullpage.js 换成本地打过探针的版本。
# gt.js 会自己去 CDN 拉一份并覆盖同名全局，光"先加载本地版"没用，
# 得改写它创建的 script 标签的 src。
# ⚠️ 这段**必须**只在提供了 patched_js 时注入：否则 /fp.js 返回空内容，
# window.Geetest 永远不会被定义，页面报 "Geetest is not a constructor"。
REDIRECT_JS = """<script>
(function () {
  var origCreate = document.createElement.bind(document);
  var desc = Object.getOwnPropertyDescriptor(HTMLScriptElement.prototype, 'src');
  document.createElement = function (tag) {
    var el = origCreate(tag);
    if (String(tag).toLowerCase() === 'script') {
      Object.defineProperty(el, 'src', {
        set: function (v) {
          if (/fullpage\\.[\\w.\\-]+\\.js/.test(v)) v = '/fp.js';
          desc.set.call(this, v);
        },
        get: function () { return desc.get.call(this); },
        configurable: true
      });
    }
    return el;
  };
})();
</script>
"""


def serve(gt: str, challenge: str, port: int, timeout: int,
          patched_js: str = '') -> dict:
    tag = ''
    if patched_js:
        with open(patched_js, 'rb') as f:
            Handler.patched_js = f.read()
        tag = REDIRECT_JS
    Handler.result = {}
    Handler.done.clear()
    Handler.page = PAGE.format(gt=gt, challenge=challenge, patched=tag)
    server = SingleBindServer(('127.0.0.1', port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f'请在浏览器打开 http://127.0.0.1:{port} 完成人机验证（{timeout} 秒内）')
    Handler.done.wait(timeout)
    server.shutdown()
    return Handler.result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='极验 v3 本地过验证助手')
    parser.add_argument('--gt', required=True)
    parser.add_argument('--challenge', required=True)
    parser.add_argument('--port', type=int, default=8777)
    parser.add_argument('--timeout', type=int, default=300)
    parser.add_argument('--patched-js', default='',
                        help='本地打过探针的 fullpage.js，用于导出明文载荷')
    args = parser.parse_args()

    result = serve(args.gt, args.challenge, args.port, args.timeout, args.patched_js)
    if not result:
        raise SystemExit('超时，未收到验证结果')
    print(json.dumps(result, ensure_ascii=False, indent=2))
