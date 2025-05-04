// ==UserScript==
// @name       Unlock Hath Perks
// @name:zh-TW 解鎖 Hath Perks
// @name:zh-CN 解锁 Hath Perks
// @description       Unlock Hath Perks and add other helpers
// @description:zh-TW 解鎖 Hath Perks 及增加一些小工具
// @description:zh-CN 解锁 Hath Perks 及增加一些小工具
// @namespace   https://flandre.in/github
// @version     2.3.0
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

  // Optimized utility functions
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
  const $style = s => document.head.appendChild($el('style', s));
  const throttle = (fn, timeout = 200) => {
    let locked = false;
    return (...args) => {
      if (!locked) {
        locked = true;
        setTimeout(() => { locked = false; }, timeout);
        fn(...args);
      }
    };
  };
  const getScrollPercentage = () => {
    const st = window.scrollY || document.documentElement.scrollTop;
    const sh = document.documentElement.scrollHeight;
    const ch = window.innerHeight;
    return (st / (sh - ch)) * 100;
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
  navbtn.addEventListener('click', () => uhpPanelContainer.classList.remove('hidden'));
  uhpPanelContainer.addEventListener('click', () => uhpPanelContainer.classList.add('hidden'));

  // Append elements
  const nb = $('#nb');
  if (nb) {
    navdiv.appendChild(navbtn);
    nb.appendChild(navdiv);
  }
  uhpPanelContainer.appendChild(uhpPanel);
  document.body.appendChild(uhpPanelContainer);

  // Page fetching function
  const fetchPage = async (url, selectors) => {
    if (!url) return { elements: [], nextURL: null };
    try {
      const resp = await fetch(url, { credentials: 'same-origin' });
      if (resp.ok) {
        const html = await resp.text();
        const docEl = new DOMParser().parseFromString(html, 'text/html').documentElement;
        const parent = $find(docEl, selectors.parent);
        const elements = parent ? [...parent.children] : [];
        const nextEl = $find(docEl, selectors.np);
        return { elements, nextURL: nextEl?.href || null };
      }
    } catch (e) { console.error('Error fetching page:', e); }
    return { elements: [], nextURL: null };
  };

  // Gallery pages infinite scroll
  if (location.pathname.startsWith('/g/') && uhpConfig.mt) {
    (async () => {
      const selectors = { np: '.ptt td:last-child > a', parent: '#gdt' };
      const container = $(selectors.parent);
      if (!container) return;

      // State object
      const state = {
        lock: false,
        nextURL: null,
        preloadedPages: [],
        preloadLimit: 3,
        loadedCount: 0
      };

      // Initial page setup
      const thisPage = await fetchPage(location.href, selectors);
      while (container.firstChild) container.firstChild.remove();

      const filteredElements = thisPage.elements.filter(el => !el.classList.contains('c'));
      filteredElements.forEach(el => container.appendChild(el));
      state.nextURL = thisPage.nextURL;

      if (!state.nextURL) return;

      // Load initial pages immediately
      const loadInitialPages = async () => {
        state.lock = true;
        let remaining = state.preloadLimit;
        let currentURL = state.nextURL;

        while (remaining > 0 && currentURL) {
          const nextPage = await fetchPage(currentURL, selectors);
          if (nextPage.elements.length) {
            nextPage.elements
              .filter(el => !el.classList.contains('c'))
              .forEach(el => container.appendChild(el));

            currentURL = nextPage.nextURL;
            state.loadedCount++;
            remaining--;
          } else {
            currentURL = null;
          }
        }

        state.nextURL = currentURL;
        state.lock = false;
        preloadNext();
      };

      // Preload next pages
      const preloadNext = async () => {
        if (!state.nextURL || state.preloadedPages.length >= state.preloadLimit) return;

        const nextPage = await fetchPage(state.nextURL, selectors);
        if (nextPage.elements.length) {
          state.preloadedPages.push({
            elements: nextPage.elements.filter(el => !el.classList.contains('c')),
            nextURL: nextPage.nextURL
          });

          state.nextURL = nextPage.nextURL;

          if (nextPage.nextURL && state.preloadedPages.length < state.preloadLimit) {
            preloadNext();
          }
        }
      };

      // Start loading
      loadInitialPages();

      // Scroll handler
      document.addEventListener('scroll', throttle(async () => {
        if (state.lock) return;

        const threshold = Math.min(90, 70 + (state.loadedCount * 2));
        if (getScrollPercentage() <= threshold) return;

        state.lock = true;

        if (state.preloadedPages.length) {
          const nextPage = state.preloadedPages.shift();
          nextPage.elements.forEach(el => container.appendChild(el));
          state.loadedCount++;
          preloadNext();
          state.lock = false;
        } else if (state.nextURL) {
          const nextPage = await fetchPage(state.nextURL, selectors);
          nextPage.elements
            .filter(el => !el.classList.contains('c'))
            .forEach(el => container.appendChild(el));

          state.nextURL = nextPage.nextURL;
          state.loadedCount++;
          preloadNext();
          state.lock = false;
        } else {
          state.lock = false;
        }
      }));
    })();
  }

  // Search results infinite scroll
  if ($('input[name="f_search"]') && $('.itg') && uhpConfig.pe) {
    (async () => {
      const isTableLayout = Boolean($('table.itg'));
      const status = $el('h1', { textContent: 'Loading initial pages...', id: 'uhp-status' });
      const selectors = {
        np: '.ptt td:last-child > a, .searchnav a[href*="next="]',
        parent: isTableLayout ? 'table.itg > tbody' : 'div.itg'
      };

      const container = $(selectors.parent);
      if (!container) return;

      // State object
      const state = {
        lock: false,
        nextURL: null,
        preloadedPages: [],
        preloadLimit: 3,
        loadedCount: 0,
        maxPages: 500,
        initialLoaded: false
      };

      // Initial page setup
      const thisPage = await fetchPage(location.href, selectors);
      while (container.firstChild) container.firstChild.remove();

      thisPage.elements.forEach(el => container.appendChild(el));
      state.nextURL = thisPage.nextURL;
      state.loadedCount = 1;

      // Replace pagination
      $('table.ptb, .itg + .searchnav, #favform + .searchnav')?.replaceWith(status);

      if (!state.nextURL) {
        status.textContent = 'End';
        return;
      }

      // Load initial pages immediately
      const loadInitialPages = async () => {
        state.lock = true;
        let remaining = state.preloadLimit;
        let currentURL = state.nextURL;

        while (remaining > 0 && currentURL) {
          status.textContent = `Loading initial pages (${state.loadedCount + 1}/${state.preloadLimit + 1})...`;

          const nextPage = await fetchPage(currentURL, selectors);
          if (nextPage.elements.length) {
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
        if (state.nextURL) preloadNext();
      };

      // Preload next pages
      const preloadNext = async () => {
        if (!state.nextURL || state.preloadedPages.length >= state.preloadLimit) return;

        const nextPage = await fetchPage(state.nextURL, selectors);
        if (nextPage.elements.length) {
          state.preloadedPages.push({
            elements: nextPage.elements,
            nextURL: nextPage.nextURL
          });

          state.nextURL = nextPage.nextURL;

          if (nextPage.nextURL && state.preloadedPages.length < state.preloadLimit) {
            preloadNext();
          }
        }
      };

      // Start loading
      loadInitialPages();

      // Scroll handler
      const scrollHandler = throttle(async () => {
        if (!state.initialLoaded || state.lock ||
            (!state.preloadedPages.length && !state.nextURL) ||
            state.loadedCount >= state.maxPages) return;

        const threshold = Math.min(90, 70 + (state.loadedCount * 2));
        if (getScrollPercentage() <= threshold) return;

        state.lock = true;
        status.textContent = `Loading page ${state.loadedCount + 1}...`;

        if (state.preloadedPages.length) {
          const nextPage = state.preloadedPages.shift();
          nextPage.elements.forEach(el => container.appendChild(el));
          state.loadedCount++;

          status.textContent = !nextPage.nextURL || state.loadedCount >= state.maxPages ?
            'End' : `Loaded ${state.loadedCount} pages`;

          setTimeout(() => {
            preloadNext();
            state.lock = false;
          }, 150);
        } else if (state.nextURL) {
          try {
            const nextPage = await fetchPage(state.nextURL, selectors);
            if (nextPage.elements.length) {
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
            if (state.nextURL) preloadNext();
            state.lock = false;
          }, 150);
        } else {
          state.lock = false;
        }
      });

      // Add scroll listener
      document.addEventListener('scroll', scrollHandler);

      // Add intersection observer for better performance
      new IntersectionObserver(entries => {
        if (entries[0].isIntersecting && state.initialLoaded) scrollHandler();
      }, { rootMargin: '200px', threshold: 0 }).observe(status);
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
      isExH() { return location.host === 'exhentai.org'; }
    },
    methods: {
      save() { GM_setValue('uhp', uhpConfig); },
      getConfId(id) { return `ubp-conf-${id}`; }
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
.material-switch>input[type="checkbox"]:disabled+label::after{content:"\\f023";line-height:24px;font-size:.8em;font-family:FontAwesome;color:initial}`);

  // Add FontAwesome
  document.head.appendChild($el('link', {
    href: 'https://use.fontawesome.com/releases/v5.8.0/css/all.css',
    rel: 'stylesheet',
    integrity: 'sha384-Mmxa0mLqhmOeaE8vgOSbKacftZcsNYDjQzuCOm6D02luYSzBG8vpaOykv9lFQ51Y',
    crossOrigin: 'anonymous'
  }));
})();