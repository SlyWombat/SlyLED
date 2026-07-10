/*
 * MmwUdp.h — fleet UDP protocol for the MMwave node: PONG discovery,
 * STATUS_REQ/RESP, OTA trigger, and MMW_TARGETS (0x70) reporting.
 */

#ifndef MMW_UDP_H
#define MMW_UDP_H

#include <stdint.h>
#include <IPAddress.h>
#include "MmwProtocol.h"

void mmwUdpBegin();                 // bind 4210 + broadcast boot PONG
void mmwUdpPoll();                  // handle PING / STATUS_REQ / OTA_UPDATE
void mmwUdpSendTargets(const MmwTarget targets[MMW_MAX_TARGETS],
                       uint8_t count, uint8_t flags);
void mmwUdpBroadcastPong();
// #922 — fire-and-forget CMD_OTA_STATUS (0x51) report; called from MmwOta
// (the socket lives here). dest = the CMD_OTA_UPDATE sender.
void mmwUdpSendOtaStatus(IPAddress dest, uint8_t status, uint8_t progress);

#endif  // MMW_UDP_H
