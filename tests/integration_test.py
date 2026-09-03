#!/usr/bin/env python3
"""DocHub 联调（集成）测试脚本
运行方式：python3 tests/integration_test.py [base_url]
默认 base_url: http://127.0.0.1:5000
说明：使用标准库 urllib，不依赖第三方包。通过 cookie 保持会话状态，
覆盖页面路由、认证流程、业务流、API 接口与错误处理。
"""
import sys
import re
import json
import urllib.request
import urllib.parse
import http.cookiejar
import http.client
from urllib.error import HTTPError, URLError

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:5000"

results = []  # (case, expected, actual, status)


def record(case, ok, detail=""):
    results.append((case, ok, detail))
    print(("PASS" if ok else "FAIL"), "|", case, ("| " + detail if detail else ""))


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """不跟随重定向，直接返回 301/302 响应"""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Session:
    def __init__(self):
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar),
            NoRedirect()
        )
        self.csrf_token = ''

    def _extract_csrf(self, html):
        """从 HTML 中提取 CSRF token（hidden input 或 meta 标签）"""
        m = re.search(r'name="csrf_token"\s+value="([^"]+)"', html)
        if m:
            self.csrf_token = m.group(1)
            return
        m = re.search(r'<meta\s+name="csrf-token"\s+content="([^"]+)"', html)
        if m:
            self.csrf_token = m.group(1)

    def request(self, method, path, data=None, headers=None):
        # 对 URL 中非 ASCII 字符做百分号编码（用于含中文的 GET 查询）
        if "?" in path:
            p, q = path.split("?", 1)
            path = p + "?" + urllib.parse.quote(q, safe="=&%")
        url = BASE + path
        body = None
        h = {"User-Agent": "dochub-integration-test"}
        if data is not None:
            # POST 自动注入 CSRF token
            if method in ('POST', 'PUT', 'DELETE', 'PATCH') and self.csrf_token:
                if 'csrf_token' not in data:
                    data = dict(data)
                    data['csrf_token'] = self.csrf_token
            body = urllib.parse.urlencode(data).encode()
            h["Content-Type"] = "application/x-www-form-urlencoded"
        if headers:
            h.update(headers)
        req = urllib.request.Request(url, data=body, headers=h, method=method)
        try:
            resp = self.opener.open(req, timeout=10)
            text = resp.read().decode("utf-8", "replace")
            if method == 'GET':
                self._extract_csrf(text)
            return resp.status, text
        except HTTPError as e:
            text = e.read().decode("utf-8", "replace")
            if method == 'GET':
                self._extract_csrf(text)
            return e.code, text
        except URLError as e:
            return -1, str(e)


s = Session()
fail_count = 0


def check(case, ok, detail=""):
    global fail_count
    record(case, ok, detail)
    if not ok:
        fail_count += 1


# ============================================================
# 1. 公开页面冒烟测试
# ============================================================
print("\n===== 1. 公开页面路由 =====")
code, html = s.request("GET", "/")
check("GET / (首页)", code == 200 and "DocHub" in html, f"status={code}")

code, html = s.request("GET", "/search?q=路由器")
check("GET /search?q=路由器", code == 200 and "路由器" in html, f"status={code}")

code, html = s.request("GET", "/search")
check("GET /search (空关键词)", code == 200, f"status={code}")

code, html = s.request("GET", "/categories")
check("GET /categories", code == 200 and "分类" in html, f"status={code}")

code, html = s.request("GET", "/doc/1")
check("GET /doc/1 (文档阅读页)", code == 200 and "小米路由器" in html, f"status={code}")

code, html = s.request("GET", "/doc/1/download")
check("GET /doc/1/download (下载)", code == 200, f"status={code}")

code, html = s.request("GET", "/community")
check("GET /community (社区)", code == 200, f"status={code}")

# 登录注册页
code, html = s.request("GET", "/auth/login")
check("GET /auth/login", code == 200, f"status={code}")

code, html = s.request("GET", "/auth/register")
check("GET /auth/register", code == 200, f"status={code}")

# API
code, body = s.request("GET", "/api/search/suggest?q=路由")
try:
    data = json.loads(body)
    api_ok = code == 200 and "results" in data and len(data["results"]) > 0
except Exception:
    api_ok = False
    data = {}
check("GET /api/search/suggest?q=路由", api_ok, f"status={code}, results={len(data.get('results', []))}")

code, body = s.request("GET", "/api/categories")
try:
    data = json.loads(body)
    api_ok = code == 200 and data.get("total", 0) > 0
except Exception:
    api_ok = False
    data = {}
check("GET /api/categories", api_ok, f"status={code}, total={data.get('total', '?')}")

# ============================================================
# 2. 认证流程
# ============================================================
print("\n===== 2. 认证流程 =====")
# 未登录访问受保护页 → 应重定向到登录
code, html = s.request("GET", "/profile")
check("未登录访问 /profile 被拦截", code in (301, 302), f"status={code}")

# 注册新用户
import uuid
suffix = uuid.uuid4().hex[:8]
test_user = f"tester_{suffix}"
test_email = f"{test_user}@test.com"
code, html = s.request("POST", "/auth/register", data={
    "username": test_user, "email": test_email,
    "password": "test123456", "confirm_password": "test123456",
})
check("POST /auth/register (注册成功)", code in (301, 302, 200), f"status={code}")

# 注册后自动登录验证
code, html = s.request("GET", "/profile")
check("注册后自动登录访问 /profile", code == 200, f"status={code}")

# 登出
code, html = s.request("POST", "/auth/logout", data={})
check("POST /auth/logout", code in (301, 302), f"status={code}")

# 错误密码登录
code, html = s.request("POST", "/auth/login", data={
    "identifier": test_user, "password": "wrongpass",
})
check("错误密码登录被拒绝", code == 401, f"status={code}")

# 正常登录
code, html = s.request("POST", "/auth/login", data={
    "identifier": test_user, "password": "test123456",
})
check("POST /auth/login (登录成功)", code in (301, 302), f"status={code}")

# ============================================================
# 3. 登录后业务流
# ============================================================
print("\n===== 3. 登录后业务流 =====")
# 个人中心各 tab
for tab in ["history", "favorites", "comments", "posts", "contributions"]:
    code, html = s.request("GET", f"/profile?tab={tab}")
    check(f"GET /profile?tab={tab}", code == 200, f"status={code}")

# 内容贡献中心
code, html = s.request("GET", "/contribute")
check("GET /contribute", code == 200 and "贡献" in html, f"status={code}")

# 提交品牌贡献
code, html = s.request("POST", "/contribute/submit/brand", data={
    "name": "测试品牌贡献", "website_url": "https://example.com",
    "description": "联调测试提交的品牌",
})
check("POST /contribute/submit/brand", code in (301, 302), f"status={code}")

# 提交文档贡献
code, html = s.request("POST", "/contribute/submit/document", data={
    "title": "测试文档贡献", "description": "联调测试提交的文档",
})
check("POST /contribute/submit/document", code in (301, 302), f"status={code}")

# 非法贡献类型 → 400
code, html = s.request("POST", "/contribute/submit/hacker", data={"name": "x"})
check("非法贡献类型返回400", code == 400, f"status={code}")

# 发布帖子
code, html = s.request("POST", "/community/create", data={
    "board": "qa", "title": "联调测试帖", "content": "这是一条联调测试帖内容",
})
check("POST /community/create (发帖)", code in (301, 302), f"status={code}")
# 找到刚发的帖子
code, html = s.request("GET", "/community")
post_id = None
import re
m = re.search(r"/community/post/(\d+)", html)
if m:
    post_id = int(m.group(1))
check("社区页存在新帖", post_id is not None, f"post_id={post_id}")

# 帖子详情 + 回复
if post_id:
    code, html = s.request("GET", f"/community/post/{post_id}")
    check(f"GET /community/post/{post_id}", code == 200, f"status={code}")
    code, html = s.request("POST", f"/community/post/{post_id}/reply", data={"content": "联调回复"})
    check("POST 回复", code in (301, 302), f"status={code}")

# 空内容发帖 → 应拒绝
code, html = s.request("POST", "/community/create", data={
    "board": "chat", "title": "", "content": "",
})
check("空标题发帖被拒绝", code in (301, 302), f"status={code}")

# 文档评论
code, html = s.request("POST", "/doc/1/comment", data={"content": "联调评论内容"})
check("POST /doc/1/comment", code in (301, 302), f"status={code}")

# 收藏/取消收藏 API
code, body = s.request("POST", "/api/favorite/add", data={"document_id": "1"})
check("POST /api/favorite/add", code == 200 and '"success":true' in body, f"status={code} {body[:80]}")
code, body = s.request("POST", "/api/favorite/remove", data={"document_id": "1"})
check("POST /api/favorite/remove", code == 200 and '"success":true' in body, f"status={code} {body[:80]}")

# 阅读进度 API
code, body = s.request("POST", "/api/reading/progress", data={"document_id": "1", "progress": "0.42"})
check("POST /api/reading/progress", code == 200 and '"success":true' in body, f"status={code} {body[:80]}")
code, body = s.request("POST", "/api/reading/progress", data={"progress": "0.5"})
check("POST /api/reading/progress (缺文档ID返回400)", code == 400, f"status={code}")

# ============================================================
# 4. 错误处理与边界
# ============================================================
print("\n===== 4. 错误处理与边界 =====")
code, html = s.request("GET", "/doc/99999")
check("GET /doc/99999 → 404", code == 404, f"status={code}")

code, html = s.request("GET", "/nonexistent-page")
check("GET 不存在页面 → 404", code == 404, f"status={code}")

code, html = s.request("GET", "/doc/abc")
check("GET /doc/abc (非法ID) → 404", code == 404, f"status={code}")

# 未登录访问收藏 API → 应重定向到登录（先 GET 建立会话获取 CSRF token）
s2 = Session()
s2.request("GET", "/")  # 建立会话 + CSRF token
code, body = s2.request("POST", "/api/favorite/add", data={"document_id": "1"})
check("未登录调用收藏API被拦截", code in (301, 302), f"status={code}")

# ============================================================
# 汇总
# ============================================================
print("\n" + "=" * 60)
total = len(results)
passed = total - fail_count
print(f"联调结果：{passed}/{total} 通过")
if fail_count:
    print("失败用例：")
    for case, ok, detail in results:
        if not ok:
            print("  -", case, "|", detail)
print("=" * 60)
sys.exit(1 if fail_count else 0)
