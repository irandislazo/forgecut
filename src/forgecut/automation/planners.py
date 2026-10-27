"""Planificadores de timeline (sin UI, sin ffmpeg, deterministas con una semilla).

Reciben una Template + la duración de la canción y devuelven un Plan: lista de PlanClip en
segundos. La conversión a frames/Clip la hace build_timeline.py.

La Voz (v3 + regla nueva)
  - Intro solo de reacciones "antes de girar".
  - NUEVO: justo después del intro SIEMPRE va un lipsync (si cabe sin sacrificar los 4 giros).
  - Los 4 jueces giran siempre, una sola vez cada uno, sin repetir el clip de giro.
  - Un juez nunca tiene clips "girado" antes de girar ni "antes" después de girar.
  - Nunca el mismo juez ni el mismo archivo seguidos; sin dos planos lejanos seguidos.
  - 1ª mitad (tras el intro): máx. 3 clips seguidos sin lipsync (si el tiempo lo permite).
Got Talent
  - Intro corto de reacciones + lejanos, y luego ciclo Reacción → Lejano → Lipsync → Lejano.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from .templates import Template

REACT_MAX_S = 5.0        # una reacción nunca dura más de esto en el timeline


@dataclass
class PlanClip:
    kind: str                       # reaction | turn | far | lipsync
    start_s: float
    dur_s: float
    path: Optional[str] = None      # None para lipsync (hueco vacío)
    judge: int = 0
    meta: dict = field(default_factory=dict)


@dataclass
class Plan:
    clips: List[PlanClip] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    info: dict = field(default_factory=dict)

    @property
    def total(self) -> float:
        return sum(c.dur_s for c in self.clips)


def _durations(tpl: Template) -> Dict[str, float]:
    return {i.path: i.duration_s for pool in tpl.pools.values() for i in pool}


def _paths(tpl: Template, key: str) -> List[str]:
    return [i.path for i in tpl.pool(key)]


def _finalize(clips: List[PlanClip], song_dur: float, far_pick: Callable[[], Optional[str]]) -> None:
    """Cuadra el total con el audio exacto sin crear dos lejanos seguidos."""
    total = sum(c.dur_s for c in clips)
    if not clips:
        return
    if total < song_dur - 1e-6:
        if clips[-1].kind == "far":
            clips[-1].dur_s += song_dur - total
        else:
            clips.append(PlanClip("far", total, song_dur - total, far_pick()))
    elif total > song_dur + 1e-6:
        clips[-1].dur_s = max(0.1, clips[-1].dur_s - (total - song_dur))
    t = 0.0
    for c in clips:                     # recalcula inicios acumulados
        c.start_s = t
        t += c.dur_s


# ═══════════════════════════════════════════════════════════════════
#  LA VOZ
# ═══════════════════════════════════════════════════════════════════
def plan_la_voz(song_dur: float, tpl: Template, rng: Optional[random.Random] = None,
                lipsync_after_intro: bool = True) -> Plan:
    rng = rng or random.Random()
    if song_dur <= 5:
        raise ValueError("Audio de canción demasiado corto.")
    miss = tpl.missing()
    if miss:
        raise ValueError("Plantilla incompleta:\n- " + "\n- ".join(miss))

    FAR_S, L = float(tpl.far_s), float(tpl.lipsync_s)
    dur_known = _durations(tpl)
    far = _paths(tpl, "far")
    before = {j: _paths(tpl, f"j{j}_antes") for j in range(1, 5)}
    turning = {j: _paths(tpl, f"j{j}_girando") for j in range(1, 5)}
    turned = {j: _paths(tpl, f"j{j}_girado") for j in range(1, 5)}
    reacts2 = _paths(tpl, "react2")
    plan = Plan()
    clips: List[PlanClip] = []
    used, usage, t = set(), {}, 0.0

    def add(kind, judge, dur, path, **meta):
        nonlocal t
        clips.append(PlanClip(kind, t, float(dur), path, judge, meta))
        t += float(dur)

    def dur(path, fallback=3.0):
        return max(0.5, min(dur_known.get(path) or fallback, REACT_MAX_S))

    def fresh(pool, recent=4):
        pool = list(dict.fromkeys(pool))
        if not pool:
            return None
        recent_paths = [c.path for c in clips[-recent:]]
        cand = ([x for x in pool if x not in recent_paths]
                or [x for x in pool if not clips or x != clips[-1].path] or pool)
        low = min(usage.get(x, 0) for x in cand)
        x = rng.choice([y for y in cand if usage.get(y, 0) == low])
        usage[x] = usage.get(x, 0) + 1
        return x

    def react(j, state):
        pool = before[j] if state == "antes" else turned[j]
        return fresh([x for x in pool if x not in used] or pool)

    def far_clip():
        return fresh(far)

    def far_ok():
        return not clips or clips[-1].kind != "far"

    def last_judge():
        return clips[-1].judge if clips else 0

    def pick_k(ks, state):
        lj = last_judge()
        cand = [k for k in ks if k != lj] or list(ks)
        if state:
            pool_of = (lambda k: before[k]) if state == "antes" else (lambda k: turned[k])
            nuevos = [k for k in cand if any(usage.get(x, 0) == 0 for x in pool_of(k))]
            cand = nuevos or cand
        return rng.choice(cand)

    def pick_judge_state(el_turned, el_before, p_turned=0.45):
        lj = last_judge()
        t_ok = [k for k in el_turned if k != lj]
        b_ok = [k for k in el_before if k != lj]
        if t_ok and (not b_ok or rng.random() < p_turned):
            return pick_k(t_ok, "girado"), "girado"
        if b_ok:
            return pick_k(b_ok, "antes"), "antes"
        if el_turned and (not el_before or rng.random() < p_turned):
            return pick_k(el_turned, "girado"), "girado"
        if el_before:
            return pick_k(el_before, "antes"), "antes"
        return None, None

    def rand_judge():
        lj = last_judge()
        return rng.choice([k for k in range(1, 5) if k != lj])

    intro_n = 0

    def run_len():
        n = 0
        for c in reversed(clips[intro_n:]):
            if c.kind == "lipsync":
                break
            n += 1
        return n

    turn_len = {j: max([dur(p) for p in turning[j]] or [3.0]) for j in range(1, 5)}
    pending: List[int] = []
    half = song_dur / 2.0

    def can_lip(extra=0.0):
        return (half - t - extra - L) >= (sum(turn_len[k] for k in pending) + 0.5)

    def need_lip():
        r = run_len()
        return r >= 3 or (r >= 1 and not can_lip(5.0))

    def add_lipsync(max_dur=None):
        d = min(L, L if max_dur is None else max_dur)
        if d >= L - 0.5 and (not clips or clips[-1].kind != "lipsync"):
            add("lipsync", 0, L, None, shot=rng.choice(["C", "D"]))
            return True
        return False

    # ── Intro: solo reacciones previas al giro ────────────────────
    intro_end = min(max(0.0, float(tpl.intro_s)), half * 0.55)
    while t < intro_end - 0.25:
        available = [j for j in range(1, 5) if before[j]]
        if not available:
            break
        j = pick_k(available, "antes")
        path = react(j, "antes")
        if not path:
            break
        add("reaction", j, min(dur(path), intro_end - t), path, state="antes")
    intro_n = len(clips)

    order = rng.sample([1, 2, 3, 4], 4)
    pending = list(order)

    # ── NUEVO: lipsync obligatorio justo tras el intro ────────────
    if lipsync_after_intro:
        if can_lip():
            add_lipsync()
        else:
            plan.warnings.append("Canción corta: no cabe el lipsync inicial sin sacrificar los 4 giros.")

    # ── Cuatro giros con separación temporal ──────────────────────
    start = max(t + 0.1, min(intro_end + 3.0, half * 0.22))
    targets = [max(start, half * f) for f in (0.30, 0.48, 0.66, 0.83)]
    targets = [max(targets[i], (targets[i - 1] + 3.0) if i else 0) for i in range(4)]
    for idx, j in enumerate(order):
        target = min(targets[idx], half - 2.5)
        while t + 1.0 < target:
            remaining_to_turn = target - t
            if need_lip() and can_lip():
                if add_lipsync():
                    continue
            if remaining_to_turn >= L + 2.0 and (not clips or clips[-1].kind != "lipsync") and rng.random() < 0.38:
                if add_lipsync(remaining_to_turn):
                    continue
            if remaining_to_turn > 7 and rng.random() < 0.22 and far_ok():
                add("far", 0, min(FAR_S, remaining_to_turn), far_clip())
                continue
            el_t = [k for k in order[:idx] if turned[k]]
            el_b = [k for k in order[idx:] if before[k] and k != j]
            k, state = pick_judge_state(el_t, el_b)
            if k is None:
                break
            if k == last_judge() and far_ok() and remaining_to_turn > FAR_S:
                add("far", 0, min(FAR_S, remaining_to_turn), far_clip())
                continue
            path = react(k, state)
            if not path:
                break
            add("reaction", k, min(dur(path), remaining_to_turn), path,
                state="girado" if state == "girado" else "antes")
        if last_judge() == j:
            k2, st2 = pick_judge_state([k for k in order[:idx] if turned[k]],
                                       [k for k in order[idx + 1:] if before[k]])
            if k2 is not None and k2 != j:
                p2 = react(k2, st2)
                if p2:
                    d2 = dur(p2)
                    if (half - t) >= d2 + sum(turn_len[k] for k in pending) + 0.5:
                        add("reaction", k2, d2, p2, state="girado" if st2 == "girado" else "antes")
        if need_lip() and can_lip():
            add_lipsync()
        path = rng.choice([x for x in turning[j] if x not in used] or turning[j])
        used.add(path)
        d = max(0.5, half - t) if t + dur(path) > half else dur(path)
        add("turn", j, d, path, state="girando")
        pending.remove(j)

    # ── Resto de la 1ª mitad ──────────────────────────────────────
    while t < half - 2.0:
        remaining_half = half - t
        if run_len() >= 3 and remaining_half >= L and clips[-1].kind != "lipsync":
            if add_lipsync(remaining_half):
                continue
        if run_len() >= 3 and remaining_half < L:
            break
        if (remaining_half >= L and (not clips or clips[-1].kind != "lipsync") and
                (not clips or clips[-1].kind in ("reaction", "turn") or rng.random() < 0.35)):
            if add_lipsync(remaining_half):
                continue
        if remaining_half < L and remaining_half > FAR_S and run_len() >= 1 and far_ok():
            add("far", 0, min(FAR_S, remaining_half), far_clip())
            continue
        turned_j = [j for j in range(1, 5) if any(c.kind == "turn" and c.judge == j for c in clips)]
        notturned_j = [j for j in range(1, 5) if j not in turned_j and before[j]]
        if turned_j and (not notturned_j or rng.random() < 0.5):
            pool_j, state = turned_j, "girado"
        else:
            pool_j, state = notturned_j, "antes"
        if far_ok() and rng.random() < 0.16 and half - t > FAR_S:
            add("far", 0, min(FAR_S, half - t), far_clip())
            continue
        if not pool_j:
            add("far", 0, min(FAR_S, half - t), far_clip())
            continue
        j = pick_k(pool_j, state)
        if j == last_judge() and far_ok() and half - t > FAR_S:
            add("far", 0, min(FAR_S, half - t), far_clip())
            continue
        path = react(j, state)
        if not path:
            break
        add("reaction", j, min(dur(path), half - t), path, state=state)

    # ── 2ª mitad: reacciones genéricas + lejanos + lipsync ────────
    while t < song_dur - 0.25:
        rem = song_dur - t
        n_before = len(clips)
        if rem >= L + 5:
            budget = 3 - run_len()
            if reacts2 and budget >= 1 and (rng.random() < 0.6 or not far_ok()):
                p = fresh(reacts2)
                add("reaction", rand_judge(), min(dur(p), rem), p, state="r2")
                budget -= 1
            if song_dur - t >= FAR_S and far_ok() and budget >= 1:
                add("far", 0, min(FAR_S, song_dur - t), far_clip())
            if song_dur - t >= L and (not clips or clips[-1].kind != "lipsync"):
                add_lipsync()
            if song_dur - t >= FAR_S and far_ok():
                add("far", 0, min(FAR_S, song_dur - t), far_clip())
        else:
            if reacts2 and rem > 0.5 and (not far_ok() or clips[-1].kind != "reaction"):
                p = fresh(reacts2)
                add("reaction", rand_judge(), min(dur(p), rem), p, state="r2")
            elif reacts2 and rem > 0.5:
                add("far", 0, min(FAR_S, rem), far_clip())
            elif rem > 0.5:
                add("far", 0, rem, far_clip())
            else:
                break
        if len(clips) == n_before:          # garantía de progreso: nunca un bucle sin avanzar
            if reacts2 and far_ok() is False:
                p = fresh(reacts2); add("reaction", rand_judge(), min(dur(p), rem), p, state="r2")
            else:
                add("far", 0, min(FAR_S, rem), far_clip())
        if len(clips) > 9999:
            break

    _finalize(clips, song_dur, far_clip)
    plan.clips = clips
    plan.info = {"order": order, "lipsyncs": sum(c.kind == "lipsync" for c in clips),
                 "style": "la_voz", "song_dur": song_dur}
    return plan


# ═══════════════════════════════════════════════════════════════════
#  GOT TALENT
# ═══════════════════════════════════════════════════════════════════
def plan_got_talent(song_dur: float, tpl: Template, rng: Optional[random.Random] = None) -> Plan:
    rng = rng or random.Random()
    if song_dur <= 5:
        raise ValueError("Audio de canción demasiado corto.")
    miss = tpl.missing()
    if miss:
        raise ValueError("Plantilla incompleta:\n- " + "\n- ".join(miss))

    FAR_S, L = float(tpl.far_s), float(tpl.lipsync_s)
    dur_known = _durations(tpl)
    reacts1, reacts2, far = _paths(tpl, "react1"), _paths(tpl, "react2"), _paths(tpl, "far")
    clips: List[PlanClip] = []
    usage: Dict[str, int] = {}
    t = 0.0
    consecutive_reactions = 0
    last_was_lipsync = False

    def fresh(pool, recent=4):
        pool = list(dict.fromkeys(pool))
        if not pool:
            return None
        recent_paths = [c.path for c in clips[-recent:]]
        cand = ([x for x in pool if x not in recent_paths]
                or [x for x in pool if not clips or x != clips[-1].path] or pool)
        low = min(usage.get(x, 0) for x in cand)
        x = rng.choice([y for y in cand if usage.get(y, 0) == low])
        usage[x] = usage.get(x, 0) + 1
        return x

    def rand_judge():
        lj = clips[-1].judge if clips else 0
        return rng.choice([k for k in range(1, 5) if k != lj])

    def add(kind, judge, dur_, path, **meta):
        nonlocal t, consecutive_reactions, last_was_lipsync
        clips.append(PlanClip(kind, t, float(dur_), path, judge, meta))
        t += float(dur_)
        consecutive_reactions = consecutive_reactions + 1 if kind == "reaction" else 0
        last_was_lipsync = kind == "lipsync"

    def pick_reaction():
        if reacts2 and t > song_dur / 2:
            return fresh(reacts2)
        return fresh(reacts1) if reacts1 else fresh(reacts2)

    def d_of(path):
        return dur_known.get(path) or 3.0

    def remaining():
        return song_dur - t

    def far_ok():
        return not clips or clips[-1].kind != "far"

    intro_target = min(30.0, song_dur * 0.12)
    while t < intro_target and remaining() > 1.0:
        n_before = len(clips)
        rv = pick_reaction()
        if rv:
            d = d_of(rv)
            if t + d <= intro_target + 2.0:
                add("reaction", rand_judge(), d, rv)
        if far_ok():
            add("far", 0, FAR_S, fresh(far))
        if len(clips) == n_before:          # nada cabe sin romper reglas: el intro termina aquí
            break

    while t + 1.0 < song_dur:
        rem = remaining()
        n_before = len(clips)
        if rem >= L and not last_was_lipsync:
            if consecutive_reactions < 2:
                rv = pick_reaction()
                if rv and t + d_of(rv) + FAR_S + L <= song_dur + 2.0:
                    add("reaction", rand_judge(), d_of(rv), rv)
            if t + FAR_S + L <= song_dur + 2.0 and far_ok():
                add("far", 0, FAR_S, fresh(far))
            if remaining() >= L:
                add("lipsync", 0, L, None, shot=rng.choice(["C", "D"]))
            if remaining() >= FAR_S and far_ok():
                add("far", 0, FAR_S, fresh(far))
        elif rem >= FAR_S:
            if far_ok():
                add("far", 0, FAR_S, fresh(far))
            if consecutive_reactions < 2:
                rv = pick_reaction()
                if rv and t + d_of(rv) <= song_dur + 1.0:
                    add("reaction", rand_judge(), d_of(rv), rv)
        else:
            if consecutive_reactions < 2:
                rv = pick_reaction()
                if rv:
                    d = min(d_of(rv), rem)
                    if d >= 0.5:
                        add("reaction", rand_judge(), d, rv)
            if remaining() >= 1.0 and far_ok():
                add("far", 0, min(FAR_S, remaining()), fresh(far))
            break
        if len(clips) == n_before:          # garantía de progreso
            rv = fresh(reacts1 + reacts2)
            if rv and not far_ok():
                add("reaction", rand_judge(), min(d_of(rv), rem), rv)
            else:
                add("far", 0, min(FAR_S, rem), fresh(far))
        if len(clips) > 9999:
            break

    _finalize(clips, song_dur, lambda: fresh(far))
    plan = Plan(clips=clips)
    plan.info = {"lipsyncs": sum(c.kind == "lipsync" for c in clips), "style": "got_talent",
                 "song_dur": song_dur}
    return plan


def plan_timeline(song_dur: float, tpl: Template, seed: Optional[int] = None) -> Plan:
    rng = random.Random(seed)
    return plan_la_voz(song_dur, tpl, rng) if tpl.style == "la_voz" else plan_got_talent(song_dur, tpl, rng)
