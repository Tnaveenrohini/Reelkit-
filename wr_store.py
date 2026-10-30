"""WaveRise admin storage: Upstash Redis over its REST API (free tier, added from Vercel → Storage).

On Vercel without a database the store reports `ok = False`: the public site keeps working with its built-in SEO,
and the admin dashboard shows real checks but refuses to save. Locally (no VERCEL env) an in-memory store is used for testing.
"""
import json, os, threading, time

import httpx


class StoreError(Exception):
    pass


class _Redis:
    """Minimal Upstash REST client: POST ["CMD", args…] → {"result": …}."""

    def __init__(self, url: str, token: str):
        self.url, self.h = url.rstrip("/"), {"Authorization": f"Bearer {token}"}
        self.c = httpx.Client(timeout=8)

    def cmd(self, *args):
        try:
            r = self.c.post(self.url, headers=self.h, json=[str(a) for a in args])
        except httpx.HTTPError as e:
            raise StoreError(f"database unreachable: {e.__class__.__name__}") from None
        try:
            j = r.json()
        except ValueError:
            raise StoreError(f"database answered {r.status_code}") from None
        if r.status_code != 200 or "error" in j:
            raise StoreError(str(j.get("error") or r.status_code)[:200])
        return j.get("result")


class _Memory:
    """Same commands, kept in this process (local development and tests only)."""

    def __init__(self):
        self.d, self.exp, self.lock = {}, {}, threading.Lock()

    def _alive(self, k):
        e = self.exp.get(k)
        if e and e < time.time():
            self.d.pop(k, None); self.exp.pop(k, None)
        return k in self.d

    def cmd(self, op, *a):
        op = op.upper()
        with self.lock:
            k = a[0] if a else None
            if k is not None:
                self._alive(k)
            if op == "GET":
                return self.d.get(k)
            if op == "SET":
                self.d[k] = a[1]; self.exp.pop(k, None)
                if len(a) >= 4 and str(a[2]).upper() == "EX":
                    self.exp[k] = time.time() + int(a[3])
                return "OK"
            if op == "DEL":
                n = 0
                for x in a:
                    n += 1 if self.d.pop(x, None) is not None else 0; self.exp.pop(x, None)
                return n
            if op == "EXPIRE":
                if k in self.d:
                    self.exp[k] = time.time() + int(a[1]); return 1
                return 0
            if op == "INCR":
                v = int(self.d.get(k, 0)) + 1; self.d[k] = str(v); return v
            if op == "HSET":
                h = self.d.setdefault(k, {})
                for i in range(1, len(a), 2):
                    h[a[i]] = a[i + 1]
                return 1
            if op == "HGET":
                return (self.d.get(k) or {}).get(a[1])
            if op == "HDEL":
                h = self.d.get(k) or {}
                return sum(1 for f in a[1:] if h.pop(f, None) is not None)
            if op == "HGETALL":
                out = []
                for f, v in (self.d.get(k) or {}).items():
                    out += [f, v]
                return out
            if op == "LPUSH":
                lst = self.d.setdefault(k, [])
                for v in a[1:]:
                    lst.insert(0, v)
                return len(lst)
            if op == "LTRIM":
                lst = self.d.get(k) or []
                s, e = int(a[1]), int(a[2]); self.d[k] = lst[s:(None if e == -1 else e + 1)]; return "OK"
            if op == "LRANGE":
                lst = self.d.get(k) or []
                s, e = int(a[1]), int(a[2]); return lst[s:(None if e == -1 else e + 1)]
            if op == "ZINCRBY":
                z = self.d.setdefault(k, {}); z[a[2]] = z.get(a[2], 0) + float(a[1]); return str(z[a[2]])
            if op == "ZREVRANGE":
                z = sorted((self.d.get(k) or {}).items(), key=lambda kv: -kv[1])
                s, e = int(a[1]), int(a[2]); part = z[s:(None if e == -1 else e + 1)]
                if len(a) > 3 and str(a[3]).upper() == "WITHSCORES":
                    out = []
                    for m, sc in part:
                        out += [m, str(sc)]
                    return out
                return [m for m, _ in part]
            if op == "ZCARD":
                return len(self.d.get(k) or {})
            if op == "ZREMRANGEBYRANK":
                z = sorted((self.d.get(k) or {}).items(), key=lambda kv: kv[1])
                s, e = int(a[1]), int(a[2]); n = len(z)
                s, e = (s + n if s < 0 else s), (e + n if e < 0 else e)
                for m, _ in z[s:e + 1]:
                    self.d[k].pop(m, None)
                return max(0, e - s + 1)
            if op == "PING":
                return "PONG"
            raise StoreError(f"unsupported command {op}")


class Store:
    P = "wr:"

    def __init__(self):
        url = (os.getenv("KV_REST_API_URL") or os.getenv("UPSTASH_REDIS_REST_URL") or "").strip()
        tok = (os.getenv("KV_REST_API_TOKEN") or os.getenv("UPSTASH_REDIS_REST_TOKEN") or "").strip()
        if url and tok:
            self.r, self.kind = _Redis(url, tok), "upstash"
        elif not os.getenv("VERCEL"):
            self.r, self.kind = _Memory(), "memory"
        else:
            self.r, self.kind = None, "none"
        self._cache = {}

    @property
    def ok(self):
        return self.r is not None

    def cmd(self, *a):
        if not self.r:
            raise StoreError("No database is connected. Add Upstash Redis in Vercel → Storage.")
        return self.r.cmd(*a)

    # --- helpers (keys are prefixed) ---
    def k(self, name):
        return self.P + name

    def get_json(self, name, default=None):
        v = self.cmd("GET", self.k(name))
        try:
            return json.loads(v) if v else default
        except ValueError:
            return default

    def set_json(self, name, val, ex=None):
        args = ["SET", self.k(name), json.dumps(val, ensure_ascii=False)]
        if ex:
            args += ["EX", int(ex)]
        self.cmd(*args); self.drop(name)

    def hall(self, name):
        flat = self.cmd("HGETALL", self.k(name)) or []
        if isinstance(flat, dict):
            items = flat.items()
        else:
            items = zip(flat[0::2], flat[1::2])
        out = {}
        for f, v in items:
            try:
                out[f] = json.loads(v)
            except ValueError:
                pass
        return out

    def hset(self, name, field, val):
        self.cmd("HSET", self.k(name), field, json.dumps(val, ensure_ascii=False)); self.drop(name)

    def hdel(self, name, field):
        n = self.cmd("HDEL", self.k(name), field); self.drop(name); return n

    def push(self, name, val, keep=1000):
        self.cmd("LPUSH", self.k(name), json.dumps(val, ensure_ascii=False)); self.cmd("LTRIM", self.k(name), 0, keep - 1)

    def lrange(self, name, n=200):
        out = []
        for v in self.cmd("LRANGE", self.k(name), 0, n - 1) or []:
            try:
                out.append(json.loads(v))
            except ValueError:
                pass
        return out

    # --- short in-process cache for values read on every public page ---
    def cached(self, name, loader, ttl=30):
        hit = self._cache.get(name)
        if hit and time.time() - hit[0] < ttl:
            return hit[1]
        val = loader()
        self._cache[name] = (time.time(), val)
        return val

    def drop(self, name):
        self._cache.pop(name, None)


STORE = Store()
