#include "c3_hardware.h"
#include "voice_audio.h"

namespace {

// Desktop <=2.2.32 parses each dotted field as an integer. Hardware identity
// belongs in Device Information, not a suffix on the protocol version.
constexpr char kFirmwareVersion[] = "0.7.7";
constexpr char kDeviceName[] = "CodexWhip";
constexpr uint32_t kSampleRateHz = 500;
constexpr uint32_t kSamplePeriodUs = 1000000UL / kSampleRateHz;
constexpr size_t kBlePreferredPayloadBytes = 244;
constexpr uint32_t kBleChunkTimeoutMs = 650;
constexpr uint32_t kRawStreamPeriodMs = 10;
constexpr uint8_t kRawBatchSamples = 2;
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
String rawTxBuffer;
size_t rawTxOffset = 0;
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

bool sendBlePayload(const uint8_t* data, size_t length) {
  const size_t payloadLimit = blePayloadLimit();
  size_t offset = 0;
  while (offset < length && linkConnected()) {
    const size_t remaining = length - offset;
    const size_t chunkLength =
        remaining < payloadLimit ? remaining : payloadLimit;
    const uint32_t startedAt = millis();
    bool sent = false;
    while (linkConnected() && millis() - startedAt < kBleChunkTimeoutMs) {
      if (bleUart.write(data + offset, chunkLength) == chunkLength) {
        sent = true;
        break;
      }
      delay(2);
    }
    if (!sent) {
      // A failed fragment must not be followed by another CSV record on the
      // same connection: the desktop decoder would concatenate both lines.
      if (linkConnected()) bleServer->disconnect(bleServer->getPeerInfo(0).getConnHandle());
      return false;
    }
    offset += chunkLength;
  }
  return offset == length;
}

bool flushRawTransmission() {
  if (rawTxOffset >= rawTxBuffer.length()) {
    rawTxBuffer = "";
    rawTxOffset = 0;
    return true;
  }
  if (!linkConnected()) {
    rawTxBuffer = "";
    rawTxOffset = 0;
    return false;
  }
  const size_t remaining = rawTxBuffer.length() - rawTxOffset;
  const size_t limit = blePayloadLimit();
  const size_t chunkLength = remaining < limit ? remaining : limit;
  const size_t written = bleUart.write(
      reinterpret_cast<const uint8_t*>(rawTxBuffer.c_str()) + rawTxOffset,
      chunkLength);
  if (written == chunkLength) rawTxOffset += written;
  return rawTxOffset >= rawTxBuffer.length();
}

void finishRawTransmission() {
  const uint32_t startedAt = millis();
  while (rawTxBuffer.length() > 0 && linkConnected() &&
         millis() - startedAt < kBleChunkTimeoutMs) {
    if (flushRawTransmission()) break;
    delay(1);
  }
  if (rawTxBuffer.length() > 0) {
    if (rawTxOffset < rawTxBuffer.length() && rawTxOffset > 0 && linkConnected())
      bleServer->disconnect(bleServer->getPeerInfo(0).getConnHandle());
    rawTxBuffer = "";
    rawTxOffset = 0;
    Serial.println("INFO,RAW_TX_CLEARED");
  }
}

void sendLine(const String& line) {
  finishRawTransmission();
  Serial.println(line);
  if (!linkConnected()) {
    return;
  }

  String payload = line;
  payload += '\n';
  if (!sendBlePayload(reinterpret_cast<const uint8_t*>(payload.c_str()),
                      payload.length())) {
    Serial.println("WARN,BLE_TX_FAILED");
  }
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

void captureMicData() {
  if (!voiceRecording || !micRx) return;
  int32_t frames[640];
  size_t bytes=0;
  esp_err_t result=i2s_channel_read(micRx,frames,sizeof(frames),&bytes,0);
  if (micOverflow.exchange(false)) voiceRingOverflow=true;
  if (result!=ESP_OK && result!=ESP_ERR_TIMEOUT) { voiceRingOverflow=true; return; }
  size_t count=bytes/(2*sizeof(int32_t));
  if (voiceRingCount+count>kVoiceRingSamples) { voiceRingOverflow=true; return; }
  for (size_t i=0;i<count;++i) {
    float value=frames[2*i]/65536.0f;
    micDc += 0.005f*(value-micDc);
    // Fixed digital gain, not AGC; tune only after real microphone measurements.
    value=constrain((value-micDc)*4.0f,-32768.0f,32767.0f);
    voiceRing[voiceRingWrite]=int16_t(value);
    voiceRingWrite=(voiceRingWrite+1)%kVoiceRingSamples;
  }
  voiceRingCount+=count;
  if (count) voiceLastPdmAtMs=millis();
}

void restoreMotionAfterVoice() {
  rawStreaming = voiceSavedRawStreaming;
  rawSuppressEvents = voiceSavedRawSuppressEvents;
  nextRawSampleMs = 0;
  rawBatchCount = 0;
  rawTxBuffer = "";
  rawTxOffset = 0;
  detector.reset();
}

bool sendVoicePayload(const String& line) {
  if (!linkConnected()) return false;
  String payload = line;
  payload += '\n';
  if (payload.length() > 256) {
    Serial.println("WARN,VOICE_LINE_TOO_LONG");
    return false;
  }
  return sendBlePayload(reinterpret_cast<const uint8_t*>(payload.c_str()),
                        payload.length());
}

void stopVoiceRecording(const char* reason) {
  if (!voiceRecording) return;
  voiceRecording = false;
  endMic();
  sendLine("VOICE,END," + String(voiceSession) + "," +
           String(voiceTotalSamples) + "," + String(reason));
  restoreMotionAfterVoice();
}

void startVoiceRecording(uint16_t silenceMs, uint16_t maximumRecordingMs) {
  if (!voiceEnabled) {
    sendLine("VOICE,ERROR,DISABLED");
    return;
  }
  if (!linkConnected()) return;
  if (voiceRecording) {
    sendLine("VOICE,ERROR,ALREADY_RECORDING");
    return;
  }

  finishRawTransmission();
  voiceSavedRawStreaming = rawStreaming;
  voiceSavedRawSuppressEvents = rawSuppressEvents;
  rawStreaming = false;
  rawSuppressEvents = true;
  rawBatchCount = 0;
  rawTxBuffer = "";
  rawTxOffset = 0;

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

  ++voiceSession;
  sendLine("VOICE,START," + String(voiceSession) + "," +
           String(kVoiceSampleRateHz) + ",IMA_ADPCM4");
  if (!beginMic()) {
    restoreMotionAfterVoice();
    sendLine("VOICE,END," + String(voiceSession) + ",0,MIC_START_FAILED");
    return;
  }
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
  if (voiceRingCount < kVoicePcmSamples) return;

  int16_t pcm[kVoicePcmSamples] = {0};
  if (voiceRingCount < kVoicePcmSamples) {
    return;
  }
  for (size_t index = 0; index < kVoicePcmSamples; ++index) {
    pcm[index] = voiceRing[voiceRingRead];
    voiceRingRead = (voiceRingRead + 1) % kVoiceRingSamples;
  }
  voiceRingCount -= kVoicePcmSamples;
  const size_t sampleCount = kVoicePcmSamples;

  uint32_t absoluteSum = 0;
  for (size_t index = 0; index < sampleCount; ++index) {
    const int32_t value = pcm[index];
    absoluteSum += static_cast<uint32_t>(value < 0 ? -value : value);
  }
  const uint16_t level = static_cast<uint16_t>(absoluteSum / sampleCount);
  const uint32_t elapsed = now - voiceStartedAtMs;
  if (elapsed <= kVoiceNoiseIgnoreMs) {
    // Ignore microphone/filter startup settling when estimating room noise.
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
    Serial.println(
        "WARN,VOICE_TX_FAILED," + String(voiceChunkSequence) + ",MTU," +
        String(blePayloadLimit()) + ",RING," + String(voiceRingCount));
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

void streamMotion(const MotionSample& sample) {
  if (!rawStreaming || !linkConnected()) return;
  // Decimate by real time, not by a count that assumes every 500 Hz polling
  // attempt succeeded. BLE/RTOS and independent data-ready timing can skip
  // reads; four successful reads previously stretched PC samples to ~16 ms.
  if (static_cast<int32_t>(sample.timestampMs - nextRawSampleMs) < 0) return;
  if (sample.timestampMs - nextRawSampleMs > kRawStreamPeriodMs * 2) {
    nextRawSampleMs = sample.timestampMs + kRawStreamPeriodMs;
  } else {
    nextRawSampleMs += kRawStreamPeriodMs;
  }

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

  if (rawBatchCount < kRawBatchSamples) return;
  const String payload = encodeBase64(rawBatch, sizeof(rawBatch));
  ++rawBatchSequence;  // Count dropped batches too, so the PC can detect loss.
  if (rawTxBuffer.length() == 0) {
    rawTxBuffer = "RAW5," + String(rawBatchSequence) + "," +
                  String(rawBatchStartMs) + "," + payload + "\n";
    rawTxOffset = 0;
  } else {
    Serial.println("WARN,RAW_TX_BACKLOG");
  }
  rawBatchCount = 0;
}

void setRawStreaming(bool enabled, bool suppressEvents) {
  rawStreaming = enabled;
  rawSuppressEvents = enabled && suppressEvents;
  nextRawSampleMs = 0;
  rawBatchCount = 0;
  if (!enabled) {
    rawTxBuffer = "";
    rawTxOffset = 0;
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
  sendLine("CFGVAL," + String(key) + "," + String(value, static_cast<unsigned int>(decimals)));
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

void handleCommand(String command) {
  command.trim();
  command.toUpperCase();

  // Long diagnostics/calibration must not interrupt an active audio stream.
  if (voiceRecording && command != "VOICE,CANCEL" && command != "VOICE,0" &&
      command != "PING" && command != "STATUS") {
    sendLine("ERR,VOICE_BUSY");
    return;
  }

  if (command == "PING") {
    sendLine("PONG," + String(kFirmwareVersion));
  } else if (command == "BLE,GET") {
    uint8_t txPhy=0,rxPhy=0;
    if(linkConnected()) bleServer->getPhy(bleServer->getPeerInfo(0).getConnHandle(),&txPhy,&rxPhy);
    sendLine("BLE,LINK,"+String(connectionInterval.load()*1.25f,2)+","+
      String(connectionLatency.load())+","+String(negotiatedMtu.load())+","+
      String(txPhy)+","+String(rxPhy)+","+String(linkTuneStage.load()));
  } else if (command == "SELFTEST") {
    runDetectorSelfTest();
  } else if (command == "IMU,GET") {
    sendLine("IMU,COUNTS," + String(imuReadOk) + "," + String(imuBusErrors) + "," + String(imuNotReady));
    for (uint8_t reg : {uint8_t(0x75), uint8_t(0x6B), uint8_t(0x6C), uint8_t(0x38), uint8_t(0x3A)}) {
      uint8_t value=0;
      bool ok=mpuRead(reg,&value,1);
      sendLine("IMU,REG," + String(reg) + "," + String(ok ? int(value) : -1));
    }
  } else if (command == "IMU,PROBE") {
    // Diagnostic only: test both possible addresses at both bus rates.
    for (uint32_t hz : {100000UL, 400000UL}) {
      Wire.setClock(hz);
      for (uint8_t address : {uint8_t(0x68), uint8_t(0x69)}) {
        Wire.beginTransmission(address);
        uint8_t ack=Wire.endTransmission();
        int id=-1;
        if (ack==0) {
          Wire.beginTransmission(address); Wire.write(0x75);
          if (Wire.endTransmission(false)==0 && Wire.requestFrom(address,size_t(1),true)==1) id=Wire.read();
        }
        sendLine("IMU,PROBE,"+String(hz)+","+String(address)+","+String(ack)+","+String(id));
      }
    }
    Wire.setClock(400000);
  } else if (command == "IMU,SAMPLE") {
    uint8_t data[14];
    if (!mpuRead(0x3B,data,14)) sendLine("IMU,SAMPLE,READ_FAILED");
    else {
      String result="IMU,SAMPLE,"+String(millis());
      for (int offset : {0,2,4,8,10,12}) result+=","+String(signedBE(data+offset));
      sendLine(result);
    }
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
  } else if (command.startsWith("VOICE,START,")) {
    unsigned int silenceMs = 0;
    unsigned int maximumMs = 0;
    int consumed = 0;
    if (sscanf(command.c_str(), "VOICE,START,%u,%u%n", &silenceMs,
               &maximumMs, &consumed) != 2 || consumed != command.length() ||
        silenceMs < 400 || silenceMs > 4000 || maximumMs < 3000 || maximumMs > 30000) {
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

String serialCommandBuffer;
bool bleDiscardCommand=false, serialDiscardCommand=false;
void consumeCommandChar(char ch, String& buffer, bool& discard) {
    if (ch == '\n' || ch == '\r') {
      if (!discard && buffer.length() > 0) {
        handleCommand(buffer);
      }
      buffer=""; discard=false;
    } else if (discard) {
      return;
    } else if (buffer.length() < 64) {
      buffer += ch;
    } else {
      buffer = ""; discard=true;
      sendLine("ERR,COMMAND_TOO_LONG");
    }
}
void pollCommands() {
  if (rxOverflow.exchange(false)) {
    commandBuffer=""; bleDiscardCommand=true;
    sendLine("ERR,RX_OVERFLOW");
  }
  // Bounded work prevents a noisy serial sender starving IMU/audio processing.
  for (int i=0;i<128 && bleUart.available();++i)
    consumeCommandChar(char(bleUart.read()),commandBuffer,bleDiscardCommand);
  for (int i=0;i<128 && Serial.available();++i)
    consumeCommandChar(char(Serial.read()),serialCommandBuffer,serialDiscardCommand);
}

}  // namespace

void setup() {
  Serial.begin(115200);
  // USB diagnostics are best effort: an unopened/stalled serial monitor must
  // never stall command handling, IMU sampling or BLE notification delivery.
  Serial.setTxTimeoutMs(0);
  delay(250);

  beginBle(kDeviceName, kFirmwareVersion);
  if (!beginMotion()) {
    sendLine("FATAL,IMU_NOT_FOUND");
    // Keep USB diagnostic output; don't advertise a functional device.
    NimBLEDevice::stopAdvertising();
    // Remain unavailable to normal BLE clients, but keep USB diagnostics alive.
    uint32_t lastReport=millis();
    while (true) {
      pollCommands();
      if (millis()-lastReport >= 2000) {
        Serial.println("FATAL,IMU_NOT_FOUND"); lastReport=millis();
      }
      delay(1);
    }
  }
  calibrateGyro();
  sendLine("READY," + String(kFirmwareVersion));
  nextSampleUs = micros();
}

void loop() {
  if (linkReset.exchange(false)) {
    // Never carry a partial record, gesture, or session into a new BLE link.
    if (voiceRecording) { voiceRecording=false; endMic(); }
    rawStreaming=false; rawSuppressEvents=false; rawBatchCount=0;
    rawTxBuffer=""; rawTxOffset=0; commandBuffer=""; bleDiscardCommand=false;
    learningMode=false; learningSamplePending=false; voiceEnabled=false;
    detector.reset(); learningDetector.reset();
  }
  pollCommands();
  serviceBleLink();
  flushRawTransmission();

  if (voiceRecording && !linkConnected()) {
    stopVoiceRecording("DISCONNECTED");
  }
  captureMicData();
  processVoiceAudio();
  if (voiceRecording) {
    delay(1);
    return;
  }

  if (learningMode && !linkConnected()) {
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
  if (!readMotion(sample) || !armed) {
    return;
  }

  streamMotion(sample);

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
