#!/usr/bin/env python3
"""Replay piomar's local customizations onto an upstream HeishaMoNR flows.json.

Usage: replay_custom.py <upstream flows.json> <live flows.json> <output flows.json>

The live flow is only used to copy the dashboard theme (ui_base) and the
nodes that were added locally. Every change is anchored on upstream node IDs;
the script fails loudly if an anchor is missing, so a future upstream release
that moves things around gets noticed instead of silently half-applied.
New nodes go into a "piomar custom" group at the bottom of their tab.
"""
import json
import secrets
import sys

up_path, live_path, out_path = sys.argv[1:4]
flows = json.load(open(up_path))
live = {n["id"]: n for n in json.load(open(live_path))}
N = {n["id"]: n for n in flows}

BROKER = "92e5b2c.00a8b5"
TAB_DASH, TAB_MANAGERS, TAB_DEBUG = "8c9d42231fd19d3c", "43c4ee3ee4757f3c", "d5fea67661261002"
custom = {}  # tab id -> list of new node ids


def get(i):
    if i not in N:
        sys.exit(f"anchor node {i} missing in upstream flow")
    return N[i]


def new_id():
    while True:
        i = secrets.token_hex(8)
        if i not in N:
            return i


def add(tab, node):
    node.setdefault("id", new_id())
    node["z"] = tab
    flows.append(node)
    N[node["id"]] = node
    custom.setdefault(tab, []).append(node["id"])
    return node["id"]


def mqtt_in(tab, topic, wires, name=""):
    return add(tab, {"type": "mqtt in", "name": name, "topic": topic, "qos": "0",
                     "datatype": "auto-detect", "broker": BROKER, "nl": False, "rap": True,
                     "rh": 0, "inputs": 0, "wires": [wires]})


def ui_insert_after(group, after_label, node):
    """Insert a dashboard widget after the widget labelled after_label (or at the end)."""
    members = sorted((n for n in flows if n.get("group") == group), key=lambda n: n.get("order", 0))
    pos = len(members)
    if after_label:
        idx = [k for k, m in enumerate(members) if (m.get("label") or m.get("name")) == after_label]
        if not idx:
            sys.exit(f"widget {after_label!r} not found in ui_group {group}")
        pos = idx[0] + 1
    for k, m in enumerate(members):
        m["order"] = k + 1 if k < pos else k + 2
    node["group"] = group
    node["order"] = pos + 1
    return node


def gauge(name, maxw):
    return {"type": "ui_gauge", "name": name, "width": "3", "height": "3", "gtype": "gage",
            "title": name, "label": "W", "format": "{{value}}", "min": 0, "max": maxw,
            "colors": ["#00b500", "#e6e600", "#ca3838"], "seg1": "", "seg2": "", "diff": False,
            "className": "", "wires": []}


def ui_text(label):
    return {"type": "ui_text", "width": 6, "height": 1, "name": label, "label": label,
            "format": "{{msg.payload}} °C", "layout": "row-spread", "className": "",
            "style": False, "font": "", "fontSize": "", "color": "#000000", "wires": []}


# 1. MQTT broker: local mosquitto on the Pi (credentials stay in flows_cred.json, same node id)
get(BROKER).update(name="raspberry", broker="127.0.0.1")

# 2. Stop Node-RED from sending HEAT setpoints (SetZ1/Z2HeatRequestTemperature).
#    Locally the SP manager spammed identical commands; upstream now de-duplicates in
#    "Block?", but keep the old behaviour (nothing sent) until tested deliberately.
#    The third output (SetCurves) carries the shift in Compensation Curve mode, which is the
#    mode this heat pump runs in; 24.03 sent that shift as SetZ1HeatRequestTemperature.
for block, comment in (("1b409e1ea72f94dd", "z1"), ("64dcf16c979d2a3a", "z2"),
                       ("d62a7aa18d066598", "SetCurves")):
    b = get(block)
    target = b["wires"][0]
    pb = add(TAB_MANAGERS, {"type": "function", "name": f"Piomar Block ({comment})",
                            "func": "// disable SP manager: don't send heat setpoints\nreturn null;",
                            "outputs": 1, "timeout": 0, "noerr": 0, "initialize": "",
                            "finalize": "", "libs": [], "wires": [target]})
    b["wires"][0] = [pb]

# 2b. Safety net in front of the only command publisher ("Dynamic MQTT out"): drop heat
#     setpoint and curve commands whatever path they come from (SoftStart, RTC, retries,
#     future upstream paths).
SETPOINT_FILTER = r"/commands/(SetZ[12]HeatRequestTemperature|SetCurves)$"
funnel = [n for n in flows if n["type"] == "function" and n.get("wires")
          and any(N.get(w, {}).get("type") == "mqtt out" and N[w].get("name") == "Dynamic MQTT out"
                  for w in n["wires"][0])]
if len(funnel) != 1:
    sys.exit(f"expected one node feeding 'Dynamic MQTT out', found {len(funnel)}")
gate = add(TAB_MANAGERS, {"type": "function", "name": "Piomar setpoint filter",
                          "func": ("// piomar: Node-RED must not change heat setpoints or curves\n"
                                   f"if (/{SETPOINT_FILTER.replace('/', chr(92) + '/')}/.test(msg.topic)) {{\n"
                                   "    const n = (context.get('dropped') || 0) + 1;\n"
                                   "    context.set('dropped', n);\n"
                                   "    node.status({fill: 'red', shape: 'dot', text: `dropped ${n}: ${msg.topic.split('/').pop()}`});\n"
                                   "    return null;\n"
                                   "}\n"
                                   "return msg;"),
                          "outputs": 1, "timeout": 0, "noerr": 0, "initialize": "",
                          "finalize": "", "libs": [], "wires": [funnel[0]["wires"][0]]})
funnel[0]["wires"][0] = [gate]

# 3. Force DHW / Force sterilization switches disabled (Home + Settings)
for i in ("c6559a95bb3c55fb", "50bbeaa9a0ad9305", "50b87b6e19d49fae", "ccb9e79ff06190fa"):
    get(i)["d"] = True

# 4. Drop GoHeishaMon's panasonic_heat_pump/commands/LWT before it reaches the command
#    log and the daily MQTT-command counter (overflow protection)
cmd_in = get("ae6175191a94d7ec")
lwt = add(TAB_DASH, {"type": "switch", "name": "drop LWT", "property": "topic",
                     "propertyType": "msg",
                     "rules": [{"t": "neq", "v": "panasonic_heat_pump/commands/LWT", "vt": "str"}],
                     "checkall": "true", "repair": False, "outputs": 1,
                     "wires": [cmd_in["wires"][0]]})
cmd_in["wires"] = [[lwt]]

# 5. Logs: heishamon Status changes and internal heater on/off
def rbe(wires):
    return add(TAB_DASH, {"type": "rbe", "name": "", "func": "rbe", "gap": "", "start": "",
                          "inout": "out", "septopics": False, "property": "payload",
                          "topi": "topic", "wires": [wires]})


def retopic(src, dst, wires):
    return add(TAB_DASH, {"type": "change", "name": "", "action": "", "property": "",
                          "from": "", "to": "", "reg": False, "wires": [wires],
                          "rules": [{"t": "change", "p": "topic", "pt": "msg", "from": src,
                                     "fromt": "str", "to": dst, "tot": "str"}]})


get("7ec2785759270a3f")  # LWT->log delay in the Logs group
t = "panasonic_heat_pump/main/Status"
mqtt_in(TAB_DASH, t, [rbe([retopic(t, "SYSTEM Status", ["7ec2785759270a3f"])])])
get("031e62b0bf3df7d7")  # link in "Input LOGS"
heater_out = add(TAB_DASH, {"type": "link out", "name": "Heater working to logs", "mode": "link",
                            "links": ["031e62b0bf3df7d7"], "wires": []})
get("031e62b0bf3df7d7")["links"].append(heater_out)
t = "panasonic_heat_pump/main/Internal_Heater_State"
mqtt_in(TAB_DASH, t, [rbe([retopic(t, "Heater working", [heater_out])])])

# 6. Home dashboard: power consumption / production gauges for HEAT and DHW
for grp, linkout, switch, cmax in (("1179fa790d2d89ca", "9de1bde370b68508", "e7d39d4073a87d3c", "4000"),
                                   ("e3cc96332cd59f07", "a3180920b08e8108", "14789bda2966231c", "4000")):
    cons = add(TAB_DASH, ui_insert_after(grp, None, gauge("Energy consumption", cmax)))
    prod = add(TAB_DASH, ui_insert_after(grp, None, gauge("Energy production", "12000")))
    li = add(TAB_DASH, {"type": "link in", "name": get(linkout)["name"] + " (gauge)",
                        "links": [linkout], "wires": [[cons]]})
    get(linkout)["links"].append(li)
    get(switch)["wires"][0].append(prod)

# 7. Home dashboard, HEAT PUMP group: zone 2 water setpoint and compressor discharge temp
pump = "bf4af523ba16d457"
z2 = add(TAB_DASH, ui_insert_after(pump, "Outlet setpoint", ui_text("Z2 setpoint")))
mqtt_in(TAB_DASH, "panasonic_heat_pump/main/Z2_Water_Target_Temp", [z2], "Z2_Water_Target_Temp")
dis = add(TAB_DASH, ui_insert_after(pump, "Inlet actual", ui_text("Discharge temp")))
mqtt_in(TAB_DASH, "panasonic_heat_pump/main/Discharge_Temp", [dis], "Discharge_Temp")

# 8. Small dashboard fixes
get("719e11414721599e")["format"] = "{{msg.payload}}"  # Heat status
get("4f19cd39ac4c4be0")["format"] = "{{msg.payload}}"  # DHW status
get("0d58a1e854263ec4")["ymin"] = "-20"                # CCC time chart (cold winters)
get("9802bf01ebf45d28")["nodata"] = "no data"          # Water HEAT chart

# 9. Upstream bug: the WAR -> CCC context migration only runs when F_CCC is undefined, but
#    the "Load on boot" nodes create F_CCC.z1.state first, so the heating curve (war_ot*/wt*)
#    is never copied. Migrate whenever old values exist and the new ones don't.
rep = get("5ce82b31e85cfe3a")
old = "if (global.get('F_CCC', \"file\") === undefined) { \n    global.set('F_CCC',{},\"file\"); // create the empty variable...."
if old not in rep["func"]:
    sys.exit("CCC migration block changed upstream, re-check fix 9")
rep["func"] = rep["func"].replace(old,
    "if (global.get('war_ot1', \"file\") !== undefined && global.get('F_CCC.z1.ot1', \"file\") === undefined) { // piomar: was F_CCC === undefined (raced with Load on boot)\n    if (global.get('F_CCC', \"file\") === undefined) { global.set('F_CCC',{},\"file\"); }")

# 11. Upstream bug: the active "T_Zone_2 Input Selector" takes TOP118 Second_Room_Thermostat_Temp as
#     the zone 2 room temperature even when it is -128 (no second room thermostat; GoHeishaMon
#     publishes TOP118 since 2026-10-14). The older, disabled selector ignored values below -100.
sel = get("f3d5c028ec98d42f")
old = "    if (isNaN(defaultTemp)) return null; // Ignore invalid input\n"
if old not in sel["func"]:
    sys.exit("zone 2 input selector changed upstream, re-check fix 11")
sel["func"] = sel["func"].replace(old, old +
    "    if (defaultTemp < -100) return null; // piomar: -128 = no second room thermostat\n")

# 12. Upstream bug: "Import from Panasonic" (CCC Zone 1/2 import/export) still writes the pre-CCC
#     names war_ot1..war_wt3, which nothing reads; the CCC function, chart and widgets use
#     F_CCC.z1.* / F_CCC.z2.*. Write those, and reload the CCC widgets like "Store Zn CCC" does.
for confirm, zone, reload in (("418e761f03c6c9cd", "z1", "123d7b6352639620"),
                              ("30ebb223f4c6626c", "z2", "285dd5263c5648c2")):
    node = get(confirm)
    if node["func"].count("global.set('war_") != 6:
        sys.exit(f"CCC {zone} import changed upstream, re-check fix 12")
    node["func"] = node["func"].replace("global.set('war_", f"global.set('F_CCC.{zone}.")
    # the curve values arrive by MQTT; right after a restart they can still be missing
    first_set = node["func"].index(f"        global.set('F_CCC.{zone}.ot1'")
    Z = zone.upper()
    node["func"] = (node["func"][:first_set] +
        f"        if ([{Z}_Heat_Curve_Outside_Low_Temp, {Z}_Heat_Curve_Outside_High_Temp, {Z}_Heat_Curve_Target_Low_Temp, "
        f"{Z}_Heat_Curve_Target_High_Temp].some(v => v === undefined)) {{ // piomar\n"
        "            msg.payload = \"No curve from the heat pump yet, try again in 5 minutes\";\n"
        "            return msg;\n"
        "        }\n" + node["func"][first_set:])
    get(reload)
    node["wires"][0].append(reload)

# 10. Dashboard theme (ui_base is not part of upstream exports)
flows.append(live["9edb394e313b9143"])

# Lay out the new nodes in a "piomar custom" group at the bottom of each tab.
for tab, ids in custom.items():
    bottom = max(n.get("y", 0) + n.get("h", 0) for n in flows if n.get("z") == tab and n["id"] not in ids)
    gid = new_id()
    top = bottom + 100
    for k, i in enumerate(ids):
        N[i].update(x=200 + 260 * (k % 4), y=top + 40 + 60 * (k // 4), g=gid)
    rows = (len(ids) + 3) // 4
    flows.append({"id": gid, "type": "group", "z": tab, "name": "piomar custom",
                  "style": {"label": True}, "nodes": ids,
                  "x": 34, "y": top, "w": 1092, "h": 40 + 60 * rows + 20})

json.dump(flows, open(out_path, "w"), indent=4, ensure_ascii=False)
print(f"wrote {out_path}: {len(flows)} nodes, added {sum(map(len, custom.values()))} custom nodes")
