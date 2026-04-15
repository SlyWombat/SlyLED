/**
 * event-bus.js — Lightweight pub/sub for cross-module communication.
 * Prevents circular imports between modules.
 * @module event-bus
 */
const _listeners = {};

export const EventBus = {
  on(event, handler) {
    if (!_listeners[event]) _listeners[event] = [];
    _listeners[event].push(handler);
  },
  off(event, handler) {
    if (!_listeners[event]) return;
    _listeners[event] = _listeners[event].filter(h => h !== handler);
  },
  emit(event, ...args) {
    if (!_listeners[event]) return;
    _listeners[event].forEach(h => { try { h(...args); } catch (e) { console.error(`EventBus ${event}:`, e); } });
  },
};
