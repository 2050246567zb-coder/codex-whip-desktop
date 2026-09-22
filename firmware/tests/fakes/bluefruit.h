#pragma once
#include <algorithm>
#include <cstdint>
#include <cstring>
#include <deque>
#include <string>
#include <vector>

constexpr int pdPASS = 1;
constexpr uint16_t BLE_CONN_HANDLE_INVALID = 0xffff;
struct FakeQueue { size_t capacity, itemSize; std::deque<std::vector<uint8_t>> items; };
using QueueHandle_t = FakeQueue*;
using TaskHandle_t = void*;
inline QueueHandle_t xQueueCreate(size_t n,size_t size) { return new FakeQueue{n,size,{}}; }
inline int xQueueReset(QueueHandle_t q) { q->items.clear(); return pdPASS; }
inline size_t uxQueueMessagesWaiting(QueueHandle_t q) { return q->items.size(); }
inline int xQueueSend(QueueHandle_t q,const void* p,int) {
 if(q->items.size()==q->capacity)return 0;
 auto b=static_cast<const uint8_t*>(p);q->items.emplace_back(b,b+q->itemSize);return pdPASS;
}
inline int xQueueOverwrite(QueueHandle_t q,const void* p) { q->items.clear();return xQueueSend(q,p,0); }
inline int xQueueReceive(QueueHandle_t q,void* p,int) {
 if(q->items.empty())return 0;
 std::memcpy(p,q->items.front().data(),q->itemSize);q->items.pop_front();return pdPASS;
}
extern uint32_t fakeNow, fakeStop;
struct EndRun {};
inline uint32_t millis() { return fakeNow; }
inline uint32_t pdMS_TO_TICKS(uint32_t n) { return n; }
inline void vTaskDelay(uint32_t n) { fakeNow+=n; if(fakeNow>=fakeStop)throw EndRun{}; }
inline void taskYIELD() { if(fakeNow>=fakeStop)throw EndRun{}; }
extern void (*fakeEntry)(void*);
extern void* fakeContext;
inline int xTaskCreate(void (*fn)(void*),const char*,int,void* p,int,TaskHandle_t*) {
 fakeEntry=fn;fakeContext=p;return pdPASS;
}
struct BLEConnection { uint16_t getMtu() { return 23; } };
struct FakeBluefruit {
 uint16_t active=1; bool live=true; int disconnects=0; BLEConnection connection;
 bool connected(uint16_t h) { return live&&active==h; }
 BLEConnection* Connection(uint16_t h) { return connected(h)?&connection:nullptr; }
 void disconnect(uint16_t h) { if(connected(h)){live=false;++disconnects;} }
};
extern FakeBluefruit Bluefruit;
struct BLEUart {
 std::string delivered; int calls=0; int failAfter=-1;
 size_t write(uint16_t handle,const uint8_t* data,size_t n) {
  ++calls;
  if(!Bluefruit.connected(handle))return 0;
  if(failAfter>=0&&calls>failAfter){fakeNow+=100;return 0;}
  delivered.append(reinterpret_cast<const char*>(data),n);return n;
 }
};
