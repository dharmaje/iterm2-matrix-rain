"""The "Matrix rain" and "Matrix rain camera" status bar components, shared by
the daemon and the profile.  iTerm2 stores a script component in a profile's
Status Bar Layout as the base64 of its registration request, so the profile's
entries are generated from these definitions (matrix-rain status-component)
and selftest checks that the committed profile still matches them."""

from __future__ import annotations

import base64

CLASS = "iTermStatusBarRPCProvidedTextComponent"
REQUEST_KEY = "registration request v2"


class Spec:
    """One clickable component.  `variable` is the user variable the daemon sets
    to make iTerm2 re-evaluate the text; `arg` is the RPC argument bound to it."""

    def __init__(self, identifier, rpc_name, arg, variable, short, detailed, exemplar):
        self.identifier = identifier
        self.rpc_name = rpc_name
        self.rpc_args = ("knobs", arg)
        self.variable = variable
        self.short, self.detailed, self.exemplar = short, detailed, exemplar


CAMERA = Spec("us.epel.iterm2-matrix-rain.camera", "matrix_rain_camera_status", "camera", "user.matrixRainCamera",
              "Matrix rain camera",
              "Shows whether iterm2-matrix-rain's camera motion tint is on.  Click to turn it on or off.",
              "◉ camera on")
RAIN = Spec("us.epel.iterm2-matrix-rain.rain", "matrix_rain_pause_status", "paused", "user.matrixRainPaused",
            "Matrix rain",
            "Shows whether iterm2-matrix-rain is animating or paused.  Click to pause or resume the rain.",
            "▶ rain on")
SPECS = (RAIN, CAMERA)

# Kept for callers that predate the pause toggle.
IDENTIFIER = CAMERA.identifier
RPC_NAME = CAMERA.rpc_name


def build_component(spec: Spec = CAMERA):
    import iterm2

    return iterm2.StatusBarComponent(
        short_description=spec.short,
        detailed_description=spec.detailed,
        knobs=[],
        exemplar=spec.exemplar,
        update_cadence=None,
        identifier=spec.identifier,
    )


def registration_b64(spec: Spec = CAMERA) -> str:
    """The registration request iTerm2 saves for this component, base64-encoded,
    built the same way iterm2.notifications builds it when the daemon registers."""
    from iterm2 import api_pb2
    from iterm2.registration import RPC_ROLE_STATUS_BAR_COMPONENT

    req = api_pb2.RPCRegistrationRequest()
    req.name = spec.rpc_name
    for name in spec.rpc_args:
        arg = api_pb2.RPCRegistrationRequest.RPCArgumentSignature()
        arg.name = name
        req.arguments.append(arg)
    req.role = RPC_ROLE_STATUS_BAR_COMPONENT
    d = api_pb2.RPCRegistrationRequest.RPCArgument()
    d.name, d.path = spec.rpc_args[1], spec.variable + "?"
    req.defaults.append(d)
    build_component(spec).set_fields_in_proto(req.status_bar_component_attributes)
    return base64.b64encode(req.SerializeToString(deterministic=True)).decode()


def layout_entry(text_color: dict | None = None, spec: Spec = CAMERA) -> dict:
    knobs: dict = {"base: priority": 5, "base: compression resistance": 1}
    if text_color:
        knobs["shared text color"] = text_color
    return {"class": CLASS, "configuration": {REQUEST_KEY: registration_b64(spec), "knobs": knobs}}
