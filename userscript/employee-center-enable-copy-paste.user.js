// ==UserScript==
// @name         Employee Center - Enable copy and paste
// @namespace    local.employee-center
// @version      1.0.0
// @description  Restore clipboard behavior by disabling only the MCAS clipboard hook.
// @match        https://ctsccprod.service-now.com.mcas.ms/*
// @run-at       document-start
// @grant        none
// @sandbox      raw
// @noframes
// @license      MIT
// ==/UserScript==

(() => {
  'use strict';

  // The top-page controller also visits TinyMCE's same-origin srcdoc frame.
  // Keep __JSWRAPPER itself: ServiceNow scripts depend on its URL rewriting.
  const patchedParsers = new WeakSet();
  const warnedDocuments = new WeakSet();
  const disabledClipboard = '{"Clipboard":"Disabled"}';

  function patchClipboardPolicy(wrapper) {
    if (typeof wrapper?.initialized !== 'function' || !wrapper.initialized()) {
      return;
    }

    const background = wrapper.ClientPolicyBackgroundCheck;
    const clipboard = wrapper.ClipboardPolicy;
    const originalParser = background?.parseBackgroundCheckResultFromServer;
    if (typeof originalParser !== 'function' ||
        typeof clipboard?.getPolicyStateInServer !== 'function') {
      return;
    }

    // Refreshes may contain Print, Download, and Upload alongside Clipboard.
    // Forward their values, the original receiver, and the parser's result.
    if (!patchedParsers.has(originalParser)) {
      const parser = function (...args) {
        if (typeof args[0] === 'string') {
          try {
            const update = JSON.parse(args[0]);
            if (update && typeof update === 'object' && !Array.isArray(update) &&
                Object.prototype.hasOwnProperty.call(update, 'Clipboard')) {
              update.Clipboard = 'Disabled';
              args[0] = JSON.stringify(update);
            }
          } catch {
            // Let the original parser handle malformed input as it normally does.
          }
        }
        return Reflect.apply(originalParser, this, args);
      };

      background.parseBackgroundCheckResultFromServer = parser;
      patchedParsers.add(parser);
    }

    // Bootstrap uses an internal parser, so update its already-registered state
    // too. Rechecking also covers wrapper reinitialization and frame reloads.
    if (clipboard.getPolicyStateInServer() !== 'Disabled') {
      background.parseBackgroundCheckResultFromServer(disabledClipboard);
    }
  }

  function visitFrame(frame) {
    let doc;
    try {
      doc = frame.document;
    } catch {
      // Cross-origin frames have independent scripts and are outside this scope.
      return;
    }
    if (!doc) return;

    try {
      patchClipboardPolicy(frame.__JSWRAPPER);
    } catch (error) {
      if (!warnedDocuments.has(doc)) {
        warnedDocuments.add(doc);
        console.warn('[Employee Center copy/paste] Could not update this frame:', error);
      }
    }

    // Do not cache WindowProxy objects: navigation can replace a frame's globals
    // while retaining its WindowProxy, and the new wrapper still needs a patch.
    for (let i = 0; i < frame.frames.length; i += 1) {
      visitFrame(frame.frames[i]);
    }
  }

  const refresh = () => visitFrame(window);
  refresh();
  window.addEventListener('load', refresh, true);
  window.addEventListener('pageshow', refresh);
  window.setInterval(refresh, 500);
})();
