"""Test de charge du socle : mesure le débit, le retard de collecte, la charge des conteneurs
et le volume disque par point, puis écrit un rapport Markdown.

Prérequis : pile démarrée avec ./deploy/mesctl install --demo
Usage     : python3 tests/loadtest.py --minutes 10 [--report tests/results/rapport.md]
"""
from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import time
from datetime import datetime
from pathlib import Path

DEPLOY = Path(__file__).resolve().parents[1] / "deploy"


def compose(*args: str) -> str:
    return subprocess.run(["docker", "compose", "--env-file", "site.env", "-f", "docker-compose.yml", *args],
                          cwd=DEPLOY, check=True, capture_output=True, text=True).stdout


def sql(q: str) -> str:
    return compose("exec", "-T", "db", "psql", "-U", "mes", "-d", "mes", "-Atc", q).strip()


def container_stats() -> dict[str, tuple[float, float]]:
    out = subprocess.run(["docker", "stats", "--no-stream", "--format", "{{json .}}"],
                         check=True, capture_output=True, text=True).stdout
    res = {}
    for line in out.splitlines():
        s = json.loads(line)
        name = s["Name"].removeprefix("mes-").rsplit("-", 1)[0]
        mem = s["MemUsage"].split("/")[0].strip()
        num = float("".join(c for c in mem if c.isdigit() or c == "."))
        mib = num * (1024 if "GiB" in mem else 1 / 1024 if "KiB" in mem else 1)
        res[name] = (float(s["CPUPerc"].rstrip("%")), mib)
    return res


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--minutes", type=float, default=10)
    p.add_argument("--report", default=str(Path(__file__).parent / "results" / f"charge-{datetime.now():%Y%m%d-%H%M}.md"))
    args = p.parse_args()

    tags = int(sql("SELECT count(*) FROM catalog.tag WHERE enabled AND historize"))
    start_ts = sql("SELECT now()")
    end = time.time() + args.minutes * 60
    rates, lags, stats = [], [], {}
    while time.time() < end:
        rates.append(float(sql("SELECT count(*) / 10.0 FROM historian.value WHERE time > now() - interval '11 seconds' AND time <= now() - interval '1 second'")))
        lags.append(float(sql("SELECT coalesce(extract(epoch FROM now() - max(time)), -1) FROM historian.value WHERE time > now() - interval '5 minutes'")))
        for k, v in container_stats().items():
            stats.setdefault(k, []).append(v)
        time.sleep(10)

    rows = int(sql(f"SELECT count(*) FROM historian.value WHERE time >= '{start_ts}'"))
    total_rows = int(sql("SELECT count(*) FROM historian.value"))
    size_raw = int(sql("SELECT hypertable_size('historian.value')"))
    # compression des chunks pour mesurer l'occupation réelle après la politique de compression
    sql("SELECT count(compress_chunk(c, if_not_compressed => true)) FROM show_chunks('historian.value') c")
    size_comp = int(sql("SELECT hypertable_size('historian.value')"))

    rate = statistics.mean(rates)
    b_raw = size_raw / total_rows
    b_comp = size_comp / total_rows
    per_day = rate * 86400
    lines = [
        f"# Test de charge du socle MES v3 ({datetime.now():%Y-%m-%d %H:%M})",
        "",
        f"Durée : {args.minutes:g} min · tags surveillés : {tags} · lignes écrites pendant le test : {rows}",
        "",
        "## Collecte",
        "",
        "| Mesure | Valeur |",
        "| --- | --- |",
        f"| Points écrits par seconde (moyenne) | {rate:.0f} |",
        f"| Points écrits par seconde (min / max) | {min(rates):.0f} / {max(rates):.0f} |",
        f"| Retard de collecte moyen | {statistics.mean(lags):.2f} s |",
        f"| Retard de collecte maximal | {max(lags):.2f} s |",
        "",
        "## Stockage",
        "",
        "| Mesure | Valeur |",
        "| --- | --- |",
        f"| Octets par point, non compressé | {b_raw:.1f} |",
        f"| Octets par point, compressé | {b_comp:.1f} |",
        f"| Points par jour à ce débit | {per_day / 1e6:.0f} millions |",
        f"| Disque par jour (compressé) | {per_day * b_comp / 1e9:.2f} Go |",
        f"| Disque par an (compressé) | {per_day * b_comp * 365 / 1e9:.0f} Go |",
        f"| Chunk du jour non compressé (2 jours en ligne) | {per_day * b_raw * 2 / 1e9:.1f} Go |",
        "",
        "## Charge des conteneurs",
        "",
        "| Conteneur | CPU moyen (% d'un cœur) | CPU max | RAM moyenne (Mio) |",
        "| --- | --- | --- | --- |",
    ]
    for name, vals in sorted(stats.items()):
        cpu = [v[0] for v in vals]
        mem = [v[1] for v in vals]
        lines.append(f"| {name} | {statistics.mean(cpu):.0f} | {max(cpu):.0f} | {statistics.mean(mem):.0f} |")
    report = "\n".join(lines) + "\n"
    Path(args.report).parent.mkdir(parents=True, exist_ok=True)
    Path(args.report).write_text(report, encoding="utf-8")
    print(report)


if __name__ == "__main__":
    main()
