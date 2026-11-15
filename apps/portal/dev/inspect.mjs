import {chromium} from '@playwright/test';
const browser=await chromium.launch();
const page=await browser.newPage({viewport:{width:1440,height:1050}});
const errors=[];page.on('pageerror',e=>errors.push(e.message));
await page.goto('http://127.0.0.1:5173/');await page.getByRole('heading',{name:/Big ideas/}).waitFor();await page.screenshot({path:'/tmp/campus-portal.png',fullPage:true});
await page.goto('http://127.0.0.1:5174/admin/');await page.getByRole('heading',{name:'Shared compute. Visible progress.'}).waitFor();await page.screenshot({path:'/tmp/campus-console.png',fullPage:true});
await page.goto('http://127.0.0.1:5174/admin/benchmarks');await page.getByRole('button',{name:'embeddings · synthetic-example'}).click();await page.screenshot({path:'/tmp/campus-benchmarks.png',fullPage:true});
await page.setViewportSize({width:390,height:844});await page.goto('http://127.0.0.1:5173/new');await page.getByRole('heading',{name:'What would you like to work on?'}).waitFor();await page.screenshot({path:'/tmp/campus-mobile.png',fullPage:true});
console.log(JSON.stringify({errors,mobileOverflow:await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth)}));await browser.close();
