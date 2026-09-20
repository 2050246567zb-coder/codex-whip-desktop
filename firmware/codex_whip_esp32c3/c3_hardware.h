#pragma once
#include <Arduino.h>
#include <Wire.h>
#include <NimBLEDevice.h>
#include <driver/i2s_std.h>
#include <atomic>
#include "whip_detector.h"

// User wiring: MPU SDA=0/SCL=1; mic BCLK=10/WS=20/SD=6, LR=GND.
// Use free pins on the same edge, avoiding GPIO7 LED and GPIO8/9 straps.
// GPIO20 is reserved for I2S WS here; diagnostics use USB CDC, not UART0 RX.
constexpr int kSda = 0, kScl = 1, kBclk = 10, kWs = 20, kMicData = 6;
constexpr uint8_t kMpuAddress = 0x68;
constexpr char kNusService[] = "6e400001-b5a3-f393-e0a9-e50e24dcca9e";
constexpr char kNusRx[] = "6e400002-b5a3-f393-e0a9-e50e24dcca9e";
constexpr char kNusTx[] = "6e400003-b5a3-f393-e0a9-e50e24dcca9e";
NimBLEServer* bleServer = nullptr;
NimBLECharacteristic* bleTx = nullptr;
QueueHandle_t rxQueue = nullptr;
std::atomic<bool> linkUp{false}, subscribed{false}, linkReset{false}, rxOverflow{false};
std::atomic<uint16_t> negotiatedMtu{23};
std::atomic<uint16_t> connectionInterval{0}, connectionLatency{0};
std::atomic<uint32_t> connectedAtMs{0};
std::atomic<uint8_t> linkTuneStage{0};
bool linkConnected() { return linkUp.load(); }
size_t blePayloadLimit() { return min(size_t(244), size_t(negotiatedMtu.load() - 3)); }

class LinkCallbacks : public NimBLEServerCallbacks {
  void onConnect(NimBLEServer* server, NimBLEConnInfo& info) override {
    negotiatedMtu = info.getMTU();
    connectionInterval=info.getConnInterval();
    connectionLatency=info.getConnLatency();
    connectedAtMs=millis(); linkTuneStage=0;
    linkReset = true;
    linkUp = true;
    server->updateConnParams(info.getConnHandle(), 6, 12, 0, 400);
  }
  void onDisconnect(NimBLEServer*, NimBLEConnInfo&, int) override {
    linkUp = false;
    subscribed = false;
    negotiatedMtu = 23;
    connectionInterval=0; connectionLatency=0; linkTuneStage=0;
    linkReset = true;
    xQueueReset(rxQueue);
    NimBLEDevice::startAdvertising();
  }
  void onMTUChange(uint16_t mtu, NimBLEConnInfo&) override { negotiatedMtu = mtu; }
  void onConnParamsUpdate(NimBLEConnInfo& info) override {
    connectionInterval=info.getConnInterval();
    connectionLatency=info.getConnLatency();
  }
};
// Connection-time requests can race service discovery on Windows. Retry only
// twice, after subscription, and only if the actual interval is still slow.
void serviceBleLink() {
  if(!linkConnected() || !subscribed) return;
  uint32_t age=millis()-connectedAtMs.load();
  uint8_t stage=linkTuneStage.load();
  if((stage==0 && age>=1200) || (stage==1 && age>=4000)) {
    linkTuneStage=stage+1;
    auto peer=bleServer->getPeerInfo(0);
    if(stage==0) {
      bleServer->setDataLen(peer.getConnHandle(),251);
      bleServer->updatePhy(peer.getConnHandle(),BLE_GAP_LE_PHY_2M_MASK,BLE_GAP_LE_PHY_2M_MASK,0);
    }
    if(peer.getConnInterval()>12 || peer.getConnLatency()!=0)
      bleServer->updateConnParams(peer.getConnHandle(),6,12,0,400);
  }
}
class RxCallbacks : public NimBLECharacteristicCallbacks {
  void onWrite(NimBLECharacteristic* ch, NimBLEConnInfo&) override {
    auto value = ch->getValue();
    for (size_t i = 0; i < value.size(); ++i) {
      char c = value.data()[i];
      if (xQueueSend(rxQueue, &c, 0) != pdTRUE) rxOverflow = true;
    }
  }
};
class TxCallbacks : public NimBLECharacteristicCallbacks {
  void onSubscribe(NimBLECharacteristic*, NimBLEConnInfo&, uint16_t value) override {
    subscribed = (value & 1) != 0;
  }
};
struct UartTransport {
  int available() { return uxQueueMessagesWaiting(rxQueue); }
  int read() { char c; return xQueueReceive(rxQueue, &c, 0) == pdTRUE ? uint8_t(c) : -1; }
  size_t write(const uint8_t* data, size_t length) {
    if (!linkConnected() || !subscribed) return 0;
    return bleTx->notify(data, length) ? length : 0;
  }
} bleUart;
void beginBle(const char* name, const char* version) {
  rxQueue = xQueueCreate(1024, sizeof(char));
  if (!rxQueue) { Serial.println("FATAL,RX_QUEUE"); while (true) delay(1000); }
  NimBLEDevice::init(name);
  NimBLEDevice::setMTU(247);
  bleServer = NimBLEDevice::createServer();
  static LinkCallbacks linkCallbacks;
  static RxCallbacks rxCallbacks;
  static TxCallbacks txCallbacks;
  bleServer->setCallbacks(&linkCallbacks, false);
  auto service = bleServer->createService(kNusService);
  auto rx = service->createCharacteristic(kNusRx, NIMBLE_PROPERTY::WRITE | NIMBLE_PROPERTY::WRITE_NR);
  rx->setCallbacks(&rxCallbacks);
  bleTx = service->createCharacteristic(kNusTx, NIMBLE_PROPERTY::NOTIFY);
  bleTx->setCallbacks(&txCallbacks);
  service->start();
  auto info = bleServer->createService("180A");
  info->createCharacteristic("2A29", NIMBLE_PROPERTY::READ)->setValue("Codex Whip");
  info->createCharacteristic("2A24", NIMBLE_PROPERTY::READ)->setValue("ESP32-C3 MPU6050 ICS43434");
  info->createCharacteristic("2A26", NIMBLE_PROPERTY::READ)->setValue(version);
  info->start();
  auto adv = NimBLEDevice::getAdvertising();
  adv->addServiceUUID(kNusService);
  adv->enableScanResponse(true);
  adv->setName(name);
  adv->start();
}

bool mpuWrite(uint8_t reg, uint8_t value) {
  Wire.beginTransmission(kMpuAddress); Wire.write(reg); Wire.write(value);
  return Wire.endTransmission() == 0;
}
bool mpuRead(uint8_t reg, uint8_t* data, size_t count) {
  Wire.beginTransmission(kMpuAddress); Wire.write(reg);
  if (Wire.endTransmission(false) != 0) return false;
  if (Wire.requestFrom(kMpuAddress, count, true) != count) return false;
  for (size_t i=0; i<count; ++i) data[i]=Wire.read();
  return true;
}
bool beginMotion() {
  Wire.begin(kSda, kScl, 400000); Wire.setTimeOut(20);
  uint8_t id = 0;
  if (!mpuRead(0x75, &id, 1) || id != 0x68) return false;
  if (!mpuWrite(0x6B, 0x80)) return false;
  delay(100);
  // PLL X gyro; DLPF 94/98 Hz; 1kHz/(1+1)=500 Hz; 2000 dps/16g.
  if (!(mpuWrite(0x6B, 1) && mpuWrite(0x6C, 0) && mpuWrite(0x1A, 2) &&
        mpuWrite(0x19, 1) && mpuWrite(0x1B, 0x18) && mpuWrite(0x1C, 0x18) &&
        mpuWrite(0x38, 1))) return false;
  delay(50);
  return true;
}
int16_t signedBE(const uint8_t* p) { return int16_t((uint16_t(p[0]) << 8) | p[1]); }
uint32_t imuReadOk=0, imuBusErrors=0, imuNotReady=0;
bool readMotion(MotionSample& sample) {
  uint8_t status=0, data[14];
  if (!mpuRead(0x3A, &status, 1)) { ++imuBusErrors; return false; }
  if (!(status & 1)) { ++imuNotReady; return false; }
  if (!mpuRead(0x3B,data,14)) { ++imuBusErrors; return false; }
  ++imuReadOk;
  sample.timestampMs=millis();
  sample.accelX=signedBE(data)/2048.0f;
  sample.accelY=signedBE(data+2)/2048.0f;
  sample.accelZ=signedBE(data+4)/2048.0f;
  sample.gyroX=signedBE(data+8)/16.4f;
  sample.gyroY=signedBE(data+10)/16.4f;
  sample.gyroZ=signedBE(data+12)/16.4f;
  return true;
}

i2s_chan_handle_t micRx=nullptr;
std::atomic<bool> micOverflow{false};
float micDc=0;
// 24-bit left-justified I2S, 32-bit stereo slots (64 BCLK/frame).
// Keep both slots in DMA and read LEFT explicitly, avoiding mono slot quirks.
bool IRAM_ATTR micLost(i2s_chan_handle_t, i2s_event_data_t*, void*) {
  micOverflow.store(true); return false;
}
void endMic() {
  if (micRx) { i2s_channel_disable(micRx); i2s_del_channel(micRx); micRx=nullptr; }
}
bool beginMic() {
  micDc=0; micOverflow=false;
  i2s_chan_config_t chan=I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_0,I2S_ROLE_MASTER);
  // 511*8=4088 bytes, below the ESP DMA descriptor limit of 4092.
  chan.dma_desc_num=16; chan.dma_frame_num=511;
  if (i2s_new_channel(&chan,nullptr,&micRx)!=ESP_OK) return false;
  i2s_std_config_t cfg={};
  cfg.clk_cfg=I2S_STD_CLK_DEFAULT_CONFIG(16000);
  cfg.slot_cfg=I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_32BIT,I2S_SLOT_MODE_STEREO);
  cfg.gpio_cfg.mclk=I2S_GPIO_UNUSED;
  cfg.gpio_cfg.bclk=gpio_num_t(kBclk); cfg.gpio_cfg.ws=gpio_num_t(kWs);
  cfg.gpio_cfg.dout=I2S_GPIO_UNUSED; cfg.gpio_cfg.din=gpio_num_t(kMicData);
  if (i2s_channel_init_std_mode(micRx,&cfg)!=ESP_OK) { endMic(); return false; }
  i2s_event_callbacks_t callbacks={}; callbacks.on_recv_q_ovf=micLost;
  if (i2s_channel_register_event_callback(micRx,&callbacks,nullptr)!=ESP_OK ||
      i2s_channel_enable(micRx)!=ESP_OK) { endMic(); return false; }
  return true;
}
