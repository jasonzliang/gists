// ==UserScript==
// @name         Google Image Translator
// @version      2.1.1
// @description  Translate text in images using Google Cloud Vision and Translation APIs
// @author       Anon
// @namespace    https://github.com/yourusername
// @license      MPLv2
// @icon         https://www.svgrepo.com/show/375395/cloud-vision-api.svg
// @noframes
// @grant        GM_setValue
// @grant        GM_getValue
// @grant        GM_xmlhttpRequest
// @grant        GM_registerMenuCommand
// @connect      vision.googleapis.com
// @connect      translation.googleapis.com
// @connect      *
// @downloadURL  https://github.com/jasonzliang/gists/raw/refs/heads/master/userscript/vision_translate.user.js
// @updateURL    https://github.com/jasonzliang/gists/raw/refs/heads/master/userscript/vision_translate.user.js
// -- GENERAL MANGA/COMIC SITES --
// @match *://dynasty-scans.com/chapters/*
// @match *://prismblush.com/comic/*
// @match *://nozomi.la/post/*
// -- HENTAI/ADULT SITES --
// @match *://hentaifr.net/*
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
// @match *://kemono.su/*
// -- FOOLSLIDE-BASED READERS --
// @match *://reader.yuriproject.net/read/*
// @match *://ecchi.japanzai.com/read/*
// @match *://h.japanzai.com/read/*
// @match *://reader.japanzai.com/read/*
// @match *://yomanga.co/reader/read/*
// @match *://raws.yomanga.co/read/*
// @match *://hentai.cafe/manga/read/*
// @match *://*.yuri-ism.net/slide/read/*
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
        minImageHeight: 50,
        useDbscan: true,
        dbscanEps: 50,
        dbscanMinPoints: 3
    };

    let config = { ...DEFAULT_CONFIG, ...GM_getValue('imageTranslatorConfig', {}) };
    const buttonMap = new WeakMap();
    const overlayMap = new WeakMap();

    // Enhanced TextDetector with image size-adaptive tolerances and optimized direction detection
    class TextDetector {
        constructor(apiKey, options = {}) {
            this.apiKey = apiKey;
            this.options = {
                // DBSCAN base parameters (from settings menu)
                dbscanEps: 50,
                dbscanMinPoints: 3,

                // Direction detection (optimized values)
                textDirection: 'auto',
                minAspectRatio: 1.2,
                verticalThreshold: 0.3,
                strongVerticalThreshold: 0.6,
                minBlockSize: 8,

                // Image size-based tolerance ratios (always enabled)
                imageSizeAdaptive: true,

                // Horizontal text tolerances as ratios of image dimensions (increased for better clustering)
                horizontal: {
                    clusterToleranceXRatio: 0.05,
                    clusterToleranceYRatio: 0.2,
                    blockToleranceXRatio: 0.02,
                    blockToleranceYRatio: 0.02
                },

                // Vertical text tolerances as ratios of image dimensions (increased for better clustering)
                vertical: {
                    clusterToleranceXRatio: 0.05,
                    clusterToleranceYRatio: 0.2,
                    blockToleranceXRatio: 0.02,
                    blockToleranceYRatio: 0.02
                },

                ...options
            };

            // Store image dimensions for adaptive calculations
            this.imageDimensions = null;
        }

        detectText(imageBase64) {
            return new Promise((resolve, reject) => {
                if (!this.apiKey) {
                    reject(new Error('Vision API key not configured'));
                    return;
                }

                GM_xmlhttpRequest({
                    method: 'POST',
                    url: `https://vision.googleapis.com/v1/images:annotate?key=${this.apiKey}`,
                    data: JSON.stringify({
                        requests: [{
                            image: { content: imageBase64 },
                            features: [{ type: 'TEXT_DETECTION', maxResults: 50 }],
                            imageContext: {
                                // Request image properties for size-adaptive tolerances
                                cropHintsParams: { aspectRatios: [1.0] }
                            }
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

                            const originalText = annotations[0].description;
                            const textBlocks = annotations.slice(1).map(a => ({
                                text: a.description,
                                boundingBox: a.boundingPoly,
                                center: this.calculateCenter(a.boundingPoly),
                                dimensions: this.calculateBlockDimensions(a.boundingPoly)
                            }));

                            if (config.useDbscan) {
                                // Estimate image dimensions from text block positions
                                this.imageDimensions = this.estimateImageDimensions(textBlocks);
                                console.log('[TextDetector] Estimated image dimensions:', this.imageDimensions);

                                // Enhanced direction detection
                                const directionAnalysis = this.detectTextDirection(textBlocks);
                                console.log(`[TextDetector] Direction analysis:`, directionAnalysis);

                                // Process with adaptive parameters
                                const processedText = this.processWithAdaptiveDbscan(textBlocks, directionAnalysis);

                                resolve({
                                    fullText: processedText,
                                    textBlocks: textBlocks,
                                    direction: directionAnalysis.direction,
                                    directionConfidence: directionAnalysis.confidence,
                                    imageDimensions: this.imageDimensions,
                                    processingMethod: 'adaptive_dbscan'
                                });
                            } else {
                                resolve({
                                    fullText: originalText,
                                    textBlocks: textBlocks,
                                    processingMethod: 'no_processing'
                                });
                            }
                        } catch (e) {
                            reject(e);
                        }
                    },
                    onerror: reject
                });
            });
        }

        // Estimate image dimensions from text block positions
        estimateImageDimensions(textBlocks) {
            if (!textBlocks.length) return { width: 1000, height: 1000 };

            const allVertices = textBlocks.flatMap(block =>
                block.boundingBox?.vertices || []
            );

            if (!allVertices.length) return { width: 1000, height: 1000 };

            const xs = allVertices.map(v => v.x || 0);
            const ys = allVertices.map(v => v.y || 0);

            // Add padding to estimated dimensions (text usually doesn't reach edges)
            const textWidth = Math.max(...xs) - Math.min(...xs);
            const textHeight = Math.max(...ys) - Math.min(...ys);

            return {
                width: Math.max(500, textWidth * 1.3),   // Add 30% padding
                height: Math.max(500, textHeight * 1.3)
            };
        }

        // Enhanced direction detection with confidence levels
        detectTextDirection(textBlocks) {
            if (this.options.textDirection !== 'auto') {
                return {
                    direction: this.options.textDirection,
                    confidence: 'manual',
                    verticalRatio: null,
                    horizontalRatio: null
                };
            }

            const analysis = this.analyzeTextBlocks(textBlocks);

            if (analysis.totalAnalyzed === 0) {
                return {
                    direction: 'horizontal',
                    confidence: 'default',
                    verticalRatio: 0,
                    horizontalRatio: 0
                };
            }

            const verticalRatio = analysis.verticalBlocks / analysis.totalAnalyzed;
            const horizontalRatio = analysis.horizontalBlocks / analysis.totalAnalyzed;
            const dominance = Math.abs(verticalRatio - horizontalRatio);

            let direction, confidence;

            if (verticalRatio >= this.options.strongVerticalThreshold) {
                direction = 'vertical';
                confidence = 'high';
            } else if (verticalRatio >= this.options.verticalThreshold && verticalRatio > horizontalRatio) {
                direction = 'vertical';
                confidence = dominance > 0.3 ? 'medium' : 'low';
            } else {
                direction = 'horizontal';
                confidence = horizontalRatio > 0.6 ? 'high' :
                            horizontalRatio > 0.4 ? 'medium' : 'low';
            }

            console.log(`[TextDetector] Direction ratios: vertical=${verticalRatio.toFixed(2)}, horizontal=${horizontalRatio.toFixed(2)}, dominance=${dominance.toFixed(2)}`);

            return {
                direction,
                confidence,
                verticalRatio,
                horizontalRatio,
                dominance,
                totalBlocks: analysis.totalAnalyzed
            };
        }

        // Analyze text blocks with optimized thresholds
        analyzeTextBlocks(textBlocks) {
            let verticalBlocks = 0;
            let horizontalBlocks = 0;
            let totalAnalyzed = 0;

            // Calculate adaptive minimum size based on image
            const avgDimension = this.calculateAverageBlockSize(textBlocks);
            const imageArea = this.imageDimensions ?
                this.imageDimensions.width * this.imageDimensions.height : 1000000;
            const adaptiveMinSize = Math.max(
                this.options.minBlockSize,
                Math.sqrt(imageArea) * 0.008  // 0.8% of image diagonal
            );

            textBlocks.forEach(block => {
                const { width, height } = block.dimensions;
                const textLength = block.text.length;

                if (width < adaptiveMinSize || height < adaptiveMinSize) {
                    return;
                }

                const aspectRatio = height / width;

                // Use different thresholds for single vs multi-character blocks
                const isShortText = textLength <= 2;
                const verticalThreshold = isShortText ?
                    this.options.minAspectRatio * 0.95 : // Slightly lower for single chars
                    this.options.minAspectRatio;
                const horizontalThreshold = 1 / verticalThreshold;

                // Weight by text length and block size
                let weight = Math.min(3, Math.max(1, textLength / 2));
                const sizeBonus = Math.min(1.5, (width * height) / (adaptiveMinSize * adaptiveMinSize));
                weight *= sizeBonus;

                if (aspectRatio >= verticalThreshold) {
                    verticalBlocks += weight;
                } else if (aspectRatio <= horizontalThreshold) {
                    horizontalBlocks += weight;
                }

                totalAnalyzed += weight;
            });

            return { verticalBlocks, horizontalBlocks, totalAnalyzed };
        }

        // Calculate image size-adaptive tolerances (always assuming image size is available)
        calculateAdaptiveTolerances(direction, directionAnalysis) {
            const params = this.options[direction];

            // Always calculate tolerances based on image size
            const tolerances = {
                clusterToleranceX: Math.round(this.imageDimensions.width * params.clusterToleranceXRatio),
                clusterToleranceY: Math.round(this.imageDimensions.height * params.clusterToleranceYRatio),
                blockToleranceX: Math.round(this.imageDimensions.width * params.blockToleranceXRatio),
                blockToleranceY: Math.round(this.imageDimensions.height * params.blockToleranceYRatio)
            };

            console.log(`[TextDetector] Image-adaptive ${direction} tolerances:`, {
                imageSize: `${this.imageDimensions.width}x${this.imageDimensions.height}`,
                cluster: `${tolerances.clusterToleranceX}x${tolerances.clusterToleranceY}`,
                block: `${tolerances.blockToleranceX}x${tolerances.blockToleranceY}`
            });

            return {
                ...tolerances,
                direction,
                method: 'image_adaptive'
            };
        }

        // Process with adaptive DBSCAN and tolerances
        processWithAdaptiveDbscan(textBlocks, directionAnalysis) {
            const direction = directionAnalysis.direction;

            // Always use DBSCAN parameters from settings menu
            const dbscanEps = this.options.dbscanEps;
            const dbscanMinPoints = this.options.dbscanMinPoints;

            console.log(`[TextDetector] Processing as ${direction} text (${directionAnalysis.confidence} confidence)`);
            console.log(`[TextDetector] DBSCAN params: eps=${dbscanEps}, minPoints=${dbscanMinPoints}`);

            // Run DBSCAN clustering
            const clusters = this.dbscanClustering(textBlocks, dbscanEps, dbscanMinPoints);

            console.log(`[TextDetector] DBSCAN detected ${clusters.length} clusters`);
            clusters.forEach((cluster, i) => {
                console.log(`  Cluster ${i + 1} (${cluster.length} blocks):`,
                    cluster.map(block => `"${block.text}"`).join(', '));
            });

            // Calculate adaptive tolerances
            const tolerances = this.calculateAdaptiveTolerances(direction, directionAnalysis);

            // Sort clusters and blocks using direction-specific logic
            const sortedClusters = this.sortClusters(clusters, tolerances);

            sortedClusters.forEach(cluster => {
                this.sortBlocksInCluster(cluster, tolerances);
            });

            // Join text appropriately for the detected direction
            const finalText = this.joinTextByDirection(sortedClusters, direction);

            console.log(`[TextDetector] Final ${direction} text:`, finalText);
            return finalText;
        }

        // Direction and tolerance-aware cluster sorting
        sortClusters(clusters, tolerances) {
            return clusters
                .filter(cluster => cluster.length > 0)
                .sort((a, b) => {
                    const centerA = this.getClusterCenter(a);
                    const centerB = this.getClusterCenter(b);

                    if (tolerances.direction === 'vertical') {
                        // Vertical: sort by Y first (right to left for manga), then X
                        if (Math.abs(centerA.y - centerB.y) > tolerances.clusterToleranceY) {
                            return centerA.y - centerB.y; // Top to bottom within same column
                        }
                        if (Math.abs(centerA.x - centerB.x) > tolerances.clusterToleranceX) {
                            return centerB.x - centerA.x; // Right to left (Japanese reading order)
                        }
                        return 0;
                    } else {
                        // Horizontal: sort by Y first (top to bottom), then X
                        if (Math.abs(centerA.y - centerB.y) > tolerances.clusterToleranceY) {
                            return centerA.y - centerB.y; // Top to bottom
                        }
                        if (Math.abs(centerA.x - centerB.x) > tolerances.clusterToleranceX) {
                            return centerA.x - centerB.x; // Left to right within same line
                        }
                        return 0;
                    }
                });
        }

        // Direction and tolerance-aware block sorting within clusters
        sortBlocksInCluster(cluster, tolerances) {
            cluster.sort((a, b) => {
                if (tolerances.direction === 'vertical') {
                    if (Math.abs(a.center.x - b.center.x) > tolerances.blockToleranceX) {
                        return b.center.x - a.center.x; // Right to left if on same level
                    }
                    // Vertical: characters stack top to bottom in columns
                    if (Math.abs(a.center.y - b.center.y) > tolerances.blockToleranceY) {
                        return a.center.y - b.center.y; // Top to bottom
                    }
                    return 0;
                } else {
                    // Horizontal: characters flow left to right in lines
                    if (Math.abs(a.center.y - b.center.y) > tolerances.blockToleranceY) {
                        return a.center.y - b.center.y; // Top to bottom (different lines)
                    }
                    if (Math.abs(a.center.x - b.center.x) > tolerances.blockToleranceX) {
                        return a.center.x - b.center.x; // Left to right within same line
                    }
                    return 0;
                }
            });
        }

        // Join text based on detected direction
        joinTextByDirection(sortedClusters, direction) {
            if (direction === 'vertical') {
                // Vertical: each cluster is a column, characters join without spaces
                return sortedClusters
                    .map(cluster => cluster.map(block => block.text).join(''))
                    .join('\n');
            } else {
                // Horizontal: each cluster is a line, words join with spaces
                return sortedClusters
                    .map(cluster => cluster.map(block => block.text).join(' '))
                    .join('\n');
            }
        }

        calculateAverageBlockSize(textBlocks) {
            const validBlocks = textBlocks.filter(block =>
                block.dimensions.width > 0 && block.dimensions.height > 0
            );

            if (!validBlocks.length) return 10;

            const avgArea = validBlocks.reduce((sum, block) =>
                sum + (block.dimensions.width * block.dimensions.height), 0
            ) / validBlocks.length;

            return Math.sqrt(avgArea);
        }

        calculateBlockDimensions(boundingPoly) {
            const vertices = boundingPoly.vertices;
            if (!vertices?.length) return { width: 0, height: 0 };

            const xs = vertices.map(v => v.x || 0);
            const ys = vertices.map(v => v.y || 0);

            return {
                width: Math.max(...xs) - Math.min(...xs),
                height: Math.max(...ys) - Math.min(...ys)
            };
        }

        calculateCenter(boundingPoly) {
            const vertices = boundingPoly.vertices;
            if (!vertices?.length) return { x: 0, y: 0 };

            const sumX = vertices.reduce((sum, v) => sum + (v.x || 0), 0);
            const sumY = vertices.reduce((sum, v) => sum + (v.y || 0), 0);

            return { x: sumX / vertices.length, y: sumY / vertices.length };
        }

        getClusterCenter(cluster) {
            const sumX = cluster.reduce((sum, block) => sum + block.center.x, 0);
            const sumY = cluster.reduce((sum, block) => sum + block.center.y, 0);
            return { x: sumX / cluster.length, y: sumY / cluster.length };
        }

        calculateDistance(point1, point2) {
            const dx = point1.x - point2.x;
            const dy = point1.y - point2.y;
            return Math.sqrt(dx * dx + dy * dy);
        }

        dbscanClustering(textBlocks, epsilon, minPoints) {
            const clusters = [];
            const visited = new Set();
            const clustered = new Set();

            for (let i = 0; i < textBlocks.length; i++) {
                if (visited.has(i)) continue;

                visited.add(i);
                const neighbors = this.getNeighbors(textBlocks, i, epsilon);

                if (neighbors.length < minPoints) continue;

                const cluster = [];
                this.expandCluster(textBlocks, i, neighbors, cluster, clustered, visited, epsilon, minPoints);

                if (cluster.length > 0) clusters.push(cluster);
            }

            // Add unclustered points as individual clusters
            for (let i = 0; i < textBlocks.length; i++) {
                if (!clustered.has(i)) {
                    clusters.push([textBlocks[i]]);
                }
            }

            return clusters;
        }

        getNeighbors(textBlocks, pointIndex, epsilon) {
            const neighbors = [];
            const currentPoint = textBlocks[pointIndex];

            for (let i = 0; i < textBlocks.length; i++) {
                if (i !== pointIndex && this.calculateDistance(currentPoint.center, textBlocks[i].center) <= epsilon) {
                    neighbors.push(i);
                }
            }

            return neighbors;
        }

        expandCluster(textBlocks, pointIndex, neighbors, cluster, clustered, visited, epsilon, minPoints) {
            cluster.push(textBlocks[pointIndex]);
            clustered.add(pointIndex);

            for (let i = 0; i < neighbors.length; i++) {
                const neighborIndex = neighbors[i];

                if (!visited.has(neighborIndex)) {
                    visited.add(neighborIndex);
                    const neighborNeighbors = this.getNeighbors(textBlocks, neighborIndex, epsilon);

                    if (neighborNeighbors.length >= minPoints) {
                        neighbors.push(...neighborNeighbors.filter(n => !neighbors.includes(n) && n !== neighborIndex));
                    }
                }

                if (!clustered.has(neighborIndex)) {
                    cluster.push(textBlocks[neighborIndex]);
                    clustered.add(neighborIndex);
                }
            }
        }
    }

    // Enhanced Language Detector
    class LanguageDetector {
        constructor() {
            this.cache = new Map();
            this.stats = new Map();
            this.MIN_CONFIDENCE = 0.7;
            this.COMMON_FALLBACKS = ['ja', 'ko', 'zh-CN', 'zh-TW', 'es', 'fr', 'de'];
        }

        isTranslatableText(text) {
            if (!text || text.trim().length < 2) return false;
            const alphaRatio = (text.match(/[a-zA-Z\u0080-\uFFFF]/g) || []).length / text.length;
            if (alphaRatio < 0.3) return false;

            const skipPatterns = [
                /^[\d\s\-\+\(\)\.]+$/,
                /^[A-Z]{2,}[\d\s]*$/,
                /^https?:\/\//,
                /^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$/
            ];

            return !skipPatterns.some(pattern => pattern.test(text.trim()));
        }

        async translateWithSmartDetection(text, targetLang = 'en') {
            if (!this.isTranslatableText(text)) {
                throw new Error('Text does not appear to be translatable');
            }

            const cached = this.getCachedDetection(text);
            if (cached && Date.now() - cached.timestamp < 300000) {
                const result = await this.translateText(text, cached.language, targetLang);
                return {
                    ...result,
                    detectedLanguage: cached.language,
                    confidence: cached.confidence,
                    detectionMethod: 'cached'
                };
            }

            try {
                const result = await this.translateText(text, 'auto', targetLang);
                if (result.confidence && result.confidence >= this.MIN_CONFIDENCE) {
                    this.cacheDetection(text, {
                        language: result.detectedLanguage,
                        confidence: result.confidence,
                        method: 'auto'
                    });
                    return result;
                } else {
                    return this.tryFallbackLanguages(text, targetLang);
                }
            } catch (error) {
                return this.tryFallbackLanguages(text, targetLang);
            }
        }

        async tryFallbackLanguages(text, targetLang) {
            const orderedFallbacks = this.COMMON_FALLBACKS.sort((a, b) => {
                const aSuccess = this.stats.get(a)?.successRate || 0;
                const bSuccess = this.stats.get(b)?.successRate || 0;
                return bSuccess - aSuccess;
            });

            for (const sourceLang of orderedFallbacks) {
                try {
                    const result = await this.translateText(text, sourceLang, targetLang);
                    const similarity = this.calculateSimilarity(text, result.translatedText);

                    if (similarity < 0.8) {
                        this.cacheDetection(text, {
                            language: sourceLang,
                            confidence: 0.6,
                            method: 'fallback'
                        });
                        return {
                            ...result,
                            detectedLanguage: sourceLang,
                            detectionMethod: 'fallback'
                        };
                    }
                } catch (e) {
                    continue;
                }
            }

            throw new Error('Could not detect language with any method');
        }

        getCachedDetection(text) {
            const key = text.substring(0, 50) + ':' + text.length;
            return this.cache.get(key);
        }

        cacheDetection(text, detection) {
            const key = text.substring(0, 50) + ':' + text.length;
            this.cache.set(key, { ...detection, timestamp: Date.now() });

            if (this.cache.size > 100) {
                const oldestKey = this.cache.keys().next().value;
                this.cache.delete(oldestKey);
            }
        }

        calculateSimilarity(text1, text2) {
            const set1 = new Set(text1.toLowerCase().split(''));
            const set2 = new Set(text2.toLowerCase().split(''));
            const intersection = new Set([...set1].filter(x => set2.has(x)));
            return intersection.size / Math.max(set1.size, set2.size);
        }

        async translateText(text, sourceLang, targetLang) {
            return new Promise((resolve, reject) => {
                if (!config.translateApiKey) {
                    reject(new Error('Translation API key not configured'));
                    return;
                }

                const body = { q: text, target: targetLang, format: 'text' };
                if (sourceLang !== 'auto') body.source = sourceLang;

                GM_xmlhttpRequest({
                    method: 'POST',
                    url: `https://translation.googleapis.com/language/translate/v2?key=${config.translateApiKey}`,
                    data: JSON.stringify(body),
                    headers: { 'Content-Type': 'application/json' },
                    timeout: 10000,
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
                                confidence: translation.confidence || 1.0,
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

        updateButtonPosition(img, button);

        button.onclick = e => {
            e.stopPropagation();
            translateImage(img);
        };

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

    // Legacy detectText function for backward compatibility
    function detectText(imageBase64) {
        const adaptiveDetector = new TextDetector(config.visionApiKey, {
            // DBSCAN parameters from settings menu
            dbscanEps: config.dbscanEps,
            dbscanMinPoints: config.dbscanMinPoints,
        });
        return adaptiveDetector.detectText(imageBase64);
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
        let translatedText, originalText, detectedLanguage, confidence, detectionMethod;

        if (typeof result === 'string') {
            translatedText = result;
            originalText = img.originalText || 'Original text not available';
            detectedLanguage = null;
            confidence = null;
            detectionMethod = null;
        } else {
            translatedText = result.translatedText;
            originalText = result.originalText;
            detectedLanguage = result.detectedLanguage;
            confidence = result.confidence;
            detectionMethod = result.detectionMethod;
        }

        const existing = overlayMap.get(img);
        if (existing) existing.remove();

        const overlay = document.createElement('div');
        overlay.className = 'img-translator-overlay';
        overlay.style.width = `${img.width}px`;

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

        if (confidence && confidence < 0.8) {
            const warning = document.createElement('div');
            warning.style.cssText = 'padding:5px 10px;background:#fff3cd;border-top:1px solid #ffeaa7;font-size:11px;color:#856404;';
            warning.textContent = 'Low confidence detection. Consider setting source language manually for better accuracy.';
            overlay.appendChild(warning);
        }

        overlay.querySelector('button').onclick = () => {
            overlay.remove();
            overlayMap.delete(img);
        };

        img.parentNode.insertBefore(overlay, img.nextSibling);
        overlayMap.set(img, overlay);

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

            img.originalText = fullText;

            const result = await detector.translateWithSmartDetection(fullText, config.targetLang);
            createTranslationDisplay(img, result);

            showToast(`Translation complete!`);

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
            <h3>Text Clustering</h3>
            <label>
                <input type="checkbox" id="useDbscan" ${config.useDbscan !== false ? 'checked' : ''}>
                Enable DBSCAN text clustering
            </label>
            <label>Epsilon Distance (pixels)</label>
            <input type="number" id="dbscanEps" min="10" max="200" value="${config.dbscanEps || 50}">
            <label>Minimum Points</label>
            <input type="number" id="dbscanMinPoints" min="1" max="10" value="${config.dbscanMinPoints || 3}">
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
                minImageHeight: parseInt($('#minImageHeight').value),
                useDbscan: $('#useDbscan').checked,
                dbscanEps: parseInt($('#dbscanEps').value),
                dbscanMinPoints: parseInt($('#dbscanMinPoints').value)
            };

            GM_setValue('imageTranslatorConfig', config);
            panel.remove();
            showToast('Settings saved');

            removeAllOverlays();
            removeAllButtons();
            setTimeout(addTranslateButtons, 500);
        };

        panel.querySelector('.btn-cancel').onclick = () => panel.remove();
        document.body.appendChild(panel);
    }

    function translateAllImages() {
        const images = Array.from($('img')).filter(isEligibleImage);
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

        GM_registerMenuCommand('Settings', showSettingsPanel);
        GM_registerMenuCommand('Translate All Images', translateAllImages);
        GM_registerMenuCommand('Remove All Translations', removeAllOverlays);

        setTimeout(addTranslateButtons, 1000);

        window.addEventListener('resize', updateAllButtonPositions);
        window.addEventListener('scroll', updateAllButtonPositions);

        setupObserver();
    }

    // Start
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();