// ==UserScript==
// @name         TesseractJS Two-Pass OCR
// @namespace    http://tampermonkey.net/
// @version      0.2
// @description  Perform two-pass OCR on images using Tesseract.js - first detect paragraphs, then recognize text
// @author       You
// @match        *://**/*
// @grant        GM_xmlhttpRequest
// @grant        GM_setValue
// @grant        GM_getValue
// @grant        GM_addStyle
// @require      https://cdn.jsdelivr.net/npm/tesseract.js@2.0.0/dist/tesseract.min.js
// @connect      *
// ==/UserScript==

(function() {
    'use strict';

    // Add styles for OCR overlay and control panel
    GM_addStyle(`
        .ocr-overlay {
            position: absolute;
            border: 2px solid #00ff00;
            background-color: rgba(0, 255, 0, 0.1);
            pointer-events: none;
            z-index: 9999;
        }
        .ocr-text {
            position: absolute;
            background-color: rgba(0, 0, 0, 0.7);
            color: white;
            padding: 5px;
            border-radius: 3px;
            font-size: 12px;
            max-width: 90%;
            overflow: hidden;
            z-index: 10000;
        }
        #ocr-control-panel {
            position: fixed;
            top: 10px;
            right: 10px;
            background-color: rgba(0, 0, 0, 0.8);
            color: white;
            padding: 10px;
            border-radius: 5px;
            z-index: 10001;
            font-family: Arial, sans-serif;
            width: 300px; /* Increased width */
            max-height: 80vh; /* Maximum height */
            overflow-y: auto; /* Add scrolling if needed */
        }
        #ocr-control-panel h3 {
            margin-top: 0;
            margin-bottom: 10px;
            font-size: 16px;
        }
        #ocr-control-panel select,
        #ocr-control-panel button {
            margin: 5px 0;
            width: 100%;
            padding: 5px;
            font-size: 14px; /* Increased font size */
        }
        #ocr-control-panel button {
            background-color: #4CAF50;
            color: white;
            border: none;
            cursor: pointer;
            height: 30px; /* Fixed height */
        }
        #ocr-control-panel button:hover {
            background-color: #45a049;
        }
        #ocr-control-panel label {
            display: block;
            margin: 5px 0;
            font-size: 14px; /* Increased font size */
        }
        .ocr-progress {
            width: 100%;
            height: 5px;
            background-color: #ddd;
            margin-top: 10px;
        }
        .ocr-progress-bar {
            height: 5px;
            background-color: #4CAF50;
            width: 0%;
        }
        #ocr-status {
            margin-top: 10px;
            font-size: 12px;
            color: #ddd;
        }
    `);

    // Constants for PSM modes
    const PSM = {
        OSD_ONLY: 0,                // Orientation and script detection only
        AUTO_OSD: 1,                // Automatic page segmentation with orientation and script detection
        AUTO_ONLY: 2,               // Automatic page segmentation, but no OSD, or OCR
        AUTO: 3,                    // Fully automatic page segmentation, but no OSD
        SINGLE_COLUMN: 4,           // Assume a single column of text of variable sizes
        SINGLE_BLOCK_VERT_TEXT: 5,  // Assume a single uniform block of vertically aligned text
        SINGLE_BLOCK: 6,            // Assume a single uniform block of text (Default)
        SINGLE_LINE: 7,             // Treat the image as a single text line
        SINGLE_WORD: 8,             // Treat the image as a single word
        CIRCLE_WORD: 9,             // Treat the image as a single word in a circle
        SINGLE_CHAR: 10,            // Treat the image as a single character
        SPARSE_TEXT: 11,            // Find as much text as possible in no particular order
        SPARSE_TEXT_OSD: 12,        // Sparse text with orientation and script detection
        RAW_LINE: 13                // Treat the image as a single text line, bypassing hacks that are Tesseract-specific
    };

    // Available languages
    const LANGUAGES = {
        'eng': 'English',
        'jpn': 'Japanese',
        'chi_sim': 'Chinese Simplified',
        'chi_tra': 'Chinese Traditional',
        'fra': 'French',
        'deu': 'German',
        'spa': 'Spanish',
        'rus': 'Russian',
        'kor': 'Korean'
    };

    // Default settings
    let settings = GM_getValue('ocrSettings', {
        lang: 'eng',
        firstPassPsm: PSM.SPARSE_TEXT, // For initial paragraph detection
        secondPassPsm: PSM.SINGLE_BLOCK, // For text recognition within paragraphs
        showBoundingBoxes: true,
        convertToJpeg: true,
        minParagraphConfidence: 60 // Minimum confidence for paragraph detection (0-100)
    });

    // Create control panel
    function createControlPanel() {
        const panel = document.createElement('div');
        panel.id = 'ocr-control-panel';
        panel.innerHTML = `
            <h3>TesseractJS Two-Pass OCR</h3>
            <div>
                <label for="ocr-lang">Language:</label>
                <select id="ocr-lang"></select>
            </div>
            <div>
                <label for="ocr-first-pass-psm">First Pass (Paragraph Detection):</label>
                <select id="ocr-first-pass-psm"></select>
            </div>
            <div>
                <label for="ocr-second-pass-psm">Second Pass (Text Recognition):</label>
                <select id="ocr-second-pass-psm"></select>
            </div>
            <div>
                <label for="ocr-min-confidence">Minimum Paragraph Confidence (%):</label>
                <input type="range" id="ocr-min-confidence" min="0" max="100" value="${settings.minParagraphConfidence}" step="5">
                <span id="ocr-confidence-value">${settings.minParagraphConfidence}%</span>
            </div>
            <div>
                <label>
                    <input type="checkbox" id="ocr-show-boxes" ${settings.showBoundingBoxes ? 'checked' : ''}>
                    Show Bounding Boxes
                </label>
            </div>
            <div>
                <label>
                    <input type="checkbox" id="ocr-convert-jpeg" ${settings.convertToJpeg ? 'checked' : ''}>
                    Convert Images to JPEG (fixes format errors)
                </label>
            </div>
            <button id="ocr-process-all">Process All Images</button>
            <button id="ocr-process-visible">Process Visible Images</button>
            <button id="ocr-clear-all">Clear OCR Results</button>
            <div class="ocr-progress">
                <div class="ocr-progress-bar" id="ocr-progress"></div>
            </div>
            <div id="ocr-status">Status: Ready</div>
        `;
        document.body.appendChild(panel);

        // Populate language dropdown
        const langSelect = document.getElementById('ocr-lang');
        for (const [code, name] of Object.entries(LANGUAGES)) {
            const option = document.createElement('option');
            option.value = code;
            option.textContent = name;
            if (code === settings.lang) {
                option.selected = true;
            }
            langSelect.appendChild(option);
        }

        // Populate PSM dropdowns
        const psmOptions = {
            [PSM.OSD_ONLY]: 'OSD Only',
            [PSM.AUTO_OSD]: 'Auto with OSD',
            [PSM.AUTO_ONLY]: 'Auto Only',
            [PSM.AUTO]: 'Fully Automatic',
            [PSM.SINGLE_COLUMN]: 'Single Column',
            [PSM.SINGLE_BLOCK_VERT_TEXT]: 'Single Block Vertical Text',
            [PSM.SINGLE_BLOCK]: 'Single Block',
            [PSM.SINGLE_LINE]: 'Single Line',
            [PSM.SINGLE_WORD]: 'Single Word',
            [PSM.CIRCLE_WORD]: 'Circle Word',
            [PSM.SINGLE_CHAR]: 'Single Character',
            [PSM.SPARSE_TEXT]: 'Sparse Text',
            [PSM.SPARSE_TEXT_OSD]: 'Sparse Text with OSD',
            [PSM.RAW_LINE]: 'Raw Line'
        };

        // First pass PSM dropdown
        const firstPassSelect = document.getElementById('ocr-first-pass-psm');
        for (const [value, name] of Object.entries(psmOptions)) {
            const option = document.createElement('option');
            option.value = value;
            option.textContent = name;
            if (parseInt(value) === settings.firstPassPsm) {
                option.selected = true;
            }
            firstPassSelect.appendChild(option);
        }

        // Second pass PSM dropdown
        const secondPassSelect = document.getElementById('ocr-second-pass-psm');
        for (const [value, name] of Object.entries(psmOptions)) {
            const option = document.createElement('option');
            option.value = value;
            option.textContent = name;
            if (parseInt(value) === settings.secondPassPsm) {
                option.selected = true;
            }
            secondPassSelect.appendChild(option);
        }

        // Update confidence value display
        const confidenceSlider = document.getElementById('ocr-min-confidence');
        const confidenceValue = document.getElementById('ocr-confidence-value');
        confidenceSlider.addEventListener('input', function() {
            confidenceValue.textContent = `${this.value}%`;
        });

        // Add event listeners
        document.getElementById('ocr-lang').addEventListener('change', function() {
            settings.lang = this.value;
            GM_setValue('ocrSettings', settings);
            updateStatus(`Language set to: ${LANGUAGES[this.value]}`);
        });

        document.getElementById('ocr-first-pass-psm').addEventListener('change', function() {
            settings.firstPassPsm = parseInt(this.value);
            GM_setValue('ocrSettings', settings);
            updateStatus(`First pass PSM set to: ${psmOptions[this.value]}`);
        });

        document.getElementById('ocr-second-pass-psm').addEventListener('change', function() {
            settings.secondPassPsm = parseInt(this.value);
            GM_setValue('ocrSettings', settings);
            updateStatus(`Second pass PSM set to: ${psmOptions[this.value]}`);
        });

        document.getElementById('ocr-min-confidence').addEventListener('change', function() {
            settings.minParagraphConfidence = parseInt(this.value);
            GM_setValue('ocrSettings', settings);
            updateStatus(`Minimum confidence set to: ${settings.minParagraphConfidence}%`);
        });

        document.getElementById('ocr-show-boxes').addEventListener('change', function() {
            settings.showBoundingBoxes = this.checked;
            GM_setValue('ocrSettings', settings);
            // Toggle visibility of existing bounding boxes
            document.querySelectorAll('.ocr-overlay').forEach(el => {
                el.style.display = settings.showBoundingBoxes ? 'block' : 'none';
            });
            updateStatus(`Bounding boxes: ${this.checked ? 'Visible' : 'Hidden'}`);
        });

        document.getElementById('ocr-convert-jpeg').addEventListener('change', function() {
            settings.convertToJpeg = this.checked;
            GM_setValue('ocrSettings', settings);
            updateStatus(`Convert to JPEG: ${this.checked ? 'Enabled' : 'Disabled'}`);
        });

        document.getElementById('ocr-process-all').addEventListener('click', () => processAllImages(false));
        document.getElementById('ocr-process-visible').addEventListener('click', () => processAllImages(true));
        document.getElementById('ocr-clear-all').addEventListener('click', clearOCRResults);
    }

    // Update status message
    function updateStatus(message) {
        const statusElement = document.getElementById('ocr-status');
        if (statusElement) {
            statusElement.textContent = `Status: ${message}`;
        }
    }

    // Convert image to base64 JPEG
    function imageToBase64Jpeg(image) {
        return new Promise((resolve, reject) => {
            try {
                // Create a canvas
                const canvas = document.createElement('canvas');
                canvas.width = image.naturalWidth;
                canvas.height = image.naturalHeight;

                // Draw the image on the canvas
                const ctx = canvas.getContext('2d');
                ctx.drawImage(image, 0, 0);

                // Convert to JPEG base64
                const base64 = canvas.toDataURL('image/jpeg', 0.9);
                resolve(base64);
            } catch (e) {
                reject(e);
            }
        });
    }

    // Convert ArrayBuffer to base64
    function arrayBufferToBase64(buffer) {
        let binary = '';
        const bytes = new Uint8Array(buffer);
        const len = bytes.byteLength;
        for (let i = 0; i < len; i++) {
            binary += String.fromCharCode(bytes[i]);
        }
        return window.btoa(binary);
    }

    // Get image data from URL using GM_xmlhttpRequest
    function getImageData(imageUrl) {
        return new Promise((resolve, reject) => {
            GM_xmlhttpRequest({
                method: 'GET',
                url: imageUrl,
                responseType: 'arraybuffer',
                onload: function(response) {
                    resolve(response.response);
                },
                onerror: function(error) {
                    reject(error);
                }
            });
        });
    }

    // Create a Tesseract worker
    async function createTesseractWorker(lang, progressCallback) {
        const worker = Tesseract.createWorker({
            logger: progressInfo => {
                if (progressCallback) progressCallback(progressInfo);
            }
        });

        await worker.load();
        await worker.loadLanguage(lang);
        await worker.initialize(lang);

        return worker;
    }

    // Prepare image for OCR
    async function prepareImage(img) {
        // Get absolute URL of the image
        const imageUrl = new URL(img.src, window.location.href).href;

        if (settings.convertToJpeg) {
            // Create a temporary image element to load the image
            const tempImg = new Image();
            tempImg.crossOrigin = 'Anonymous';

            // Load image and convert to JPEG
            const loadImage = new Promise((resolve, reject) => {
                tempImg.onload = () => resolve();
                tempImg.onerror = (err) => reject(err);

                // For cross-origin images, first download with GM_xmlhttpRequest
                if (new URL(imageUrl).origin !== window.location.origin) {
                    getImageData(imageUrl).then(buffer => {
                        const base64 = arrayBufferToBase64(buffer);
                        // Detect image type based on first bytes
                        let mimeType = 'image/jpeg'; // default
                        const arr = new Uint8Array(buffer.slice(0, 4));
                        if (arr[0] === 0x89 && arr[1] === 0x50 && arr[2] === 0x4E && arr[3] === 0x47) {
                            mimeType = 'image/png';
                        } else if (arr[0] === 0xFF && arr[1] === 0xD8) {
                            mimeType = 'image/jpeg';
                        } else if (arr[0] === 0x47 && arr[1] === 0x49 && arr[2] === 0x46) {
                            mimeType = 'image/gif';
                        } else if (arr[0] === 0x52 && arr[1] === 0x49 && arr[2] === 0x46 && arr[3] === 0x46) {
                            mimeType = 'image/webp';
                        }
                        tempImg.src = `data:${mimeType};base64,${base64}`;
                    }).catch(reject);
                } else {
                    tempImg.src = imageUrl;
                }
            });

            await loadImage;

            // Convert to JPEG
            const jpegBase64 = await imageToBase64Jpeg(tempImg);
            return { imageData: jpegBase64, originalImage: img };
        } else {
            // Use original image
            const buffer = await getImageData(imageUrl);
            return { imageData: buffer, originalImage: img };
        }
    }

    // First pass: detect paragraphs in the image
    async function detectParagraphs(imageData) {
        updateStatus('First pass: Detecting paragraphs...');

        // Create worker for paragraph detection
        const worker = await createTesseractWorker(settings.lang, progressInfo => {
            if (progressInfo.status === 'recognizing text') {
                const progressBar = document.getElementById('ocr-progress');
                if (progressBar && progressInfo.progress !== undefined) {
                    progressBar.style.width = `${progressInfo.progress * 50}%`; // First pass is 50% of total
                }
                updateStatus(`Detecting paragraphs: ${Math.floor(progressInfo.progress * 100)}%`);
            }
        });

        // Set parameters for paragraph detection
        await worker.setParameters({
            tessedit_pageseg_mode: settings.firstPassPsm,
        });

        // Perform OCR to detect paragraphs
        const { data } = await worker.recognize(imageData);

        // Extract paragraph information
        const paragraphs = [];

        // First check if we have paragraphs in the data
        if (data.paragraphs && data.paragraphs.length > 0) {
            for (const para of data.paragraphs) {
                if (para.confidence >= settings.minParagraphConfidence) {
                    paragraphs.push({
                        bbox: para.bbox,
                        confidence: para.confidence
                    });
                }
            }
        }
        // If no paragraphs, try to use blocks
        else if (data.blocks && data.blocks.length > 0) {
            for (const block of data.blocks) {
                if (block.confidence >= settings.minParagraphConfidence) {
                    paragraphs.push({
                        bbox: block.bbox,
                        confidence: block.confidence
                    });
                }
            }
        }
        // If no blocks either, try to use lines
        else if (data.lines && data.lines.length > 0) {
            // Group nearby lines into paragraphs
            const lines = data.lines.filter(line => line.confidence >= settings.minParagraphConfidence);
            const lineHeight = lines.reduce((sum, line) => sum + (line.bbox.y1 - line.bbox.y0), 0) / lines.length;
            const lineGapThreshold = lineHeight * 1.5; // Lines with gaps larger than this are considered separate paragraphs

            let currentParagraphLines = [lines[0]];
            let currentBottom = lines[0].bbox.y1;

            for (let i = 1; i < lines.length; i++) {
                const line = lines[i];

                // If this line is close to the previous one, add it to the current paragraph
                if (line.bbox.y0 - currentBottom < lineGapThreshold) {
                    currentParagraphLines.push(line);
                    currentBottom = Math.max(currentBottom, line.bbox.y1);
                } else {
                    // Create a paragraph from the current group of lines
                    const paraBox = {
                        x0: Math.min(...currentParagraphLines.map(l => l.bbox.x0)),
                        y0: Math.min(...currentParagraphLines.map(l => l.bbox.y0)),
                        x1: Math.max(...currentParagraphLines.map(l => l.bbox.x1)),
                        y1: Math.max(...currentParagraphLines.map(l => l.bbox.y1))
                    };

                    const avgConfidence = currentParagraphLines.reduce((sum, l) => sum + l.confidence, 0) / currentParagraphLines.length;

                    paragraphs.push({
                        bbox: paraBox,
                        confidence: avgConfidence
                    });

                    // Start a new paragraph with this line
                    currentParagraphLines = [line];
                    currentBottom = line.bbox.y1;
                }
            }

            // Add the last paragraph
            if (currentParagraphLines.length > 0) {
                const paraBox = {
                    x0: Math.min(...currentParagraphLines.map(l => l.bbox.x0)),
                    y0: Math.min(...currentParagraphLines.map(l => l.bbox.y0)),
                    x1: Math.max(...currentParagraphLines.map(l => l.bbox.x1)),
                    y1: Math.max(...currentParagraphLines.map(l => l.bbox.y1))
                };

                const avgConfidence = currentParagraphLines.reduce((sum, l) => sum + l.confidence, 0) / currentParagraphLines.length;

                paragraphs.push({
                    bbox: paraBox,
                    confidence: avgConfidence
                });
            }
        }
        // If nothing else, use the entire image as one paragraph
        else if (data.text && data.text.trim()) {
            // Create a paragraph that covers the whole image
            paragraphs.push({
                bbox: {
                    x0: 0,
                    y0: 0,
                    x1: data.width || 1000, // Default if width is not available
                    y1: data.height || 1000 // Default if height is not available
                },
                confidence: data.confidence || 0
            });
        }

        // Terminate worker
        await worker.terminate();

        return paragraphs;
    }

    // Second pass: recognize text within each paragraph
    async function recognizeTextInParagraphs(imageData, paragraphs, img) {
        updateStatus('Second pass: Recognizing text in paragraphs...');

        // Create worker for text recognition
        const worker = await createTesseractWorker(settings.lang, progressInfo => {
            if (progressInfo.status === 'recognizing text') {
                const progressBar = document.getElementById('ocr-progress');
                if (progressBar && progressInfo.progress !== undefined) {
                    // Second pass starts at 50% and goes to 100%
                    progressBar.style.width = `${50 + (progressInfo.progress * 50)}%`;
                }
                updateStatus(`Recognizing text: ${Math.floor(progressInfo.progress * 100)}%`);
            }
        });

        // Get bounding client rect of the image
        const rect = img.getBoundingClientRect();
        const imgWidth = img.naturalWidth;
        const imgHeight = img.naturalHeight;
        const scaleX = rect.width / imgWidth;
        const scaleY = rect.height / imgHeight;

        // Process each paragraph
        const results = [];

        for (let i = 0; i < paragraphs.length; i++) {
            const para = paragraphs[i];
            updateStatus(`Processing paragraph ${i+1} of ${paragraphs.length}`);

            // Set parameters for text recognition
            await worker.setParameters({
                tessedit_pageseg_mode: settings.secondPassPsm,
            });

            // Add padding to ensure we get all text
            const paddingX = (para.bbox.x1 - para.bbox.x0) * 0.05; // 5% padding
            const paddingY = (para.bbox.y1 - para.bbox.y0) * 0.05; // 5% padding

            // Create rectangle for this paragraph (with padding)
            const rectangle = {
                left: Math.max(0, para.bbox.x0 - paddingX),
                top: Math.max(0, para.bbox.y0 - paddingY),
                width: Math.min(imgWidth - para.bbox.x0, (para.bbox.x1 - para.bbox.x0) + (paddingX * 2)),
                height: Math.min(imgHeight - para.bbox.y0, (para.bbox.y1 - para.bbox.y0) + (paddingY * 2))
            };

            // Recognize text in this paragraph
            const { data } = await worker.recognize(imageData, { rectangle });

            // Calculate scaled bounding box
            const bbox = para.bbox;
            const scaledLeft = rect.left + bbox.x0 * scaleX;
            const scaledTop = rect.top + bbox.y0 * scaleY;
            const scaledWidth = (bbox.x1 - bbox.x0) * scaleX;
            const scaledHeight = (bbox.y1 - bbox.y0) * scaleY;

            // Add some padding to the bounding boxes for better visibility
            const displayPaddingX = Math.max(5, scaledWidth * 0.02);
            const displayPaddingY = Math.max(5, scaledHeight * 0.02);

            // Store the results
            results.push({
                bbox: {
                    x0: scaledLeft - displayPaddingX + window.scrollX,
                    y0: scaledTop - displayPaddingY + window.scrollY,
                    x1: scaledLeft + scaledWidth + displayPaddingX + window.scrollX,
                    y1: scaledTop + scaledHeight + displayPaddingY + window.scrollY
                },
                text: data.text.trim(),
                confidence: data.confidence,
                originalBbox: para.bbox
            });
        }

        // Terminate worker
        await worker.terminate();

        return results;
    }

    // Display OCR results on the page
    function displayOCRResults(results, img) {
        // Create overlays for each paragraph
        results.forEach(result => {
            if (!result.text) return; // Skip empty results

            // Create overlay for bounding box
            if (settings.showBoundingBoxes) {
                const overlay = document.createElement('div');
                overlay.className = 'ocr-overlay';
                overlay.style.left = `${result.bbox.x0}px`;
                overlay.style.top = `${result.bbox.y0}px`;
                overlay.style.width = `${result.bbox.x1 - result.bbox.x0}px`;
                overlay.style.height = `${result.bbox.y1 - result.bbox.y0}px`;
                document.body.appendChild(overlay);

                // Link overlay to image for easy cleanup
                if (!img.ocrOverlays) img.ocrOverlays = [];
                img.ocrOverlays.push(overlay);
            }

            // Create text overlay
            const textDiv = document.createElement('div');
            textDiv.className = 'ocr-text';
            textDiv.style.left = `${result.bbox.x0}px`;
            textDiv.style.top = `${result.bbox.y1 + 5}px`; // Position below the bounding box
            textDiv.style.maxWidth = `${result.bbox.x1 - result.bbox.x0}px`;
            textDiv.textContent = result.text;

            // Add confidence as title
            textDiv.title = `Confidence: ${Math.round(result.confidence)}%`;

            document.body.appendChild(textDiv);

            // Link text overlay to image for easy cleanup
            if (!img.ocrTexts) img.ocrTexts = [];
            img.ocrTexts.push(textDiv);
        });
    }

    // Process a single image using two-pass approach
    async function processImage(img) {
        // Skip if already processed
        if (img.dataset.ocrProcessed === 'true') return;

        try {
            // Mark as processed to avoid duplicate processing
            img.dataset.ocrProcessed = 'true';

            updateStatus('Preparing image...');
            // Prepare the image for OCR
            const { imageData, originalImage } = await prepareImage(img);

            // First pass: detect paragraphs
            const paragraphs = await detectParagraphs(imageData);

            if (paragraphs.length === 0) {
                updateStatus('No text detected in image');
                return;
            }

            updateStatus(`Detected ${paragraphs.length} paragraph(s)`);

            // Second pass: recognize text in each paragraph
            const results = await recognizeTextInParagraphs(imageData, paragraphs, originalImage);

            // Display results
            displayOCRResults(results, originalImage);

            // Update progress
            const progressBar = document.getElementById('ocr-progress');
            if (progressBar) progressBar.style.width = '0%';
            updateStatus('OCR completed successfully');

        } catch (error) {
            console.error('Error processing image:', error);
            // Mark as not processed so it can be retried
            img.dataset.ocrProcessed = 'false';
            const progressBar = document.getElementById('ocr-progress');
            if (progressBar) progressBar.style.width = '0%';
            updateStatus(`Error: ${error.message}`);
        }
    }

    // Check if an element is visible in viewport
    function isElementInViewport(el) {
        const rect = el.getBoundingClientRect();
        return (
            rect.top >= 0 &&
            rect.left >= 0 &&
            rect.bottom <= (window.innerHeight || document.documentElement.clientHeight) &&
            rect.right <= (window.innerWidth || document.documentElement.clientWidth)
        );
    }

    // Process all images on the page
    async function processAllImages(onlyVisible = false) {
        // Clear previous results
        clearOCRResults();

        // Get all images
        const allImages = Array.from(document.querySelectorAll('img')).filter(img => {
            // Filter out very small or invisible images
            return img.complete &&
                  img.naturalWidth > 50 &&
                  img.naturalHeight > 50 &&
                  img.offsetParent !== null &&
                  !img.src.startsWith('data:'); // Skip data URLs
        });

        // Filter for visibility if needed
        const images = onlyVisible
            ? allImages.filter(img => isElementInViewport(img))
            : allImages;

        if (images.length === 0) {
            updateStatus(`No suitable ${onlyVisible ? 'visible ' : ''}images found`);
            return;
        }

        updateStatus(`Found ${images.length} images to process`);

        // Process each image sequentially
        for (let i = 0; i < images.length; i++) {
            updateStatus(`Processing image ${i+1} of ${images.length}`);
            await processImage(images[i]);
        }

        // Ensure progress bar is complete
        const progressBar = document.getElementById('ocr-progress');
        if (progressBar) {
            progressBar.style.width = '100%';
            // Reset after a delay
            setTimeout(() => {
                progressBar.style.width = '0%';
            }, 1000);
        }

        updateStatus('All selected images processed');
    }

    // Clear OCR results
    function clearOCRResults() {
        // Remove all overlays and text divs
        document.querySelectorAll('.ocr-overlay, .ocr-text').forEach(el => el.remove());

        // Reset processed flag on all images
        document.querySelectorAll('img').forEach(img => {
            img.dataset.ocrProcessed = 'false';
            img.ocrOverlays = null;
            img.ocrTexts = null;
        });

        // Reset progress bar
        const progressBar = document.getElementById('ocr-progress');
        if (progressBar) progressBar.style.width = '0%';

        updateStatus('OCR results cleared');
    }

    // Handle window resize - update overlay positions
    function handleWindowResize() {
        // Get all processed images
        const processedImages = document.querySelectorAll('img[data-ocr-processed="true"]');

        // If there are any processed images, clear and reprocess them
        if (processedImages.length > 0) {
            clearOCRResults();
            updateStatus('Window resized, OCR results cleared');
        }
    }

    // Handle scroll events (for lazy-loaded images)
    function handleScroll() {
        // Debounce the scroll handler
        if (handleScroll.timeout) {
            clearTimeout(handleScroll.timeout);
        }

        handleScroll.timeout = setTimeout(() => {
            // Check if any processed images went out of view or new images came into view
            const processedImages = document.querySelectorAll('img[data-ocr-processed="true"]');
            if (processedImages.length > 0) {
                const someOutOfView = Array.from(processedImages).some(img => {
                    const overlays = img.ocrOverlays || [];
                    const texts = img.ocrTexts || [];

                    // If the image is out of view, hide its overlays
                    if (!isElementInViewport(img)) {
                        overlays.forEach(o => o.style.display = 'none');
                        texts.forEach(t => t.style.display = 'none');
                        return true;
                    } else {
                        // If the image is in view, show its overlays
                        overlays.forEach(o => o.style.display = settings.showBoundingBoxes ? 'block' : 'none');
                        texts.forEach(t => t.style.display = 'block');
                        return false;
                    }
                });

                if (someOutOfView) {
                    updateStatus('Some OCR results hidden (out of view)');
                }
            }
        }, 200);
    }

    // Initialize script
    function init() {
        // Create control panel
        createControlPanel();

        // Add window resize handler with debounce
        let resizeTimer;
        window.addEventListener('resize', function() {
            clearTimeout(resizeTimer);
            resizeTimer = setTimeout(handleWindowResize, 500);
        });

        // Add scroll handler for lazy-loaded images
        window.addEventListener('scroll', handleScroll);

        updateStatus('OCR tool initialized - version 2.0');
    }

    // Wait for page to fully load before initializing
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();