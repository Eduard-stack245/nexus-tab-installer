const api=typeof browser!=='undefined'?browser:chrome;
const status=document.getElementById('status');
const pair=document.getElementById('pair');
const sync=document.getElementById('sync');
const grant=document.getElementById('grant');
const NEXUS_ORIGINS=['https://www.nexusmods.com/*','https://nexusmods.com/*'];
// The program tells its language when it answers; before that the language of the browser counts.
const ENGLISH={
  'Собирает ссылки модов Nexus из всех окон этого профиля, не переключая вкладки.':'Collects links to Nexus mods from all windows of this profile, without switching tabs.',
  'Проверяем подключение…':'Checking the connection…',
  'Разрешить доступ к Nexus':'Allow access to Nexus',
  'Подключить к программе':'Connect to the program',
  'Проверить подключение и обновить список':'Check the connection and refresh the list',
  'Откройте Nexus Tab Installer и подтвердите запрос доступа один раз. Никакие другие сайты не передаются.':'Open Nexus Tab Installer and confirm the access request once. No other sites are passed on.',
  'Расширению не разрешён доступ к nexusmods.com, поэтому файлы не скачаются.\nНажмите «Разрешить доступ к Nexus».':'The extension is not allowed to access nexusmods.com, so files will not download.\nPress "Allow access to Nexus".',
  '✓ Вкладки передаются в программу':'✓ Tabs are passed to the program',
  'Модов:':'Mods:',
  'Окон:':'Windows:',
  '✓ Канал загрузок работает':'✓ The download channel works',
  '⏳ Проверяем канал загрузок':'⏳ Checking the download channel',
  'Переподключить':'Reconnect',
  'Ожидается разрешение в окне Nexus Tab Installer.\nПосле подтверждения нажмите «Проверить подключение».':'Waiting for permission in the Nexus Tab Installer window.\nAfter confirming, press "Check the connection".',
  'Нет соединения. Запустите Nexus Tab Installer.':'No connection. Start Nexus Tab Installer.',
  'Нет ответа расширения':'No answer from the extension',
  'Не удалось связаться с фоновым модулем расширения.':'Could not reach the background part of the extension.',
  'Программа отвечает ошибкой HTTP':'The program answers with HTTP error',
  'На порту 38471 запущена другая программа':'Another program is running on port 38471',
  'Нет соединения с Nexus Tab Installer на 127.0.0.1:38471. Запустите программу и проверьте порт.':'No connection with Nexus Tab Installer on 127.0.0.1:38471. Start the program and check the port.',
  'Программа доступна, но запрос подключения не прошёл. Проверьте разрешения расширения и перезапустите его.':'The program is there, but the connection request failed. Check the permissions of the extension and restart it.',
  'Ожидается разрешение в Nexus Tab Installer':'Waiting for permission in Nexus Tab Installer',
  'Нет свежего ответа программы — проверьте соединение':'No fresh answer from the program: check the connection',
  'Программа не приняла ключ подключения. Разрешите браузер в окне программы.':'The program did not accept the connection key. Allow the browser in the program window.',
  'Приложение доступно, но список вкладок не принят:':'The program is there, but the list of tabs was not accepted:',
  'Ошибка расширения':'Extension error',
  'Ошибка соединения':'Connection error',
  'Канал заданий:':'Job channel:',
  'Программа не приняла ключ подключения':'The program did not accept the connection key'};
const UKRAINIAN={
  'Собирает ссылки модов Nexus из всех окон этого профиля, не переключая вкладки.':'Збирає посилання на моди Nexus з усіх вікон цього профілю, не перемикаючи вкладки.',
  'Проверяем подключение…':'Перевіряємо підключення…',
  'Разрешить доступ к Nexus':'Дозволити доступ до Nexus',
  'Подключить к программе':'Підключити до програми',
  'Проверить подключение и обновить список':'Перевірити підключення й оновити список',
  'Откройте Nexus Tab Installer и подтвердите запрос доступа один раз. Никакие другие сайты не передаются.':'Відкрийте Nexus Tab Installer і підтвердьте запит доступу один раз. Жодні інші сайти не передаються.',
  'Расширению не разрешён доступ к nexusmods.com, поэтому файлы не скачаются.\nНажмите «Разрешить доступ к Nexus».':'Розширенню не дозволено доступ до nexusmods.com, тому файли не завантажаться.\nНатисніть «Дозволити доступ до Nexus».',
  '✓ Вкладки передаются в программу':'✓ Вкладки передаються в програму',
  'Модов:':'Модів:',
  'Окон:':'Вікон:',
  '✓ Канал загрузок работает':'✓ Канал завантажень працює',
  '⏳ Проверяем канал загрузок':'⏳ Перевіряємо канал завантажень',
  'Переподключить':'Перепідключити',
  'Ожидается разрешение в окне Nexus Tab Installer.\nПосле подтверждения нажмите «Проверить подключение».':'Очікується дозвіл у вікні Nexus Tab Installer.\nПісля підтвердження натисніть «Перевірити підключення».',
  'Нет соединения. Запустите Nexus Tab Installer.':'Немає з\'єднання. Запустіть Nexus Tab Installer.',
  'Нет ответа расширения':'Немає відповіді розширення',
  'Не удалось связаться с фоновым модулем расширения.':'Не вдалося зв\'язатися з фоновим модулем розширення.',
  'Программа отвечает ошибкой HTTP':'Програма відповідає помилкою HTTP',
  'На порту 38471 запущена другая программа':'На порту 38471 запущено іншу програму',
  'Нет соединения с Nexus Tab Installer на 127.0.0.1:38471. Запустите программу и проверьте порт.':'Немає з\'єднання з Nexus Tab Installer на 127.0.0.1:38471. Запустіть програму й перевірте порт.',
  'Программа доступна, но запрос подключения не прошёл. Проверьте разрешения расширения и перезапустите его.':'Програма доступна, але запит підключення не пройшов. Перевірте дозволи розширення й перезапустіть його.',
  'Ожидается разрешение в Nexus Tab Installer':'Очікується дозвіл у Nexus Tab Installer',
  'Нет свежего ответа программы — проверьте соединение':'Немає свіжої відповіді програми, перевірте з\'єднання',
  'Программа не приняла ключ подключения. Разрешите браузер в окне программы.':'Програма не прийняла ключ підключення. Дозвольте браузер у вікні програми.',
  'Приложение доступно, но список вкладок не принят:':'Програма доступна, але список вкладок не прийнято:',
  'Ошибка расширения':'Помилка розширення',
  'Ошибка соединения':'Помилка з\'єднання',
  'Канал заданий:':'Канал завдань:',
  'Программа не приняла ключ подключения':'Програма не прийняла ключ підключення'};
const WORDS={en:ENGLISH,uk:UKRAINIAN};
const browserLanguage=(navigator.language||'').toLowerCase().slice(0,2);
let language=browserLanguage==='ru'||browserLanguage==='uk'?browserLanguage:'en';
function say(text){
  const words=WORDS[language];
  if(!words)return text;
  let result=String(text);
  for(const russian of Object.keys(words).sort((a,b)=>b.length-a.length))result=result.split(russian).join(words[russian]);
  return result;
}
function sayAll(){
  document.documentElement.lang=language;
  for(const element of document.querySelectorAll('p,small,button,#status'))element.textContent=say(element.textContent);
}
function show(data){
  grant.hidden=data.siteAccess!==false;
  if(data.siteAccess===false){
    status.textContent=say('Расширению не разрешён доступ к nexusmods.com, поэтому файлы не скачаются.\nНажмите «Разрешить доступ к Nexus».');
  }else if(data.connected){
    status.textContent=say(`✓ Вкладки передаются в программу\nМодов: ${data.count||0} · Окон: ${data.windows||0}\n` +
      (data.helperReady ? '✓ Канал загрузок работает' :
       '⏳ Проверяем канал загрузок' + (data.helperError ? ': '+data.helperError : '…')));
    pair.textContent=say('Переподключить');
  }else if(data.pending){
    status.textContent=say('Ожидается разрешение в окне Nexus Tab Installer.\nПосле подтверждения нажмите «Проверить подключение».');
  }else{
    status.textContent=say(data.reason||'Нет соединения. Запустите Nexus Tab Installer.');
  }
}
async function send(type){
  pair.disabled=true;sync.disabled=true;
  try{
    const result=await api.runtime.sendMessage({type});
    show(result||{connected:false,reason:'Нет ответа расширения'});
  }catch(_){show({connected:false,reason:'Не удалось связаться с фоновым модулем расширения.'});}
  finally{pair.disabled=false;sync.disabled=false;}
}
// What the program last said about its language wins over the language of the browser.
api.storage.local.get('appLanguage').then(saved=>{
  if(['ru','en','uk'].includes(saved.appLanguage))language=saved.appLanguage;
  sayAll();
}).catch(sayAll);
pair.addEventListener('click',()=>send('pair-browser'));
sync.addEventListener('click',()=>send('sync-tabs'));
// The browser asks the user; this must run directly from the click.
grant.addEventListener('click',async()=>{
  try{await api.permissions.request({origins:NEXUS_ORIGINS});}catch(_){}
  send('sync-tabs');
});
// Merely reading cached status used to leave a fresh extension in the
// 'not connected' state indefinitely. Opening the popup now wakes the
// worker and performs actual pairing/sync, without copying a token.
send('sync-tabs');
// Pending desktop approval may complete while the popup is open. A closed
// popup does not matter: the background alarm also retries pairing.
let connectionAttempts=0;
const connectionPoll=setInterval(async()=>{
  if(connectionAttempts++>=24){clearInterval(connectionPoll);return;}
  try {
    const result=await api.runtime.sendMessage({type:'sync-tabs'});
    show(result||{connected:false,reason:'Нет ответа расширения'});
    if(result?.connected){clearInterval(connectionPoll);}
  }catch(_){/* The desktop can start later; keep trying for a short time. */}
},2500);
