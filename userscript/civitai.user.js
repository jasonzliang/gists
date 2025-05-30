// ==UserScript==
// @name         Civitai Search Filter Fix
// @namespace    http://tampermonkey.net/
// @version      1.0
// @icon         https://registry.npmmirror.com/@lobehub/icons-static-png/latest/files/dark/civitai-color.png
// @description  Fix Civitai search by removing unsupported user.id filters
// @author       You
// @match        https://civitai.com/*
// @grant        none
// @run-at       document-start
// @downloadURL  https://github.com/jasonzliang/gists/raw/refs/heads/master/userscript/civitai.user.js
// @updateURL    https://github.com/jasonzliang/gists/raw/refs/heads/master/userscript/civitai.user.js
// ==/UserScript==

(function() {
    'use strict';

    console.log('Civitai Search Fix: Userscript loaded');

    // Store the original fetch function
    const originalFetch = window.fetch;

    // Override fetch to intercept search requests
    window.fetch = function(...args) {
        const [url, options] = args;

        // Check if this is a search request to Meilisearch
        if (url && (url.includes('search.civitai.com') || url.includes('/multi-search'))) {
            console.log('Civitai Search Fix: Intercepted search request', url);

            // If there's a request body, modify it
            if (options && options.body) {
                try {
                    const requestData = JSON.parse(options.body);

                    // Process each query in the multi-search request
                    if (requestData.queries && Array.isArray(requestData.queries)) {
                        requestData.queries.forEach((query, index) => {
                            if (query.filter) {
                                const originalFilter = query.filter;
                                const fixedFilter = fixSearchFilter(query.filter);

                                if (originalFilter !== fixedFilter) {
                                    console.log(`Civitai Search Fix: Modified filter for query ${index}:`);
                                    console.log('Original:', originalFilter);
                                    console.log('Fixed:', fixedFilter);
                                    query.filter = fixedFilter;
                                }
                            }
                        });
                    }

                    // Update the request body with fixed filters
                    options.body = JSON.stringify(requestData);

                } catch (error) {
                    console.error('Civitai Search Fix: Error parsing request body:', error);
                }
            }
        }

        // Call the original fetch with potentially modified arguments
        return originalFetch.apply(this, args);
    };

    /**
     * Fix search filter by removing or replacing unsupported user.id filters
     */
    function fixSearchFilter(filter) {
        if (!filter) return filter;

        // Handle array of filters
        if (Array.isArray(filter)) {
            return filter.map(f => fixSearchFilter(f)).filter(f => f !== null);
        }

        // Handle string filters
        if (typeof filter === 'string') {
            // Remove user.id filters entirely
            // Pattern matches: "user.id = 123456" or "(poi != true OR user.id = 123456)"
            let fixedFilter = filter;

            // Strategy 1: Remove the entire OR clause containing user.id
            fixedFilter = fixedFilter.replace(/\(poi != true OR user\.id = \d+\)/g, 'poi != true');

            // Strategy 2: Remove standalone user.id filters
            fixedFilter = fixedFilter.replace(/user\.id = \d+/g, '');

            // Strategy 3: Clean up any leftover logical operators
            fixedFilter = fixedFilter.replace(/\s+AND\s+AND\s+/g, ' AND ');
            fixedFilter = fixedFilter.replace(/^\s*AND\s+/g, '');
            fixedFilter = fixedFilter.replace(/\s+AND\s*$/g, '');
            fixedFilter = fixedFilter.replace(/\(\s*\)/g, '');

            // If the filter becomes empty, return a safe default
            if (!fixedFilter.trim()) {
                return 'minor != true'; // Basic safety filter
            }

            return fixedFilter.trim();
        }

        return filter;
    }

    // Also intercept XMLHttpRequest for older code
    const originalXHROpen = XMLHttpRequest.prototype.open;
    const originalXHRSend = XMLHttpRequest.prototype.send;

    XMLHttpRequest.prototype.open = function(method, url, ...args) {
        this._url = url;
        this._method = method;
        return originalXHROpen.apply(this, [method, url, ...args]);
    };

    XMLHttpRequest.prototype.send = function(data) {
        if (this._url && this._url.includes('search.civitai.com') && data) {
            try {
                const requestData = JSON.parse(data);
                if (requestData.queries && Array.isArray(requestData.queries)) {
                    requestData.queries.forEach((query, index) => {
                        if (query.filter) {
                            const originalFilter = query.filter;
                            const fixedFilter = fixSearchFilter(query.filter);

                            if (originalFilter !== fixedFilter) {
                                console.log(`Civitai Search Fix (XHR): Modified filter for query ${index}:`);
                                console.log('Original:', originalFilter);
                                console.log('Fixed:', fixedFilter);
                                query.filter = fixedFilter;
                            }
                        }
                    });
                    data = JSON.stringify(requestData);
                }
            } catch (error) {
                console.error('Civitai Search Fix (XHR): Error parsing request:', error);
            }
        }

        return originalXHRSend.apply(this, [data]);
    };

    console.log('Civitai Search Fix: Request interception set up successfully');
})();
