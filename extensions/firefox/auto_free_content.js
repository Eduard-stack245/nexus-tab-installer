/* Nexus Tab Collector: page side of an assigned download job.
   Runs only on a tab the extension opened for a job. Presses the site's own
   "Slow download" (Free) or "Start download" (Premium) button and lets the
   site's countdown finish; the background script intercepts the download.
   Login and verification pages are left to the user; nothing is bypassed.

   Nexus renders the download panel inside web components with open shadow
   roots (<mod-file-download>, <mod-download-modal>), so every lookup here
   searches through shadow roots. */
(() => {
  if (window.__nexusTabAutoLoaded) return;
  window.__nexusTabAutoLoaded = true;
  const api = typeof browser !== 'undefined' ? browser : chrome;

  const send = message => {
    try { return Promise.resolve(api.runtime.sendMessage(message)).catch(() => null); }
    catch (_) { return Promise.resolve(null); }
  };
  // Event codes and numbers only: no page text, URLs or cookies.
  const trace = (event, data = {}) => send({type: 'auto-free-trace', event, ...data});
  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

  // ------------------------------------------------------------ DOM helpers
  function deepQueryAll(selector, root = document, out = [], depth = 0) {
    if (!root || depth > 12) return out;
    try { out.push(...root.querySelectorAll(selector)); } catch (_) { return out; }
    let all = [];
    try { all = root.querySelectorAll('*'); } catch (_) {}
    for (const el of all) if (el.shadowRoot) deepQueryAll(selector, el.shadowRoot, out, depth + 1);
    return out;
  }
  const textOf = el => ((el && (el.innerText || el.textContent)) || '').replace(/\s+/g, ' ').trim();
  const usable = el => {
    if (!el || !el.isConnected || el.disabled || el.getAttribute('aria-disabled') === 'true') return false;
    if (!el.getClientRects().length) return false;
    const style = getComputedStyle(el);
    return style.visibility !== 'hidden' && style.display !== 'none' && style.pointerEvents !== 'none';
  };
  const controls = root => deepQueryAll('button, a, [role="button"]', root);
  const findControl = (root, pattern) => controls(root).find(el => pattern.test(textOf(el)) && usable(el));
  // Text of an element or shadow root, without the <style> blocks Nexus puts there.
  const rootText = root => {
    const parts = [];
    const collect = node => {
      for (const child of node.childNodes || []) {
        if (child.nodeType === 3) parts.push(child.nodeValue);
        else if (child.nodeType === 1 && child.tagName !== 'STYLE' && child.tagName !== 'SCRIPT') {
          collect(child);
          if (child.shadowRoot) collect(child.shadowRoot);
        }
      }
    };
    if (root) collect(root);
    return parts.join(' ').replace(/\s+/g, ' ');
  };
  const press = el => {
    try { el.scrollIntoView({block: 'center'}); } catch (_) {}
    el.click();
  };

  // ------------------------------------------------------------ page model
  const modPath = job => new RegExp('^/(?:games/)?' + job.game.replace(/[^a-z0-9_-]/gi, '') +
                                    '/mods/' + Number(job.mod_id) + '(?:/|$)', 'i');
  const onModPage = job => /(^|\.)nexusmods\.com$/i.test(location.hostname) && modPath(job).test(location.pathname);
  const fileUrl = (job, fileId) =>
    `https://www.nexusmods.com/${job.game}/mods/${Number(job.mod_id)}?tab=files` +
    (fileId ? `&file_id=${Number(fileId)}` : '');
  const urlFileId = () => Number(new URL(location.href).searchParams.get('file_id')) || 0;

  function challengePresent() {
    if (/just a moment|attention required|checking your browser|verify you are human/i.test(document.title || '')) return true;
    return !!document.querySelector('#challenge-form, #cf-challenge-running, #challenge-stage, ' +
      'iframe[src*="challenges.cloudflare.com"], iframe[src*="captcha"], .g-recaptcha, .h-captcha');
  }

  function downloadHost(fileId) {
    const hosts = deepQueryAll('mod-file-download');
    return hosts.find(h => h.getAttribute('file-id') === String(fileId)) || null;
  }

  // Logged-out pages render "Slow download" as a link to users.nexusmods.com
  // and "Log in to download" buttons.
  function loginRequired(host) {
    const root = host && host.shadowRoot;
    if (root) {
      if (/you need an account to download/i.test(rootText(root))) return true;
      const slow = controls(root).find(el => /^slow download$/i.test(textOf(el)));
      if (slow && slow.tagName === 'A' && /users\.nexusmods\.com/i.test(slow.getAttribute('href') || '')) return true;
    }
    return !!controls(document).find(el => /^log in to download$/i.test(textOf(el)) && usable(el));
  }

  function loginUrl() {
    const modal = deepQueryAll('mod-download-modal[login-href]')[0];
    const href = modal && modal.getAttribute('login-href');
    if (href && /^https:\/\/users\.nexusmods\.com\//i.test(href)) return href;
    return 'https://users.nexusmods.com/auth/sign_in?redirect_url=' + encodeURIComponent(location.href);
  }

  // Error texts of the site's download panel.
  function siteError(root) {
    const text = rootText(root);
    if (/could no longer be accessed|has been (removed|deleted)|file (was )?not found/i.test(text)) return 'file_unavailable';
    if (/could not be retrieved|already downloading in another tab|restart the download|something went wrong/i.test(text)) return 'site_error';
    return '';
  }

  // The site refuses further requests. Only the download panel and bare error pages
  // are read: a mod's own text can mention anything ("reached the carry weight limit").
  // Length says nothing here: a real file page has about 2500 characters of text. The
  // site's own layout tells a page of the site from an error page.
  const LIMIT_TEXT = /you are being rate limited|too many (requests|downloads)|error 1015|reached (your|the) [a-z ]*limit/i;
  const BLOCK_TEXT = /sorry, you have been blocked|you are unable to access|error 10(06|07|08|09|20)\b|your (account|ip)[a-z ]* (has been |is )?(banned|suspended|blocked)/i;
  const onSitePage = () => !!document.querySelector('#mainContent, #pagetitle, .modpage, mod-file-download');

  function siteRefusal(panel) {
    let text = panel && panel.shadowRoot ? rootText(panel.shadowRoot) : '';
    if (!onSitePage()) text = (document.title || '') + ' ' + ((document.body && document.body.innerText) || '');
    if (BLOCK_TEXT.test(text)) return 'blocked';
    return LIMIT_TEXT.test(text) ? 'rate_limited' : '';
  }

  // Newest file of the "Main files" section (used when the app has no API key).
  function resolveMainFile() {
    const pick = selector => [...document.querySelectorAll(selector)]
      .map(h => ({file_id: Number(h.getAttribute('data-id')), date: Number(h.getAttribute('data-date')) || 0,
                  size_kb: Number(h.getAttribute('data-size')) || 0}))
      .filter(f => f.file_id > 0)
      .sort((a, b) => b.date - a.date || b.file_id - a.file_id)[0] || null;
    return pick('#file-container-main-files dt[data-id], #file-container-main-files .file-expander-header[data-id]') ||
      pick('#mod_files [id^="file-container-"]:not(#file-container-old-files):not(#file-container-archived-files) dt[data-id]');
  }
  const hasFilesList = () => !!document.querySelector('#mod_files, [id^="file-container-"]');

  // Optional "disable your adblocker" offer: close only its own close button.
  function dismissAdblockOffer() {
    const dialogs = deepQueryAll('[role="dialog"], [aria-modal="true"], .modal, [class*="modal"]');
    const offer = dialogs.find(d => usable(d) && /disable your adblocker/i.test(rootText(d)));
    if (!offer) return false;
    const close = controls(offer).find(el => usable(el) &&
      (/^(close|dismiss|×|✕|x)$/i.test(textOf(el)) ||
       /^(close|dismiss)/i.test(el.getAttribute('aria-label') || el.getAttribute('title') || '')));
    if (!close) return false;
    press(close);
    return true;
  }

  // ------------------------------------------------------------ job runner
  let running = false;
  let stopRequested = false;

  async function run(job) {
    if (running) return;
    running = true;
    stopRequested = false;
    const started = Date.now();
    let fileId = Number(job.file_id) || urlFileId() || 0;
    let clicks = 0, lastClickAt = 0, modalClicks = 0, resumeCancels = 0, adblockCloses = 0;
    let countdownSince = 0, focusAsked = false, lastStep = '', lastProbe = 0, lastSentAt = 0;
    let manualSince = 0, manualSent = false;
    const step = (name, info) => {
      if (name === lastStep && Date.now() - lastSentAt < 5000) return Promise.resolve(null);
      if (name !== lastStep) { lastStep = name; trace('stage', {step: name}); }
      lastSentAt = Date.now();
      return send({type: 'auto-free-progress', step: name, info: info || {}});
    };
    const fail = (reason, note = '') =>
      send({type: 'auto-free-result', status: 'failed', reason, note: String(note).slice(0, 120)});
    const loginGuard = () => {
      // Navigate to the sign-in page once per job; then wait for the user.
      const key = 'nexusTabLogin:' + job.id;
      if (!sessionStorage.getItem(key)) {
        sessionStorage.setItem(key, '1');
        location.href = loginUrl();
        return true;
      }
      return false;
    };
    trace('job_started');
    try {
      await step('page');
      while (!stopRequested) {
        const refusal = siteRefusal(fileId ? downloadHost(fileId) : null);
        if (refusal) {
          trace('site_refused', {reason: refusal});
          await fail(refusal);
          return;
        }
        if (challengePresent()) {
          trace('challenge_detected');
          await step('verify');
          await sleep(2000);
          continue;
        }
        if (!onModPage(job)) {
          await fail('navigation');
          return;
        }
        if (dismissAdblockOffer()) {
          trace('adblock_offer_closed');
          if (++adblockCloses > 3) { await fail('page_error', 'adblock offer keeps reopening'); return; }
          await sleep(800);
          continue;
        }
        // No file chosen yet (no API key in the app): newest main file.
        if (!fileId) {
          const main = resolveMainFile();
          if (main) {
            trace('resolved_file');
            await step('resolved', main);
            location.href = fileUrl(job, main.file_id);
            return;
          }
          if (loginRequired(null) && loginGuard()) { await step('login'); return; }
          if (!hasFilesList() && new URL(location.href).searchParams.get('tab') !== 'files') {
            trace('navigate');
            location.href = fileUrl(job, 0);
            return;
          }
          if (Date.now() - started > 25000) { trace('no_button'); await fail('no_button', 'files list'); return; }
          await step('resolve');
          await sleep(800);
          continue;
        }
        const host = downloadHost(fileId);
        if (!host) {
          if (urlFileId() !== fileId) {
            trace('navigate');
            location.href = fileUrl(job, fileId);
            return;
          }
          if (Date.now() - started > 30000) { trace('no_button'); await fail('no_button', 'download panel'); return; }
          await step('file');
          await sleep(700);
          continue;
        }
        const root = host.shadowRoot;
        if (!root) { await sleep(500); continue; } // custom element not upgraded yet
        if (loginRequired(host)) {
          trace('login_required');
          await step('login');
          if (loginGuard()) return;
          await sleep(3000);
          continue;
        }
        const error = siteError(root);
        if (error) {
          trace('site_error', {reason: error});
          await fail(error);
          return;
        }
        // A stale "resumable download" from an earlier session hides the
        // normal buttons: discard it with the site's own Cancel button.
        if (findControl(root, /^resume download$/i)) {
          const cancel = findControl(root, /^cancel$/i);
          if (cancel && resumeCancels++ < 2) {
            press(cancel);
            trace('resume_cancelled');
            await sleep(1200);
            continue;
          }
        }
        // Files over 500 MB in Chrome/Edge: choose the ordinary download.
        const standard = findControl(document, /^standard download$/i);
        if (standard && modalClicks < 3) {
          modalClicks++;
          press(standard);
          trace('modal_confirmed');
          await step('modal');
          await sleep(1000);
          continue;
        }
        const panelText = rootText(root);
        if (/your download will start in/i.test(panelText)) {
          if (!countdownSince) { countdownSince = Date.now(); trace('countdown'); }
          await step('countdown');
          const seconds = Number(host.getAttribute('timeout-seconds')) || 5;
          // Hidden windows throttle timers; a visible window lets the countdown finish.
          if (!focusAsked && Date.now() - countdownSince > (seconds + 12) * 1000) {
            focusAsked = true;
            trace('focus_requested');
            send({type: 'auto-free-focus'});
          }
          await sleep(700);
          continue;
        }
        // After the countdown the site offers its own link to the file. The download
        // normally started by itself and the background has stopped this page by now.
        const manual = findControl(root, /^start download manually$/i);
        if (manual) {
          if (!manualSince) manualSince = Date.now();
          if (!manualSent && manual.href && Date.now() - manualSince > 6000) {
            manualSent = true;
            trace('manual_link');
            send({type: 'auto-free-link', url: manual.href});
          }
          await step('link');
          await sleep(800);
          continue;
        }
        const premium = host.getAttribute('user-is-premium') === 'true';
        const button = premium
          ? (findControl(root, /^start download$/i) || findControl(root, /^(fast download|download)$/i))
          : findControl(root, /^slow download$/i);
        if (button && button.tagName === 'A' && /users\.nexusmods\.com/i.test(button.getAttribute('href') || '')) {
          await step('login');
          if (loginGuard()) return;
          await sleep(3000);
          continue;
        }
        if (button && clicks < 3 && (!lastClickAt || Date.now() - lastClickAt > 15000)) {
          await sleep(400);
          if (stopRequested) break;
          press(button);
          clicks++;
          lastClickAt = Date.now();
          trace(premium ? 'premium_start' : 'button_clicked', {attempt: clicks});
          await step('clicked', premium ? {premium: true} : undefined);
          await sleep(1200);
          continue;
        }
        if (clicks) {
          // Clicked: the site is generating the link / starting the download.
          await step('link');
          await sleep(800);
          continue;
        }
        if (Date.now() - lastProbe > 4000) {
          lastProbe = Date.now();
          const all = controls(root);
          trace('control_scan', {count: all.length, items: all.filter(el => /download/i.test(textOf(el))).length,
                                 selected: all.filter(usable).length, age_ms: Date.now() - started});
        }
        if (Date.now() - started > 30000) { trace('no_button'); await fail('no_button', 'download button'); return; }
        await step('file');
        await sleep(700);
      }
    } catch (error) {
      await fail('page_error', String(error && error.message || error));
    } finally {
      running = false;
    }
  }

  api.runtime.onMessage.addListener(message => {
    if (message?.type === 'auto-free-stop') { stopRequested = true; trace('cancel_received'); return; }
    if (message?.type === 'auto-free-step' && message.job && !running) {
      trace('command_received');
      run(message.job);
    }
  });

  // windows.create() can resolve after the page has loaded, so the first
  // "ready" may arrive before the background knows this tab: retry briefly,
  // but only when the background says this tab is the one being opened.
  let readyAttempts = 0;
  const requestAssignment = async () => {
    if (running || readyAttempts >= 30) return;
    readyAttempts++;
    const response = await send({type: 'auto-free-ready'});
    if (response?.job && !running) {
      trace('assignment_received', {attempt: readyAttempts});
      run(response.job);
      return;
    }
    if (response && !response.retry) return; // an ordinary Nexus tab: do nothing
    setTimeout(requestAssignment, 1000);
  };
  requestAssignment();
})();
