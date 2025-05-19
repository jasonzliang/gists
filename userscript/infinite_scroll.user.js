// ==UserScript==
// @name        Unlock Hath Perks
// @name:zh-TW  解鎖 Hath Perks
// @name:zh-CN  解锁 Hath Perks
// @description Unlock Hath Perks and add other helpers
// @description:zh-TW 解鎖 Hath Perks 及增加一些小工具
// @description:zh-CN 解锁 Hath Perks 及增加一些小工具
// @namespace   https://flandre.in/github
// @version     2.4.5
// @match       https://e-hentai.org/*
// @match       https://exhentai.org/*
// @require     https://unpkg.com/vue@2.6.9/dist/vue.min.js
// @icon        https://i.imgur.com/JsU0vTd.png
// @grant       GM_getValue
// @grant       GM.getValue
// @grant       GM_setValue
// @grant       GM.setValue
// @noframes
// @author      FlandreDaisuki, Anon
// @supportURL  https://github.com/FlandreDaisuki/My-Browser-Extensions/issues
// @homepageURL https://github.com/FlandreDaisuki/My-Browser-Extensions/blob/master/userscripts/UnlockHathPerks/README.md
// @license     MPLv2
// @downloadURL https://github.com/jasonzliang/gists/raw/refs/heads/master/userscripts/infinite_scroll.user.js
// @updateURL   https://github.com/jasonzliang/gists/raw/refs/heads/master/userscripts/infinite_scroll.user.js
// ==/UserScript==

(function () {
    'use strict';

    // Optimized utility functions
    const $ = s => document.querySelector(s),
        $find = (el, s) => el.querySelector(s),
        $el = (tag, attr = {}, cb) => {
            const el = document.createElement(tag);
            typeof attr === 'string' ? el.textContent = attr : Object.assign(el, attr);
            cb?.(el);
            return el;
        },
        $style = s => document.head.appendChild($el('style', s)),
        throttle = (fn, timeout = 200) => {
            let locked = false;
            return (...args) => {
                if (locked) return;
                locked = true;
                setTimeout(() => locked = false, timeout);
                fn(...args);
            };
        },
        getScrollPercentage = () => {
            const st = window.scrollY || document.documentElement.scrollTop,
                sh = document.documentElement.scrollHeight,
                ch = window.innerHeight;
            return (st / (sh - ch)) * 100;
        },
        isDarkTheme = location.host === 'exhentai.org';

    // Create page divider with page number
    const createPageDivider = (pageNum, containerType) => {
        const wrapper = containerType === 'gallery' ?
            $el('div', {
                className: 'uhp-gallery-divider'
            }) :
            containerType === 'search-grid' ?
            $el('div', {
                className: 'uhp-divider-wrapper search-grid-divider'
            }) :
            $el('tr', {
                className: 'uhp-divider-wrapper search-table-divider'
            });

        const pageInfo = $el('span', {
            textContent: `Page ${pageNum}`,
            className: 'uhp-page-number' + (isDarkTheme ? ' dark' : '')
        });

        if (containerType === 'search-table') {
            const cell = $el('td', {
                colSpan: '10'
            });
            cell.appendChild(pageInfo);
            wrapper.appendChild(cell);
        } else {
            wrapper.appendChild(pageInfo);
        }

        return wrapper;
    };

    // Insert divider
    const insertDivider = (container, pageNum) => {
        let containerType = 'search-grid';
        if (container.id === 'gdt') containerType = 'gallery';
        else if (container.tagName.toLowerCase() === 'tbody') containerType = 'search-table';

        return container.appendChild(createPageDivider(pageNum, containerType));
    };

    // Config setup
    const uhpConfig = Object.assign({
        abg: true,
        mt: true,
        pe: true
    }, GM_getValue('uhp', {}));
    GM_setValue('uhp', uhpConfig);

    // Ad block
    if (uhpConfig.abg) {
        Object.defineProperty(window, 'adsbyjuicy', {
            configurable: false,
            enumerable: false,
            writable: false,
            value: Object.create(null)
        });
    }

    // DOM setup
    const navbtn = $el('a', {
            id: 'uhp-btn',
            textContent: 'Unlock Hath Perks'
        }),
        uhpPanelContainer = $el('div', {
            className: 'hidden',
            id: 'uhp-panel-container'
        }),
        uhpPanel = $el('div', {
            id: 'uhp-panel',
            className: isDarkTheme ? 'dark' : ''
        }, el => el.addEventListener('click', ev => ev.stopPropagation()));

    // Setup event listeners
    navbtn.addEventListener('click', () => uhpPanelContainer.classList.remove('hidden'));
    uhpPanelContainer.addEventListener('click', () => uhpPanelContainer.classList.add('hidden'));

    // Append elements
    const nb = $('#nb');
    if (nb) {
        const navdiv = $el('div');
        navdiv.appendChild(navbtn);
        nb.appendChild(navdiv);
    }
    uhpPanelContainer.appendChild(uhpPanel);
    document.body.appendChild(uhpPanelContainer);

    // Page fetching function
    const fetchPage = async (url, selectors) => {
        if (!url) return {
            elements: [],
            nextURL: null
        };
        try {
            const resp = await fetch(url, {
                credentials: 'same-origin'
            });
            if (resp.ok) {
                const html = await resp.text(),
                    docEl = new DOMParser().parseFromString(html, 'text/html').documentElement,
                    parent = $find(docEl, selectors.parent),
                    nextEl = $find(docEl, selectors.np);

                return {
                    elements: parent ? [...parent.children] : [],
                    nextURL: nextEl?.href || null
                };
            }
        } catch (e) {
            console.error('Error fetching page:', e);
        }
        return {
            elements: [],
            nextURL: null
        };
    };

    // Gallery pages infinite scroll
    if (location.pathname.startsWith('/g/') && uhpConfig.mt) {
        (async () => {
            const selectors = {
                    np: '.ptt td:last-child > a',
                    parent: '#gdt'
                },
                container = $(selectors.parent);

            if (!container) return;

            // Improved state object
            const state = {
                lock: false,
                currentPage: 1, // Current page number
                loadedPages: new Set([1]), // Track loaded page numbers
                nextURL: null,
                preloadedPages: [], // Store {pageNum, elements, nextURL}
                preloadLimit: 3,
                maxPages: 100 // Safety limit
            };

            // Helper function to extract page number from URL
            const getPageNum = (url) => {
                try {
                    const match = url.match(/\?p=(\d+)/);
                    return match ? parseInt(match[1]) + 1 : 1; // URLs are 0-indexed, display is 1-indexed
                } catch (e) {
                    return null;
                }
            };

            // Initial page setup
            const thisPage = await fetchPage(location.href, selectors);

            // Clear the container
            while (container.firstChild) container.firstChild.remove();

            // Add current page elements
            thisPage.elements.filter(el => !el.classList.contains('c'))
                .forEach(el => container.appendChild(el));

            // Set next URL
            state.nextURL = thisPage.nextURL;
            if (!state.nextURL) return;

            // Preload specific page
            const preloadPage = async (url, pageNum) => {
                if (!url || state.loadedPages.has(pageNum)) return null;

                try {
                    const page = await fetchPage(url, selectors);
                    if (page.elements.length > 0) {
                        return {
                            pageNum: pageNum,
                            elements: page.elements.filter(el => !el.classList.contains('c')),
                            nextURL: page.nextURL
                        };
                    }
                } catch (e) {
                    console.error(`Error preloading page ${pageNum}:`, e);
                }
                return null;
            };

            // Preload multiple pages sequentially
            const preloadNextPages = async () => {
                if (!state.nextURL || state.lock) return;

                let currentURL = state.nextURL;
                let pagesAdded = 0;

                while (currentURL && pagesAdded < state.preloadLimit) {
                    const nextPageNum = getPageNum(currentURL);

                    // Skip if already loaded or preloaded
                    if (state.loadedPages.has(nextPageNum) ||
                        state.preloadedPages.some(p => p.pageNum === nextPageNum)) {
                        break;
                    }

                    const nextPage = await preloadPage(currentURL, nextPageNum);
                    if (nextPage) {
                        state.preloadedPages.push(nextPage);
                        currentURL = nextPage.nextURL;
                        pagesAdded++;
                    } else {
                        break;
                    }
                }
            };

            // Load and display a page
            const displayPage = (pageData) => {
                if (!pageData || state.loadedPages.has(pageData.pageNum)) return false;

                // Mark as loaded
                state.loadedPages.add(pageData.pageNum);

                // Add divider with correct page number
                insertDivider(container, pageData.pageNum);

                // Add page elements
                pageData.elements.forEach(el => container.appendChild(el));

                // Update state
                state.currentPage = pageData.pageNum;

                return true;
            };

            // Start preloading
            preloadNextPages();

            // Scroll handler
            document.addEventListener('scroll', throttle(() => {
                if (state.lock || state.loadedPages.size >= state.maxPages) return;

                const ptbElement = $('table.ptb');
                if (!ptbElement) return;

                const anchorTop = ptbElement.getBoundingClientRect().top,
                    vh = window.innerHeight;

                if (anchorTop >= vh * 2) return;

                state.lock = true;

                // Process function - handles displaying next page and updating state
                const processNextPage = async () => {
                    try {
                        // First try to use a preloaded page
                        if (state.preloadedPages.length > 0) {
                            // Sort by page number to ensure order
                            state.preloadedPages.sort((a, b) => a.pageNum - b.pageNum);
                            const nextPage = state.preloadedPages.shift();

                            // Display the page
                            const displayed = displayPage(nextPage);

                            if (displayed && nextPage.nextURL) {
                                state.nextURL = nextPage.nextURL;

                                // Preload more pages if needed
                                if (state.preloadedPages.length < state.preloadLimit) {
                                    setTimeout(() => preloadNextPages(), 100);
                                }
                            }
                        }
                        // If no preloaded pages, fetch the next one directly
                        else if (state.nextURL) {
                            const nextPageNum = getPageNum(state.nextURL);

                            // Skip if already loaded
                            if (!state.loadedPages.has(nextPageNum)) {
                                const pageData = await preloadPage(state.nextURL, nextPageNum);

                                if (pageData) {
                                    // Display the page
                                    displayPage(pageData);

                                    // Update next URL
                                    state.nextURL = pageData.nextURL;

                                    // Preload more pages
                                    setTimeout(() => preloadNextPages(), 100);
                                }
                            }
                        }
                    } catch (e) {
                        console.error("Error processing next page:", e);
                    } finally {
                        state.lock = false;
                    }
                };

                // Process next page
                processNextPage();
            }, 250)); // Slightly higher throttle time for better performance
        })();
    }

    // Search results infinite scroll
    if ($('input[name="f_search"]') && $('.itg') && uhpConfig.pe) {
        (async () => {
            const isTableLayout = Boolean($('table.itg')),
                status = $el('h1', {
                    textContent: 'Loading initial pages...',
                    id: 'uhp-status'
                }),
                selectors = {
                    np: '.ptt td:last-child > a, .searchnav a[href*="next="]',
                    parent: isTableLayout ? 'table.itg > tbody' : 'div.itg'
                },
                container = $(selectors.parent);

            if (!container) return;

            // State object
            const state = {
                lock: false,
                nextURL: null,
                preloadedPages: [],
                preloadLimit: 2,
                loadedCount: 1,
                maxPages: 500,
                initialLoaded: false
            };

            // Initial page setup
            const thisPage = await fetchPage(location.href, selectors);
            while (container.firstChild) container.firstChild.remove();

            thisPage.elements.forEach(el => container.appendChild(el));
            state.nextURL = thisPage.nextURL;

            // Replace pagination
            $('table.ptb, .itg + .searchnav, #favform + .searchnav')?.replaceWith(status);

            if (!state.nextURL) {
                status.textContent = 'End';
                return;
            }

            // Preload next pages
            const preloadNext = async () => {
                if (state.preloadLimit <= 0 || !state.nextURL ||
                    state.preloadedPages.length >= state.preloadLimit) return;

                const nextPage = await fetchPage(state.nextURL, selectors);
                if (nextPage.elements.length) {
                    state.preloadedPages.push({
                        elements: nextPage.elements,
                        nextURL: nextPage.nextURL
                    });

                    state.nextURL = nextPage.nextURL;

                    if (nextPage.nextURL && state.preloadedPages.length < state.preloadLimit)
                        preloadNext();
                }
            };

            // Load initial pages
            const loadInitialPages = async () => {
                if (state.preloadLimit <= 0) {
                    state.initialLoaded = true;
                    status.textContent = state.nextURL ? `Loaded ${state.loadedCount} pages` : 'End';
                    return;
                }

                state.lock = true;
                let remaining = state.preloadLimit,
                    currentURL = state.nextURL;

                while (remaining > 0 && currentURL) {
                    status.textContent = `Loading initial pages (${state.loadedCount + 1}/${Math.max(1, state.preloadLimit) + 1})...`;

                    const nextPage = await fetchPage(currentURL, selectors);
                    if (nextPage.elements.length) {
                        insertDivider(container, state.loadedCount + 1);
                        nextPage.elements.forEach(el => container.appendChild(el));

                        currentURL = nextPage.nextURL;
                        state.loadedCount++;
                        remaining--;
                    } else {
                        currentURL = null;
                    }
                }

                state.nextURL = currentURL;
                state.initialLoaded = true;
                state.lock = false;

                status.textContent = state.nextURL ? `Loaded ${state.loadedCount} pages` : 'End';
                if (state.nextURL && state.preloadLimit > 0) preloadNext();
            };

            // Start loading
            loadInitialPages();

            // Scroll handler
            const scrollHandler = throttle(() => {
                if (!state.initialLoaded || state.lock ||
                    (!state.preloadedPages.length && !state.nextURL) ||
                    state.loadedCount >= state.maxPages) return;

                const threshold = Math.min(95, 60 + (state.loadedCount * 2));
                if (getScrollPercentage() <= threshold) return;

                state.lock = true;
                status.textContent = `Loading page ${state.loadedCount + 1}...`;

                if (state.preloadedPages.length) {
                    const nextPage = state.preloadedPages.shift();
                    insertDivider(container, state.loadedCount + 1);

                    nextPage.elements.forEach(el => container.appendChild(el));
                    state.loadedCount++;

                    status.textContent = !nextPage.nextURL || state.loadedCount >= state.maxPages ?
                        'End' : `Loaded ${state.loadedCount} pages`;

                    setTimeout(() => {
                        if (state.preloadLimit > 0) preloadNext();
                        state.lock = false;
                    }, 150);
                } else if (state.nextURL) {
                    (async () => {
                        try {
                            const nextPage = await fetchPage(state.nextURL, selectors);
                            if (nextPage.elements.length) {
                                insertDivider(container, state.loadedCount + 1);

                                nextPage.elements.forEach(el => container.appendChild(el));
                                state.nextURL = nextPage.nextURL;
                                state.loadedCount++;

                                status.textContent = !state.nextURL || state.loadedCount >= state.maxPages ?
                                    'End' : `Loaded ${state.loadedCount} pages`;
                            } else {
                                status.textContent = 'End';
                                state.nextURL = null;
                            }
                        } catch (error) {
                            console.error('Error loading next page:', error);
                            status.textContent = 'Error loading more pages';
                        }

                        setTimeout(() => {
                            if (state.nextURL && state.preloadLimit > 0) preloadNext();
                            state.lock = false;
                        }, 150);
                    })();
                } else {
                    state.lock = false;
                }
            });

            // Add scroll listener
            document.addEventListener('scroll', scrollHandler);

            // Intersection observer for better performance
            new IntersectionObserver(entries => {
                if (entries[0].isIntersecting && state.initialLoaded) scrollHandler();
            }, {
                rootMargin: '200px',
                threshold: 0
            }).observe(status);
        })();
    }

    // Vue Panel
    new Vue({
        el: '#uhp-panel',
        template: `
<div id="uhp-panel" :class="{ dark: isExH }" @click.stop>
  <h1>Hath Perks</h1>
  <div>
    <div v-for="d in HathPerks" class="option-grid">
      <div class="material-switch">
        <input :id="getConfId(d.abbr)" type="checkbox" v-model="conf[d.abbr]" @change="save" />
        <label :for="getConfId(d.abbr)"></label>
      </div>
      <span class="uhp-conf-title">{{d.title}}</span>
      <span class="uhp-conf-desc">{{d.desc}}</span>
    </div>
  </div>
</div>`,
        data: {
            conf: uhpConfig,
            HathPerks: [{
                abbr: 'abg',
                title: 'Ads-Be-Gone',
                desc: 'Remove ads. You can use it with adblock webextensions.',
            }, {
                abbr: 'mt',
                title: 'More Thumbs',
                desc: 'Scroll infinitely in gallery pages.',
            }, {
                abbr: 'pe',
                title: 'Paging Enlargement',
                desc: 'Scroll infinitely in search results pages.',
            }]
        },
        computed: {
            isExH() {
                return location.host === 'exhentai.org';
            }
        },
        methods: {
            save() {
                GM_setValue('uhp', uhpConfig);
            },
            getConfId(id) {
                return `ubp-conf-${id}`;
            }
        }
    });

    // CSS styles
    $style(`
#nb{width:initial;max-width:initial;max-height:initial;justify-content:center}
table.itc+p.nopm{display:flex;flex-flow:row wrap;justify-content:center}
input[name="f_search"]{width:100%}
input[name="favcat"]+div{display:flex;flex-flow:row wrap;justify-content:center;gap:8px}
.gl1t{display:flex;flex-flow:column}
.gl1t>.gl3t{flex:1}
.gl1t>.gl3t>a{display:flex;align-items:center;justify-content:center;height:100%}
#uhp-btn{cursor:pointer}
#uhp-panel-container{position:fixed;top:0;height:100vh;width:100vw;background-color:rgba(200,200,200,.7);z-index:2;display:flex;align-items:center;justify-content:center}
#uhp-panel-container.hidden{visibility:hidden;opacity:0}
#uhp-panel{padding:1.2rem;background-color:floralwhite;border-radius:1rem;font-size:1rem;color:darkred;max-width:650px}
#uhp-panel.dark{background-color:dimgray;color:ghostwhite}
#uhp-panel .option-grid{display:grid;grid-template-columns:max-content 120px 1fr;grid-gap:.5rem 1rem;margin:.5rem 1rem}
#uhp-panel .option-grid>*{display:flex;justify-content:center;align-items:center}
#uhp-status{text-align:center;font-size:3rem;clear:both;padding:2rem 0}
.material-switch{display:inline-block}
.material-switch>input[type="checkbox"]{display:none}
.material-switch>input[type="checkbox"]+label{display:inline-block;position:relative;margin:6px;border-radius:8px;width:40px;height:16px;opacity:.3;background-color:#000;box-shadow:inset 0 0 10px rgba(0,0,0,.5);transition:all .4s ease-in-out}
.material-switch>input[type="checkbox"]+label::after{position:absolute;top:-4px;left:-4px;border-radius:16px;width:24px;height:24px;content:"";background-color:#fff;box-shadow:0 0 5px rgba(0,0,0,.3);transition:all .3s ease-in-out}
.material-switch>input[type="checkbox"]:checked+label{background-color:#0e0;opacity:.7}
.material-switch>input[type="checkbox"]:checked+label::after{background-color:inherit;left:20px}
.material-switch>input[type="checkbox"]:disabled+label::after{content:"\\f023";line-height:24px;font-size:.8em;font-family:FontAwesome;color:initial}

/* Page divider styles */
.uhp-divider-wrapper{width:100%;margin:20px 0;clear:both;text-align:center}
.search-grid-divider{grid-column:1/-1!important;display:flex!important;justify-content:center!important;padding:10px 0!important;background:transparent!important}
.search-table-divider td{padding:15px 0!important;text-align:center!important}
.uhp-gallery-divider{display:flex!important;justify-content:center!important;align-items:center!important;width:100%!important;height:40px!important;margin:15px 0!important;grid-column:1/-1!important;clear:both!important;background:none!important;float:none!important}
#gdt .uhp-gallery-divider{width:100%!important;height:auto!important;float:none!important;margin:15px 0!important;padding:0!important;background:none!important;text-align:center!important;display:block!important;overflow:visible!important;position:relative!important}
.uhp-page-number{display:inline-block!important;font-weight:bold!important;font-size:16px!important;padding:5px 15px!important;background-color:#f8f8f8!important;border-radius:15px!important;box-shadow:0 1px 3px rgba(0,0,0,0.2)!important;position:relative!important;z-index:1!important}
.uhp-page-number.dark{background-color:#383838!important;color:#f1f1f1!important}
body[style*="background:#34353b"] .uhp-page-number{background-color:#34353b!important;color:#f1f1f1!important}
body[style*="background:#4f535b"] .uhp-page-number{background-color:#4f535b!important;color:#f1f1f1!important}
.itg.gld>.uhp-divider-wrapper{grid-column:1/-1!important;width:100%!important;display:flex!important;justify-content:center!important;background:none!important}
`);

    // Add FontAwesome
    document.head.appendChild($el('link', {
        href: 'https://use.fontawesome.com/releases/v5.8.0/css/all.css',
        rel: 'stylesheet',
        integrity: 'sha384-Mmxa0mLqhmOeaE8vgOSbKacftZcsNYDjQzuCOm6D02luYSzBG8vpaOykv9lFQ51Y',
        crossOrigin: 'anonymous'
    }));
})();