# -*- coding: utf-8 -*-
import random
import sys
import urllib.parse
import re
import requests
import time
import json
import os
import datetime as dt

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
        self.session.headers.update({"User-Agent": "Mozilla/5.0"})
        self.timeout = timeout
        self.start_ts = time.time()

    def _now(self):
        return now_bj()

    def login_9997(self):
        s = self.session
        s.get(BASE_9997 + "/login.jsp", timeout=self.timeout)
        xsrf = s.cookies.get("XSRF-TOKEN")
        if xsrf:
            s.headers["X-XSRF-TOKEN"] = xsrf
        r = s.post(BASE_9997 + "/roomLogin/login",
                   data={"loginName": self.username, "loginPass": self.password},
                   timeout=self.timeout)
        idx = s.get(BASE_9997 + "/room/roomIndex", timeout=self.timeout).text
        m = re.search(r"userId\s*=\s*['\"](\d+)['\"]", idx)
        self.user_id = m.group(1) if m else None
        xsrf = s.cookies.get("XSRF-TOKEN")
        if xsrf:
            s.headers["X-XSRF-TOKEN"] = xsrf
        return self.user_id is not None

    def _post_9997(self, path, **params):
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
            return r.text.strip()
        except Exception as e:
            print(f"  [net] POST {path} error: {e}")
            return "-1"

    def _get_9997(self, path, **params):
        url = f"{BASE_9997}{path}"
        if params:
            enc = "&".join(f"{k}={urllib.parse.quote(str(v), safe='')}"
                           for k, v in params.items())
            url += "?" + enc
        try:
            r = self.session.get(url, timeout=self.timeout)
            return r.text
        except Exception as e:
            print(f"  [net] GET {path} error: {e}")
            return ""

    def find_own_rooms(self):
        html = self._get_9997("/room/gotoAddRoom",
                              userId=self.user_id, roomLevelId=1)
        rooms = {}
        for block in re.split(r'<div class="col-11 px-2 mb-3 room-list-item">', html):
            owner = re.search(r"房主名字[：:]\s*([^<\s]+)", block)
            if not owner:
                continue
            m = re.search(r"gotoJoinRoom\('\d+','(\d+)','(\d+)'\)", block)
            if not m:
                continue
            level = int(m.group(1))
            rid = m.group(2)
            if "已结束" not in block:
                rooms[level] = rid
        return rooms

    def room_clean(self, room_level):
        try:
            self._post_9997("/room/roomClean",
                            userId=self.user_id, roomLevelId=room_level)
        except Exception as e:
            print(f"  [clean] error: {e}")

    def create_room(self, room_level=1):
        now = self._now()
        name = f"paopao3 {now.strftime('%H%M')}"
        self.room_clean(room_level)
        time.sleep(2)
        params = {
            "userId": self.user_id, "roomLevelId": room_level,
            "roomName": name, "roomPassword": "123",
            "totalPeriod": "4", "isNeed": "0", "roomPeriodLength": "20",
        }
        resp = self._post_9997("/room/addRoom", **params)
        print(f"  [create] addRoom: {resp}")
        time.sleep(3)
        rooms = self.find_own_rooms()
        room_id = rooms.get(room_level)
        if room_id:
            print(f"  [create] success! room={room_id}")
        return room_id

    def start_room(self, room_id):
        resp = self._post_9997("/room/startRoomExp",
                               type="1", roomId=room_id, userId=self.user_id)
        print(f"  [start] resp: {resp}")
        return resp == "1"

    def flip_room(self, room_id):
        resp = self._post_9997("/room/startRoomExp",
                               type="2", roomId=room_id, userId=self.user_id)
        return resp

    def end_room(self, room_id, room_level):
        resp = self._post_9997("/room/startRoomExp",
                               type="3", roomId=room_id, userId=self.user_id,
                               roomLevelId=room_level)
        return resp


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
        s, user, _ = self.login_9001(uid, room_id)
        if user:
            return int(user.get("periodNum", 1))
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
            tp = twp = tgp = 0
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


def main():
    username = os.environ.get("BOT_USER", "自动-1")
    password = os.environ.get("BOT_PASS", "321")

    print(f"{'='*50}")
    print(f"paopao3 scheduler {now_bj().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*50}")

    sched = Scheduler(username, password)
    dc = DecisionClient()

    if not sched.login_9997():
        print("Login 9997 failed")
        return

    state = load_state()
    room_id = state.get("room_id")
    room_level = state.get("room_level", 1)

    # Check existing room
    if room_id:
        rooms = sched.find_own_rooms()
        if room_level not in rooms or rooms[room_level] != room_id:
            print(f"Room {room_id} not found, checking...")
            room_id = rooms.get(room_level)
            if room_id:
                state["room_id"] = room_id
                save_state(state)

    # Create room if needed
    if not room_id:
        rooms = sched.find_own_rooms()
        if rooms:
            room_level = list(rooms.keys())[0]
            room_id = rooms[room_level]
            print(f"Found existing room: {room_id} (level {room_level})")
            state["room_id"] = room_id
            state["room_level"] = room_level
            save_state(state)
        else:
            print("Creating room...")
            room_id = sched.create_room(room_level)
            if not room_id:
                print("Create room failed")
                return
            state = {"room_id": room_id, "room_level": room_level,
                     "created_at": now_bj().isoformat()}
            save_state(state)
            print(f"Room created: {room_id}")

    # Start room if not started
    if not state.get("started"):
        print("Starting room...")
        if sched.start_room(room_id):
            state["started"] = True
            state["start_time"] = now_bj().isoformat()
            save_state(state)
            print("Room started!")
        else:
            print("Start failed, will retry")

    # Get current period
    uid = sched.user_id
    period = dc.get_period(uid, room_id)
    print(f"Current period: {period}")

    if period > 4:
        print("Game over!")
        sched.end_room(room_id, room_level)
        save_state({})
        return

    # Submit decisions if not done
    key = f"q{period}_done"
    if not state.get(key):
        print(f"Submitting Q{period} decisions...")
        for attempt in range(3):
            try:
                if dc.submit_all_decisions(uid, room_id, period):
                    state[key] = True
                    state[f"q{period}_flip_at"] = time.time()
                    save_state(state)
                    print(f"Q{period} decisions submitted!")
                    break
            except Exception as e:
                print(f"  Error: {e}")
                time.sleep(30)

    # Try to flip
    flip_at = state.get(f"q{period}_flip_at", 0)
    elapsed = time.time() - flip_at if flip_at else 0

    if elapsed >= 1200:
        print(f"Flipping Q{period}...")
        resp = sched.flip_room(room_id)
        print(f"  Flip result: {resp}")
        if resp == "1":
            del state[key]
            if f"q{period}_flip_at" in state:
                del state[f"q{period}_flip_at"]
            save_state(state)
            print("Flip OK!")
        else:
            print("Not time yet or error")
    elif flip_at:
        remaining = 1200 - elapsed
        print(f"Wait {remaining:.0f}s to flip")
    else:
        print("No flip time recorded yet")


if __name__ == "__main__":
    main()
