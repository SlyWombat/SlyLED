/*
 * SacnRecv.h — sACN E1.31 receiver for DMX bridge (#109).
 *
 * Listens on UDP 5568 for E1.31 packets and copies the 512-byte DMX
 * universe payload into dmxBuf when the universe + priority + sequence
 * checks pass.  Multicast group 239.255.<hi>.<lo> is joined for the
 * configured universe; on universe change the caller must invoke
 * sacnRejoin() so the listener follows the new group.
 *
 * Wire format reference: desktop/shared/dmx_sacn.py (build_sacn_data /
 * parse_sacn_data) — kept in sync.
 */

#ifndef SACNRECV_H
#define SACNRECV_H

#include "BoardConfig.h"

#ifdef BOARD_DMX_BRIDGE

#include <WiFiUdp.h>

constexpr uint16_t SACN_PORT     = 5568;
constexpr uint16_t SACN_BUF_SIZE = 660;       // 638-byte max + margin

extern WiFiUDP            sacnUDP;
extern volatile uint32_t  sacnRxCount;
extern volatile uint32_t  sacnPps;
extern char               sacnLastSender[16]; // dotted-quad, NUL-terminated

void sacnInit();        // bind socket + join multicast group for current universe
void sacnRejoin();      // re-join after a universe change (calls leave + join)
void pollSacn();        // non-blocking; called every loop()

#endif // BOARD_DMX_BRIDGE
#endif // SACNRECV_H
