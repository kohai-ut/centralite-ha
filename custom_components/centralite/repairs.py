"""Repairs issues for the Centralite integration.

The v1->v2 migration issue is raised directly in migrate.py via
async_create_issue with is_fixable=False, so it needs no RepairsFlow here.

TODO(v2.x): if any future issue is made fixable (is_fixable=True), implement
async_create_fix_flow in this module to handle the repair UI flow.
"""
