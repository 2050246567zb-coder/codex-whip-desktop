#pragma once
#include <stdint.h>
#include <math.h>

// Hardware-independent policy. No acceleration integration or absolute position.
class PowerIdle {
 public:
  static constexpr uint32_t timeoutMs = 300000;
  void reset(uint32_t now) { lastMotion = now; seeded = false; wakeHits = 0; moving = false; }
  bool idle(uint32_t now, float ax, float ay, float az, float gx, float gy, float gz) {
    if (!finite(ax, ay, az) || !finite(gx, gy, gz)) { reset(now); return false; }
    const float norm = sqrtf(ax*ax+ay*ay+az*az);
    const float delta = distance(ax,ay,az);
    const float gyro2 = gx*gx+gy*gy+gz*gz;
    if (!seeded || delta > .45f || fabsf(norm-1.f) > .45f || gyro2 > 6400.f) {
      x=ax; y=ay; z=az; seeded=true; lastMotion=now;
      moving=false;
      return false;
    }
    if (delta > .15f || fabsf(norm-1.f) > .25f || gyro2 > 400.f) {
      if (!moving) { moving=true; motionSince=now; }
      if (uint32_t(now-motionSince) >= 80) {
        x=ax; y=ay; z=az; lastMotion=now; moving=false;
      }
      return false;
    }
    moving=false;
    return uint32_t(now-lastMotion) >= timeoutMs;
  }
  void anchor(float ax, float ay, float az) { x=ax; y=ay; z=az; wakeHits=0; }
  bool wake(float ax, float ay, float az) {
    if (!finite(ax,ay,az)) return false;
    const float movement=distance(ax,ay,az);
    // A single clear pickup, or two consecutive smaller changes. Fixed anchor
    // also catches gradual tilting; tiny sensor noise cannot walk the baseline.
    if (movement > .35f) return true;
    wakeHits = movement > .15f ? wakeHits+1 : 0;
    return wakeHits >= 2;
  }
 private:
  static bool finite(float a,float b,float c) { return isfinite(a)&&isfinite(b)&&isfinite(c); }
  float distance(float a,float b,float c) const { return sqrtf((a-x)*(a-x)+(b-y)*(b-y)+(c-z)*(c-z)); }
  uint32_t lastMotion=0;
  uint32_t motionSince=0;
  float x=0,y=0,z=1;
  bool seeded=false;
  bool moving=false;
  uint8_t wakeHits=0;
};

// Pure policy regression, callable over BLE without waiting five real minutes.
inline uint8_t powerIdleSelfTest() {
  uint8_t passed=0;
  PowerIdle p;
  p.reset(0);
  if (!p.idle(0,0,0,1,2,-4,0)) ++passed;
  if (!p.idle(299999,.005f,0,1,2,-4,0)) ++passed;
  if (p.idle(300000,0,0,1,2,-4,0)) ++passed;
  if (!p.idle(300001,0,0,1,90,0,0)) ++passed;
  if (!p.idle(600000,0,0,1,0,0,0)) ++passed;
  if (p.idle(600001,0,0,1,0,0,0)) ++passed;
  if (!p.idle(600002,.2f,0,1,0,0,0)) ++passed;
  p.reset(0xffff0000u);
  p.idle(0xffff0000u,0,0,1,0,0,0);
  if (p.idle(uint32_t(0xffff0000u+300000u),0,0,1,0,0,0)) ++passed;
  p.anchor(0,0,1);
  if (!p.wake(.02f,0,1)) ++passed;
  if (!p.wake(.2f,0,1) && p.wake(.2f,0,1)) ++passed;
  p.anchor(0,0,1);
  if (p.wake(.4f,0,1)) ++passed;
  p.anchor(0,0,1);
  if (!p.wake(.2f,0,1) && !p.wake(0,0,1) && !p.wake(.2f,0,1)) ++passed;
  return passed;
}
