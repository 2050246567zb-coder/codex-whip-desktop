#pragma once

#include <stddef.h>
#include <stdint.h>

constexpr uint8_t kVoiceFrameMagic0 = 0xA5;
constexpr uint8_t kVoiceFrameMagic1 = 0x5A;
constexpr uint8_t kVoiceFrameTypeAudio = 1;
constexpr uint8_t kVoiceFrameVersion = 1;
constexpr size_t kVoiceFrameOverheadBytes = 21;
constexpr size_t kVoiceFrameMaximumBytes = 244;

inline void voicePacketWrite16(uint8_t* output, uint16_t value) {
  output[0] = static_cast<uint8_t>(value & 0xFF);
  output[1] = static_cast<uint8_t>((value >> 8) & 0xFF);
}

inline void voicePacketWrite32(uint8_t* output, uint32_t value) {
  output[0] = static_cast<uint8_t>(value & 0xFF);
  output[1] = static_cast<uint8_t>((value >> 8) & 0xFF);
  output[2] = static_cast<uint8_t>((value >> 16) & 0xFF);
  output[3] = static_cast<uint8_t>((value >> 24) & 0xFF);
}

inline uint16_t voicePacketCrc16(const uint8_t* data, size_t length) {
  uint16_t crc = 0xFFFF;
  for (size_t index = 0; index < length; ++index) {
    crc ^= static_cast<uint16_t>(data[index]) << 8;
    for (uint8_t bit = 0; bit < 8; ++bit) {
      crc = (crc & 0x8000) ? static_cast<uint16_t>((crc << 1) ^ 0x1021)
                           : static_cast<uint16_t>(crc << 1);
    }
  }
  return crc;
}

inline size_t packVoiceAudioFrame(
    uint32_t session, uint32_t sequence, uint16_t sampleCount,
    int16_t predictor, uint8_t stepIndex, const uint8_t* encoded,
    size_t encodedBytes, uint8_t* output, size_t capacity) {
  const size_t payloadBytes = 13 + encodedBytes;
  const size_t totalBytes = 6 + payloadBytes + 2;
  if (!encoded || !output || !encodedBytes || encodedBytes > 220 ||
      !sampleCount || sampleCount > encodedBytes * 2 + 1 || stepIndex > 88 ||
      totalBytes > capacity || totalBytes > kVoiceFrameMaximumBytes) return 0;
  output[0] = kVoiceFrameMagic0;
  output[1] = kVoiceFrameMagic1;
  output[2] = kVoiceFrameTypeAudio;
  output[3] = kVoiceFrameVersion;
  voicePacketWrite16(output + 4, static_cast<uint16_t>(payloadBytes));
  voicePacketWrite32(output + 6, session);
  voicePacketWrite32(output + 10, sequence);
  voicePacketWrite16(output + 14, sampleCount);
  voicePacketWrite16(output + 16, static_cast<uint16_t>(predictor));
  output[18] = stepIndex;
  for (size_t index = 0; index < encodedBytes; ++index) output[19 + index] = encoded[index];
  const uint16_t crc = voicePacketCrc16(output + 2, 17 + encodedBytes);
  voicePacketWrite16(output + 19 + encodedBytes, crc);
  return totalBytes;
}
