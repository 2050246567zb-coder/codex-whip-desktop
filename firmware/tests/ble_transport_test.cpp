#include <cassert>
#include <iostream>
#include "bluefruit.h"
#include "ble_transport.h"
#include "host_profile.h"
uint32_t fakeNow=0, fakeStop=0;
void (*fakeEntry)(void*)=nullptr;
void* fakeContext=nullptr;
FakeBluefruit Bluefruit;
void runFor(uint32_t ms) {
 fakeStop=fakeNow+ms;
 try {fakeEntry(fakeContext);} catch(const EndRun&) {}
}
bool send(WhipBleTransport& t,const std::string& s,bool motion=false) {
 return t.enqueue(reinterpret_cast<const uint8_t*>(s.data()),s.size(),motion);
}
bool sendAudio(WhipBleTransport& t,const std::string& s) {
 return t.enqueueAudio(reinterpret_cast<const uint8_t*>(s.data()),s.size());
}
void connect(WhipBleTransport& t,uint16_t handle=1) {
 Bluefruit={};Bluefruit.active=handle;t.open(handle);t.acceptSession(t.session());
}
int main() {
 for(auto os:{HostSystem::Compatible,HostSystem::MacOS,HostSystem::Linux}) {
  for(bool sleeping:{false,true}) {
   auto p=hostLinkParameters(hostProfile(os),sleeping);
   assert(p.minimum>=12 && p.minimum%12==0);
   assert(p.maximum>=p.minimum+12);
   assert(p.latency<=30);
   assert(p.supervisionTimeout>=200&&p.supervisionTimeout<=600);
   assert(p.maximum*1.25*(p.latency+1)*3<p.supervisionTimeout*10);
  }
 }
 HostSystem os=HostSystem::Compatible;
 assert(parseHostSystem("MACOS",os)&&hostProfile(os).batchSamples==2);
 assert(hostBatchSamples(hostProfile(os),12)==2); // 15 ms link
 assert(hostBatchSamples(hostProfile(os),24)==4); // 30 ms link
 assert(hostBatchSamples(hostProfile(os),80)==4); // bounded buffer
 assert(parseHostSystem("WINDOWS",os)&&hostProfile(os).batchSamples==4);
 assert(hostBatchSamples(hostProfile(os),6)==4);
 assert(!parseHostSystem("MACBOOK",os));
 {
  BLEUart uart; WhipBleTransport t(uart);assert(t.begin());connect(t);
  const std::string audio(75,'A');
  assert(send(t,audio+"\n"));assert(send(t,"old-motion\n",true));
  assert(send(t,"latest-motion\n",true));assert(send(t,"END\n"));
  assert(uart.calls==0); // Producers never enter the blocking BLE write.
  runFor(10);
  assert(uart.delivered==audio+"\nEND\nlatest-motion\n");
  assert(t.replacedMotion()==1);
 }
 {
  BLEUart uart;WhipBleTransport t(uart);assert(t.begin());connect(t);
  for(int i=0;i<8;++i)assert(send(t,std::to_string(i)+"\n"));
  assert(send(t,"M\n",true));runFor(10);
  assert(uart.delivered=="0\n1\n2\n3\nM\n4\n5\n6\n7\n");
 }
 {
  BLEUart uart;WhipBleTransport t(uart);assert(t.begin());connect(t);
  assert(send(t,"old-session\n"));t.close();
  assert(!send(t,"disconnected\n"));t.open(2);Bluefruit.active=2;
  assert(!send(t,"old-audio-before-main-reset\n"));
  t.acceptSession(t.session());assert(send(t,"new-session\n"));runFor(10);
  assert(uart.delivered=="new-session\n");
 }
 {
  BLEUart uart;WhipBleTransport t(uart);assert(t.begin());connect(t);
  for(int i=0;i<48;++i)assert(send(t,"STATUS\n"));
  assert(!send(t,"VOICE,END\n"));runFor(10);
  assert(Bluefruit.disconnects==1);assert(uart.delivered.empty());
 }
 {
  BLEUart uart;WhipBleTransport t(uart);assert(t.begin());connect(t);
  for(int i=0;i<45;++i)assert(send(t,"STATUS\n"));
  assert(!sendAudio(t,"framed-audio")); // Three control slots stay reserved.
  assert(send(t,"VOICE,END,1,0,TX_BACKPRESSURE\n"));
  runFor(10);
  assert(Bluefruit.disconnects==0);
 }
 {
  BLEUart uart;uart.failAfter=1;WhipBleTransport t(uart);assert(t.begin());connect(t);
  assert(send(t,std::string(70,'X')+"\n"));assert(send(t,"NEXT\n"));runFor(900);
  assert(uart.delivered==std::string(20,'X'));
  assert(Bluefruit.disconnects==1); // No next CSV line after partial failure.
  assert(t.longestWriteMs()==100);
 }
 std::cout << "Host profiles and transport scenarios passed\n";
}
