#pragma once

#include <stdint.h>

// Energy-based voice activity gate. Times are measured in captured PCM, not
// wall time, so a delayed BLE notification cannot shorten the silence window.
class VoiceActivityGate {
 public:
  void reset(uint16_t silenceMs) {
    silenceMs_ = silenceMs;
    noiseLevel_ = UINT16_MAX;
    lastSpeechMs_ = 0;
    onsetFrames_ = 0;
    continuationFrames_ = 0;
    speechDetected_ = false;
  }

  void observe(uint16_t level, uint32_t audioMs) {
    if (audioMs <= 140) return;  // PDM startup transient.
    if (audioMs <= 340) {
      if (level < noiseLevel_) noiseLevel_ = level;
      return;
    }

    const uint32_t baseline = noiseLevel_ == UINT16_MAX ? 100 : noiseLevel_;
    const uint32_t onsetThreshold = maximum(260, baseline * 2 + 60);
    const uint32_t continuationThreshold = maximum(190, baseline * 3 / 2 + 50);
    if (!speechDetected_) {
      onsetFrames_ = level >= onsetThreshold ? onsetFrames_ + 1 : 0;
      if (onsetFrames_ >= 3) {
        speechDetected_ = true;
        lastSpeechMs_ = audioMs;
        continuationFrames_ = 0;
      }
    } else {
      continuationFrames_ = level >= continuationThreshold
                                ? (continuationFrames_ < 2 ? continuationFrames_ + 1 : 2)
                                : 0;
      if (continuationFrames_ >= 2) {
        lastSpeechMs_ = audioMs;
      }
    }

    // Follow slowly changing room noise only on frames below the speech gate.
    // A single loud impact never resets the silence timer or raises the floor.
    if (level < continuationThreshold) {
      const uint32_t current = baseline;
      noiseLevel_ = static_cast<uint16_t>((current * 31 + level) / 32);
    }
  }

  bool speechDetected() const { return speechDetected_; }
  bool silenceExpired(uint32_t audioMs) const {
    return speechDetected_ && audioMs - lastSpeechMs_ >= silenceMs_;
  }

 private:
  static uint32_t maximum(uint32_t left, uint32_t right) {
    return left > right ? left : right;
  }

  uint16_t silenceMs_ = 1200;
  uint16_t noiseLevel_ = UINT16_MAX;
  uint32_t lastSpeechMs_ = 0;
  uint8_t onsetFrames_ = 0;
  uint8_t continuationFrames_ = 0;
  bool speechDetected_ = false;
};
