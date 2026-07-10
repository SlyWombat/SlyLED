/*
 * MmwHttp.h — minimal status/config web page for the MMwave node.
 *
 * Fleet parity (operator request, bench 2026-07-10): like the LED children,
 * the node serves a small page — live targets (x/y/distance/speed), frame
 * stats, WiFi info — plus a WiFi credentials form that writes NVS (the
 * provisioning path the fleet's children have via their config pages).
 * Strict-SPA rule honoured: the device is a JSON API (/status.json); the
 * page is a thin fetch-and-render shell.
 */

#ifndef MMW_HTTP_H
#define MMW_HTTP_H

void mmwHttpBegin();   // start the server (call once WiFi is up)
void mmwHttpPoll();    // service at most one request per call

#endif  // MMW_HTTP_H
