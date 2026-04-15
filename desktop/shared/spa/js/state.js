/**
 * state.js — Centralized application state store.
 * Getter/setter pattern so importers can't accidentally reassign state.
 * Setters emit EventBus events for cross-module reactivity.
 * @module state
 */
import { EventBus } from './event-bus.js';

const _s = {
  // Navigation
  currentTab: 'dash',
  // Fixtures & children
  fixtures: [],
  children: [],
  // Objects & effects
  objects: [],
  spatialFx: [],
  // Actions
  actions: [],
  // Timelines
  timelines: [],
  currentTimeline: null,
  // Layout
  layout: { canvasW: 3000, canvasH: 2000, children: [] },
  stage: { w: 3, h: 2, d: 1.5 },
  // Settings
  settings: {},
  // Tracking
  trackingCams: {},
  // UI state
  panelSections: { fixtures: true, objects: false },
  modalStack: [],
};

export const State = {
  // Generic getter
  get(key) { return _s[key]; },

  // Fixtures
  get fixtures() { return _s.fixtures; },
  setFixtures(v) { _s.fixtures = v; EventBus.emit('fixtures:changed', v); },

  // Children
  get children() { return _s.children; },
  setChildren(v) { _s.children = v; EventBus.emit('children:changed', v); },

  // Objects
  get objects() { return _s.objects; },
  setObjects(v) { _s.objects = v; EventBus.emit('objects:changed', v); },

  // Actions
  get actions() { return _s.actions; },
  setActions(v) { _s.actions = v; EventBus.emit('actions:changed', v); },

  // Timelines
  get timelines() { return _s.timelines; },
  setTimelines(v) { _s.timelines = v; EventBus.emit('timelines:changed', v); },
  get currentTimeline() { return _s.currentTimeline; },
  setCurrentTimeline(v) { _s.currentTimeline = v; EventBus.emit('timeline:selected', v); },

  // Settings
  get settings() { return _s.settings; },
  setSettings(v) { _s.settings = v; EventBus.emit('settings:changed', v); },

  // Layout / Stage
  get layout() { return _s.layout; },
  setLayout(v) { _s.layout = v; EventBus.emit('layout:changed', v); },
  get stage() { return _s.stage; },
  setStage(v) { _s.stage = v; EventBus.emit('stage:changed', v); },

  // Tracking
  get trackingCams() { return _s.trackingCams; },
  setTrackingCam(camId, active) {
    if (active) _s.trackingCams[camId] = true;
    else delete _s.trackingCams[camId];
    EventBus.emit('tracking:changed', camId, active);
  },

  // Navigation
  get currentTab() { return _s.currentTab; },
  setCurrentTab(v) { _s.currentTab = v; EventBus.emit('tab:changed', v); },
};
