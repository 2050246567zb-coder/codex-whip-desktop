#include <bluefruit.h>
#include <LSM6DS3.h>
#include <PDM.h>
#include <Wire.h>
#include <Adafruit_LittleFS.h>
#include <InternalFileSystem.h>

#include "voice_audio.h"
#include "whip_detector.h"
#include "power_idle.h"
#include "host_profile.h"
#include "ble_transport.h"

namespace {

constexpr char kFirmwareVersion[] = "0.7.5";
constexpr char kDeviceName[] = "CodexWhip";
constexpr uint32_t kSampleRateHz = 416;
constexpr uint32_t kSamplePeriodUs = 1000000UL / kSampleRateHz;
constexpr uint8_t kStatusRegister = LSM6DS3_ACC_GYRO_STATUS_REG;
constexpr uint8_t kFirstMotionRegister = LSM6DS3_ACC_GYRO_OUTX_L_G;
constexpr uint8_t kGyroReadyMask = 0x02;
constexpr uint8_t kAccelReadyMask = 0x01;
constexpr uint32_t kRawStreamPeriodMs = 10;
constexpr uint8_t kRawBatchSamples = 4;
// RAW5: uint16 elapsed-ms + six int16 values (0.1 dps, 0.001 g).
// RAW4's 16 dps steps discarded slow wrist rotation before it reached the PC.
constexpr size_t kRawSampleBytes = 14;
constexpr uint32_t kVoiceSampleRateHz = 16000;
// 300 PCM samples encode to at most 150 ADPCM bytes. Including the AUD1
// header/base64/newline, the complete record stays below a 244-byte payload,
// so an MTU 247 connection normally needs one notification instead of two.
constexpr size_t kVoicePcmSamples = 300;
constexpr size_t kVoicePdmBufferSamples = 640;
// Keep 800 ms of PCM so a short Windows BLE notification stall does not force
// an otherwise healthy recording to abort.
constexpr size_t kVoiceRingSamples = 12800;
constexpr uint32_t kVoiceNoiseIgnoreMs = 140;
constexpr uint32_t kVoiceNoiseCalibrationMs = 340;
constexpr uint32_t kVoiceNoSpeechTimeoutMs = 4000;
constexpr uint8_t kChargeStatusPin = 23;  // P0.17, active-low BQ25100 ~CHG.
constexpr uint32_t kBatteryReportPeriodMs = 30000;
constexpr uint32_t kChargeDebounceMs = 80;
// LSM6DS3TR-C hardware double-tap engine. Register values follow ST AN5130
// section 5.5.5: all axes, slope threshold, Shock/Quiet/Duration state machine.
// At the active +/-16 g range, TAP_THS=2 is a 1.0 g per-axis slope threshold.
constexpr uint8_t kTapSourceRegister = 0x1C;
constexpr uint8_t kTapConfigRegister = 0x58;
constexpr uint8_t kTapThresholdRegister = 0x59;
constexpr uint8_t kTapDurationRegister = 0x5A;
constexpr uint8_t kWakeThresholdRegister = 0x5B;
constexpr uint8_t kMd1ConfigRegister = 0x5E;
constexpr uint8_t kDoubleTapSourceMask = 0x50;  // TAP_IA | DOUBLE_TAP.
constexpr uint32_t kTapPollPeriodMs = 8;
constexpr float kTapThresholdStepG = 0.5f;  // FS_XL / 32 at +/-16 g.
constexpr uint8_t kTapThresholdMaximumCode = 24;  // Product UI caps at 12 g.

LSM6DS3 imu(I2C_MODE, 0x6A);
BLEDis deviceInfo;
BLEUart bleUart;
PowerIdle powerIdle;
bool powerEnabled = false;
bool powerSleeping = false;
bool powerFsReady = false;
bool powerHardwareFault = false;
bool powerSettling = false;
uint8_t activeXl = 0, activeGyro = 0, activeCtrl6 = 0;
uint32_t powerResumeAt = 0, powerLastSampleAt = 0;
constexpr char kPowerFile[] = "/whip-power";

WhipDetectorConfig makeLearningConfig() {
  WhipDetectorConfig config;
  config.startGyroDps = 180.0f;
  config.startDynamicAccelG = 0.25f;
  config.confirmGyroDps = 220.0f;
  config.confirmDynamicAccelG = 0.32f;
  config.hardDynamicAccelG = 0.65f;
  config.hardAccelMinGyroDps = 150.0f;
  config.confirmSamples = 1;
  config.minimumAngularTravelDeg = 8.0f;
  config.minimumDirectionConsistency = 0.0f;
  config.minimumDominantAxisRatio = 0.0f;
  config.maximumPeakGapMs = 1000;
  config.minimumDurationMs = 20;
  config.quietGyroDps = 180.0f;
  config.quietDynamicAccelG = 0.25f;
  config.quietTimeMs = 45;
  config.candidateTimeoutMs = 400;
  config.maxEventMs = 1300;
  config.cooldownMs = 500;
  return config;
}

WhipDetectorConfig detectorConfig;
WhipDetector detector(detectorConfig);
WhipDetector learningDetector(makeLearningConfig());

bool armed = true;
bool learningMode = false;
uint32_t learningSampleSequence = 0;
bool learningSamplePending = false;
WhipEvent pendingLearningEvent = {};
const char* pendingLearningResult = "EMPTY";
uint32_t nextSampleUs = 0;
String commandBuffer;
bool rawStreaming = false;
bool rawSuppressEvents = false;
uint32_t nextRawSampleMs = 0;
uint8_t rawBatchCount = 0;
uint8_t rawBatch[kRawBatchSamples * kRawSampleBytes] = {0};
uint32_t rawBatchStartMs = 0;
uint32_t rawBatchSequence = 0;
WhipBleTransport transport(bleUart);
HostProfile activeHost = hostProfile(HostSystem::Compatible);
std::atomic<bool> linkReset{false};
uint32_t lastStreamSampleAt = 0;
uint32_t longestStreamGapMs = 0;
float streamBiasX = 0.0f;
float streamBiasY = 0.0f;
float streamBiasZ = 0.0f;
bool voiceEnabled = false;
bool voiceRecording = false;
bool voiceSavedRawStreaming = false;
bool voiceSavedRawSuppressEvents = false;
uint32_t voiceSession = 0;
uint32_t voiceChunkSequence = 0;
uint32_t voiceTotalSamples = 0;
uint32_t voiceStartedAtMs = 0;
uint32_t voiceLastSpeechAtMs = 0;
uint16_t voiceNoiseLevel = 0;
bool voiceSpeechDetected = false;
uint8_t voiceAdpcmStepIndex = 0;
uint16_t voiceSilenceMs = 1200;
uint16_t voiceMaximumRecordingMs = 15000;
int16_t voiceRing[kVoiceRingSamples] = {0};
int16_t voiceCapture[kVoicePdmBufferSamples] = {0};
volatile size_t voiceRingRead = 0;
volatile size_t voiceRingWrite = 0;
volatile size_t voiceRingCount = 0;
volatile uint32_t voiceLastPdmAtMs = 0;
volatile bool voiceRingOverflow = false;
uint32_t nextBatteryReportAt = 0;
bool chargingState = false;
bool chargingCandidate = false;
uint32_t chargingCandidateSince = 0;
bool hardwareTapReady = false;
uint8_t hardwareTapThresholdCode = 2;
uint32_t nextHardwareTapPollAt = 0;

void sendLine(const String& line);

bool writeImuRegisterVerified(uint8_t reg, uint8_t value) {
  uint8_t actual = 0;
  return imu.writeRegister(reg, value) == IMU_SUCCESS &&
         imu.readRegister(&actual, reg) == IMU_SUCCESS && actual == value;
}

float hardwareTapThresholdG() {
  return hardwareTapThresholdCode * kTapThresholdStepG;
}

bool setHardwareTapThreshold(float minimumG) {
  if (!isfinite(minimumG) || minimumG < kTapThresholdStepG || minimumG > 12.0f) {
    return false;
  }
  const uint8_t code = static_cast<uint8_t>(constrain(
      static_cast<int>(ceilf(minimumG / kTapThresholdStepG)),
      1, kTapThresholdMaximumCode));
  uint8_t tapThreshold = 0;
  if (imu.readRegister(&tapThreshold, kTapThresholdRegister) != IMU_SUCCESS) {
    return false;
  }
  if (!writeImuRegisterVerified(
          kTapThresholdRegister,
          static_cast<uint8_t>((tapThreshold & 0xE0) | code))) {
    return false;
  }
  hardwareTapThresholdCode = code;
  uint8_t ignored = 0;
  imu.readRegister(&ignored, kTapSourceRegister);  // Clear stale latched tap.
  return true;
}

bool configureHardwareDoubleTap() {
  uint8_t tapThreshold = 0;
  uint8_t wakeThreshold = 0;
  uint8_t md1 = 0;
  if (imu.readRegister(&tapThreshold, kTapThresholdRegister) != IMU_SUCCESS ||
      imu.readRegister(&wakeThreshold, kWakeThresholdRegister) != IMU_SUCCESS ||
      imu.readRegister(&md1, kMd1ConfigRegister) != IMU_SUCCESS) {
    return false;
  }
  bool ok = writeImuRegisterVerified(kTapConfigRegister, 0x8E);
  ok = writeImuRegisterVerified(
           kTapThresholdRegister,
           static_cast<uint8_t>((tapThreshold & 0xE0) |
                                hardwareTapThresholdCode)) && ok;
  // ST AN5130 double-tap state machine: SHOCK=3 (~57.7 ms),
  // QUIET=3 (~28.8 ms) rejects contact bounce, and DUR=13 gives a fixed
  // 1.0-second window at 416 Hz. There is no desktop interval detector.
  ok = writeImuRegisterVerified(kTapDurationRegister, 0xDF) && ok;
  ok = writeImuRegisterVerified(
           kWakeThresholdRegister,
           static_cast<uint8_t>(wakeThreshold | 0x80)) && ok;
  ok = writeImuRegisterVerified(
           kMd1ConfigRegister,
           static_cast<uint8_t>(md1 | 0x08)) && ok;
  uint8_t ignored = 0;
  imu.readRegister(&ignored, kTapSourceRegister);  // Clear a stale source.
  nextHardwareTapPollAt = millis();
  return ok;
}

uint16_t readBatteryMillivolts() {
  // Seeed exposes VBAT through P0.31. P0.14 must remain LOW while charging
  // so the divider stays enabled and the ADC input never sees raw LiPo voltage.
  analogReference(AR_INTERNAL_3_0);
  analogReadResolution(12);
  delayMicroseconds(250);
  analogRead(PIN_VBAT);  // Discard the first conversion after reference change.
  uint32_t sum = 0;
  constexpr uint8_t kSamples = 8;
  for (uint8_t sample = 0; sample < kSamples; ++sample) {
    sum += analogRead(PIN_VBAT);
    delayMicroseconds(120);
  }
  analogReference(AR_DEFAULT);
  analogReadResolution(10);

  const float pinMillivolts = (sum / static_cast<float>(kSamples)) * 3000.0f / 4095.0f;
  // XIAO revisions use either 1:1 or approximately 1M:510K dividers. Their
  // ADC voltage ranges do not overlap for a normal single-cell LiPo, allowing
  // safe automatic compensation without a user-visible voltage setting.
  const float dividerCompensation = pinMillivolts > 1500.0f ? 2.0f : (1510.0f / 510.0f);
  return static_cast<uint16_t>(pinMillivolts * dividerCompensation + 0.5f);
}

uint8_t batteryPercent(uint16_t millivolts) {
  struct Point { uint16_t millivolts; uint8_t percent; };
  constexpr Point curve[] = {
      {3300, 0}, {3400, 5}, {3600, 15}, {3700, 30}, {3800, 50},
      {3900, 65}, {4000, 80}, {4100, 90}, {4200, 100},
  };
  if (millivolts <= curve[0].millivolts) return 0;
  for (size_t index = 1; index < sizeof(curve) / sizeof(curve[0]); ++index) {
    if (millivolts <= curve[index].millivolts) {
      const Point& lower = curve[index - 1];
      const Point& upper = curve[index];
      return lower.percent + static_cast<uint8_t>(
          (millivolts - lower.millivolts) * (upper.percent - lower.percent) /
          (upper.millivolts - lower.millivolts));
    }
  }
  return 100;
}

void reportBattery() {
  const uint8_t percent = batteryPercent(readBatteryMillivolts());
  sendLine("BATTERY," + String(percent) + "," + String(chargingState ? 1 : 0));
  nextBatteryReportAt = millis() + kBatteryReportPeriodMs;
}

void pollChargeStatus() {
  const uint32_t now = millis();
  const bool rawCharging = digitalRead(kChargeStatusPin) == LOW;
  if (rawCharging != chargingCandidate) {
    chargingCandidate = rawCharging;
    chargingCandidateSince = now;
    return;
  }
  if (rawCharging != chargingState && now - chargingCandidateSince >= kChargeDebounceMs) {
    chargingState = rawCharging;
    if (Bluefruit.connected() && !voiceRecording) reportBattery();
  }
}

int16_t readInt16LE(const uint8_t* bytes) {
  return static_cast<int16_t>(static_cast<uint16_t>(bytes[0]) |
                              (static_cast<uint16_t>(bytes[1]) << 8));
}

bool readMotion(MotionSample& sample) {
  uint8_t status = 0;
  if (imu.readRegister(&status, kStatusRegister) != IMU_SUCCESS) {
    return false;
  }
  if ((status & (kGyroReadyMask | kAccelReadyMask)) !=
      (kGyroReadyMask | kAccelReadyMask)) {
    return false;
  }

  uint8_t raw[12] = {0};  
  if (imu.readRegisterRegion(raw, kFirstMotionRegister, sizeof(raw)) != IMU_SUCCESS) {
    return false;
  }

  sample.timestampMs = millis();
  sample.gyroX = imu.calcGyro(readInt16LE(&raw[0]));
  sample.gyroY = imu.calcGyro(readInt16LE(&raw[2]));
  sample.gyroZ = imu.calcGyro(readInt16LE(&raw[4]));
  sample.accelX = imu.calcAccel(readInt16LE(&raw[6]));
  sample.accelY = imu.calcAccel(readInt16LE(&raw[8]));
  sample.accelZ = imu.calcAccel(readInt16LE(&raw[10]));
  return true;
}

void pollHardwareDoubleTap(uint32_t now) {
  if (!hardwareTapReady || !voiceEnabled || voiceRecording) return;
  if (static_cast<int32_t>(now - nextHardwareTapPollAt) < 0) return;
  nextHardwareTapPollAt = now + kTapPollPeriodMs;

  uint8_t source = 0;
  if (imu.readRegister(&source, kTapSourceRegister) == IMU_SUCCESS &&
      (source & kDoubleTapSourceMask) == kDoubleTapSourceMask) {
    sendLine("TAP2," + String(now) + "," + String(source));
  }
}

void serialDiagnostic(const String& line) {
  // An attached USB cable without a serial reader must not stall sampling.
  if (Serial && Serial.availableForWrite() >= static_cast<int>(line.length() + 2)) {
    Serial.print(line);
    Serial.print("\r\n");
  }
}

bool sendBlePayload(const uint8_t* data, size_t length) {
  return transport.enqueue(data, length);
}

void sendLine(const String& line) {
  serialDiagnostic(line);
  if (!Bluefruit.connected()) return;
  String payload = line + '\n';
  sendBlePayload(reinterpret_cast<const uint8_t*>(payload.c_str()), payload.length());
}

String encodeBase64(const uint8_t* data, size_t length) {
  static const char alphabet[] =
      "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
  String encoded;
  encoded.reserve(((length + 2) / 3) * 4);
  for (size_t offset = 0; offset < length; offset += 3) {
    const uint32_t first = data[offset];
    const uint32_t second = offset + 1 < length ? data[offset + 1] : 0;
    const uint32_t third = offset + 2 < length ? data[offset + 2] : 0;
    const uint32_t value = (first << 16) | (second << 8) | third;
    encoded += alphabet[(value >> 18) & 0x3F];
    encoded += alphabet[(value >> 12) & 0x3F];
    encoded += offset + 1 < length ? alphabet[(value >> 6) & 0x3F] : '=';
    encoded += offset + 2 < length ? alphabet[value & 0x3F] : '=';
  }
  return encoded;
}

void onVoicePdmData() {
  const int availableBytes = PDM.available();
  const size_t requested =
      min(static_cast<size_t>(availableBytes), sizeof(voiceCapture));
  const int bytesRead = PDM.read(voiceCapture, requested);
  const size_t capturedSamples =
      bytesRead > 0 ? static_cast<size_t>(bytesRead) / sizeof(int16_t) : 0;
  if (voiceRingCount + capturedSamples > kVoiceRingSamples) {
    voiceRingOverflow = true;
    return;
  }
  for (size_t index = 0; index < capturedSamples; ++index) {
    voiceRing[voiceRingWrite] = voiceCapture[index];
    voiceRingWrite = (voiceRingWrite + 1) % kVoiceRingSamples;
  }
  voiceRingCount += capturedSamples;
  if (capturedSamples > 0) voiceLastPdmAtMs = millis();
}

void restoreMotionAfterVoice() {
  rawStreaming = voiceSavedRawStreaming;
  rawSuppressEvents = voiceSavedRawSuppressEvents;
  nextRawSampleMs = 0;
  lastStreamSampleAt = 0;
  rawBatchCount = 0;
  transport.discardMotion();
  detector.reset();
}

bool sendVoicePayload(const String& line) {
  if (!Bluefruit.connected()) return false;
  String payload = line;
  payload += '\n';
  if (payload.length() > 256) {
    serialDiagnostic("WARN,VOICE_LINE_TOO_LONG");
    return false;
  }
  return transport.enqueueAudio(reinterpret_cast<const uint8_t*>(payload.c_str()),
                                payload.length());
}

void stopVoiceRecording(const char* reason) {
  if (!voiceRecording) return;
  voiceRecording = false;
  PDM.end();
  sendLine("VOICE,END," + String(voiceSession) + "," +
           String(voiceTotalSamples) + "," + String(reason));
  restoreMotionAfterVoice();
}

void startVoiceRecording(uint16_t silenceMs, uint16_t maximumRecordingMs) {
  if (!voiceEnabled) {
    sendLine("VOICE,ERROR,DISABLED");
    return;
  }
  if (!Bluefruit.connected()) return;
  if (voiceRecording) {
    sendLine("VOICE,ERROR,ALREADY_RECORDING");
    return;
  }

  voiceSavedRawStreaming = rawStreaming;
  voiceSavedRawSuppressEvents = rawSuppressEvents;
  rawStreaming = false;
  rawSuppressEvents = true;
  rawBatchCount = 0;
  transport.discardMotion();

  voiceSilenceMs = constrain(silenceMs, 400, 4000);
  voiceMaximumRecordingMs = constrain(maximumRecordingMs, 3000, 30000);
  voiceChunkSequence = 0;
  voiceTotalSamples = 0;
  voiceStartedAtMs = millis();
  voiceLastSpeechAtMs = voiceStartedAtMs;
  voiceNoiseLevel = UINT16_MAX;
  voiceSpeechDetected = false;
  voiceAdpcmStepIndex = 0;
  voiceRingRead = 0;
  voiceRingWrite = 0;
  voiceRingCount = 0;
  voiceRingOverflow = false;
  voiceLastPdmAtMs = voiceStartedAtMs;

  PDM.setBufferSize(kVoicePdmBufferSamples * sizeof(int16_t));
  PDM.onReceive(onVoicePdmData);
  ++voiceSession;
  sendLine("VOICE,START," + String(voiceSession) + "," +
           String(kVoiceSampleRateHz) + ",IMA_ADPCM4");
  if (!PDM.begin(1, kVoiceSampleRateHz)) {
    restoreMotionAfterVoice();
    sendLine("VOICE,END," + String(voiceSession) + ",0,MIC_START_FAILED");
    return;
  }
  PDM.setGain(30);
  voiceRecording = true;
}

void processVoiceAudio() {
  if (!voiceRecording) return;
  const uint32_t now = millis();
  if (voiceRingOverflow) {
    stopVoiceRecording("BUFFER_OVERFLOW");
    return;
  }

  if (now - voiceLastPdmAtMs > 1000) {
    stopVoiceRecording("MIC_STALLED");
    return;
  }
  if (voiceRingCount < kVoicePcmSamples || !transport.audioReady()) return;

  int16_t pcm[kVoicePcmSamples] = {0};
  NVIC_DisableIRQ(PDM_IRQn);
  if (voiceRingCount < kVoicePcmSamples) {
    NVIC_EnableIRQ(PDM_IRQn);
    return;
  }
  for (size_t index = 0; index < kVoicePcmSamples; ++index) {
    pcm[index] = voiceRing[voiceRingRead];
    voiceRingRead = (voiceRingRead + 1) % kVoiceRingSamples;
  }
  voiceRingCount -= kVoicePcmSamples;
  NVIC_EnableIRQ(PDM_IRQn);
  const size_t sampleCount = kVoicePcmSamples;

  uint32_t absoluteSum = 0;
  for (size_t index = 0; index < sampleCount; ++index) {
    const int32_t value = pcm[index];
    absoluteSum += static_cast<uint32_t>(value < 0 ? -value : value);
  }
  const uint16_t level = static_cast<uint16_t>(absoluteSum / sampleCount);
  const uint32_t elapsed = now - voiceStartedAtMs;
  if (elapsed <= kVoiceNoiseIgnoreMs) {
    // The nRF PDM filter has a large startup transient. Do not let that set
    // the room-noise baseline or it will suppress the following utterance.
  } else if (elapsed <= kVoiceNoiseCalibrationMs) {
    voiceNoiseLevel = min(voiceNoiseLevel, level);
  } else {
    const uint16_t baseline =
        voiceNoiseLevel == UINT16_MAX ? static_cast<uint16_t>(100)
                                      : voiceNoiseLevel;
    const uint16_t speechThreshold =
        max(static_cast<uint16_t>(180),
            static_cast<uint16_t>(min(12000UL,
                static_cast<uint32_t>(baseline) * 2UL)));
    if (level >= speechThreshold) {
      voiceSpeechDetected = true;
      voiceLastSpeechAtMs = now;
    }
  }

  ImaAdpcmBlock block;
  const size_t encodedBytes =
      encodeImaAdpcmBlock(pcm, sampleCount, voiceAdpcmStepIndex, block);
  if (encodedBytes == 0) {
    stopVoiceRecording("ENCODE_ERROR");
    return;
  }
  const String encoded = encodeBase64(block.data, encodedBytes);
  const String line =
      "AUD1," + String(voiceSession) + "," + String(voiceChunkSequence) +
      "," + String(sampleCount) + "," + String(block.predictor) + "," +
      String(block.stepIndex) + "," + encoded;
  if (!sendVoicePayload(line)) {
    serialDiagnostic(
        "WARN,VOICE_TX_FAILED," + String(voiceChunkSequence) + ",MTU," +
        String(Bluefruit.Connection(Bluefruit.connHandle()) ?
               Bluefruit.Connection(Bluefruit.connHandle())->getMtu() : 0) +
        ",RING," + String(voiceRingCount));
    stopVoiceRecording("TX_FAILED");
    return;
  }
  ++voiceChunkSequence;
  voiceTotalSamples += sampleCount;

  if (voiceSpeechDetected && now - voiceLastSpeechAtMs >= voiceSilenceMs) {
    stopVoiceRecording("SILENCE");
  } else if (!voiceSpeechDetected && elapsed >= kVoiceNoSpeechTimeoutMs) {
    stopVoiceRecording("NO_SPEECH");
  } else if (elapsed >= voiceMaximumRecordingMs) {
    stopVoiceRecording(voiceSpeechDetected ? "TIMEOUT" : "NO_SPEECH");
  }
}

int16_t quantize16(float value, float scale) {
  const float scaled = roundf(value * scale);
  if (scaled > 32767.0f) return 32767;
  if (scaled < -32768.0f) return -32768;
  return static_cast<int16_t>(scaled);
}

void pack16(uint8_t* destination, uint16_t value) {
  destination[0] = value & 0xff;
  destination[1] = (value >> 8) & 0xff;
}

uint8_t motionBatchSamples() {
  BLEConnection* connection = Bluefruit.Connection(Bluefruit.connHandle());
  return hostBatchSamples(activeHost, connection ? connection->getConnectionInterval() : 12);
}

void streamMotion(const MotionSample& sample) {
  if (!rawStreaming || !Bluefruit.connected()) return;
  // Decimate by real time, not by a count that assumes every 416 Hz polling
  // attempt succeeded. BLE/RTOS and independent data-ready timing can skip
  // reads; four successful reads previously stretched PC samples to ~16 ms.
  if (static_cast<int32_t>(sample.timestampMs - nextRawSampleMs) < 0) return;
  if (sample.timestampMs - nextRawSampleMs > kRawStreamPeriodMs * 2) {
    nextRawSampleMs = sample.timestampMs + kRawStreamPeriodMs;
  } else {
    nextRawSampleMs += kRawStreamPeriodMs;
  }

  if (lastStreamSampleAt) {
    const uint32_t gap = sample.timestampMs - lastStreamSampleAt;
    if (gap > longestStreamGapMs) longestStreamGapMs = gap;
  }
  lastStreamSampleAt = sample.timestampMs;
  if (rawBatchCount && sample.timestampMs - rawBatchStartMs > 200) rawBatchCount = 0;
  if (rawBatchCount == 0) rawBatchStartMs = sample.timestampMs;
  const size_t offset = rawBatchCount * kRawSampleBytes;
  pack16(rawBatch + offset, sample.timestampMs - rawBatchStartMs);
  pack16(rawBatch + offset + 2, quantize16(sample.gyroX - streamBiasX, 10.0f));
  pack16(rawBatch + offset + 4, quantize16(sample.gyroY - streamBiasY, 10.0f));
  pack16(rawBatch + offset + 6, quantize16(sample.gyroZ - streamBiasZ, 10.0f));
  pack16(rawBatch + offset + 8, quantize16(sample.accelX, 1000.0f));
  pack16(rawBatch + offset + 10, quantize16(sample.accelY, 1000.0f));
  pack16(rawBatch + offset + 12, quantize16(sample.accelZ, 1000.0f));
  ++rawBatchCount;

  if (rawBatchCount < motionBatchSamples()) return;
  const String payload = encodeBase64(rawBatch, rawBatchCount * kRawSampleBytes);
  ++rawBatchSequence; // Replaced, unsent batches remain visible as sequence gaps.
  const String line = "RAW5," + String(rawBatchSequence) + "," +
                      String(rawBatchStartMs) + "," + payload + "\n";
  transport.enqueue(reinterpret_cast<const uint8_t*>(line.c_str()), line.length(), true);
  rawBatchCount = 0;
}

void setRawStreaming(bool enabled, bool suppressEvents) {
  rawStreaming = enabled;
  rawSuppressEvents = enabled && suppressEvents;
  nextRawSampleMs = 0;
  lastStreamSampleAt = 0;
  rawBatchCount = 0;
  if (!enabled) {
    transport.discardMotion();
  }
  detector.reset();
  sendLine("RAW," + String(enabled ? (suppressEvents ? 2 : 1) : 0));
}

void sendWhipEvent(const WhipEvent& event) {
  char line[160];
  snprintf(line, sizeof(line),
           "WHIP2,%lu,%.1f,%.2f,%u,%.1f,%.3f,%.3f,%u,%.1f",
           static_cast<unsigned long>(event.sequence), event.peakGyroDps,
           event.peakAccelG, event.durationMs, event.angularTravelDeg,
           event.directionConsistency, event.dominantAxisRatio,
           event.peakGapMs, event.peakJerkGps);
  sendLine(String(line));
}

const char* rejectionName(WhipRejectReason reason) {
  switch (reason) {
    case WhipRejectReason::NotConfirmed:
      return "NOT_CONFIRMED";
    case WhipRejectReason::TooShort:
      return "TOO_SHORT";
    case WhipRejectReason::LowTravel:
      return "LOW_TRAVEL";
    case WhipRejectReason::LowDirection:
      return "LOW_DIRECTION";
    case WhipRejectReason::LowAxis:
      return "LOW_AXIS";
    case WhipRejectReason::PeakGap:
      return "PEAK_GAP";
    case WhipRejectReason::NoQuiet:
      return "NO_QUIET";
  }
  return "UNKNOWN";
}

void sendWhipRejection(const WhipRejection& rejection) {
  char line[176];
  snprintf(line, sizeof(line),
           "REJECT2,%lu,%s,%.1f,%.2f,%u,%.1f,%.3f,%.3f,%u,%u",
           static_cast<unsigned long>(rejection.attempt),
           rejectionName(rejection.reason), rejection.peakGyroDps,
           rejection.peakAccelG, rejection.durationMs,
           rejection.angularTravelDeg, rejection.directionConsistency,
           rejection.dominantAxisRatio, rejection.peakGapMs,
           rejection.confirmed ? 1 : 0);
  sendLine(String(line));
}

void sendLearningSample(const char* result, const WhipEvent& event) {
  char line[196];
  snprintf(line, sizeof(line),
           "SAMPLE3,%lu,%s,%.1f,%.3f,%u,%.1f,%.3f,%.3f,%u,%.1f",
           static_cast<unsigned long>(++learningSampleSequence), result,
           event.peakGyroDps, event.peakDynamicAccelG, event.durationMs,
           event.angularTravelDeg, event.directionConsistency,
           event.dominantAxisRatio, event.peakGapMs, event.peakJerkGps);
  sendLine(String(line));
}

void rememberLearningSample(const char* result, const WhipEvent& event) {
  pendingLearningEvent = event;
  pendingLearningResult = result;
  learningSamplePending = true;
}

void rememberLearningSample(const WhipRejection& rejection) {
  WhipEvent event = {};
  event.durationMs = rejection.durationMs;
  event.peakAccelG = rejection.peakAccelG;
  event.peakGyroDps = rejection.peakGyroDps;
  event.angularTravelDeg = rejection.angularTravelDeg;
  event.directionConsistency = rejection.directionConsistency;
  event.dominantAxisRatio = rejection.dominantAxisRatio;
  event.peakGapMs = rejection.peakGapMs;
  event.peakJerkGps = rejection.peakJerkGps;
  event.peakDynamicAccelG = rejection.peakDynamicAccelG;
  rememberLearningSample(rejectionName(rejection.reason), event);
}

bool buildSyntheticWhip(WhipDetector& target, WhipEvent& event,
                        uint16_t activeSamples = 12, bool rebound = false) {
  target.reset();
  MotionSample sample = {};
  sample.timestampMs = 1000;
  sample.accelX = 2.0f;
  sample.gyroZ = 600.0f;
  target.update(sample, event);

  bool detected = false;
  for (uint16_t index = 0; index < activeSamples && !detected; ++index) {
    sample.timestampMs += 5;
    sample.accelX = 2.5f + (index == activeSamples / 2 ? 1.0f : 0.0f);
    const float direction = rebound && index >= activeSamples / 2 ? -1.0f : 1.0f;
    sample.gyroZ = direction *
                   (1050.0f + (index == activeSamples / 2 ? 250.0f : 0.0f));
    detected = target.update(sample, event);
  }
  for (uint8_t index = 0; index < 12 && !detected; ++index) {
    sample.timestampMs += 5;
    sample.accelX = 1.0f;
    sample.gyroZ = 0.0f;
    detected = target.update(sample, event);
  }

  return detected;
}

void sendSyntheticWhipEvent(uint16_t activeSamples = 12, bool rebound = false) {
  WhipEvent event;
  const bool detected = buildSyntheticWhip(detector, event, activeSamples, rebound);

  if (detected) {
    sendWhipEvent(event);
  } else {
    sendLine("TEST,FAILED");
  }
}

void captureSyntheticLearningSample() {
  if (!learningMode) {
    sendLine("ERR,LEARN_NOT_STARTED");
    return;
  }
  WhipEvent event;
  if (buildSyntheticWhip(learningDetector, event)) {
    rememberLearningSample("CAPTURED", event);
    sendLine("TEST,LEARN,READY");
  } else {
    sendLine("TEST,LEARN,FAILED");
  }
}

void runDetectorSelfTest() {
  // Diagnostics only: never emit WHIP or change the live detector/config.
  // Test the actual compiled state machine both with factory and user values.
  const char* names[] = {"WRIST", "REBOUND", "SLOW", "TABLE", "SPIKE", "SHAKE"};
  uint8_t passed = 0;
  for (uint8_t profile = 0; profile < 2; ++profile) {
    for (uint8_t kind = 0; kind < 6; ++kind) {
      WhipDetector tested(profile ? detectorConfig : WhipDetectorConfig());
      WhipEvent event;
      uint8_t detections = 0;
      for (uint16_t t = 0; t < 2400; t += 5) {
        MotionSample sample = {};
        sample.timestampMs = t + 1000;
        sample.accelZ = 1.0f;
        if (kind <= 2 && t >= 100 && t < 400) {
          const float phase = (t - 100) / 300.0f;
          sample.gyroZ = (kind == 2 ? 35.0f : 1100.0f) * sinf(phase * PI);
          if (kind == 1 && phase >= 0.5f) sample.gyroZ *= -1.0f;
        } else if (kind == 3 && t >= 150 && t < 165) {
          sample.accelZ = 4.5f;
        } else if (kind == 4 && t == 150) {
          sample.gyroZ = 1800.0f;
        } else if (kind == 5 && t >= 100 && t < 1900) {
          sample.gyroZ = (t / 40) % 2 ? 1100.0f : -1100.0f;
        }
        if (tested.update(sample, event)) ++detections;
      }
      const bool ok = detections == (kind < 2 ? 1 : 0);
      if (ok) ++passed;
      sendLine("SELFTEST," + String(profile ? "USER," : "DEFAULT,") + names[kind] +
               "," + String(ok ? "PASS" : "FAIL") + "," + String(detections));
    }
  }
  sendLine("SELFTEST,DONE," + String(passed) + ",12");
}

bool requestHostConnection(uint16_t handle, const HostProfile& profile, bool sleeping) {
  // Apple-compatible active range: 15-30 ms, latency 0, supervision 4 s.
  // Sleeping range: 120-135 ms. Units are 1.25 ms and 10 ms respectively.
  ble_gap_conn_params_t params = {};
  const HostLinkParameters requested = hostLinkParameters(profile, sleeping);
  params.min_conn_interval = requested.minimum;
  params.max_conn_interval = requested.maximum;
  params.slave_latency = requested.latency;
  params.conn_sup_timeout = requested.supervisionTimeout;
  return sd_ble_gap_conn_param_update(handle, &params) == NRF_SUCCESS;
}

void onBleConnect(uint16_t connectionHandle) {
  transport.open(connectionHandle);
  linkReset.store(true);
  BLEConnection* connection = Bluefruit.Connection(connectionHandle);
  if (!connection) return;
  connection->requestPHY();
  connection->requestDataLengthUpdate();
  connection->requestMtuExchange(247);
  requestHostConnection(connectionHandle, hostProfile(HostSystem::Compatible), false);
}

void onBleDisconnect(uint16_t, uint8_t) {
  transport.close();
  linkReset.store(true);
}

void reportLink() {
  BLEConnection* connection = Bluefruit.Connection(Bluefruit.connHandle());
  if (!connection) return;
  sendLine("LINK," + String(activeHost.name) + "," +
           String(connection->getConnectionInterval() * 1.25f, 2) + "," +
           String(connection->getMtu()) + "," + String(longestStreamGapMs) + "," +
           String(transport.longestWriteMs()) + "," + String(transport.replacedMotion()) +
           "," + String(motionBatchSamples()));
}

void advertise() {
  Bluefruit.Advertising.stop();
  Bluefruit.Advertising.clearData();
  Bluefruit.ScanResponse.clearData();
  Bluefruit.Advertising.addFlags(BLE_GAP_ADV_FLAGS_LE_ONLY_GENERAL_DISC_MODE);
  Bluefruit.Advertising.addTxPower();
  Bluefruit.Advertising.addService(bleUart);
  Bluefruit.ScanResponse.addName();
  Bluefruit.Advertising.restartOnDisconnect(true);
  Bluefruit.Advertising.setInterval(32, 244);
  Bluefruit.Advertising.setFastTimeout(30);
  Bluefruit.Advertising.start(0);
}

void calibrateGyro() {
  constexpr uint16_t kRequiredStableSamples = 180;
  constexpr uint32_t kTimeoutMs = 3500;
  constexpr float kMaxStillGyroDps = 5.0f;
  constexpr float kMinStillAccelG = 0.90f;
  constexpr float kMaxStillAccelG = 1.10f;

  uint16_t accepted = 0;
  float sumX = 0.0f;
  float sumY = 0.0f;
  float sumZ = 0.0f;
  const uint32_t startedAt = millis();

  while (accepted < kRequiredStableSamples && millis() - startedAt < kTimeoutMs) {
    MotionSample sample;
    if (!readMotion(sample)) {
      delay(1);
      continue;
    }

    const float gyroMagnitude = sqrtf(sample.gyroX * sample.gyroX +
                                      sample.gyroY * sample.gyroY +
                                      sample.gyroZ * sample.gyroZ);
    const float accelMagnitude = sqrtf(sample.accelX * sample.accelX +
                                       sample.accelY * sample.accelY +
                                       sample.accelZ * sample.accelZ);
    if (gyroMagnitude <= kMaxStillGyroDps && accelMagnitude >= kMinStillAccelG &&
        accelMagnitude <= kMaxStillAccelG) {
      sumX += sample.gyroX;
      sumY += sample.gyroY;
      sumZ += sample.gyroZ;
      ++accepted;
    } else {
      // The stable window must be consecutive; never average separate poses
      // across handling motion into a false gyro bias.
      accepted = 0;
      sumX = sumY = sumZ = 0.0f;
    }
  }

  if (accepted >= kRequiredStableSamples) {
    streamBiasX = sumX / accepted;
    streamBiasY = sumY / accepted;
    streamBiasZ = sumZ / accepted;
    detector.setGyroBias(streamBiasX, streamBiasY, streamBiasZ);
    learningDetector.setGyroBias(streamBiasX, streamBiasY, streamBiasZ);
    sendLine("CAL,OK," + String(accepted));
  } else {
    // Preserve the last successful calibration if a requested CAL was moving.
    sendLine("CAL,SKIPPED," + String(accepted));
  }
}

bool inRange(float value, float minimum, float maximum) {
  return isfinite(value) && value >= minimum && value <= maximum;
}

bool parseConfigValue(const String& text, float& value) {
  const char* start = text.c_str();
  char* end = nullptr;
  value = strtof(start, &end);
  return end != start && *end == '\0' && isfinite(value);
}

bool applyConfigValue(const String& key, float value) {
  if (key == "SG" && inRange(value, 100.0f, 2500.0f)) {
    detectorConfig.startGyroDps = value;
  } else if (key == "SA" && inRange(value, 0.1f, 8.0f)) {
    detectorConfig.startDynamicAccelG = value;
  } else if (key == "CG" && inRange(value, 150.0f, 3500.0f)) {
    detectorConfig.confirmGyroDps = value;
  } else if (key == "CA" && inRange(value, 0.1f, 12.0f)) {
    detectorConfig.confirmDynamicAccelG = value;
  } else if (key == "HA" && inRange(value, 0.2f, 15.0f)) {
    detectorConfig.hardDynamicAccelG = value;
  } else if (key == "HG" && inRange(value, 100.0f, 3000.0f)) {
    detectorConfig.hardAccelMinGyroDps = value;
  } else if (key == "CS" && inRange(value, 1.0f, 8.0f)) {
    detectorConfig.confirmSamples = static_cast<uint8_t>(roundf(value));
  } else if (key == "AT" && inRange(value, 5.0f, 500.0f)) {
    detectorConfig.minimumAngularTravelDeg = value;
  } else if (key == "DC" && inRange(value, 0.0f, 1.0f)) {
    detectorConfig.minimumDirectionConsistency = value;
  } else if (key == "AX" && inRange(value, 0.0f, 1.0f)) {
    detectorConfig.minimumDominantAxisRatio = value;
  } else if (key == "PG" && inRange(value, 0.0f, 800.0f)) {
    detectorConfig.maximumPeakGapMs = static_cast<uint16_t>(roundf(value));
  } else if (key == "MD" && inRange(value, 10.0f, 500.0f)) {
    detectorConfig.minimumDurationMs = static_cast<uint16_t>(roundf(value));
  } else if (key == "ME" && inRange(value, 200.0f, 2000.0f)) {
    detectorConfig.maxEventMs = static_cast<uint16_t>(roundf(value));
  } else if (key == "CD" && inRange(value, 200.0f, 3000.0f)) {
    detectorConfig.cooldownMs = static_cast<uint16_t>(roundf(value));
  } else {
    return false;
  }
  if (!learningMode) {
    detector.setConfig(detectorConfig);
  }
  return true;
}

void sendConfigValue(const char* key, float value, uint8_t decimals = 3) {
  sendLine("CFGVAL," + String(key) + "," + String(value, decimals));
}

void sendConfigValues() {
  sendConfigValue("SG", detectorConfig.startGyroDps, 1);
  sendConfigValue("SA", detectorConfig.startDynamicAccelG);
  sendConfigValue("CG", detectorConfig.confirmGyroDps, 1);
  sendConfigValue("CA", detectorConfig.confirmDynamicAccelG);
  sendConfigValue("HA", detectorConfig.hardDynamicAccelG);
  sendConfigValue("HG", detectorConfig.hardAccelMinGyroDps, 1);
  sendConfigValue("CS", detectorConfig.confirmSamples, 0);
  sendConfigValue("AT", detectorConfig.minimumAngularTravelDeg, 1);
  sendConfigValue("DC", detectorConfig.minimumDirectionConsistency);
  sendConfigValue("AX", detectorConfig.minimumDominantAxisRatio);
  sendConfigValue("PG", detectorConfig.maximumPeakGapMs, 0);
  sendConfigValue("MD", detectorConfig.minimumDurationMs, 0);
  sendConfigValue("ME", detectorConfig.maxEventMs, 0);
  sendConfigValue("CD", detectorConfig.cooldownMs, 0);
  sendLine("CFGVAL,DONE");
}

void startLearning() {
  learningMode = true;
  learningSampleSequence = 0;
  learningSamplePending = false;
  pendingLearningResult = "EMPTY";
  detector.reset();
  learningDetector.setConfig(makeLearningConfig());
  sendLine("LEARN,STARTED");
}

void stopLearning() {
  learningMode = false;
  learningSamplePending = false;
  pendingLearningResult = "EMPTY";
  learningDetector.reset();
  detector.setConfig(detectorConfig);
  sendLine("LEARN,STOPPED");
}

void reportPower() {
  sendLine("POWER," + String(powerEnabled ? 1 : 0) + "," +
           (powerSleeping ? "SLEEP" : "ACTIVE") + ",300");
}

bool writePowerRegister(uint8_t reg, uint8_t value) {
  uint8_t actual = 0;
  return imu.writeRegister(reg, value) == IMU_SUCCESS &&
         imu.readRegister(&actual, reg) == IMU_SUCCESS && actual == value;
}

void clearPowerMotion() {
  // Never cut a partially transmitted RAW line before a POWER status line.
  rawBatchCount = 0;
  transport.discardMotion();
  detector.reset();
  learningDetector.reset();
}

bool wakePower() {
  powerIdle.reset(millis());
  if (!powerSleeping) return true;
  // Restore exact saved ranges/filter/rates, not library defaults.
  bool ok = writePowerRegister(0x15, activeCtrl6);
  ok = writePowerRegister(0x10, activeXl) && ok;
  ok = writePowerRegister(0x11, activeGyro) && ok;
  if (!ok) return false; // Retry from the low-frequency loop, never use wrong scales.
  powerSleeping = false;
  clearPowerMotion();
  powerResumeAt = millis() + 300; // Gyro settling; pickup must not become a strike.
  powerSettling = true;
  nextHardwareTapPollAt = millis();
  uint8_t ignoredTapSource = 0;
  imu.readRegister(&ignoredTapSource, kTapSourceRegister);
  nextSampleUs = micros();
  Bluefruit.autoConnLed(true);
  BLEConnection* connection = Bluefruit.Connection(Bluefruit.connHandle());
  if (connection) requestHostConnection(connection->handle(), activeHost, false);
  reportPower();
  return true;
}

void sleepPower(const MotionSample& sample) {
  if (imu.readRegister(&activeXl, 0x10) != IMU_SUCCESS ||
      imu.readRegister(&activeGyro, 0x11) != IMU_SUCCESS ||
      imu.readRegister(&activeCtrl6, 0x15) != IMU_SUCCESS) {
    powerIdle.reset(millis());
    return;
  }
  // LSM6DS3TR-C: CTRL6_C.XL_HM_MODE=1, accel ODR=26 Hz;
  // preserve +/-16 g scaling; gyro ODR=0 (power-down).
  powerSleeping = true; // Enables rollback even if one register write fails.
  bool ok = writePowerRegister(0x11, activeGyro & 0x0f);
  ok = writePowerRegister(0x15, activeCtrl6 | 0x10) && ok;
  ok = writePowerRegister(0x10, (activeXl & 0x0f) | 0x20) && ok;
  if (!ok) {
    powerHardwareFault = true;
    sendLine("POWERERR,IMU_CONFIG");
    wakePower();
    return;
  }
  powerIdle.anchor(sample.accelX, sample.accelY, sample.accelZ);
  powerLastSampleAt = millis();
  clearPowerMotion();
  Bluefruit.autoConnLed(false);
  // XIAO's common-anode RGB is active-low. This core's LED_STATE_ON=1
  // describes generic boards and must not be used to turn this RGB off.
  digitalWrite(LED_RED, HIGH);
  digitalWrite(LED_GREEN, HIGH);
  digitalWrite(LED_BLUE, HIGH);
  BLEConnection* connection = Bluefruit.Connection(Bluefruit.connHandle());
  // Compatible low-power range; the central chooses the final interval.
  if (connection) requestHostConnection(connection->handle(), activeHost, true);
  reportPower();
}

void pollSleepingMotion() {
  if (powerHardwareFault) { wakePower(); delay(100); return; }
  uint8_t status = 0;
  uint8_t raw[6];
  if (imu.readRegister(&status, kStatusRegister) == IMU_SUCCESS &&
      (status & kAccelReadyMask) &&
      imu.readRegisterRegion(raw, LSM6DS3_ACC_GYRO_OUTX_L_XL, sizeof(raw)) == IMU_SUCCESS) {
    powerLastSampleAt = millis();
    if (powerIdle.wake(imu.calcAccel(readInt16LE(raw)),
                       imu.calcAccel(readInt16LE(raw+2)),
                       imu.calcAccel(readInt16LE(raw+4)))) {
      if (!wakePower()) powerHardwareFault = true;
    }
  } else if (millis() - powerLastSampleAt > 1500) {
    powerHardwareFault = true;
    sendLine("POWERERR,IMU_TIMEOUT");
    wakePower();
  }
  // FreeRTOS delay yields the CPU instead of spinning at the active 416 Hz.
  if (powerSleeping) delay(40);
}

bool savePowerPreference(bool enabled) {
  if (!powerFsReady) return false;
  Adafruit_LittleFS_Namespace::File file(InternalFS);
  if (!file.open(kPowerFile, Adafruit_LittleFS_Namespace::FILE_O_WRITE)) return false;
  file.seek(0);
  const uint8_t value = enabled ? '1' : '0';
  const bool ok = file.write(&value, 1) == 1;
  file.close();
  return ok;
}

void handleCommand(String command) {
  command.trim();
  command.toUpperCase();

  if (command == "POWERGET") {
    reportPower();
    return;
  }
  if (command == "POWERTEST") {
    sendLine("POWERTEST," + String(powerIdleSelfTest()) + ",12");
    return;
  }
  if (command == "POWERHOLD") {
    if (!wakePower()) sendLine("POWERERR,IMU_RESTORE");
    return;
  }
  if (command == "POWER,0" || command == "POWER,1") {
    const bool enabled = command == "POWER,1";
    if (enabled != powerEnabled && !savePowerPreference(enabled)) {
      sendLine("POWERERR,SAVE_FAILED");
      return;
    }
    const bool changed = enabled != powerEnabled;
    powerEnabled = enabled;
    if (!enabled && !wakePower()) {
      powerHardwareFault = true;
      sendLine("POWERERR,IMU_RESTORE");
    }
    if (changed) powerIdle.reset(millis());
    reportPower();
    return;
  }
  if (command == "CAL" || command == "LEARN,START" || command.startsWith("VOICE,START,")) {
    if (!wakePower()) { sendLine("POWERERR,IMU_RESTORE"); return; }
  }
  if (command == "PING") {
    sendLine("PONG," + String(kFirmwareVersion));
    sendLine("CAPS,HOST_PROFILE,1");
    sendLine("TAPENGINE," + String(hardwareTapReady ? 1 : 0) +
             ",ST_AN5130," + String(hardwareTapThresholdG(), 2) + ",1000");
    reportBattery();
  } else if (command.startsWith("HOST,")) {
    HostSystem host;
    if (!parseHostSystem(command.substring(5).c_str(), host)) {
      sendLine("HOST,ERROR,UNSUPPORTED");
      return;
    }
    activeHost = hostProfile(host);
    rawBatchCount = 0;
    lastStreamSampleAt = 0;
    transport.discardMotion();
    const bool requested = requestHostConnection(Bluefruit.connHandle(), activeHost, powerSleeping);
    sendLine("HOST,OK," + String(activeHost.name) + "," +
             String(activeHost.batchSamples) + "," + (requested ? "REQUESTED" : "REQUEST_FAILED"));
  } else if (command == "LINK") {
    reportLink();
  } else if (command == "BATTERY") {
    reportBattery();
  } else if (command == "SELFTEST") {
    runDetectorSelfTest();
  } else if (command == "STATUS") {
    sendLine("STATUS," + String(armed ? 1 : 0) + "," +
             String(kFirmwareVersion) + "," + String(learningMode ? 1 : 0));
  } else if (command == "ARM,1") {
    armed = true;
    detector.reset();
    sendLine("ARMED,1");
  } else if (command == "ARM,0") {
    armed = false;
    learningMode = false;
    learningSamplePending = false;
    pendingLearningResult = "EMPTY";
    detector.reset();
    learningDetector.reset();
    sendLine("ARMED,0");
  } else if (command == "CAL") {
    calibrateGyro();
  } else if (command == "LEARN,START") {
    startLearning();
  } else if (command == "LEARN,TAKE") {
    if (!learningMode) {
      sendLine("ERR,LEARN_NOT_STARTED");
    } else if (!learningSamplePending) {
      sendLine("LEARN,EMPTY");
    } else {
      sendLearningSample(pendingLearningResult, pendingLearningEvent);
      learningSamplePending = false;
      pendingLearningResult = "EMPTY";
      sendLine("LEARN,RECORDED");
    }
  } else if (command == "LEARN,STOP") {
    stopLearning();
  } else if (command == "RAW,1") {
    setRawStreaming(true, false);
  } else if (command == "RAW,2") {
    setRawStreaming(true, true);
  } else if (command == "RAW,0") {
    setRawStreaming(false, false);
  } else if (command == "VOICE,1") {
    voiceEnabled = true;
    sendLine("VOICE,ENABLED,1");
  } else if (command == "VOICE,0") {
    if (voiceRecording) stopVoiceRecording("DISABLED");
    voiceEnabled = false;
    sendLine("VOICE,ENABLED,0");
  } else if (command.startsWith("TAPCFG,")) {
    const float requested = command.substring(7).toFloat();
    if (!setHardwareTapThreshold(requested)) {
      sendLine("TAPCFG,ERROR,RANGE");
    } else {
      sendLine("TAPCFG,OK," + String(hardwareTapThresholdG(), 2));
    }
  } else if (command.startsWith("VOICE,START,")) {
    unsigned int silenceMs = 0;
    unsigned int maximumMs = 0;
    if (sscanf(command.c_str(), "VOICE,START,%u,%u", &silenceMs,
               &maximumMs) != 2) {
      sendLine("VOICE,ERROR,START_FORMAT");
    } else {
      startVoiceRecording(static_cast<uint16_t>(silenceMs),
                          static_cast<uint16_t>(maximumMs));
    }
  } else if (command == "VOICE,CANCEL") {
    if (voiceRecording) {
      stopVoiceRecording("CANCELLED");
    } else {
      sendLine("VOICE,ERROR,NOT_RECORDING");
    }
  } else if (command == "CFG,GET") {
    sendConfigValues();
  } else if (command == "CFG,RESET") {
    detectorConfig = WhipDetectorConfig();
    if (!learningMode) {
      detector.setConfig(detectorConfig);
    }
    sendLine("CFG,RESET,OK");
  } else if (command.startsWith("CFG,")) {
    const int secondComma = command.indexOf(',', 4);
    if (secondComma < 0) {
      sendLine("ERR,CFG_FORMAT");
      return;
    }
    const String key = command.substring(4, secondComma);
    const String valueText = command.substring(secondComma + 1);
    float value = 0.0f;
    if (!parseConfigValue(valueText, value) || !applyConfigValue(key, value)) {
      sendLine("ERR,CFG_VALUE," + key);
      return;
    }
    sendLine("CFG,OK," + key);
  } else if (command == "TEST") {
    sendSyntheticWhipEvent();
  } else if (command == "TEST,SLOW") {
    sendSyntheticWhipEvent(160);
  } else if (command == "TEST,REBOUND") {
    sendSyntheticWhipEvent(20, true);
  } else if (command == "TEST,LEARN") {
    captureSyntheticLearningSample();
  } else if (command.length() > 0) {
    sendLine("ERR,UNKNOWN_COMMAND");
  }
}

void pollCommands() {
  while (bleUart.available()) {
    const char ch = static_cast<char>(bleUart.read());
    if (ch == '\n' || ch == '\r') {
      if (commandBuffer.length() > 0) {
        handleCommand(commandBuffer);
        commandBuffer = "";
      }
    } else if (commandBuffer.length() < 64) {
      commandBuffer += ch;
    } else {
      commandBuffer = "";
      sendLine("ERR,COMMAND_TOO_LONG");
    }
  }
}

}  // namespace

void setup() {
  Serial.begin(115200);
  delay(250);

  pinMode(VBAT_ENABLE, OUTPUT);
  digitalWrite(VBAT_ENABLE, LOW);
  pinMode(kChargeStatusPin, INPUT_PULLUP);
  chargingState = digitalRead(kChargeStatusPin) == LOW;
  chargingCandidate = chargingState;
  chargingCandidateSince = millis();

  // Explicitly retain the maximum ranges and 416 Hz rate used by the detector.
  imu.settings.accelRange = 16;
  imu.settings.accelSampleRate = kSampleRateHz;
  imu.settings.accelBandWidth = 200;
  imu.settings.gyroRange = 2000;
  imu.settings.gyroSampleRate = kSampleRateHz;
  imu.settings.gyroBandWidth = 400;
  if (imu.begin() != IMU_SUCCESS) {
    serialDiagnostic("FATAL,IMU_NOT_FOUND");
    while (true) {
      delay(1000);
    }
  }
  // The on-board LSM6DS3TR-C supports I2C Fast Mode. At the Wire default
  // 100 kHz, status + burst reads consume most of the 416 Hz sample period.
  Wire.setClock(400000);
  hardwareTapReady = configureHardwareDoubleTap();
  if (!hardwareTapReady) {
    serialDiagnostic("WARN,TAP_ENGINE_CONFIG_FAILED");
  }

  Bluefruit.autoConnLed(true);
  Bluefruit.configPrphBandwidth(BANDWIDTH_MAX);
  Bluefruit.begin();
  powerFsReady = InternalFS.begin();
  if (powerFsReady) {
    Adafruit_LittleFS_Namespace::File file(InternalFS);
    if (file.open(kPowerFile, Adafruit_LittleFS_Namespace::FILE_O_READ)) {
      powerEnabled = file.read() == '1';
      file.close();
    }
  }
  Bluefruit.setTxPower(4);
  Bluefruit.setName(kDeviceName);
  if (!transport.begin()) {
    serialDiagnostic("FATAL,BLE_TX_TASK");
    while (true) delay(1000);
  }
  Bluefruit.Periph.setConnInterval(12, 24);
  Bluefruit.Periph.setConnSlaveLatency(0);
  Bluefruit.Periph.setConnSupervisionTimeoutMS(4000);
  Bluefruit.Periph.setConnectCallback(onBleConnect);
  Bluefruit.Periph.setDisconnectCallback(onBleDisconnect);

  deviceInfo.setManufacturer("Codex Whip");
  deviceInfo.setModel("XIAO nRF52840 Sense");
  deviceInfo.setFirmwareRev(kFirmwareVersion);
  deviceInfo.begin();
  bleUart.begin();
  advertise();

  calibrateGyro();
  sendLine("READY," + String(kFirmwareVersion));
  nextSampleUs = micros();
  powerIdle.reset(millis());
}

void loop() {
  if (linkReset.exchange(false)) {
    const uint32_t session = transport.session();
    // All per-peer state is reset in the sampling task, never in BLE callbacks.
    if (voiceRecording) { voiceRecording = false; PDM.end(); }
    activeHost = hostProfile(HostSystem::Compatible);
    rawStreaming = false;
    rawSuppressEvents = false;
    rawBatchCount = 0;
    nextRawSampleMs = 0;
    lastStreamSampleAt = longestStreamGapMs = 0;
    commandBuffer = "";
    learningMode = false;
    voiceEnabled = false;
    detector.reset();
    learningDetector.reset();
    transport.acceptSession(session);
  }
  pollCommands();
  pollChargeStatus();

  if (Bluefruit.connected() && !voiceRecording &&
      static_cast<int32_t>(millis() - nextBatteryReportAt) >= 0) {
    reportBattery();
  }

  if (voiceRecording && !Bluefruit.connected()) {
    stopVoiceRecording("DISCONNECTED");
  }
  processVoiceAudio();
  if (voiceRecording) {
    powerIdle.reset(millis());
    yield();
    return;
  }

  if (powerSleeping) { pollSleepingMotion(); return; }

  if (learningMode && !Bluefruit.connected()) {
    stopLearning();
  }

  const uint32_t nowUs = micros();
  if (static_cast<int32_t>(nowUs - nextSampleUs) < 0) {
    yield();
    return;
  }
  if (nowUs - nextSampleUs > kSamplePeriodUs * 4) {
    nextSampleUs = nowUs + kSamplePeriodUs;
  } else {
    nextSampleUs += kSamplePeriodUs;
  }

  MotionSample sample;
  if (!readMotion(sample)) {
    if (millis() - powerLastSampleAt > 1000) powerIdle.reset(millis());
    return;
  }
  powerLastSampleAt = millis();
  if (powerSettling) {
    if (static_cast<int32_t>(millis() - powerResumeAt) < 0) return;
    powerSettling = false;
  }
  if (learningMode) powerIdle.reset(sample.timestampMs);
  else if (powerEnabled && !powerHardwareFault &&
           powerIdle.idle(sample.timestampMs, sample.accelX, sample.accelY, sample.accelZ,
                          sample.gyroX, sample.gyroY, sample.gyroZ)) {
    sleepPower(sample);
    return;
  }
  if (!armed) return;

  streamMotion(sample);
  pollHardwareDoubleTap(sample.timestampMs);

  if (learningMode) {
    WhipEvent event;
    if (learningDetector.update(sample, event)) {
      rememberLearningSample("CAPTURED", event);
    }
    WhipRejection rejection;
    if (learningDetector.takeRejection(rejection)) {
      rememberLearningSample(rejection);
    }
  } else if (!rawSuppressEvents) {
    WhipEvent event;
    if (detector.update(sample, event)) {
      sendWhipEvent(event);
    }
    WhipRejection rejection;
    if (detector.takeRejection(rejection)) {
      sendWhipRejection(rejection);
    }
  }
}
