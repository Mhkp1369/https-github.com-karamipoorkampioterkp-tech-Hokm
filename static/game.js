const socket = io();

const $ = (id) => document.getElementById(id);
const lobby = $("lobby");
const waiting = $("waiting");
const game = $("game");

let mySeat = null;
let lastState = null;
let selectedCard = null;

function show(el) {
  lobby.classList.add("hidden");
  waiting.classList.add("hidden");
  game.classList.add("hidden");
  el.classList.remove("hidden");
}

function isRed(suit) {
  return suit === "♥" || suit === "♦";
}

function cardEl(card, opts = {}) {
  const d = document.createElement("div");
  d.className = "card " + (isRed(card.suit) ? "red" : "black");
  if (opts.playable) d.classList.add("playable");
  if (opts.selected) d.classList.add("selected");
  d.textContent = card.rank + card.suit;
  if (opts.onClick) d.onclick = opts.onClick;
  return d;
}

function cardBack() {
  const d = document.createElement("div");
  d.className = "card back";
  d.textContent = "🂠";
  return d;
}

// ---- لابی ----
$("btnCreate").onclick = () => {
  const name = $("playerName").value.trim() || "بازیکن";
  const max_players = parseInt($("maxPlayers").value, 10);
  $("lobbyError").textContent = "";
  socket.emit("create_room", { name, max_players });
};

$("btnJoin").onclick = () => {
  const name = $("playerName").value.trim() || "بازیکن";
  const code = $("roomCode").value.trim().toUpperCase();
  if (!code) {
    $("lobbyError").textContent = "کد اتاق را وارد کنید";
    return;
  }
  $("lobbyError").textContent = "";
  socket.emit("join_room", { name, code });
};

$("btnStart").onclick = () => socket.emit("start_game");
$("btnCopy").onclick = () => {
  const code = $("waitCode").textContent;
  navigator.clipboard.writeText(code).then(() => {
    $("waitMsg").textContent = "کد کپی شد!";
  }).catch(() => {
    $("waitMsg").textContent = "کد: " + code;
  });
};

document.querySelectorAll(".mode-btn").forEach((btn) => {
  btn.onclick = () => socket.emit("choose_mode", { mode: btn.dataset.mode });
});
document.querySelectorAll(".trump-btn").forEach((btn) => {
  btn.onclick = () => socket.emit("choose_trump", { suit: btn.dataset.suit });
});

socket.on("joined", (data) => {
  mySeat = data.seat;
  $("waitCode").textContent = data.code;
  show(waiting);
});

socket.on("error", (data) => {
  const msg = data.msg || "خطا";
  if (!lobby.classList.contains("hidden")) {
    $("lobbyError").textContent = msg;
  } else if (!waiting.classList.contains("hidden") && $("waitError")) {
    $("waitError").textContent = msg;
  } else if ($("gameMsg")) {
    $("gameMsg").textContent = msg;
  }
});

socket.on("state", (s) => {
  lastState = s;
  if (s.state === "lobby") {
    show(waiting);
    $("waitCode").textContent = s.code;
    mySeat = s.my_seat;
    renderSeatPicker(s);
    const t0 = (s.team_names && s.team_names[0]) || "تیم طلایی";
    const t1 = (s.team_names && s.team_names[1]) || "تیم آبی";
    $("waitMsg").textContent = `${s.player_count} نفر · صندلی خالی = کامپیوتر · ${t0} در برابر ${t1}`;
    if (s.is_host && s.player_count >= 1) {
      $("btnStart").classList.remove("hidden");
    } else {
      $("btnStart").classList.add("hidden");
    }
    return;
  }

  show(game);
  renderGame(s);
});

function renderSeatPicker(s) {
  const bySeat = {};
  (s.players || []).forEach((p) => {
    if (p.name != null) bySeat[p.seat] = p;
  });

  // نام تیم‌ها
  const t0 = (s.team_names && s.team_names[0]) || "تیم طلایی";
  const t1 = (s.team_names && s.team_names[1]) || "تیم آبی";
  if (document.activeElement !== $("teamName0")) $("teamName0").value = t0;
  if (document.activeElement !== $("teamName1")) $("teamName1").value = t1;

  document.querySelectorAll(".seat-btn").forEach((btn) => {
    const seat = parseInt(btn.dataset.seat, 10);
    const p = bySeat[seat];
    const nameEl = btn.querySelector(".sb-name");
    const actEl = btn.querySelector(".sb-action");
    btn.classList.remove("mine", "filled", "empty");
    if (p && p.name) {
      nameEl.textContent = p.name + (p.is_me ? " ★" : "");
      btn.classList.add("filled");
      if (p.is_me) {
        btn.classList.add("mine");
        actEl.textContent = "جای شما";
      } else {
        actEl.textContent = "اشغال شده";
      }
    } else {
      nameEl.textContent = "— خالی —";
      actEl.textContent = "برای نشستن اینجا را لمس کن";
      btn.classList.add("empty");
    }
  });
}

// کلیک روی صندلی
document.querySelectorAll(".seat-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    if (!btn.classList.contains("empty")) return;
    const seat = parseInt(btn.dataset.seat, 10);
    $("waitError").textContent = "";
    socket.emit("change_seat", { seat });
  });
});

// ذخیره نام تیم
function saveTeamName(idx) {
  const el = $(idx === 0 ? "teamName0" : "teamName1");
  const name = el.value.trim();
  if (!name) return;
  socket.emit("set_team_name", { name, team: idx });
}
$("btnSaveTeam0").onclick = () => saveTeamName(0);
$("btnSaveTeam1").onclick = () => saveTeamName(1);

function relativeSeat(absSeat) {
  // صندلی من پایین باشد
  if (mySeat === null) return absSeat;
  return (absSeat - mySeat + 4) % 4;
}

function renderGame(s) {
  mySeat = s.my_seat;

  // امتیاز
  const tn0 = (s.team_names && s.team_names[0]) || "تیم ۱";
  const tn1 = (s.team_names && s.team_names[1]) || "تیم ۲";
  if (s.max_players === 4) {
    $("scoreLine").textContent = `${tn0}: ${s.team_scores[0]} | ${tn1}: ${s.team_scores[1]} | دست: ${s.team_tricks[0]}-${s.team_tricks[1]}`;
  } else {
    $("scoreLine").textContent = "امتیاز: " + s.players.filter(p => p.name).map(p => `${p.name}:${p.tricks}`).join(" ");
  }
  $("modeLine").textContent = s.mode || "";
  $("trumpLine").textContent = s.trump ? `حکم: ${s.trump_name} ${s.trump}` : "بدون حکم";
  $("gameMsg").textContent = s.message || "";

  // صندلی‌ها — mapping: 0 bottom, 1 left, 2 top, 3 right relative
  const map = { 0: "seat-bottom", 1: "seat-left", 2: "seat-top", 3: "seat-right" };
  document.querySelectorAll(".seat").forEach((el) => {
    el.querySelector(".pname").textContent = "";
    el.querySelector(".pname").className = "pname";
    const pc = el.querySelector(".pcards");
    if (pc) pc.textContent = "";
  });

  // پاک کردن کارت‌های بازی‌شده قبلی
  document.querySelectorAll(".played-slot").forEach((slot) => {
    slot.innerHTML = "";
  });

  s.players.forEach((p) => {
    if (!p.name) return;
    const rel = relativeSeat(p.seat);
    const el = document.querySelector("." + map[rel]);
    if (!el) return;
    const nameEl = el.querySelector(".pname");
    nameEl.textContent = p.name + (p.is_me ? " ★" : "");
    if (p.is_hakem) nameEl.classList.add("hakem");
    // نوبت
    if (s.state === "playing") {
      const expected = (s.leader + s.trick.length) % 4;
      if (p.seat === expected) nameEl.classList.add("turn");
    }
    const pc = el.querySelector(".pcards");
    if (pc && !p.is_me) pc.textContent = p.card_count + " کارت";
  });

  // کارت هر بازیکن جلوی خودش (نه وسط روی هم)
  (s.trick || []).forEach((t) => {
    const rel = relativeSeat(t.seat);
    const el = document.querySelector("." + map[rel]);
    if (!el) return;
    const slot = el.querySelector(".played-slot");
    if (slot) slot.appendChild(cardEl(t.card));
  });

  // نمایش پخش حاکم
  const hr = $("hakemReveal");
  if (s.state === "hakem" && s.hakem_reveal && s.hakem_reveal.length) {
    hr.classList.remove("hidden");
    hr.innerHTML = "";
    s.hakem_reveal.forEach((t) => hr.appendChild(cardEl(t.card)));
  } else {
    hr.classList.add("hidden");
  }

  // دست من
  const hand = $("myHand");
  hand.innerHTML = "";
  const canPlay = s.can_play;
  (s.my_hand || []).forEach((c) => {
    const playable = canPlay && isValidLocal(s, c);
    const sel = selectedCard && selectedCard.suit === c.suit && selectedCard.rank === c.rank;
    hand.appendChild(
      cardEl(c, {
        playable,
        selected: sel,
        onClick: () => {
          if (!canPlay || !isValidLocal(s, c)) return;
          if (selectedCard && selectedCard.suit === c.suit && selectedCard.rank === c.rank) {
            socket.emit("play_card", { card: c });
            selectedCard = null;
          } else {
            selectedCard = c;
            renderGame(lastState);
          }
        },
      })
    );
  });

  // پنل‌ها
  const isHakem = s.my_seat === s.hakem;
  $("modePanel").classList.toggle("hidden", !(s.state === "choose_mode" && isHakem));
  $("trumpPanel").classList.toggle("hidden", !(s.state === "choose_trump" && isHakem));
  $("gameOver").classList.toggle("hidden", s.state !== "game_over");
  if (s.state === "game_over") {
    $("winnerText").textContent = s.message || "بازی تمام شد";
  }
}

function isValidLocal(s, card) {
  if (!s.led_suit) return true;
  const hasLed = (s.my_hand || []).some((c) => c.suit === s.led_suit);
  if (hasLed && card.suit !== s.led_suit) return false;
  return true;
}

// نام تصادفی پیش‌فرض
const names = ["علی", "رضا", "سارا", "مریم", "حسین", "نگار", "امیر", "زهرا"];
$("playerName").value = names[Math.floor(Math.random() * names.length)];
