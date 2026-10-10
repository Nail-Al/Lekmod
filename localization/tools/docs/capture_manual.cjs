/* Capture the actual LLE HTML/JavaScript with explicit demonstration responses.
 * No real project, game installation, private snapshot or external account is used.
 * npm install --prefix <temporary-directory> --no-save playwright@1.56.0
 * NODE_PATH=<temporary-directory>/node_modules node localization/tools/docs/capture_manual.cjs
 */
"use strict";
const {chromium}=require("playwright");
const fs=require("node:fs"),path=require("node:path"),http=require("node:http"),assert=require("node:assert/strict");
const root=path.resolve(__dirname,"../.."),editor=path.join(root,"editor"),output=path.join(root,"docs/images");
const fingerprint="a".repeat(64),empty=()=>({text:"",gender:"",plurality:"",note:"",identifier:""});
const policeEN="Reduces enemy [ICON_SPY] Spy stealing rate by 25% and the amount of [ICON_RESEARCH] Science [ICON_SPY] Spies can steal by 50%.[NEWLINE][NEWLINE]City must have a [ICON_BUILDING_CONSTABLE] Constabulary.";
const policeRU="Сокращает на 25% эффективность вражеских [ICON_SPY] Шпионов при краже технологий и на 50% количество [ICON_RESEARCH] Науки, которое они могут похитить.[NEWLINE][NEWLINE]В городе должна быть построена [ICON_BUILDING_CONSTABLE] Жандармерия.";
const samples=[
 {key:"TXT_KEY_MANUAL_FARM_HELP",category:"improvements",english:"[ICON_FOOD] Food helps your city grow.[NEWLINE]Build farms to increase food.",translation:"[ICON_FOOD] Пища помогает городу расти.[NEWLINE]Стройте фермы, чтобы увеличить её производство.",tokens:{"[ICON_FOOD]":1,"[NEWLINE]":1}},
 {key:"TXT_KEY_BUILDING_POLICE_STATION_HELP",category:"buildings",english:policeEN,translation:"",tokens:{"[ICON_SPY]":2,"[ICON_RESEARCH]":1,"[NEWLINE]":2,"[ICON_BUILDING_CONSTABLE]":1}},
 {key:"TXT_KEY_MANUAL_RESEARCH_HELP",category:"menus",english:"Research provides [ICON_RESEARCH] Science towards technologies.",translation:"Исследования приносят [ICON_RESEARCH] Науку для открытия технологий.",tokens:{"[ICON_RESEARCH]":1}},
 {key:"TXT_KEY_MANUAL_TRADE_HELP",category:"menus",english:"Trade with {1_CityName} provides [ICON_GOLD] Gold.",translation:"",tokens:{"{1_CityName}":1,"[ICON_GOLD]":1}},
 {key:"TXT_KEY_MANUAL_GROWTH_HELP",category:"menus",english:"Your city has grown. Assign the new citizen to a tile.",translation:"",tokens:{}},
];
const preferences={mode:"translator",onboarded:true,locale:"RU_RU",category:"all",prefill:true,wrap:true,panel_expanded:true,page_size:"100",
 visible_columns:[],translator_visible_columns:["key","lekmod_en_US","translation","translation_status","synced_to"],
 developer_visible_columns:["key","kind","text","entity_status","source_file","source_line","synced_to"],
 translator_column_order:[],developer_column_order:[],translator_filters:{},developer_filters:{},
 column_widths:{key:235,lekmod_en_US:365,translation:335,translation_status:120,synced_to:190,kind:115,text:350,entity_status:100,source_file:245,source_line:85},
 project_path:"C:\\Projects\\Lekmod",game_path:"C:\\Games\\Steam\\steamapps\\common\\Sid Meier's Civilization V",snapshot_url:"https://example.invalid/team-vanilla.enc",
 apply_target:"",translator_sync_column:"auto",developer_sync_column:"auto",developer_status_column:"auto"};
let revision=0,issues=[],saves=[],pending={},applied=false;
function status(){return {draft_count:Object.keys(pending).length,draft_undo_available:revision>0,draft_redo_available:false,
 saved_version_count:saves.length,checkpoint_saved_at:saves[0]?.saved_at||"",checkpoint_dirty:!!Object.keys(pending).length,apply_issue_count:issues.length};}
function game(){return {path:preferences.game_path,state:"installed",selected_mod:"LEKMOD_v35.4",mods:[{name:"LEKMOD_v35.4",version:"v35.4.003",release:"v35.4"}],error:""};}
function rows(developer=false){return samples.map((sample,index)=>{
 const draft=pending[sample.key],edit=draft?.edit||{...empty(),text:sample.translation};
 return {key:sample.key,index,category:sample.category,kind:"Row",text:sample.english,characters:sample.english.length,entity_status:"applied",
 source_file:"localization/en_US/primary.xml",source_line:10+index*4,english_edited_at:"2026-10-09T12:00:00+02:00",changed_in:["v35.4"],classification:"lekmod_new",
 lekmod_en_US:sample.english,lekmod_en_US_characters:String(sample.english.length),lekmod_en_US_gender:"",lekmod_en_US_plurality:"",vanilla_en_US:"",vanilla_target:"",lekmod_target:"",
 translation:edit.text,translation_gender:edit.gender,translation_plurality:edit.plurality,translator_note:edit.note,
 translation_status:draft?"draft":sample.translation?"applied":"missing",translation_characters:String(edit.text.length),translation_updated_at:sample.translation?"2026-10-10T18:30:00+02:00":"",
 source_fingerprint:fingerprint,required_format_tokens:developer?sample.tokens:JSON.stringify(sample.tokens),
 draft_slot:(developer?"P:"+index+":":"T:RU_RU:")+sample.key,draft_revision:draft?revision:0,
 draft_base:developer?{key:sample.key,kind:"Row",text:sample.english}:{approval:sample.translation?{text:sample.translation}:null,source_fingerprint:fingerprint},
 applied_edit:developer?{...empty(),text:sample.english,identifier:sample.key}:{...empty(),text:sample.translation},has_local_draft:!!draft&&!developer,
 synced_to:{project:!draft,game:!draft},game_value:{Text:sample.translation||sample.english,Gender:"",Plurality:""}};
 });}
const merge={locales:["en_US","RU_RU"],identical_count:12,choices:{},items:[
 {id:"en_US:TXT_KEY_MANUAL_GROWTH_HELP",locale:"en_US",key:"TXT_KEY_MANUAL_GROWTH_HELP",team:"Your city has grown.",incoming:samples[4].english,status:"conflict",resets:["RU_RU","DE_DE"]},
 {id:"RU_RU:TXT_KEY_MANUAL_FARM_HELP",locale:"RU_RU",key:"TXT_KEY_MANUAL_FARM_HELP",english:samples[0].english,team:samples[0].translation,incoming:"[ICON_FOOD] Пища обеспечивает рост города.[NEWLINE]Стройте фермы для увеличения её производства.",status:"conflict",incoming_note:"Terminology reviewed",resets:[]},
 {id:"RU_RU:TXT_KEY_MANUAL_TRADE_HELP",locale:"RU_RU",key:"TXT_KEY_MANUAL_TRADE_HELP",english:samples[3].english,team:"",incoming:"Торговля приносит [ICON_GOLD] Золото.",status:"stale",reason:"English source fingerprint changed",resets:[]},
]};
function metadata(){return {ready:true,project:{version:"v35.4.003"},release:"v35.4",included_source:false,game:game(),preferences:{...preferences},
 locales:{RU_RU:["buildings","improvements","menus"],DE_DE:["buildings","menus"]},vanilla_counts:{},snapshot_error:"",version_history:{available:["v35.3","v35.4"],synced:["v35.4"],upgrade_versions:[]},
 config:{build:{shipped:true},checks:{}},editor_version:"0.27",server_instance:"manual-demo",apply_state:{state:"idle"},...status()};}
async function api(url,body){const pathname=url.pathname;
 if(pathname==="/api/meta")return metadata();
 if(pathname==="/api/preferences"){Object.assign(preferences,body);return {preferences:{...preferences}};}
 if(pathname==="/api/rows"||pathname==="/api/primary"){
  const query=url.searchParams.get("q")||"",category=url.searchParams.get("category")||"all";
  const result=rows(pathname.endsWith("primary")).filter(row=>(!query||[row.key,row.lekmod_en_US,row.translation].some(value=>value.includes(query)))&&(category==="all"||row.category===category));
  return {total:result.length,rows:result};
 }
 if(pathname==="/api/draft"){revision++;pending[body.key]={...body};return {entry:{slot:body.slot,revision,payload:{...body}},...status()};}
 if(pathname==="/api/draft-checkpoint"){
  saves=[{id:3,saved_at:"2026-10-10T19:45:00+02:00",row_count:2},{id:2,saved_at:"2026-10-10T18:30:00+02:00",row_count:1},{id:1,saved_at:"2026-10-09T12:00:00+02:00",row_count:0}];return status();
 }
 if(pathname==="/api/draft-saves")return {saves,...status()};
 if(pathname==="/api/drafts")return {entries:Object.entries(pending).map(([key,payload])=>({slot:"T:RU_RU:"+key,revision,updated_at:"2026-10-10T19:45:00+02:00",payload})),...status()};
 if(pathname==="/api/apply-project"){
  issues=[{slot:"T:RU_RU:"+samples[1].key,revision,current_revision:revision,mode:"translator",locale:"RU_RU",key:samples[1].key,english_text:policeEN,draft_text:policeRU,
   reason:"Formatting tokens differ from English. Missing: [ICON_SPY] × 1. Your draft is saved.",code:"formatting",source_fingerprint:fingerprint,needs_recheck:false}];
  applied=true;return {state:"running",target:body.target,count:2,...status()};
 }
 if(pathname==="/api/apply-status")return {state:"complete",target:"all",count:2,applied_count:1,phase:"Complete",...status()};
 if(pathname==="/api/apply-issues")return {issues,...status()};
 if(pathname==="/api/primary-create-info")return {source_file:"localization/en_US/primary.xml",line:38,operation:"Row"};
 if(pathname==="/api/handoff-preview")return merge;
 if(pathname==="/api/handoff-review"){
  merge.items[0].resets=body.choices[merge.items[0].id]==="incoming"?["RU_RU","DE_DE"]:[];
  return merge;
 }
 if(pathname==="/api/handoff-choices")return {choices:body.choices};
 if(pathname==="/api/versions")return {versions:[{version:"v35.4",supported:true,date:"2026-09-01"},{version:"v35.3",supported:true,date:"2026-07-01"}],warning:""};
 if(pathname==="/api/editor-latest")return {current:"0.27",latest:"0.27",available:false};
 if(pathname==="/api/game-process")return {processes:[]};
 if(pathname==="/api/game-status")return game();
 if(pathname==="/api/download-status")return {state:"idle"};
 if(pathname==="/api/logs")return {events:[{at:"2026-10-10T19:45:00+02:00",event:"apply",result:"1 applied; 1 draft needs review"}],actions:[]};
 if(pathname==="/api/event")return {};
 throw Error("Unimplemented documentation endpoint: "+pathname);
}
async function main(){
 fs.mkdirSync(output,{recursive:true});
 const errors=[];
 const server=http.createServer(async(request,response)=>{
  try{
   const url=new URL(request.url,"http://localhost");let bytes=[];for await(const part of request)bytes.push(part);
   if(url.pathname.startsWith("/api/")){
    const raw=Buffer.concat(bytes),body=/json/.test(request.headers["content-type"]||"")&&raw.length?JSON.parse(raw):{};
    const content=JSON.stringify(await api(url,body));
    response.writeHead(200,{"Content-Type":"application/json"});response.end(content);return;
   }
   const files={"/":"index.html","/app.js":"app.js","/favicon.svg":"favicon.svg"};
   if(!files[url.pathname]){response.writeHead(404);response.end();return;}
   const content=fs.readFileSync(path.join(editor,files[url.pathname]));
   response.writeHead(200,{"Content-Type":url.pathname.endsWith(".js")?"application/javascript":url.pathname.endsWith(".svg")?"image/svg+xml":"text/html"});
   response.end(content);
  }catch(error){errors.push(error.message);response.writeHead(500,{"Content-Type":"application/json"});response.end(JSON.stringify({error:error.message}));}
 });
 await new Promise(resolve=>server.listen(0,"127.0.0.1",resolve));
 let browser;
 try{
  browser=await chromium.launch({headless:true});
  const page=await browser.newPage({viewport:{width:1440,height:1280},deviceScaleFactor:1,timezoneId:"Europe/Prague"});
  page.on("pageerror",error=>errors.push(error.message));
  const capture=async(name,selector)=>{await page.locator("#toast").waitFor({state:"hidden"});
   await page.locator(selector||"body").screenshot({path:path.join(output,name+".png"),animations:"disabled"});};
  const ready=async()=>{await page.locator("#table-loading").waitFor({state:"hidden"});await page.locator("#table tbody tr").first().waitFor();};
  await page.goto("http://127.0.0.1:"+server.address().port);await ready();
  await page.click("#settings-button");await page.locator("#source-version-status").filter({hasText:"Latest available"}).waitFor();
  await capture("00-settings-overview","#settings-dialog");
  await capture("01-settings-project",".settings-group:nth-child(1)");
  await capture("02-settings-game",".settings-group:nth-child(2)");
  await page.locator("#vanilla-details > summary").click();await capture("03-settings-reference",".settings-group:nth-child(3)");
  await capture("15-editor-updates",".editor-update-group");
  await page.click("#settings-close");
  await page.locator("#table tbody tr").first().click();await page.setViewportSize({width:1440,height:1480});
  await capture("04-translator");await page.setViewportSize({width:1440,height:1280});
  await page.click("#filters-button");await page.selectOption("#filter-status","missing");await page.check("#filter-needs");
  await capture("05-filters","#filters-dialog");await page.click("#filters-x");
  await page.click("#mode");await ready();await page.locator("#table tbody tr").first().click();await page.setViewportSize({width:1440,height:1480});
  await capture("08-developer");await page.setViewportSize({width:1440,height:1280});
  await page.click("#create-key");await page.locator("#create-key-dialog[open]").waitFor();
  await page.fill("#create-identifier","TXT_KEY_MY_NEW_DESCRIPTION");await page.fill("#create-text","A description for a new gameplay entity.");
  await capture("09-new-english-key","#create-key-dialog");await page.click("#create-cancel");
  await page.click("#mode");await ready();await page.click("#exports-button");await capture("10-import-export","#exports-dialog");
  await page.setInputFiles("#handoff-file",{name:"team-handoff.zip",mimeType:"application/zip",buffer:Buffer.from("documentation demonstration")});
  await page.click("#handoff-preview");await page.locator("#merge-view").waitFor({state:"visible"});
  await page.locator('select[aria-label="Decision for en_US TXT_KEY_MANUAL_GROWTH_HELP"]').selectOption("keep");
  await page.locator('select[aria-label="Decision for RU_RU TXT_KEY_MANUAL_FARM_HELP"]:not([disabled])').waitFor();
  await page.locator('select[aria-label="Decision for RU_RU TXT_KEY_MANUAL_FARM_HELP"]').selectOption("incoming");
  await page.locator("#merge-apply:not([disabled])").waitFor();await page.setViewportSize({width:1440,height:1040});
  await capture("11-merge");await page.click("#merge-back");await page.setViewportSize({width:1440,height:1280});
  await page.fill("#search-input","TXT_KEY_BUILDING_POLICE_STATION_HELP");await page.press("#search-input","Enter");await ready();
  await page.locator("#table tbody tr").first().click();await page.fill("#translation",policeRU);
  await page.locator("#draft-state").filter({hasText:"Saved locally"}).waitFor();
  await page.click("#save");await page.locator("#saved-versions:not([disabled])").waitFor();await page.click("#saved-versions");
  await page.locator(".saved-version").first().waitFor();await capture("06-saved-versions","#saved-versions-dialog");await page.click("#saved-versions-close");
  await page.click("#apply-toggle");await capture("07-apply-targets");await page.click("#apply-toggle");
  await page.click("#local-drafts");await page.locator(".draft-entry").first().waitFor();await capture("14-local-drafts","#drafts-dialog");await page.click("#drafts-close");
  await page.click("#apply-primary");await page.locator("#troubleshoot-button").waitFor({state:"visible"});
  await page.locator("#apply-primary:not([disabled])").waitFor();await page.click("#troubleshoot-button");
  await page.locator("#troubleshoot-table tbody tr").waitFor();await page.setViewportSize({width:1440,height:860});await capture("12-troubleshoot");
  await page.getByRole("button",{name:"Accept formatting",exact:true}).click();await capture("13-accept-formatting","#formatting-dialog");
  assert(applied,"Apply demonstration did not run");assert.deepEqual(errors,[],"Documentation capture encountered UI errors");
  console.log("Captured 16 screenshots from the actual LLE interface using labelled demonstration data.");
 }finally{if(browser)await browser.close();await new Promise(resolve=>server.close(resolve));}
}
main().catch(error=>{console.error(error);process.exitCode=1;});
