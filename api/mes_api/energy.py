"""Répartition de l'énergie en production et hors production.

Un compteur rattaché à une ligne (attribut « ligne ») est « en production » quand sa ligne tourne,
c'est-à-dire hors de tout arrêt détecté (planifié ou non). Un compteur commun (utilités, éclairage...)
est en production tant qu'au moins une ligne tourne. Les incréments du compteur sont lus par minute
(période de 3 jours au plus) ou par heure, et partagés au prorata du temps de marche de chaque pas.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


def merge(intervals):
    out: list[list[datetime]] = []
    for s, e in sorted(i for i in intervals if i[1] > i[0]):
        if out and s <= out[-1][1]:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return out


def stopped_seconds(stops, start: datetime, step: int) -> dict[datetime, float]:
    """Secondes d'arrêt par pas de `step` secondes (clé : début du pas, aligné sur `start`)."""
    out: dict[datetime, float] = {}
    for s, e in merge(stops):
        s = max(s, start)
        if e <= s:
            continue
        k = int((s - start).total_seconds() // step)
        b = start + timedelta(seconds=k * step)
        while b < e:
            b1 = b + timedelta(seconds=step)
            ov = (min(e, b1) - max(s, b)).total_seconds()
            if ov > 0:
                out[b] = out.get(b, 0.0) + ov
            b = b1
    return out


def deltas(seed: float | None, samples: list[tuple[datetime, float]]) -> list[tuple[datetime, float]]:
    """Incréments d'un compteur par pas ; une remise à zéro ne donne pas d'incrément négatif."""
    out, prev = [], seed
    for t, v in samples:
        if v is None:
            continue
        if prev is not None:
            out.append((t, max(0.0, v - prev)))
        prev = v
    return out


def split(meters, samples, seeds, line_stops, start: datetime, end: datetime, step: int, tz: ZoneInfo, by_day: bool):
    """meters : [{id, tag_id, compteur, line, usage}] ; samples : {tag_id: [(pas, dernière valeur)]} ;
    line_stops : {nom de ligne: [(début, fin)]}. Renvoie la série, le détail par compteur et le total."""
    start_al = start.replace(second=0, microsecond=0)
    if step == 3600:
        start_al = start_al.replace(minute=0)
    stopped = {ln: stopped_seconds(st, start_al, step) for ln, st in line_stops.items()}

    def run_frac(line, b):
        span = max(1.0, (min(b + timedelta(seconds=step), end) - max(b, start)).total_seconds())
        if line is not None and line in stopped:
            return max(0.0, 1 - stopped[line].get(b, 0.0) / span)
        if not stopped:
            return 1.0
        return max(max(0.0, 1 - s.get(b, 0.0) / span) for s in stopped.values())

    def key(b: datetime) -> datetime:
        loc = b.astimezone(tz)
        if by_day:   # journée de production de 6 h à 6 h
            d = (loc - timedelta(hours=6)).date()
            return datetime(d.year, d.month, d.day, 6, tzinfo=tz)
        return loc.replace(minute=0, second=0, microsecond=0)

    serie: dict[datetime, list[float]] = {}
    rows = []
    for m in meters:
        line = m["line"] if m["line"] in line_stops else None
        p = h = 0.0
        for b, kwh in deltas(seeds.get(m["tag_id"]), samples.get(m["tag_id"], [])):
            f = run_frac(line, b)
            p += kwh * f
            h += kwh * (1 - f)
            acc = serie.setdefault(key(b), [0.0, 0.0])
            acc[0] += kwh * f
            acc[1] += kwh * (1 - f)
        rows.append({"id": m["id"], "compteur": m["compteur"], "line": m["line"], "usage": m["usage"],
                     "kwh": p + h, "prod_kwh": p, "hors_kwh": h})
    rows.sort(key=lambda r: -r["kwh"])
    prod = sum(r["prod_kwh"] for r in rows)
    hors = sum(r["hors_kwh"] for r in rows)
    return ([{"t": k.isoformat(), "prod_kwh": v[0], "hors_kwh": v[1]} for k, v in sorted(serie.items())],
            rows, {"kwh": prod + hors, "prod_kwh": prod, "hors_kwh": hors})
