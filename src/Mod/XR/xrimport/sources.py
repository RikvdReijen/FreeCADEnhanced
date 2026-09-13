# SPDX-License-Identifier: LGPL-2.1-or-later
"""Fetching models from the sharing platforms.

Four platforms, four different levels of API:

===========  ====================================================================
Thingiverse  documented REST API (``api.thingiverse.com``); needs an app token
             from https://www.thingiverse.com/developers, stored in preferences
Printables   public GraphQL endpoint (``api.printables.com/graphql``) used by
             the site itself; unofficial, no token needed for public models
MakerWorld   JSON endpoints behind the site (``makerworld.com/api/v1``);
             unofficial, no token needed for public models
GrabCAD      no public API and downloads require a signed-in session, so the
             resolver recognises the URL, reads the model title from the page,
             and asks for the ZIP the user downloads in a browser
===========  ====================================================================

Every source implements :class:`Source`: ``matches(url)``, ``resolve(url)``
→ :class:`ModelRef` with its downloadable files, and ``download(file, dest)``.
HTTP goes through one injectable ``fetch(url, headers) -> bytes`` so the
whole module is testable offline, and so a corporate proxy or a cache can be
put in front of it. Unofficial endpoints are exactly that: they work today
and may stop; failures are reported as :class:`SourceError` with the URL.
"""

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request

from .formats import SUPPORTED_EXTENSIONS

USER_AGENT = "FreeCAD-XR/1.0 (+https://freecad.org)"


class SourceError(RuntimeError):
    def __init__(self, message, url=None, status=None):
        super().__init__(message)
        self.url = url
        self.status = status


class ModelFile(object):
    __slots__ = ("name", "url", "size", "kind", "extra")

    def __init__(self, name, url, size=None, kind=None, extra=None):
        self.name = name
        self.url = url
        self.size = size
        self.kind = kind or os.path.splitext(name)[1].lower().lstrip(".")
        self.extra = dict(extra or {})

    @property
    def supported(self):
        return os.path.splitext(self.name)[1].lower() in SUPPORTED_EXTENSIONS

    def to_dict(self):
        return {"name": self.name, "url": self.url, "size": self.size, "kind": self.kind}

    def __repr__(self):
        return "ModelFile(%r)" % self.name


class ModelRef(object):
    __slots__ = ("source", "id", "title", "author", "license", "url", "files", "thumbnail", "notes")

    def __init__(self, source, id, title="", author="", license="", url="", files=(), thumbnail=None, notes=()):
        self.source = source
        self.id = str(id)
        self.title = title
        self.author = author
        self.license = license
        self.url = url
        self.files = list(files)
        self.thumbnail = thumbnail
        self.notes = list(notes)

    @property
    def printable_files(self):
        return [f for f in self.files if f.supported]

    def to_dict(self):
        return {"source": self.source, "id": self.id, "title": self.title, "author": self.author,
                "license": self.license, "url": self.url, "files": [f.to_dict() for f in self.files],
                "thumbnail": self.thumbnail, "notes": list(self.notes)}

    def __repr__(self):
        return "ModelRef(%s:%s %r, %d files)" % (self.source, self.id, self.title, len(self.files))


def default_fetch(url, headers=None, data=None, timeout=30.0):
    request = urllib.request.Request(url, data=data, headers=dict(headers or {}))
    request.add_header("User-Agent", USER_AGENT)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        raise SourceError("HTTP %d fetching %s" % (exc.code, url), url, exc.code) from None
    except urllib.error.URLError as exc:
        raise SourceError("cannot reach %s: %s" % (url, exc.reason), url) from None


class Source(object):
    name = ""
    hosts = ()

    def __init__(self, fetch=None, token=None):
        self.fetch = fetch or default_fetch
        self.token = token

    def matches(self, url):
        host = urllib.parse.urlparse(url).netloc.lower()
        return any(host == h or host.endswith("." + h) for h in self.hosts)

    def resolve(self, url):
        raise NotImplementedError

    def download(self, model_file, dest_dir):
        data = self.fetch(model_file.url, self._headers())
        path = os.path.join(dest_dir, _safe_filename(model_file.name))
        with open(path, "wb") as handle:
            handle.write(data)
        return path

    def _headers(self):
        return {}

    def _json(self, url, headers=None, data=None):
        raw = self.fetch(url, headers or self._headers(), data)
        try:
            return json.loads(raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else raw)
        except ValueError:
            raise SourceError("%s did not return JSON" % url, url)


def _safe_filename(name):
    name = os.path.basename(name.replace("\\", "/")) or "model"
    return re.sub(r"[^A-Za-z0-9._ -]+", "_", name)


# ----------------------------------------------------------------------
# Thingiverse — official API
# ----------------------------------------------------------------------


class Thingiverse(Source):
    name = "thingiverse"
    hosts = ("thingiverse.com",)
    api = "https://api.thingiverse.com"
    _ID = re.compile(r"/thing:(\d+)")

    def thing_id(self, url):
        m = self._ID.search(url)
        if not m:
            raise SourceError("not a Thingiverse thing URL: %s" % url, url)
        return m.group(1)

    def _headers(self):
        if not self.token:
            raise SourceError("Thingiverse needs an app token (Preferences → XR → Import → Thingiverse token)")
        return {"Authorization": "Bearer %s" % self.token}

    def resolve(self, url):
        tid = self.thing_id(url)
        thing = self._json("%s/things/%s" % (self.api, tid))
        files = self._json("%s/things/%s/files" % (self.api, tid))
        model_files = [
            ModelFile(f.get("name", "file"), f.get("download_url") or f.get("public_url", ""),
                      f.get("size"), extra={"id": f.get("id")})
            for f in files if isinstance(f, dict)
        ]
        return ModelRef(
            self.name, tid, thing.get("name", ""), (thing.get("creator") or {}).get("name", ""),
            thing.get("license", ""), thing.get("public_url", url), model_files,
            thumbnail=thing.get("thumbnail"),
        )


# ----------------------------------------------------------------------
# Printables — GraphQL used by the site
# ----------------------------------------------------------------------


class Printables(Source):
    name = "printables"
    hosts = ("printables.com",)
    api = "https://api.printables.com/graphql"
    files_base = "https://files.printables.com"
    _ID = re.compile(r"/model/(\d+)")
    QUERY = """query PrintFiles($id: ID!) { print(id: $id) { id name license { name }
      user { publicUsername } stls { id name fileSize filePreviewPath }
      gcodes { id name fileSize } otherFiles { id name fileSize } images { filePath } } }"""

    def model_id(self, url):
        m = self._ID.search(url)
        if not m:
            raise SourceError("not a Printables model URL: %s" % url, url)
        return m.group(1)

    def _headers(self):
        return {"Content-Type": "application/json", "Accept": "application/json"}

    def resolve(self, url):
        mid = self.model_id(url)
        body = json.dumps({"query": self.QUERY, "variables": {"id": mid}}).encode("utf-8")
        reply = self._json(self.api, data=body)
        model = (reply.get("data") or {}).get("print")
        if not model:
            raise SourceError("Printables returned no model for %s (%s)" % (mid, reply.get("errors")), url)
        notes = ["Printables: unofficial endpoint; file downloads need a browser session if this fails"]
        files = []
        for group in ("stls", "otherFiles", "gcodes"):
            for f in model.get(group) or []:
                files.append(ModelFile(f.get("name", "file"), self.download_url(mid, f), f.get("fileSize"),
                                       extra={"id": f.get("id"), "group": group}))
        images = model.get("images") or []
        thumb = self.files_base + "/" + images[0]["filePath"] if images and images[0].get("filePath") else None
        return ModelRef(self.name, mid, model.get("name", ""),
                        (model.get("user") or {}).get("publicUsername", ""),
                        (model.get("license") or {}).get("name", ""), url, files, thumb, notes)

    def download_url(self, model_id, file_info):
        """Printables issues download links per request; the URL below is the
        site's own pattern and is resolved to a signed link by ``download``."""
        return "https://www.printables.com/model/%s/files/%s" % (model_id, file_info.get("id"))

    def download(self, model_file, dest_dir):
        body = json.dumps({
            "query": "mutation GetDownloadLink($id: ID!, $modelId: ID!, $fileType: DownloadFileTypeEnum!) "
                     "{ getDownloadLink(id: $id, printId: $modelId, fileType: $fileType) { ok output { link } } }",
            "variables": {"id": model_file.extra.get("id"), "modelId": model_file.url.split("/model/")[1].split("/")[0],
                          "fileType": {"stls": "stl", "gcodes": "gcode"}.get(model_file.extra.get("group"), "other")},
        }).encode("utf-8")
        reply = self._json(self.api, data=body)
        link = ((reply.get("data") or {}).get("getDownloadLink") or {}).get("output", {}).get("link")
        if not link:
            raise SourceError("Printables did not issue a download link for %s" % model_file.name, model_file.url)
        return Source.download(self, ModelFile(model_file.name, link), dest_dir)


# ----------------------------------------------------------------------
# MakerWorld (Bambu Lab) — JSON behind the site
# ----------------------------------------------------------------------


class MakerWorld(Source):
    name = "makerworld"
    hosts = ("makerworld.com",)
    api = "https://makerworld.com/api/v1/design-service/design"
    _ID = re.compile(r"/models/(\d+)")

    def design_id(self, url):
        m = self._ID.search(url)
        if not m:
            raise SourceError("not a MakerWorld model URL: %s" % url, url)
        return m.group(1)

    def resolve(self, url):
        did = self.design_id(url)
        design = self._json("%s/%s" % (self.api, did))
        files = []
        for instance in design.get("instances") or []:
            for f in instance.get("files") or design.get("modelFiles") or []:
                name = f.get("name") or f.get("fileName") or "model.3mf"
                link = f.get("url") or f.get("downloadUrl") or ""
                files.append(ModelFile(name, link, f.get("size") or f.get("fileSize"),
                                       extra={"instance": instance.get("id")}))
        for f in design.get("modelFiles") or []:
            if not any(x.url == (f.get("url") or "") for x in files):
                files.append(ModelFile(f.get("name") or "model.3mf", f.get("url") or "", f.get("size")))
        notes = ["MakerWorld: unofficial endpoint; some designs only download as a print profile (.3mf with slicer settings)"]
        return ModelRef(self.name, did, design.get("title", ""), (design.get("designCreator") or {}).get("name", ""),
                        design.get("license", ""), url, files, design.get("cover"), notes)


# ----------------------------------------------------------------------
# GrabCAD — no API; URL recognition and a manual ZIP
# ----------------------------------------------------------------------


class GrabCAD(Source):
    name = "grabcad"
    hosts = ("grabcad.com",)
    _ID = re.compile(r"/library/([A-Za-z0-9_-]+)")

    def library_id(self, url):
        m = self._ID.search(url)
        if not m:
            raise SourceError("not a GrabCAD library URL: %s" % url, url)
        return m.group(1)

    def resolve(self, url):
        lid = self.library_id(url)
        title = lid.replace("-", " ")
        try:
            page = self.fetch(url, {}).decode("utf-8", "replace")
            m = re.search(r"<title>(.*?)</title>", page, re.S)
            if m:
                title = re.sub(r"\s*\|\s*3D CAD Model Library\s*\|\s*GrabCAD\s*$", "", m.group(1).strip())
        except SourceError:
            pass
        return ModelRef(self.name, lid, title, "", "", url, [], None, [
            "GrabCAD has no public download API and requires a signed-in session. "
            "Download the ZIP in your browser and open it with xrimport.convert.import_archive()."
        ])


ALL_SOURCES = (Thingiverse, Printables, MakerWorld, GrabCAD)


def source_for(url, fetch=None, tokens=None):
    tokens = tokens or {}
    for cls in ALL_SOURCES:
        src = cls(fetch=fetch, token=tokens.get(cls.name))
        if src.matches(url):
            return src
    raise SourceError("no importer knows %s (supported: %s)" % (url, ", ".join(c.name for c in ALL_SOURCES)), url)


def resolve(url, fetch=None, tokens=None):
    return source_for(url, fetch, tokens).resolve(url)
