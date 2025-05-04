// ==UserScript==
// @name       Unlock Hath Perks
// @name:zh-TW 解鎖 Hath Perks
// @name:zh-CN 解锁 Hath Perks
// @description       Unlock Hath Perks and add other helpers
// @description:zh-TW 解鎖 Hath Perks 及增加一些小工具
// @description:zh-CN 解锁 Hath Perks 及增加一些小工具
// @namespace   https://flandre.in/github
// @version     2.2.5
// @match       https://e-hentai.org/*
// @match       https://exhentai.org/*
// @require     https://unpkg.com/vue@2.6.9/dist/vue.min.js
// @icon        https://i.imgur.com/JsU0vTd.png
// @grant       GM_getValue
// @grant       GM.getValue
// @grant       GM_setValue
// @grant       GM.setValue
// @noframes
// @author      FlandreDaisuki
// @supportURL  https://github.com/FlandreDaisuki/My-Browser-Extensions/issues
// @homepageURL https://github.com/FlandreDaisuki/My-Browser-Extensions/blob/master/userscripts/UnlockHathPerks/README.md
// @license     MPLv2
// @downloadURL https://github.com/jasonzliang/gists/raw/refs/heads/master/userscripts/infinite_scroll.user.js
// @updateURL https://github.com/jasonzliang/gists/raw/refs/heads/master/userscripts/infinite_scroll.user.js
// ==/UserScript==

(function() {
  'use strict';

  // Utility functions - simplified
  const $ = s => document.querySelector(s);
  const $find = (el, s) => el.querySelector(s);

  const $el = (tag, attr = {}, cb) => {
    const el = document.createElement(tag);
    if (typeof attr === 'string') {
      el.textContent = attr;
    } else {
      Object.assign(el, attr);
    }
    cb?.(el);
    return el;
  };

  const $style = s => $el('style', s, el => document.head.appendChild(el));

  // Optimized throttle function
  const throttle = (fn, timeout = 200, immediate = true) => {
    let locked = false;
    return (...args) => {
      if (!locked) {
        locked = true;
        if (immediate) fn(...args);
        setTimeout(() => {
          if (!immediate) fn(...args);
          locked = false;
        }, timeout);
      }
    };
  };

  // More efficient scroll detection
  const getScrollPercentage = () => {
    const st = window.scrollY || document.documentElement.scrollTop;
    const sh = document.documentElement.scrollHeight;
    const ch = window.innerHeight;
    return (st / (sh - ch)) * 100;
  };

  // #region DOM setup

  // Set up the navigation button
  const navdiv = $el('div');
  const navbtn = $el('a', {
    id: 'uhp-btn',
    textContent: 'Unlock Hath Perks'
  });

  const uhpPanelContainer = $el('div', {
    className: 'hidden',
    id: 'uhp-panel-container'
  });

  const uhpPanel = $el('div', {
    id: 'uhp-panel'
  }, el => {
    if (location.host === 'exhentai.org') {
      el.classList.add('dark');
    }
    el.addEventListener('click', ev => ev.stopPropagation());
  });

  // Setup event listeners
  navbtn.addEventListener('click', () => {
    uhpPanelContainer.classList.remove('hidden');
  });

  uhpPanelContainer.addEventListener('click', () => {
    uhpPanelContainer.classList.add('hidden');
  });

  // Append elements to the DOM
  const nb = $('#nb');
  if (nb) {
    nb.appendChild(navdiv);
    navdiv.appendChild(navbtn);
  }

  document.body.appendChild(uhpPanelContainer);
  uhpPanelContainer.appendChild(uhpPanel);

  // #endregion DOM setup

  // #region Config setup

  const uhpConfig = Object.assign({
    abg: true,
    mt: true,
    pe: true
  }, GM_getValue('uhp', {}));

  GM_setValue('uhp', uhpConfig);

  // Apply Ad block if enabled
  if (uhpConfig.abg) {
    Object.defineProperty(window, 'adsbyjuicy', {
      configurable: false,
      enumerable: false,
      writable: false,
      value: Object.create(null)
    });
  }

  // #endregion Config setup

  // #region Functionality implementations

  // Common page fetching function used by both infinite scroll implementations
  const fetchPage = async (url, selectors) => {
    const result = { elements: [], nextURL: null };
    if (!url) return result;

    try {
      const resp = await fetch(url, {
        credentials: 'same-origin',
        cache: 'force-cache'
      });

      if (resp.ok) {
        const html = await resp.text();
        const docEl = new DOMParser().parseFromString(html, 'text/html').documentElement;

        const parent = $find(docEl, selectors.parent);
        result.elements = parent ? [...parent.children] : [];

        const nextEl = $find(docEl, selectors.np);
        result.nextURL = nextEl?.href || null;
      }
    } catch (error) {
      console.error('Error fetching page:', error);
    }

    return result;
  };

  // Handle infinite scroll for gallery pages
  if (location.pathname.startsWith('/g/') && uhpConfig.mt) {
    (async () => {
      const selectors = {
        np: '.ptt td:last-child > a',
        parent: '#gdt'
      };

      const pageState = {
        parent: $(selectors.parent),
        lock: false,
        nextURL: null,
        preloadedPages: [],
        preloadLimit: 3,
        loadedCount: 0
      };

      if (!pageState.parent) return;

      // Initial page setup
      const thisPage = await fetchPage(location.href, selectors);

      // Clear the container
      while (pageState.parent.firstChild) {
        pageState.parent.firstChild.remove();
      }

      // Add current page elements
      thisPage.elements
        .filter(el => !el.classList.contains('c'))
        .forEach(el => pageState.parent.appendChild(el));

      pageState.nextURL = thisPage.nextURL;

      if (!pageState.nextURL) return;

      // Preload function
      const preloadPages = async () => {
        if (!pageState.nextURL || pageState.preloadedPages.length >= pageState.preloadLimit) return;

        const nextPage = await fetchPage(pageState.nextURL, selectors);
        if (nextPage.elements.length > 0) {
          pageState.preloadedPages.push({
            elements: nextPage.elements.filter(el => !el.classList.contains('c')),
            nextURL: nextPage.nextURL
          });

          pageState.nextURL = nextPage.nextURL;

          // Continue preloading if needed
          if (nextPage.nextURL && pageState.preloadedPages.length < pageState.preloadLimit) {
            preloadPages();
          }
        }
      };

      // Start initial preload
      preloadPages();

      // Scroll handler
      const scrollHandler = throttle(async () => {
        const threshold = Math.min(90, 60 + (pageState.loadedCount * 2));

        if (getScrollPercentage() > threshold && !pageState.lock) {
          pageState.lock = true;

          // Use preloaded page if available
          if (pageState.preloadedPages.length > 0) {
            const nextPage = pageState.preloadedPages.shift();
            nextPage.elements.forEach(el => pageState.parent.appendChild(el));
            pageState.loadedCount++;

            // Start preloading more
            preloadPages();
            pageState.lock = false;
          }
          else if (pageState.nextURL) {
            // Direct fetch if no preloaded pages
            const nextPage = await fetchPage(pageState.nextURL, selectors);

            nextPage.elements
              .filter(el => !el.classList.contains('c'))
              .forEach(el => pageState.parent.appendChild(el));

            pageState.nextURL = nextPage.nextURL;
            pageState.loadedCount++;

            // Preload next pages
            preloadPages();
            pageState.lock = false;
          } else {
            pageState.lock = false;
          }
        }
      }, 200);

      // Add scroll listener
      document.addEventListener('scroll', scrollHandler);
    })();
  }

  // Handle infinite scroll for search results
  if ($('input[name="f_search"]') && $('.itg') && uhpConfig.pe) {
    (async () => {
      const isTableLayout = Boolean($('table.itg'));
      const status = $el('h1', {
        textContent: 'Loading...',
        id: 'uhp-status'
      });

      const selectors = {
        np: '.ptt td:last-child > a, .searchnav a[href*="next="]',
        parent: isTableLayout ? 'table.itg > tbody' : 'div.itg'
      };

      const pageState = {
        parent: $(selectors.parent),
        lock: false,
        nextURL: null,
        preloadedPages: [],
        preloadLimit: 3,
        loadedCount: 0,
        maxPages: 500
      };

      if (!pageState.parent) return;

      // Initial page setup
      const thisPage = await fetchPage(location.href, selectors);

      // Clear container
      while (pageState.parent.firstChild) {
        pageState.parent.firstChild.remove();
      }

      // Add current page elements
      thisPage.elements.forEach(el => pageState.parent.appendChild(el));
      pageState.nextURL = thisPage.nextURL;

      // Replace pagination with status indicator
      $('table.ptb, .itg + .searchnav, #favform + .searchnav')?.replaceWith(status);

      if (!pageState.nextURL) {
        status.textContent = 'End';
        return;
      }

      // Preload function
      const preloadPages = async () => {
        if (!pageState.nextURL || pageState.preloadedPages.length >= pageState.preloadLimit) return;

        const nextPage = await fetchPage(pageState.nextURL, selectors);
        if (nextPage.elements.length > 0) {
          pageState.preloadedPages.push({
            elements: nextPage.elements,
            nextURL: nextPage.nextURL
          });

          pageState.nextURL = nextPage.nextURL;

          // Continue preloading if needed
          if (nextPage.nextURL && pageState.preloadedPages.length < pageState.preloadLimit) {
            preloadPages();
          }
        }
      };

      // Start initial preload
      preloadPages();

      // Scroll handler
      const scrollHandler = throttle(async () => {
        const threshold = Math.min(95, 60 + (pageState.loadedCount * 2));

        if (getScrollPercentage() > threshold && !pageState.lock &&
            (pageState.preloadedPages.length > 0 || pageState.nextURL) &&
            pageState.loadedCount < pageState.maxPages) {

          pageState.lock = true;
          status.textContent = `Loading page ${pageState.loadedCount + 1}...`;

          // Use preloaded page if available
          if (pageState.preloadedPages.length > 0) {
            const nextPage = pageState.preloadedPages.shift();
            nextPage.elements.forEach(el => pageState.parent.appendChild(el));
            pageState.loadedCount++;

            // Update status
            if (!nextPage.nextURL || pageState.loadedCount >= pageState.maxPages) {
              status.textContent = 'End';
            } else {
              status.textContent = `Loaded ${pageState.loadedCount} pages`;
            }

            // Start preloading more
            setTimeout(() => {
              preloadPages();
              pageState.lock = false;
            }, 150);
          }
          else if (pageState.nextURL) {
            // Direct fetch if no preloaded pages
            try {
              const nextPage = await fetchPage(pageState.nextURL, selectors);

              if (nextPage.elements.length > 0) {
                nextPage.elements.forEach(el => pageState.parent.appendChild(el));
                pageState.nextURL = nextPage.nextURL;
                pageState.loadedCount++;

                // Update status
                if (!pageState.nextURL || pageState.loadedCount >= pageState.maxPages) {
                  status.textContent = 'End';
                } else {
                  status.textContent = `Loaded ${pageState.loadedCount} pages`;
                }
              } else {
                status.textContent = 'End';
                pageState.nextURL = null;
              }
            } catch (error) {
              console.error('Error loading next page:', error);
              status.textContent = 'Error loading more pages';
            }

            // Clean up and preload more
            setTimeout(() => {
              if (pageState.nextURL) {
                preloadPages();
              }
              pageState.lock = false;
            }, 150);
          } else {
            pageState.lock = false;
          }
        }
      }, 200);

      // Add scroll listener
      document.addEventListener('scroll', scrollHandler);

      // Add intersection observer for better performance
      const observer = new IntersectionObserver(entries => {
        if (entries[0].isIntersecting) {
          scrollHandler();
        }
      }, {
        root: null,
        rootMargin: '200px',
        threshold: 0
      });

      observer.observe(status);
    })();
  }

  // #endregion Functionality implementations

  // #region Vue Panel

  const uhpPanelTemplate = `
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
</div>
`;

  // Vue component for settings
  new Vue({
    el: '#uhp-panel',
    template: uhpPanelTemplate,
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
      isExH() { return location.host === 'exhentai.org'; }
    },
    methods: {
      save() { GM_setValue('uhp', uhpConfig); },
      getConfId(id) { return `ubp-conf-${id}`; }
    }
  });

  // #endregion Vue Panel

  // #region Styles

  // Add CSS styles
  $style(`
/* Layout styles */
#nb {
  width: initial;
  max-width: initial;
  max-height: initial;
  justify-content: center;
}

/* Search styles */
table.itc + p.nopm {
  display: flex;
  flex-flow: row wrap;
  justify-content: center;
}
input[name="f_search"] {
  width: 100%;
}

/* Favorites styles */
input[name="favcat"] + div {
  display: flex;
  flex-flow: row wrap;
  justify-content: center;
  gap: 8px;
}

/* Gallery grid styles */
.gl1t {
  display: flex;
  flex-flow: column;
}
.gl1t > .gl3t {
  flex: 1;
}
.gl1t > .gl3t > a {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
}

/* UHP styles */
#uhp-btn {
  cursor: pointer;
}
#uhp-panel-container {
  position: fixed;
  top: 0;
  height: 100vh;
  width: 100vw;
  background-color: rgba(200, 200, 200, 0.7);
  z-index: 2;
  display: flex;
  align-items: center;
  justify-content: center;
}
#uhp-panel-container.hidden {
  visibility: hidden;
  opacity: 0;
}
#uhp-panel {
  padding: 1.2rem;
  background-color: floralwhite;
  border-radius: 1rem;
  font-size: 1rem;
  color: darkred;
  max-width: 650px;
}
#uhp-panel.dark {
  background-color: dimgray;
  color: ghostwhite;
}
#uhp-panel .option-grid {
  display: grid;
  grid-template-columns: max-content 120px 1fr;
  grid-gap: 0.5rem 1rem;
  margin: 0.5rem 1rem;
}
#uhp-panel .option-grid > * {
  display: flex;
  justify-content: center;
  align-items: center;
}
#uhp-status {
  text-align: center;
  font-size: 3rem;
  clear: both;
  padding: 2rem 0;
}

/* Material switch styles */
.material-switch {
  display: inline-block;
}
.material-switch > input[type="checkbox"] {
  display: none;
}
.material-switch > input[type="checkbox"] + label {
  display: inline-block;
  position: relative;
  margin: 6px;
  border-radius: 8px;
  width: 40px;
  height: 16px;
  opacity: 0.3;
  background-color: rgb(0, 0, 0);
  box-shadow: inset 0px 0px 10px rgba(0, 0, 0, 0.5);
  transition: all 0.4s ease-in-out;
}
.material-switch > input[type="checkbox"] + label::after {
  position: absolute;
  top: -4px;
  left: -4px;
  border-radius: 16px;
  width: 24px;
  height: 24px;
  content: "";
  background-color: rgb(255, 255, 255);
  box-shadow: 0px 0px 5px rgba(0, 0, 0, 0.3);
  transition: all 0.3s ease-in-out;
}
.material-switch > input[type="checkbox"]:checked + label {
  background-color: #0e0;
  opacity: 0.7;
}
.material-switch > input[type="checkbox"]:checked + label::after {
  background-color: inherit;
  left: 20px;
}
.material-switch > input[type="checkbox"]:disabled + label::after {
  content: "\\f023";
  line-height: 24px;
  font-size: 0.8em;
  font-family: FontAwesome;
  color: initial;
}`);

  // Add FontAwesome
  $el('link', {
    href: 'https://use.fontawesome.com/releases/v5.8.0/css/all.css',
    rel: 'stylesheet',
    integrity: 'sha384-Mmxa0mLqhmOeaE8vgOSbKacftZcsNYDjQzuCOm6D02luYSzBG8vpaOykv9lFQ51Y',
    crossOrigin: 'anonymous'
  }, el => document.head.appendChild(el));

  // #endregion Styles
})();