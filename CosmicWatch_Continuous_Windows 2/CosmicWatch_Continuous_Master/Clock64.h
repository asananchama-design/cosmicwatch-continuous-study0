#ifndef CW_CLOCK64_H
#define CW_CLOCK64_H
#include <stdint.h>
// Call at least once per 32-bit micros() cycle (~71.6 minutes).
// Firmware calls this on every loop, including while waiting for a pulse to end.
struct Clock64 {
  uint32_t previous = 0;
  uint64_t high = 0;
  uint64_t extend(uint32_t low) {
    if (low < previous) high += (uint64_t(1) << 32);
    previous = low;
    return high + low;
  }
};
#endif
