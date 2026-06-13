// page-hook.js - runs in the eCloud page context and captures point query payloads.

(function() {
  "use strict";

  const ENDPOINT_SUFFIX = "/business/point/pointDataShowList";
  const MESSAGE_TYPE = "ECLOUD_POINT_QUERY_CAPTURED";

  if (window.__ECLOUD_POINT_QUERY_HOOK_INSTALLED__) {
    return;
  }
  window.__ECLOUD_POINT_QUERY_HOOK_INSTALLED__ = true;

  function isPointQueryUrl(url) {
    return String(url || "").includes(ENDPOINT_SUFFIX);
  }

  function parseBody(body) {
    if (!body) return null;
    if (typeof body === "string") {
      try {
        return JSON.parse(body);
      } catch (error) {
        return null;
      }
    }
    if (body instanceof URLSearchParams) {
      return Object.fromEntries(body.entries());
    }
    if (body instanceof FormData) {
      return Object.fromEntries(body.entries());
    }
    if (typeof body === "object") {
      try {
        return JSON.parse(JSON.stringify(body));
      } catch (error) {
        return null;
      }
    }
    return null;
  }

  function emit(url, body, transport) {
    const payload = parseBody(body);
    if (!payload) return;
    window.postMessage(
      {
        type: MESSAGE_TYPE,
        url: String(url || ""),
        payload,
        transport,
        capturedAt: new Date().toISOString(),
      },
      "*",
    );
  }

  const originalFetch = window.fetch;
  if (typeof originalFetch === "function") {
    window.fetch = function(input, init) {
      try {
        const url = typeof input === "string" ? input : input && input.url;
        const body = init && Object.prototype.hasOwnProperty.call(init, "body") ? init.body : null;
        if (isPointQueryUrl(url)) {
          emit(url, body, "fetch");
        }
      } catch (error) {
        // Never break the production page request.
      }
      return originalFetch.apply(this, arguments);
    };
  }

  const OriginalXMLHttpRequest = window.XMLHttpRequest;
  if (typeof OriginalXMLHttpRequest === "function") {
    const originalOpen = OriginalXMLHttpRequest.prototype.open;
    const originalSend = OriginalXMLHttpRequest.prototype.send;

    OriginalXMLHttpRequest.prototype.open = function(method, url) {
      this.__ecloudPointQueryUrl = url;
      return originalOpen.apply(this, arguments);
    };

    OriginalXMLHttpRequest.prototype.send = function(body) {
      try {
        if (isPointQueryUrl(this.__ecloudPointQueryUrl)) {
          emit(this.__ecloudPointQueryUrl, body, "xhr");
        }
      } catch (error) {
        // Never break the production page request.
      }
      return originalSend.apply(this, arguments);
    };
  }
})();
