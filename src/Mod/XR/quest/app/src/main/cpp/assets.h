// SPDX-License-Identifier: LGPL-2.1-or-later
//
// Access to the APK's asset bundle (shaders and the environment specs the
// Gradle `copyEnvironments` task stages into assets/environments/).
#pragma once

#include <cstdint>
#include <string>
#include <vector>

struct AAssetManager;

namespace fcxr {

class Assets {
public:
    // Called from the JNI bridge on the Java side's asset manager. The
    // AAssetManager pointer must outlive this object (it does: MainActivity
    // holds the AssetManager for the process lifetime).
    void init(AAssetManager* manager) { manager_ = manager; }
    bool ready() const { return manager_ != nullptr; }

    // Reads a whole asset. Returns false when it does not exist.
    bool read(const std::string& path, std::vector<uint8_t>* out) const;
    bool readText(const std::string& path, std::string* out) const;
    // Lists the files (not directories) directly under `dir`.
    std::vector<std::string> list(const std::string& dir) const;

private:
    AAssetManager* manager_ = nullptr;
};

}  // namespace fcxr
