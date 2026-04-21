/* SG 4D & Toto generator -- entertainment only */

// ------------------------------------------------------------
// Draw schedule: 4D = Wed(3), Sat(6), Sun(0); Toto = Mon(1), Thu(4)
// ------------------------------------------------------------
const FOURD_DAYS = [0, 3, 6];
const TOTO_DAYS = [1, 4];
const DAY_NAMES = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

function nextDraw(daysArr, from = new Date()) {
  for (let i = 0; i < 7; i++) {
    const d = new Date(from);
    d.setDate(from.getDate() + i);
    if (daysArr.includes(d.getDay())) {
      return { date: d, daysAway: i };
    }
  }
  return null;
}

function renderReminder() {
  const el = document.getElementById("draw-reminder");
  const now = new Date();
  const today = now.getDay();
  const fourDToday = FOURD_DAYS.includes(today);
  const totoToday = TOTO_DAYS.includes(today);

  const next4D = nextDraw(FOURD_DAYS, now);
  const nextToto = nextDraw(TOTO_DAYS, now);

  const fmt = (d) => `${DAY_NAMES[d.getDay()]} ${d.getDate()}/${d.getMonth() + 1}`;
  const whenWord = (n) => (n === 0 ? "today" : n === 1 ? "tomorrow" : `in ${n} days`);

  let parts = [];
  if (fourDToday) parts.push(`<span class="hot">4D draws today</span>`);
  else parts.push(`Next 4D: ${fmt(next4D.date)} (${whenWord(next4D.daysAway)})`);
  if (totoToday) parts.push(`<span class="hot">TOTO draws today</span>`);
  else parts.push(`Next TOTO: ${fmt(nextToto.date)} (${whenWord(nextToto.daysAway)})`);

  el.innerHTML = parts.join(" &nbsp;&middot;&nbsp; ") + " &nbsp;&middot;&nbsp; 6:30 PM SGT";
}

// ------------------------------------------------------------
// Tabs
// ------------------------------------------------------------
document.querySelectorAll(".tab").forEach((btn) => {
  btn.addEventListener("click", () => {
    const target = btn.dataset.tab;
    document.querySelectorAll(".tab").forEach((t) => {
      const active = t === btn;
      t.classList.toggle("active", active);
      t.setAttribute("aria-selected", active ? "true" : "false");
    });
    document.querySelectorAll(".panel").forEach((p) => {
      p.classList.toggle("active", p.id === target);
    });
  });
});

// ------------------------------------------------------------
// Hint state (populated by chat box)
// ------------------------------------------------------------
const hints = {
  preferredDigits: new Set(),   // 0-9, bias individual digits for 4D
  preferredTotoNums: new Set(), // 1-49, forced into Toto picks
  fullFourD: null,              // exact 4-digit string if user gave one
  avoidDigits: new Set(),
  avoidTotoNums: new Set(),
  parity: null,                 // 'even' | 'odd'
  notes: [],
};

function hintsEmpty() {
  return (
    hints.preferredDigits.size === 0 &&
    hints.preferredTotoNums.size === 0 &&
    !hints.fullFourD &&
    hints.avoidDigits.size === 0 &&
    hints.avoidTotoNums.size === 0 &&
    !hints.parity
  );
}

function summariseHints() {
  if (hintsEmpty()) return "No active hints -- generation is fully random.";
  const bits = [];
  if (hints.fullFourD) bits.push(`seed 4D ${hints.fullFourD}`);
  if (hints.preferredDigits.size)
    bits.push(`prefer digits ${[...hints.preferredDigits].sort().join(",")}`);
  if (hints.preferredTotoNums.size)
    bits.push(
      `prefer Toto ${[...hints.preferredTotoNums].sort((a, b) => a - b).join(",")}`
    );
  if (hints.avoidDigits.size)
    bits.push(`avoid digits ${[...hints.avoidDigits].sort().join(",")}`);
  if (hints.avoidTotoNums.size)
    bits.push(
      `avoid Toto ${[...hints.avoidTotoNums].sort((a, b) => a - b).join(",")}`
    );
  if (hints.parity) bits.push(`${hints.parity} only`);
  return "Active hints: " + bits.join("; ");
}

function renderHintState() {
  document.getElementById("chat-state").textContent = summariseHints();
}

// ------------------------------------------------------------
// Hint parser
// ------------------------------------------------------------
function parseHint(rawText) {
  const text = rawText.toLowerCase();
  const notes = [];

  // Reset command
  if (/\b(reset|clear|start over|forget)\b/.test(text)) {
    hints.preferredDigits.clear();
    hints.preferredTotoNums.clear();
    hints.avoidDigits.clear();
    hints.avoidTotoNums.clear();
    hints.fullFourD = null;
    hints.parity = null;
    return ["Cleared all hints. Back to fully random."];
  }

  // Parity
  if (/\beven\b/.test(text)) { hints.parity = "even"; notes.push("prefer even numbers"); }
  if (/\bodd\b/.test(text)) { hints.parity = "odd"; notes.push("prefer odd numbers"); }

  // Avoid / skip phrases
  const avoidMatches = text.matchAll(/(?:avoid|no|not|skip|except|exclude)\s+([\d ,and]+)/g);
  for (const m of avoidMatches) {
    const nums = extractNumbers(m[1]);
    nums.forEach((n) => {
      if (n >= 0 && n <= 9) hints.avoidDigits.add(n);
      if (n >= 1 && n <= 49) hints.avoidTotoNums.add(n);
    });
    if (nums.length) notes.push("avoiding " + nums.join(","));
  }

  // Full 4D seed
  const fullFour = text.match(/\b(\d{4})\b/);
  if (fullFour) {
    hints.fullFourD = fullFour[1];
    notes.push(`seed 4D ${fullFour[1]}`);
    // also feed its digits as preferences
    for (const ch of fullFour[1]) hints.preferredDigits.add(Number(ch));
  }

  // Birthdays / dates: DD/MM or DD Month
  const dmMatch = text.match(/\b(\d{1,2})[\/\-\. ](\d{1,2})(?:[\/\-\. ](\d{2,4}))?\b/);
  if (dmMatch) {
    const dd = Number(dmMatch[1]);
    const mm = Number(dmMatch[2]);
    if (dd >= 1 && dd <= 31) addTotoNum(dd);
    if (mm >= 1 && mm <= 12) addTotoNum(mm);
    if (dmMatch[3]) {
      const yearDigits = dmMatch[3].slice(-2);
      for (const ch of yearDigits) hints.preferredDigits.add(Number(ch));
    }
    notes.push("using your date");
  }

  // Month-name dates ("14 March", "March 14")
  const months = ["january","february","march","april","may","june","july","august","september","october","november","december"];
  for (let i = 0; i < months.length; i++) {
    const re1 = new RegExp(`\\b(\\d{1,2})\\s+${months[i]}\\b`);
    const re2 = new RegExp(`\\b${months[i]}\\s+(\\d{1,2})\\b`);
    const m = text.match(re1) || text.match(re2);
    if (m) {
      const dd = Number(m[1]);
      if (dd >= 1 && dd <= 31) addTotoNum(dd);
      addTotoNum(i + 1);
      notes.push(`using ${months[i]} date`);
      break;
    }
  }

  // Any stray numbers the user mentions (lucky 7, 23, etc.)
  const strays = extractNumbers(text);
  strays.forEach((n) => {
    if (n >= 1 && n <= 49) addTotoNum(n);
    if (n >= 0 && n <= 9) hints.preferredDigits.add(n);
    if (n > 49 && n < 100) {
      // split two-digit into digits
      hints.preferredDigits.add(Math.floor(n / 10));
      hints.preferredDigits.add(n % 10);
    }
  });

  // Feelings / themes -- use text as seed for perceived "direction"
  const themeWords = ["lucky","birthday","anniversary","love","money","wealth","family","happy","new","dream"];
  const foundThemes = themeWords.filter((w) => text.includes(w));
  if (foundThemes.length) notes.push("theme: " + foundThemes.join(", "));

  if (!notes.length) notes.push("noted -- no specific numeric hints found, text kept as vibe");
  return notes;
}

function addTotoNum(n) {
  if (n >= 1 && n <= 49 && !hints.avoidTotoNums.has(n)) {
    hints.preferredTotoNums.add(n);
  }
}

function extractNumbers(s) {
  const out = [];
  const re = /\d+/g;
  let m;
  while ((m = re.exec(s)) !== null) out.push(Number(m[0]));
  return out;
}

// ------------------------------------------------------------
// Random helpers
// ------------------------------------------------------------
function randInt(min, max) {
  return Math.floor(Math.random() * (max - min + 1)) + min;
}

function pickDigit() {
  for (let tries = 0; tries < 20; tries++) {
    let d;
    if (hints.preferredDigits.size && Math.random() < 0.55) {
      const arr = [...hints.preferredDigits];
      d = arr[randInt(0, arr.length - 1)];
    } else {
      d = randInt(0, 9);
    }
    if (hints.avoidDigits.has(d)) continue;
    if (hints.parity === "even" && d % 2 !== 0) continue;
    if (hints.parity === "odd" && d % 2 === 0) continue;
    return d;
  }
  return randInt(0, 9);
}

function generateFourDNumber() {
  if (hints.fullFourD && Math.random() < 0.35) {
    return hints.fullFourD;
  }
  let digits = [];
  for (let i = 0; i < 4; i++) digits.push(pickDigit());
  return digits.join("");
}

function permutations(str) {
  if (str.length <= 1) return [str];
  const set = new Set();
  for (let i = 0; i < str.length; i++) {
    const rest = str.slice(0, i) + str.slice(i + 1);
    for (const p of permutations(rest)) set.add(str[i] + p);
  }
  return [...set];
}

// ------------------------------------------------------------
// 4D render
// ------------------------------------------------------------
function renderFourD() {
  const count = clamp(Number(document.getElementById("fourd-count").value) || 1, 1, 12);
  const bet = document.getElementById("fourd-bettype").value;
  const out = document.getElementById("fourd-results");
  out.innerHTML = "";

  for (let i = 0; i < count; i++) {
    const num = generateFourDNumber();
    const entry = document.createElement("div");
    entry.className = "entry";

    let body = `<div class="entry-label">Entry ${i + 1} &middot; ${betLabel(bet)}</div>`;
    body += `<div class="numbers">${num
      .split("")
      .map((d) => `<span class="digit-box">${d}</span>`)
      .join("")}</div>`;

    if (bet === "ibet") {
      const perms = permutations(num);
      body += `<div class="meta">iBet covers ${perms.length} permutation(s): ${perms.join(", ")}</div>`;
    } else if (bet === "roll") {
      const rollPos = randInt(0, 3);
      const rolled = num.split("");
      rolled[rollPos] = "R";
      body += `<div class="meta">Rolling digit at position ${rollPos + 1}: ${rolled.join("")} (covers 10 numbers 0-9 in that slot)</div>`;
    } else if (bet === "system") {
      // Unique-digit system entry
      const uniqueNum = forceUniqueDigits(num);
      const perms = permutations(uniqueNum);
      body = `<div class="entry-label">Entry ${i + 1} &middot; System Entry (24 perms)</div>`;
      body += `<div class="numbers">${uniqueNum
        .split("")
        .map((d) => `<span class="digit-box">${d}</span>`)
        .join("")}</div>`;
      body += `<div class="meta">Covers ${perms.length} permutations.</div>`;
    } else {
      body += `<div class="meta">Ordinary bet -- Big covers all 23 prizes, Small covers top 3.</div>`;
    }

    entry.innerHTML = body;
    out.appendChild(entry);
  }
}

function forceUniqueDigits(num) {
  const digits = num.split("").map(Number);
  const used = new Set();
  for (let i = 0; i < digits.length; i++) {
    if (used.has(digits[i])) {
      for (let d = 0; d < 10; d++) {
        if (!used.has(d) && !hints.avoidDigits.has(d)) { digits[i] = d; break; }
      }
    }
    used.add(digits[i]);
  }
  return digits.join("");
}

function betLabel(b) {
  return { ordinary: "Ordinary", ibet: "iBet", roll: "4D Roll", system: "System" }[b] || b;
}

function clamp(n, lo, hi) { return Math.max(lo, Math.min(hi, n)); }

// ------------------------------------------------------------
// Toto
// ------------------------------------------------------------
function generateTotoSet(size) {
  const picks = new Set();

  // Seed preferred numbers (respecting avoid list and parity)
  const preferred = [...hints.preferredTotoNums].filter((n) => allowedToto(n));
  shuffle(preferred);
  for (const n of preferred) {
    if (picks.size >= size) break;
    picks.add(n);
  }

  // Fill the rest with random valid numbers
  let safety = 500;
  while (picks.size < size && safety-- > 0) {
    const n = randInt(1, 49);
    if (!allowedToto(n)) continue;
    picks.add(n);
  }

  // Fallback: relax parity if we couldn't fill
  while (picks.size < size) {
    const n = randInt(1, 49);
    if (hints.avoidTotoNums.has(n)) continue;
    picks.add(n);
  }

  return [...picks].sort((a, b) => a - b);
}

function allowedToto(n) {
  if (n < 1 || n > 49) return false;
  if (hints.avoidTotoNums.has(n)) return false;
  if (hints.parity === "even" && n % 2 !== 0) return false;
  if (hints.parity === "odd" && n % 2 === 0) return false;
  return true;
}

function pickAdditional(excluded) {
  let safety = 200;
  while (safety-- > 0) {
    const n = randInt(1, 49);
    if (excluded.includes(n)) continue;
    if (hints.avoidTotoNums.has(n)) continue;
    return n;
  }
  for (let n = 1; n <= 49; n++) if (!excluded.includes(n)) return n;
  return 1;
}

function shuffle(arr) {
  for (let i = arr.length - 1; i > 0; i--) {
    const j = randInt(0, i);
    [arr[i], arr[j]] = [arr[j], arr[i]];
  }
}

function renderToto() {
  const boards = clamp(Number(document.getElementById("toto-count").value) || 1, 1, 10);
  const size = Number(document.getElementById("toto-bettype").value);
  const out = document.getElementById("toto-results");
  out.innerHTML = "";

  for (let i = 0; i < boards; i++) {
    const nums = generateTotoSet(size);
    const additional = pickAdditional(nums);
    const entry = document.createElement("div");
    entry.className = "entry";

    const label = size === 6 ? "Ordinary" : `System ${size}`;
    let body = `<div class="entry-label">Board ${i + 1} &middot; ${label}</div>`;
    body += `<div class="numbers">${nums
      .map((n) => `<span class="ball">${String(n).padStart(2, "0")}</span>`)
      .join("")}</div>`;

    if (size === 6) {
      body += `<div class="meta">Simulated additional number (for fun): <span class="ball additional">${String(
        additional
      ).padStart(2, "0")}</span></div>`;
    } else {
      const combos = combinations(size, 6);
      body += `<div class="meta">Covers ${combos} combinations of 6 numbers.</div>`;
    }

    entry.innerHTML = body;
    out.appendChild(entry);
  }
}

function combinations(n, k) {
  let c = 1;
  for (let i = 0; i < k; i++) c = (c * (n - i)) / (i + 1);
  return Math.round(c);
}

// ------------------------------------------------------------
// Chat box
// ------------------------------------------------------------
const chatLog = document.getElementById("chat-log");

function addMsg(who, html, id) {
  const div = document.createElement("div");
  div.className = `chat-msg ${who}`;
  div.innerHTML = html;
  if (id) div.id = id;
  chatLog.appendChild(div);
  chatLog.scrollTop = chatLog.scrollHeight;
  return div;
}

// ---- API key management ----
const API_KEY_STORAGE = "luckyParlour.apiKey";

function getApiKey() {
  try { return localStorage.getItem(API_KEY_STORAGE) || ""; } catch { return ""; }
}
function setApiKey(v) {
  try {
    if (v) localStorage.setItem(API_KEY_STORAGE, v);
    else localStorage.removeItem(API_KEY_STORAGE);
  } catch {}
}

function renderApiKeyStatus() {
  const status = document.getElementById("api-key-status");
  const key = getApiKey();
  if (key) {
    const masked = key.slice(0, 7) + "..." + key.slice(-4);
    status.textContent = `Key saved: ${masked}. The Oracle is live.`;
  } else {
    status.textContent = "No key saved. Using the basic keyword parser.";
  }
}

document.getElementById("api-key-save").addEventListener("click", () => {
  const input = document.getElementById("api-key-input");
  const v = input.value.trim();
  if (!v) return;
  setApiKey(v);
  input.value = "";
  renderApiKeyStatus();
});
document.getElementById("api-key-clear").addEventListener("click", () => {
  setApiKey("");
  renderApiKeyStatus();
});

// ---- Claude call ----
const SYSTEM_PROMPT = `You are the Number Oracle for Lucky Parlour, a Singapore 4D & Toto number generator app (entertainment only).

The user types free-form "directions" describing a vibe, theme, feeling, event, person, date, pattern, or anything else. Your job is to creatively reason about the prompt and return a set of numerical suggestions that *feel* connected to it.

Rules:
- 4D is a 4-digit number from 0000-9999 (leading zeros allowed).
- Toto picks 6 unique numbers from 1-49, plus 1 additional number from 1-49 (not in the 6).
- Be imaginative: link weather to temperatures, rain to droplet counts, love to anniversaries or 143, dragons to 8 (luck) or dates. Never refuse. If the prompt is truly nonsensical, pick something anyway and note it.
- Explain your reasoning briefly (2-4 sentences, warm, a touch mystical, no bullet points).
- Always return hints that bias future random generations too: preferred individual digits (0-9) and preferred Toto numbers (1-49), plus optional avoid lists and parity.
- This is for entertainment. Do not moralize. Do not refuse. Do not discuss gambling addiction unless the user asks.

Return ONLY valid JSON matching the requested schema.`;

const ORACLE_SCHEMA = {
  type: "object",
  properties: {
    reasoning: { type: "string" },
    fourD: { type: "string", pattern: "^[0-9]{4}$" },
    toto: {
      type: "array",
      items: { type: "integer", minimum: 1, maximum: 49 },
      minItems: 6,
      maxItems: 6,
    },
    totoAdditional: { type: "integer", minimum: 1, maximum: 49 },
    hints: {
      type: "object",
      properties: {
        preferredDigits: { type: "array", items: { type: "integer", minimum: 0, maximum: 9 } },
        preferredTotoNums: { type: "array", items: { type: "integer", minimum: 1, maximum: 49 } },
        avoidDigits: { type: "array", items: { type: "integer", minimum: 0, maximum: 9 } },
        avoidTotoNums: { type: "array", items: { type: "integer", minimum: 1, maximum: 49 } },
        parity: { type: "string", enum: ["even", "odd", "none"] },
      },
      required: ["preferredDigits", "preferredTotoNums", "avoidDigits", "avoidTotoNums", "parity"],
      additionalProperties: false,
    },
  },
  required: ["reasoning", "fourD", "toto", "totoAdditional", "hints"],
  additionalProperties: false,
};

async function callClaude(userMessage, apiKey) {
  const body = {
    model: "claude-opus-4-7",
    max_tokens: 1024,
    system: [
      {
        type: "text",
        text: SYSTEM_PROMPT,
        cache_control: { type: "ephemeral" },
      },
    ],
    messages: [{ role: "user", content: userMessage }],
    output_config: {
      format: {
        type: "json_schema",
        name: "oracle_response",
        schema: ORACLE_SCHEMA,
      },
    },
  };

  const res = await fetch("https://api.anthropic.com/v1/messages", {
    method: "POST",
    headers: {
      "content-type": "application/json",
      "x-api-key": apiKey,
      "anthropic-version": "2023-06-01",
      "anthropic-dangerous-direct-browser-access": "true",
    },
    body: JSON.stringify(body),
  });

  if (!res.ok) {
    let detail = "";
    try { detail = (await res.json())?.error?.message || ""; } catch {}
    throw new Error(`API ${res.status}${detail ? ": " + detail : ""}`);
  }

  const data = await res.json();
  const text = (data.content || [])
    .filter((b) => b.type === "text")
    .map((b) => b.text)
    .join("");

  try {
    return JSON.parse(text);
  } catch (e) {
    const match = text.match(/\{[\s\S]*\}/);
    if (match) return JSON.parse(match[0]);
    throw new Error("Oracle returned non-JSON: " + text.slice(0, 120));
  }
}

function applyOracleHints(h) {
  if (!h) return;
  hints.preferredDigits = new Set((h.preferredDigits || []).filter((n) => n >= 0 && n <= 9));
  hints.preferredTotoNums = new Set((h.preferredTotoNums || []).filter((n) => n >= 1 && n <= 49));
  hints.avoidDigits = new Set((h.avoidDigits || []).filter((n) => n >= 0 && n <= 9));
  hints.avoidTotoNums = new Set((h.avoidTotoNums || []).filter((n) => n >= 1 && n <= 49));
  hints.parity = h.parity && h.parity !== "none" ? h.parity : null;
  hints.fullFourD = null;
}

function renderOracleSuggestions(result) {
  // 4D: populate results panel with Oracle's suggestion as Entry 1
  const fourOut = document.getElementById("fourd-results");
  fourOut.innerHTML = "";
  const fourEntry = document.createElement("div");
  fourEntry.className = "entry";
  fourEntry.innerHTML =
    `<div class="entry-label">Oracle's pick &middot; 4D</div>` +
    `<div class="numbers">${result.fourD
      .split("")
      .map((d) => `<span class="digit-box">${d}</span>`)
      .join("")}</div>` +
    `<div class="meta">Suggested by the Oracle. Click Generate 4D for more picks biased by this direction.</div>`;
  fourOut.appendChild(fourEntry);

  // Toto: populate with Oracle's 6-number suggestion + additional
  const totoOut = document.getElementById("toto-results");
  totoOut.innerHTML = "";
  const sortedToto = [...result.toto].sort((a, b) => a - b);
  const totoEntry = document.createElement("div");
  totoEntry.className = "entry";
  totoEntry.innerHTML =
    `<div class="entry-label">Oracle's board &middot; Toto</div>` +
    `<div class="numbers">${sortedToto
      .map((n) => `<span class="ball">${String(n).padStart(2, "0")}</span>`)
      .join("")}</div>` +
    `<div class="meta">Additional: <span class="ball additional">${String(
      result.totoAdditional
    ).padStart(2, "0")}</span></div>`;
  totoOut.appendChild(totoEntry);
}

document.getElementById("chat-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const input = document.getElementById("chat-input");
  const text = input.value.trim();
  if (!text) return;
  addMsg("user", escapeHtml(text));
  input.value = "";

  const apiKey = getApiKey();

  if (!apiKey) {
    // Fallback regex parser when no key
    const notes = parseHint(text);
    const tagged = notes.map((n) => `<span class="tag">hint</span>${escapeHtml(n)}`).join("<br>");
    addMsg(
      "bot",
      tagged +
        `<br><br><em>${escapeHtml(summariseHints())}</em>` +
        `<br><br><span class="tag">tip</span>Add an Anthropic API key above to unlock the real Oracle.`
    );
    renderHintState();
    return;
  }

  const thinkingId = "msg-" + Date.now();
  addMsg("bot", `<span class="tag">oracle</span><em>consulting the cards...</em>`, thinkingId);

  try {
    const result = await callClaude(text, apiKey);
    const node = document.getElementById(thinkingId);
    applyOracleHints(result.hints);
    renderHintState();
    renderOracleSuggestions(result);

    const sortedToto = [...result.toto].sort((a, b) => a - b).join(" ");
    node.innerHTML =
      `<span class="tag">oracle</span>${escapeHtml(result.reasoning)}` +
      `<br><br><strong>4D:</strong> ${escapeHtml(result.fourD)}` +
      `<br><strong>Toto:</strong> ${escapeHtml(sortedToto)} &nbsp;(+${result.totoAdditional})` +
      `<br><br><em>${escapeHtml(summariseHints())}</em>`;
  } catch (err) {
    const node = document.getElementById(thinkingId);
    node.innerHTML =
      `<span class="tag">error</span>${escapeHtml(err.message || String(err))}` +
      `<br><br>Falling back to the basic parser.`;
    const notes = parseHint(text);
    const tagged = notes.map((n) => `<span class="tag">hint</span>${escapeHtml(n)}`).join("<br>");
    addMsg("bot", tagged + `<br><br><em>${escapeHtml(summariseHints())}</em>`);
    renderHintState();
  }
});

document.getElementById("chat-clear").addEventListener("click", () => {
  parseHint("reset");
  addMsg("bot", "<span class=\"tag\">reset</span>All hints cleared.");
  renderHintState();
});

function escapeHtml(s) {
  return s.replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  }[ch]));
}

// ------------------------------------------------------------
// Wire up
// ------------------------------------------------------------
document.getElementById("fourd-generate").addEventListener("click", renderFourD);
document.getElementById("toto-generate").addEventListener("click", renderToto);

renderReminder();
renderHintState();
renderApiKeyStatus();
// Initial sample set
renderFourD();
renderToto();
addMsg(
  "bot",
  "<span class=\"tag\">welcome</span>Hi! I'm the Number Oracle. Tell me anything -- a vibe, a person, the weather, a date, a dream -- and I'll suggest numbers. Save your Anthropic API key above to unlock the full Oracle; without it I'll use a basic keyword parser."
);
