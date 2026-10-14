# piomar customizations

Local changes to HeishaMoNR, replayed onto the upstream release by `replay_custom.py`
instead of being edited into the flow by hand. Live instance: Node-RED 4.0.8 on
`raspberry4.local` (projects mode, project `smart-home`), talking to GoHeishaMon (pando85 app)
through the local mosquitto.

```bash
python3 piomar/replay_custom.py "flows (26.5.1 stable).json" <live flows.json> "piomar/flows (26.5.1 stable + piomar).json"
```

The live flow is only read for the dashboard theme (`ui_base`). The script stops if an
upstream anchor node is missing; on a new release, check the anchors it complains about.

| # | Change | Why |
|---|--------|-----|
| 1 | MQTT broker `127.0.0.1` | mosquitto on the Pi |
| 2 | "Piomar Block" after all three SP manager `Block?` nodes (z1, z2, SetCurves) | Node-RED never sends `SetZ1/Z2HeatRequestTemperature` or `SetCurves` (it used to spam identical commands; upstream de-duplicates since 25.x, untested here). In Compensation Curve mode (this heat pump) 26.x sends the SoftStart/RTC shift as `SetCurves` |
| 2b | "Piomar setpoint filter" in front of "Dynamic MQTT out", the only command publisher | safety net: drops those three topics from any path; its node status counts the drops |
| 3 | Force DHW / Force sterilization switches disabled | not used from this dashboard |
| 4 | `drop LWT` switch on `commands/#` | GoHeishaMon publishes `commands/LWT`; it showed up in the command log and the daily MQTT command counter |
| 5 | heishamon `Status` and `Internal_Heater_State` changes in the Logs | |
| 6 | Power consumption / production gauges in Home HEAT and DHW | |
| 7 | `Z2 setpoint` and `Discharge temp` in Home HEAT PUMP | |
| 8 | Heat/DHW status text format, CCC chart min -20 °C, "no data" on Water HEAT | |
| 9 | Fix of the WAR -> CCC context migration | upstream only migrates when `F_CCC` is undefined, but the "Load on boot" nodes create it first, so the curve (`war_ot*/wt*`) was dropped (tested with a copy of the Pi context) |
| 10 | Dark theme `ui_base` | not part of upstream exports |
| 11 | "T_Zone_2 Input Selector" ignores TOP118 below -100 | -128 means no second room thermostat; upstream 26.5.1 used it as the zone 2 room temperature (the older selector had this check) |
| 12 | "Import from Panasonic" writes `F_CCC.z1/z2.*` and reloads the CCC widgets | upstream still wrote the pre-CCC `war_*` names, so the import changed nothing |

Already upstream (dropped from the replay): `gte 0` on the Chart Energy switches, powerful mode
logged when `> 0`, wider charts. Dropped as debug leftovers: `node.warn` in "Check operation
mode" and "set.TOP17_Powerful_Mode_Time".
