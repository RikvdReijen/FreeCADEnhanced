// SPDX-License-Identifier: LGPL-2.1-or-later
//
// LAN sync client for ARCHITECTURE.md §3.
//
// Plain HTTP/1.1 over BSD sockets — no external dependency — driven from a
// single worker thread so the render thread never blocks. The render thread
// posts jobs and drains finished results once per frame:
//
//     sync.requestScene("Part", 2);
//     ...
//     SyncResult r;
//     while (sync.poll(&r)) { ... }
//
// Endpoints implemented: /hello, /pair, /documents, /scene, /scene/hash,
// /events (long poll), /environments, /environment, /thumbnail, /paint,
// /vector, plus /state, which the desktop server offers as an extension to the
// §3 table and which reports the environment and scale the desktop is in.
//
// Every request sends `Connection: close`: the server drops the connection on
// an unauthenticated request without reading the body, so a keep-alive pool
// would desynchronise the moment a token expires.
#pragma once

#include <atomic>
#include <condition_variable>
#include <deque>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

#include "json.h"

namespace fcxr {

// A server found by the UDP discovery beacon or entered by hand.
struct ServerInfo {
    std::string address;  // dotted quad
    int port = 47810;
    std::string name;
    std::string id;
    int protocolVersion = 1;
    bool authRequired = true;
    bool paired = false;
    bool pairingActive = false;
};

enum class SyncJobKind {
    Discover,
    Hello,
    Pair,
    Documents,
    Scene,
    SceneHash,
    Events,
    Environments,
    Environment,
    Thumbnail,
    State,
    UploadPaint,
    UploadVector,
};

struct SyncResult {
    SyncJobKind kind = SyncJobKind::Hello;
    bool ok = false;
    int status = 0;             // HTTP status, 0 for a transport failure
    std::string error;
    std::string key;            // document name / environment id, echoed back
    std::vector<uint8_t> data;  // .fcxr or PNG payloads
    json::Value body;           // parsed JSON payloads
    std::vector<ServerInfo> servers;  // Discover only
};

enum class SyncState { Offline, Discovering, Connecting, Unpaired, Ready, Busy, Failed };

class SyncClient {
public:
    SyncClient();
    ~SyncClient();

    void start();
    void stop();

    // Connection settings. Safe to call from the render thread.
    void setServer(const std::string& address, int port);
    void setToken(const std::string& token);
    std::string token() const;
    std::string serverAddress() const;
    int serverPort() const;

    // ---- jobs (all asynchronous; results come back through poll()) -------
    void discover(int timeoutMs = 700);
    void hello();
    void pair(const std::string& code, const std::string& deviceName);
    void requestDocuments();
    void requestScene(const std::string& doc, int lod);
    void requestSceneHash(const std::string& doc);
    void requestEnvironments();
    void requestEnvironment(const std::string& id);
    void requestThumbnail(const std::string& doc);
    void requestState();
    // Long poll. The worker re-arms it automatically until stopEvents().
    void startEvents(int64_t since);
    void stopEvents();
    void uploadPaint(std::vector<uint8_t> fcxr);
    void uploadVector(const std::string& jsonText);

    // ---- results ---------------------------------------------------------
    // Returns false when the queue is empty. Never blocks.
    bool poll(SyncResult* out);

    SyncState state() const { return state_.load(); }
    // 0..1 for the job in flight (scene downloads report real progress when
    // the server sends a Content-Length).
    float progress() const { return progress_.load(); }
    std::string statusText() const;
    int64_t lastEventSeq() const { return lastEventSeq_.load(); }

private:
    struct Job {
        SyncJobKind kind;
        std::string key;        // doc name, environment id, pairing code
        std::string text;       // request body
        std::vector<uint8_t> data;
        int number = 0;         // lod / timeout / since
    };

    void workerMain();
    void runJob(const Job& job);
    void push(Job job);
    void deliver(SyncResult result);
    void setState(SyncState s) { state_.store(s); }

    // Blocking HTTP request on the worker thread. Returns the status code, or
    // 0 with `error` set for a transport failure.
    int request(const char* method, const std::string& path, const std::string& contentType,
                const uint8_t* body, size_t bodySize, std::vector<uint8_t>* response,
                std::string* error, int timeoutMs, bool trackProgress);

    std::vector<ServerInfo> discoverBlocking(int timeoutMs);

    mutable std::mutex mutex_;
    std::condition_variable wake_;
    std::deque<Job> jobs_;
    std::deque<SyncResult> results_;
    std::thread worker_;
    std::atomic<bool> running_{false};
    std::atomic<bool> eventsActive_{false};
    std::atomic<SyncState> state_{SyncState::Offline};
    std::atomic<float> progress_{0.0f};
    std::atomic<int64_t> lastEventSeq_{0};

    std::string address_;
    int port_ = 47810;
    std::string token_;
    std::string lastError_;
};

// URL-escapes a query parameter value.
std::string urlEncode(const std::string& value);

}  // namespace fcxr
