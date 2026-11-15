import {createRequire} from 'node:module';
import {fileURLToPath,pathToFileURL} from 'node:url';
import path from 'node:path';
import fs from 'node:fs/promises';
import crypto from 'node:crypto';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'../..');
const require=createRequire(pathToFileURL(path.join(root,'apps/portal/package.json')));
const {chromium,expect}=require('@playwright/test');
const base=process.env.CAMPUS_TEST_ORIGIN||'https://10.4.146.76:8443';
const runtime=process.env.CAMPUS_TEST_RUNTIME||path.join(process.env.LOCALAPPDATA,'Campus/server-96bfe6d');
const evidence=path.join(root,'.git/campus-runner/web-product/live');
await fs.mkdir(evidence,{recursive:true});
const credentialsFile=path.join(runtime,'web-test-account.json');
let credentials;
try {credentials=JSON.parse(await fs.readFile(credentialsFile,'utf8'));} catch(e) {
  if(e.code!=='ENOENT')throw e;
  credentials={name:'Проверка веб-интерфейса',email:`web-test-${Date.now()}@example.invalid`,password:crypto.randomBytes(24).toString('base64url')};
  await fs.writeFile(credentialsFile,JSON.stringify(credentials),{mode:0o600,flag:'wx'});
}
const browser=await chromium.launch({headless:true});
const context=await browser.newContext({ignoreHTTPSErrors:true,viewport:{width:1440,height:1000},reducedMotion:'reduce'});
const page=await context.newPage();
const errors=[];page.on('pageerror',e=>errors.push(e.message));
const report={origin:base,started_at:new Date().toISOString(),checks:[],tls_note:'Browser screenshots use isolated test context; HTTPS chain/hostname independently verified using the deployment public CA.'};
try {
  await page.goto(base,{waitUntil:'networkidle'});
  await expect(page.getByRole('heading',{name:'Вход в аккаунт'})).toBeVisible();
  await page.getByLabel('Электронная почта').fill(credentials.email);
  await page.getByLabel('Пароль',{exact:true}).fill(credentials.password);
  const [loginResponse]=await Promise.all([
    page.waitForResponse(r=>r.url()===base+'/api/v1/auth/login'&&r.request().method()==='POST'),
    page.getByRole('button',{name:'Войти',exact:true}).click()
  ]);
  if(loginResponse.status()===401) {
    await page.getByRole('button',{name:'Создать аккаунт',exact:true}).click();
    await page.getByLabel('Имя',{exact:true}).fill(credentials.name);
    await page.getByLabel('Электронная почта').fill(credentials.email);
    await page.getByLabel('Пароль',{exact:true}).fill(credentials.password);
    await page.getByRole('button',{name:'Зарегистрироваться',exact:true}).click();
  }
  await expect(page.getByRole('heading',{name:'Ваше рабочее пространство'})).toBeVisible({timeout:20000});
  report.checks.push('real password authentication and authenticated dashboard');
  await expect(page.getByRole('link',{name:'Управление пулом'})).toHaveCount(0);
  await page.screenshot({path:path.join(evidence,'member-overview.png'),fullPage:true});
  await page.getByRole('link',{name:'Устройства',exact:true}).click();
  await expect(page.getByRole('link',{name:'Windows',exact:true})).toBeVisible();
  const downloadWait=page.waitForEvent('download');
  await page.getByRole('link',{name:'Windows',exact:true}).click();
  const download=await downloadWait;
  await download.saveAs(path.join(evidence,'Connect.cmd'));
  const starter=await fs.readFile(path.join(evidence,'Connect.cmd'));
  if(starter.length<1000)throw new Error('invalid real starter download');
  report.starter={bytes:starter.length,sha256:crypto.createHash('sha256').update(starter).digest('hex')};
  report.checks.push('real authenticated downloads page and complete starter bytes');
  await page.screenshot({path:path.join(evidence,'member-devices.png'),fullPage:true});
  const statsResponse=await context.request.get(base+'/api/v1/me/stats');
  if(statsResponse.status()!==200)throw new Error('stats unavailable');
  report.stats=await statsResponse.json();
  const forbidden=await context.request.get(base+'/api/v1/admin/users');
  if(forbidden.status()!==403)throw new Error('member admin API not denied');
  report.checks.push('live member statistics and server-enforced admin denial');
  await page.getByRole('button',{name:'Выйти',exact:true}).click();
  await expect(page.getByRole('heading',{name:'Вход в аккаунт'})).toBeVisible();
  await page.getByLabel('Электронная почта').fill(credentials.email);
  await page.getByLabel('Пароль',{exact:true}).fill(credentials.password);
  await page.getByRole('button',{name:'Войти',exact:true}).click();
  await expect(page.getByRole('navigation',{name:'Основная навигация'})).toBeVisible();
  await page.getByRole('link',{name:'Обзор',exact:true}).click();
  await expect(page.getByRole('heading',{name:'Ваше рабочее пространство'})).toBeVisible();
  report.checks.push('logout and repeat password login with real cookie session');
  await page.setViewportSize({width:390,height:844});
  await page.screenshot({path:path.join(evidence,'member-mobile.png'),fullPage:true});
  if(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth))throw new Error('mobile horizontal overflow');
  if(errors.length)throw new Error('browser runtime errors: '+errors.join('; '));
  report.checks.push('mobile viewport, no horizontal overflow or browser runtime errors');
  report.passed=true;
} catch(e) {
  await page.screenshot({path:path.join(evidence,'failure.png'),fullPage:true}).catch(()=>{});
  report.passed=false;report.error=e.message;process.exitCode=1;
} finally {
  await fs.writeFile(path.join(evidence,'browser-report.json'),JSON.stringify(report,null,2));
  await browser.close();
  console.log(JSON.stringify({passed:report.passed,checks:report.checks,starter:report.starter,error:report.error}));
}
