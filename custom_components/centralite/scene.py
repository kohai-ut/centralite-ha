"""Stateless scene platform for the Centralite integration (optional).

Most scenes are exposed as switch entities (see switch.py). This module is
reserved for any scenes the user prefers as stateless one-shot triggers.

TODO(v2.x): implement a `scene` platform that fires `coordinator.activate_scene`
as a stateless Scene entity, for users who want fire-and-forget scenes rather
than the stateful switch in switch.py. Not wired into PLATFORMS yet.
"""
