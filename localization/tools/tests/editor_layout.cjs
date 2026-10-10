/* Real Chromium geometry: the clear control stays inside the search border. */
"use strict";
const {chromium}=require("playwright");
const fs=require("node:fs"),path=require("node:path"),assert=require("node:assert/strict");
(async()=>{
 const browser=await chromium.launch({headless:true});
 try {
  const page=await browser.newPage();
  const html=fs.readFileSync(path.resolve(__dirname,"../../editor/index.html"),"utf8")
   .replace(/<script[^>]*src="\/app\.js"[^>]*><\/script>/g,"");
  for(const width of [1440,1280,768,375,280]){
   await page.setViewportSize({width,height:900});await page.setContent(html);
   await page.evaluate(()=>{document.querySelector('#workspace').hidden=false;document.querySelector('#app-loading').hidden=true;});
   await page.locator("#search-input").fill("TXT_KEY_BUILDING_POLICE_STATION_HELP");
   await page.locator("#search-clear").evaluate(button=>button.disabled=false);
   const rectangles=await page.evaluate(()=>Object.fromEntries([".search-box","#search-clear","#search-submit"].map(selector=>{
    const r=document.querySelector(selector).getBoundingClientRect();
    return [selector,{left:r.left,right:r.right,top:r.top,bottom:r.bottom,height:r.height}];
   })));
   const box=rectangles[".search-box"],clear=rectangles["#search-clear"],submit=rectangles["#search-submit"];
   assert(clear.left>=box.left&&clear.right<=box.right&&clear.top>=box.top&&clear.bottom<=box.bottom,
    `Clear control protrudes at ${width}px: ${JSON.stringify(rectangles)}`);
   assert(clear.right<submit.left,"Clear control overlaps the Search button");
   assert.equal(box.height,38);assert.equal(clear.height,27);
  }
  console.log("Search geometry passed in Chromium at 1440, 1280, 768, 375 and 280px.");
 } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exit(1);});
