# SPDX-License-Identifier: LGPL-2.1-or-later
"""Where transcripts come from.

* :class:`TextBackend` — typed text; the desktop fallback and the test seam.
* :class:`VoskBackend` — offline recognition with `vosk <https://alphacephei.com/vosk>`_
  and ``sounddevice``, when both are installed and a model directory is set.
  The vocabulary is passed as a grammar so the recogniser only chooses among
  words the commands use, which is what makes "fillet two millimetres" come
  out right instead of "fill it to mill a meters".
* :class:`RemoteBackend` — transcripts pushed from the headset over the
  sync protocol (``POST /api/v1/voice``); the Quest side uses Android's
  ``SpeechRecognizer``.

All expose ``available``, ``start()``, ``stop()`` and ``poll() -> [Transcript]``.
"""

import json
import queue
import threading
import time


class Transcript(object):
    __slots__ = ("text", "confidence", "final", "source", "time")

    def __init__(self, text, confidence=1.0, final=True, source="text", time_=None):
        self.text = text
        self.confidence = float(confidence)
        self.final = bool(final)
        self.source = source
        self.time = time.time() if time_ is None else float(time_)

    def to_dict(self):
        return {"text": self.text, "confidence": self.confidence, "final": self.final, "source": self.source, "time": self.time}

    @classmethod
    def from_dict(cls, d):
        return cls(str(d.get("text", "")), float(d.get("confidence", 1.0)), bool(d.get("final", True)),
                   str(d.get("source", "remote")), d.get("time"))

    def __repr__(self):
        return "Transcript(%r, %.2f)" % (self.text, self.confidence)


class Backend(object):
    name = "backend"
    available = False
    unavailable_reason = ""

    def __init__(self):
        self._queue = queue.Queue()
        self.listening = False

    def start(self):
        self.listening = True

    def stop(self):
        self.listening = False

    def push(self, transcript):
        if isinstance(transcript, str):
            transcript = Transcript(transcript, source=self.name)
        self._queue.put(transcript)

    def poll(self):
        out = []
        while True:
            try:
                out.append(self._queue.get_nowait())
            except queue.Empty:
                return out


class TextBackend(Backend):
    name = "text"
    available = True


class RemoteBackend(Backend):
    """Transcripts arrive from the sync server's ``/api/v1/voice`` endpoint."""

    name = "remote"
    available = True

    def receive(self, payload):
        """Accept a JSON body (dict or text) from the server; returns the transcript."""
        if isinstance(payload, (bytes, bytearray, str)):
            payload = json.loads(payload)
        transcript = Transcript.from_dict(payload)
        self.push(transcript)
        return transcript


class VoskBackend(Backend):
    name = "vosk"

    def __init__(self, model_path=None, vocabulary=None, sample_rate=16000, device=None):
        super().__init__()
        self.model_path = model_path
        self.vocabulary = list(vocabulary or [])
        self.sample_rate = int(sample_rate)
        self.device = device
        self._thread = None
        self._stop = threading.Event()
        self.available, self.unavailable_reason = self._probe()

    def _probe(self):
        try:
            import vosk  # noqa: F401
        except ImportError:
            return False, "the 'vosk' package is not installed (pip install vosk)"
        try:
            import sounddevice  # noqa: F401
        except ImportError:
            return False, "the 'sounddevice' package is not installed (pip install sounddevice)"
        if not self.model_path:
            return False, "no Vosk model directory configured (Preferences → XR → Voice)"
        return True, ""

    def start(self):
        if not self.available or self.listening:
            return
        import sounddevice
        import vosk

        model = vosk.Model(self.model_path)
        grammar = json.dumps(sorted(set(self.vocabulary + ["[unk]"]))) if self.vocabulary else None
        recogniser = vosk.KaldiRecognizer(model, self.sample_rate, grammar) if grammar else vosk.KaldiRecognizer(model, self.sample_rate)
        recogniser.SetWords(True)
        self._stop.clear()

        def run():
            def callback(indata, frames, time_info, status):
                if self._stop.is_set():
                    raise sounddevice.CallbackStop()
                if recogniser.AcceptWaveform(bytes(indata)):
                    result = json.loads(recogniser.Result())
                    text = result.get("text", "").strip()
                    if text:
                        words = result.get("result", [])
                        conf = min((w.get("conf", 1.0) for w in words), default=1.0)
                        self.push(Transcript(text, conf, True, self.name))
                else:
                    partial = json.loads(recogniser.PartialResult()).get("partial", "").strip()
                    if partial:
                        self.push(Transcript(partial, 0.5, False, self.name))

            with sounddevice.RawInputStream(samplerate=self.sample_rate, blocksize=4000, dtype="int16",
                                            channels=1, device=self.device, callback=callback):
                while not self._stop.is_set():
                    time.sleep(0.05)

        self._thread = threading.Thread(target=run, name="xrvoice-vosk", daemon=True)
        self._thread.start()
        self.listening = True

    def stop(self):
        self._stop.set()
        self.listening = False
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None


def vocabulary_words(commands=None):
    """Every word the grammar can use — the recogniser's grammar list."""
    from . import grammar, numbers

    words = set()
    for cmd in commands or grammar.COMMANDS:
        for pattern in cmd.patterns:
            for raw in pattern.replace("[", " ").replace("]", " ").replace("(", " ").replace(")", " ").split():
                if raw.startswith("{"):
                    continue
                words.update(raw.split("|"))
    words.update(numbers._SMALL)
    words.update(numbers._TENS)
    words.update(numbers._SCALE)
    words.update(numbers._FRACTIONS)
    words.update(k for k in numbers.UNITS if k.isalpha())
    words.update(["point", "and", "of", "the", "this", "these", "please", "x", "y", "z"])
    words.update(grammar.DIRECTIONS)
    words.update(["printer", "laser", "cutter", "workshop", "studio", "void", "bambu"])
    return sorted(w for w in words if w.isalpha())


def best_backend(model_path=None, vocabulary=None):
    vosk_backend = VoskBackend(model_path, vocabulary)
    if vosk_backend.available:
        return vosk_backend
    return TextBackend()
