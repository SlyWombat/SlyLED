/*
 * Parent.h — Parent (Giga R1 WiFi) data structures, global data declarations,
 *            and function declarations.
 *
 * All content is guarded by #ifdef BOARD_GIGA — safe to include on any board.
 */

#ifndef PARENT_H
#define PARENT_H

#include "BoardConfig.h"
#include "Protocol.h"

#ifdef BOARD_GIGA

// ── Parent-specific constants ─────────────────────────────────────────────────

constexpr uint8_t MAX_CHILDREN  = 8;
constexpr uint8_t CHILD_UNKNOWN = 0;
constexpr uint8_t CHILD_ONLINE  = 1;
constexpr uint8_t CHILD_OFFLINE = 2;

constexpr uint8_t MAX_RUNNERS     = 4;
constexpr uint8_t MAX_STEPS       = 16;
constexpr uint8_t RUNNER_NAME_LEN = 16;

// ── Parent data structures ────────────────────────────────────────────────────

struct StringInfo {
  uint16_t ledCount;
  uint16_t lengthMm;
  uint8_t  ledType;
  uint8_t  cableDir;
  uint16_t cableMm;
  uint8_t  stripDir;
};

struct ChildNode {
  uint8_t    ip[4];
  char       hostname[HOSTNAME_LEN];
  char       name[CHILD_NAME_LEN];
  char       description[CHILD_DESC_LEN];
  int16_t    xMm, yMm, zMm;
  uint8_t    stringCount;
  StringInfo strings[MAX_STR_PER_CHILD];
  uint8_t    status;
  uint32_t   lastSeenEpoch;
  bool       configFetched;
  bool       inUse;
};

struct AppSettings {
  uint8_t  units;
  uint8_t  darkMode;
  uint16_t canvasWidthMm;
  uint16_t canvasHeightMm;
  char     parentName[16];
  uint8_t  activeRunner;
  bool     runnerRunning;
};

struct RunnerAction {
  uint8_t  type;
  uint8_t  r, g, b;
  uint16_t onMs, offMs;
  uint8_t  wipeDir, wipeSpeedPct;
};  // 10 bytes

struct AreaRect {
  uint16_t x0, y0, x1, y1;  // 0–10000 (units of 0.01%)
};  // 8 bytes

struct RunnerStep {
  RunnerAction action;
  AreaRect     area;
  uint16_t     durationS;
};  // 20 bytes

struct ChildStepPayload {
  uint8_t ledStart[MAX_STR_PER_CHILD];
  uint8_t ledEnd[MAX_STR_PER_CHILD];
};  // 16 bytes

struct Runner {
  char             name[RUNNER_NAME_LEN];
  uint8_t          stepCount;
  bool             computed;
  bool             inUse;
  RunnerStep       steps[MAX_STEPS];                    // 320 bytes
  ChildStepPayload payload[MAX_STEPS][MAX_CHILDREN];    // 1024 bytes
};  // ~1363 bytes each; 4 runners ≈ 5452 bytes

// ── Global data (defined in Parent.cpp) ──────────────────────────────────────

extern ChildNode   children[MAX_CHILDREN];
extern AppSettings settings;
extern Runner      runners[MAX_RUNNERS];

// ── Function declarations ─────────────────────────────────────────────────────

void sendParentSPA(WiFiClient& c);

void sendPing(IPAddress dest);
void registerChild(IPAddress ip, const PongPayload* pong);

void sendApiChildren(WiFiClient& c);
void sendApiChildrenExport(WiFiClient& c);
void handleApiChildrenImport(WiFiClient& c, int contentLen);
void handleChildIdRoute(WiFiClient& c, const char* req, bool isPost, bool isDel, int contentLen);
void handleApiChildStatus(WiFiClient& c, uint8_t id);

void sendApiLayout(WiFiClient& c);
void handlePostLayout(WiFiClient& c, int contentLen);

void sendApiSettings(WiFiClient& c);
void handlePostSettings(WiFiClient& c, int contentLen);

void sendCmdAction(IPAddress dest, const ActionPayload* p);
void sendCmdActionStop(IPAddress dest);
void handleApiAction(WiFiClient& c, int contentLen);
void handleApiActionStop(WiFiClient& c, int contentLen);

void sendApiRunners(WiFiClient& c);
void sendApiRunner(WiFiClient& c, uint8_t id);
void handlePostRunners(WiFiClient& c, int contentLen);
void handleRunnerIdRoute(WiFiClient& c, const char* req, bool isGet, bool isPut,
                         bool isDel, int contentLen);
void computeRunner(uint8_t id);
void sendLoadStep(IPAddress dest, uint8_t stepIdx, uint8_t totalSteps,
                  const RunnerStep& step, const ChildStepPayload& pl);
void syncRunner(uint8_t id);
void startRunner(uint8_t id);
void stopAllRunners();

#endif  // BOARD_GIGA

#endif  // PARENT_H
