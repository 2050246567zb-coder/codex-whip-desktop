#include "../codex_whip/voice_activity.h"

#include <cassert>
#include <stdint.h>

static void frames(VoiceActivityGate& gate, uint32_t first, uint32_t last,
                   uint16_t level) {
  for (uint32_t ms = first; ms <= last; ms += 25) gate.observe(level, ms);
}

int main() {
  VoiceActivityGate gate;
  gate.reset(2200);
  frames(gate, 0, 375, 100);
  gate.observe(900, 400);  // One click cannot start recording activity.
  frames(gate, 425, 575, 100);
  assert(!gate.speechDetected());

  frames(gate, 600, 975, 600);
  assert(gate.speechDetected());
  frames(gate, 1000, 2375, 100);  // Natural pause inside a sentence.
  assert(!gate.silenceExpired(2375));
  frames(gate, 2400, 2575, 230);  // Softer continuation extends recording.
  frames(gate, 2600, 4375, 160);  // Background noise is below continuation gate.
  assert(!gate.silenceExpired(4375));
  gate.observe(900, 4400);  // One noise spike cannot extend recording.
  frames(gate, 4425, 4750, 160);
  assert(!gate.silenceExpired(4750));
  gate.observe(160, 4800);
  assert(gate.silenceExpired(4800));
}
