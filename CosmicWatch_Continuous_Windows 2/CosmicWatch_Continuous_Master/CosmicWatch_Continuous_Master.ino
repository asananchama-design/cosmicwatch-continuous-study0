/*
  CosmicWatch v2 / original 16 MHz ATmega328P Nano: continuous serial study.
  Protocol CW_CONTINUOUS_V1, firmware CW_CONT_1.0.0, 115200 baud, no SD card.
  Master records local singles; slave records local D6-gated coincidences.
  Based on the CosmicWatch v2 original D6 sequence (two A3 conversions).
  This software does NOT calibrate the coincidence window or synchronize clocks.
  UTC receipt timestamps are added by the Windows logger, not by the Nano.
  EXTERNAL AREF and fast ADC settings require the original v2 circuit.
  Original project: github.com/spenceraxani/CosmicWatch-Desktop-Muon-Detector-v2
*/
#include <EEPROM.h>
#include "Clock64.h"

#ifndef MASTER_MODE
#define MASTER_MODE 1
#endif
#if !defined(__AVR_ATmega328P__) || F_CPU != 16000000UL
#error "Requires original 16 MHz ATmega328P Nano / CosmicWatch v2 hardware."
#endif
#if MASTER_MODE != 0 && MASTER_MODE != 1
#error "MASTER_MODE must be 0 or 1."
#endif

const byte LED_PIN = 3;
const byte COINCIDENCE_PIN = 6;
const int SIGNAL_THRESHOLD = 50;
const int RESET_THRESHOLD = 25;
const uint64_t STATUS_INTERVAL_US = 10000000ULL;
Clock64 clock64;
uint64_t originUs = 0, nextStatusUs = 0;
uint64_t eventCount = 0, localTriggers = 0, busyUs = 0;
uint64_t activeBusyStart = 0;
bool waitingLow = false;
char detectorName[40];

uint64_t clockUs() { return clock64.extend(micros()); }

void print64(uint64_t value) {
  char digits[21];
  byte n = 0;
  do { digits[n++] = char('0' + value % 10); value /= 10; } while (value);
  while (n) Serial.write(digits[--n]);
}

void readName() {
  byte i = 0;
  for (; i < sizeof(detectorName) - 1; ++i) {
    byte c = EEPROM.read(i);
    if (c == 0 || c == 255) break;
    detectorName[i] = (c >= 32 && c <= 126) ? char(c) : '?';
  }
  detectorName[i] = '\0';
  if (!i) strcpy(detectorName, "UNNAMED");
}

void statusIfDue(uint64_t now) {
  if (now < nextStatusUs) return;
  // Status carries cumulative counters even if no coincidences occurred.
  uint64_t start = now;
  Serial.print(F("S,")); print64(now - originUs);
  Serial.print(','); print64(eventCount);
  Serial.print(','); print64(localTriggers);
  Serial.print(','); print64(busyUs + (waitingLow ? now - activeBusyStart : 0));
  Serial.print(','); Serial.println(waitingLow ? 1 : 0);
  Serial.flush();
  uint64_t end = clockUs();
  if (!waitingLow) busyUs += end - start;
  nextStatusUs = end + STATUS_INTERVAL_US;
}

void setup() {
  pinMode(LED_PIN, OUTPUT);
  digitalWrite(LED_PIN, LOW);
  digitalWrite(COINCIDENCE_PIN, LOW);
  pinMode(COINCIDENCE_PIN, MASTER_MODE ? OUTPUT : INPUT);
  Serial.begin(115200);
  analogReference(EXTERNAL);
  ADCSRA &= ~(_BV(ADPS0) | _BV(ADPS1) | _BV(ADPS2));
  ADCSRA |= _BV(ADPS0) | _BV(ADPS1); // Original prescaler 8, not precision ADC calibration.
  readName();
  Serial.println(F("# CW_CONTINUOUS_V1"));
  Serial.println(F("# firmware=CW_CONT_1.0.0"));
  Serial.print(F("# role="));
  Serial.println(MASTER_MODE ? F("MASTER_SINGLES") : F("SLAVE_COINCIDENCE"));
  Serial.print(F("# detector=")); Serial.println(detectorName);
  Serial.println(F("# signal_threshold=50"));
  Serial.println(F("# reset_threshold=25"));
  Serial.println(F("# coincidence_method=original_D6_level_two_A3_reads;window_not_calibrated"));
  Serial.println(F("# E,event_id,uptime_us,adc_trigger,adc_sample,local_triggers,busy_us_before_event,temp_adc"));
  Serial.println(F("# S,uptime_us,accepted_events,local_triggers,busy_us,awaiting_rearm"));
  Serial.println(F("# START"));
  Serial.flush();
  analogRead(A0);
  originUs = clockUs();
  nextStatusUs = originUs; // Immediate status verifies a functioning zero-event detector.
}

void loop() {
  uint64_t now = clockUs();
  // Nonblocking re-arm permits clock maintenance and status during a stuck HIGH input.
  if (waitingLow) {
    if (analogRead(A0) <= RESET_THRESHOLD) {
      busyUs += clockUs() - activeBusyStart;
      waitingLow = false;
    }
    statusIfDue(clockUs());
    return;
  }

  uint64_t busyStart = now;
  int trigger = analogRead(A0);
  if (trigger > SIGNAL_THRESHOLD) {
    uint32_t eventLow = micros();
    int sample = analogRead(A0);
    // Keep the original pulse and sampling sequence together: no serial or
    // 64-bit arithmetic inside this timing-sensitive section.
    if (MASTER_MODE) digitalWrite(COINCIDENCE_PIN, HIGH);
    analogRead(A3);
    bool keep = MASTER_MODE || digitalRead(COINCIDENCE_PIN) == HIGH;
    analogRead(A3);
    if (MASTER_MODE) digitalWrite(COINCIDENCE_PIN, LOW);
    uint64_t eventUs = clock64.extend(eventLow) - originUs;
    ++localTriggers;
    waitingLow = true;
    activeBusyStart = busyStart;
    if (keep) {
      ++eventCount;
      int tempAdc = (analogRead(A3) + analogRead(A3) + analogRead(A3)) / 3;
      digitalWrite(LED_PIN, HIGH);
      Serial.print(F("E,")); print64(eventCount);
      Serial.print(','); print64(eventUs);
      Serial.print(','); Serial.print(trigger);
      Serial.print(','); Serial.print(sample);
      Serial.print(','); print64(localTriggers);
      Serial.print(','); print64(busyUs);
      Serial.print(','); Serial.println(tempAdc);
      Serial.flush(); // Include serial transmission in the measured local busy interval.
      digitalWrite(LED_PIN, LOW);
    }
  }
  statusIfDue(clockUs());
}
