/**
 * api.js — HTTP request wrapper for the SlyLED REST API.
 * Replaces the global `ra()` function with a module export.
 * @module api
 */

/**
 * Make an async API request.
 * @param {string} method - HTTP method (GET, POST, PUT, DELETE)
 * @param {string} path - API path (e.g. '/api/fixtures')
 * @param {object|null} body - JSON body for POST/PUT
 * @param {function} callback - Called with parsed JSON or null on error
 */
export function api(method, path, body, callback) {
  const x = new XMLHttpRequest();
  x.open(method, path, true);
  x.timeout = 15000;
  if (body) x.setRequestHeader('Content-Type', 'application/json');
  x.onload = function () {
    try {
      callback(JSON.parse(x.responseText));
    } catch (e) {
      callback(null);
    }
  };
  x.onerror = function () { callback(null); };
  x.ontimeout = function () { callback(null); };
  x.send(body ? JSON.stringify(body) : null);
}

/**
 * Promise-based API request.
 * @param {string} method
 * @param {string} path
 * @returns {Promise<object>}
 */
export function apiFetch(method, path, body) {
  return new Promise((resolve, reject) => {
    api(method, path, body, (r) => {
      if (r) resolve(r);
      else reject(new Error(`API ${method} ${path} failed`));
    });
  });
}
