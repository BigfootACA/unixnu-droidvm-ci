// SPDX-License-Identifier: BSD-2-Clause-Patent
// Opaque ABI storage for the exact original class, not source-level access
// to its private fields. The actual original metadata supplies its full
// Framebuffer/Service/IOService ancestry. Only qualified base prototypes may
// be called from source; opaque entries are original dispatch targets only.
#pragma once
#include <IOKit/IOService.h>
#include <libkern/libkern.h>
struct OriginalLegacySwapRequest;
class IOMobileFramebufferLegacy : public IOService {
 OSDeclareAbstractStructors(IOMobileFramebufferLegacy)
 unsigned char originalPrivate[0x18a0-sizeof(IOService)];
public:
 // Both fields are qualified by the complete original slot-249 getter.
 // This modifies this object's own mode state, never a vtable or executable.
 void installBootDimensions(uint32_t width,uint32_t height) {
  *reinterpret_cast<uint32_t *>(reinterpret_cast<unsigned char *>(this)+5808)=width;
  *reinterpret_cast<uint32_t *>(reinterpret_cast<unsigned char *>(this)+5812)=height;
 }
 const OriginalLegacySwapRequest *pendingSwapRequest() const {
  const OriginalLegacySwapRequest *request=nullptr;
  memcpy(&request,reinterpret_cast<const unsigned char *>(this)+5480,sizeof(request));return request;
 }
 // The real normal scanout backend removes its pending head before calling
 // slot 295. Slot 312 is a discard path (terminal state 8), not presentation.
 // The queue is a tail queue (head at +5480, tail pointer at +5488, each request's next at +2960 and previous-link at +2968): taking the head
 // moves the next request, if a client already queued one behind it (int-132: swap 3 arrived during swap 2's GPU copy and the old
 // single-entry form refused to retire swap 2, so every wait on it stayed busy), up to the head. The original queue's reference
 // moves to the current-swap slot for the original completion body.
 bool activateOnlyPendingSwap(const OriginalLegacySwapRequest *request) {
  auto self=reinterpret_cast<unsigned char *>(this);
  auto bytes=reinterpret_cast<const unsigned char *>(request);
  const void *current=nullptr,*head=nullptr,*tail=nullptr,*next=nullptr,*previous=nullptr;
  memcpy(&current,self+5472,8);memcpy(&head,self+5480,8);memcpy(&tail,self+5488,8);
  if(!request || current || head!=request)return false;
  memcpy(&next,bytes+2960,8);memcpy(&previous,bytes+2968,8);
  if(previous!=self+5480)return false;
  if(next) {
   const void *link=self+5480;
   memcpy(const_cast<unsigned char *>(reinterpret_cast<const unsigned char *>(next))+2968,&link,8);
  } else if(tail!=bytes+2960)return false;
  memcpy(self+5472,&request,8);
  if(next) {memcpy(self+5480,&next,8);}
  else {const void *empty=nullptr,*emptyTail=self+5480;memcpy(self+5480,&empty,8);memcpy(self+5488,&emptyTail,8);}
  return true;
 }
 void free() override;
 bool init(OSDictionary *properties=nullptr) override;
 IOReturn setProperties(OSObject *) override;
 void systemWillShutdown(IOOptionBits) override;
 IOService *probe(IOService *,SInt32 *) override;
 bool start(IOService *) override;
 void stop(IOService *) override;
 IOWorkLoop *getWorkLoop() const override;
 IOReturn newUserClient(task_t,void *,UInt32,IOUserClient **) override;
 IOReturn setPowerState(unsigned long,IOService *) override;
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque168();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque169();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque170();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque171();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque172();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque173();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque174();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque175();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque176();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque177();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque178();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque179();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque180();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque181();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque182();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque183();
 virtual bool isMainDisplay();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque185();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque186();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque187();
 virtual IOReturn getDotPitch(uint32_t *);
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque189();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque190();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque191();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque192();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque193();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque194();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque195();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque196();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque197();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque198();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque199();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque200();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque201();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque202();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque203();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque204();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque205();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque206();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque207();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque208();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque209();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque210();
 virtual __attribute__((noreturn)) void opaque211()=0;
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque212();
 virtual IOReturn supportedFrameInfo(uint32_t,uint32_t *,char *,uint32_t *)=0;
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque214();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque215();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque216();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque217();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque218();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque219();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque220();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque221();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque222();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque223();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque224();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque225();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque226();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque227();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque228();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque229();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque230();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque231();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque232();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque233();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque234();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque235();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque236();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque237();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque238();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque239();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque240();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque241();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque242();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque243();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque244();
 virtual __attribute__((noreturn)) void opaque245()=0;
 virtual __attribute__((noreturn)) void opaque246()=0;
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque247();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque248();
 virtual void getDisplayDimensions(uint32_t *,uint32_t *);
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque250();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque251();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque252();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque253();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque254();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque255();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque256();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque257();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque258();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque259();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque260();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque261();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque262();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque263();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque264();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque265();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque266();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque267();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque268();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque269();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque270();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque271();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque272();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque273();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque274();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque275();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque276();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque277();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque278();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque279();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque280();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque281();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque282();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque283();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque284();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque285();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque286();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque287();
 virtual bool startHardware();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque289();
 virtual void kickSwapQueue();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque291();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque292();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque293();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque294();
 virtual void completeCurrentSwap();
 virtual bool validateSwap(const OriginalLegacySwapRequest *);
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque297();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque298();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque299();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque300();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque301();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque302();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque303();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque304();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque305();
 virtual __attribute__((noreturn)) void opaque306()=0;
 virtual __attribute__((noreturn)) void opaque307()=0;
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque308();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque309();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque310();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque311();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque312();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque313();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque314();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque315();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque316();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque317();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque318();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque319();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque320();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque321();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque322();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque323();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque324();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque325();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque326();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque327();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque328();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque329();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque330();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque331();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque332();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque333();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque334();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque335();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque336();
 virtual IOReturn collectCompletionReports(uint32_t,void *,uint32_t *,uint32_t)=0;
 virtual __attribute__((noreturn)) void opaque338()=0;
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque339();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque340();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque341();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque342();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque343();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque344();
 virtual __attribute__((noreturn)) void opaque345()=0;
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque346();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque347();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque348();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque349();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque350();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque351();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque352();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque353();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque354();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque355();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque356();
 virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque357();
};
static_assert(sizeof(IOMobileFramebufferLegacy)==0x18a0,"Actual original instance extent");
