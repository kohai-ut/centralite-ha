"""Device triggers for physical Centralite button presses.

TODO(v2.x): implement HA device triggers (async_get_triggers /
async_attach_trigger) so automations can fire on physical switch
press/release/tap events. The protocol already surfaces these via
SwitchEvent in the coordinator (_on_switch_event); this module would expose
them as device-automation triggers. Empty for now — HA discovers no triggers
until the required functions exist.
"""
