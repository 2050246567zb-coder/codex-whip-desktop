// Isolated diagnostic: no Bluetooth, Wi-Fi, microphone, gesture or desktop code.
// GPIO and MPU settings deliberately match product firmware 0.7.4.
#include <Arduino.h>
#include <Wire.h>

constexpr int SDA_PIN=0, SCL_PIN=1;
constexpr uint8_t ADDRESS=0x68;
uint32_t rate=400000, good=0, errors=0, notReady=0;
uint32_t lastSample=0, lastReport=0;
bool ready=false;
String command;

bool readReg(uint8_t reg,uint8_t* data,size_t count) {
  Wire.beginTransmission(ADDRESS); Wire.write(reg);
  if (Wire.endTransmission(false)!=0) return false;
  if (Wire.requestFrom(ADDRESS,count,true)!=count) return false;
  for(size_t i=0;i<count;i++) data[i]=Wire.read();
  return true;
}
bool writeReg(uint8_t reg,uint8_t value) {
  Wire.beginTransmission(ADDRESS); Wire.write(reg); Wire.write(value);
  uint8_t error=Wire.endTransmission();
  if(error) Serial.printf("WRITE_FAIL,reg=%02X,error=%u\n",reg,error);
  return error==0;
}
void probe() {
  Serial.printf("BUS,hz=%lu,SDA=%d,SCL=%d\n",(unsigned long)rate,digitalRead(SDA_PIN),digitalRead(SCL_PIN));
  for(uint8_t address:{uint8_t(0x68),uint8_t(0x69)}) {
    Wire.beginTransmission(address);
    uint8_t error=Wire.endTransmission();
    Serial.printf("PROBE,address=%02X,error=%u\n",address,error);
  }
}
void initImu() {
  ready=false;
  uint8_t id=0;
  if(!readReg(0x75,&id,1)) {Serial.println("INIT_FAIL,WHOAMI_READ"); return;}
  Serial.printf("WHOAMI,%02X\n",id);
  if(id!=0x68) {Serial.println("INIT_FAIL,WHOAMI_VALUE"); return;}
  if(!writeReg(0x6B,0x80)) return;
  delay(100);
  if(!(writeReg(0x6B,1)&&writeReg(0x6C,0)&&writeReg(0x1A,2)&&
       writeReg(0x19,1)&&writeReg(0x1B,0x18)&&writeReg(0x1C,0x18)&&writeReg(0x38,1))) return;
  delay(50);
  ready=true;
  Serial.println("INIT_OK");
}
int16_t signedBE(const uint8_t* p) {return int16_t((uint16_t(p[0])<<8)|p[1]);}
void setup() {
  Serial.begin(115200); Serial.setTxTimeoutMs(0); delay(250);
  Serial.println("USB_IMU_DIAG,1,BLUETOOTH_NOT_INITIALIZED");
  bool started=Wire.begin(SDA_PIN,SCL_PIN,rate); Wire.setTimeOut(20);
  Serial.printf("WIRE_BEGIN,%d\n",started);
  initImu();
}
void loop() {
  for(int n=0;n<64&&Serial.available();n++) {
    char c=Serial.read();
    if(c=='\n') {
      command.trim();
      if(command=="PROBE") probe();
      else if(command=="INIT") initImu();
      else if(command=="RELEASE") {
        ready=false;
        bool stopped=Wire.end();
        // High impedance with weak pull-ups, never drive a potentially shorted line high.
        pinMode(SDA_PIN,INPUT_PULLUP); pinMode(SCL_PIN,INPUT_PULLUP);
        delay(10);
        Serial.printf("RELEASED,wire_end=%d,SDA=%d,SCL=%d\n",stopped,digitalRead(SDA_PIN),digitalRead(SCL_PIN));
      }
      else if(command=="SLOW"||command=="FAST") {
        rate=command=="SLOW"?100000:400000;
        // Restart host controller only. Separate from baseline and logged.
        Wire.end();
        bool ok=Wire.begin(SDA_PIN,SCL_PIN,rate); Wire.setTimeOut(20);
        Serial.printf("HOST_RESTART,%lu,%d\n",(unsigned long)rate,ok);
        probe(); initImu();
      }
      command="";
    } else if(command.length()<32) command+=c;
  }
  uint32_t now=millis();
  if(ready&&now-lastSample>=10) {
    lastSample=now;
    uint8_t status=0,data[14];
    if(!readReg(0x3A,&status,1)) ++errors;
    else if(!(status&1)) ++notReady;
    else if(!readReg(0x3B,data,14)) ++errors;
    else {
      ++good;
      Serial.printf("SAMPLE,%lu,%d,%d,%d,%d,%d,%d\n",(unsigned long)now,
        signedBE(data),signedBE(data+2),signedBE(data+4),signedBE(data+8),signedBE(data+10),signedBE(data+12));
    }
  }
  if(now-lastReport>=1000) {
    lastReport=now;
    Serial.printf("STATS,ready=%d,ok=%lu,errors=%lu,not_ready=%lu,SDA=%d,SCL=%d\n",
      ready,(unsigned long)good,(unsigned long)errors,(unsigned long)notReady,digitalRead(SDA_PIN),digitalRead(SCL_PIN));
  }
  delay(1);
}
