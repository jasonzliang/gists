// ==UserScript==
// @name         Auto MathJax Renderer
// @icon         https://upload.wikimedia.org/wikipedia/commons/9/92/LaTeX_logo.svg
// @namespace    http://tampermonkey.net/
// @version      0.6
// @description  Automatically render LaTeX math formulas on the page using MathJax
// @match        https://claude.ai/*
// @grant        none
// ==/UserScript==
(function () {
    'use strict';

    // Insert MathJax library
    const script = document.createElement('script');
    script.type = 'text/javascript';
    script.src = 'https://cdnjs.cloudflare.com/ajax/libs/mathjax/2.7.7/MathJax.js?config=TeX-AMS_CHTML';
    document.getElementsByTagName('head')[0].appendChild(script);

    // Configure MathJax
    window.MathJax = {
        tex2jax: {
            inlineMath: [['$', '$'], ['\\(', '\\)']],
            displayMath: [['$$', '$$'], ['\\[', '\\]']],
            processEscapes: true
        },
        CommonHTML: { linebreaks: { automatic: true } },
        "HTML-CSS": { linebreaks: { automatic: true } },
        SVG: { linebreaks: { automatic: true } }
    };

    // Render function
    function renderMathJax() {
        if (window.MathJax && window.MathJax.Hub) {
            MathJax.Hub.Queue(["Typeset", MathJax.Hub]);
        }
    }

    // Auto-render on page load
    window.addEventListener('load', () => {
        setTimeout(renderMathJax, 1000); // Delay to ensure MathJax is loaded
    });

    // Debounced auto-render on DOM changes (new messages)
    let debounceTimer;
    const observer = new MutationObserver(() => {
        clearTimeout(debounceTimer);
        debounceTimer = setTimeout(renderMathJax, 1000);
    });

    // Start observing when DOM is ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', () => {
            observer.observe(document.body, {
                childList: true,
                subtree: true
            });
        });
    } else {
        observer.observe(document.body, {
            childList: true,
            subtree: true
        });
    }
})();
