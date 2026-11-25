"""Génère grafana/dashboards/systeme.json (tableau de bord « Système - supervision technique »).

Le JSON est livré dans le dépôt ; ce script sert à le régénérer après modification :
    python3 grafana/generate/systeme.py
"""
from __future__ import annotations

import json
import pathlib

PROM = {"type": "prometheus", "uid": "prometheus"}
SQL = {"type": "grafana-postgresql-datasource", "uid": "historian"}
RI = "$__rate_interval"
OUT = pathlib.Path(__file__).resolve().parents[1] / "dashboards" / "systeme.json"

panels: list[dict] = []
_id = 0
_y = 0
_x = 0
_row_h = 0


def nid() -> int:
    global _id
    _id += 1
    return _id


def place(w: int, h: int) -> dict:
    """Place les panneaux de gauche à droite, retour à la ligne quand la largeur 24 est atteinte."""
    global _x, _y, _row_h
    if _x + w > 24:
        _x, _y, _row_h = 0, _y + _row_h, 0
    pos = {"h": h, "w": w, "x": _x, "y": _y}
    _x += w
    _row_h = max(_row_h, h)
    return pos


def row(title: str, collapsed: bool = False) -> None:
    global _x, _y, _row_h
    _y += _row_h
    _x, _row_h = 0, 0
    panels.append({"id": nid(), "type": "row", "title": title, "collapsed": collapsed,
                   "gridPos": {"h": 1, "w": 24, "x": 0, "y": _y}, "panels": []})
    _y += 1


def prom(expr: str, legend: str = "", instant: bool = False, ref: str = "A", fmt: str = "time_series") -> dict:
    t = {"refId": ref, "datasource": PROM, "expr": expr, "legendFormat": legend or "__auto",
         "range": not instant, "instant": instant, "editorMode": "code"}
    if fmt != "time_series":
        t["format"] = fmt
    return t


def sql(q: str, ref: str = "A") -> dict:
    return {"refId": ref, "datasource": SQL, "format": "table", "rawQuery": True, "editorMode": "code", "rawSql": q}


def stat(title: str, expr: str, unit: str = "none", w: int = 3, h: int = 4, thresholds=None, decimals=None,
         desc: str = "", mappings=None, ds=PROM, target=None, graph: bool = True) -> None:
    steps = [{"color": "green", "value": None}]
    for v, c in (thresholds or []):
        steps.append({"color": c, "value": v})
    d = {"unit": unit, "thresholds": {"mode": "absolute", "steps": steps}, "color": {"mode": "thresholds"}}
    if decimals is not None:
        d["decimals"] = decimals
    if mappings:
        d["mappings"] = mappings
    panels.append({
        "id": nid(), "type": "stat", "title": title, "description": desc, "datasource": ds,
        "gridPos": place(w, h),
        "fieldConfig": {"defaults": d, "overrides": []},
        "options": {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                    "colorMode": "background" if thresholds else "value",
                    "graphMode": "area" if graph else "none", "textMode": "value", "justifyMode": "center"},
        "targets": [target or prom(expr)],
    })


def ts(title: str, targets: list[dict], unit: str = "short", w: int = 8, h: int = 8, stack: bool = False,
       desc: str = "", overrides=None, minv=None, maxv=None, fill: int = 10, legend_calcs=("mean", "max"),
       legend_table: bool = False) -> None:
    d = {"unit": unit, "custom": {"drawStyle": "line", "lineWidth": 1, "fillOpacity": fill, "showPoints": "never",
                                  "spanNulls": True, "stacking": {"mode": "normal" if stack else "none", "group": "A"},
                                  "axisSoftMin": 0}, "color": {"mode": "palette-classic"}}
    if minv is not None:
        d["min"] = minv
    if maxv is not None:
        d["max"] = maxv
    panels.append({
        "id": nid(), "type": "timeseries", "title": title, "description": desc, "datasource": PROM,
        "gridPos": place(w, h),
        "fieldConfig": {"defaults": d, "overrides": overrides or []},
        "options": {"legend": {"displayMode": "table" if legend_table else "list", "placement": "bottom",
                               "calcs": list(legend_calcs) if legend_table else []},
                    "tooltip": {"mode": "multi", "sort": "desc"}},
        "targets": targets,
    })


def neg(regex: str) -> dict:
    """Série affichée sous l'axe (écritures, émission) : lecture au-dessus, écriture en dessous."""
    return {"matcher": {"id": "byRegexp", "options": regex},
            "properties": [{"id": "custom.transform", "value": "negative-Y"}]}


def table(title: str, targets: list[dict], w: int = 12, h: int = 9, desc: str = "", overrides=None,
          transformations=None, sort=None, ds=PROM, unit: str = "short") -> None:
    panels.append({
        "id": nid(), "type": "table", "title": title, "description": desc, "datasource": ds,
        "gridPos": place(w, h),
        "fieldConfig": {"defaults": {"unit": unit, "custom": {"align": "auto", "cellOptions": {"type": "auto"}}},
                        "overrides": overrides or []},
        "options": {"showHeader": True, "cellHeight": "sm", "footer": {"show": False},
                    "sortBy": sort or []},
        "targets": targets,
        "transformations": transformations or [],
    })


def col(name: str, unit: str | None = None, display: str | None = None, gauge_max=None, decimals=None,
        thresholds=None, width=None) -> dict:
    props = []
    if display:
        props.append({"id": "displayName", "value": display})
    if unit:
        props.append({"id": "unit", "value": unit})
    if decimals is not None:
        props.append({"id": "decimals", "value": decimals})
    if width:
        props.append({"id": "custom.width", "value": width})
    if gauge_max is not None:
        props += [{"id": "custom.cellOptions", "value": {"type": "gauge", "mode": "basic", "valueDisplayMode": "text"}},
                  {"id": "max", "value": gauge_max}, {"id": "min", "value": 0}]
    if thresholds:
        props += [{"id": "thresholds", "value": {"mode": "absolute", "steps":
                   [{"color": "green", "value": None}] + [{"color": c, "value": v} for v, c in thresholds]}},
                  {"id": "custom.cellOptions", "value": {"type": "color-text"}}]
    return {"matcher": {"id": "byName", "options": name}, "properties": props}


def merge_table(rename: dict[str, str], exclude=("Time",)) -> list[dict]:
    return [{"id": "merge", "options": {}},
            {"id": "organize", "options": {"excludeByName": {k: True for k in exclude}, "renameByName": rename}}]


# =====================================================================================================
row("Vue d'ensemble")
CPU_HOST = f'100 * (1 - avg(rate(node_cpu_seconds_total{{mode="idle"}}[{RI}])))'
MEM_HOST = '100 * (1 - node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)'
stat("CPU hôte", CPU_HOST, "percent", thresholds=[(70, "orange"), (90, "red")], decimals=0)
stat("Mémoire hôte", MEM_HOST, "percent", thresholds=[(80, "orange"), (92, "red")], decimals=0)
stat("Disque le plus plein", '100 * max(1 - node_filesystem_avail_bytes{fstype!~"tmpfs|overlay|squashfs|fuse.*"} '
     '/ node_filesystem_size_bytes{fstype!~"tmpfs|overlay|squashfs|fuse.*"})', "percent",
     thresholds=[(80, "orange"), (90, "red")], decimals=0, desc="Système de fichiers de l'hôte le plus rempli.")
stat("Charge / cœur (1 min)", 'node_load1 / scalar(count(node_cpu_seconds_total{mode="idle"}))', "none",
     thresholds=[(0.8, "orange"), (1.5, "red")], decimals=2)
stat("Conteneurs actifs", 'sum(mes_container_running)', "none", graph=False,
     desc="Conteneurs Docker en cours d'exécution sur l'hôte.")
stat("Points/s écrits", f'sum(rate(mes_collector_rows_written_total[{RI}]))', "none", decimals=0,
     desc="Lignes écrites par seconde dans l'historian (après deadband).")
stat("Latence API p95", f'histogram_quantile(0.95, sum by (le) (rate(mes_api_request_duration_seconds_bucket[{RI}])))',
     "s", thresholds=[(0.5, "orange"), (2, "red")], decimals=3)
stat("Cibles en erreur", 'count(up == 0) or vector(0)', "none", thresholds=[(1, "red")], graph=False,
     desc="Exporters ou services que Prometheus n'arrive pas à interroger (détail en bas de page).")

# =====================================================================================================
row("Hôte : CPU, mémoire, disques, réseau")
ts("CPU par mode", [prom(f'100 * sum by (mode) (rate(node_cpu_seconds_total{{mode!="idle"}}[{RI}])) '
                         f'/ scalar(count(node_cpu_seconds_total{{mode="idle"}}))', "{{mode}}")],
   "percent", stack=True, maxv=100, desc="Part du temps CPU total de l'hôte, par mode (user, system, iowait...).")
ts("Mémoire", [prom('node_memory_MemTotal_bytes - node_memory_MemAvailable_bytes', "utilisée"),
               prom('node_memory_Buffers_bytes + node_memory_Cached_bytes', "cache + tampons", ref="B"),
               prom('node_memory_SwapTotal_bytes - node_memory_SwapFree_bytes', "swap utilisé", ref="C"),
               prom('node_memory_MemTotal_bytes', "totale", ref="D")], "bytes",
   overrides=[{"matcher": {"id": "byName", "options": "totale"},
               "properties": [{"id": "custom.fillOpacity", "value": 0}, {"id": "custom.lineStyle",
                              "value": {"fill": "dash", "dash": [6, 4]}}, {"id": "color", "value": {"mode": "fixed", "fixedColor": "text"}}]}])
ts("Charge système", [prom('node_load1', "1 min"), prom('node_load5', "5 min", ref="B"),
                      prom('node_load15', "15 min", ref="C"),
                      prom('count(node_cpu_seconds_total{mode="idle"})', "cœurs", ref="D")], "short", fill=0)
DISK = 'device!~"^(loop|ram|fd|sr|nbd|zram).*"'
ts("Disques : IO/s (lecture ↑ / écriture ↓)",
   [prom(f'sum by (device) (rate(node_disk_reads_completed_total{{{DISK}}}[{RI}]))', "lecture {{device}}"),
    prom(f'sum by (device) (rate(node_disk_writes_completed_total{{{DISK}}}[{RI}]))', "écriture {{device}}", ref="B")],
   "iops", overrides=[neg("^écriture.*")], desc="Opérations d'entrée/sortie par seconde, par disque.")
ts("Disques : débit (lecture ↑ / écriture ↓)",
   [prom(f'sum by (device) (rate(node_disk_read_bytes_total{{{DISK}}}[{RI}]))', "lecture {{device}}"),
    prom(f'sum by (device) (rate(node_disk_written_bytes_total{{{DISK}}}[{RI}]))', "écriture {{device}}", ref="B")],
   "Bps", overrides=[neg("^écriture.*")])
ts("Disques : occupation et attente",
   [prom(f'100 * rate(node_disk_io_time_seconds_total{{{DISK}}}[{RI}])', "occupation {{device}}"),
    prom(f'1000 * rate(node_disk_write_time_seconds_total{{{DISK}}}[{RI}]) / clamp_min(rate(node_disk_writes_completed_total{{{DISK}}}[{RI}]), 1)',
         "attente écriture {{device}} (ms)", ref="B")],
   "percent", fill=0, desc="Occupation : % du temps où le disque traite des IO (100 % = saturé). "
                          "Attente : durée moyenne d'une écriture en ms (axe de droite).",
   overrides=[{"matcher": {"id": "byRegexp", "options": "^attente.*"},
               "properties": [{"id": "unit", "value": "ms"}, {"id": "custom.axisPlacement", "value": "right"}]}])
ts("Réseau (réception ↑ / émission ↓)",
   [prom(f'sum by (device) (rate(node_network_receive_bytes_total[{RI}]))', "reçu {{device}}"),
    prom(f'sum by (device) (rate(node_network_transmit_bytes_total[{RI}]))', "émis {{device}}", ref="B")],
   "Bps", overrides=[neg("^émis.*")])
ts("Réseau : paquets et erreurs",
   [prom(f'sum(rate(node_network_receive_packets_total[{RI}]))', "paquets reçus/s"),
    prom(f'sum(rate(node_network_transmit_packets_total[{RI}]))', "paquets émis/s", ref="B"),
    prom(f'sum(rate(node_network_receive_errs_total[{RI}]) + rate(node_network_transmit_errs_total[{RI}]) '
         f'+ rate(node_network_receive_drop_total[{RI}]) + rate(node_network_transmit_drop_total[{RI}]))',
         "erreurs + rejets/s", ref="C")], "pps", fill=0,
   overrides=[{"matcher": {"id": "byName", "options": "erreurs + rejets/s"},
               "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": "red"}},
                              {"id": "custom.axisPlacement", "value": "right"}]}])
table("Systèmes de fichiers",
      [prom('node_filesystem_size_bytes{fstype!~"tmpfs|overlay|squashfs|nsfs|fuse.*"}', instant=True, fmt="table"),
       prom('node_filesystem_avail_bytes{fstype!~"tmpfs|overlay|squashfs|nsfs|fuse.*"}', instant=True, fmt="table", ref="B"),
       prom('100 * (1 - node_filesystem_avail_bytes{fstype!~"tmpfs|overlay|squashfs|nsfs|fuse.*"} / '
            'node_filesystem_size_bytes{fstype!~"tmpfs|overlay|squashfs|nsfs|fuse.*"})', instant=True, fmt="table", ref="C")],
      w=8, h=8,
      transformations=[{"id": "merge", "options": {}},
                       {"id": "organize", "options": {
                           "excludeByName": {"Time": True, "__name__": True, "instance": True, "job": True,
                                             "device": True, "device_error": True, "fstype": True},
                           "indexByName": {"mountpoint": 0, "fstype": 1, "Value #A": 2, "Value #B": 3, "Value #C": 4},
                           "renameByName": {"mountpoint": "Point de montage", "fstype": "Type", "Value #A": "Taille",
                                            "Value #B": "Libre", "Value #C": "Utilisé"}}},
                       {"id": "groupBy", "options": {"fields": {
                           "Point de montage": {"aggregations": [], "operation": "groupby"},
                           "Taille": {"aggregations": ["max"], "operation": "aggregate"},
                           "Libre": {"aggregations": ["min"], "operation": "aggregate"},
                           "Utilisé": {"aggregations": ["max"], "operation": "aggregate"}}}},
                       {"id": "organize", "options": {"renameByName": {"Taille (max)": "Taille", "Libre (min)": "Libre",
                                                                       "Utilisé (max)": "Utilisé"}}}],
      overrides=[col("Taille", "bytes", width=80), col("Libre", "bytes", width=80),
                 col("Utilisé", "percent", gauge_max=100, decimals=0, width=110)],
      sort=[{"displayName": "Utilisé", "desc": True}])

# =====================================================================================================
row("Conteneurs Docker (services MES)")
C = 'service!=""'
ts("CPU par conteneur", [prom(f'100 * sum by (service) (rate(mes_container_cpu_seconds_total[{RI}]))', "{{service}}")],
   "percent", stack=True, desc="100 % = un cœur entièrement occupé.", legend_table=True)
ts("Mémoire par conteneur", [prom('sum by (service) (mes_container_memory_bytes)', "{{service}}")],
   "bytes", stack=True, legend_table=True, legend_calcs=("lastNotNull", "max"),
   desc="Mémoire utilisée hors cache récupérable (même valeur que docker stats).")
ts("IO disque par conteneur (lecture ↑ / écriture ↓)",
   [prom(f'sum by (service) (rate(mes_container_disk_read_bytes_total[{RI}]))', "lecture {{service}}"),
    prom(f'sum by (service) (rate(mes_container_disk_write_bytes_total[{RI}]))', "écriture {{service}}", ref="B")],
   "Bps", overrides=[neg("^écriture.*")])
ts("Réseau par conteneur (réception ↑ / émission ↓)",
   [prom(f'sum by (service) (rate(mes_container_network_receive_bytes_total[{RI}]))', "reçu {{service}}"),
    prom(f'sum by (service) (rate(mes_container_network_transmit_bytes_total[{RI}]))', "émis {{service}}", ref="B")],
   "Bps", overrides=[neg("^émis.*")], w=8)
HEALTH = [{"type": "value", "options": {"1": {"text": "sain", "color": "green"}, "0.5": {"text": "démarrage", "color": "orange"},
                                        "0": {"text": "en échec", "color": "red"}, "-1": {"text": "—", "color": "text"}}}]
table("Conteneurs",
      [prom('max by (service, image, state) (mes_container_info)', instant=True, fmt="table"),
       prom(f'100 * sum by (service) (rate(mes_container_cpu_seconds_total[2m]))', instant=True, fmt="table", ref="B"),
       prom('sum by (service) (mes_container_memory_bytes)', instant=True, fmt="table", ref="C"),
       prom('time() - max by (service) (mes_container_start_time_seconds)', instant=True, fmt="table", ref="D"),
       prom('sum by (service) (mes_container_restarts_total)', instant=True, fmt="table", ref="E"),
       prom('min by (service) (mes_container_health)', instant=True, fmt="table", ref="F"),
       prom('sum by (service) (mes_container_pids)', instant=True, fmt="table", ref="G")],
      w=16, h=10,
      transformations=[{"id": "merge", "options": {}},
                       {"id": "organize", "options": {
                           "excludeByName": {"Time": True, "Value #A": True},
                           "indexByName": {"service": 0, "state": 1, "Value #F": 2, "Value #B": 3, "Value #C": 4,
                                           "Value #G": 5, "Value #D": 6, "Value #E": 7, "image": 8},
                           "renameByName": {"service": "Service", "state": "État", "image": "Image", "Value #B": "CPU",
                                            "Value #C": "Mémoire", "Value #D": "Démarré depuis",
                                            "Value #E": "Redémarrages", "Value #F": "Santé", "Value #G": "Processus"}}}],
      overrides=[col("CPU", "percent", gauge_max=100, decimals=1), col("Mémoire", "bytes"),
                 col("Démarré depuis", "dtdurations"), col("Redémarrages", decimals=0, thresholds=[(1, "orange"), (5, "red")]),
                 {"matcher": {"id": "byName", "options": "Santé"},
                  "properties": [{"id": "mappings", "value": HEALTH}, {"id": "custom.cellOptions", "value": {"type": "color-text"}}]},
                 col("Service", width=150), col("État", width=90)],
      sort=[{"displayName": "CPU", "desc": True}],
      desc="Tous les conteneurs de l'hôte. Un « Démarré depuis » court ou des redémarrages signalent un service instable.")

# =====================================================================================================
row("Processus de l'hôte")
P = 'groupname!=""'
table("Processus (regroupés par nom)",
      [prom(f'sum by (groupname) (namedprocess_namegroup_num_procs{{{P}}})', instant=True, fmt="table"),
       prom(f'100 * sum by (groupname) (rate(namedprocess_namegroup_cpu_seconds_total{{{P}}}[2m]))', instant=True, fmt="table", ref="B"),
       prom(f'sum by (groupname) (namedprocess_namegroup_memory_bytes{{memtype="resident",{P}}})', instant=True, fmt="table", ref="C"),
       prom(f'sum by (groupname) (namedprocess_namegroup_num_threads{{{P}}})', instant=True, fmt="table", ref="D"),
       prom(f'sum by (groupname) (rate(namedprocess_namegroup_read_bytes_total{{{P}}}[2m]))', instant=True, fmt="table", ref="E"),
       prom(f'sum by (groupname) (rate(namedprocess_namegroup_write_bytes_total{{{P}}}[2m]))', instant=True, fmt="table", ref="F"),
       prom(f'sum by (groupname) (namedprocess_namegroup_open_filedesc{{{P}}})', instant=True, fmt="table", ref="G")],
      w=24, h=10,
      transformations=merge_table({"groupname": "Processus", "Value #A": "Nombre", "Value #B": "CPU",
                                   "Value #C": "Mémoire", "Value #D": "Threads", "Value #E": "Lecture disque",
                                   "Value #F": "Écriture disque", "Value #G": "Fichiers ouverts"}),
      overrides=[col("CPU", "percent", decimals=1, thresholds=[(50, "orange"), (90, "red")]), col("Mémoire", "bytes"),
                 col("Lecture disque", "Bps"), col("Écriture disque", "Bps"), col("Processus", width=220)],
      sort=[{"displayName": "CPU", "desc": True}],
      desc="Tous les processus de l'hôte Docker, regroupés par nom de commande (postgres, python, nginx, grafana...).")
ts("CPU des 10 processus les plus actifs",
   [prom(f'topk(10, 100 * sum by (groupname) (rate(namedprocess_namegroup_cpu_seconds_total{{{P}}}[{RI}])))', "{{groupname}}")],
   "percent", w=12, h=8, fill=0)
ts("Mémoire des 10 processus les plus gros",
   [prom(f'topk(10, sum by (groupname) (namedprocess_namegroup_memory_bytes{{memtype="resident",{P}}}))', "{{groupname}}")],
   "bytes", w=12, h=8, fill=0)

# =====================================================================================================
row("API : requêtes HTTP et tâches de fond")
H = "mes_api_request_duration_seconds_bucket"
ts("Requêtes/s par route", [prom(f'sum by (route) (rate(mes_api_requests_total[{RI}]))', "{{route}}")],
   "reqps", stack=True, legend_table=True, legend_calcs=("mean", "max"))
ts("Latence API (toutes routes)",
   [prom(f'histogram_quantile(0.50, sum by (le) (rate({H}[{RI}])))', "p50"),
    prom(f'histogram_quantile(0.95, sum by (le) (rate({H}[{RI}])))', "p95", ref="B"),
    prom(f'histogram_quantile(0.99, sum by (le) (rate({H}[{RI}])))', "p99", ref="C")], "s", fill=0)
ts("Réponses en erreur",
   [prom(f'sum by (status) (rate(mes_api_requests_total{{status=~"5.."}}[{RI}]))', "{{status}}"),
    prom(f'sum by (status) (rate(mes_api_requests_total{{status=~"4.."}}[{RI}]))', "{{status}}", ref="B")],
   "reqps", stack=True, desc="4xx : requête refusée (droits, saisie) ; 5xx : erreur du serveur.",
   overrides=[{"matcher": {"id": "byRegexp", "options": "^5.*"},
               "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": "red"}}]}])
panels.append({
    "id": nid(), "type": "heatmap", "title": "Distribution des temps de réponse", "datasource": PROM,
    "gridPos": place(8, 8),
    "targets": [{**prom(f'sum by (le) (increase({H}[{RI}]))', "{{le}}", fmt="heatmap")}],
    "options": {"calculate": False, "yAxis": {"unit": "s"}, "cellGap": 1,
                "color": {"mode": "scheme", "scheme": "Oranges", "exponent": 0.5, "steps": 64},
                "tooltip": {"mode": "single", "yHistogram": True}, "legend": {"show": True},
                "rowsFrame": {"layout": "auto"}},
    "fieldConfig": {"defaults": {}, "overrides": []},
})
table("Routes : débit et latence (5 dernières minutes)",
      [prom('sum by (method, route) (rate(mes_api_requests_total[5m]))', instant=True, fmt="table"),
       prom(f'histogram_quantile(0.50, sum by (method, route, le) (rate({H}[5m])))', instant=True, fmt="table", ref="B"),
       prom(f'histogram_quantile(0.95, sum by (method, route, le) (rate({H}[5m])))', instant=True, fmt="table", ref="C"),
       prom('(sum by (method, route) (rate(mes_api_requests_total{status=~"5.."}[5m])) '
            'or 0 * sum by (method, route) (rate(mes_api_requests_total[5m]))) '
            '/ sum by (method, route) (rate(mes_api_requests_total[5m]))', instant=True, fmt="table", ref="D")],
      w=16, h=8,
      transformations=merge_table({"method": "Méthode", "route": "Route", "Value #A": "Req/s",
                                   "Value #B": "p50", "Value #C": "p95", "Value #D": "Erreurs 5xx"}),
      overrides=[col("Req/s", "reqps", decimals=2), col("p50", "s"),
                 col("p95", "s", thresholds=[(0.5, "orange"), (2, "red")]),
                 col("Erreurs 5xx", "percentunit", thresholds=[(0.01, "red")]), col("Méthode", width=90)],
      sort=[{"displayName": "p95", "desc": True}])
ts("Requêtes en cours et pool de connexions",
   [prom('mes_api_requests_in_progress', "requêtes en cours"),
    prom('mes_api_db_pool_size - mes_api_db_pool_idle', "connexions base occupées", ref="B"),
    prom('mes_api_db_pool_max', "pool max", ref="C")], "short", w=8, fill=0,
   desc="Si les connexions occupées atteignent « pool max », les requêtes attendent une connexion libre.")
ts("Tâches de fond : durée moyenne d'un cycle",
   [prom(f'sum by (task) (rate(mes_api_task_duration_seconds_sum[{RI}])) / sum by (task) (rate(mes_api_task_duration_seconds_count[{RI}]))', "{{task}}")],
   "s", w=8, fill=0, desc="evenements : détection arrêts/alarmes ; shifts : postes et KPI ; derives : moteurs ; purge.")
table("Tâches de fond",
      [prom('time() - mes_api_task_last_success_timestamp', instant=True, fmt="table"),
       prom('sum by (task) (increase(mes_api_task_errors_total[1h]))', instant=True, fmt="table", ref="B")],
      w=8, h=8,
      transformations=merge_table({"task": "Tâche", "Value #A": "Dernier succès il y a", "Value #B": "Erreurs (1 h)"},
                                  exclude=("Time", "__name__", "instance", "job")),
      overrides=[col("Dernier succès il y a", "s", decimals=0, thresholds=[(600, "orange"), (3600, "red")]),
                 col("Erreurs (1 h)", decimals=0, thresholds=[(1, "red")])])

# =====================================================================================================
row("Collecte OPC UA → historian")
UP = [{"type": "value", "options": {"0": {"text": "COUPÉ", "color": "red"}, "1": {"text": "OK", "color": "green"}}}]
stat("Session OPC UA", 'max(mes_collector_opcua_up)', mappings=UP, thresholds=[], graph=False)
panels[-1]["fieldConfig"]["defaults"]["thresholds"]["steps"] = [{"color": "red", "value": None}, {"color": "green", "value": 1}]
panels[-1]["options"]["colorMode"] = "background"
stat("Écriture base", 'max(mes_collector_db_up)', mappings=UP, thresholds=[], graph=False)
panels[-1]["fieldConfig"]["defaults"]["thresholds"]["steps"] = [{"color": "red", "value": None}, {"color": "green", "value": 1}]
panels[-1]["options"]["colorMode"] = "background"
stat("Tags surveillés", 'sum(mes_collector_tags)', graph=False)
stat("Abonnements", 'sum(mes_collector_subscriptions)', graph=False)
stat("File d'attente", 'sum(mes_collector_queue)', thresholds=[(5000, "orange"), (20000, "red")],
     desc="Lignes reçues pas encore écrites en base.")
stat("Tampon disque", 'sum(mes_collector_spool_files)', thresholds=[(1, "orange"), (50, "red")],
     desc="Lots mis de côté pendant une indisponibilité de la base ; rejoués automatiquement.")
stat("Retard d'écriture", 'time() - max(mes_collector_last_write_timestamp)', "s",
     thresholds=[(10, "orange"), (60, "red")], decimals=0, graph=False)
stat("Filtrage deadband", f'1 - sum(rate(mes_collector_rows_accepted_total[{RI}])) / sum(rate(mes_collector_notifications_total[{RI}]))',
     "percentunit", decimals=0, desc="Part des valeurs reçues écartées car inchangées (deadband).")
ts("Débit de collecte",
   [prom(f'sum(rate(mes_collector_notifications_total[{RI}]))', "reçues de l'automate"),
    prom(f'sum(rate(mes_collector_rows_accepted_total[{RI}]))', "retenues (deadband)", ref="B"),
    prom(f'sum(rate(mes_collector_rows_written_total[{RI}]))', "écrites en base", ref="C")], "short", fill=0,
   desc="Valeurs par seconde.")
ts("Latence d'écriture historian (COPY)",
   [prom(f'histogram_quantile(0.50, sum by (le) (rate(mes_collector_write_duration_seconds_bucket[{RI}])))', "p50"),
    prom(f'histogram_quantile(0.95, sum by (le) (rate(mes_collector_write_duration_seconds_bucket[{RI}])))', "p95", ref="B"),
    prom(f'histogram_quantile(0.99, sum by (le) (rate(mes_collector_write_duration_seconds_bucket[{RI}])))', "p99", ref="C")],
   "s", fill=0)
ts("Lots écrits",
   [prom(f'sum(rate(mes_collector_batch_rows_count[{RI}]))', "lots/s"),
    prom(f'sum(rate(mes_collector_batch_rows_sum[{RI}])) / sum(rate(mes_collector_batch_rows_count[{RI}]))', "lignes par lot", ref="B")],
   "short", fill=0, overrides=[{"matcher": {"id": "byName", "options": "lignes par lot"},
                                "properties": [{"id": "custom.axisPlacement", "value": "right"}]}])
ts("File d'attente et tampon disque",
   [prom('sum(mes_collector_queue)', "file d'attente (lignes)"),
    prom('sum(mes_collector_spool_files)', "tampon disque (lots)", ref="B")], "short", fill=0,
   overrides=[{"matcher": {"id": "byName", "options": "tampon disque (lots)"},
               "properties": [{"id": "custom.axisPlacement", "value": "right"}]}])
ts("Incidents de collecte",
   [prom(f'sum(increase(mes_collector_write_errors_total[{RI}]))', "écritures échouées"),
    prom(f'sum(increase(mes_collector_opcua_session_errors_total[{RI}]))', "coupures OPC UA", ref="B"),
    prom(f'sum(increase(mes_collector_rows_replayed_total[{RI}]))', "lignes rejouées du tampon", ref="C")],
   "short", fill=40, w=8)
ts("Ressources du collecteur",
   [prom(f'100 * sum(rate(mes_container_cpu_seconds_total{{service="collector"}}[{RI}]))', "CPU"),
    prom('sum(mes_container_memory_bytes{service="collector"})', "mémoire", ref="B")], "percent", fill=0, w=8,
   overrides=[{"matcher": {"id": "byName", "options": "mémoire"},
               "properties": [{"id": "unit", "value": "bytes"}, {"id": "custom.axisPlacement", "value": "right"}]}])

# =====================================================================================================
row("Base de données PostgreSQL / TimescaleDB")
DB = 'datname="mes"'
stat("Taille de la base", f'pg_database_size_bytes{{{DB}}}', "bytes", w=4)
stat("Connexions", f'sum(pg_stat_activity_count{{{DB}}})', w=4,
     desc="Toutes connexions à la base mes (actives et inactives).")
stat("Transactions/s", f'sum(rate(pg_stat_database_xact_commit{{{DB}}}[{RI}]) + rate(pg_stat_database_xact_rollback{{{DB}}}[{RI}]))', w=4, decimals=0)
stat("Cache hit", f'sum(rate(pg_stat_database_blks_hit{{{DB}}}[{RI}])) / clamp_min(sum(rate(pg_stat_database_blks_hit{{{DB}}}[{RI}]) + rate(pg_stat_database_blks_read{{{DB}}}[{RI}])), 1)',
     "percentunit", w=4, decimals=1, desc="Part des lectures servies par la mémoire (shared_buffers). Viser > 99 %.")
stat("Temps moyen d'une requête SQL", f'sum(rate(pg_stat_statements_seconds_total{{{DB}}}[{RI}])) / clamp_min(sum(rate(pg_stat_statements_calls_total{{{DB}}}[{RI}])), 1e-9)',
     "s", w=4, thresholds=[(0.1, "orange"), (1, "red")])
stat("Plus longue transaction", f'max(pg_stat_activity_max_tx_duration{{{DB}}})', "s", w=4,
     thresholds=[(60, "orange"), (600, "red")], decimals=0)
ts("Connexions par état", [prom(f'sum by (state) (pg_stat_activity_count{{{DB}}})', "{{state}}")], "short", stack=True)
ts("Transactions/s", [prom(f'rate(pg_stat_database_xact_commit{{{DB}}}[{RI}])', "commit"),
                      prom(f'rate(pg_stat_database_xact_rollback{{{DB}}}[{RI}])', "rollback", ref="B")], "short", fill=0)
ts("Lignes par seconde",
   [prom(f'rate(pg_stat_database_tup_inserted{{{DB}}}[{RI}])', "insérées"),
    prom(f'rate(pg_stat_database_tup_updated{{{DB}}}[{RI}])', "modifiées", ref="B"),
    prom(f'rate(pg_stat_database_tup_deleted{{{DB}}}[{RI}])', "supprimées", ref="C"),
    prom(f'rate(pg_stat_database_tup_fetched{{{DB}}}[{RI}])', "lues", ref="D")], "short", fill=0)
ts("Temps SQL par seconde (5 requêtes les plus coûteuses)",
   [prom(f'topk(5, sum by (queryid) (rate(pg_stat_statements_seconds_total{{{DB}}}[{RI}])))', "requête {{queryid}}")],
   "s", stack=True, desc="Secondes d'exécution cumulées par seconde. L'identifiant renvoie à la colonne ID du tableau ci-dessous.")
ts("IO de la base",
   [prom(f'rate(pg_stat_database_blks_read{{{DB}}}[{RI}]) * 8192', "lu sur disque"),
    prom(f'rate(pg_stat_database_temp_bytes{{{DB}}}[{RI}])', "fichiers temporaires", ref="B")], "Bps", fill=0,
   desc="Fichiers temporaires : tris ou jointures qui ne tiennent pas en mémoire (work_mem).")
ts("Verrous et blocages",
   [prom(f'sum by (mode) (pg_locks_count{{{DB}}}) > 0', "{{mode}}"),
    prom(f'increase(pg_stat_database_deadlocks{{{DB}}}[{RI}])', "deadlocks", ref="B")], "short", fill=0)
table("Requêtes SQL les plus coûteuses (depuis le démarrage de la base)",
      [sql("""SELECT s.queryid::text AS "ID", left(regexp_replace(s.query, '\\s+', ' ', 'g'), 160) AS "Requête",
       s.calls AS "Appels",
       round(s.mean_exec_time::numeric, 2) AS "Moyenne (ms)",
       round(s.max_exec_time::numeric, 1) AS "Max (ms)",
       round((s.total_exec_time / 1000)::numeric, 1) AS "Total (s)",
       round((100 * s.total_exec_time / sum(s.total_exec_time) OVER ())::numeric, 1) AS "Part (%)",
       s.rows AS "Lignes",
       round((100.0 * s.shared_blks_hit / nullif(s.shared_blks_hit + s.shared_blks_read, 0))::numeric, 1) AS "Cache (%)"
FROM public.pg_stat_statements s JOIN pg_database d ON d.oid = s.dbid
WHERE d.datname = 'mes' AND s.query NOT ILIKE '%pg_stat_statements%'
ORDER BY s.total_exec_time DESC LIMIT 25""")],
      ds=SQL, w=24, h=10,
      overrides=[col("Requête", width=560), col("ID", "string", width=170), col("Part (%)", gauge_max=100),
                 col("Moyenne (ms)", thresholds=[(100, "orange"), (1000, "red")])],
      desc="pg_stat_statements : temps cumulé par requête normalisée. Pour remettre à zéro : SELECT pg_stat_statements_reset();")
table("Requêtes en cours d'exécution",
      [sql("""SELECT pid AS "PID", usename AS "Utilisateur", coalesce(application_name, '') AS "Application",
       client_addr::text AS "Client", state AS "État",
       coalesce(wait_event_type || ':' || wait_event, '') AS "Attente",
       round(extract(epoch FROM now() - query_start)::numeric, 1) AS "Durée (s)",
       left(regexp_replace(query, '\\s+', ' ', 'g'), 200) AS "Requête"
FROM pg_stat_activity
WHERE datname = 'mes' AND pid <> pg_backend_pid() AND state <> 'idle' AND usename <> 'mes_reader'
ORDER BY query_start NULLS LAST""")],
      ds=SQL, w=16, h=8, overrides=[col("Requête", width=480), col("PID", width=60), col("Client", width=110),
                                    col("Application", width=110), col("État", width=110),
                                    col("Durée (s)", width=80, thresholds=[(5, "orange"), (60, "red")])],
      desc="Requêtes SQL en cours (hors Grafana). Vide quand la base est au repos.")
table("Tables les plus volumineuses",
      [sql("""SELECT hypertable_schema || '.' || hypertable_name AS "Table",
       hypertable_size(format('%I.%I', hypertable_schema, hypertable_name)::regclass) AS "Taille",
       num_chunks AS "Chunks", compression_enabled AS "Compression"
FROM timescaledb_information.hypertables
UNION ALL
SELECT schemaname || '.' || relname, pg_total_relation_size(relid), NULL, NULL
FROM pg_stat_user_tables
WHERE schemaname IN ('mes', 'catalog') AND pg_total_relation_size(relid) > 1048576
ORDER BY 2 DESC LIMIT 15""")],
      ds=SQL, w=8, h=8, overrides=[col("Taille", "bytes")])

# =====================================================================================================
row("Prometheus et cibles surveillées", collapsed=False)
table("État des cibles",
      [prom('max by (job, instance) (up)', instant=True, fmt="table"),
       prom('max by (job, instance) (scrape_duration_seconds)', instant=True, fmt="table", ref="B"),
       prom('max by (job, instance) (scrape_samples_scraped)', instant=True, fmt="table", ref="C")],
      w=12, h=10,
      transformations=merge_table({"job": "Cible", "instance": "Adresse", "Value #A": "État",
                                   "Value #B": "Durée de collecte", "Value #C": "Séries"},
                                  exclude=("Time",)),
      overrides=[{"matcher": {"id": "byName", "options": "État"},
                  "properties": [{"id": "mappings", "value": UP},
                                 {"id": "custom.cellOptions", "value": {"type": "color-background"}}]},
                 col("Durée de collecte", "s")])
ts("Prometheus : séries actives et volume",
   [prom('prometheus_tsdb_head_series', "séries actives"),
    prom('sum(prometheus_tsdb_storage_blocks_bytes) + sum(prometheus_tsdb_wal_storage_size_bytes)', "volume disque", ref="B")],
   "short", w=12, fill=0, overrides=[{"matcher": {"id": "byName", "options": "volume disque"},
                                      "properties": [{"id": "unit", "value": "bytes"}, {"id": "custom.axisPlacement", "value": "right"}]}])

dashboard = {
    "uid": "mes-systeme",
    "title": "Système - supervision technique",
    "description": "Tableau de bord de développement et d'exploitation : hôte, conteneurs, processus, API, collecte OPC UA, base de données. "
                   "Nécessite le profil monitoring (./mesctl monitoring on).",
    "tags": ["mes-v3", "système"],
    "schemaVersion": 39, "version": 1, "editable": False, "graphTooltip": 1,
    "refresh": "10s", "time": {"from": "now-1h", "to": "now"}, "timezone": "browser",
    "timepicker": {"refresh_intervals": ["10s", "30s", "1m", "5m"]},
    "links": [{"title": "Collecte - santé", "type": "link", "url": "/grafana/d/mes-collecte", "icon": "dashboard"}],
    "panels": panels, "templating": {"list": []}, "annotations": {"list": []},
}
OUT.write_text(json.dumps(dashboard, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(f"{OUT} : {len(panels)} panneaux")
