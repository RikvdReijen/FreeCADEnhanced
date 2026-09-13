# SPDX-License-Identifier: LGPL-2.1-or-later
"""Voice as a modelling input.

Hands are busy in VR; voice is the free channel. "Fillet these edges, two
millimetres" while the hands hold the geometry.

::

    numbers.py   spoken numbers and units -> values
    grammar.py   the command vocabulary -> typed intents
    dispatch.py  intents -> actions on the selection / the XR bridges
    backends.py  where transcripts come from (typed, Vosk offline, headset)

:class:`VoiceSession` ties them together: ``poll()`` once per frame drains
the backend, parses, dispatches, and records what happened for the HUD.
"""

from .numbers import Quantity, find_quantity, parse_number, parse_quantity, tokenize
from .grammar import COMMANDS, Command, Intent, Parse, help_text, parse
from .dispatch import ActionResult, Context, Dispatcher, default_handlers
from .backends import Backend, RemoteBackend, TextBackend, Transcript, VoskBackend, best_backend, vocabulary_words


class VoiceSession(object):
    """Backend + grammar + dispatcher, with a log the HUD can show."""

    def __init__(self, backend=None, dispatcher=None, context_factory=None, confidence_threshold=0.5):
        self.backend = backend or TextBackend()
        self.dispatcher = dispatcher or Dispatcher(default_handlers(), confidence_threshold)
        self.context_factory = context_factory or Context
        self.history = []
        self.events = []
        self.partial = ""

    @property
    def listening(self):
        return self.backend.listening

    def start(self):
        self.backend.start()

    def stop(self):
        self.backend.stop()

    def say(self, text, confidence=1.0):
        """Feed an utterance directly (typed, or from the headset)."""
        self.backend.push(Transcript(text, confidence, True, self.backend.name))
        return self.poll()

    def poll(self):
        """Process pending transcripts. Returns the results of final ones."""
        results = []
        for transcript in self.backend.poll():
            if not transcript.final:
                self.partial = transcript.text
                continue
            self.partial = ""
            parsed = parse(transcript.text, transcript.confidence)
            context = self.context_factory()
            result = self.dispatcher.handle(parsed.intent, context)
            if parsed.intent is None:
                hint = (" — did you mean: " + ", ".join(parsed.alternatives)) if parsed.alternatives else ""
                result.message = "didn't understand %r%s" % (transcript.text, hint)
                self.events.append(_VoiceEvent("misheard", transcript.text))
            else:
                self.events.append(_VoiceEvent("heard" if result.ok else "misheard", transcript.text))
                if result.ok:
                    self.events.append(_VoiceEvent("done", result.message))
            self.history.append((transcript, parsed, result))
            if len(self.history) > 50:
                del self.history[:-50]
            results.append(result)
        return results

    def drain_events(self):
        events, self.events = self.events, []
        return events


class _VoiceEvent(object):
    __slots__ = ("kind", "text")

    def __init__(self, kind, text):
        self.kind = kind
        self.text = text

    def __repr__(self):
        return "VoiceEvent(%s %r)" % (self.kind, self.text)


__all__ = ["Quantity", "find_quantity", "parse_number", "parse_quantity", "tokenize", "COMMANDS", "Command", "Intent",
           "Parse", "help_text", "parse", "ActionResult", "Context", "Dispatcher", "default_handlers", "Backend",
           "RemoteBackend", "TextBackend", "Transcript", "VoskBackend", "best_backend", "vocabulary_words", "VoiceSession"]
