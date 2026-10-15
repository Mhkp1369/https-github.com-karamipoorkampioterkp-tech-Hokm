# -*- coding: utf-8 -*-
"""
سرور بازی حکم آنلاین - چندنفره (۲ تا ۴ نفر)
"""
import random
import string
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple

from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit, join_room, leave_room

app = Flask(__name__)
app.config["SECRET_KEY"] = "hokm-secret-change-in-production"
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="eventlet")

SUITS = ["♠", "♥", "♦", "♣"]
SUIT_NAMES = {"♠": "پیک", "♥": "دل", "♦": "خشت", "♣": "گشنیز"}
RANKS = ["2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A"]
RANK_VAL = {r: i for i, r in enumerate(RANKS)}


class Mode(str, Enum):
    NORMAL = "حکم معمولی"
    NARS = "نرس"
    TAK_NARS = "تک‌نرس"


def card_value(rank: str, mode: Mode) -> int:
    base = RANK_VAL[rank]
    if mode == Mode.NARS:
        return 12 - base
    if mode == Mode.TAK_NARS:
        return 12 if rank == "A" else 11 - base
    return base


def new_deck() -> List[dict]:
    cards = [{"suit": s, "rank": r} for s in SUITS for r in RANKS]
    random.shuffle(cards)
    return cards


def room_code() -> str:
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=5))


BOT_NAMES = [
    "کامران", "نرگس", "بهراد", "لیلا", "کیان", "آیدا",
    "پویا", "هستی", "آرمان", "نیلوفر", "سامان", "مهسا",
]


@dataclass
class Player:
    sid: str
    name: str
    seat: int
    hand: List[dict] = field(default_factory=list)
    tricks: int = 0
    connected: bool = True
    is_bot: bool = False


@dataclass
class Room:
    code: str
    host_sid: str
    players: Dict[str, Player] = field(default_factory=dict)  # sid -> Player
    seats: List[Optional[str]] = field(default_factory=lambda: [None, None, None, None])
    max_players: int = 4
    state: str = "lobby"  # lobby, hakem, choose_mode, choose_trump, playing, hand_end, game_over
    hakem: int = 0
    trump: Optional[str] = None
    mode: Mode = Mode.NORMAL
    leader: int = 0
    trick: List[Tuple[int, dict]] = field(default_factory=list)  # (seat, card)
    led_suit: Optional[str] = None
    team_scores: List[int] = field(default_factory=lambda: [0, 0])  # for 4p teams
    individual_scores: List[int] = field(default_factory=lambda: [0, 0, 0, 0])
    team_tricks: List[int] = field(default_factory=lambda: [0, 0])
    team_names: List[str] = field(default_factory=lambda: ["تیم طلایی", "تیم آبی"])  # 0=seats0+2, 1=seats1+3
    target: int = 7
    deck: List[dict] = field(default_factory=list)
    hakem_reveal: List[Tuple[int, dict]] = field(default_factory=list)
    message: str = ""


rooms: Dict[str, Room] = {}
sid_to_room: Dict[str, str] = {}


def get_room(sid: str) -> Optional[Room]:
    code = sid_to_room.get(sid)
    return rooms.get(code) if code else None


def seat_of(room: Room, sid: str) -> Optional[int]:
    p = room.players.get(sid)
    return p.seat if p else None


def occupied_seats(room: Room) -> List[int]:
    return [i for i, s in enumerate(room.seats) if s]


def fill_bots(room: Room):
    """صندلی‌های خالی تا ۴ نفر را با ربات پر کن — فقط اسم، بدون نمایش سطح"""
    used = {p.name for p in room.players.values()}
    names = [n for n in BOT_NAMES if n not in used]
    random.shuffle(names)
    for seat in range(4):
        if room.seats[seat] is None:
            bot_id = f"bot_{room.code}_{seat}"
            name = names.pop() if names else f"بازیکن{seat+1}"
            bot = Player(sid=bot_id, name=name, seat=seat, is_bot=True, connected=True)
            room.players[bot_id] = bot
            room.seats[seat] = bot_id


def ai_choose_mode_and_trump(hand: List[dict]) -> Tuple[Mode, Optional[str]]:
    """هوش قوی برای انتخاب حالت و حکم"""
    by_suit = defaultdict(list)
    for c in hand:
        by_suit[c["suit"]].append(c)
    best_suit = max(SUITS, key=lambda s: (
        len(by_suit[s]),
        sum(RANK_VAL[c["rank"]] for c in by_suit[s]),
    ))
    high = sum(1 for c in hand if RANK_VAL[c["rank"]] >= 9)
    long_suit = max(len(by_suit[s]) for s in SUITS)
    if high <= 1 and long_suit <= 2 and random.random() < 0.4:
        return (Mode.NARS if random.random() < 0.55 else Mode.TAK_NARS), None
    return Mode.NORMAL, best_suit


def ai_pick_card(room: Room, seat: int) -> dict:
    """بازی فوق‌حرفه‌ای کارت"""
    sid = room.seats[seat]
    hand = room.players[sid].hand
    led = room.led_suit
    if led and any(c["suit"] == led for c in hand):
        candidates = [c for c in hand if c["suit"] == led]
    else:
        candidates = list(hand)

    mode = room.mode
    trump = room.trump

    def val(c):
        return card_value(c["rank"], mode)

    if not room.trick:
        # شروع: ترجیح حکم یا کارت قوی
        if mode == Mode.NORMAL and trump:
            tr = [c for c in candidates if c["suit"] == trump]
            if tr and random.random() < 0.5:
                return max(tr, key=val)
        return max(candidates, key=val)

    # آیا می‌تواند ببرد؟
    best_val = -1
    has_trump_played = any(
        mode == Mode.NORMAL and trump and c["suit"] == trump for _, c in room.trick
    )
    for _, c in room.trick:
        v = val(c)
        if mode == Mode.NORMAL:
            if trump and c["suit"] == trump:
                if v > best_val:
                    best_val = v
            elif not has_trump_played and c["suit"] == led and v > best_val:
                best_val = v
        else:
            if c["suit"] == led and v > best_val:
                best_val = v

    can_win = []
    for c in candidates:
        v = val(c)
        if mode == Mode.NORMAL:
            if trump and c["suit"] == trump:
                if not has_trump_played or v > best_val:
                    can_win.append(c)
            elif not has_trump_played and c["suit"] == led and v > best_val:
                can_win.append(c)
        else:
            if c["suit"] == led and v > best_val:
                can_win.append(c)

    if can_win:
        return min(can_win, key=val)  # کم‌ترین کارت برنده‌کننده
    return min(candidates, key=val)  # دور انداختن ضعیف


def schedule_ai(room: Room):
    """اگر نوبت ربات است، بعد از کمی تأخیر بازی کند"""
    socketio.start_background_task(_ai_turn_worker, room.code)


def _ai_turn_worker(code: str):
    import eventlet
    eventlet.sleep(0.7)
    room = rooms.get(code)
    if not room:
        return

    if room.state == "choose_mode":
        sid = room.seats[room.hakem]
        if sid and room.players[sid].is_bot:
            hand = room.players[sid].hand
            mode, trump = ai_choose_mode_and_trump(hand)
            room.mode = mode
            if mode in (Mode.NARS, Mode.TAK_NARS):
                room.trump = None
                finish_deal(room)
            else:
                room.trump = trump
                # مستقیماً حکم را هم انتخاب می‌کند
                finish_deal(room)
        return

    if room.state == "choose_trump":
        sid = room.seats[room.hakem]
        if sid and room.players[sid].is_bot:
            hand = room.players[sid].hand
            _, trump = ai_choose_mode_and_trump(hand)
            room.trump = trump or SUITS[0]
            finish_deal(room)
        return

    if room.state == "playing":
        seat = expected_seat(room)
        sid = room.seats[seat]
        if not sid or not room.players[sid].is_bot:
            return
        if not room.players[sid].hand:
            return
        card = ai_pick_card(room, seat)
        remove_card(room.players[sid].hand, card)
        if not room.trick:
            room.led_suit = card["suit"]
        room.trick.append((seat, card))
        room.message = f"{room.players[sid].name} بازی کرد"
        broadcast_room(room)
        active = [i for i in range(4) if room.seats[i]]
        if len(room.trick) >= len(active):
            eventlet.sleep(1.0)
            room2 = rooms.get(code)
            if room2 and room2.state == "playing" and room2.trick:
                resolve_trick(room2)
        else:
            schedule_ai(room)


def public_state(room: Room, for_sid: str) -> dict:
    """وضعیت عمومی برای یک کلاینت (دست خودش را می‌بیند)"""
    me = room.players.get(for_sid)
    players_info = []
    for i in range(4):
        sid = room.seats[i]
        if sid and sid in room.players:
            p = room.players[sid]
            players_info.append({
                "seat": i,
                "name": p.name,
                "card_count": len(p.hand),
                "tricks": p.tricks,
                "connected": p.connected,
                "is_me": sid == for_sid,
                "is_hakem": i == room.hakem,
            })
        else:
            players_info.append({
                "seat": i,
                "name": None,
                "card_count": 0,
                "tricks": 0,
                "connected": False,
                "is_me": False,
                "is_hakem": False,
            })

    my_hand = []
    if me:
        my_hand = sorted(
            me.hand,
            key=lambda c: (
                c["suit"] != room.trump if room.trump else False,
                c["suit"],
                -RANK_VAL[c["rank"]],
            ),
        )

    return {
        "code": room.code,
        "state": room.state,
        "max_players": room.max_players,
        "player_count": len(room.players),
        "players": players_info,
        "my_seat": me.seat if me else None,
        "my_hand": my_hand,
        "hakem": room.hakem,
        "trump": room.trump,
        "trump_name": SUIT_NAMES.get(room.trump, "") if room.trump else "",
        "mode": room.mode.value if isinstance(room.mode, Mode) else room.mode,
        "leader": room.leader,
        "trick": [{"seat": s, "card": c} for s, c in room.trick],
        "led_suit": room.led_suit,
        "team_scores": room.team_scores,
        "team_tricks": room.team_tricks,
        "team_names": room.team_names,
        "individual_scores": room.individual_scores,
        "target": room.target,
        "message": room.message,
        "is_host": room.host_sid == for_sid,
        "hakem_reveal": [{"seat": s, "card": c} for s, c in room.hakem_reveal[-12:]],
        "can_play": (
            room.state == "playing"
            and me is not None
            and expected_seat(room) == me.seat
        ),
    }


def broadcast_room(room: Room):
    for sid in list(room.players.keys()):
        if room.players[sid].connected:
            socketio.emit("state", public_state(room, sid), room=sid)


def valid_play(room: Room, seat: int, card: dict) -> bool:
    p_sid = room.seats[seat]
    if not p_sid:
        return False
    p = room.players[p_sid]
    if card not in p.hand:
        # compare by value
        if not any(c["suit"] == card["suit"] and c["rank"] == card["rank"] for c in p.hand):
            return False
    if not room.trick:
        return True
    led = room.led_suit
    has_led = any(c["suit"] == led for c in p.hand)
    if has_led and card["suit"] != led:
        return False
    return True


def remove_card(hand: List[dict], card: dict):
    for i, c in enumerate(hand):
        if c["suit"] == card["suit"] and c["rank"] == card["rank"]:
            hand.pop(i)
            return


def trick_winner(room: Room) -> int:
    led = room.trick[0][1]["suit"]
    best_i = 0
    best_val = -1
    best_trump = False
    for i, (seat, card) in enumerate(room.trick):
        is_trump = room.mode == Mode.NORMAL and room.trump and card["suit"] == room.trump
        val = card_value(card["rank"], room.mode)
        if room.mode == Mode.NORMAL:
            if is_trump:
                if not best_trump or val > best_val:
                    best_val, best_i, best_trump = val, i, True
            elif not best_trump and card["suit"] == led and val > best_val:
                best_val, best_i = val, i
        else:
            if card["suit"] == led and val > best_val:
                best_val, best_i = val, i
    return room.trick[best_i][0]


def start_hand(room: Room):
    for p in room.players.values():
        p.hand = []
        p.tricks = 0
    room.team_tricks = [0, 0]
    room.trick = []
    room.led_suit = None
    room.trump = None
    room.mode = Mode.NORMAL
    room.deck = new_deck()
    room.hakem_reveal = []
    room.state = "hakem"
    room.message = "تعیین حاکم — پخش کارت تا آس"
    broadcast_room(room)
    socketio.start_background_task(hakem_animation, room.code)


def hakem_animation(code: str):
    import eventlet
    room = rooms.get(code)
    if not room:
        return
    deck = new_deck()
    seats = occupied_seats(room)
    if not seats:
        return
    # فقط روی صندلی‌های پر پخش کن؛ برای سادگی روی هر ۴ صندلی اگر پر باشد
    order = [i for i in range(4) if room.seats[i]]
    if not order:
        order = [0]
    idx = 0
    found = False
    while deck and not found:
        seat = order[idx % len(order)]
        card = deck.pop()
        room.hakem_reveal.append((seat, card))
        room.message = f"کارت برای {room.players[room.seats[seat]].name if room.seats[seat] else seat}"
        broadcast_room(room)
        eventlet.sleep(0.4)
        if card["rank"] == "A":
            room.hakem = seat
            room.hakem_found = True
            name = room.players[room.seats[seat]].name if room.seats[seat] else "?"
            room.message = f"{name} حاکم شد!"
            found = True
            broadcast_room(room)
            eventlet.sleep(1.5)
            break
        idx += 1
    if not found:
        room.hakem = order[0]
        room.message = f"{room.players[room.seats[room.hakem]].name} حاکم شد!"
        broadcast_room(room)
        eventlet.sleep(1.0)

    # پخش ۵ کارت اول
    room.deck = new_deck()
    for _ in range(5):
        for s in range(4):
            if room.seats[s]:
                room.players[room.seats[s]].hand.append(room.deck.pop())
    room.state = "choose_mode"
    hakem_sid = room.seats[room.hakem]
    if hakem_sid:
        room.message = f"حاکم ({room.players[hakem_sid].name}) حالت بازی را انتخاب کند"
    broadcast_room(room)
    schedule_ai(room)


def finish_deal(room: Room):
    for _ in range(8):
        for s in range(4):
            if room.seats[s] and room.deck:
                room.players[room.seats[s]].hand.append(room.deck.pop())
    room.leader = room.hakem
    room.state = "playing"
    if room.mode == Mode.NORMAL and room.trump:
        room.message = f"شروع | حکم: {SUIT_NAMES[room.trump]}"
    else:
        room.message = f"شروع | {room.mode.value} (بدون حکم)"
    broadcast_room(room)
    schedule_ai(room)


def resolve_trick(room: Room):
    winner = trick_winner(room)
    w_sid = room.seats[winner]
    if w_sid:
        room.players[w_sid].tricks += 1
    if room.max_players == 4 and all(room.seats):
        team = 0 if winner in (0, 2) else 1
        room.team_tricks[team] += 1
    room.message = f"برنده دست: {room.players[w_sid].name if w_sid else winner}"
    room.leader = winner
    room.trick = []
    room.led_suit = None
    broadcast_room(room)

    # پایان دست؟
    if room.max_players == 4 and all(room.seats):
        if room.team_tricks[0] >= 7 or room.team_tricks[1] >= 7:
            end_hand(room)
            return
    else:
        # آزاد: هر کس ۷ دست ببرد
        for p in room.players.values():
            if p.tricks >= 7:
                end_hand(room)
                return
    next_sid = room.seats[room.leader]
    if next_sid:
        room.message = f"نوبت {room.players[next_sid].name}"
    broadcast_room(room)
    schedule_ai(room)


def end_hand(room: Room):
    if room.max_players == 4 and all(room.seats):
        if room.team_tricks[0] >= 7:
            pts = 1
            if room.team_tricks[1] == 0:
                pts = 2 if room.hakem in (0, 2) else 3
            room.team_scores[0] += pts
            room.message = f"تیم ۱ برد (+{pts})"
        else:
            pts = 1
            if room.team_tricks[0] == 0:
                pts = 2 if room.hakem in (1, 3) else 3
            room.team_scores[1] += pts
            room.message = f"تیم ۲ برد (+{pts})"
        if max(room.team_scores) >= room.target:
            room.state = "game_over"
            room.message += " — پایان بازی!"
            broadcast_room(room)
            return
        # حاکم بعدی
        if not (
            (room.team_tricks[0] >= 7 and room.hakem in (0, 2))
            or (room.team_tricks[1] >= 7 and room.hakem in (1, 3))
        ):
            # صندلی بعدی پر
            for _ in range(4):
                room.hakem = (room.hakem + 1) % 4
                if room.seats[room.hakem]:
                    break
    else:
        # امتیاز انفرادی
        best = max(room.players.values(), key=lambda p: p.tricks)
        room.individual_scores[best.seat] += 1
        room.message = f"{best.name} این دست را برد"
        if max(room.individual_scores) >= room.target:
            room.state = "game_over"
            broadcast_room(room)
            return

    room.state = "hand_end"
    broadcast_room(room)
    socketio.start_background_task(next_hand_delay, room.code)


def next_hand_delay(code: str):
    import eventlet
    eventlet.sleep(2.5)
    room = rooms.get(code)
    if room and room.state == "hand_end":
        start_hand(room)


# ---------- HTTP ----------
@app.route("/")
def index():
    return render_template("index.html")


# ---------- Socket ----------
@socketio.on("create_room")
def on_create(data):
    name = (data or {}).get("name", "بازیکن").strip()[:16] or "بازیکن"
    max_p = int((data or {}).get("max_players", 4))
    max_p = max(2, min(4, max_p))
    code = room_code()
    while code in rooms:
        code = room_code()
    room = Room(code=code, host_sid=request.sid, max_players=max_p)
    player = Player(sid=request.sid, name=name, seat=0)
    room.players[request.sid] = player
    room.seats[0] = request.sid
    rooms[code] = room
    sid_to_room[request.sid] = code
    join_room(code)
    join_room(request.sid)
    emit("joined", {"code": code, "seat": 0})
    broadcast_room(room)


@socketio.on("join_room")
def on_join(data):
    code = (data or {}).get("code", "").strip().upper()
    name = (data or {}).get("name", "بازیکن").strip()[:16] or "بازیکن"
    room = rooms.get(code)
    if not room:
        emit("error", {"msg": "اتاق پیدا نشد"})
        return
    if room.state != "lobby":
        emit("error", {"msg": "بازی شروع شده است"})
        return
    if len(room.players) >= room.max_players:
        emit("error", {"msg": "اتاق پر است"})
        return
    # اولین صندلی خالی
    seat = next((i for i, s in enumerate(room.seats) if s is None and i < room.max_players), None)
    if seat is None:
        emit("error", {"msg": "صندلی خالی نیست"})
        return
    player = Player(sid=request.sid, name=name, seat=seat)
    room.players[request.sid] = player
    room.seats[seat] = request.sid
    sid_to_room[request.sid] = code
    join_room(code)
    join_room(request.sid)
    emit("joined", {"code": code, "seat": seat})
    broadcast_room(room)


@socketio.on("change_seat")
def on_change_seat(data):
    """بازیکن صندلی خودش را عوض می‌کند تا هم‌تیمی را انتخاب کند.
    تیم‌ها: صندلی ۰ و ۲ با هم | صندلی ۱ و ۳ با هم
    """
    room = get_room(request.sid)
    if not room or room.state != "lobby":
        emit("error", {"msg": "فقط در لابی می‌توان صندلی عوض کرد"})
        return
    new_seat = (data or {}).get("seat")
    try:
        new_seat = int(new_seat)
    except (TypeError, ValueError):
        return
    if new_seat < 0 or new_seat > 3:
        return
    p = room.players.get(request.sid)
    if not p or p.is_bot:
        return
    if p.seat == new_seat:
        return
    if room.seats[new_seat] is not None:
        emit("error", {"msg": "این صندلی پر است"})
        return
    old = p.seat
    room.seats[old] = None
    room.seats[new_seat] = request.sid
    p.seat = new_seat
    team = room.team_names[0] if new_seat in (0, 2) else room.team_names[1]
    room.message = f"{p.name} → صندلی {new_seat + 1} ({team})"
    broadcast_room(room)


@socketio.on("set_team_name")
def on_set_team_name(data):
    """نام‌گذاری تیم — بازیکن تیم خودش، یا میزبان هر دو تیم"""
    room = get_room(request.sid)
    if not room or room.state != "lobby":
        return
    p = room.players.get(request.sid)
    if not p or p.is_bot:
        return
    name = ((data or {}).get("name") or "").strip()[:20]
    if not name:
        return
    try:
        team_idx = int((data or {}).get("team", -1))
    except (TypeError, ValueError):
        team_idx = -1
    if team_idx not in (0, 1):
        team_idx = 0 if p.seat in (0, 2) else 1
    my_team = 0 if p.seat in (0, 2) else 1
    is_host = room.host_sid == request.sid
    if team_idx != my_team and not is_host:
        emit("error", {"msg": "فقط نام تیم خودتان را می‌توانید عوض کنید"})
        return
    room.team_names[team_idx] = name
    room.message = f"نام تیم: {name}"
    broadcast_room(room)


@socketio.on("start_game")
def on_start():
    room = get_room(request.sid)
    if not room or room.host_sid != request.sid:
        emit("error", {"msg": "فقط میزبان می‌تواند شروع کند"})
        return
    humans = sum(1 for p in room.players.values() if not p.is_bot)
    if humans < 1:
        emit("error", {"msg": "حداقل یک بازیکن لازم است"})
        return
    # صندلی‌های خالی = ربات (فوق‌حرفه‌ای، فقط اسم)
    fill_bots(room)
    room.max_players = 4  # همیشه ۴ نفره با پر کردن ربات
    start_hand(room)


@socketio.on("choose_mode")
def on_mode(data):
    room = get_room(request.sid)
    if not room or room.state != "choose_mode":
        return
    if seat_of(room, request.sid) != room.hakem:
        emit("error", {"msg": "فقط حاکم حالت را انتخاب می‌کند"})
        return
    mode_str = (data or {}).get("mode", "حکم معمولی")
    mapping = {
        "حکم معمولی": Mode.NORMAL,
        "نرس": Mode.NARS,
        "تک‌نرس": Mode.TAK_NARS,
        "normal": Mode.NORMAL,
        "nars": Mode.NARS,
        "tak_nars": Mode.TAK_NARS,
    }
    room.mode = mapping.get(mode_str, Mode.NORMAL)
    if room.mode in (Mode.NARS, Mode.TAK_NARS):
        room.trump = None
        finish_deal(room)
    else:
        room.state = "choose_trump"
        room.message = "حاکم خال حکم را انتخاب کند"
        broadcast_room(room)


@socketio.on("choose_trump")
def on_trump(data):
    room = get_room(request.sid)
    if not room or room.state != "choose_trump":
        return
    if seat_of(room, request.sid) != room.hakem:
        return
    suit = (data or {}).get("suit")
    if suit not in SUITS:
        return
    room.trump = suit
    finish_deal(room)


def next_occupied(room: Room, start: int) -> int:
    s = start % 4
    for _ in range(4):
        if room.seats[s]:
            return s
        s = (s + 1) % 4
    return start


def expected_seat(room: Room) -> int:
    """صندلی نفر بعدی با رد شدن از صندلی خالی"""
    s = room.leader
    for i in range(len(room.trick)):
        s = next_occupied(room, (s + 1) % 4)
    return next_occupied(room, s) if room.trick else next_occupied(room, room.leader)


@socketio.on("play_card")
def on_play(data):
    room = get_room(request.sid)
    if not room or room.state != "playing":
        return
    seat = seat_of(room, request.sid)
    if seat is None:
        return
    expected = expected_seat(room)
    if seat != expected:
        emit("error", {"msg": "نوبت شما نیست"})
        return
    card = (data or {}).get("card")
    if not card:
        return
    if not valid_play(room, seat, card):
        emit("error", {"msg": "این کارت مجاز نیست"})
        return
    remove_card(room.players[request.sid].hand, card)
    if not room.trick:
        room.led_suit = card["suit"]
    room.trick.append((seat, card))
    broadcast_room(room)

    active = [i for i in range(4) if room.seats[i]]
    if len(room.trick) >= len(active):
        socketio.start_background_task(_delayed_resolve, room.code)
    else:
        schedule_ai(room)


def _delayed_resolve(code: str):
    import eventlet
    eventlet.sleep(1.2)
    room = rooms.get(code)
    if room and room.state == "playing" and room.trick:
        resolve_trick(room)


@socketio.on("disconnect")
def on_disconnect():
    room = get_room(request.sid)
    if not room:
        return
    p = room.players.get(request.sid)
    if p:
        p.connected = False
        room.message = f"{p.name} قطع شد"
    if request.sid in sid_to_room:
        del sid_to_room[request.sid]
    # اگر میزبان رفت و کسی ماند
    if room.host_sid == request.sid:
        for sid in room.players:
            if sid != request.sid and room.players[sid].connected:
                room.host_sid = sid
                break
    broadcast_room(room)
    # پاکسازی اتاق خالی
    if all(not pl.connected for pl in room.players.values()):
        code = room.code
        if code in rooms:
            del rooms[code]


@socketio.on("reconnect_name")
def on_reconnect(data):
    """اختیاری — برای آینده"""
    pass


if __name__ == "__main__":
    print("حکم آنلاین — http://0.0.0.0:5000")
    socketio.run(app, host="0.0.0.0", port=5000, debug=True)
