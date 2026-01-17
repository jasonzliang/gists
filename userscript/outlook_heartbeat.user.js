// ==UserScript==
// @name         Outlook Session Keep-Alive
// @namespace    http://tampermonkey.net/
// @version      1.0
// @description  Keep Outlook online session active by periodic activity simulation
// @author       You
// @match        https://outlook.office.com/*
// @match        https://outlook.office365.com/*
// @match        https://outlook.live.com/*
// @icon         data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAzODQgNTEyIiBmaWxsPSIjMjhhOGVhIj48cGF0aCBkPSJNMzgxLjIgMTcyLjhDMzc3LjEgMTY0LjkgMzY4LjkgMTYwIDM2MCAxNjBoLTE1Ni42bDUwLjg0LTEyNy4xYzIuOTY5LTcuMzc1IDIuMDYyLTE1Ljc4LTIuNDA2LTIyLjM4UzIzOS4xIDAgMjMyIDBoLTE3NkM0My45NyAwIDMzLjgxIDguOTA2IDMyLjIyIDIwLjg0bC0zMiAyNDBDLS43MTc5IDI2Ny43IDEuMzc2IDI3NC42IDUuOTM4IDI3OS44QzEwLjUgMjg1IDE3LjA5IDI4OCAyNCAyODhoMTQ2LjNsLTQxLjc4IDE5NC4xYy0yLjQwNiAxMS4yMiAzLjQ2OSAyMi41NiAxNCAyNy4wOUMxNDUuNiA1MTEuNCAxNDguOCA1MTIgMTUyIDUxMmM3LjcxOSAwIDE1LjIyLTMuNzUgMTkuODEtMTAuNDRsMjA4LTMwNEMzODQuOCAxOTAuMiAzODUuNCAxODAuNyAzODEuMiAxNzIuOHoiLz48L3N2Zz4=
// @grant        none
// ==/UserScript==

(function() {
    'use strict';

    // Configuration
    let PING_INTERVAL = 30 * 60 * 1000; // 30 minutes in milliseconds
    let REFRESH_INTERVAL = 2 * 60 * 60 * 1000; // 2 hours in milliseconds
    const DEBUG = true; // Set to false to disable console logs

    let pingTimer;
    let refreshTimer;
    let lastActivity = Date.now();
    let indicator;
    let settingsMenu;
    let updateIndicatorTimer;

    function log(message) {
        if (DEBUG) {
            console.log(`[Outlook Keep-Alive] ${new Date().toLocaleTimeString()}: ${message}`);
        }
    }

    // Create indicator with settings menu
    function createIndicatorWithMenu() {
        // Create main indicator
        indicator = document.createElement('div');
        indicator.id = 'outlook-keepalive-indicator';
        indicator.style.cssText = `
            position: fixed;
            bottom: 0;
            left: 0;
            background: transparent;
            color: black;
            width: 52px;
            height: 32px;
            font-size: 16px;
            z-index: 10000;
            font-family: 'Segoe UI', Arial, sans-serif;
            cursor: pointer;
            opacity: 0.3;
            transition: opacity 0.3s ease;
            user-select: none;
            border-radius: 0 8px 0 0;
            display: flex;
            align-items: center;
            justify-content: center;
        `;
        indicator.innerHTML = '⚡';

        // Hover effects
        indicator.addEventListener('mouseenter', () => {
            indicator.style.opacity = '1';
        });

        indicator.addEventListener('mouseleave', () => {
            if (!settingsMenu || settingsMenu.style.display === 'none') {
                indicator.style.opacity = '0.3';
            }
        });

        // Create settings menu
        settingsMenu = document.createElement('div');
        settingsMenu.id = 'outlook-keepalive-menu';
        settingsMenu.style.cssText = `
            position: fixed;
            bottom: 40px;
            left: 0;
            background: white;
            border: 1px solid #ccc;
            border-radius: 8px 8px 8px 0;
            padding: 15px;
            z-index: 10001;
            font-family: 'Segoe UI', Arial, sans-serif;
            font-size: 12px;
            box-shadow: 0 4px 12px rgba(0,0,0,0.15);
            display: none;
            min-width: 200px;
            color: #333;
        `;

        settingsMenu.innerHTML = `
            <div style="font-weight: bold; margin-bottom: 10px; color: #28a8ea;">⚡ Keep-Alive Settings</div>
            <div style="margin-bottom: 8px;">
                <label>Ping Interval (minutes):</label><br>
                <input type="number" id="ping-interval-input" min="1" max="120" value="${PING_INTERVAL / 60000}" style="width: 60px; padding: 2px; margin-top: 2px;">
            </div>
            <div style="margin-bottom: 8px;">
                <label>Refresh Interval (hours):</label><br>
                <input type="number" id="refresh-interval-input" min="1" max="24" value="${REFRESH_INTERVAL / 3600000}" style="width: 60px; padding: 2px; margin-top: 2px;">
            </div>
            <div style="margin-bottom: 10px; font-size: 11px; color: #666;">
                <div id="status-display">Last activity: Just now</div>
            </div>
            <div>
                <button id="apply-settings" style="background: #28a8ea; color: white; border: none; padding: 4px 8px; border-radius: 3px; cursor: pointer; font-size: 11px;">Apply</button>
                <button id="close-settings" style="background: #666; color: white; border: none; padding: 4px 8px; border-radius: 3px; cursor: pointer; font-size: 11px; margin-left: 5px;">Close</button>
            </div>
        `;

        // Add click handler to indicator
        indicator.addEventListener('click', (e) => {
            e.stopPropagation();
            toggleSettingsMenu();
        });

        // Add handlers to menu buttons
        document.addEventListener('click', (e) => {
            if (e.target.id === 'apply-settings') {
                applySettings();
            } else if (e.target.id === 'close-settings') {
                hideSettingsMenu();
            } else if (!settingsMenu.contains(e.target) && !indicator.contains(e.target)) {
                hideSettingsMenu();
            }
        });

        document.body.appendChild(indicator);
        document.body.appendChild(settingsMenu);

        // Update indicator every minute
        updateIndicatorTimer = setInterval(updateIndicatorDisplay, 60000);
        updateIndicatorDisplay(); // Initial update
    }

    function toggleSettingsMenu() {
        if (settingsMenu.style.display === 'none' || !settingsMenu.style.display) {
            settingsMenu.style.display = 'block';
            indicator.style.opacity = '1';
            updateStatusDisplay();
        } else {
            hideSettingsMenu();
        }
    }

    function hideSettingsMenu() {
        settingsMenu.style.display = 'none';
        indicator.style.opacity = '0.3';
    }

    function updateIndicatorDisplay() {
        if (indicator) {
            // Keep the lightning bolt symbol, don't change it
            indicator.innerHTML = '⚡';
        }
    }

    function updateStatusDisplay() {
        const statusDiv = document.getElementById('status-display');
        if (statusDiv) {
            const minutes = Math.round((Date.now() - lastActivity) / 1000 / 60);
            const nextPing = Math.ceil((PING_INTERVAL - (Date.now() - lastActivity)) / 60000);
            const status = minutes === 0 ? 'Just now' : `${minutes} minutes ago`;
            const nextAction = nextPing > 0 ? ` | Next ping: ${nextPing}m` : ' | Ping due now';
            statusDiv.textContent = `Last activity: ${status}${nextAction}`;
        }
    }

    function applySettings() {
        const pingInput = document.getElementById('ping-interval-input');
        const refreshInput = document.getElementById('refresh-interval-input');

        const newPingInterval = parseInt(pingInput.value) * 60 * 1000;
        const newRefreshInterval = parseInt(refreshInput.value) * 60 * 60 * 1000;

        if (newPingInterval >= 60000 && newRefreshInterval >= 3600000) {
            // Clear old timers
            if (pingTimer) clearInterval(pingTimer);
            if (refreshTimer) clearInterval(refreshTimer);

            // Update intervals
            PING_INTERVAL = newPingInterval;
            REFRESH_INTERVAL = newRefreshInterval;

            // Set new timers
            pingTimer = setInterval(performKeepAlive, PING_INTERVAL);
            refreshTimer = setInterval(() => {
                const timeSinceLastActivity = Date.now() - lastActivity;
                if (timeSinceLastActivity > REFRESH_INTERVAL) {
                    log('User inactive for extended period, performing refresh');
                    hardRefresh();
                }
            }, REFRESH_INTERVAL);

            log(`Settings updated - Ping: ${PING_INTERVAL / 60000}min, Refresh: ${REFRESH_INTERVAL / 3600000}h`);
            hideSettingsMenu();
        } else {
            alert('Invalid values. Ping interval must be at least 1 minute, refresh interval at least 1 hour.');
        }
    }

    // Simulate user activity by triggering mouse movement
    function simulateActivity() {
        try {
            // Create and dispatch a mouse move event
            const event = new MouseEvent('mousemove', {
                view: window,
                bubbles: true,
                cancelable: true,
                clientX: Math.random() * 100,
                clientY: Math.random() * 100
            });
            document.dispatchEvent(event);

            // Also try to focus the document
            if (document.hasFocus && !document.hasFocus()) {
                window.focus();
            }

            // Trigger a small scroll if possible
            if (window.scrollY === 0) {
                window.scrollTo(0, 1);
                setTimeout(() => window.scrollTo(0, 0), 100);
            } else {
                window.scrollTo(0, window.scrollY - 1);
                setTimeout(() => window.scrollTo(0, window.scrollY + 1), 100);
            }

            lastActivity = Date.now();
            log('Activity simulated successfully');
            return true;
        } catch (error) {
            log('Error simulating activity: ' + error.message);
            return false;
        }
    }

    // Perform a soft refresh by reloading specific elements or making API calls
    function softRefresh() {
        try {
            // Try to find and click refresh button if it exists
            const refreshButtons = document.querySelectorAll('[aria-label*="Refresh"], [title*="Refresh"], .ms-Button[aria-label*="refresh" i]');
            if (refreshButtons.length > 0) {
                refreshButtons[0].click();
                log('Clicked refresh button');
                return true;
            }

            // Alternative: dispatch a custom refresh event
            const refreshEvent = new CustomEvent('outlook-keepalive-refresh', {
                bubbles: true,
                detail: { timestamp: Date.now() }
            });
            document.dispatchEvent(refreshEvent);
            log('Dispatched refresh event');
            return true;
        } catch (error) {
            log('Soft refresh failed: ' + error.message);
            return false;
        }
    }

    // Hard refresh as last resort
    function hardRefresh() {
        try {
            log('Performing hard refresh...');
            window.location.reload();
        } catch (error) {
            log('Hard refresh failed: ' + error.message);
        }
    }

    // Check if user has been inactive and perform appropriate action
    function performKeepAlive() {
        const now = Date.now();
        const timeSinceLastActivity = now - lastActivity;

        log(`Performing keep-alive check. Time since last activity: ${Math.round(timeSinceLastActivity / 1000 / 60)} minutes`);

        // If user has been inactive for more than ping interval, simulate activity
        if (timeSinceLastActivity > PING_INTERVAL) {
            if (!simulateActivity()) {
                // If activity simulation fails, try soft refresh
                if (!softRefresh()) {
                    log('Both activity simulation and soft refresh failed');
                }
            }
        }
    }

    // Track real user activity
    function trackUserActivity() {
        lastActivity = Date.now();
    }

    // Set up activity tracking
    function setupActivityTracking() {
        const events = ['mousedown', 'mousemove', 'keypress', 'scroll', 'touchstart', 'click'];
        events.forEach(event => {
            document.addEventListener(event, trackUserActivity, true);
        });
        log('Activity tracking set up for events: ' + events.join(', '));
    }

    // Initialize the keep-alive system
    function init() {
        log('Initializing Outlook Keep-Alive script...');

        // Set up activity tracking
        setupActivityTracking();

        // Set up periodic ping
        pingTimer = setInterval(performKeepAlive, PING_INTERVAL);
        log(`Ping timer set for every ${PING_INTERVAL / 1000 / 60} minutes`);

        // Set up periodic refresh (as backup)
        refreshTimer = setInterval(() => {
            const timeSinceLastActivity = Date.now() - lastActivity;
            // Only do hard refresh if user has been inactive for a long time
            if (timeSinceLastActivity > REFRESH_INTERVAL) {
                log('User inactive for extended period, performing refresh');
                hardRefresh();
            }
        }, REFRESH_INTERVAL);
        log(`Refresh timer set for every ${REFRESH_INTERVAL / 1000 / 60} minutes`);

        // Add visual indicator with settings menu
        if (DEBUG) {
            createIndicatorWithMenu();
        }

        log('Outlook Keep-Alive script initialized successfully');
    }

    // Clean up on page unload
    window.addEventListener('beforeunload', () => {
        if (pingTimer) clearInterval(pingTimer);
        if (refreshTimer) clearInterval(refreshTimer);
        if (updateIndicatorTimer) clearInterval(updateIndicatorTimer);
        log('Timers cleared on page unload');
    });

    // Wait for page to be fully loaded
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }

})();