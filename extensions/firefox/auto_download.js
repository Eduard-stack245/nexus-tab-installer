// Browser-assisted Nexus downloads. Shared by the Chromium and Firefox builds
// (identical file in both folders). Uses from background.js: api, SERVER,
// traceEvent(), getSourceId().
//
// One job at a time: the desktop app hands out a selected file, this script
// opens its page in a dedicated browser window, the content script presses
// the site's own download button and waits for the site's countdown, and the
// download the site then starts is intercepted here:
//   transfer "app"     -> the browser download is cancelled and the signed
//                         link is handed to the desktop app, which saves it;
//   transfer "browser" -> the browser saves the file, the app gets its path.
// Login and verification pages are shown to the user; nothing is bypassed.

const AUTO_POLL_IDLE_MS = 10000;
const AUTO_POLL_ACTIVE_MS = 1500;
const AUTO_POLL_WAITING_MS = 5000;        // the app paces the files: nothing to do yet
const AUTO_WINDOW_KEEP_SECONDS = 90;      // close the helper window for longer pauses
const AUTO_HEARTBEAT_MS = 8000;
const AUTO_JOB_TIMEOUT_MS = 120000;       // page opened -> download started
const AUTO_USER_WAIT_MS = 900000;         // login / verification done by the user
const AUTO_STALLED_DOWNLOAD_MS = 300000;  // browser download without progress
const AUTO_WINDOW_IDLE_CLOSE_MS = 20000;  // close the helper window after a batch
const NEXUS_PAGE = /^https:\/\/(www\.)?nexusmods\.com\//i;
const NEXUS_LOGIN = /^https:\/\/users\.nexusmods\.com\//i;

let autoState = null;      // the leased job and its browser tab (persisted)
let autoIdleWindow = null; // helper window kept between jobs: {windowId, tabId, since}
let autoLoaded = false;
let autoTimer = null;
let autoTicking = false;
let autoLastActive = 0;
let autoOpening = null;    // job URL while windows.create() is still running
let autoPollCount = 0;
const autoStartTabs = new Set(); // the browser's start pages seen in our helper windows

async function loadAuto() {
  if (autoLoaded) return autoState;
  try {
    const saved = await api.storage.local.get(['autoState', 'autoIdleWindow']);
    autoState = saved.autoState && saved.autoState.job ? saved.autoState : null;
    autoIdleWindow = saved.autoIdleWindow && Number.isInteger(saved.autoIdleWindow.windowId)
      ? saved.autoIdleWindow : null;
  } catch (_) {
    autoState = null;
  }
  autoLoaded = true;
  return autoState;
}

async function saveAuto() {
  try {
    if (autoState) await api.storage.local.set({autoState});
    else await api.storage.local.remove('autoState');
    if (autoIdleWindow) await api.storage.local.set({autoIdleWindow});
    else await api.storage.local.remove('autoIdleWindow');
  } catch (_) {}
}

async function autoHeaders(extra = {}) {
  const {authToken} = await api.storage.local.get('authToken');
  return authToken ? {'X-Nexus-Token': authToken, ...extra} : null;
}

async function postAuto(jobId, payload) {
  const headers = await autoHeaders({'Content-Type': 'application/json'});
  if (!headers || !jobId) return false;
  try {
    const response = await fetch(SERVER + '/auto/complete', {
      method: 'POST', headers,
      body: JSON.stringify({source_id: await getSourceId(), job_id: jobId, ...payload})
    });
    return response.ok;
  } catch (_) {
    return false;
  }
}

async function sendProgress(step, info = {}) {
  const st = autoState;
  if (!st) return false;
  st.lastBeat = Date.now();
  return postAuto(st.job.id, {status: 'progress', step, info});
}

function scheduleAuto(delay) {
  if (autoTimer) clearTimeout(autoTimer);
  autoTimer = setTimeout(() => { autoTimer = null; autoTick(); }, delay);
}

// Name kept for background.js: it polls after each tab synchronisation.
function pollAutoFree() { return autoTick(); }

async function autoTick() {
  if (autoTicking) return;
  autoTicking = true;
  let next = AUTO_POLL_IDLE_MS;
  try {
    await loadAuto();
    // An approval may be waiting in the program: take it now, not at the next alarm.
    const {pendingPairId} = await api.storage.local.get('pendingPairId');
    if (pendingPairId) await syncTabs().catch(() => {});
    const headers = await autoHeaders({'X-Nexus-Source': await getSourceId(), 'X-Nexus-Extension': EXTENSION_VERSION});
    if (!headers) return;
    if (autoState) {
      next = await superviseJob(headers);
      return;
    }
    const response = await fetch(SERVER + '/auto/next', {headers, cache: 'no-store'});
    if (response.status === 403) {
      // The key stays: see syncTabs(), which also asks this program for access.
      await api.storage.local.set({tokenRefused: true, lastAutoError: 'Программа не приняла ключ подключения'});
      return;
    }
    if (!response.ok) throw new Error('HTTP ' + response.status);
    const payload = await response.json();
    await api.storage.local.set({lastAutoError: '', lastAutoPollAt: Date.now(), tokenRefused: false});
    if (autoPollCount++ % 30 === 0) traceEvent('helper.poll_ok');
    if (autoPollCount % 15 === 0) flushTraceRing().catch(() => {});
    // The desktop asked for a FRESH tab snapshot (a new scan).
    if (typeof payload.scan_marker === 'string' && payload.scan_marker && !payload.scan_marker.endsWith(':0')) {
      const stored = await api.storage.local.get('lastScanMarker');
      if (stored.lastScanMarker !== payload.scan_marker) {
        await api.storage.local.set({lastScanMarker: payload.scan_marker});
        syncTabs().catch(() => {});
      }
    }
    if (payload.job) {
      autoLastActive = Date.now();
      await startJob(payload.job);
      next = 2000;
    } else if (payload.active) {
      autoLastActive = Date.now();
      const wait = Number(payload.wait) || 0;
      next = wait > 5 ? AUTO_POLL_WAITING_MS : AUTO_POLL_ACTIVE_MS;
      if (wait > AUTO_WINDOW_KEEP_SECONDS) await closeIdleWindow(true);
    } else {
      await closeIdleWindow(false);
    }
  } catch (error) {
    traceEvent('helper.poll_failed', {reason: 'network_or_server'});
    await api.storage.local.set({lastAutoError: 'Канал заданий: ' + String(error?.message || error).slice(0, 100)}).catch(() => {});
  } finally {
    autoTicking = false;
    scheduleAuto(next);
  }
}

// ---------------------------------------------------------------- windows
async function createHelperWindow(url) {
  const options = {url, type: 'normal', focused: false, width: 1180, height: 860};
  try {
    return await api.windows.create(options);
  } catch (_) {
    // Some Firefox versions reject focused:false.
    return api.windows.create({url, type: 'normal'});
  }
}

async function openJobTab(url) {
  if (autoIdleWindow) {
    try {
      await api.windows.get(autoIdleWindow.windowId);
      const tab = await api.tabs.create({windowId: autoIdleWindow.windowId, url, active: true});
      const placeholder = autoIdleWindow.tabId;
      const windowId = autoIdleWindow.windowId;
      autoIdleWindow = null;
      if (Number.isInteger(placeholder)) await settle(api.tabs.remove(placeholder));
      traceEvent('window.reused');
      return {windowId, tabId: tab.id};
    } catch (_) {
      autoIdleWindow = null;
    }
  }
  const win = await createHelperWindow(url);
  traceEvent('window.created', {window_id: win.id});
  const tabs = await api.tabs.query({windowId: win.id});
  // Opera GX puts its own start page into every new window, first and active,
  // and sends the requested page behind it: find the tab by its address.
  // A single tab that is still blank is ours: Firefox shows its address only after loading starts.
  const address = t => t.pendingUrl || t.url || '';
  const tab = tabs.find(t => sameJobPage(address(t), url)) ||
              (tabs.length === 1 && /^(about:blank)?$/.test(address(tabs[0])) ? tabs[0] : null) ||
              await api.tabs.create({windowId: win.id, url, active: true});
  if (!Number.isInteger(tab.id)) throw new Error('No download tab');
  await closeStartPages(win.id, tab.id);
  return {windowId: win.id, tabId: tab.id, newWindow: true};
}

// Opera GX may never answer a request to close its start page: nothing may wait for that forever.
const settle = (promise, ms = 1500) =>
  Promise.race([Promise.resolve(promise).catch(() => {}), new Promise(resolve => setTimeout(resolve, ms))]);

// The helper window has just been created, so anything else in it is the browser's
// own start page: it would keep the window open and hide the page of the file.
async function closeStartPages(windowId, keepTabId) {
  await settle(api.tabs.update(keepTabId, {active: true}));
  const tabs = await api.tabs.query({windowId}).catch(() => []);
  for (const tab of tabs) {
    if (tab.id !== keepTabId) {
      autoStartTabs.add(tab.id);
      await settle(api.tabs.remove(tab.id));
    }
  }
}

async function focusAutoWindow() {
  const st = autoState;
  if (!st) return;
  try {
    await api.windows.update(st.windowId, {focused: true, state: 'normal', drawAttention: true});
  } catch (_) {
    try { await api.windows.update(st.windowId, {focused: true}); } catch (__) {}
  }
}

// Park the helper window on a blank tab so the next job does not open a new window.
async function releaseJobTab(st) {
  if (!st) return;
  try {
    await api.windows.get(st.windowId);
    const blank = await api.tabs.create({windowId: st.windowId, url: 'about:blank', active: true});
    autoIdleWindow = {windowId: st.windowId, tabId: blank.id, since: Date.now()};
  } catch (_) {
    autoIdleWindow = null;
  }
  if (Number.isInteger(st.tabId)) await settle(api.tabs.remove(st.tabId));
}

async function closeIdleWindow(force) {
  if (!autoIdleWindow) return;
  const now = Date.now();
  if (!force && (now - autoIdleWindow.since < AUTO_WINDOW_IDLE_CLOSE_MS ||
                 now - autoLastActive < AUTO_WINDOW_IDLE_CLOSE_MS)) return;
  const {tabId, windowId} = autoIdleWindow;
  autoIdleWindow = null;
  await saveAuto();
  // Closing only our placeholder tab: the window disappears if nothing else is in it.
  if (Number.isInteger(tabId)) await settle(api.tabs.remove(tabId));
  // A start page that would not close must not keep our empty window open.
  const rest = await api.tabs.query({windowId}).catch(() => []);
  if (rest.length && rest.every(tab => autoStartTabs.has(tab.id))) await settle(api.windows.remove(windowId));
  traceEvent('window.closed');
}

// ---------------------------------------------------------------- jobs
function jobForPage(st) {
  const job = {...st.job};
  if (st.resolved?.file_id) job.file_id = st.resolved.file_id;
  return job;
}

async function startJob(job) {
  traceEvent('job.received', {job_id: job.id, mod_id: job.mod_id, file_id: job.file_id});
  autoOpening = job.url;
  let placement;
  try {
    placement = await openJobTab(job.url);
  } finally {
    autoOpening = null;
  }
  autoState = {
    job, windowId: placement.windowId, tabId: placement.tabId,
    openedAt: Date.now(), lastBeat: 0, waitingUser: null, userSince: 0, newWindow: placement.newWindow,
    clickedAt: 0, downloadId: null, handled: false, pageAck: false,
    resolved: null, lastBytes: 0, lastBytesAt: Date.now()
  };
  await saveAuto();
  traceEvent('tab.created', {job_id: job.id, tab_id: placement.tabId});
  await sendProgress('opening');
}

async function endJob() {
  const st = autoState;
  autoState = null;
  await releaseJobTab(st);
  await saveAuto();
  scheduleAuto(800);
}

async function failJob(reason, note = '') {
  const st = autoState;
  if (!st) return;
  traceEvent('job.failed', {job_id: st.job.id, reason});
  if (st.downloadId != null) {
    await api.downloads.cancel(st.downloadId).catch(() => {});
  }
  await postAuto(st.job.id, {status: 'failed', reason, note: String(note || '').slice(0, 120),
                             file_id: st.resolved?.file_id || st.job.file_id});
  await endJob();
}

async function tellAutoTab() {
  const st = autoState;
  if (!st) return;
  try {
    await api.tabs.sendMessage(st.tabId, {type: 'auto-free-step', job: jobForPage(st)});
    traceEvent('content.command_delivered', {job_id: st.job.id, tab_id: st.tabId});
  } catch (_) {
    traceEvent('content.command_failed', {job_id: st.job.id, tab_id: st.tabId, reason: 'content_script_unavailable'});
    // The page script cannot run without site access: every next file would fail too.
    if (!await hasSiteAccess()) await failJob('no_site_access');
  }
}

async function superviseJob(headers) {
  const st = autoState;
  let serverState = 'leased';
  try {
    const response = await fetch(SERVER + '/auto/status?job_id=' + encodeURIComponent(st.job.id),
                                 {headers, cache: 'no-store'});
    if (response.ok) serverState = (await response.json()).state;
    await api.storage.local.set({lastAutoPollAt: Date.now(), lastAutoError: ''});
  } catch (_) {
    return 3000; // desktop app briefly unavailable: keep the page and retry
  }
  if (serverState !== 'leased') {
    // Cancelled, retried elsewhere, confirmed by the NXM handler / Downloads
    // watcher, or unknown after an app restart: stop this page.
    if (serverState === 'cancelled') traceEvent('job.cancelled', {job_id: st.job.id});
    await api.tabs.sendMessage(st.tabId, {type: 'auto-free-stop'}).catch(() => {});
    if (st.downloadId != null && serverState !== 'confirmed') {
      await api.downloads.cancel(st.downloadId).catch(() => {});
    }
    await endJob();
    return 1000;
  }
  const tab = await api.tabs.get(st.tabId).catch(() => null);
  if (!tab) {
    if (!st.handled) await failJob('tab_closed');
    return 1000;
  }
  // A start page can appear a moment after the window: check once more in the first seconds.
  if (st.newWindow && Date.now() - st.openedAt > 1500) {
    st.newWindow = false;
    await saveAuto();
    await closeStartPages(st.windowId, st.tabId);
  }
  const url = tab.url || tab.pendingUrl || '';
  if (NEXUS_LOGIN.test(url) && st.waitingUser !== 'login') {
    st.waitingUser = 'login';
    st.userSince = st.userSince || Date.now();
    await saveAuto();
    traceEvent('page.login_required', {job_id: st.job.id});
    await focusAutoWindow();
    await sendProgress('login');
  }
  if (st.downloadId != null) return superviseBrowserDownload();
  const now = Date.now();
  if (st.waitingUser) {
    if (now - st.userSince > AUTO_USER_WAIT_MS) {
      await failJob(st.waitingUser === 'login' ? 'login_required' : 'verification');
      return 1000;
    }
  } else if (now - st.openedAt > AUTO_JOB_TIMEOUT_MS) {
    traceEvent('job.timeout', {job_id: st.job.id});
    await failJob('timeout');
    return 1000;
  }
  if (now - (st.lastBeat || 0) > AUTO_HEARTBEAT_MS) {
    await sendProgress(st.waitingUser ? (st.waitingUser === 'login' ? 'login' : 'verify') : 'heartbeat');
  }
  // A missed handshake: deliver the command again once the page has loaded.
  if (!st.pageAck && now - st.openedAt > 6000 && tab.status === 'complete' && NEXUS_PAGE.test(url)) {
    await tellAutoTab();
  }
  return 2000;
}

async function superviseBrowserDownload() {
  const st = autoState;
  const [item] = await api.downloads.search({id: st.downloadId}).catch(() => []);
  if (!item) {
    await failJob('transfer_failed', 'download removed');
    return 1000;
  }
  if (item.state === 'complete') {
    await finishBrowserDownload(item);
    return 800;
  }
  if (item.state === 'interrupted') {
    traceEvent('download.browser_failed', {job_id: st.job.id});
    await failJob('transfer_failed', String(item.error || '').slice(0, 60));
    return 1000;
  }
  const now = Date.now();
  const received = Number(item.bytesReceived) || 0;
  if (received !== st.lastBytes) {
    st.lastBytes = received;
    st.lastBytesAt = now;
    await saveAuto();
  } else if (now - st.lastBytesAt > AUTO_STALLED_DOWNLOAD_MS) {
    await failJob('transfer_failed', 'no progress');
    return 1000;
  }
  // "Always ask where to save" is on: the browser waits for an answer in its dialog.
  if (!received && !st.saveDialogShown && now - st.lastBytesAt > 15000) {
    st.saveDialogShown = true;
    await saveAuto();
    await focusAutoWindow();
  }
  await sendProgress('download', {bytes: received, total: Math.max(0, Number(item.totalBytes) || 0)});
  return 2000;
}

async function finishBrowserDownload(item) {
  const st = autoState;
  if (!st || st.finishing) return;
  st.finishing = true;
  traceEvent('download.browser_complete', {job_id: st.job.id});
  await postAuto(st.job.id, {
    status: 'file', path: item.filename || '',
    file_id: st.resolved?.file_id || st.job.file_id,
    filename: String(item.filename || '').split(/[\\/]/).pop()
  });
  await endJob();
}

// ---------------------------------------------------------------- downloads
function urlHost(url) {
  try { return new URL(url).hostname.toLowerCase(); } catch (_) { return ''; }
}

// The program saves this file itself. A small file can finish before the cancel
// arrives, and erase() alone only forgets it: the copy would stay in Downloads.
async function discardBrowserCopy(id) {
  await api.downloads.cancel(id).catch(() => {});
  await api.downloads.removeFile(id).catch(() => {});
  await api.downloads.erase({id}).catch(() => {});
}

// Why this browser download is not the file the leased job asked the site for
// ('' = it is). CDN directories are hex bytes of the file's UUID (.../d7/97/69/...),
// so digits in a path mean nothing; file names do carry the mod ID.
function rejectDownload(item, url, st) {
  if (!/^https:\/\//i.test(url)) return 'not_https';
  if ((item.mime || '').toLowerCase().startsWith('text/html')) return 'html';
  const referrer = item.referrer || '';
  if (referrer && !/(^|\.)nexusmods\.com$/.test(urlHost(referrer))) return 'referrer';
  let name = '';
  try { name = decodeURIComponent(new URL(url).pathname.split('/').pop()); } catch (_) {}
  if (name.split(/\D+/).includes(String(st.job.mod_id))) return '';
  // Otherwise trust it only right after our click on the selected file page.
  return st.clickedAt > 0 && Date.now() - st.clickedAt < 180000 ? '' : 'no_click';
}

async function onAutoDownloadCreated(item) {
  await loadAuto();
  const st = autoState;
  if (!st || st.handled || st.downloadId != null) return;
  const url = item.finalUrl || item.url || '';
  const rejected = rejectDownload(item, url, st);
  if (rejected) {
    // Remembered in case the site's own link later proves it is the job's file.
    st.ignored = [...(st.ignored || []), {id: item.id, url: item.url || '', finalUrl: item.finalUrl || ''}];
    await saveAuto();
    traceEvent('download.ignored', {job_id: st.job.id, reason: rejected});
    return;
  }
  st.handled = true;
  traceEvent('download.intercepted', {job_id: st.job.id});
  const fileId = st.resolved?.file_id || st.job.file_id;
  if (st.job.transfer === 'browser') {
    st.downloadId = item.id;
    st.lastBytesAt = Date.now();
    await saveAuto();
    traceEvent('download.browser_started', {job_id: st.job.id});
    await api.tabs.sendMessage(st.tabId, {type: 'auto-free-stop'}).catch(() => {});
    await sendProgress('download', {bytes: 0, total: Math.max(0, Number(item.totalBytes) || 0)});
    scheduleAuto(1500);
    return;
  }
  await saveAuto();
  // The desktop app downloads the signed link itself (resume, retries, destination).
  await discardBrowserCopy(item.id);
  const accepted = await postAuto(st.job.id, {
    status: 'url', url, file_id: fileId,
    filename: String(item.filename || '').split(/[\\/]/).pop()
  });
  traceEvent('download.handoff', {job_id: st.job.id, result: accepted ? 'accepted' : 'rejected'});
  await api.tabs.sendMessage(st.tabId, {type: 'auto-free-stop'}).catch(() => {});
  await endJob();
}

async function onAutoDownloadChanged(delta) {
  await loadAuto();
  const st = autoState;
  if (!st || st.downloadId !== delta.id || !delta.state) return;
  if (delta.state.current === 'complete') {
    const [item] = await api.downloads.search({id: delta.id}).catch(() => []);
    if (item) await finishBrowserDownload(item);
  } else if (delta.state.current === 'interrupted') {
    traceEvent('download.browser_failed', {job_id: st.job.id});
    await failJob('transfer_failed');
  }
}

if (api.downloads) {
  api.downloads.onCreated.addListener(item => { onAutoDownloadCreated(item).catch(() => {}); });
  api.downloads.onChanged.addListener(delta => { onAutoDownloadChanged(delta).catch(() => {}); });
}

// ---------------------------------------------------------------- page messages
function sameJobPage(tabUrl, jobUrl) {
  try {
    const a = new URL(tabUrl), b = new URL(jobUrl);
    return a.origin === b.origin && a.pathname.toLowerCase() === b.pathname.toLowerCase();
  } catch (_) {
    return false;
  }
}

async function handlePageMessage(msg, sender) {
  await loadAuto();
  const st = autoState;
  const tabId = sender.tab?.id;
  if (msg.type === 'auto-free-ready') {
    if (st && tabId === st.tabId) {
      st.pageAck = true;
      await saveAuto();
      traceEvent('content.ready_received', {job_id: st.job.id, tab_id: tabId});
      return {job: jobForPage(st)};
    }
    // windows.create() may still be running when the page asks: retry briefly.
    const retry = !!(autoOpening && sameJobPage(sender.tab?.url || '', autoOpening));
    return {job: null, retry};
  }
  if (!st || tabId !== st.tabId) return {ok: false};
  if (msg.type === 'auto-free-trace') {
    traceEvent('page.' + String(msg.event || 'invalid'), {
      job_id: st.job.id, tab_id: tabId, attempt: msg.attempt, step: msg.step,
      reason: msg.reason, count: msg.count, items: msg.items, selected: msg.selected, age_ms: msg.age_ms
    });
    return {ok: true};
  }
  if (msg.type === 'auto-free-focus') {
    await focusAutoWindow();
    return {ok: true};
  }
  // The site's "Start download manually" link: the download was not seen in time.
  if (msg.type === 'auto-free-link') {
    const url = String(msg.url || '');
    if (st.handled || st.downloadId != null || !/^https:\/\//i.test(url)) return {ok: false};
    const same = (st.ignored || []).filter(d => d.url === url || d.finalUrl === url);
    if (st.job.transfer === 'browser') {
      if (!same.length) {
        await api.downloads.download({url, saveAs: false});
        return {ok: true};
      }
      st.handled = true;
      st.downloadId = same[0].id;
      st.lastBytesAt = Date.now();
      await saveAuto();
      traceEvent('download.browser_started', {job_id: st.job.id});
      await api.tabs.sendMessage(st.tabId, {type: 'auto-free-stop'}).catch(() => {});
      scheduleAuto(1500);
      return {ok: true};
    }
    st.handled = true;
    await saveAuto();
    // A copy the browser is already saving must not run next to the app's download.
    for (const download of same) await discardBrowserCopy(download.id);
    // Firefox reports a download only after its "save as" dialog is answered, and no
    // extension can close that dialog: one more would open for every next file.
    const warning = browserLabel() === 'firefox' && !same.length ? 'ask_where_to_save' : '';
    const accepted = await postAuto(st.job.id, {status: 'url', url, warning, file_id: st.resolved?.file_id || st.job.file_id});
    traceEvent('download.handoff', {job_id: st.job.id, result: accepted ? 'accepted' : 'rejected'});
    await api.tabs.sendMessage(st.tabId, {type: 'auto-free-stop'}).catch(() => {});
    await endJob();
    return {ok: accepted};
  }
  if (msg.type === 'auto-free-progress') {
    const step = String(msg.step || '');
    const info = msg.info && typeof msg.info === 'object' ? msg.info : {};
    if (step === 'clicked') st.clickedAt = Date.now();
    if (step === 'resolved' && Number(info.file_id) > 0) {
      st.resolved = {file_id: Number(info.file_id), size_kb: Number(info.size_kb) || 0};
    }
    if (step === 'login' || step === 'verify') {
      if (!st.waitingUser) {
        st.waitingUser = step;
        st.userSince = Date.now();
        await focusAutoWindow();
      }
    } else if (st.waitingUser && step !== 'heartbeat') {
      // The user finished signing in / verification: give the page full time again.
      st.waitingUser = null;
      st.userSince = 0;
      st.openedAt = Date.now();
    }
    await saveAuto();
    const forwarded = {};
    if (step === 'resolved') {
      forwarded.file_id = Number(info.file_id) || 0;
      forwarded.size_kb = Number(info.size_kb) || 0;
    }
    if (info.premium === true) forwarded.premium = true;
    return {ok: await sendProgress(step, forwarded)};
  }
  if (msg.type === 'auto-free-result' && msg.status === 'failed') {
    await failJob(String(msg.reason || 'page_error'), msg.note);
    return {ok: true};
  }
  return {ok: false};
}

api.runtime.onMessage.addListener((msg, sender, respond) => {
  if (!msg || typeof msg.type !== 'string' || !msg.type.startsWith('auto-free-')) return;
  handlePageMessage(msg, sender).then(respond, () => respond({ok: false}));
  return true;
});

api.tabs.onUpdated.addListener((tabId, change) => {
  if (!autoState || tabId !== autoState.tabId) return;
  if (change.status === 'complete') tellAutoTab();
});

api.tabs.onRemoved.addListener(tabId => {
  if (autoIdleWindow && tabId === autoIdleWindow.tabId) {
    autoIdleWindow = null;
    saveAuto();
  }
  if (autoState && tabId === autoState.tabId && !autoState.handled) {
    failJob('tab_closed');
  }
});

// The alarm wakes a suspended service worker / event page every 30 s.
api.alarms.onAlarm.addListener(alarm => { if (alarm.name === 'nexus-auto-free') autoTick(); });
api.alarms.create('nexus-auto-free', {periodInMinutes: 0.5});

// Resume a job that was running before the service worker was restarted.
(async () => {
  await loadAuto();
  if (autoState) await tellAutoTab();
  scheduleAuto(2000);
})();
