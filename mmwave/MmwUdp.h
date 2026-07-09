/*
 * MmwUdp.h — fleet UDP protocol for the MMwave node: PONG discovery,
 * STATUS_REQ/RESP, OTA trigger, and MMW_TARGETS (0x70) reporting.
 */

#ifndef MMW_UDP_H
#define MMW_UDP_H

#include <stdint.h>
#include "MmwProtocol.h"

void mmwUdpBegin();                 // bind 4210 + broadcast boot PONG
void mmwUdpPoll();                  // handle PING / STATUS_REQ / OTA_UPDATE
void mmwUdpSendTargets(const MmwTarget targets[MMW_MAX_TARGETS],
                       uint8_t count, uint8_t flags);
void mmwUdpBroadcastPong();

#endif  // MMW_UDP_H
