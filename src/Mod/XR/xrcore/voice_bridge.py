# SPDX-License-Identifier: LGPL-2.1-or-later
"""Voice input on the desktop viewer.

Builds the :class:`xrvoice.VoiceSession` with the best backend the machine
has (Vosk with the model directory from the preferences, else typed text),
and gives the dispatcher a context assembled from the GUI selection, the
active document and the live feature sessions. ``poll()`` runs once per
frame; results are printed to the report view and felt through the
haptics ("heard" tick / "misheard" buzz). Transcripts that arrive from a
headset through the sync server (``POST /api/v1/voice``) are fed into the
same session by :func:`remote_sink`.
"""

import FreeCAD

from xrcore import docmesh, service

__all__ = ["get_session", "ensure_session", "start", "stop", "toggle", "listening", "say", "poll", "remote_sink",
           "attach", "detach", "status_text"]


def _context():
    from xrvoice import Context

    doc = FreeCAD.ActiveDocument
    return Context(selection=docmesh.selected_objects(doc), document=doc, viewer=service.get_widget(),
                   sessions=service.features(), environment=service.get_environment_id())


def get_session():
    return service.get_feature("voice")


def ensure_session():
    session = get_session()
    if session is not None:
        return session
    from xrvoice import VoiceSession, best_backend, vocabulary_words

    prefs = service.preferences()
    model = prefs.GetString("VoiceModelPath", "")
    backend = best_backend(model or None, vocabulary_words())
    if backend.name != "vosk":
        reason = getattr(backend, "unavailable_reason", "") or "no offline recogniser"
        FreeCAD.Console.PrintMessage(
            "XR: voice — offline recognition unavailable (%s); typed commands and headset transcripts still work\n"
            % (reason or "Vosk not configured"))
    session = VoiceSession(backend, context_factory=_context, confidence_threshold=prefs.GetFloat("VoiceConfidence", 0.5))
    service.set_feature("voice", session)
    server = service.sync_server()
    if server is not None:
        server.voice_sink = remote_sink
    return session


def attach(widget=None):
    return ensure_session()


def detach():
    session = get_session()
    if session is not None:
        session.stop()


def start():
    session = ensure_session()
    session.start()
    FreeCAD.Console.PrintMessage("XR: voice — listening (%s)\n" % session.backend.name)
    return session.listening


def stop():
    session = get_session()
    if session is not None:
        session.stop()
    return False


def toggle():
    session = ensure_session()
    return stop() if session.listening else start()


def listening():
    session = get_session()
    return bool(session and session.listening)


def say(text, confidence=1.0):
    """Feed typed text (the desktop fallback and the console)."""
    results = ensure_session().say(text, confidence)
    _report(results)
    return results


def remote_sink(payload, peer_id):
    """A transcript from a headset (registered as the sync server's voice sink)."""
    from xrvoice import Transcript

    session = ensure_session()
    session.backend.push(Transcript.from_dict(dict(payload, source="peer:%s" % peer_id)))
    return "queued"


def poll():
    session = get_session()
    if session is None:
        return []
    results = session.poll()
    _report(results)
    try:
        from xrcore import haptics_bridge
        from xrhaptics import VOICE_EVENTS

        haptics_bridge.feed(session.drain_events(), VOICE_EVENTS)
    except Exception:
        session.drain_events()
    return results


def _report(results):
    for result in results:
        if result.ok:
            FreeCAD.Console.PrintMessage("XR voice: %s\n" % result.message)
        else:
            FreeCAD.Console.PrintWarning("XR voice: %s\n" % result.message)


def status_text():
    session = get_session()
    if session is None:
        return ""
    if session.partial:
        return "voice: …%s" % session.partial[-24:]
    return "voice: on" if session.listening else "voice: off"
