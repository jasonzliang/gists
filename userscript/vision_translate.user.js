// ==UserScript==
// @name         Google Image Translator
// @namespace    https://github.com/yourusername
// @version      1.3.1
// @description  Translate text in images using Google Cloud Vision and Translation APIs
// @icon         https://www.svgrepo.com/show/375395/cloud-vision-api.svg
// @author       Anon
// @noframes
// @match        https://e-hentai.org/*
// @match        https://exhentai.org/*
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

    // Configuration with defaults
    const DEFAULT_CONFIG = {
        visionApiKey: '',
        translateApiKey: '',
        sourceLang: 'auto',
        targetLang: 'en',
        fontSize: 16,
        fontFamily: 'Arial, sans-serif',
        textColor: '#000000',
        minImageWidth: 100,
        minImageHeight: 50,
        autoDetectImages: true
    };

    // Load config from storage
    let config = Object.assign({}, DEFAULT_CONFIG, GM_getValue('imageTranslatorConfig', {}));

    // CSS styles - combined into one string for better performance
    const styles = `
        .img-translator-btn{position:absolute;z-index:9999;background:rgba(66,133,244,.9);color:#fff;border:none;border-radius:50%;width:20px;height:20px;font-size:10px;line-height:1;cursor:pointer;opacity:.9;transition:opacity .2s;display:flex;align-items:center;justify-content:center;padding:0;box-shadow:0 1px 3px rgba(0,0,0,0.3)}
        .img-translator-btn:hover{opacity:1;background:rgba(66,133,244,1)}
        .img-translator-loading{position:absolute;z-index:10000;background:rgba(0,0,0,.7);color:#fff;padding:10px 15px;border-radius:5px;display:flex;align-items:center;justify-content:center;font-size:14px}
        .img-translator-loading::after{content:'';display:inline-block;width:16px;height:16px;margin-left:10px;border:2px solid #fff;border-radius:50%;border-top-color:transparent;animation:img-translator-spin 1s linear infinite}
        @keyframes img-translator-spin{to{transform:rotate(360deg)}}
        .img-translator-overlay{position:absolute;top:0;left:0;pointer-events:none;z-index:9998}
        .img-translator-settings{position:fixed;top:50%;left:50%;transform:translate(-50%,-50%);background:#fff;padding:20px;border-radius:8px;box-shadow:0 0 20px rgba(0,0,0,.5);z-index:10001;max-width:500px;max-height:80vh;overflow-y:auto;font-family:Arial,sans-serif;color:#000}
        .img-translator-settings h2{margin-top:0;border-bottom:1px solid #eee;padding-bottom:10px;color:#000}
        .img-translator-settings h3{margin-top:15px;margin-bottom:5px;color:#000}
        .img-translator-settings label{display:block;margin:12px 0 4px;font-weight:700;color:#000}
        .img-translator-settings input,.img-translator-settings select{width:100%;padding:8px;border:1px solid #ddd;border-radius:4px;box-sizing:border-box;color:#000;background-color:#fff}
        .img-translator-settings input[type=checkbox]{width:auto}
        .img-translator-settings .btn-group{display:flex;justify-content:flex-end;margin-top:20px;gap:10px}
        .img-translator-settings button{padding:8px 16px;border:none;border-radius:4px;cursor:pointer}
        .img-translator-settings .btn-save{background:#4285f4;color:#fff}
        .img-translator-settings .btn-cancel{background:#f1f1f1;color:#333}
        .img-translator-toast{position:fixed;top:20px;left:20px;background:rgba(0,0,0,0.8);color:#fff;padding:5px 12px;border-radius:5px;z-index:10002;font-size:11px;animation:img-translator-toast 3s forwards;box-shadow:0 2px 8px rgba(0,0,0,0.2)}
        @keyframes img-translator-toast{0%{opacity:0;transform:translateY(-10px)}10%{opacity:1;transform:translateY(0)}90%{opacity:1;transform:translateY(0)}100%{opacity:0;transform:translateY(-10px)}}
        .img-translator-overlay.side-by-side{pointer-events:auto}
    `;

    // DOM helper functions
    const $ = selector => document.querySelector(selector);
    const $$ = selector => document.querySelectorAll(selector);

    // Show toast message
    function showToast(message, duration = 3000) {
        const toast = document.createElement('div');
        toast.className = 'img-translator-toast';
        toast.textContent = message;
        document.body.appendChild(toast);
        setTimeout(() => toast.parentNode?.removeChild(toast), duration);
    }

    // Add styles to page
    function addStyles() {
        const styleEl = document.createElement('style');
        styleEl.textContent = styles;
        document.head.appendChild(styleEl);
    }

    // Register menu commands
    function registerMenuCommands() {
        GM_registerMenuCommand('Configure Image Translator', showSettingsPanel);
        GM_registerMenuCommand('Translate All Images on Page', translateAllImages);
        GM_registerMenuCommand('Remove All Translations', removeAllTranslations);
    }

    // Settings panel HTML template
    function getSettingsPanelHTML() {
        return `
            <h2>Image Translator Settings</h2>
            <h3>API Keys</h3>
            <label for="visionApiKey">Google Cloud Vision API Key</label>
            <input type="password" id="visionApiKey" value="${config.visionApiKey || ''}">
            <label for="translateApiKey">Google Cloud Translation API Key</label>
            <input type="password" id="translateApiKey" value="${config.translateApiKey || ''}">
            <h3>Translation Settings</h3>
            <label for="sourceLang">Source Language</label>
            <select id="sourceLang">${getLanguageOptions(config.sourceLang, true)}</select>
            <label for="targetLang">Target Language</label>
            <select id="targetLang">${getLanguageOptions(config.targetLang)}</select>
            <h3>Appearance</h3>
            <label for="fontSize">Font Size (px)</label>
            <input type="number" id="fontSize" min="8" max="36" value="${config.fontSize}">
            <label for="fontFamily">Font Family</label>
            <select id="fontFamily">
                <option value="Arial, sans-serif" ${config.fontFamily === 'Arial, sans-serif' ? 'selected' : ''}>Arial</option>
                <option value="'Times New Roman', serif" ${config.fontFamily === "'Times New Roman', serif" ? 'selected' : ''}>Times New Roman</option>
                <option value="'Courier New', monospace" ${config.fontFamily === "'Courier New', monospace" ? 'selected' : ''}>Courier New</option>
                <option value="Georgia, serif" ${config.fontFamily === "Georgia, serif" ? 'selected' : ''}>Georgia</option>
                <option value="Verdana, sans-serif" ${config.fontFamily === "Verdana, sans-serif" ? 'selected' : ''}>Verdana</option>
            </select>
            <label for="textColor">Text Color</label>
            <input type="color" id="textColor" value="${config.textColor}">
            <h3>Detection Settings</h3>
            <label for="minImageWidth">Minimum Image Width (px)</label>
            <input type="number" id="minImageWidth" min="30" max="1000" value="${config.minImageWidth}">
            <label for="minImageHeight">Minimum Image Height (px)</label>
            <input type="number" id="minImageHeight" min="30" max="1000" value="${config.minImageHeight}">
            <label for="autoDetectImages">
                <input type="checkbox" id="autoDetectImages" ${config.autoDetectImages ? 'checked' : ''}>
                Automatically detect images on page load
            </label>
            <div class="btn-group">
                <button class="btn-cancel">Cancel</button>
                <button class="btn-save">Save Settings</button>
            </div>
        `;
    }

    // Generate language options
    function getLanguageOptions(selected, includeAuto = false) {
        const languages = [
            ['en', 'English'],
            ['es', 'Spanish'],
            ['fr', 'French'],
            ['de', 'German'],
            ['it', 'Italian'],
            ['ja', 'Japanese'],
            ['ko', 'Korean'],
            ['pt', 'Portuguese'],
            ['ru', 'Russian'],
            ['zh-CN', 'Chinese (Simplified)'],
            ['zh-TW', 'Chinese (Traditional)'],
            ['ar', 'Arabic'],
            ['hi', 'Hindi']
        ];

        let options = includeAuto ? `<option value="auto" ${selected === 'auto' ? 'selected' : ''}>Auto-detect</option>` : '';

        languages.forEach(([code, name]) => {
            options += `<option value="${code}" ${selected === code ? 'selected' : ''}>${name}</option>`;
        });

        return options;
    }

    // Show settings panel
    function showSettingsPanel() {
        const panel = document.createElement('div');
        panel.className = 'img-translator-settings';
        panel.innerHTML = getSettingsPanelHTML();

        // Color preview updates
        panel.querySelectorAll('input[type="color"]').forEach(input => {
            const id = input.id;
            // Add a preview element right after the color input
            const previewEl = document.createElement('div');
            previewEl.style.width = '40%';
            previewEl.style.height = '20px';
            previewEl.style.marginTop = '5px';
            previewEl.style.marginLeft = 'auto';
            previewEl.style.marginRight = 'auto';
            previewEl.style.border = '1px solid #ddd';
            previewEl.style.borderRadius = '5px';
            previewEl.innerHTML = 'Text Color Preview';
            previewEl.style.padding = '5px';
            previewEl.style.fontFamily = config.fontFamily;
            previewEl.style.color = input.value;
            previewEl.style.textAlign = 'center';

            input.parentNode.insertBefore(previewEl, input.nextSibling);

            // Update the preview on input change
            input.addEventListener('input', () => {
                if (id === 'textColor') {
                    previewEl.style.color = input.value;
                }
            });
        });

        // Button event listeners
        panel.querySelector('.btn-save').addEventListener('click', () => saveSettings(panel));
        panel.querySelector('.btn-cancel').addEventListener('click', () => document.body.removeChild(panel));

        document.body.appendChild(panel);
    }

    // Save settings
    function saveSettings(panel) {
        config = {
            visionApiKey: panel.querySelector('#visionApiKey').value,
            translateApiKey: panel.querySelector('#translateApiKey').value,
            sourceLang: panel.querySelector('#sourceLang').value,
            targetLang: panel.querySelector('#targetLang').value,
            fontSize: parseInt(panel.querySelector('#fontSize').value),
            fontFamily: panel.querySelector('#fontFamily').value,
            textColor: panel.querySelector('#textColor').value,
            minImageWidth: parseInt(panel.querySelector('#minImageWidth').value),
            minImageHeight: parseInt(panel.querySelector('#minImageHeight').value),
            autoDetectImages: panel.querySelector('#autoDetectImages').checked
        };

        GM_setValue('imageTranslatorConfig', config);
        document.body.removeChild(panel);
        showToast('Settings saved successfully');

        // Re-initialize
        removeAllTranslations();
        removeAllTranslationButtons();
        addTranslateButtons();
    }

    // Add translate buttons to eligible images
    function addTranslateButtons() {
        // Use vanilla JS instead of $$
        const images = document.querySelectorAll('img');

        // Guard clause in case there are no images
        if (!images || images.length === 0) return;

        images.forEach(img => {
            // First check if the image is fully loaded and has dimensions
            if (!img.complete) {
                // For images that aren't loaded yet, wait for them to load
                img.addEventListener('load', () => {
                    // Once loaded, check dimensions and add button if needed
                    if (img.width >= config.minImageWidth &&
                        img.height >= config.minImageHeight &&
                        !img.hasAttribute('data-has-translator')) {
                        addTranslateButtonToImage(img);
                    }
                }, {
                    once: true
                }); // Use once:true to avoid memory leaks
                return;
            }

            // Skip small images or those that already have a button
            if (img.width < config.minImageWidth ||
                img.height < config.minImageHeight ||
                img.hasAttribute('data-has-translator')) return;

            addTranslateButtonToImage(img);
        });
    }

    // Helper function to add translate button to a single image
    function addTranslateButtonToImage(img) {
        // Safety check
        if (!img || img.hasAttribute('data-has-translator')) return;

        img.setAttribute('data-has-translator', 'true');

        // Create and position the translate button
        const translateBtn = document.createElement('button');
        translateBtn.className = 'img-translator-btn';
        translateBtn.textContent = 'T';
        translateBtn.style.display = 'none'; // Initially hidden, but will show on hover

        // Make image a positioning context if needed
        const imgStyle = window.getComputedStyle(img);
        if (imgStyle.position === 'static') {
            img.style.position = 'relative';
        }

        document.body.appendChild(translateBtn);

        // Position button function - with error handling
        const updateButtonPosition = () => {
            try {
                // Check if image is still in the DOM
                if (!img.isConnected) {
                    // Image is gone, remove the button
                    if (translateBtn.parentNode) {
                        translateBtn.parentNode.removeChild(translateBtn);
                    }
                    return;
                }

                const imgRect = img.getBoundingClientRect();
                const margin = 5;
                const btnSize = 20;

                // Position in upper left corner
                let top = window.scrollY + imgRect.top + margin;
                let left = window.scrollX + imgRect.left + margin;

                translateBtn.style.top = `${top}px`;
                translateBtn.style.left = `${left}px`;
            } catch (err) {
                console.error('Error updating button position:', err);
                // If there's an error, try to clean up
                if (translateBtn.parentNode) {
                    translateBtn.parentNode.removeChild(translateBtn);
                }
            }
        };

        // Initial positioning with timeout to ensure image dimensions are set
        setTimeout(updateButtonPosition, 100);

        // Store button reference - wrap in try/catch
        try {
            img.translatorButton = translateBtn;
        } catch (err) {
            console.error('Error storing button reference:', err);
        }

        // Add click event
        translateBtn.addEventListener('click', (e) => {
            e.stopPropagation(); // Prevent triggering other events
            translateImage(img);
        });

        // Show button only on hover
        img.addEventListener('mouseenter', () => {
            translateBtn.style.display = 'flex';
            updateButtonPosition(); // Update position when showing
        });

        img.addEventListener('mouseleave', (e) => {
            // Check if mouse is over the button
            const btnRect = translateBtn.getBoundingClientRect();
            if (
                e.clientX < btnRect.left ||
                e.clientX > btnRect.right ||
                e.clientY < btnRect.top ||
                e.clientY > btnRect.bottom
            ) {
                translateBtn.style.display = 'none';
            }
        });

        translateBtn.addEventListener('mouseleave', (event) => {
            if (!img.isConnected) {
                if (translateBtn.parentNode) {
                    translateBtn.parentNode.removeChild(translateBtn);
                }
                return;
            }

            const imgRect = img.getBoundingClientRect();
            if (
                event.clientX < imgRect.left ||
                event.clientX > imgRect.right ||
                event.clientY < imgRect.top ||
                event.clientY > imgRect.bottom
            ) {
                translateBtn.style.display = 'none';
            }
        });

        // Add a MutationObserver to detect if the image is removed from DOM
        const observer = new MutationObserver((mutations) => {
            mutations.forEach(mutation => {
                if (mutation.type === 'childList' &&
                    Array.from(mutation.removedNodes).includes(img)) {
                    if (translateBtn.parentNode) {
                        translateBtn.parentNode.removeChild(translateBtn);
                    }
                    observer.disconnect();
                }
            });
        });

        if (img.parentNode) {
            observer.observe(img.parentNode, {
                childList: true
            });
        }
    }

    // Remove all translation buttons
    function removeAllTranslationButtons() {
        document.querySelectorAll('.img-translator-btn').forEach(btn => {
            if (btn && btn.parentNode) {
                btn.parentNode.removeChild(btn);
            }
        });

        document.querySelectorAll('img[data-has-translator]').forEach(img => {
            img.removeAttribute('data-has-translator');
        });
    }

    // Remove all translations
    function removeAllTranslations() {
        document.querySelectorAll('.img-translator-overlay').forEach(overlay => {
            if (overlay && overlay.parentNode) {
                overlay.parentNode.removeChild(overlay);
            }
        });
    }

    // Convert image to base64
    function imageToBase64(img) {
        return new Promise((resolve, reject) => {
            const isCrossOrigin = (url) => {
                if (url.startsWith('data:')) return false;
                try {
                    const parsedUrl = new URL(url);
                    return parsedUrl.origin !== window.location.origin;
                } catch (e) {
                    return true;
                }
            };

            // Helper function to process same-origin images
            const processWithCanvas = () => {
                try {
                    const canvas = document.createElement('canvas');
                    canvas.width = img.naturalWidth;
                    canvas.height = img.naturalHeight;
                    const ctx = canvas.getContext('2d');
                    ctx.drawImage(img, 0, 0);

                    try {
                        resolve(canvas.toDataURL('image/jpeg').split(',')[1]);
                    } catch (e) {
                        console.error('Canvas tainted, trying proxy method', e);
                        fetchExternalImage();
                    }
                } catch (e) {
                    reject(e);
                }
            };

            // Helper function to fetch cross-origin images
            const fetchExternalImage = () => {
                showToast('Fetching external image...');
                GM_xmlhttpRequest({
                    method: 'GET',
                    url: img.src,
                    responseType: 'arraybuffer',
                    onload: function(response) {
                        try {
                            const uint8Array = new Uint8Array(response.response);
                            let binary = '';
                            const len = uint8Array.byteLength;
                            for (let i = 0; i < len; i++) {
                                binary += String.fromCharCode(uint8Array[i]);
                            }
                            resolve(window.btoa(binary));
                        } catch (e) {
                            reject(e);
                        }
                    },
                    onerror: function(error) {
                        reject(error);
                    }
                });
            };

            // Main logic
            if (isCrossOrigin(img.src)) {
                fetchExternalImage();
            } else {
                if (img.complete) {
                    processWithCanvas();
                } else {
                    img.onload = processWithCanvas;
                    img.onerror = () => reject(new Error('Image failed to load'));
                }
            }
        });
    }

    // Detect text in image with Vision API
    function detectTextWithBoundingBoxes(imageBase64) {
        return new Promise((resolve, reject) => {
            if (!config.visionApiKey) {
                reject(new Error('Google Cloud Vision API key is not set. Please configure it in the settings.'));
                return;
            }

            const visionApiUrl = `https://vision.googleapis.com/v1/images:annotate?key=${config.visionApiKey}`;
            const requestBody = {
                requests: [{
                    image: {
                        content: imageBase64
                    },
                    features: [{
                        type: 'TEXT_DETECTION',
                        maxResults: 50
                    }]
                }]
            };

            GM_xmlhttpRequest({
                method: 'POST',
                url: visionApiUrl,
                data: JSON.stringify(requestBody),
                headers: {
                    'Content-Type': 'application/json'
                },
                onload: function(response) {
                    try {
                        const result = JSON.parse(response.responseText);

                        if (result.error) {
                            reject(new Error(`Vision API Error: ${result.error.message}`));
                            return;
                        }

                        if (!result.responses || !result.responses[0]) {
                            resolve({
                                fullText: '',
                                textBlocks: []
                            });
                            return;
                        }

                        const annotations = result.responses[0];

                        if (!annotations.textAnnotations || annotations.textAnnotations.length === 0) {
                            resolve({
                                fullText: '',
                                textBlocks: []
                            });
                            return;
                        }

                        // First element contains full text
                        const fullText = annotations.textAnnotations[0].description;

                        // Rest are individual blocks with bounding polygons
                        const textBlocks = annotations.textAnnotations.slice(1).map(annotation => ({
                            text: annotation.description,
                            boundingBox: annotation.boundingPoly
                        }));

                        resolve({
                            fullText,
                            textBlocks
                        });
                    } catch (e) {
                        reject(e);
                    }
                },
                onerror: function(error) {
                    reject(error);
                }
            });
        });
    }

    // Translate text with Translation API
    function translateText(text, sourceLang, targetLang) {
        return new Promise((resolve, reject) => {
            if (!text || text.trim() === '') {
                resolve('');
                return;
            }

            if (!config.translateApiKey) {
                reject(new Error('Google Cloud Translation API key is not set. Please configure it in the settings.'));
                return;
            }

            const translateApiUrl = `https://translation.googleapis.com/language/translate/v2?key=${config.translateApiKey}`;
            const requestBody = {
                q: text,
                target: targetLang || config.targetLang || 'en',
                format: 'text'
            };

            // Only set source language if not auto
            if (sourceLang && sourceLang !== 'auto') {
                requestBody.source = sourceLang;
            }

            GM_xmlhttpRequest({
                method: 'POST',
                url: translateApiUrl,
                data: JSON.stringify(requestBody),
                headers: {
                    'Content-Type': 'application/json'
                },
                onload: function(response) {
                    try {
                        const result = JSON.parse(response.responseText);

                        if (result.error) {
                            reject(new Error(`Translation API Error: ${result.error.message}`));
                            return;
                        }

                        if (result.data && result.data.translations && result.data.translations.length > 0) {
                            resolve(result.data.translations[0].translatedText);
                        } else {
                            reject(new Error('Translation failed. No translations returned.'));
                        }
                    } catch (e) {
                        reject(e);
                    }
                },
                onerror: function(error) {
                    reject(error);
                }
            });
        });
    }

    // Create side-by-side display
    function createSideBySideDisplay(img, translatedText, originalText) {
        // Remove existing overlay if it exists
        if (img.translationOverlay && img.translationOverlay.parentNode) {
            img.translationOverlay.parentNode.removeChild(img.translationOverlay);
        }

        // Create container
        const container = document.createElement('div');
        container.className = 'img-translator-overlay side-by-side';
        container.style.width = `${img.width}px`;
        container.style.marginTop = '10px';
        container.style.border = '1px solid #ccc';
        container.style.borderRadius = '5px';
        container.style.overflow = 'hidden';
        container.style.position = 'relative'; // Ensure proper positioning
        container.style.backgroundColor = '#FFFFFF'; // Always white background

        // Create content
        container.innerHTML = `
            <div style="padding:8px;background-color:#f0f0f0;border-bottom:1px solid #ccc;font-weight:bold;font-size:14px;position:relative;">
                Translation Results
                <button style="position:absolute;top:5px;right:5px;background:none;border:none;font-size:20px;cursor:pointer;color:#666;">×</button>
            </div>
            <div style="display:flex;flex-direction:row;padding:10px;background-color:#FFFFFF;">
                <div style="flex:1;padding:0 10px;border-right:1px solid #eee;">
                    <h4 style="margin:0 0 10px 0;font-size:12px;color:#666;">Translated Text</h4>
                    <div style="font-size:${config.fontSize}px;font-family:${config.fontFamily};color:${config.textColor};line-height:1.5;white-space:pre-wrap;">${translatedText}</div>
                </div>
                <div style="flex:1;padding:0 10px;">
                    <h4 style="margin:0 0 10px 0;font-size:12px;color:#666;">Original Text</h4>
                    <div style="font-size:${config.fontSize}px;font-family:${config.fontFamily};color:${config.textColor};line-height:1.5;white-space:pre-wrap;">${originalText}</div>
                </div>
            </div>
        `;

        // Add close button functionality
        const closeButton = container.querySelector('button');
        closeButton.addEventListener('click', () => {
            if (container.parentNode) {
                container.parentNode.removeChild(container);
            }
        });

        // Position and insert after the image
        if (img.parentNode) {
            // Create a wrapper if needed to ensure proper positioning
            const wrapper = document.createElement('div');
            wrapper.style.position = 'relative';
            wrapper.style.display = 'block';
            wrapper.style.width = `${img.width}px`;
            wrapper.style.margin = '0 auto';

            // Insert wrapper after image
            img.parentNode.insertBefore(wrapper, img.nextSibling);
            wrapper.appendChild(container);
        } else {
            console.error('Image has no parent node, cannot append translation display');
        }

        // Store reference to the overlay
        img.translationOverlay = container;

        // Ensure the display is visible by scrolling to it if needed
        setTimeout(() => {
            container.scrollIntoView({
                behavior: 'smooth',
                block: 'nearest'
            });
        }, 100);

        return container;
    }

    // Translate all images on the page
    function translateAllImages() {
        const images = document.querySelectorAll('img');
        let count = 0;

        images.forEach(img => {
            if (img.width >= config.minImageWidth && img.height >= config.minImageHeight) {
                setTimeout(() => translateImage(img), count * 500); // Stagger translations
                count++;
            }
        });

        if (count > 0) {
            showToast(`Translating ${count} images...`);
        } else {
            showToast('No suitable images found for translation.');
        }
    }

    // Main translation function
    async function translateImage(img) {
        if (!config.visionApiKey || !config.translateApiKey) {
            showToast('API keys not configured. Please set them in the settings.');
            showSettingsPanel();
            return;
        }

        try {
            // Show loading indicator
            const loadingDiv = document.createElement('div');
            loadingDiv.className = 'img-translator-loading';
            loadingDiv.textContent = 'Translating';

            const imgRect = img.getBoundingClientRect();
            loadingDiv.style.top = `${window.scrollY + imgRect.top + imgRect.height/2 - 20}px`;
            loadingDiv.style.left = `${window.scrollX + imgRect.left + imgRect.width/2 - 50}px`;

            document.body.appendChild(loadingDiv);

            // Process image
            const base64Image = await imageToBase64(img);
            const {
                fullText,
                textBlocks
            } = await detectTextWithBoundingBoxes(base64Image);

            if (!fullText || fullText.trim() === '' || textBlocks.length === 0) {
                document.body.removeChild(loadingDiv);
                showToast('No text detected in this image.');
                return;
            }

            // Translate detected text
            const translatedText = await translateText(fullText, config.sourceLang, config.targetLang);

            // Remove loading indicator
            document.body.removeChild(loadingDiv);

            // Create side-by-side display
            createSideBySideDisplay(img, translatedText, fullText);
            showToast('Translation complete!');

        } catch (error) {
            console.error('Translation error:', error);

            // Remove loading indicator if it exists
            const loadingDiv = document.querySelector('.img-translator-loading');
            if (loadingDiv && loadingDiv.parentNode) {
                loadingDiv.parentNode.removeChild(loadingDiv);
            }

            showToast(`Error: ${error.message}`);
        }
    }

    // Update position of translation buttons
    function updateButtonPositions() {
        document.querySelectorAll('img[data-has-translator]').forEach(img => {
            if (img && img.translatorButton) {
                const imgRect = img.getBoundingClientRect();
                const margin = 5;

                // Position in upper left corner
                let top = window.scrollY + imgRect.top + margin;
                let left = window.scrollX + imgRect.left + margin;

                img.translatorButton.style.top = `${top}px`;
                img.translatorButton.style.left = `${left}px`;
            }
        });
    }

    // Observe DOM for new images
    function setupMutationObserver() {
        const observer = new MutationObserver((mutations) => {
            let shouldAddButtons = false;

            mutations.some(mutation => {
                if (mutation.type === 'childList') {
                    // Check for new images
                    for (const node of mutation.addedNodes) {
                        if (node.nodeName === 'IMG' ||
                            (node.nodeType === 1 && node.querySelectorAll('img').length > 0)) {
                            shouldAddButtons = true;
                            return true; // break the loop
                        }
                    }
                }
                return false;
            });

            if (shouldAddButtons && config.autoDetectImages) {
                setTimeout(addTranslateButtons, 500);
            }
        });

        observer.observe(document.body, {
            childList: true,
            subtree: true
        });
        return observer;
    }

    // Initialize the script
    function init() {
        addStyles();
        registerMenuCommands();

        // Add translate buttons if auto-detect is enabled
        if (config.autoDetectImages) {
            setTimeout(addTranslateButtons, 1000);
        }

        // Setup event listeners
        window.addEventListener('resize', updateButtonPositions);
        window.addEventListener('scroll', updateButtonPositions);

        // Observe DOM for new images
        const observer = setupMutationObserver();

        // Store observer reference
        window._imgTranslatorObserver = observer;
    }

    // Start when page is loaded
    if (document.readyState === 'loading') {
        window.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();