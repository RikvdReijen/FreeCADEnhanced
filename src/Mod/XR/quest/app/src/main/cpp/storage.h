// SPDX-License-Identifier: LGPL-2.1-or-later
//
// The on-device library of `.fcxr` packages plus small pieces of persistent
// state (recent files, pairing token, last environment).
//
// Everything lives under the app's private files directory, which Java passes
// in at startup:
//
//   <files>/library/<name>.fcxr     scene packages (synced, sideloaded or
//                                   downloaded from Drive)
//   <files>/thumbs/<name>.png       thumbnails, keyed by library file name
//   <files>/settings.json           the small settings blob below
//
// No Android APIs are used, so this compiles and runs on the host for tests.
#pragma once

#include <cstdint>
#include <string>
#include <vector>

#include "json.h"

namespace fcxr {

struct LibraryEntry {
    std::string path;        // absolute
    std::string fileName;    // "part.fcxr"
    std::string title;       // manifest asset.source_document, or the file name
    std::string environment; // manifest scene.environment
    uint64_t bytes = 0;
    int64_t modified = 0;    // unix seconds
    bool hasThumbnail = false;
};

struct Settings {
    std::string serverHost;
    int serverPort = 47810;
    std::string serverToken;
    std::string serverName;
    std::string environment;   // last environment id
    std::string lastDocument;  // library path
    bool passthrough = false;
    float userScaleOverride = 0.0f;  // 0 = follow the environment
    std::vector<std::string> recents;
};

class Storage {
public:
    // `filesDir` is Context.getFilesDir(); `cacheDir` is Context.getCacheDir().
    bool init(const std::string& filesDir, const std::string& cacheDir);
    bool ready() const { return !filesDir_.empty(); }

    const std::string& filesDir() const { return filesDir_; }
    const std::string& cacheDir() const { return cacheDir_; }
    std::string libraryDir() const { return filesDir_ + "/library"; }

    // Scans the library directory, newest first. Reads only the manifest of
    // each package, not the geometry.
    std::vector<LibraryEntry> list() const;

    // Writes `data` as <library>/<name>, replacing any existing file
    // atomically (write to .tmp, then rename). Returns the full path.
    bool save(const std::string& name, const uint8_t* data, size_t size,
              std::string* pathOut = nullptr);
    bool load(const std::string& path, std::vector<uint8_t>* out) const;
    bool remove(const std::string& path);

    // Thumbnails are PNG, named after the library file.
    std::string thumbnailPath(const std::string& libraryPath) const;
    bool saveThumbnail(const std::string& libraryPath, const uint8_t* png, size_t size);

    // Settings are loaded once at startup and written back on change.
    const Settings& settings() const { return settings_; }
    Settings& mutableSettings() { return settings_; }
    bool loadSettings();
    bool saveSettings() const;
    // Moves `path` to the front of the recent list (max 12 entries).
    void noteRecent(const std::string& path);

    // Sanitises a name coming from the network or a SAF picker into something
    // safe to write: no separators, no leading dots, always ends in .fcxr.
    static std::string safeFileName(const std::string& name);

private:
    std::string filesDir_;
    std::string cacheDir_;
    Settings settings_;
};

// Small filesystem helpers shared with the rest of the app.
bool fileExists(const std::string& path);
bool makeDirectories(const std::string& path);
bool readWholeFile(const std::string& path, std::vector<uint8_t>* out);
bool writeWholeFileAtomic(const std::string& path, const uint8_t* data, size_t size);

}  // namespace fcxr
