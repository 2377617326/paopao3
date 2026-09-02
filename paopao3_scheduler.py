# -*- coding: utf-8 -*-
"""
paopao3 scheduler - 建房翻期抄paopao1, 决策按用户指定Q1-Q4逻辑
"""
import random
import sys
import urllib.parse
import re
import requests
import time
import json
import os
import datetime as dt
import signal

def _sigterm_exit(sig, frame):
    sys.exit(0)

signal.signal(signal.SIGTERM, _sigterm_exit)

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_9001 = "http://121.42.10.114:9001"
BASE_9997 = "http://121.42.10.114:9997"
COST_TV = 3708
COST_WATCH = 2121
COST_GAME = 597
TZ_OFFSET = dt.timedelta(hours=8)

FORECAST = {
    "tv": {"east": 12800, "central": 7360, "west": 7040},
    "watch": {"east": 46400, "central": 28000, "west": 28000},
    "game": {"east": 42400, "central": 24000, "west": 25600},
}

Q2_SCENARIOS = {
    1: {"tv": (0.70, 0.85), "watch": (1.10, 1.50), "game": (1.05, 1.15)},
    2: {"tv": (0.85, 1.05), "watch": (1.10, 1.50), "game": (0.92, 1.05)},
    3: {"tv": (0.85, 1.05), "watch": (1.00, 1.25), "game": (1.05, 1.15)},
}

Q3_SCENARIOS = {
    1: {"tv": (0.70, 0.85), "watch": (0.60, 1.30), "game": (0.50, 1.15)},
    2: {"tv": (0.85, 1.05), "watch": (0.60, 1.40), "game": (0.60, 1.05)},
    3: {"tv": (0.85, 1.05), "watch": (0.80, 1.15), "game": (0.50, 1.15)},
}

Q4_RANGES = {
    "tv": (0.50, 0.55),
    "watch": (0.60, 1.10),
    "game": (0.50, 1.00),
}

Q2Q3_TIME_ALLOC = [
    ([0, 0, 0], [40, 40, 40], [60, 60, 60]),
    ([10, 10, 10], [30, 30, 30], [60, 60, 60]),
    ([10, 10, 10], [40, 40, 40], [50, 50, 50]),
]

Q4_TIME_ALLOC = [
    ([20, 20, 20], [60, 60, 60], [20, 20, 20]),
    ([0, 0, 0], [60, 60, 60], [40, 40, 40]),
    ([10, 10, 10], [50, 50, 50], [40, 40, 40]),
]

LEVELS = {
    1: {"name": "牛刀小试", "full_n": 6},
    2: {"name": "锋芒毕露", "full_n": 14},
    3: {"name": "群雄争霸", "full_n": 18},
}

ROOM_NAME_MARK = "尔尔定时比赛q群5342744003"
ROOM_NAME_TPL = ROOM_NAME_MARK + " 自动测试{time}开"
TOTAL_PERIOD = 4
PERIOD_LENGTH = 20
ROOM_PASSWORD = "123"
FORCE_START_AFTER = 40
START_LIMIT_HOUR = 22
MAX_JOB_RUNTIME = 1.9 * 60 * 60
STATE_FILE = "paopao3_state.json"


def now_bj():
    return dt.datetime.utcnow() + TZ_OFFSET


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    return {}


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)


class Scheduler:
    def __init__(self, username, password, timeout=15):
        self.username = username
        self.password = password
        self.user_id = None
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
        self.timeout = timeout
        self.start_ts = time.time()

    def _time_left(self):
        return MAX_JOB_RUNTIME - (time.time() - self.start_ts)

    def _now(self):
        return now_bj()

    def _post(self, path, **params):
        url = f"{BASE_9997}{path}"
        if params:
            enc = "&".join(f"{k}={urllib.parse.quote(str(v), safe='')}"
                           for k, v in params.items())
            url += "?" + enc
        xsrf = self.session.cookies.get("XSRF-TOKEN")
        if xsrf:
            self.session.headers["X-XSRF-TOKEN"] = xsrf
        try:
            r = self.session.post(url, timeout=self.timeout)
            text = r.text.strip()
        except Exception as e:
            print(f"  [net] POST {path} error: {e}", flush=True)
            return "-1"
        if "CSRF" in text or "1004" in text:
            print("  [CSRF] token expired, re-login...", flush=True)
            self.login()
            xsrf = self.session.cookies.get("XSRF-TOKEN")
            if xsrf:
                self.session.headers["X-XSRF-TOKEN"] = xsrf
            try:
                r = self.session.post(url, timeout=self.timeout)
                text = r.text.strip()
            except Exception as e:
                print(f"  [net] POST {path} retry error: {e}", flush=True)
                return "-1"
        return text

    def _get(self, path, **params):
        url = f"{BASE_9997}{path}"
        if params:
            enc = "&".join(f"{k}={urllib.parse.quote(str(v), safe='')}"
                           for k, v in params.items())
            url += "?" + enc
        try:
            r = self.session.get(url, timeout=self.timeout)
        except Exception as e:
            print(f"  [net] GET {path} error: {e}", flush=True)
            return ""
        r.encoding = "utf-8"
        if "login" in r.url.lower() or r.text.strip() == "":
            self.login()
            try:
                r = self.session.get(url, timeout=self.timeout)
            except Exception as e:
                print(f"  [net] GET {path} retry error: {e}", flush=True)
                return ""
            r.encoding = "utf-8"
        return r.text

    def login(self, max_retries=10):
        for attempt in range(max_retries):
            if attempt > 0:
                self.session = requests.Session()
                self.session.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
                print(f"  [login] retry {attempt+1}...", flush=True)
                time.sleep(30)
            r = self.session.post(f"{BASE_9997}/roomLogin/login",
                                  data={"loginName": self.username, "loginPass": self.password},
                                  timeout=self.timeout)
            resp = r.text.strip()
            if resp == "1":
                idx = self._get("/room/roomIndex")
                m = re.search(r"userId\s*=\s*['\"](\d+)['\"]", idx)
                self.user_id = m.group(1) if m else None
                print(f"[OK] login: {self.username} (userId={self.user_id})")
                return True
            print(f"[FAIL] login: {resp}", flush=True)
        print(f"[FAIL] login failed after {max_retries} retries", flush=True)
        return False

    def room_clean(self, room_level):
        try:
            self._post("/room/roomClean", userId=self.user_id, roomLevelId=room_level)
        except Exception as e:
            print(f"  [clean] error: {e}")

    def find_own_rooms(self):
        found = {}
        for lv in LEVELS:
            try:
                html = self._get("/room/gotoAddRoom", userId=self.user_id, roomLevelId=lv)
                for block in re.split(r'<div class="col-11 px-2 mb-3 room-list-item">', html):
                    owner_match = re.search(r'房主名字[：:]\s*([^<\s]+)', block)
                    if not owner_match:
                        continue
                    owner = owner_match.group(1)
                    if owner != self.username and owner != str(self.user_id):
                        continue
                    m = re.search(r"gotoJoinRoom\('\d+','\d+','(\d+)'\)", block)
                    if m:
                        rid = m.group(1)
                        if lv not in found or "已结束" not in block:
                            found[lv] = rid
            except Exception:
                continue
        return found

    def room_status(self, room_id, room_level):
        try:
            html = self._get("/room/gotoJoinRoom",
                             userId=self.user_id, roomLevelId=room_level, roomId=room_id)
            m = re.search(r"(\d+)/(\d+)", html)
            if m:
                return int(m.group(1)), int(m.group(2))
        except Exception:
            pass
        return None, None

    def is_room_finished(self, room_id, room_level):
        try:
            html = self._get("/room/gotoAddRoom", userId=self.user_id, roomLevelId=room_level)
            for block in re.split(r'<div class="col-11 px-2 mb-3 room-list-item">', html):
                if f"'{room_id}'" in block:
                    return "已结束" in block
        except Exception:
            pass
        return False

    def is_room_started(self, room_id, room_level):
        try:
            html = self._get("/room/gotoAddRoom", userId=self.user_id, roomLevelId=room_level)
            for block in re.split(r'<div class="col-11 px-2 mb-3 room-list-item">', html):
                if f"'{room_id}'" in block:
                    if "已结束" in block:
                        return True
                    if "继续等待" in block:
                        return False
                    if "进行中" in block or "进入竞赛" in block:
                        return True
                    return False
        except Exception:
            pass
        return False

    def _get_room_block(self, room_id, room_level):
        try:
            html = self._get("/room/gotoAddRoom", userId=self.user_id, roomLevelId=room_level)
            for block in re.split(r'<div class="col-11 px-2 mb-3 room-list-item">', html):
                if str(room_id) in block:
                    return block
        except Exception:
            pass
        return ""

    def _get_room_name(self, room_id, room_level):
        for attempt in range(5):
            block = self._get_room_block(room_id, room_level)
            if block:
                for pattern in [
                    r'房间名称[：:]\s*([^<]+)',
                    r'class="room[^"]*name[^"]*"[^>]*>([^<]+)',
                    r'自动测试\d{1,2}:\d{2}开',
                    r'尔尔定时[^\s<]+',
                ]:
                    m = re.search(pattern, block)
                    if m:
                        return m.group(0).strip() if not m.lastindex else m.group(1).strip()
            if attempt < 4:
                time.sleep(3)
        return ""

    def _parse_start_time_from_name(self, room_name):
        m = re.search(r'(\d{1,2}:\d{2})开', room_name)
        if not m:
            return None
        h, mi = map(int, m.group(1).split(':'))
        now = self._now()
        target = now.replace(hour=h, minute=mi, second=0, microsecond=0)
        if target <= now:
            return now
        return target

    def start_exp(self, room_id, room_level):
        return self._post("/room/startRoomExp", type="1", roomId=room_id, userId=self.user_id)

    def next_period(self, room_id, room_level):
        return self._post("/room/startRoomExp", type="2", roomId=room_id, userId=self.user_id)

    def finish_exp(self, room_id, room_level):
        return self._post("/room/startRoomExp", type="3",
                          roomId=room_id, userId=self.user_id, roomLevelId=room_level)

    def create_room(self, room_level, created_at, room_name=None):
        level = LEVELS[room_level]
        n = level["full_n"]
        force_time = created_at + dt.timedelta(minutes=FORCE_START_AFTER)
        if room_name is None:
            name = ROOM_NAME_TPL.format(n=n, time=force_time.strftime("%H:%M"))
        else:
            name = room_name
        self.room_clean(room_level)
        params = {
            "userId": self.user_id, "roomLevelId": room_level,
            "roomName": name, "roomPassword": ROOM_PASSWORD,
            "totalPeriod": TOTAL_PERIOD, "isNeed": "0",
            "roomPeriodLength": PERIOD_LENGTH,
        }
        try:
            resp = self._post("/room/addRoom", **params)
        except Exception as e:
            print(f"  [create] error: {e}", flush=True)
            return False, None
        print(f"  [create] addRoom: {resp}", flush=True)
        if resp in ("0", "2", "3"):
            print(f"  [create] failed: code={resp}", flush=True)
            return False, None
        time.sleep(3)
        room_id = self.find_own_rooms().get(room_level)
        if room_id is None:
            time.sleep(3)
            room_id = self.find_own_rooms().get(room_level)
        if room_id is None:
            print(f"  [create] success but room not found!", flush=True)
            return False, None
        print(f"  [create] success! level={room_level}({level['name']}) room={room_id} name=[{name}]", flush=True)
        return True, room_id

    def wait_and_start(self, room_id, room_level, created_at):
        room_name = self._get_room_name(room_id, room_level)
        target_time = self._parse_start_time_from_name(room_name)
        if not target_time:
            target_time = created_at + dt.timedelta(minutes=FORCE_START_AFTER)
        wait_sec = (target_time - self._now()).total_seconds()
        if wait_sec > 0:
            print(f"  [wait] room {room_id} [{room_name}] start {target_time.strftime('%H:%M')}, wait {wait_sec/60:.0f}min", flush=True)
            while self._now() < target_time:
                time.sleep(min(30, max(1, wait_sec)))
                wait_sec = (target_time - self._now()).total_seconds()
        print(f"  [start] time! starting...", flush=True)
        while True:
            self.start_exp(room_id, room_level)
            time.sleep(5)
            if self.is_room_started(room_id, room_level):
                print("  [start] room started!", flush=True)
                return True
            players, maxp = self.room_status(room_id, room_level)
            if players is None:
                print("  [start] room gone", flush=True)
                return False
            print(f"  [start] not started({players}/{maxp}), retry in 10s...", flush=True)
            time.sleep(10)

    def pick_level(self, primary, secondary):
        try:
            html = self._get("/room/gotoAddRoom", userId=self.user_id, roomLevelId=primary)
            stux_m = re.search(r'var stuX\s*=\s*[\'"](\d+)[\'"]', html)
            if stux_m and stux_m.group(1) != "0":
                return primary
        except Exception:
            return primary
        try:
            html = self._get("/room/gotoAddRoom", userId=self.user_id, roomLevelId=secondary)
            stux_m = re.search(r'var stuX\s*=\s*[\'"](\d+)[\'"]', html)
            if stux_m and stux_m.group(1) != "0":
                return secondary
        except Exception:
            return primary
        return 1

    def plan_level(self):
        now = self._now()
        h = now.hour + now.minute / 60
        if 8 <= h < 9:
            return (1, 1)
        if 9 <= h < 12:
            return (2, 1)
        if 12 <= h < 14:
            return (1, 1)
        if 14 <= h < 17:
            return (2, 1)
        if 17 <= h < 20:
            return (3, 2)
        if 20 <= h < START_LIMIT_HOUR:
            return (1, 1)
        return None


class DecisionClient:
    def __init__(self, timeout=15):
        self.timeout = timeout

    def login_9001(self, uid, room_id):
        s = requests.Session()
        s.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
        s.get(f"{BASE_9001}/", timeout=self.timeout)
        xsrf = s.cookies.get("XSRF-TOKEN")
        if xsrf:
            s.headers["X-XSRF-TOKEN"] = xsrf
        r = s.post(f"{BASE_9001}/room/gotoMatch",
                   data={"str": f"login?userId={uid}*roomId={room_id}"},
                   timeout=self.timeout)
        d = r.json()["Data"]
        if "loginName" not in d:
            return None, None, None
        xsrf = s.cookies.get("XSRF-TOKEN")
        if xsrf:
            s.headers["X-XSRF-TOKEN"] = xsrf
        r2 = s.post(f"{BASE_9001}/login/login", data={
            "loginName": d["loginName"], "loginPass": d["loginPass"],
            "loginType": d["loginType"], "expId": d["expId"],
            "lagOrVersionId": "102",
        }, timeout=self.timeout)
        user = r2.json()["Data"]
        ck = {
            "userName": urllib.parse.quote(str(user.get("userName")), safe=""),
            "className": urllib.parse.quote(str(user.get("className")), safe=""),
        }
        return s, user, ck

    def submit_decision(self, s, user, ck, period_num, typ, decision_str,
                        state=0, timeout=15):
        p = {
            "type": str(typ), "periodNum": str(period_num),
            "num": str(period_num),
            "companyId": str(user.get("companyId")),
            "expId": str(user.get("expId")),
            "userId": str(user.get("userId")),
            "userName": ck["userName"],
            "className": ck["className"],
            "lagOrVersionId": "102", "str": decision_str,
        }
        if state:
            p["state"] = str(state)
        xsrf = s.cookies.get("XSRF-TOKEN")
        if xsrf:
            s.headers["X-XSRF-TOKEN"] = xsrf
        url = (f"{BASE_9001}/student/decisionInfo/saveDecisionInfo?"
               + urllib.parse.urlencode(p))
        r = s.post(url, timeout=timeout)
        return r.json()

    def get_period(self, uid, room_id):
        try:
            s, user, _ = self.login_9001(uid, room_id)
            if user:
                return int(user.get("periodNum", 1))
        except Exception:
            pass
        return 1

    def submit_all_decisions(self, uid, room_id, period_num):
        s, user, ck = self.login_9001(uid, room_id)
        if s is None:
            print(f"    [9001] login failed")
            return False

        n = 8
        quarter = period_num

        # type4
        r = self.submit_decision(s, user, ck, period_num, 4,
                                 "9,9,9,1,9,9,9,1,9,9,9,1,")
        ok = r.get("Status") == 2000
        print(f"    type4: {'OK' if ok else 'FAIL'}")

        # type5
        salary = random.randint(3900, 4150)
        commission = round(random.uniform(2.4, 3.15), 2)
        if quarter == 1:
            type5_str = "99,99,99,9,9,9,0,0,0,0,0,0,0,0,0,0,0,0,3800,1.5,9,9,9,"
        elif quarter == 4:
            alloc_idx = random.choices([0, 1, 2], weights=[5, 80, 15])[0]
            tv_a, wa, ga = Q4_TIME_ALLOC[alloc_idx]
            type5_str = (f"99,99,99,{tv_a[0]},{wa[0]},{ga[0]},"
                         f"0,0,0,0,0,0,0,0,0,0,0,0,"
                         f"{salary},{commission},9,9,9,")
        else:
            alloc_idx = random.choices([0, 1, 2], weights=[80, 15, 5])[0]
            tv_a, wa, ga = Q2Q3_TIME_ALLOC[alloc_idx]
            type5_str = (f"99,99,99,{tv_a[0]},{wa[0]},{ga[0]},"
                         f"0,0,0,0,0,0,0,0,0,0,0,0,"
                         f"{salary},{commission},9,9,9,")
        r = self.submit_decision(s, user, ck, period_num, 5, type5_str)
        print(f"    type5: {'OK' if r.get('Status') == 2000 else 'FAIL'}")

        # type3
        tv_total = watch_total = game_total = 0
        if quarter == 1:
            tv_east = 1500
            game_east = 10000
            watch_east = max(0, int((45000 - 45000 - tv_east * COST_TV
                                    - game_east * COST_GAME) / COST_WATCH))
            tv_total = tv_east
            watch_total = watch_east
            game_total = game_east
            type3_str = f"{tv_east},0,0,{watch_east},0,0,{game_east},0,0,"
        else:
            if quarter == 2:
                sc = Q2_SCENARIOS
            elif quarter == 3:
                sc = Q3_SCENARIOS
            else:
                sc = None
            if sc:
                scenario = random.randint(1, 3)
                pcts = {}
                for prod in ["tv", "watch", "game"]:
                    lo, hi = sc[scenario][prod]
                    pcts[prod] = random.uniform(lo, hi)
            else:
                pcts = {}
                for prod in ["tv", "watch", "game"]:
                    lo, hi = Q4_RANGES[prod]
                    pcts[prod] = random.uniform(lo, hi)
            tv_e = int(FORECAST["tv"]["east"] / n * pcts["tv"])
            tv_c = int(FORECAST["tv"]["central"] / n * pcts["tv"])
            tv_w = int(FORECAST["tv"]["west"] / n * pcts["tv"])
            tv_total = tv_e + tv_c + tv_w
            wa_e = int(FORECAST["watch"]["east"] / n * pcts["watch"])
            wa_c = int(FORECAST["watch"]["central"] / n * pcts["watch"])
            wa_w = int(FORECAST["watch"]["west"] / n * pcts["watch"])
            watch_total = wa_e + wa_c + wa_w
            ga_e = int(FORECAST["game"]["east"] / n * pcts["game"])
            ga_c = int(FORECAST["game"]["central"] / n * pcts["game"])
            ga_w = int(FORECAST["game"]["west"] / n * pcts["game"])
            game_total = ga_e + ga_c + ga_w
            type3_str = (f"{tv_e},{tv_c},{tv_w},{wa_e},{wa_c},{wa_w},"
                         f"{ga_e},{ga_c},{ga_w},")
        r = self.submit_decision(s, user, ck, period_num, 3, type3_str)
        print(f"    type3: {'OK' if r.get('Status') == 2000 else 'FAIL'}")

        # type1
        t1 = tv_total + 480
        w1 = watch_total + 480
        g1 = game_total + 480
        r = self.submit_decision(s, user, ck, period_num, 1, f"{t1},{w1},{g1},")
        print(f"    type1: {'OK' if r.get('Status') == 2000 else 'FAIL'}")

        # type6
        if quarter == 1:
            type6_str = "0,0,0,0,0,0,0,0,0,10,11,12,13,13,15,12,14,5,5,2,2,"
        elif quarter == 2:
            tp = random.randint(1, 250) * 10000
            twp = random.randint(200, 450) * 10000
            tgp = random.randint(450, 850) * 10000
            type6_str = (f"{tp},{tp},{tp},{twp},{twp},{twp},"
                         f"{tgp},{tgp},{tgp},10,11,12,13,13,15,12,14,5,5,2,2,")
        elif quarter == 3:
            tp = random.randint(1, 45) * 10000
            twp = random.randint(450, 950) * 10000
            tgp = random.randint(750, 1150) * 10000
            type6_str = (f"{tp},{tp},{tp},{twp},{twp},{twp},"
                         f"{tgp},{tgp},{tgp},10,11,12,13,13,15,12,14,5,5,2,2,")
        else:
            tp = random.randint(1, 150) * 10000
            twp = random.randint(750, 1250) * 10000
            tgp = random.randint(850, 1150) * 10000
            type6_str = (f"{tp},{tp},{tp},{twp},{twp},{twp},"
                         f"{tgp},{tgp},{tgp},10,11,12,13,13,15,12,14,5,5,2,2,")
        r6 = self.submit_decision(s, user, ck, period_num, 6, type6_str)
        print(f"    type6: {'OK' if r6.get('Status') == 2000 else 'FAIL'}")

        # type2
        if quarter == 1:
            prods = random.sample(["tv", "watch", "game"], 2)
            rd_vals = {"tv": random.randint(80, 150) * 10000,
                       "watch": random.randint(80, 150) * 10000,
                       "game": random.randint(80, 150) * 10000}
            rdt = rd_vals.get("tv", 0) if "tv" in prods else 0
            rdw = rd_vals.get("watch", 0) if "watch" in prods else 0
            rdg = rd_vals.get("game", 0) if "game" in prods else 0
        elif quarter == 2:
            rdt = random.randint(800, 1800) * 10000
            rdw = random.randint(1200, 3200) * 10000
            rdg = random.randint(2500, 4200) * 10000
        elif quarter == 3:
            rdt = random.randint(1200, 2000) * 10000
            rdw = random.randint(1200, 2000) * 10000
            rdg = random.randint(3000, 4200) * 10000
        else:
            rdt = random.randint(2700, 3800) * 10000
            rdw = random.randint(2700, 4500) * 10000
            rdg = random.randint(3500, 6000) * 10000
        type2_str = f"{rdt},{rdw},{rdg},100,100,100,"
        r = self.submit_decision(s, user, ck, period_num, 2, type2_str)
        print(f"    type2: {'OK' if r.get('Status') == 2000 else 'FAIL'}")

        # type7
        r = self.submit_decision(s, user, ck, period_num, 7,
                                 "9999,7999,9999,9999,7999,9999,9999,7999,9999,")
        print(f"    type7: {'OK' if r.get('Status') == 2000 else 'FAIL'}")

        # type8
        r = self.submit_decision(s, user, ck, period_num, 8,
                                 "1,0,0,0,0,0,0,0,0,0,0,0,0,0,0,1,", state=2)
        print(f"    type8: {'OK' if r.get('Status') == 2000 else 'FAIL'}")
        return True


def flip_loop(sched, dc, room_id, room_level):
    uid = sched.user_id
    current_period = dc.get_period(uid, room_id)
    print(f"  [flip] current period: {current_period}", flush=True)

    if current_period > 4:
        print("  [flip] game over", flush=True)
        sched.finish_exp(room_id, room_level)
        return True

    for attempt in range(3):
        try:
            dc.submit_all_decisions(uid, room_id, current_period)
            break
        except Exception as e:
            print(f"  [flip] submit error: {e}", flush=True)
            time.sleep(30)

    no_flip_count = 0
    while current_period < TOTAL_PERIOD:
        if sched._time_left() < 600:
            print("  [flip] time limit, exit", flush=True)
            return False
        if sched.is_room_finished(room_id, room_level):
            print("  [flip] room finished", flush=True)
            return True
        resp = sched.next_period(room_id, room_level)
        if resp == "1":
            current_period += 1
            no_flip_count = 0
            print(f"  [flip] flipped! period {current_period}", flush=True)
            for attempt in range(3):
                try:
                    dc.submit_all_decisions(uid, room_id, current_period)
                    break
                except Exception as e:
                    print(f"  [flip] submit error: {e}", flush=True)
                    time.sleep(30)
            if sched.is_room_finished(room_id, room_level):
                print("  [flip] room finished after flip", flush=True)
                return True
        else:
            no_flip_count += 1
            print(f"  [flip] resp={resp}, retry 30s...", flush=True)
            time.sleep(30)
            if no_flip_count >= 20:
                print("  [flip] too many retries, finish", flush=True)
                sched.finish_exp(room_id, room_level)
                return True

    print("  [finish] end room...", flush=True)
    for _ in range(20):
        if sched._time_left() < 600:
            return False
        resp = sched.finish_exp(room_id, room_level)
        if resp == "1" or sched.is_room_finished(room_id, room_level):
            print("  [finish] done!", flush=True)
            return True
        time.sleep(30)
    return True


def handle_room(sched, dc, room_id, room_level):
    level = LEVELS[room_level]
    players, maxp = sched.room_status(room_id, room_level)
    print(f"  [handle] room {room_id} level {room_level}({level['name']}) {players}/{maxp}", flush=True)
    if players is None:
        print("  [handle] room not accessible", flush=True)
        return True

    if sched.is_room_started(room_id, room_level):
        print("  [handle] room already started, flip loop", flush=True)
    else:
        created_at = sched._now()
        started = sched.wait_and_start(room_id, room_level, created_at)
        if not started:
            return False

    flip_loop(sched, dc, room_id, room_level)
    return True


def main():
    username = os.environ.get("BOT_USER", "自动-1")
    password = os.environ.get("BOT_PASS", "321")

    print(f"{'='*50}")
    print(f"paopao3 scheduler {now_bj().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*50}")

    sched = Scheduler(username, password)
    dc = DecisionClient()

    if not sched.login():
        print("Login failed")
        return

    sched.start_ts = time.time()
    skip_rooms = set()

    while True:
        if sched._time_left() < 600:
            print("=== time limit, exit ===", flush=True)
            return

        now = sched._now()
        print(f"\n[{now.strftime('%H:%M:%S')}] main loop", flush=True)

        plan = sched.plan_level()
        if plan is None:
            print(f"[{now.strftime('%H:%M')}] after {START_LIMIT_HOUR}h, check rooms...", flush=True)
            own = sched.find_own_rooms()
            has_handleable = False
            if own:
                for lv, rid in own.items():
                    if rid not in skip_rooms and not sched.is_room_finished(rid, lv):
                        handle_room(sched, dc, rid, lv)
                        skip_rooms.add(rid)
                        has_handleable = True
                        break
            if not has_handleable:
                print("  no handleable rooms, create new", flush=True)
                room_level = 1
                ok, room_id = sched.create_room(room_level, sched._now())
                if ok:
                    print(f"  [create] success! room={room_id}", flush=True)
                    handle_room(sched, dc, room_id, room_level)
                else:
                    print("  [create] failed", flush=True)
                    time.sleep(30)
            continue

        primary, secondary = plan
        print(f"  plan: primary={LEVELS[primary]['name']} secondary={LEVELS[secondary]['name']}", flush=True)

        own = sched.find_own_rooms()
        print(f"  found {len(own)} rooms", flush=True)
        if own:
            handled = False
            for lv, rid in own.items():
                if rid not in skip_rooms and not sched.is_room_finished(rid, lv):
                    handle_room(sched, dc, rid, lv)
                    skip_rooms.add(rid)
                    handled = True
                    break
            if handled:
                continue
            print("  all rooms finished, create new", flush=True)

        room_level = primary if primary == secondary else sched.pick_level(primary, secondary)
        print(f"  selected: {room_level}({LEVELS[room_level]['name']})", flush=True)

        print(f"  [create] creating...", flush=True)
        created_at = sched._now()
        ok, room_id = sched.create_room(room_level, created_at)
        if not ok:
            own = sched.find_own_rooms()
            if own:
                for lv, rid in own.items():
                    if rid not in skip_rooms and not sched.is_room_finished(rid, lv):
                        handle_room(sched, dc, rid, lv)
                        skip_rooms.add(rid)
                        break
                continue
            print("  [create] failed, wait 30s", flush=True)
            time.sleep(30)
            continue
        print(f"  [create] success! room={room_id}", flush=True)
        handle_room(sched, dc, room_id, room_level)


if __name__ == "__main__":
    main()
