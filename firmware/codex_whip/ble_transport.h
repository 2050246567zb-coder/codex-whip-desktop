#pragma once
#include <bluefruit.h>
#include <atomic>

// Only this worker writes to BLEUart. Bluefruit's notify path can wait for a
// semaphore, so even a single write must never run in the IMU sampling loop.
// Complete records are serialized. Pending motion is replaceable, but a
// partially transmitted record is completed or the connection is closed.
class WhipBleTransport {
 public:
  static constexpr size_t kRecordBytes = 384;
  static constexpr size_t kReliableRecords = 48;
  static constexpr size_t kControlReserveRecords = 3;
  static constexpr uint32_t kSendDeadlineMs = 650;

  explicit WhipBleTransport(BLEUart& uart) : uart_(uart) {}

  bool begin() {
    reliable_ = xQueueCreate(kReliableRecords, sizeof(Record));
    motion_ = xQueueCreate(1, sizeof(Record));
    return reliable_ && motion_ &&
        xTaskCreate(taskEntry, "whip-ble-tx", 1024, this, 1, &task_) == pdPASS;
  }

  void open(uint16_t handle) {
    close();
    handle_.store(handle);
  }

  void close() {
    acceptedEpoch_.store(0);
    handle_.store(BLE_CONN_HANDLE_INVALID);
    epoch_.fetch_add(1);
    if (reliable_) xQueueReset(reliable_);
    if (motion_) xQueueReset(motion_);
    faultEpoch_.store(0);
  }

  void discardMotion() { if (motion_) xQueueReset(motion_); }

  uint32_t session() const { return epoch_.load(); }
  void acceptSession(uint32_t epoch) {
    if (epoch == epoch_.load()) acceptedEpoch_.store(epoch);
  }

  bool enqueue(const uint8_t* data, size_t length, bool motion = false) {
    const uint32_t epoch = epoch_.load();
    if (acceptedEpoch_.load() != epoch || !length || length > kRecordBytes || !reliable_ ||
        handle_.load() == BLE_CONN_HANDLE_INVALID) return false;
    Record record;
    record.epoch = epoch;
    record.length = length;
    memcpy(record.bytes, data, length);
    if (motion) {
      if (uxQueueMessagesWaiting(motion_)) replacedMotion_.fetch_add(1);
      return xQueueOverwrite(motion_, &record) == pdPASS;
    }
    // Full reliable queue cannot silently lose a VOICE START/END or ACK.
    if (xQueueSend(reliable_, &record, 0) == pdPASS) return true;
    faultEpoch_.store(epoch);
    return false;
  }

  // Audio is framed, sequenced and acknowledged by the desktop. Never turn
  // backpressure into a BLE disconnect: reserve room for VOICE END and report
  // failure to the recorder so it can abort only the current recording.
  bool enqueueAudio(const uint8_t* data, size_t length) {
    const uint32_t epoch = epoch_.load();
    if (acceptedEpoch_.load() != epoch || !length || length > kRecordBytes ||
        !reliable_ || handle_.load() == BLE_CONN_HANDLE_INVALID) return false;
    if (uxQueueMessagesWaiting(reliable_) >=
        kReliableRecords - kControlReserveRecords) return false;
    Record record;
    record.epoch = epoch;
    record.length = length;
    memcpy(record.bytes, data, length);
    return xQueueSend(reliable_, &record, 0) == pdPASS;
  }

  bool consumeAudioFault() { return false; }

  uint32_t replacedMotion() const { return replacedMotion_.load(); }
  uint32_t longestWriteMs() const { return longestWriteMs_.load(); }

 private:
  struct Record {
    uint32_t epoch;
    uint16_t length;
    uint8_t bytes[kRecordBytes];
  };
  BLEUart& uart_;
  QueueHandle_t reliable_ = nullptr;
  QueueHandle_t motion_ = nullptr;
  TaskHandle_t task_ = nullptr;
  std::atomic<uint16_t> handle_{BLE_CONN_HANDLE_INVALID};
  std::atomic<uint32_t> epoch_{1};
  std::atomic<uint32_t> acceptedEpoch_{0};
  std::atomic<uint32_t> faultEpoch_{0};
  std::atomic<uint32_t> replacedMotion_{0};
  std::atomic<uint32_t> longestWriteMs_{0};

  bool current(uint32_t epoch, uint16_t handle) {
    return epoch == epoch_.load() && handle == handle_.load() &&
           handle != BLE_CONN_HANDLE_INVALID && Bluefruit.connected(handle);
  }

  bool transmit(const Record& record, uint16_t handle) {
    const uint32_t started = millis();
    size_t offset = 0;
    while (offset < record.length && current(record.epoch, handle)) {
      BLEConnection* connection = Bluefruit.Connection(handle);
      if (!connection) return false;
      size_t limit = connection->getMtu() > 3 ? connection->getMtu() - 3 : 20;
      if (limit > 244) limit = 244;
      const size_t remaining = record.length - offset;
      const size_t chunk = remaining < limit ? remaining : limit;
      const uint32_t before = millis();
      const size_t sent = uart_.write(handle, record.bytes + offset, chunk);
      const uint32_t duration = millis() - before;
      if (duration > longestWriteMs_.load()) longestWriteMs_.store(duration);
      if (sent == chunk) offset += chunk;
      else if (sent != 0) return false; // Never retry an ambiguous fragment.
      if (offset == record.length) return true;
      if (millis() - started >= kSendDeadlineMs) return false;
      if (!sent) vTaskDelay(pdMS_TO_TICKS(1));
    }
    return false;
  }

  static void taskEntry(void* context) {
    static_cast<WhipBleTransport*>(context)->run();
  }

  void run() {
    Record record;
    uint8_t reliableBurst = 0;
    for (;;) {
      const uint16_t handle = handle_.load();
      const uint32_t epoch = epoch_.load();
      if (faultEpoch_.load() == epoch && current(epoch, handle)) {
        Bluefruit.disconnect(handle);
        vTaskDelay(pdMS_TO_TICKS(2));
        continue;
      }
      // Bound command bursts so motion cannot starve during configuration.
      bool found = false;
      if (reliableBurst >= 4) {
        found = xQueueReceive(motion_, &record, 0) == pdPASS;
        reliableBurst = 0;
      }
      if (!found && xQueueReceive(reliable_, &record, 0) == pdPASS) {
        found = true;
        ++reliableBurst;
      }
      if (!found) {
        found = xQueueReceive(motion_, &record, 0) == pdPASS;
        reliableBurst = 0;
      }
      if (!found) { vTaskDelay(pdMS_TO_TICKS(1)); continue; }
      const uint16_t sendHandle = handle_.load();
      if (!current(record.epoch, sendHandle)) continue;
      if (!transmit(record, sendHandle) && current(record.epoch, sendHandle)) {
        // Flow-controlled audio never fills this queue; a failed write means
        // the peer may have received a partial record, so only a fresh session
        // can safely resume without concatenating the next packet.
        faultEpoch_.store(record.epoch);
      }
      taskYIELD();
    }
  }
};
