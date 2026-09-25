"""show_scheduler.py — the engine that plays what the schedule says (#954).

One daemon thread ("show-scheduler"). Each tick asks ``schedule_eval.evaluate``
what should be playing, and when the answer changes it calls the injected
``actions`` object — so the engine is unit-testable with a fake clock and fake
actions, and every side effect lives in parent_server.

Rules (operator decisions 2026-09-24, each a setting in the document):

* **Idle is the wash**, never black, unless an entry says ``off`` or idle is
  set to ``off``. Transitions are hand-offs: the outgoing show does not sweep
  to black, so there is no dark frame between entries.
* **Manual wins.** Any start/stop/next that isn't from the scheduler sets an
  override: the engine goes passive ("Manual — schedule paused"). Resume clears
  it; with ``overridePolicy.autoResumeAtBoundary`` (default on) it also clears
  at the next window edge. The override is persisted, so a restart does not
  silently resume a show the operator stopped.
* **Kill switch.** ``enabled: false`` → the engine never touches output.
* **Restart.** Inside a window with ``resume: position``, it joins in progress
  at the position wall-clock says; a window that ended while the orchestrator
  was down is skipped (no catch-up).
"""

import threading
import time
from collections import deque
from datetime import datetime, timezone

import schedule_eval as se

TICK_MAX_S = 60.0


def same_content(a, b):
    """Two plays that put the same thing on the stage (so moving between an
    entry and idle that both loop the wash doesn't restart it)."""
    if not a or not b:
        return False
    ka, kb = a.get("kind"), b.get("kind")
    if ka != kb:
        return False
    if ka == "timeline":
        return a.get("timelineId") == b.get("timelineId")
    if ka == "playlist":
        return list(a.get("order") or []) == list(b.get("order") or [])
    return True


class ShowScheduler:
    def __init__(self, get_doc, actions, state=None, save_state=None, log=None,
                 clock=time.time):
        """
        get_doc()     → the schedule document (dict)
        actions       → .play(play, position_s, decision) / .idle(play, decision)
                        / .off(decision) / .durations(play) → seconds or None
        state         → persisted dict: {"override": {...}|None, ...}
        save_state(s) → persist it
        """
        self._get_doc = get_doc
        self._actions = actions
        self.state = state if state is not None else {}
        self.state.setdefault("override", None)
        self._save_state = save_state or (lambda s: None)
        self._log = log
        self._clock = clock
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._lock = threading.RLock()
        self._thread = None
        self.current = None          # {"key", "play", "since", "reason"}
        self.last_decision = None
        self.last_tick = None
        self.log_ring = deque(maxlen=500)

    # ── logging ─────────────────────────────────────────────────────────────
    def _note(self, msg):
        entry = {"at": datetime.fromtimestamp(self._clock(), timezone.utc), "msg": msg}
        self.log_ring.append(entry)
        if self._log is not None:
            self._log.info("Scheduler: %s", msg)

    # ── thread ──────────────────────────────────────────────────────────────
    def start(self, delay_s=5.0):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()

        def run():
            if delay_s and self._stop.wait(delay_s):
                return
            while not self._stop.is_set():
                try:
                    wait = self.tick()
                except Exception as exc:          # never die silently
                    if self._log is not None:
                        self._log.exception("Scheduler: tick failed")
                    self._note(f"tick failed: {exc}")
                    wait = TICK_MAX_S
                self._wake.wait(max(0.5, min(TICK_MAX_S, wait)))
                self._wake.clear()

        self._thread = threading.Thread(target=run, daemon=True, name="show-scheduler")
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._wake.set()

    def wake(self):
        """Re-evaluate now (document saved, override changed, …)."""
        self._wake.set()

    # ── override ────────────────────────────────────────────────────────────
    def set_override(self, kind="manual", by=None):
        with self._lock:
            now = self._clock()
            resume_at = None
            if self.last_decision and self.last_decision.get("next"):
                resume_at = self.last_decision["next"]["atUtc"].timestamp()
            self.state["override"] = {"active": True, "kind": kind, "by": by,
                                      "since": now, "resumeAt": resume_at}
            self._save_state(self.state)
            self._note(f"manual override ({kind}) by {by or 'unknown'} — schedule paused"
                       + (" until the next window edge" if resume_at else ""))
            # The operator owns the stage now; forget what we last started so
            # a resume re-applies the schedule even if it's the same content.
            self.current = None
        self.wake()

    def resume(self, by=None):
        with self._lock:
            had = bool((self.state.get("override") or {}).get("active"))
            self.state["override"] = None
            self._save_state(self.state)
            if had:
                self._note(f"resumed by {by or 'operator'}")
        self.wake()

    def override_active(self):
        return bool((self.state.get("override") or {}).get("active"))

    # ── one evaluation ──────────────────────────────────────────────────────
    def tick(self):
        """Evaluate and act once. Returns seconds until the next tick."""
        with self._lock:
            now = self._clock()
            if self.last_tick is not None and now - self.last_tick > 2 * TICK_MAX_S + 5:
                self._note(f"clock jumped +{int(now - self.last_tick)} s; re-evaluating")
            self.last_tick = now
            doc = self._get_doc() or {}
            if not doc.get("enabled"):
                if self.current is not None:
                    self._note("disabled — no longer driving output")
                self.current = None
                self.last_decision = None
                return TICK_MAX_S
            decision = se.evaluate(doc, now)
            self.last_decision = decision
            nxt = decision.get("next")
            wait = TICK_MAX_S if not nxt else nxt["atUtc"].timestamp() - now + 0.5

            ov = self.state.get("override") or {}
            if ov.get("active"):
                policy = (doc.get("overridePolicy") or {})
                resume_at = ov.get("resumeAt")
                if policy.get("autoResumeAtBoundary", True) and resume_at and now >= resume_at:
                    self.state["override"] = None
                    self._save_state(self.state)
                    self._note("override cleared at the window edge "
                               "(auto-resume) — back on schedule")
                else:
                    if resume_at and policy.get("autoResumeAtBoundary", True):
                        wait = min(wait, resume_at - now + 0.5)
                    return wait

            play = decision["play"]
            cur = self.current
            if cur is not None and cur["key"] == decision["key"]:
                # Phase 2 — fade out ahead of an 'off' edge (never into
                # another show: that would put a dark dip in a hand-off).
                tr = (decision.get("window") or {}).get("transition") or {}
                fo = float(tr.get("fadeOutS") or 0)
                if fo > 0 and nxt and (nxt.get("play") or {}).get("kind") == "off" \
                        and not cur.get("fadingOut") and hasattr(self._actions, "fade"):
                    edge = nxt["atUtc"].timestamp()
                    if now >= edge - fo:
                        self._actions.fade(1.0, 0.0, max(0.5, edge - now))
                        cur["fadingOut"] = True
                        self._note(f"fading out over {int(edge - now)} s before going dark")
                    else:
                        wait = min(wait, edge - fo - now + 0.2)
                return wait
            if cur is not None and same_content(cur["play"], play) \
                    and play.get("kind") != "off":
                # Entry ↔ idle on the same content: keep it running.
                self.current = dict(cur, key=decision["key"], reason=decision["reason"])
                self._note(f"now {self._what(decision)} — same content, left running")
                return wait
            self._apply(decision, now)
            return wait

    def _what(self, decision):
        if decision.get("entry"):
            e = decision["entry"]
            return f"{e.get('scheduleName')} › {e.get('name')}".strip(" ›")
        return decision.get("source", "idle")

    def _apply(self, decision, now):
        play = decision["play"]
        kind = play.get("kind")
        prev_dark = self.current is None or \
            (self.current.get("play") or {}).get("kind") == "off"
        old = self._what({"entry": None, "source": "nothing"}) if self.current is None \
            else self.current.get("what")
        what = self._what(decision)
        pos = 0.0
        if kind in ("timeline", "playlist"):
            pos = float(decision.get("elapsedS") or 0.0)
            if decision["source"] == "entry":
                self._actions.play(play, pos, decision)
            else:
                self._actions.idle(play, decision)
            # Phase 2 — fade in, only when coming up from dark.
            tr = (decision.get("window") or {}).get("transition") or {}
            fi = float(tr.get("fadeInS") or 0)
            if fi > 0 and prev_dark and hasattr(self._actions, "fade"):
                self._actions.fade(0.0, 1.0, fi)
        elif kind == "hold":
            self._actions.idle(play, decision)
        else:
            self._actions.off(decision)
        win = decision.get("window")
        span = ""
        if win:
            span = (f" (window {win['startUtc'].strftime('%H:%M')}–"
                    f"{win['endUtc'].strftime('%H:%M')} UTC, pos {int(pos)//60:02d}:"
                    f"{int(pos)%60:02d})")
        self._note(f"{old} -> {what} because {decision['reason']}{span}")
        self.current = {"key": decision["key"], "play": play, "since": now,
                        "reason": decision["reason"], "what": what}

    # ── state view ──────────────────────────────────────────────────────────
    def state_view(self, doc):
        """The /api/schedule/state body (datetimes still as datetimes)."""
        now = self._clock()
        enabled = bool((doc or {}).get("enabled"))
        try:
            tz = se.tzinfo_for(doc)
        except se.ScheduleError as exc:
            # No tz database on this host (Windows without the tzdata
            # package) or an unknown zone: say so instead of a 500.
            return {"enabled": enabled, "override": self.state.get("override"),
                    "clock": None, "current": None, "now": None, "next": None,
                    "sun": None, "error": str(exc)}
        local = datetime.fromtimestamp(now, timezone.utc).astimezone(tz)
        out = {"enabled": enabled,
               "override": self.state.get("override"),
               "clock": {"now": datetime.fromtimestamp(now, timezone.utc),
                         "tz": str(tz), "utcOffsetMin":
                         int(local.utcoffset().total_seconds() // 60),
                         "isDst": bool(local.dst())},
               "current": None if self.current is None else
               {"what": self.current.get("what"), "play": self.current.get("play"),
                "since": datetime.fromtimestamp(self.current["since"], timezone.utc),
                "reason": self.current.get("reason")}}
        try:
            d = se.evaluate(doc, now)
        except se.ScheduleError as exc:
            out.update(now=None, next=None, sun=None, error=str(exc))
            return out
        out["now"] = {"play": d["play"], "source": d["source"], "entry": d["entry"],
                      "window": d["window"], "reason": d["reason"],
                      "positionS": d["elapsedS"], "candidates": d["candidates"]}
        out["next"] = d.get("next")
        out["sun"] = d.get("sun")
        out["notes"] = d.get("notes")
        return out
