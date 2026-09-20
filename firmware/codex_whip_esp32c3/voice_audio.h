#pragma once

#include <Arduino.h>

struct ImaAdpcmBlock {
  int16_t predictor = 0;
  uint8_t stepIndex = 0;
  uint8_t data[160] = {0};
  size_t byteCount = 0;
};

inline size_t encodeImaAdpcmBlock(const int16_t* samples, size_t sampleCount,
                                  uint8_t& carriedStepIndex,
                                  ImaAdpcmBlock& block) {
  static const int16_t kStepTable[89] = {
      7, 8, 9, 10, 11, 12, 13, 14, 16, 17, 19, 21, 23, 25, 28, 31,
      34, 37, 41, 45, 50, 55, 60, 66, 73, 80, 88, 97, 107, 118, 130,
      143, 157, 173, 190, 209, 230, 253, 279, 307, 337, 371, 408, 449,
      494, 544, 598, 658, 724, 796, 876, 963, 1060, 1166, 1282, 1411,
      1552, 1707, 1878, 2066, 2272, 2499, 2749, 3024, 3327, 3660, 4026,
      4428, 4871, 5358, 5894, 6484, 7132, 7845, 8630, 9493, 10442,
      11487, 12635, 13899, 15289, 16818, 18500, 20350, 22385, 24623,
      27086, 29794, 32767};
  static const int8_t kIndexTable[8] = {-1, -1, -1, -1, 2, 4, 6, 8};

  if (samples == nullptr || sampleCount == 0 || sampleCount > 320) return 0;
  block.predictor = samples[0];
  block.stepIndex = carriedStepIndex > 88 ? 88 : carriedStepIndex;
  block.byteCount = 0;
  memset(block.data, 0, sizeof(block.data));
  int32_t predictor = block.predictor;
  int32_t stepIndex = block.stepIndex;
  uint8_t pending = 0;
  bool lowNibble = true;

  for (size_t index = 1; index < sampleCount; ++index) {
    const int32_t step = kStepTable[stepIndex];
    int32_t difference = static_cast<int32_t>(samples[index]) - predictor;
    uint8_t code = 0;
    if (difference < 0) {
      code = 8;
      difference = -difference;
    }
    int32_t reconstructed = step >> 3;
    if (difference >= step) {
      code |= 4;
      difference -= step;
      reconstructed += step;
    }
    if (difference >= (step >> 1)) {
      code |= 2;
      difference -= step >> 1;
      reconstructed += step >> 1;
    }
    if (difference >= (step >> 2)) {
      code |= 1;
      reconstructed += step >> 2;
    }
    predictor += (code & 8) ? -reconstructed : reconstructed;
    predictor = constrain(predictor, -32768, 32767);
    stepIndex += kIndexTable[code & 7];
    stepIndex = constrain(stepIndex, 0, 88);

    if (lowNibble) {
      pending = code & 0x0F;
      lowNibble = false;
    } else {
      if (block.byteCount >= sizeof(block.data)) return 0;
      block.data[block.byteCount++] = pending | ((code & 0x0F) << 4);
      lowNibble = true;
    }
  }
  if (!lowNibble) {
    if (block.byteCount >= sizeof(block.data)) return 0;
    block.data[block.byteCount++] = pending;
  }
  carriedStepIndex = static_cast<uint8_t>(stepIndex);
  return block.byteCount;
}
