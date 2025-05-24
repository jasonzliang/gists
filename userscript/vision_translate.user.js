// ==UserScript==
// @name         Google Image Translator
// @namespace    https://github.com/yourusername
// @version      2.1.0
// @description  Translate text in images using Google Cloud Vision and Translation APIs
// @icon         https://www.svgrepo.com/show/375395/cloud-vision-api.svg
// @author       Anon
// @noframes
// -- NSFW START
// @match *://dynasty-scans.com/chapters/*
// @match *://hentaifr.net/*
// @match *://prismblush.com/comic/*
// @match *://www.hentairules.net/galleries*/picture.php*
// @match *://pururin.us/read/*
// @match *://hitomi.la/reader/*
// @match *://*.doujins.com/*
// @match *://www.8muses.com/comix/picture/*/*/*/*
// @match *://nowshelf.com/watch/*
// @match *://nhentai.net/g/*/*
// @match *://e-hentai.org/s/*/*
// @match *://exhentai.org/s/*/*
// @match *://www.fakku.net/*/*/read*
// @match *://hentaihere.com/m/*/*/*
// @match *://www.hentaihere.com/m/*/*/*
// @match *://*.tsumino.com/Read/View/*
// @match *://www.hentaibox.net/*/*
// @match *://*.hentai-free.org/*
// @match *://*.mangafap.com/image/*
// @match *://*.hentai4manga.com/hentai_manga/*
// @match *://*.heymanga.me/manga/*
// @match *://*.simply-hentai.com/*/page/*
// @match *://*.gameofscanlation.moe/projects/*/*
// @match *://*.luscious.net/c/*/pictures/album/*/id/*
// @match *://*.hentaifox.com/g/*
// @match *://*.hentai2read.com/*/*/*
// @match *://*.hentai.ms/manga/*/*
// -- NSFW END
// -- FOOLSLIDE NSFW START
// @match *://reader.yuriproject.net/read/*
// @match *://ecchi.japanzai.com/read/*
// @match *://h.japanzai.com/read/*
// @match *://reader.japanzai.com/read/*
// @match *://yomanga.co/reader/read/*
// @match *://raws.yomanga.co/read/*
// @match *://hentai.cafe/manga/read/*
// @match *://*.yuri-ism.net/slide/read/*
// -- FOOLSLIDE NSFW END
// @grant        GM_setValue
// @grant        GM_getValue
// @grant        GM_xmlhttpRequest
// @grant        GM_registerMenuCommand
// @connect      vision.googleapis.com
// @connect      translation.googleapis.com
// @connect      *
// @license      MPLv2
// @downloadURL  https://github.com/jasonzliang/gists/raw/refs/heads/master/userscripts/vision_translate.user.js
// @updateURL    https://github.com/jasonzliang/gists/raw/refs/heads/master/userscripts/vision_translate.user.js
// ==/UserScript==

(function() {
    'use strict';

    // Configuration
    const DEFAULT_CONFIG = {
        visionApiKey: '',
        translateApiKey: '',
        sourceLang: 'auto',
        targetLang: 'en',
        fontSize: 16,
        fontFamily: 'Arial, sans-serif',
        textColor: '#000000',
        minImageWidth: 100,
        minImageHeight: 50
    };

    let config = { ...DEFAULT_CONFIG, ...GM_getValue('imageTranslatorConfig', {}) };
    const buttonMap = new WeakMap(); // Track buttons for cleanup
    const overlayMap = new WeakMap(); // Track overlays for cleanup

    // Enhanced Language Detector
    class LanguageDetector {
        constructor() {
            this.cache = new Map();
            this.stats = new Map(); // Track detection accuracy
            this.MIN_CONFIDENCE = 0.7;
            this.COMMON_FALLBACKS = ['ja', 'ko', 'zh-CN', 'zh-TW', 'es', 'fr', 'de'];
        }

        // Pre-filter obviously non-translatable content
        isTranslatableText(text) {
            if (!text || text.trim().length < 2) return false;

            // Skip if mostly numbers/symbols
            const alphaRatio = (text.match(/[a-zA-Z\u0080-\uFFFF]/g) || []).length / text.length;
            if (alphaRatio < 0.3) return false;

            // Skip common non-translatable patterns
            const skipPatterns = [
                /^[\d\s\-\+\(\)\.]+$/, // Phone numbers, dates
                /^[A-Z]{2,}[\d\s]*$/, // License plates, codes
                /^https?:\/\//, // URLs
                /^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$/ // Emails
            ];

            return !skipPatterns.some(pattern => pattern.test(text.trim()));
        }

        // Get cached language detection
        getCachedDetection(text) {
            const key = this.generateCacheKey(text);
            return this.cache.get(key);
        }

        // Cache detection result
        cacheDetection(text, detection) {
            const key = this.generateCacheKey(text);
            this.cache.set(key, {
                ...detection,
                timestamp: Date.now()
            });

            // Clean old cache entries (keep last 100)
            if (this.cache.size > 100) {
                const oldestKey = this.cache.keys().next().value;
                this.cache.delete(oldestKey);
            }
        }

        generateCacheKey(text) {
            // Use first 50 chars + length as cache key
            return text.substring(0, 50) + ':' + text.length;
        }

        // Enhanced translation with smart auto-detect
        async translateWithSmartDetection(text, targetLang = 'en') {
            if (!this.isTranslatableText(text)) {
                throw new Error('Text does not appear to be translatable');
            }

            // Check cache first
            const cached = this.getCachedDetection(text);
            if (cached && Date.now() - cached.timestamp < 300000) { // 5 min cache
                const result = await this.translateText(text, cached.language, targetLang);
                return {
                    ...result,
                    detectedLanguage: cached.language,
                    confidence: cached.confidence,
                    detectionMethod: cached.method || 'cached'
                };
            }

            try {
                // Try auto-detect first
                const result = await this.translateText(text, 'auto', targetLang);

                if (result.confidence && result.confidence >= this.MIN_CONFIDENCE) {
                    // Cache successful detection
                    this.cacheDetection(text, {
                        language: result.detectedLanguage,
                        confidence: result.confidence,
                        method: 'auto'
                    });

                    this.updateStats(result.detectedLanguage, true);
                    return result;
                } else {
                    console.warn(`Low confidence detection: ${result.confidence}`);
                    return this.tryFallbackLanguages(text, targetLang);
                }

            } catch (error) {
                console.warn('Auto-detect failed:', error.message);
                return this.tryFallbackLanguages(text, targetLang);
            }
        }

        // Try common languages as fallback
        async tryFallbackLanguages(text, targetLang) {
            // Order fallbacks by previous success rate
            const orderedFallbacks = this.COMMON_FALLBACKS.sort((a, b) => {
                const aSuccess = this.stats.get(a)?.successRate || 0;
                const bSuccess = this.stats.get(b)?.successRate || 0;
                return bSuccess - aSuccess;
            });

            for (const sourceLang of orderedFallbacks) {
                try {
                    const result = await this.translateText(text, sourceLang, targetLang);

                    // Simple heuristic: if translation is very different from original,
                    // it's probably correct
                    const similarity = this.calculateSimilarity(text, result.translatedText);
                    if (similarity < 0.8) { // Less than 80% similar = good translation
                        this.updateStats(sourceLang, true);

                        // Cache this successful fallback
                        this.cacheDetection(text, {
                            language: sourceLang,
                            confidence: 0.6, // Lower confidence for fallback
                            method: 'fallback'
                        });

                        return {
                            ...result,
                            detectedLanguage: sourceLang,
                            detectionMethod: 'fallback'
                        };
                    }
                } catch (e) {
                    this.updateStats(sourceLang, false);
                    continue;
                }
            }

            throw new Error('Could not detect language with any method');
        }

        // Update success statistics for languages
        updateStats(language, success) {
            if (!this.stats.has(language)) {
                this.stats.set(language, { attempts: 0, successes: 0, successRate: 0 });
            }

            const stats = this.stats.get(language);
            stats.attempts++;
            if (success) stats.successes++;
            stats.successRate = stats.successes / stats.attempts;
        }

        // Simple text similarity calculation
        calculateSimilarity(text1, text2) {
            const len1 = text1.length;
            const len2 = text2.length;
            const maxLen = Math.max(len1, len2);

            if (maxLen === 0) return 1;

            // Simple character overlap measure
            const set1 = new Set(text1.toLowerCase().split(''));
            const set2 = new Set(text2.toLowerCase().split(''));
            const intersection = new Set([...set1].filter(x => set2.has(x)));

            return intersection.size / Math.max(set1.size, set2.size);
        }

        // Enhanced translate function with better error handling
        async translateText(text, sourceLang, targetLang) {
            return new Promise((resolve, reject) => {
                if (!config.translateApiKey) {
                    reject(new Error('Translation API key not configured'));
                    return;
                }

                const body = {
                    q: text,
                    target: targetLang,
                    format: 'text'
                };

                // Add source language if not auto
                if (sourceLang !== 'auto') {
                    body.source = sourceLang;
                }

                GM_xmlhttpRequest({
                    method: 'POST',
                    url: `https://translation.googleapis.com/language/translate/v2?key=${config.translateApiKey}`,
                    data: JSON.stringify(body),
                    headers: { 'Content-Type': 'application/json' },
                    timeout: 10000, // 10 second timeout
                    onload: response => {
                        try {
                            const result = JSON.parse(response.responseText);

                            if (result.error) {
                                reject(new Error(`Translation API: ${result.error.message}`));
                                return;
                            }

                            const translation = result.data?.translations?.[0];
                            if (!translation) {
                                reject(new Error('No translation returned'));
                                return;
                            }

                            resolve({
                                translatedText: translation.translatedText,
                                detectedLanguage: translation.detectedSourceLanguage,
                                confidence: translation.confidence || 1.0, // Default high confidence if not provided
                                originalText: text
                            });

                        } catch (e) {
                            reject(new Error(`Failed to parse translation response: ${e.message}`));
                        }
                    },
                    onerror: error => reject(new Error(`Network error: ${error.message}`)),
                    ontimeout: () => reject(new Error('Translation request timed out'))
                });
            });
        }

        // Get detection statistics for debugging
        getStats() {
            return Object.fromEntries(this.stats);
        }

        // Clear cache and stats
        reset() {
            this.cache.clear();
            this.stats.clear();
        }
    }

    const detector = new LanguageDetector();

    // Styles
    const styles = `
        .img-translator-btn{position:absolute;z-index:9999;background:rgba(34,34,34,.8);color:#fff;border:none;border-radius:50%;width:20px;height:20px;font-size:10px;line-height:1;cursor:pointer;opacity:.5;transition:opacity .2s;display:none;align-items:center;justify-content:center;padding:0;box-shadow:0 1px 3px rgba(0,0,0,.2)}
        .img-translator-btn:hover{opacity:1;background:rgba(34,34,34,1)}
        .img-translator-loading{position:fixed;top:50%;left:50%;transform:translate(-50%,-50%);z-index:10000;background:rgba(0,0,0,.8);color:#fff;padding:15px 25px;border-radius:5px;display:flex;align-items:center;font-size:14px}
        .img-translator-loading::after{content:'';width:16px;height:16px;margin-left:10px;border:2px solid #fff;border-radius:50%;border-top-color:transparent;animation:spin 1s linear infinite}
        @keyframes spin{to{transform:rotate(360deg)}}
        .img-translator-overlay{position:relative;width:100%;margin:10px auto;border:1px solid #ccc;border-radius:5px;overflow:hidden;background:#fff}
        .img-translator-settings{position:fixed;top:50%;left:50%;transform:translate(-50%,-50%);background:#fff;padding:20px;border-radius:8px;box-shadow:0 0 20px rgba(0,0,0,.5);z-index:10000;max-width:500px;max-height:80vh;overflow-y:auto;font-family:Arial,sans-serif;color:#000}
        .img-translator-settings h2{margin-top:0;border-bottom:1px solid #eee;padding-bottom:10px}
        .img-translator-settings h3{margin:15px 0 5px;color:#333}
        .img-translator-settings label{display:block;margin:12px 0 4px;font-weight:bold}
        .img-translator-settings input,.img-translator-settings select{width:100%;padding:8px;border:1px solid #ddd;border-radius:4px;box-sizing:border-box}
        .img-translator-settings input[type=checkbox]{width:auto}
        .img-translator-settings .btn-group{display:flex;justify-content:flex-end;margin-top:20px;gap:10px}
        .img-translator-settings button{padding:8px 16px;border:none;border-radius:4px;cursor:pointer}
        .img-translator-settings .btn-save{background:#4285f4;color:#fff}
        .img-translator-settings .btn-cancel{background:#f1f1f1;color:#333}
        .img-translator-toast{position:fixed;top:0px;left:0px;background:rgba(34,34,34,.8);color:#fff;padding:5px 12px;border-bottom-right-radius:5px;z-index:10000;font-size:11px;animation:img-translator-toast 3s forwards;box-shadow:0 2px 8px rgba(0,0,0,.2)}
        @keyframes img-translator-toast{0%{opacity:0;transform:translateY(-10px)}10%{opacity:1;transform:translateY(0)}90%{opacity:1;transform:translateY(0)}100%{opacity:0;transform:translateY(-10px)}}
    `;

    // Utilities
    const $ = sel => document.querySelector(sel);
    const $$ = sel => document.querySelectorAll(sel);

    function showToast(message, duration = 3000) {
        const toast = document.createElement('div');
        toast.className = 'img-translator-toast';
        toast.textContent = message;
        document.body.appendChild(toast);
        setTimeout(() => toast.remove(), duration);
    }

    function addStyles() {
        if (!$('style[data-img-translator]')) {
            const style = document.createElement('style');
            style.setAttribute('data-img-translator', '');
            style.textContent = styles;
            document.head.appendChild(style);
        }
    }

    // Language options
    const LANGUAGES = [
        ['en', 'English'], ['es', 'Spanish'], ['fr', 'French'], ['de', 'German'],
        ['it', 'Italian'], ['ja', 'Japanese'], ['ko', 'Korean'], ['pt', 'Portuguese'],
        ['ru', 'Russian'], ['zh-CN', 'Chinese (Simplified)'], ['zh-TW', 'Chinese (Traditional)'],
        ['ar', 'Arabic'], ['hi', 'Hindi']
    ];

    function getLanguageOptions(selected, includeAuto = false) {
        let options = includeAuto ? `<option value="auto" ${selected === 'auto' ? 'selected' : ''}>Auto-detect</option>` : '';
        LANGUAGES.forEach(([code, name]) => {
            options += `<option value="${code}" ${selected === code ? 'selected' : ''}>${name}</option>`;
        });
        return options;
    }

    // Button positioning
    function updateButtonPosition(img, button) {
        if (!img.isConnected || !button.isConnected) return;

        try {
            const rect = img.getBoundingClientRect();
            const margin = 5;
            const btnSize = 20;

            // Position at bottom-left corner
            button.style.top = `${window.scrollY + rect.bottom - btnSize - margin}px`;
            button.style.left = `${window.scrollX + rect.left + margin}px`;
        } catch (err) {
            console.warn('Error updating button position:', err);
        }
    }

    function updateAllButtonPositions() {
        $$('img[data-has-translator]').forEach(img => {
            const button = buttonMap.get(img);
            if (button) updateButtonPosition(img, button);
        });
    }

    // Image processing
    function isEligibleImage(img) {
        return img.complete &&
               img.width >= config.minImageWidth &&
               img.height >= config.minImageHeight &&
               !img.hasAttribute('data-has-translator');
    }

    function addTranslateButton(img) {
        if (buttonMap.has(img)) return;

        img.setAttribute('data-has-translator', 'true');

        const button = document.createElement('button');
        button.className = 'img-translator-btn';
        button.textContent = 'T';
        button.title = 'Translate image text';

        document.body.appendChild(button);
        buttonMap.set(img, button);

        // Position button
        updateButtonPosition(img, button);

        // Event handlers
        button.onclick = e => {
            e.stopPropagation();
            translateImage(img);
        };

        // Show/hide on hover
        const showButton = () => {
            button.style.display = 'flex';
            updateButtonPosition(img, button);
        };

        const hideButton = e => {
            if (!e.relatedTarget || !button.contains(e.relatedTarget)) {
                button.style.display = 'none';
            }
        };

        img.addEventListener('mouseenter', showButton);
        img.addEventListener('mouseleave', hideButton);
        button.addEventListener('mouseleave', hideButton);

        // Cleanup observer
        const observer = new MutationObserver(mutations => {
            mutations.forEach(mutation => {
                if (mutation.type === 'childList' &&
                    Array.from(mutation.removedNodes).includes(img)) {
                    cleanup();
                }
            });
        });

        const cleanup = () => {
            button.remove();
            buttonMap.delete(img);
            observer.disconnect();
        };

        if (img.parentNode) {
            observer.observe(img.parentNode, { childList: true });
        }
    }

    function addTranslateButtons() {
        $$('img').forEach(img => {
            if (img.complete) {
                if (isEligibleImage(img)) addTranslateButton(img);
            } else {
                img.addEventListener('load', () => {
                    if (isEligibleImage(img)) addTranslateButton(img);
                }, { once: true });
            }
        });
    }

    function removeAllButtons() {
        $$('.img-translator-btn').forEach(btn => btn.remove());
        $$('img[data-has-translator]').forEach(img => {
            img.removeAttribute('data-has-translator');
            buttonMap.delete(img);
        });
    }

    function removeAllOverlays() {
        $$('.img-translator-overlay').forEach(overlay => overlay.remove());
        overlayMap.clear();
    }

    // Image conversion
    function imageToBase64(img) {
        return new Promise((resolve, reject) => {
            const isExternal = !img.src.startsWith(window.location.origin) && !img.src.startsWith('data:');

            if (isExternal) {
                GM_xmlhttpRequest({
                    method: 'GET',
                    url: img.src,
                    responseType: 'arraybuffer',
                    onload: response => {
                        try {
                            const bytes = new Uint8Array(response.response);
                            let binary = '';
                            bytes.forEach(byte => binary += String.fromCharCode(byte));
                            resolve(btoa(binary));
                        } catch (e) {
                            reject(e);
                        }
                    },
                    onerror: reject
                });
            } else {
                try {
                    const canvas = document.createElement('canvas');
                    canvas.width = img.naturalWidth;
                    canvas.height = img.naturalHeight;
                    const ctx = canvas.getContext('2d');
                    ctx.drawImage(img, 0, 0);
                    resolve(canvas.toDataURL('image/jpeg').split(',')[1]);
                } catch (e) {
                    reject(e);
                }
            }
        });
    }

    // API calls
    function detectText(imageBase64) {
        return new Promise((resolve, reject) => {
            if (!config.visionApiKey) {
                reject(new Error('Vision API key not configured'));
                return;
            }

            GM_xmlhttpRequest({
                method: 'POST',
                url: `https://vision.googleapis.com/v1/images:annotate?key=${config.visionApiKey}`,
                data: JSON.stringify({
                    requests: [{
                        image: { content: imageBase64 },
                        features: [{ type: 'TEXT_DETECTION', maxResults: 50 }]
                    }]
                }),
                headers: { 'Content-Type': 'application/json' },
                onload: response => {
                    try {
                        const result = JSON.parse(response.responseText);
                        if (result.error) {
                            reject(new Error(`Vision API: ${result.error.message}`));
                            return;
                        }

                        const annotations = result.responses?.[0]?.textAnnotations;
                        if (!annotations?.length) {
                            resolve({ fullText: '', textBlocks: [] });
                            return;
                        }

                        resolve({
                            fullText: annotations[0].description,
                            textBlocks: annotations.slice(1).map(a => ({
                                text: a.description,
                                boundingBox: a.boundingPoly
                            }))
                        });
                    } catch (e) {
                        reject(e);
                    }
                },
                onerror: reject
            });
        });
    }

    // Legacy translateText function for backward compatibility
    function translateText(text) {
        return detector.translateText(text, config.sourceLang, config.targetLang);
    }

    // Helper function to get language name from code
    function getLanguageName(code) {
        const languageNames = {
            'en': 'English', 'ja': 'Japanese', 'ko': 'Korean', 'zh-CN': 'Chinese (Simplified)',
            'zh-TW': 'Chinese (Traditional)', 'es': 'Spanish', 'fr': 'French', 'de': 'German',
            'it': 'Italian', 'pt': 'Portuguese', 'ru': 'Russian', 'ar': 'Arabic', 'hi': 'Hindi'
        };
        return languageNames[code] || code;
    }

    // Translation display
    function createTranslationDisplay(img, result) {
        // Handle both enhanced result object and legacy string
        let translatedText, originalText, detectedLanguage, confidence, detectionMethod;

        if (typeof result === 'string') {
            // Legacy mode - just translated text
            translatedText = result;
            originalText = img.originalText || 'Original text not available';
            detectedLanguage = null;
            confidence = null;
            detectionMethod = null;
        } else {
            // Enhanced mode - full result object
            translatedText = result.translatedText;
            originalText = result.originalText;
            detectedLanguage = result.detectedLanguage;
            confidence = result.confidence;
            detectionMethod = result.detectionMethod;
        }

        // Remove existing overlay
        const existing = overlayMap.get(img);
        if (existing) existing.remove();

        const overlay = document.createElement('div');
        overlay.className = 'img-translator-overlay';
        overlay.style.width = `${img.width}px`;

        // Language detection info
        let detectionInfo = '';
        if (detectedLanguage) {
            const langName = getLanguageName(detectedLanguage);
            const confidencePercent = confidence ? Math.round(confidence * 100) : 100;
            const methodText = detectionMethod === 'fallback' ? ', fallback' :
                              detectionMethod === 'cached' ? ', cached' : '';
            detectionInfo = `<div style="font-size:11px;color:#666;margin-top:2px;">Detected: ${langName} (${confidencePercent}%${methodText})</div>`;
        }

        overlay.innerHTML = `
            <div style="padding:8px;background:#f0f0f0;border-bottom:1px solid #ccc;text-align:center;">
                <div style="font-weight:bold;">Translation Results</div>
                ${detectionInfo}
            </div>
            <div style="display:flex;padding:10px;background:#fff;">
                <div style="flex:1;padding-right:10px;border-right:1px solid #eee;">
                    <h4 style="margin:0 0 10px 0;font-size:12px;color:#666;">Translated</h4>
                    <div style="font-size:${config.fontSize}px;font-family:${config.fontFamily};color:${config.textColor};line-height:1.4;white-space:pre-wrap;">${translatedText}</div>
                </div>
                <div style="flex:1;padding-left:10px;">
                    <h4 style="margin:0 0 10px 0;font-size:12px;color:#666;">Original</h4>
                    <div style="font-size:${config.fontSize}px;font-family:${config.fontFamily};color:${config.textColor};line-height:1.4;white-space:pre-wrap;">${originalText}</div>
                </div>
            </div>
            <button style="position:absolute;top:8px;right:8px;background:none;border:none;font-size:18px;cursor:pointer;color:#666;">×</button>
        `;

        // Add confidence warning for low-confidence detections
        if (confidence && confidence < 0.8) {
            const warning = document.createElement('div');
            warning.style.cssText = 'padding:5px 10px;background:#fff3cd;border-top:1px solid #ffeaa7;font-size:11px;color:#856404;';
            warning.textContent = 'Low confidence detection. Consider setting source language manually for better accuracy.';
            overlay.appendChild(warning);
        }

        // Close button
        overlay.querySelector('button').onclick = () => {
            overlay.remove();
            overlayMap.delete(img);
        };

        // Insert after image
        img.parentNode.insertBefore(overlay, img.nextSibling);
        overlayMap.set(img, overlay);

        // Scroll into view
        setTimeout(() => overlay.scrollIntoView({ behavior: 'smooth', block: 'nearest' }), 100);
    }

    // Main translation function
    async function translateImage(img) {
        if (!config.visionApiKey || !config.translateApiKey) {
            showToast('Please configure API keys in settings');
            showSettingsPanel();
            return;
        }

        let loading;
        try {
            loading = document.createElement('div');
            loading.className = 'img-translator-loading';
            loading.textContent = 'Detecting text...';
            document.body.appendChild(loading);

            const base64 = await imageToBase64(img);
            const { fullText } = await detectText(base64);

            if (!fullText?.trim()) {
                showToast('No text detected in image');
                return;
            }

            loading.textContent = 'Translating...';

            // Store original text on image for legacy compatibility
            img.originalText = fullText;

            // Use enhanced translation with smart detection
            const result = await detector.translateWithSmartDetection(fullText, config.targetLang);
            createTranslationDisplay(img, result);

            const langInfo = result.detectedLanguage ? ` (${getLanguageName(result.detectedLanguage)})` : '';
            showToast(`Translation complete!${langInfo}`);

        } catch (error) {
            console.error('Translation error:', error);
            showToast(`Error: ${error.message}`);
        } finally {
            if (loading) loading.remove();
        }
    }

    // Settings panel
    function showSettingsPanel() {
        const panel = document.createElement('div');
        panel.className = 'img-translator-settings';
        panel.innerHTML = `
            <h2>Image Translator Settings</h2>
            <h3>API Keys</h3>
            <label>Vision API Key</label>
            <input type="password" id="visionApiKey" value="${config.visionApiKey}">
            <label>Translation API Key</label>
            <input type="password" id="translateApiKey" value="${config.translateApiKey}">
            <h3>Languages</h3>
            <label>Source Language</label>
            <select id="sourceLang">${getLanguageOptions(config.sourceLang, true)}</select>
            <label>Target Language</label>
            <select id="targetLang">${getLanguageOptions(config.targetLang)}</select>
            <h3>Appearance</h3>
            <label>Font Size (px)</label>
            <input type="number" id="fontSize" min="8" max="36" value="${config.fontSize}">
            <label>Font Family</label>
            <select id="fontFamily">
                <option value="Arial, sans-serif" ${config.fontFamily.includes('Arial') ? 'selected' : ''}>Arial</option>
                <option value="Georgia, serif" ${config.fontFamily.includes('Georgia') ? 'selected' : ''}>Georgia</option>
                <option value="'Courier New', monospace" ${config.fontFamily.includes('Courier') ? 'selected' : ''}>Courier New</option>
            </select>
            <label>Text Color</label>
            <input type="color" id="textColor" value="${config.textColor}">
            <h3>Detection</h3>
            <label>Min Width (px)</label>
            <input type="number" id="minImageWidth" min="30" max="1000" value="${config.minImageWidth}">
            <label>Min Height (px)</label>
            <input type="number" id="minImageHeight" min="30" max="1000" value="${config.minImageHeight}">
            <div class="btn-group">
                <button class="btn-cancel">Cancel</button>
                <button class="btn-save">Save</button>
            </div>
        `;

        panel.querySelector('.btn-save').onclick = () => {
            config = {
                visionApiKey: $('#visionApiKey').value,
                translateApiKey: $('#translateApiKey').value,
                sourceLang: $('#sourceLang').value,
                targetLang: $('#targetLang').value,
                fontSize: parseInt($('#fontSize').value),
                fontFamily: $('#fontFamily').value,
                textColor: $('#textColor').value,
                minImageWidth: parseInt($('#minImageWidth').value),
                minImageHeight: parseInt($('#minImageHeight').value)
            };

            GM_setValue('imageTranslatorConfig', config);
            panel.remove();
            showToast('Settings saved');

            // Refresh
            removeAllOverlays();
            removeAllButtons();
            setTimeout(addTranslateButtons, 500);
        };

        panel.querySelector('.btn-cancel').onclick = () => panel.remove();
        document.body.appendChild(panel);
    }

    function translateAllImages() {
        const images = Array.from($$('img')).filter(isEligibleImage);
        if (!images.length) {
            showToast('No suitable images found');
            return;
        }

        showToast(`Translating ${images.length} images...`);
        images.forEach((img, i) => setTimeout(() => translateImage(img), i * 500));
    }

    // DOM observation
    function setupObserver() {
        const observer = new MutationObserver(mutations => {
            const hasNewImages = mutations.some(m =>
                m.type === 'childList' &&
                Array.from(m.addedNodes).some(n =>
                    n.nodeName === 'IMG' || (n.nodeType === 1 && n.querySelector('img'))
                )
            );

            if (hasNewImages) {
                setTimeout(addTranslateButtons, 1000);
            }
        });

        observer.observe(document.body, { childList: true, subtree: true });
        return observer;
    }

    // Initialize
    function init() {
        addStyles();

        // Register menu commands
        GM_registerMenuCommand('Settings', showSettingsPanel);
        GM_registerMenuCommand('Translate All Images', translateAllImages);
        GM_registerMenuCommand('Remove All Translations', removeAllOverlays);

        // Add buttons - always enabled now
        setTimeout(addTranslateButtons, 1000);

        // Event listeners
        window.addEventListener('resize', updateAllButtonPositions);
        window.addEventListener('scroll', updateAllButtonPositions);

        // Setup observer
        setupObserver();
    }

    // Start
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();