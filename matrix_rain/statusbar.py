"""The "Matrix rain camera" status bar component, shared by the daemon and the
profile.  iTerm2 stores a script component in a profile's Status Bar Layout as
the base64 of its registration request, so the profile's entry is generated
from this definition (matrix-rain status-component) and selftest checks that
the committed profile still matches it."""

from __future__ import annotations

import base64

IDENTIFIER = "us.epel.iterm2-matrix-rain.camera"
RPC_NAME = "matrix_rain_camera_status"
RPC_ARGS = ("knobs", "camera")
RPC_DEFAULTS = {"camera": "user.matrixRainCamera?"}
CLASS = "iTermStatusBarRPCProvidedTextComponent"
REQUEST_KEY = "registration request v2"


def build_component():
    import iterm2

    return iterm2.StatusBarComponent(
        short_description="Matrix rain camera",
        detailed_description="Shows whether iterm2-matrix-rain's camera motion tint is on.  Click to turn it on or off.",
        knobs=[],
        exemplar="◉ camera on",
        update_cadence=None,
        identifier=IDENTIFIER,
    )


def registration_b64() -> str:
    """The registration request iTerm2 saves for this component, base64-encoded,
    built the same way iterm2.notifications builds it when the daemon registers."""
    from iterm2 import api_pb2
    from iterm2.registration import RPC_ROLE_STATUS_BAR_COMPONENT

    req = api_pb2.RPCRegistrationRequest()
    req.name = RPC_NAME
    for name in RPC_ARGS:
        arg = api_pb2.RPCRegistrationRequest.RPCArgumentSignature()
        arg.name = name
        req.arguments.append(arg)
    req.role = RPC_ROLE_STATUS_BAR_COMPONENT
    for name, path in RPC_DEFAULTS.items():
        d = api_pb2.RPCRegistrationRequest.RPCArgument()
        d.name, d.path = name, path
        req.defaults.append(d)
    build_component().set_fields_in_proto(req.status_bar_component_attributes)
    return base64.b64encode(req.SerializeToString(deterministic=True)).decode()


def layout_entry(text_color: dict | None = None) -> dict:
    knobs: dict = {"base: priority": 5, "base: compression resistance": 1}
    if text_color:
        knobs["shared text color"] = text_color
    return {"class": CLASS, "configuration": {REQUEST_KEY: registration_b64(), "knobs": knobs}}
