# Copyright 2026 FSMW
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

# Object identity cannot be supplied by a serialized RPC context.
_BINDING_CAPABILITY = object()
_NATIVE_MOVE_GUARD = object()


def trusted(env):
    return env.context.get("_incoming_account_capability") is _BINDING_CAPABILITY


def internal_context():
    return {"_incoming_account_capability": _BINDING_CAPABILITY}


def native_guard(env):
    return env.context.get("_incoming_account_native_guard") is _NATIVE_MOVE_GUARD


def native_context():
    return {"_incoming_account_native_guard": _NATIVE_MOVE_GUARD}
