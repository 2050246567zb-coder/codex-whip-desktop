#pragma once

#include <Arduino.h>
#include <math.h>

struct MotionSample {
  uint32_t timestampMs;
  float accelX;
  float accelY;
  float accelZ;
  float gyroX;
  float gyroY;
  float gyroZ;
};

struct WhipEvent {
  uint32_t sequence;
  uint16_t durationMs;
  float peakAccelG;
  float peakGyroDps;
  float angularTravelDeg;
  float directionConsistency;
  float dominantAxisRatio;
  uint16_t peakGapMs;
  float peakJerkGps;
  float peakDynamicAccelG;
};

enum class WhipRejectReason : uint8_t {
  NotConfirmed,
  TooShort,
  LowTravel,
  LowDirection,
  LowAxis,
  PeakGap,
  NoQuiet,
};

struct WhipRejection {
  uint32_t attempt;
  WhipRejectReason reason;
  uint16_t durationMs;
  float peakAccelG;
  float peakGyroDps;
  float angularTravelDeg;
  float directionConsistency;
  float dominantAxisRatio;
  uint16_t peakGapMs;
  bool confirmed;
  float peakJerkGps;
  float peakDynamicAccelG;
};

struct WhipDetectorConfig {
  // V2 keeps the proven 0.1.2 start strength, but acceleration thresholds use
  // distance from 1 g so both positive and negative handle-end acceleration
  // contribute. Gyro-derived shape is the primary discriminator.
  float startGyroDps = 546.0f;
  float startDynamicAccelG = 0.885f;
  float confirmGyroDps = 845.0f;
  float confirmDynamicAccelG = 1.275f;
  float hardDynamicAccelG = 2.38f;
  float hardAccelMinGyroDps = 390.0f;
  uint8_t confirmSamples = 3;

  float minimumAngularTravelDeg = 35.0f;
  // Keep reporting net direction, but do not reject on it: real handle-end
  // whips include braking and rebound, which legitimately cancel direction.
  float minimumDirectionConsistency = 0.0f;
  float minimumDominantAxisRatio = 0.45f;
  uint16_t maximumPeakGapMs = 180;
  uint16_t minimumDurationMs = 40;

  float quietGyroDps = 180.0f;
  float quietDynamicAccelG = 0.25f;
  uint16_t quietTimeMs = 45;
  uint16_t candidateTimeoutMs = 220;
  // Real calibration produced several valid, single-axis candidates that had
  // not completed their 45 ms quiet tail at 700 ms.
  uint16_t maxEventMs = 900;
  uint16_t cooldownMs = 700;
};

class WhipDetector {
 public:
  explicit WhipDetector(const WhipDetectorConfig& config = WhipDetectorConfig())
      : config_(config) {}

  void setGyroBias(float x, float y, float z) {
    biasX_ = x;
    biasY_ = y;
    biasZ_ = z;
  }

  void setConfig(const WhipDetectorConfig& config) {
    config_ = config;
    reset();
  }

  void reset() {
    state_ = State::Idle;
    cooldownStartMs_ = 0;
    rejectionPending_ = false;
    clearCandidate();
  }

  bool takeRejection(WhipRejection& rejection) {
    if (!rejectionPending_) return false;
    rejection = lastRejection_;
    rejectionPending_ = false;
    return true;
  }

  bool update(const MotionSample& sample, WhipEvent& event) {
    const float correctedGyroX = sample.gyroX - biasX_;
    const float correctedGyroY = sample.gyroY - biasY_;
    const float correctedGyroZ = sample.gyroZ - biasZ_;
    const float gyroMagnitude = sqrtf(correctedGyroX * correctedGyroX +
                                      correctedGyroY * correctedGyroY +
                                      correctedGyroZ * correctedGyroZ);
    const float accelMagnitude = sqrtf(sample.accelX * sample.accelX +
                                       sample.accelY * sample.accelY +
                                       sample.accelZ * sample.accelZ);
    const float dynamicAccel = fabsf(accelMagnitude - 1.0f);

    switch (state_) {
      case State::Idle:
        if (gyroMagnitude >= config_.startGyroDps ||
            dynamicAccel >= config_.startDynamicAccelG) {
          beginCandidate(sample.timestampMs, gyroMagnitude, accelMagnitude,
                         dynamicAccel);
        }
        return false;

      case State::Candidate:
        accumulate(sample.timestampMs, correctedGyroX, correctedGyroY,
                   correctedGyroZ, gyroMagnitude, accelMagnitude, dynamicAccel);

        if (isConfirmationMotion() &&
            (gyroMagnitude >= config_.startGyroDps ||
             dynamicAccel >= config_.startDynamicAccelG)) {
          if (confirmationCount_ < 255) {
            ++confirmationCount_;
          }
          if (confirmationCount_ >= config_.confirmSamples) {
            confirmed_ = true;
          }
        } else if (!confirmed_) {
          confirmationCount_ = 0;
        }

        if (isQuiet(gyroMagnitude, dynamicAccel)) {
          if (quietStartMs_ == 0) {
            quietStartMs_ = sample.timestampMs;
          }
        } else {
          quietStartMs_ = 0;
        }

        if (quietStartMs_ != 0 &&
            elapsed(sample.timestampMs, quietStartMs_) >= config_.quietTimeMs) {
          const bool accepted = confirmed_ && passesShapeFilters(sample.timestampMs);
          if (accepted) {
            fillEvent(sample.timestampMs, event);
          } else {
            recordRejection(rejectionReason(sample.timestampMs),
                            sample.timestampMs);
          }
          enterCooldown(sample.timestampMs);
          return accepted;
        }

        if (elapsed(sample.timestampMs, candidateStartMs_) >= config_.maxEventMs) {
          // A continuous shake that never returns to quiet is not a whip.
          recordRejection(WhipRejectReason::NoQuiet, sample.timestampMs);
          // Do not accept the tail of the SAME endless shake as a new whip
          // when a fixed cooldown expires. Rearm only after a real quiet tail.
          clearCandidate();
          state_ = State::AwaitQuiet;
          return false;
        }

        if (!confirmed_ &&
            elapsed(sample.timestampMs, candidateStartMs_) >= config_.candidateTimeoutMs) {
          recordRejection(WhipRejectReason::NotConfirmed, sample.timestampMs);
          state_ = State::Idle;
          clearCandidate();
        }
        return false;

      case State::AwaitQuiet:
        if (!isQuiet(gyroMagnitude, dynamicAccel)) {
          quietStartMs_ = 0;
        } else if (quietStartMs_ == 0) {
          quietStartMs_ = sample.timestampMs;
        } else if (elapsed(sample.timestampMs, quietStartMs_) >= config_.quietTimeMs) {
          enterCooldown(sample.timestampMs);
        }
        return false;

      case State::Cooldown:
        if (elapsed(sample.timestampMs, cooldownStartMs_) >= config_.cooldownMs) {
          state_ = State::Idle;
        }
        return false;
    }

    return false;
  }

 private:
  enum class State { Idle, Candidate, AwaitQuiet, Cooldown };

  static uint32_t elapsed(uint32_t now, uint32_t then) { return now - then; }

  static float clamp01(float value) {
    if (value < 0.0f) return 0.0f;
    if (value > 1.0f) return 1.0f;
    return value;
  }

  static uint16_t saturatedDuration(uint32_t value) {
    return static_cast<uint16_t>(value > 65535 ? 65535 : value);
  }

  static uint16_t timestampDistance(uint32_t first, uint32_t second) {
    const uint32_t difference = min(first - second, second - first);
    return saturatedDuration(difference);
  }

  bool isQuiet(float gyroMagnitude, float dynamicAccel) const {
    return gyroMagnitude < min(config_.quietGyroDps, config_.startGyroDps * 0.45f) &&
           dynamicAccel < config_.quietDynamicAccelG;
  }

  bool isRotationLed() const {
    return peakGyroDps_ >= config_.confirmGyroDps &&
        angularTravelDeg_ >= min(15.0f, config_.minimumAngularTravelDeg * 0.4f);
  }

  bool isConfirmationMotion() const {
    const bool paired = peakGyroDps_ >= config_.confirmGyroDps &&
                        peakDynamicAccelG_ >= config_.confirmDynamicAccelG;
    const bool impactLed = peakDynamicAccelG_ >= config_.hardDynamicAccelG &&
                           peakGyroDps_ >= config_.hardAccelMinGyroDps;
    // A wrist-led whip can have little linear acceleration at the grip.
    // Confirm sustained fast rotation + travel, then still require the full
    // travel/axis/duration filters and a braking/quiet tail before emitting.
    return paired || impactLed || isRotationLed();
  }

  void beginCandidate(uint32_t timestampMs, float gyroMagnitude,
                      float accelMagnitude, float dynamicAccel) {
    clearCandidate();
    state_ = State::Candidate;
    ++attemptSequence_;
    candidateStartMs_ = timestampMs;
    lastSampleMs_ = timestampMs;
    peakGyroMs_ = timestampMs;
    peakAccelMs_ = timestampMs;
    peakGyroDps_ = gyroMagnitude;
    peakAccelG_ = accelMagnitude;
    peakDynamicAccelG_ = dynamicAccel;
    previousAccelMagnitudeG_ = accelMagnitude;
  }

  void accumulate(uint32_t timestampMs, float gyroX, float gyroY, float gyroZ,
                  float gyroMagnitude, float accelMagnitude, float dynamicAccel) {
    uint32_t deltaMs = elapsed(timestampMs, lastSampleMs_);
    if (deltaMs > 20) deltaMs = 20;
    const float deltaSeconds = deltaMs * 0.001f;

    angularTravelDeg_ += gyroMagnitude * deltaSeconds;
    signedRotationX_ += gyroX * deltaSeconds;
    signedRotationY_ += gyroY * deltaSeconds;
    signedRotationZ_ += gyroZ * deltaSeconds;
    gyroEnergyXX_ += gyroX * gyroX * deltaSeconds;
    gyroEnergyXY_ += gyroX * gyroY * deltaSeconds;
    gyroEnergyXZ_ += gyroX * gyroZ * deltaSeconds;
    gyroEnergyYY_ += gyroY * gyroY * deltaSeconds;
    gyroEnergyYZ_ += gyroY * gyroZ * deltaSeconds;
    gyroEnergyZZ_ += gyroZ * gyroZ * deltaSeconds;

    if (deltaSeconds > 0.0f) {
      const float jerk = fabsf(accelMagnitude - previousAccelMagnitudeG_) /
                         deltaSeconds;
      peakJerkGps_ = max(peakJerkGps_, jerk);
    }
    previousAccelMagnitudeG_ = accelMagnitude;
    lastSampleMs_ = timestampMs;

    if (gyroMagnitude > peakGyroDps_) {
      peakGyroDps_ = gyroMagnitude;
      peakGyroMs_ = timestampMs;
    }
    peakAccelG_ = max(peakAccelG_, accelMagnitude);
    if (dynamicAccel > peakDynamicAccelG_) {
      peakDynamicAccelG_ = dynamicAccel;
      peakAccelMs_ = timestampMs;
    }
  }

  float directionConsistency() const {
    if (angularTravelDeg_ <= 0.001f) return 0.0f;
    const float netRotation = sqrtf(signedRotationX_ * signedRotationX_ +
                                    signedRotationY_ * signedRotationY_ +
                                    signedRotationZ_ * signedRotationZ_);
    return clamp01(netRotation / angularTravelDeg_);
  }

  float dominantAxisRatio() const {
    const float total = gyroEnergyXX_ + gyroEnergyYY_ + gyroEnergyZZ_;
    if (total <= 0.001f) return 0.0f;

    // Power iteration finds the principal rotation axis of the symmetric
    // gyro-energy matrix. Unlike choosing X/Y/Z, this is independent of how
    // the sensor PCB is rotated inside the handle.
    float axisX = 0.0f;
    float axisY = 0.0f;
    float axisZ = 0.0f;
    if (gyroEnergyXX_ >= gyroEnergyYY_ && gyroEnergyXX_ >= gyroEnergyZZ_) {
      axisX = 1.0f;
    } else if (gyroEnergyYY_ >= gyroEnergyZZ_) {
      axisY = 1.0f;
    } else {
      axisZ = 1.0f;
    }
    for (uint8_t iteration = 0; iteration < 6; ++iteration) {
      const float nextX = gyroEnergyXX_ * axisX + gyroEnergyXY_ * axisY +
                          gyroEnergyXZ_ * axisZ;
      const float nextY = gyroEnergyXY_ * axisX + gyroEnergyYY_ * axisY +
                          gyroEnergyYZ_ * axisZ;
      const float nextZ = gyroEnergyXZ_ * axisX + gyroEnergyYZ_ * axisY +
                          gyroEnergyZZ_ * axisZ;
      const float norm = sqrtf(nextX * nextX + nextY * nextY + nextZ * nextZ);
      if (norm <= 0.001f) return 0.0f;
      axisX = nextX / norm;
      axisY = nextY / norm;
      axisZ = nextZ / norm;
    }
    const float projectedX = gyroEnergyXX_ * axisX + gyroEnergyXY_ * axisY +
                             gyroEnergyXZ_ * axisZ;
    const float projectedY = gyroEnergyXY_ * axisX + gyroEnergyYY_ * axisY +
                             gyroEnergyYZ_ * axisZ;
    const float projectedZ = gyroEnergyXZ_ * axisX + gyroEnergyYZ_ * axisY +
                             gyroEnergyZZ_ * axisZ;
    const float principalEnergy = axisX * projectedX + axisY * projectedY +
                                  axisZ * projectedZ;
    return clamp01(principalEnergy / total);
  }

  bool passesShapeFilters(uint32_t timestampMs) const {
    return elapsed(timestampMs, candidateStartMs_) >= config_.minimumDurationMs &&
           angularTravelDeg_ >= config_.minimumAngularTravelDeg &&
           (isRotationLed() || directionConsistency() >= config_.minimumDirectionConsistency) &&
           dominantAxisRatio() >= config_.minimumDominantAxisRatio &&
           (isRotationLed() || timestampDistance(peakGyroMs_, peakAccelMs_) <=
               config_.maximumPeakGapMs);
  }

  WhipRejectReason rejectionReason(uint32_t timestampMs) const {
    if (!confirmed_) return WhipRejectReason::NotConfirmed;
    if (elapsed(timestampMs, candidateStartMs_) < config_.minimumDurationMs) {
      return WhipRejectReason::TooShort;
    }
    if (angularTravelDeg_ < config_.minimumAngularTravelDeg) {
      return WhipRejectReason::LowTravel;
    }
    if (!isRotationLed() && directionConsistency() < config_.minimumDirectionConsistency) {
      return WhipRejectReason::LowDirection;
    }
    if (dominantAxisRatio() < config_.minimumDominantAxisRatio) {
      return WhipRejectReason::LowAxis;
    }
    return WhipRejectReason::PeakGap;
  }

  void recordRejection(WhipRejectReason reason, uint32_t timestampMs) {
    lastRejection_.attempt = attemptSequence_;
    lastRejection_.reason = reason;
    lastRejection_.durationMs =
        saturatedDuration(elapsed(timestampMs, candidateStartMs_));
    lastRejection_.peakAccelG = peakAccelG_;
    lastRejection_.peakGyroDps = peakGyroDps_;
    lastRejection_.angularTravelDeg = angularTravelDeg_;
    lastRejection_.directionConsistency = directionConsistency();
    lastRejection_.dominantAxisRatio = dominantAxisRatio();
    lastRejection_.peakGapMs = timestampDistance(peakGyroMs_, peakAccelMs_);
    lastRejection_.confirmed = confirmed_;
    lastRejection_.peakJerkGps = peakJerkGps_;
    lastRejection_.peakDynamicAccelG = peakDynamicAccelG_;
    rejectionPending_ = true;
  }

  void fillEvent(uint32_t timestampMs, WhipEvent& event) {
    event.sequence = ++sequence_;
    event.durationMs = saturatedDuration(elapsed(timestampMs, candidateStartMs_));
    event.peakAccelG = peakAccelG_;
    event.peakGyroDps = peakGyroDps_;
    event.angularTravelDeg = angularTravelDeg_;
    event.directionConsistency = directionConsistency();
    event.dominantAxisRatio = dominantAxisRatio();
    event.peakGapMs = timestampDistance(peakGyroMs_, peakAccelMs_);
    event.peakJerkGps = peakJerkGps_;
    event.peakDynamicAccelG = peakDynamicAccelG_;
  }

  void enterCooldown(uint32_t timestampMs) {
    state_ = State::Cooldown;
    cooldownStartMs_ = timestampMs;
    clearCandidate();
  }

  void clearCandidate() {
    candidateStartMs_ = 0;
    lastSampleMs_ = 0;
    quietStartMs_ = 0;
    peakGyroMs_ = 0;
    peakAccelMs_ = 0;
    peakAccelG_ = 0.0f;
    peakDynamicAccelG_ = 0.0f;
    peakGyroDps_ = 0.0f;
    angularTravelDeg_ = 0.0f;
    signedRotationX_ = 0.0f;
    signedRotationY_ = 0.0f;
    signedRotationZ_ = 0.0f;
    gyroEnergyXX_ = 0.0f;
    gyroEnergyXY_ = 0.0f;
    gyroEnergyXZ_ = 0.0f;
    gyroEnergyYY_ = 0.0f;
    gyroEnergyYZ_ = 0.0f;
    gyroEnergyZZ_ = 0.0f;
    previousAccelMagnitudeG_ = 0.0f;
    peakJerkGps_ = 0.0f;
    confirmationCount_ = 0;
    confirmed_ = false;
  }

  WhipDetectorConfig config_;
  State state_ = State::Idle;
  uint32_t sequence_ = 0;
  uint32_t attemptSequence_ = 0;
  uint32_t candidateStartMs_ = 0;
  uint32_t lastSampleMs_ = 0;
  uint32_t quietStartMs_ = 0;
  uint32_t cooldownStartMs_ = 0;
  uint32_t peakGyroMs_ = 0;
  uint32_t peakAccelMs_ = 0;
  float peakAccelG_ = 0.0f;
  float peakDynamicAccelG_ = 0.0f;
  float peakGyroDps_ = 0.0f;
  float angularTravelDeg_ = 0.0f;
  float signedRotationX_ = 0.0f;
  float signedRotationY_ = 0.0f;
  float signedRotationZ_ = 0.0f;
  float gyroEnergyXX_ = 0.0f;
  float gyroEnergyXY_ = 0.0f;
  float gyroEnergyXZ_ = 0.0f;
  float gyroEnergyYY_ = 0.0f;
  float gyroEnergyYZ_ = 0.0f;
  float gyroEnergyZZ_ = 0.0f;
  float previousAccelMagnitudeG_ = 0.0f;
  float peakJerkGps_ = 0.0f;
  float biasX_ = 0.0f;
  float biasY_ = 0.0f;
  float biasZ_ = 0.0f;
  uint8_t confirmationCount_ = 0;
  bool confirmed_ = false;
  WhipRejection lastRejection_ = {};
  bool rejectionPending_ = false;
};
