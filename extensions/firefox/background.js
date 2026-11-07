// The extension requests permission from the desktop app; no copied pairing code.
// Only Nexus Mods mod tabs are transmitted, after the user approves access.
// The same file runs in Chrome, Edge, Opera GX and Firefox.
const api = typeof browser !== 'undefined' ? browser : chrome;
const SERVER = 'http://127.0.0.1:38471';
const EXTENSION_VERSION = api.runtime.getManifest().version;
const NEXUS_ORIGINS = ['https://www.nexusmods.com/*', 'https://nexusmods.com/*'];
// Safe, bounded cross-process diagnostics. Never send page URLs, NXM links,
// cookies, API keys, or arbitrary error messages. All fields are allowlisted
// again by the desktop application before being written to disk.
const TRACE_FIELDS = new Set(['job_id','mod_id','file_id','step','state','result','stage',
  'status','http_status','attempt','window_id','tab_id','count','browser','method',
  'endpoint','reason','elapsed_ms','selected','source_id','profile_count','items','ack','age_ms','client_ts']);
const TRACE_NAME = /^[a-z][a-z0-9_.-]{0,63}$/;
let traceChain = Promise.resolve();
let traceFlushCycle = 0;
async function traceEvent(event, data={}) {
  if (!TRACE_NAME.test(event)) return;
  const filtered = {};
  for (const [key,value] of Object.entries(data)) {
    if (!TRACE_FIELDS.has(key)) continue;
    if (typeof value === 'number' && Number.isSafeInteger(value) && value >= 0) filtered[key]=value;
    else if (typeof value === 'string' && TRACE_NAME.test(value)) filtered[key]=value;
  }
  // Do not store secrets in extension logs, even in failure paths.
  filtered.client_ts=Date.now();
  const entry={ts:new Date().toISOString(),event,data:filtered};
  traceChain = traceChain.then(async()=>{
    try {
      const {authToken}=await api.storage.local.get('authToken');
      if (!authToken) throw new Error('not paired yet');
      const response=await fetch(SERVER+'/diagnostics/event', {
        method:'POST',headers:{'Content-Type':'application/json','X-Nexus-Token':authToken},
        body:JSON.stringify({event,data:filtered})
      });
      if (!response.ok) throw new Error('diagnostics unavailable');
    } catch (_) {
      // Keep only redacted event codes when desktop is temporarily offline.
      try {
        const stored=await api.storage.local.get('diagnosticRing');
        const ring=Array.isArray(stored.diagnosticRing)?stored.diagnosticRing:[];
        ring.push(entry);
        await api.storage.local.set({diagnosticRing:ring.slice(-100)});
      } catch (_) {}
    }
  }).catch(()=>{});
}
async function flushTraceRing() {
  try {
    const {authToken,diagnosticRing}=await api.storage.local.get(['authToken','diagnosticRing']);
    if (!authToken || !Array.isArray(diagnosticRing) || !diagnosticRing.length) return;
    let sent=0;
    for(const entry of diagnosticRing.slice(-100)) {
      const response=await fetch(SERVER+'/diagnostics/event', {
        method:'POST',headers:{'Content-Type':'application/json','X-Nexus-Token':authToken},
        body:JSON.stringify({event:entry.event,data:entry.data})
      });
      if(!response.ok)break;
      sent++;
    }
    if(sent)await api.storage.local.set({diagnosticRing:diagnosticRing.slice(sent)});
  }catch(_){}
}

async function checkDesktopHealth(){
  try {
    const response=await fetch(SERVER+'/health', {cache:'no-store'});
    if(!response.ok)return {ok:false,reason:'Программа отвечает ошибкой HTTP '+response.status};
    const data=await response.json();
    if(data.service!=='nexus-tab-installer')return {ok:false,reason:'На порту 38471 запущена другая программа'};
    if(data.language)await api.storage.local.set({appLanguage:data.language});
    return {ok:true};
  }catch(_){
    return {ok:false,reason:'Нет соединения с Nexus Tab Installer на 127.0.0.1:38471. Запустите программу и проверьте порт.'};
  }
}

const MOD_URL = /^https:\/\/(?:www\.)?nexusmods\.com\/(?:games\/)?[a-z0-9_-]+\/mods\/\d+(?:[/?#]|$)/i;
let timer;
let connecting = false;
function browserLabel() {
  const ua = navigator.userAgent || '';
  if (/Firefox\//.test(ua)) return 'firefox';
  if (/Edg(?:A|iOS)?\//.test(ua)) return 'edge';
  if (/OPR\//.test(ua)) return 'opera-gx';
  return 'chrome';
}
async function jsonCall(path, init={}) {
  const response = await fetch(SERVER + path, init);
  if (!response.ok) throw new Error(`Desktop app ${path}: HTTP ${response.status}`);
  return response.json();
}
async function requestPairing() {
  traceEvent('pairing.started');
  if (connecting) return false;
  connecting = true;
  try {
    const stored = await api.storage.local.get(['authToken', 'pendingPairId', 'tokenRefused']);
    if (stored.authToken && !stored.tokenRefused) return true;
    if (stored.pendingPairId) {
      const info = await jsonCall('/pair/status?request_id='+encodeURIComponent(stored.pendingPairId),
        {headers:{'X-Nexus-Pairing':'1'}});
      if (info.state === 'approved' && info.token) {
        await api.storage.local.set({authToken:info.token,tokenRefused:false,lastSyncError:''});
        traceEvent('pairing.approved');
        // Deliver diagnostics recorded before approval, now that a token exists.
        await flushTraceRing();
        await api.storage.local.remove('pendingPairId');
        return true;
      }
      if (info.state === 'pending') {
        await api.storage.local.set({lastSyncError:''});
        return false;
      }
      await api.storage.local.remove('pendingPairId');
    }
    const data = await jsonCall('/pair/request', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({browser:browserLabel()})
    });
    if (data.request_id) await api.storage.local.set({pendingPairId:data.request_id,lastSyncError:''});
    return false;
  } catch (error) {
    const health=await checkDesktopHealth();
    await api.storage.local.set({lastSyncError:health.ok
      ? 'Программа доступна, но запрос подключения не прошёл. Проверьте разрешения расширения и перезапустите его.'
      : health.reason});
    return false;
  } finally {
    connecting = false;
  }
}
async function getSourceId() {
  const stored = await api.storage.local.get('sourceId');
  if (stored.sourceId && /^[0-9a-f]{32}$/.test(stored.sourceId)) return stored.sourceId;
  const id = crypto.randomUUID().replace(/-/g, '').toLowerCase();
  await api.storage.local.set({sourceId:id});
  return id;
}
// Firefox lets the user withhold a site, and Chromium browsers can restrict it to
// "on click". Without this access the page script does not run at all.
async function hasSiteAccess(){
  try { return await api.permissions.contains({origins:NEXUS_ORIGINS}); }
  catch (_) { return true; }
}
async function connectionStatus(){
  const health=await checkDesktopHealth();
  const saved=await api.storage.local.get(['authToken','pendingPairId','lastSyncAt','lastSyncCount','lastSyncWindows','lastSyncError','lastAutoError','lastAutoPollAt']);
  const recent=Boolean(saved.lastSyncAt && Date.now()-saved.lastSyncAt < 120000);
  return {
    siteAccess:await hasSiteAccess(),
    connected:Boolean(health.ok && saved.authToken && recent && !saved.lastSyncError),
    paired:Boolean(saved.authToken),
    pending:Boolean(saved.pendingPairId),
    count:saved.lastSyncCount||0,
    windows:saved.lastSyncWindows||0,
    helperReady:Boolean(saved.lastAutoPollAt && Date.now()-saved.lastAutoPollAt < 90000 && !saved.lastAutoError),
    helperError:saved.lastAutoError||'',
    reason:(!health.ok?health.reason:saved.lastSyncError)||(!saved.authToken?'Ожидается разрешение в Nexus Tab Installer':
      !recent?'Нет свежего ответа программы — проверьте соединение':'')
  };
}
async function syncTabs(){
  let {authToken}=await api.storage.local.get('authToken');
  if(!authToken){
    if(!await requestPairing()) return {ok:false,...await connectionStatus()};
    ({authToken}=await api.storage.local.get('authToken'));
  }
  if(!authToken)return {ok:false,...await connectionStatus()};
  traceEvent('snapshot.started');
  try{
    // tabs.query({}) reads ALL normal windows in this profile, including inactive tabs.
    const all=await api.tabs.query({});
    const seen=new Set();
    // The helper's own download tab is not one of the user's mods.
    const helperTab=typeof autoState!=='undefined'&&autoState?autoState.tabId:null;
    const tabs=all.filter(t=>MOD_URL.test(t.url||'')&&t.id!==helperTab).filter(t=>{
      const m=t.url.match(/\/(?:games\/)?([a-z0-9_-]+)\/mods\/(\d+)/i);
      if(!m)return false;
      // Tabs of one mod on different files are different downloads.
      const id=`${m[1].toLowerCase()}:${m[2]}:${new URL(t.url).searchParams.get('file_id')||''}`;
      if(seen.has(id))return false;
      seen.add(id);return true;
    }).map(t=>({url:t.url,title:t.title||''}));
    const windows=new Set(all.map(t=>t.windowId).filter(v=>Number.isInteger(v))).size;
    const response=await fetch(SERVER+'/snapshot',{
      method:'POST',headers:{'Content-Type':'application/json','X-Nexus-Token':authToken},
      body:JSON.stringify({browser:browserLabel(),source_id:await getSourceId(),tabs})
    });
    if(response.status===403){
      // Another copy of the program may hold the port for a while, so the key stays
      // until a new one is approved: the real program can accept it again.
      await api.storage.local.set({tokenRefused:true,
        lastSyncError:'Программа не приняла ключ подключения. Разрешите браузер в окне программы.'});
      if(await requestPairing())return syncTabs();
      return {ok:false,...await connectionStatus()};
    }
    if(!response.ok)throw new Error(`HTTP ${response.status}`);
    traceEvent('snapshot.finished',{count:tabs.length});
    await api.storage.local.set({lastSyncAt:Date.now(),lastSyncCount:tabs.length,
      lastSyncWindows:windows,lastSyncError:'',tokenRefused:false});
    // Handshake is not complete until BOTH tabs and auto-job channel work.
    // Check the job channel now instead of waiting up to 30s for an alarm;
    // it is safe when there are no jobs, and does not click any webpage.
    try { await pollAutoFree(); } catch (_) { /* next scheduled poll retries */ }
    return {ok:true,connected:true,paired:true,count:tabs.length,windows,siteAccess:await hasSiteAccess()};
  }catch(e){
    traceEvent('snapshot.failed');
    const health=await checkDesktopHealth();
    const message=health.ok
      ? 'Приложение доступно, но список вкладок не принят: '+String(e?.message||e).slice(0,110)
      : health.reason;
    await api.storage.local.set({lastSyncError:message});
    return {ok:false,...await connectionStatus(),reason:message};
  }
}
function scheduleSync() {
  if (timer) clearTimeout(timer);
  timer = setTimeout(()=>{timer=undefined;syncTabs();},500);
}
api.tabs.onCreated.addListener(scheduleSync);
api.tabs.onUpdated.addListener(scheduleSync);
api.tabs.onRemoved.addListener(scheduleSync);
api.tabs.onActivated.addListener(scheduleSync);
api.windows.onFocusChanged.addListener(scheduleSync);
api.runtime.onStartup.addListener(scheduleSync);
api.runtime.onInstalled.addListener(scheduleSync);
api.runtime.onMessage.addListener((msg, sender, respond)=>{
  if(msg?.type==='connection-status'){
    connectionStatus().then(respond).catch(()=>respond({connected:false,reason:'Ошибка расширения'}));
    return true;
  }
  if(msg && (msg.type==='pair-browser' || msg.type==='sync-tabs')){
    syncTabs().then(respond).catch(()=>respond({ok:false,reason:'Ошибка соединения'}));
    return true;
  }
});
api.alarms.onAlarm.addListener(alarm=>{
  if(alarm.name==='nexus-sync')syncTabs();
});
api.alarms.create('nexus-sync',{periodInMinutes:0.5});
syncTabs();


// Download automation lives in auto_download.js. Chromium service workers import it
// here; the Firefox manifest loads it after this file.
if (typeof importScripts === 'function') importScripts('auto_download.js');

// Flush connection errors recorded while the desktop program was offline.
setTimeout(flushTraceRing,4000);

