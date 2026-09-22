#pragma once
#include <stdint.h>
#include <string.h>

// The desktop declares its OS after CAPS,HOST_PROFILE,1. BLE addresses, MTU,
// connection timing and device names cannot reliably identify a host OS.
enum class HostSystem : uint8_t { Compatible, MacOS, Windows, Linux };

struct HostProfile {
  HostSystem system;
  uint16_t minInterval;  // BLE units of 1.25 ms
  uint16_t maxInterval;
  uint8_t batchSamples;
  const char* name;
};

struct HostLinkParameters {
  uint16_t minimum, maximum, latency, supervisionTimeout;
};

inline HostLinkParameters hostLinkParameters(const HostProfile& profile, bool sleeping) {
  return {static_cast<uint16_t>(sleeping ? 96 : profile.minInterval),
          static_cast<uint16_t>(sleeping ? 108 : profile.maxInterval), 0, 400};
}

inline HostProfile hostProfile(HostSystem system) {
  switch (system) {
    case HostSystem::MacOS: return {system, 12, 24, 2, "MACOS"};
    case HostSystem::Windows: return {system, 6, 12, 4, "WINDOWS"};
    case HostSystem::Linux: return {system, 12, 24, 2, "LINUX"};
    default: return {HostSystem::Compatible, 12, 24, 4, "COMPATIBLE"};
  }
}

inline uint8_t hostBatchSamples(const HostProfile& profile, uint16_t intervalUnits) {
  if (profile.system != HostSystem::MacOS && profile.system != HostSystem::Linux)
    return profile.batchSamples;
  // Sample every 10 ms. Keep a little headroom over the central's real
  // interval instead of producing 50 records/s on a 30 ms (~33/s) link.
  const uint16_t count = intervalUnits * 5 / 40 + 1;
  return count < 2 ? 2 : count > 4 ? 4 : count;
}

inline bool parseHostSystem(const char* name, HostSystem& system) {
  if (!strcmp(name, "MACOS")) system = HostSystem::MacOS;
  else if (!strcmp(name, "WINDOWS")) system = HostSystem::Windows;
  else if (!strcmp(name, "LINUX")) system = HostSystem::Linux;
  else if (!strcmp(name, "COMPATIBLE")) system = HostSystem::Compatible;
  else return false;
  return true;
}
