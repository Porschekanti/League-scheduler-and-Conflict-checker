/**
 * Intramural Scheduler — frontend.
 *
 * TypeScript, zero dependencies. Compiled to ../app.js by ../build.mjs using
 * Node's own `module.stripTypeScriptTypes`; nothing is installed.
 *
 * Because types are *stripped* rather than transformed, this file avoids the
 * constructs that need real codegen: no `enum`, no `namespace`, no constructor
 * parameter properties. Union types and `as const` cover the same ground and
 * survive stripping untouched.
 */

const API = (() => {
  const override = new URLSearchParams(location.search).get("api");
  if (override) return override.replace(/\/$/, "");
  if (location.port === "8000") return location.origin;
  return "http://127.0.0.1:8000";
})();

/* ------------------------------------------------------------------- types */

type Role = "HEAD" | "REP" | "VIEWER";

interface Session { token: string; role: Role; sportScope: string | null; email: string; }
interface Team { id: number; name: string; sport: string; season_id: number; }
interface Venue { id: number; name: string; location: string | null; }
interface Season { id: number; sport: string; start_date: string; end_date: string; }
interface Term { id: number; label: string; start_date: string; end_date: string; is_current: number; }

interface Match {
  id: number; home_team_id: number; away_team_id: number; venue_id: number;
  start_time: string; end_time: string; status: string; sport: string;
  season_id: number; version: number;
}

interface RosterEntry { id: number; name: string; roll_number: string; sport: string; }

interface Assignment {
  id: number; role: string; sport_scope: string | null; status: string;
  nominated_user_id: number; term_id: number;
}

interface Conflict {
  type: "venue" | "player" | "blackout" | "venue_external"
      | "player_external" | "blackout_external" | "invalid";
  player_name?: string; conflicting_match_id?: number;
  rest_minutes?: number; required_minutes?: number;
  description?: string; external_ref?: string; source?: string; reason?: string;
}

interface Draft { id: number; version: number; label: string; when: string; term: string; }
interface ApiResult<T> { ok: boolean; status: number; body: T; }

/* --------------------------------------------------------------- app state */

const state: {
  session: Session | null;
  teams: Team[]; venues: Venue[]; seasons: Season[];
  drafts: Draft[]; matches: Match[];
  month: Date; selected: Match | null;
  loadedOnce: boolean;
} = {
  session: null,
  teams: [], venues: [], seasons: [],
  drafts: [], matches: [],
  month: new Date(new Date().getFullYear(), new Date().getMonth(), 1),
  selected: null,
  loadedOnce: false,
};

/* ------------------------------------------------------------------ helpers */

function $<T extends HTMLElement>(id: string): T {
  const el = document.getElementById(id);
  if (!el) throw new Error(`missing element #${id}`);
  return el as T;
}

function maybe<T extends HTMLElement>(id: string): T | null {
  return document.getElementById(id) as T | null;
}

function el<K extends keyof HTMLElementTagNameMap>(
  tag: K, className?: string, text?: string
): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function clear(node: HTMLElement): void {
  while (node.firstChild) node.removeChild(node.firstChild);
}

function val(id: string): string {
  return ($(id) as HTMLInputElement | HTMLSelectElement).value;
}

async function api<T = any>(path: string, options: RequestInit = {}): Promise<ApiResult<T>> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (state.session) headers["Authorization"] = `Bearer ${state.session.token}`;
  try {
    const res = await fetch(API + path, {
      ...options, headers: { ...headers, ...(options.headers as Record<string, string>) },
    });
    let body: any = null;
    try { body = await res.json(); } catch { body = null; }
    return { ok: res.ok, status: res.status, body: body as T };
  } catch {
    return { ok: false, status: 0,
      body: { detail: "Cannot reach the API. Is the backend running?" } as any };
  }
}

function errorText(body: any): string {
  if (!body) return "Something went wrong.";
  if (typeof body.detail === "string") return body.detail;
  if (Array.isArray(body.detail)) return body.detail.map((d: any) => d.msg || String(d)).join("; ");
  if (body.detail) return JSON.stringify(body.detail);
  return JSON.stringify(body);
}

/** Stored times are canonical UTC; show them in the viewer's own zone. */
function fmtTime(iso: string): string {
  const d = new Date(iso);
  return isNaN(d.getTime()) ? iso : d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function fmtDay(iso: string): string {
  const d = new Date(iso);
  return isNaN(d.getTime()) ? iso
    : d.toLocaleDateString([], { weekday: "short", day: "numeric", month: "short" });
}

function durationMins(a: string, b: string): number {
  return Math.round((new Date(b).getTime() - new Date(a).getTime()) / 60000);
}

function teamName(id: number): string {
  const t = state.teams.find(x => x.id === id);
  return t ? t.name : `Team ${id}`;
}

function venueName(id: number): string {
  const v = state.venues.find(x => x.id === id);
  return v ? v.name : `Venue ${id}`;
}

function pad(n: number): string { return String(n).padStart(2, "0"); }

function localIso(d: Date): string {
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}` +
         `T${pad(d.getHours())}:${pad(d.getMinutes())}:00`;
}

/* -------------------------------------------------------------------- toast */

function toast(title: string, body?: string, kind: "ok" | "bad" | "info" = "info"): void {
  const wrap = $("toasts");
  const node = el("div", `toast ${kind}`);
  node.appendChild(el("div", "toast-bar"));
  const col = el("div");
  col.appendChild(el("div", "toast-title", title));
  if (body) col.appendChild(el("div", "toast-body", body));
  node.appendChild(col);
  wrap.appendChild(node);
  setTimeout(() => {
    node.classList.add("is-leaving");
    setTimeout(() => node.remove(), 240);
  }, 4200);
}

/** Press feedback that outlives the click: a short spring pop on success. */
function pop(button: HTMLButtonElement): void {
  button.classList.remove("did-succeed");
  void button.offsetWidth;                       // restart the animation
  button.classList.add("did-succeed");
  setTimeout(() => button.classList.remove("did-succeed"), 460);
}

async function withBusy<T>(button: HTMLButtonElement, work: () => Promise<T>): Promise<T> {
  button.classList.add("is-busy");
  button.disabled = true;
  try { return await work(); }
  finally { button.classList.remove("is-busy"); button.disabled = false; }
}

function showResult(host: HTMLElement, text: string, kind: "ok" | "bad" | "info"): void {
  clear(host);
  host.appendChild(el("div", `result ${kind}`, text));
}

function emptyState(mark: string, title: string, body: string): HTMLElement {
  const wrap = el("div", "empty");
  wrap.appendChild(el("div", "empty-mark", mark));
  wrap.appendChild(el("div", "empty-title", title));
  wrap.appendChild(el("div", "empty-body", body));
  return wrap;
}

/* ---------------------------------------------------------------- conflicts
   Plain language, because the point is to tell the organizer what to DO next. */

function describeConflict(c: Conflict): { kind: string; why: HTMLElement } {
  const why = el("div", "conflict-why");
  const strong = (t: string) => { const b = el("b", undefined, t); return b; };

  switch (c.type) {
    case "venue":
      why.append("The venue is already taken by match ", strong(`#${c.conflicting_match_id}`),
        " then. Move the room or the time.");
      return { kind: "Venue double-booked", why };

    case "venue_external":
      why.append("The master sheet already holds this venue — ",
        strong(c.description || c.external_ref || "a sheet booking"), ". Move the room or the time.");
      return { kind: "Venue held in the master sheet", why };

    case "player":
      why.append(strong(c.player_name || "A player"), " is already playing in match ",
        strong(`#${c.conflicting_match_id}`), ". They cannot be in two places.");
      return { kind: "Player double-booked", why };

    case "player_external":
      why.append(strong(c.player_name || "A player"), " is committed in the master sheet — ",
        strong(c.description || c.external_ref || "a sheet booking"), ".");
      return { kind: "Player committed in the master sheet", why };

    case "blackout":
    case "blackout_external": {
      const rest = c.rest_minutes ?? 0;
      const need = c.required_minutes ?? 90;
      why.append(strong(c.player_name || "A player"), " would get only ", strong(`${rest} min`),
        " of rest, and needs ", strong(`${need} min`), ". Push this match ",
        strong(`${need - rest} min`), " later, or pick another player.");
      const meter = el("div", "rest-meter");
      const fill = el("span");
      fill.style.width = "0%";
      meter.appendChild(fill);
      why.appendChild(meter);
      requestAnimationFrame(() => {
        fill.style.width = `${Math.min(100, Math.round((rest / need) * 100))}%`;
      });
      return { kind: "Not enough rest", why };
    }

    case "invalid":
      why.textContent = c.reason || "This match could not be checked.";
      return { kind: "Cannot be scheduled", why };

    default:
      why.textContent = JSON.stringify(c);
      return { kind: "Conflict", why };
  }
}

function renderConflicts(host: HTMLElement, conflicts: Conflict[]): void {
  const box = el("div", "conflicts");
  box.appendChild(el("div", "conflicts-head",
    conflicts.length === 1 ? "1 reason this cannot be booked"
                           : `${conflicts.length} reasons this cannot be booked`));
  for (const c of conflicts) {
    const row = el("div", `conflict c-${c.type}`);
    row.appendChild(el("div", "conflict-bar"));
    const body = el("div");
    const { kind, why } = describeConflict(c);
    body.appendChild(el("div", "conflict-kind", kind));
    body.appendChild(why);
    row.appendChild(body);
    box.appendChild(row);
  }
  clear(host);
  host.appendChild(box);
}

/* ------------------------------------------------------- public schedule ---
   Kept as a month calendar: people think about a schedule in weeks, and the
   grid shows load and gaps at a glance in a way a flat list cannot.          */

async function loadSchedule(): Promise<void> {
  const res = await api<Match[]>("/schedules?season=active");
  if (res.ok && Array.isArray(res.body)) {
    state.matches = res.body;
    state.loadedOnce = true;
    if (state.selected) {
      state.selected = state.matches.find(m => m.id === state.selected!.id) ?? null;
    }
  } else if (!state.loadedOnce) {
    clear($("calendar"));
    $("calendar").appendChild(emptyState("!", "Cannot reach the scheduler",
      "Start the backend and this fills in on its own."));
    return;
  }
  renderCalendar();
  renderDetail();
}

function renderCalendar(): void {
  const y = state.month.getFullYear();
  const mo = state.month.getMonth();

  const byDay = new Map<number, Match[]>();
  for (const m of state.matches) {
    const d = new Date(m.start_time);
    if (isNaN(d.getTime()) || d.getFullYear() !== y || d.getMonth() !== mo) continue;
    const list = byDay.get(d.getDate());
    if (list) list.push(m); else byDay.set(d.getDate(), [m]);
  }

  $("cal-title").textContent =
    state.month.toLocaleDateString([], { month: "long", year: "numeric" });
  $("match-count").textContent =
    state.matches.length === 1 ? "1 confirmed" : `${state.matches.length} confirmed`;

  const grid = $("calendar");
  clear(grid);

  const firstDow = new Date(y, mo, 1).getDay();
  const lastDate = new Date(y, mo + 1, 0).getDate();
  const today = new Date();

  for (let i = 0; i < firstDow; i++) grid.appendChild(el("div", "day is-empty"));

  for (let n = 1; n <= lastDate; n++) {
    const dayMatches = byDay.get(n) ?? [];
    const cell = el("div", "day");
    if (dayMatches.length) cell.classList.add("has-matches");
    if (today.getFullYear() === y && today.getMonth() === mo && today.getDate() === n) {
      cell.classList.add("is-today");
    }

    const num = el("div", "day-num", String(n));
    cell.appendChild(num);

    for (const m of dayMatches.slice(0, 3)) {
      const chip = el("button", "event") as HTMLButtonElement;
      chip.type = "button";
      chip.appendChild(el("span", "event-time", fmtTime(m.start_time)));
      chip.appendChild(el("span", "event-name", `${teamName(m.home_team_id)} v ${teamName(m.away_team_id)}`));
      if (state.selected && state.selected.id === m.id) chip.classList.add("is-selected");
      chip.addEventListener("click", (e) => {
        e.stopPropagation();
        state.selected = m;
        renderCalendar();
        renderDetail();
      });
      cell.appendChild(chip);
    }
    if (dayMatches.length > 3) {
      cell.appendChild(el("div", "more", `+${dayMatches.length - 3} more`));
    }
    grid.appendChild(cell);
  }

  const used = firstDow + lastDate;
  for (let i = used; i % 7 !== 0; i++) grid.appendChild(el("div", "day is-empty"));
}

function renderDetail(): void {
  const host = $("match-detail");
  clear(host);
  const m = state.selected;
  if (!m) {
    host.appendChild(emptyState("—", "No match selected",
      "Pick a match above to see its full details."));
    return;
  }

  const card = el("div", "detail-card");
  const head = el("div", "between");
  const title = el("div");
  title.appendChild(el("div", "detail-teams",
    `${teamName(m.home_team_id)} vs ${teamName(m.away_team_id)}`));
  title.appendChild(el("div", "detail-when",
    `${fmtDay(m.start_time)} · ${fmtTime(m.start_time)}–${fmtTime(m.end_time)} · ${durationMins(m.start_time, m.end_time)} min`));
  head.appendChild(title);
  head.appendChild(el("span", "pill pill-live", m.status));
  card.appendChild(head);

  const dl = el("dl");
  const kv = (k: string, v: string) => {
    const row = el("div", "kv");
    row.appendChild(el("dt", undefined, k));
    row.appendChild(el("dd", undefined, v));
    dl.appendChild(row);
  };
  kv("Sport", m.sport);
  kv("Venue", venueName(m.venue_id));
  kv("Match id", `#${m.id}`);
  kv("Version", `v${m.version}`);
  card.appendChild(dl);
  host.appendChild(card);
}

/* ----------------------------------------------------------- reference data */

async function loadReference(): Promise<void> {
  const [teams, venues, seasons] = await Promise.all([
    api<Team[]>("/teams"), api<Venue[]>("/venues"), api<Season[]>("/seasons"),
  ]);
  if (teams.ok && Array.isArray(teams.body)) state.teams = teams.body;
  if (venues.ok && Array.isArray(venues.body)) state.venues = venues.body;
  if (seasons.ok && Array.isArray(seasons.body)) state.seasons = seasons.body;
  fillSelects();
  renderCalendar();
}

function sportsAvailable(): string[] {
  const all = Array.from(new Set([
    ...state.teams.map(t => t.sport),
    ...state.seasons.map(s => s.sport),
  ])).sort();
  const scope = state.session?.role === "REP" ? state.session.sportScope : null;
  return scope ? all.filter(s => s === scope) : all;
}

/** Hick's Law: pick from the real, short list rather than typing an id. */
function fillSelects(): void {
  const sports = sportsAvailable();

  for (const id of ["b-sport", "t-sport", "nom-sport"]) {
    const sel = maybe<HTMLSelectElement>(id);
    if (!sel) continue;
    const keep = sel.value;
    clear(sel);
    for (const s of sports) sel.appendChild(new Option(s, s));
    if (sports.includes(keep)) sel.value = keep;
  }

  const venueSel = maybe<HTMLSelectElement>("b-venue");
  if (venueSel) {
    const keep = venueSel.value;
    clear(venueSel);
    for (const v of state.venues) venueSel.appendChild(new Option(v.name, String(v.id)));
    if (state.venues.some(v => String(v.id) === keep)) venueSel.value = keep;
  }

  syncTeamOptions();

  const scope = state.session?.role === "REP" ? state.session.sportScope : null;
  const rosterTeam = maybe<HTMLSelectElement>("p-team");
  if (rosterTeam) {
    const keep = rosterTeam.value;
    clear(rosterTeam);
    for (const t of state.teams) {
      if (!scope || t.sport === scope) {
        rosterTeam.appendChild(new Option(`${t.name} — ${t.sport}`, String(t.id)));
      }
    }
    if (rosterTeam.querySelector(`option[value="${keep}"]`)) rosterTeam.value = keep;
    loadRoster();
  }
}

/** Only teams of the chosen sport can meet, so never offer the others. */
function syncTeamOptions(): void {
  const sportSel = maybe<HTMLSelectElement>("b-sport");
  if (!sportSel) return;
  const eligible = state.teams.filter(t => t.sport === sportSel.value);

  for (const id of ["b-home", "b-away"]) {
    const sel = maybe<HTMLSelectElement>(id);
    if (!sel) continue;
    const keep = sel.value;
    clear(sel);
    for (const t of eligible) sel.appendChild(new Option(t.name, String(t.id)));
    if (eligible.some(t => String(t.id) === keep)) sel.value = keep;
  }

  const home = maybe<HTMLSelectElement>("b-home");
  const away = maybe<HTMLSelectElement>("b-away");
  if (home && away && home.value === away.value && away.options.length > 1) {
    away.selectedIndex = 1;
  }
  document.querySelectorAll<HTMLSelectElement>(".fx-home, .fx-away").forEach(sel => {
    const keep = sel.value;
    clear(sel);
    for (const t of eligible) sel.appendChild(new Option(t.name, String(t.id)));
    if (eligible.some(t => String(t.id) === keep)) sel.value = keep;
  });
}

/* -------------------------------------------------------------------- auth */

async function signIn(button: HTMLButtonElement): Promise<void> {
  const email = val("login-email").trim();
  const password = val("login-password");
  const out = $("login-result");

  if (!email || !password) { showResult(out, "Enter your email and password.", "bad"); return; }

  const res = await withBusy(button, () =>
    api<{ access_token: string; role: Role; sport_scope: string | null }>(
      "/auth/login", { method: "POST", body: JSON.stringify({ email, password }) }));

  if (!res.ok) { showResult(out, errorText(res.body), "bad"); return; }

  state.session = {
    token: res.body.access_token, role: res.body.role,
    sportScope: res.body.sport_scope, email,
  };
  pop(button);
  clear(out);
  ($("login-password") as HTMLInputElement).value = "";
  renderSession();
  fillSelects();
  loadPending();
  toast("Signed in",
    `${res.body.role}${res.body.sport_scope ? " · " + res.body.sport_scope : " · all sports"}`, "ok");
}

function signOut(): void {
  state.session = null;
  state.drafts = [];
  renderDrafts();
  renderSession();
  toast("Signed out", undefined, "info");
}

function renderSession(): void {
  const s = state.session;
  $("login-card").hidden = !!s;
  $("organizer").hidden = !s;
  $("session-chip").hidden = !s;
  $("admin-tab").hidden = !(s && s.role === "HEAD");
  $("pending-card").hidden = true;

  if (s) {
    $("session-who").textContent = s.role;
    $("session-scope").textContent = s.sportScope ? s.sportScope : "all sports";
  }
  if (!s && currentTab() === "admin") selectTab("book");
}

/* ------------------------------------------------------------------ booking */

async function book(button: HTMLButtonElement): Promise<void> {
  const out = $("book-result");
  const home = Number(val("b-home"));
  const away = Number(val("b-away"));

  if (!home || !away) { showResult(out, "Pick both teams first.", "bad"); return; }
  if (home === away) { showResult(out, "A team cannot play itself.", "bad"); return; }

  const payload = {
    home_team_id: home, away_team_id: away,
    venue_id: Number(val("b-venue")), sport: val("b-sport"),
    start_time: val("b-start"), end_time: val("b-end"),
  };

  const res = await withBusy(button, () =>
    api<any>("/schedules", { method: "POST", body: JSON.stringify(payload) }));

  if (res.status === 409 && res.body?.detail?.conflicts) {
    renderConflicts(out, res.body.detail.conflicts as Conflict[]);
    toast("Not booked", "The slot clashes — see the reasons.", "bad");
    return;
  }
  if (!res.ok) { showResult(out, errorText(res.body), "bad"); return; }

  pop(button);
  clear(out);
  state.drafts.unshift({
    id: res.body.match_id, version: res.body.version,
    label: `${teamName(home)} vs ${teamName(away)}`,
    when: `${fmtDay(payload.start_time)} · ${fmtTime(payload.start_time)}`,
    term: res.body.term ?? "",
  });
  renderDrafts();
  if (res.body.term) updateTermBadge(res.body.term, !!res.body.in_break);
  toast("Draft created", `Filed under ${res.body.term}. Publish it from Drafts.`, "ok");
  selectTab("drafts");
}

function updateTermBadge(term: string, inBreak: boolean): void {
  const badge = $("term-badge");
  badge.hidden = false;
  badge.classList.toggle("is-break", inBreak);
  $("term-label").textContent = term;
  $("term-note").textContent = inBreak ? "between trimesters" : "in session";
}

/* ------------------------------------------------------------------- drafts */

function renderDrafts(): void {
  const host = $("drafts");
  $("draft-count").textContent = String(state.drafts.length);
  clear(host);

  if (state.drafts.length === 0) {
    host.appendChild(emptyState("—", "No drafts yet",
      "Book a match or run a generation, and it lands here awaiting publish."));
    return;
  }

  for (const d of state.drafts) {
    const card = el("div", "draft");
    const top = el("div", "draft-top");

    const left = el("div");
    left.appendChild(el("div", "draft-teams", d.label));
    left.appendChild(el("div", "draft-when", d.when));
    top.appendChild(left);

    const right = el("div", "stack");
    right.appendChild(el("span", "draft-id", `#${d.id} · v${d.version}`));
    if (d.term) right.appendChild(el("span", "pill pill-term", d.term));
    top.appendChild(right);
    card.appendChild(top);

    const actions = el("div", "draft-actions");
    const publish = el("button", "btn btn-primary btn-sm", "Publish") as HTMLButtonElement;
    publish.type = "button";
    const cancel = el("button", "btn btn-ghost btn-sm danger", "Cancel") as HTMLButtonElement;
    cancel.type = "button";
    actions.appendChild(publish);
    actions.appendChild(cancel);
    card.appendChild(actions);

    const slot = el("div");
    card.appendChild(slot);

    publish.addEventListener("click", () => publishDraft(d, publish, card, slot));
    cancel.addEventListener("click", () => cancelDraft(d, cancel, card));

    host.appendChild(card);
  }
}

function removeDraft(d: Draft, card: HTMLElement): void {
  card.classList.add("is-leaving");
  setTimeout(() => {
    state.drafts = state.drafts.filter(x => x.id !== d.id);
    renderDrafts();
  }, 260);
}

async function publishDraft(
  d: Draft, button: HTMLButtonElement, card: HTMLElement, slot: HTMLElement
): Promise<void> {
  const res = await withBusy(button, () =>
    api<any>(`/schedules/${d.id}/publish`, {
      method: "POST", body: JSON.stringify({ expected_version: d.version }),
    }));

  if (res.status === 409 && res.body?.detail?.conflicts) {
    renderConflicts(slot, res.body.detail.conflicts as Conflict[]);
    toast("Not published", "Something changed since the draft was made.", "bad");
    return;
  }
  if (!res.ok) { showResult(slot, errorText(res.body), "bad"); return; }

  pop(button);
  toast("Published", `Match #${d.id} is now public.`, "ok");
  removeDraft(d, card);
  loadSchedule();
}

async function cancelDraft(d: Draft, button: HTMLButtonElement, card: HTMLElement): Promise<void> {
  const res = await withBusy(button, () =>
    api<any>(`/schedules/${d.id}/cancel`, {
      method: "POST", body: JSON.stringify({ expected_version: d.version }),
    }));
  if (!res.ok) { toast("Not cancelled", errorText(res.body), "bad"); return; }
  toast("Cancelled", `Match #${d.id} released its venue and players.`, "ok");
  removeDraft(d, card);
  loadSchedule();
}

/* ------------------------------------------------------------ roster & teams */

async function createTeam(button: HTMLButtonElement): Promise<void> {
  const out = $("team-result");
  const name = val("t-name").trim();
  if (!name) { showResult(out, "Give the team a name.", "bad"); return; }

  const res = await withBusy(button, () =>
    api<any>("/teams", { method: "POST", body: JSON.stringify({ name, sport: val("t-sport") }) }));

  if (!res.ok) { showResult(out, errorText(res.body), "bad"); return; }
  pop(button);
  ($("t-name") as HTMLInputElement).value = "";
  showResult(out, `Created ${name} (team #${res.body.team_id}).`, "ok");
  toast("Team created", `${name} joined the current trimester.`, "ok");
  await loadReference();
}

async function addPlayer(button: HTMLButtonElement): Promise<void> {
  const out = $("roster-result");
  const name = val("p-name").trim();
  const roll = val("p-roll").trim();
  const teamId = Number(val("p-team"));

  if (!name || !roll) { showResult(out, "Name and roll number are both needed.", "bad"); return; }
  if (!teamId) { showResult(out, "Pick a team first — create one if the list is empty.", "bad"); return; }

  const res = await withBusy(button, () =>
    api<any>("/players", {
      method: "POST", body: JSON.stringify({ name, roll_number: roll, team_id: teamId }),
    }));

  if (!res.ok) { showResult(out, errorText(res.body), "bad"); return; }
  pop(button);
  showResult(out, `Added ${name} to ${teamName(teamId)}.`, "ok");
  ($("p-name") as HTMLInputElement).value = "";
  ($("p-roll") as HTMLInputElement).value = "";
  toast("Roster updated", `${name} joined ${teamName(teamId)}.`, "ok");
  loadRoster();
}

async function loadRoster(): Promise<void> {
  const host = maybe("roster-list");
  const sel = maybe<HTMLSelectElement>("p-team");
  if (!host || !sel || !sel.value) { if (host) clear(host); return; }

  const teamId = Number(sel.value);
  const res = await api<RosterEntry[]>(`/teams/${teamId}/roster`);
  clear(host);
  if (!res.ok || !Array.isArray(res.body)) return;

  if (res.body.length === 0) {
    host.appendChild(el("div", "hint", `Nobody on ${teamName(teamId)} yet.`));
    return;
  }

  for (const p of res.body) {
    const row = el("div", "roster-row");
    const who = el("div");
    who.appendChild(el("div", "roster-name", p.name));
    who.appendChild(el("div", "roster-roll", p.roll_number));
    row.appendChild(who);

    const remove = el("button", "btn btn-ghost btn-sm danger", "Remove") as HTMLButtonElement;
    remove.type = "button";
    remove.addEventListener("click", () => removePlayer(p, teamId, remove, row));
    row.appendChild(remove);
    host.appendChild(row);
  }
}

async function removePlayer(
  p: RosterEntry, teamId: number, button: HTMLButtonElement, row: HTMLElement
): Promise<void> {
  const res = await withBusy(button, () =>
    api<any>(`/players/${p.id}/teams/${teamId}`, { method: "DELETE" }));
  if (!res.ok) { toast("Not removed", errorText(res.body), "bad"); return; }
  row.classList.add("is-leaving");
  setTimeout(loadRoster, 240);
  toast("Removed", `${p.name} left ${teamName(teamId)}.`, "ok");
}

/* --------------------------------------------------------------- generation
   A fixture builder rather than a JSON box: the same information, but without
   asking an organizer to hand-write syntax that a typo silently breaks.      */

function addFixtureRow(): void {
  const host = $("fixtures");
  const row = el("div", "fixture");

  const head = el("div", "between");
  head.appendChild(el("div", "group-label", `Fixture ${host.children.length + 1}`));
  const remove = el("button", "btn btn-ghost btn-sm", "Remove") as HTMLButtonElement;
  remove.type = "button";
  remove.addEventListener("click", () => { row.remove(); renumberFixtures(); });
  head.appendChild(remove);
  row.appendChild(head);

  const teams = el("div", "row2");
  const eligible = state.teams.filter(t => t.sport === val("b-sport"));
  const mk = (cls: string, index: number) => {
    const field = el("div", "field");
    field.appendChild(el("label", undefined, index === 0 ? "Home" : "Away"));
    const sel = el("select", cls) as HTMLSelectElement;
    for (const t of eligible) sel.appendChild(new Option(t.name, String(t.id)));
    if (eligible.length > 1) sel.selectedIndex = index;
    field.appendChild(sel);
    return field;
  };
  teams.appendChild(mk("fx-home", 0));
  teams.appendChild(mk("fx-away", 1));
  row.appendChild(teams);

  const venueField = el("div", "field");
  venueField.appendChild(el("label", undefined, "Venue"));
  const venueSel = el("select", "fx-venue") as HTMLSelectElement;
  for (const v of state.venues) venueSel.appendChild(new Option(v.name, String(v.id)));
  venueField.appendChild(venueSel);
  row.appendChild(venueField);

  const base = new Date();
  base.setDate(base.getDate() + 7 + host.children.length * 7);
  base.setHours(18, 0, 0, 0);
  const end = new Date(base.getTime() + 60 * 60000);

  const times = el("div", "row2");
  const mkTime = (cls: string, label: string, value: string) => {
    const field = el("div", "field");
    field.appendChild(el("label", undefined, label));
    const input = el("input", cls) as HTMLInputElement;
    input.type = "text";
    input.spellcheck = false;
    input.value = value;
    field.appendChild(input);
    return field;
  };
  times.appendChild(mkTime("fx-start", "Starts", localIso(base)));
  times.appendChild(mkTime("fx-end", "Ends", localIso(end)));
  row.appendChild(times);

  host.appendChild(row);
}

function renumberFixtures(): void {
  document.querySelectorAll<HTMLElement>("#fixtures .fixture .group-label")
    .forEach((lab, i) => { lab.textContent = `Fixture ${i + 1}`; });
}

function collectFixtures(): any[] {
  return Array.from(document.querySelectorAll<HTMLElement>("#fixtures .fixture")).map(row => ({
    home_team_id: Number((row.querySelector(".fx-home") as HTMLSelectElement).value),
    away_team_id: Number((row.querySelector(".fx-away") as HTMLSelectElement).value),
    venue_id: Number((row.querySelector(".fx-venue") as HTMLSelectElement).value),
    start_time: (row.querySelector(".fx-start") as HTMLInputElement).value,
    end_time: (row.querySelector(".fx-end") as HTMLInputElement).value,
  }));
}

async function generate(button: HTMLButtonElement): Promise<void> {
  const out = $("generate-result");
  const fixtures = collectFixtures();

  if (!fixtures.length) { showResult(out, "Add at least one fixture.", "bad"); return; }
  if (fixtures.some(f => f.home_team_id === f.away_team_id)) {
    showResult(out, "Every fixture needs two different teams.", "bad"); return;
  }

  const res = await withBusy(button, () =>
    api<any>("/schedule-generations", {
      method: "POST", body: JSON.stringify({ sport: val("b-sport"), fixtures }),
    }));

  if (!res.ok) { showResult(out, errorText(res.body), "bad"); return; }
  pop(button);
  pollJob(res.body.job_id, out);
}

async function pollJob(jobId: number, out: HTMLElement): Promise<void> {
  clear(out);
  const stage = el("div", "stage");
  stage.appendChild(el("span", "stage-dot"));
  const label = el("span", undefined, "Queued…");
  stage.appendChild(label);
  out.appendChild(stage);

  for (let i = 0; i < 40; i++) {
    const res = await api<any>(`/schedule-generations/${jobId}`);
    const status = res.body?.status as string | undefined;
    if (!status) { showResult(out, errorText(res.body), "bad"); return; }

    stage.setAttribute("data-state", status);
    label.textContent =
      status === "QUEUED" ? "Queued…" :
      status === "RUNNING" ? "Checking every fixture…" :
      status === "COMPLETED" ? "Done" : "Failed";

    if (status === "COMPLETED") {
      const created: number[] = res.body.result?.created_draft_match_ids ?? [];
      const skipped: any[] = res.body.result?.skipped ?? [];
      label.textContent =
        `${created.length} draft${created.length === 1 ? "" : "s"} created, ${skipped.length} skipped`;

      for (const id of created) {
        state.drafts.unshift({
          id, version: 1, label: `Generated match #${id}`,
          when: "from generation", term: "",
        });
      }
      renderDrafts();

      if (skipped.length) {
        const all: Conflict[] = [];
        for (const s of skipped) for (const c of s.conflicts) all.push(c);
        const box = el("div");
        renderConflicts(box, all);
        out.appendChild(box);
      }
      toast("Generation finished", `${created.length} created, ${skipped.length} skipped.`, "ok");
      return;
    }
    if (status === "FAILED") {
      showResult(out, `Job failed: ${JSON.stringify(res.body.result)}`, "bad");
      toast("Generation failed", undefined, "bad");
      return;
    }
    await new Promise(r => setTimeout(r, 600));
  }
}

/* -------------------------------------------------------------- master sheet */

async function sheetStatus(button: HTMLButtonElement): Promise<void> {
  const out = $("sheet-result");
  const res = await withBusy(button, () => api<any>("/master-sheet/status"));
  if (!res.ok) { showResult(out, errorText(res.body), "bad"); return; }

  clear(out);
  const dl = el("dl");
  const kv = (k: string, v: string) => {
    const row = el("div", "kv");
    row.appendChild(el("dt", undefined, k));
    row.appendChild(el("dd", undefined, v));
    dl.appendChild(row);
  };
  kv("Backend", String(res.body.backend));
  kv("Mirrored rows",
    String((res.body.mirrored ?? []).reduce((n: number, m: any) => n + m.bookings, 0)));
  out.appendChild(dl);

  if (res.body.backend === "none") {
    out.appendChild(el("div", "result info",
      "No sheet configured. Set MASTER_SHEET_BACKEND on the server to csv or google."));
  }
}

async function sheetSync(button: HTMLButtonElement): Promise<void> {
  const out = $("sheet-result");
  const res = await withBusy(button, () => api<any>("/master-sheet/sync", { method: "POST" }));
  if (!res.ok) { showResult(out, errorText(res.body), "bad"); return; }
  pop(button);
  const r = res.body;
  showResult(out,
    `${r.synced} synced, ${r.removed} removed, ${r.rejected.length} rejected.`,
    r.rejected.length ? "bad" : "ok");
  toast("Master sheet synced", `${r.synced} bookings mirrored.`, "ok");
}

/* ------------------------------------------------------------ role handover */

async function loadPending(): Promise<void> {
  if (!state.session) return;
  const res = await api<Assignment[]>("/role-assignments/pending-for-me");
  const card = $("pending-card");
  const host = $("pending-list");
  clear(host);

  if (!res.ok || !Array.isArray(res.body) || res.body.length === 0) {
    card.hidden = true;
    return;
  }
  card.hidden = false;

  for (const a of res.body) {
    const row = el("div", "between");
    row.appendChild(el("div", undefined,
      `${a.role}${a.sport_scope ? " · " + a.sport_scope : ""}`));
    const accept = el("button", "btn btn-primary btn-sm", "Accept") as HTMLButtonElement;
    accept.type = "button";
    accept.addEventListener("click", () => acceptNomination(a, accept));
    row.appendChild(accept);
    host.appendChild(row);
  }
}

async function acceptNomination(a: Assignment, button: HTMLButtonElement): Promise<void> {
  const res = await withBusy(button, () =>
    api<any>(`/role-assignments/${a.id}/accept`, { method: "POST" }));
  if (!res.ok) { toast("Not accepted", errorText(res.body), "bad"); return; }
  pop(button);
  if (state.session) {
    state.session.role = res.body.role as Role;
    state.session.sportScope = res.body.sport_scope;
  }
  renderSession();
  fillSelects();
  loadPending();
  toast("Role accepted", `You are now ${res.body.role}. Sign in again to refresh your token.`, "ok");
}

async function loadSuccession(): Promise<void> {
  const termSel = maybe<HTMLSelectElement>("nom-term");
  const termList = maybe("term-list");
  const history = maybe("assignment-history");
  if (!termSel || !termList || !history) return;

  const terms = await api<Term[]>("/terms");
  const list = terms.ok && Array.isArray(terms.body) ? terms.body : [];

  const keep = termSel.value;
  clear(termSel);
  for (const t of list) termSel.appendChild(new Option(t.label, String(t.id)));
  if (list.some(t => String(t.id) === keep)) termSel.value = keep;

  clear(termList);
  for (const t of list) {
    const row = el("div", "between");
    const left = el("div");
    left.appendChild(el("div", "roster-name", t.label));
    left.appendChild(el("div", "roster-roll", `${t.start_date} → ${t.end_date}`));
    row.appendChild(left);
    if (t.is_current) {
      row.appendChild(el("span", "pill pill-live", "Current"));
    } else {
      const act = el("button", "btn btn-ghost btn-sm", "Make current") as HTMLButtonElement;
      act.type = "button";
      act.addEventListener("click", () => activateTerm(t.id, act));
      row.appendChild(act);
    }
    termList.appendChild(row);
  }

  const assigns = await api<Assignment[]>("/role-assignments");
  clear(history);
  const rows = assigns.ok && Array.isArray(assigns.body) ? assigns.body : [];
  if (!rows.length) {
    history.appendChild(el("div", "hint", "No nominations yet."));
    return;
  }
  for (const a of rows) {
    const row = el("div", "between");
    const left = el("div");
    left.appendChild(el("div", "roster-name",
      `${a.role}${a.sport_scope ? " · " + a.sport_scope : ""}`));
    left.appendChild(el("div", "roster-roll", `#${a.id} · user ${a.nominated_user_id} · ${a.status}`));
    row.appendChild(left);
    if (a.status === "ACCEPTED") {
      const rev = el("button", "btn btn-ghost btn-sm danger", "Revoke") as HTMLButtonElement;
      rev.type = "button";
      rev.addEventListener("click", () => revokeAssignment(a.id, rev));
      row.appendChild(rev);
    }
    history.appendChild(row);
  }
}

async function createTerm(button: HTMLButtonElement): Promise<void> {
  const out = $("succession-result");
  const payload = {
    label: val("term-label-input").trim(),
    start_date: val("term-start"),
    end_date: val("term-end"),
  };
  if (!payload.label || !payload.start_date || !payload.end_date) {
    showResult(out, "Label, start and end dates are all needed.", "bad");
    return;
  }
  const res = await withBusy(button, () =>
    api<any>("/terms", { method: "POST", body: JSON.stringify(payload) }));
  if (!res.ok) { showResult(out, errorText(res.body), "bad"); return; }
  pop(button);
  showResult(out, `Created term ${payload.label}.`, "ok");
  loadSuccession();
}

async function activateTerm(id: number, button: HTMLButtonElement): Promise<void> {
  const res = await withBusy(button, () => api<any>(`/terms/${id}/activate`, { method: "POST" }));
  if (!res.ok) { toast("Not activated", errorText(res.body), "bad"); return; }
  toast("Term activated", undefined, "ok");
  loadSuccession();
}

async function nominate(button: HTMLButtonElement): Promise<void> {
  const out = $("succession-result");
  const role = val("nom-role");
  const email = val("nom-email").trim();
  const termId = Number(val("nom-term"));

  if (!email || !termId) { showResult(out, "Pick a term and enter the nominee's email.", "bad"); return; }

  const lookup = await api<any>(`/users/lookup?email=${encodeURIComponent(email)}`);
  if (!lookup.ok) { showResult(out, errorText(lookup.body), "bad"); return; }

  const res = await withBusy(button, () =>
    api<any>("/role-assignments/nominate", {
      method: "POST",
      body: JSON.stringify({
        role,
        sport_scope: role === "REP" ? val("nom-sport") : null,
        term_id: termId,
        nominee_user_id: lookup.body.id,
      }),
    }));

  if (!res.ok) { showResult(out, errorText(res.body), "bad"); return; }
  pop(button);
  showResult(out, `${lookup.body.name} nominated as ${role}. They must accept it.`, "ok");
  ($("nom-email") as HTMLInputElement).value = "";
  loadSuccession();
}

async function revokeAssignment(id: number, button: HTMLButtonElement): Promise<void> {
  const res = await withBusy(button, () =>
    api<any>(`/role-assignments/${id}/revoke`, { method: "POST" }));
  if (!res.ok) { toast("Not revoked", errorText(res.body), "bad"); return; }
  toast("Revoked", res.body.fallback_user_id
    ? `Fell back to user #${res.body.fallback_user_id}.`
    : "No previous holder to fall back to.", "ok");
  loadSuccession();
}

/* --------------------------------------------------------------------- tabs */

function currentTab(): string {
  const active = document.querySelector<HTMLElement>(".tab[aria-selected='true']");
  return active?.dataset.tab ?? "book";
}

function selectTab(name: string): void {
  document.querySelectorAll<HTMLElement>(".tab").forEach(t => {
    t.setAttribute("aria-selected", String(t.dataset.tab === name));
  });
  document.querySelectorAll<HTMLElement>(".tabpanel").forEach(p => {
    p.classList.toggle("is-active", p.dataset.panel === name);
  });
  if (name === "admin") loadSuccession();
  if (name === "roster") loadRoster();
}

/* --------------------------------------------------------------------- wire */

function defaultTimes(): void {
  const start = new Date();
  start.setDate(start.getDate() + 1);
  start.setHours(18, 0, 0, 0);
  ($("b-start") as HTMLInputElement).value = localIso(start);
  ($("b-end") as HTMLInputElement).value = localIso(new Date(start.getTime() + 60 * 60000));
}

/** Keep the end an hour after the start until the user says otherwise. */
function bindTimeCoupling(): void {
  const start = $("b-start") as HTMLInputElement;
  const end = $("b-end") as HTMLInputElement;
  let touched = false;
  end.addEventListener("input", () => { touched = true; });
  start.addEventListener("change", () => {
    if (touched) return;
    const d = new Date(start.value);
    if (isNaN(d.getTime())) return;
    end.value = localIso(new Date(d.getTime() + 60 * 60000));
  });
}

function onClick(id: string, fn: (b: HTMLButtonElement) => void): void {
  const b = maybe<HTMLButtonElement>(id);
  if (b) b.addEventListener("click", () => fn(b));
}

function init(): void {
  document.querySelectorAll<HTMLElement>(".tab").forEach(tab => {
    tab.addEventListener("click", () => selectTab(tab.dataset.tab!));
  });

  onClick("login-submit", signIn);
  onClick("book-submit", book);
  onClick("roster-submit", addPlayer);
  onClick("team-submit", createTeam);
  onClick("generate-submit", generate);
  onClick("sheet-status", sheetStatus);
  onClick("sheet-sync", sheetSync);
  onClick("term-submit", createTerm);
  onClick("nominate-submit", nominate);
  onClick("add-fixture", () => addFixtureRow());
  $("signout").addEventListener("click", signOut);

  $("cal-prev").addEventListener("click", () => {
    state.month = new Date(state.month.getFullYear(), state.month.getMonth() - 1, 1);
    renderCalendar();
  });
  $("cal-next").addEventListener("click", () => {
    state.month = new Date(state.month.getFullYear(), state.month.getMonth() + 1, 1);
    renderCalendar();
  });
  $("cal-today").addEventListener("click", () => {
    const now = new Date();
    state.month = new Date(now.getFullYear(), now.getMonth(), 1);
    renderCalendar();
  });

  ($("b-sport") as HTMLSelectElement).addEventListener("change", syncTeamOptions);
  ($("p-team") as HTMLSelectElement).addEventListener("change", loadRoster);
  ($("nom-role") as HTMLSelectElement).addEventListener("change", () => {
    $("nom-sport-field").hidden = val("nom-role") !== "REP";
  });

  for (const id of ["login-email", "login-password"]) {
    $(id).addEventListener("keydown", (e) => {
      if ((e as KeyboardEvent).key === "Enter") ($("login-submit") as HTMLButtonElement).click();
    });
  }

  defaultTimes();
  bindTimeCoupling();
  renderSession();
  renderDrafts();
  renderDetail();
  loadReference().then(() => { addFixtureRow(); addFixtureRow(); });
  loadSchedule();
  setInterval(loadSchedule, 6000);
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init);
} else {
  init();
}
