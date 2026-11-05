# Copyright 2026 FSMW
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

"""Non-serializable capability for validated native invoice inverse writes."""

_NATIVE_POS_BINDING = object()


def is_native_binding(env):
    return env.context.get("_dte_pos_native_binding") is _NATIVE_POS_BINDING
