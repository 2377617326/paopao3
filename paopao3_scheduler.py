# -*- coding: utf-8 -*-
import random
import sys
import urllib.parse
import re
import requests
import time
import json
import os

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_9001 = "http://121.42.10.114:9001"
BASE_9997 = "http://121.42.10.114:9997"
COST_TV = 3708
COST_WATCH = 2121
COST_GAME = 597

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


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    return {}


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)


def login_9997(username, password):
    s = requests.Session()
    s.headers.update({"User-Agent": "Mozilla/5.0"})
    s.get(BASE_9997 + "/login.jsp")
    s.post(BASE_9997 + "/roomLogin/login",
           data={"loginName": username, "loginPass": password})
    xsrf = s.cookies.get("XSRF-TOKEN")
    idx = s.get(BASE_9997 + "/room/roomIndex").text
    m = re.search(r"userId\s*=\s*['\"](\d+)['\"]", idx)
    uid = m.group(1) if m else None
    if xsrf:
        s.headers["X-XSRF-TOKEN"] = xsrf
    return s, uid


def find_active_room(uid):
    s, _ = login_9997("自动-1", "321")
    html = s.get(BASE_9997 + "/room/gotoAddRoom",
                 params={"userId": uid, "roomLevelId": 1}).text
    for block in re.split(r'<div class="col-11 px-2 mb-3 room-list-item">', html):
        owner_match = re.search(r"房主名字[：:]\s*([^<\s]+)", block)
        if owner_match and owner_match.group(1) == "自动-1":
            m2 = re.search(r"gotoJoinRoom\('\d+','\d+','(\d+)'\)", block)
            if m2:
                rid = m2.group(1)
                if "已结束" not in block:
                    return rid
    return None


def login_9001(uid, room_id, timeout=15):
    s = requests.Session()
    s.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
    s.get(f"{BASE_9001}/", timeout=timeout)
    xsrf = s.cookies.get("XSRF-TOKEN")
    if xsrf:
        s.headers["X-XSRF-TOKEN"] = xsrf
    r = s.post(f"{BASE_9001}/room/gotoMatch",
               data={"str": f"login?userId={uid}*roomId={room_id}"},
               timeout=timeout)
    d = r.json()["Data"]
    if "loginName" not in d:
        return None, None
    xsrf = s.cookies.get("XSRF-TOKEN")
    if xsrf:
        s.headers["X-XSRF-TOKEN"] = xsrf
    r2 = s.post(f"{BASE_9001}/login/login", data={
        "loginName": d["loginName"], "loginPass": d["loginPass"],
        "loginType": d["loginType"], "expId": d["expId"],
        "lagOrVersionId": "102",
    }, timeout=timeout)
    user = r2.json()["Data"]
    return s, user


def submit_decision(s, user, ck, period_num, typ, decision_str, state=0,
                    timeout=15):
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


def flip_room(s, room_id, uid, timeout=15):
    xsrf = s.cookies.get("XSRF-TOKEN")
    if xsrf:
        s.headers["X-XSRF-TOKEN"] = xsrf
    url = (f"{BASE_9997}/room/startRoomExp?type=2"
           f"&roomId={room_id}&userId={uid}")
    r = s.post(url, timeout=timeout)
    return r.text


def run_decisions(s, user, ck, period_num, quarter, n=8):
    print(f"\nQ{quarter} decisions...")

    # type4
    r = submit_decision(s, user, ck, period_num, 4,
                        "9,9,9,1,9,9,9,1,9,9,9,1,")
    print(f"  type4: {'OK' if r.get('Status') == 2000 else 'FAIL'}")

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
    r = submit_decision(s, user, ck, period_num, 5, type5_str)
    print(f"  type5: {'OK' if r.get('Status') == 2000 else 'FAIL'}")

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
    r = submit_decision(s, user, ck, period_num, 3, type3_str)
    print(f"  type3: {'OK' if r.get('Status') == 2000 else 'FAIL'}")

    # type1
    t1 = tv_total + 480
    w1 = watch_total + 480
    g1 = game_total + 480
    r = submit_decision(s, user, ck, period_num, 1, f"{t1},{w1},{g1},")
    print(f"  type1: {'OK' if r.get('Status') == 2000 else 'FAIL'}")

    # type6
    if quarter == 1:
        tp = twp = tgp = 0
        type6_str = ("0,0,0,0,0,0,0,0,0,"
                     "10,11,12,13,13,15,12,14,5,5,2,2,")
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
    r6 = submit_decision(s, user, ck, period_num, 6, type6_str)
    remaining = r6.get("Data", {}).get("state", 0)
    print(f"  type6: {'OK' if r6.get('Status') == 2000 else 'FAIL'}"
          f" (remaining={remaining:,.0f})")

    # type2
    if quarter == 1:
        prods = random.sample(["tv", "watch", "game"], 2)
        rd_vals = {"tv": random.randint(80, 150) * 10000,
                   "watch": random.randint(80, 150) * 10000,
                   "game": random.randint(80, 150) * 10000}
        rdt = rd_vals[prods[0]] if "tv" in prods else 0
        rdw = rd_vals[prods[1]] if "watch" in prods else 0
        rdg = rd_vals[prods[1]] if "game" in prods else 0
        if "tv" in prods and "watch" in prods:
            rdt = rd_vals["tv"]
            rdw = rd_vals["watch"]
            rdg = 0
        elif "tv" in prods and "game" in prods:
            rdt = rd_vals["tv"]
            rdw = 0
            rdg = rd_vals["game"]
        else:
            rdt = 0
            rdw = rd_vals["watch"]
            rdg = rd_vals["game"]
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
    r = submit_decision(s, user, ck, period_num, 2, type2_str)
    print(f"  type2: {'OK' if r.get('Status') == 2000 else 'FAIL'}")

    # type7
    r = submit_decision(s, user, ck, period_num, 7,
                        "9999,7999,9999,9999,7999,9999,9999,7999,9999,")
    print(f"  type7: {'OK' if r.get('Status') == 2000 else 'FAIL'}")

    # type8
    r = submit_decision(s, user, ck, period_num, 8,
                        "1,0,0,0,0,0,0,0,0,0,0,0,0,0,0,1,", state=2)
    print(f"  type8: {'OK' if r.get('Status') == 2000 else 'FAIL'}")


def main():
    uid = "9258"
    n = 8

    state = load_state()
    room_id = state.get("room_id")

    if not room_id:
        room_id = find_active_room(uid)
        if not room_id:
            print("No active room, creating not supported yet")
            return
        state["room_id"] = room_id
        save_state(state)

    print(f"Room: {room_id}")

    s, user = login_9001(uid, room_id)
    if s is None:
        print("Login failed")
        return

    period = int(user.get("periodNum"))
    print(f"Period: {period}")

    if period > 4:
        print("Game over, resetting state")
        save_state({})
        return

    ck = {
        "userName": urllib.parse.quote(str(user.get("userName")), safe=""),
        "className": urllib.parse.quote(str(user.get("className")), safe=""),
    }

    key = f"q{period}_submitted"
    if not state.get(key):
        run_decisions(s, user, ck, period, period, n)
        state[key] = True
        state[f"q{period}_flip_time"] = time.time()
        save_state(state)
        print(f"\nQ{period} done. Will flip after 20 min.")
        return

    flip_time = state.get(f"q{period}_flip_time", 0)
    elapsed = time.time() - flip_time
    wait = 1200 - elapsed

    if wait > 0:
        print(f"Wait {wait:.0f}s for period to elapse...")
        if wait > 60:
            print("Too long to sleep, exit and retry later")
            return
        time.sleep(wait + 10)

    print(f"\nFlipping Q{period}...")
    result = flip_room(s, room_id, uid)
    print(f"  Result: {result}")

    if result == "1" or "未到结算时间" not in result:
        del state[key]
        if f"q{period}_flip_time" in state:
            del state[f"q{period}_flip_time"]
        save_state(state)
        print("Flip OK!")
    else:
        print("Not time yet, will retry next run")


if __name__ == "__main__":
    main()
