/* Browser integration: real app.js and HTML with deterministic localhost responses. */
"use strict";
const {JSDOM, VirtualConsole} = require("jsdom");
const fs = require("node:fs");
const assert = require("node:assert/strict");
const path = require("node:path");
const root = path.resolve(__dirname, "../../..");
const prefs = {mode:"translator",onboarded:true,locale:"RU_RU",category:"all",prefill:true,wrap:true,
  panel_expanded:true,page_size:"100",visible_columns:[],translator_visible_columns:[],developer_visible_columns:[],
  translator_column_order:[],developer_column_order:[],column_widths:{},translator_filters:{},developer_filters:{},
  project_path:"project",game_path:"game",snapshot_url:"",apply_target:"",
  translator_sync_column:"auto",developer_sync_column:"auto",developer_status_column:"auto"};
let connected=true, gameRunning=false, revision=0, cursor=0, checkpoint={}, savedAt="", applying=null;
const requests=[], history=[], errors=[], values={};
let debug=()=>({});
const keys=Array.from({length:4},(_,i)=>"TXT_KEY_UI_"+(i+1));
const blank=()=>({text:"",gender:"",plurality:"",note:"",identifier:""});
function status(){return {draft_count:Object.values(values).filter(v=>v.text||v.note).length,
  draft_undo_available:cursor>0,draft_redo_available:cursor<history.length,
  checkpoint_saved_at:savedAt,checkpoint_dirty:JSON.stringify(values)!==JSON.stringify(checkpoint)};}
function rows(developer=false){
 return keys.map((key,i)=>{const value=values[key]||blank(), english="English "+(i+1), edited=!!(value.text||value.note);
  return {key,category:i<2?"menus":"buildings",index:i,kind:"Row",text:english,characters:english.length,entity_status:"applied",
   source_file:"localization/en_US/primary.xml",source_line:i+3,english_edited_at:"",changed_in:[],
   classification:"lekmod_new",lekmod_en_US:english,lekmod_en_US_characters:String(english.length),
   lekmod_en_US_gender:"",lekmod_en_US_plurality:"",vanilla_en_US:"",vanilla_target:"",lekmod_target:"",
   translation:value.text,translation_gender:value.gender,translation_plurality:value.plurality,translator_note:value.note,
   translation_status:edited?"draft":"missing",translation_characters:String(value.text.length),translation_updated_at:"",
   source_fingerprint:"a".repeat(64),required_format_tokens:developer?{}:"{}",
   draft_slot:(developer?"P:"+i+":":"T:RU_RU:")+key,draft_revision:revision,
   draft_base:developer?{key,kind:"Row",text:english}:{approval:null,source_fingerprint:"a".repeat(64)},
   applied_edit:developer?{...blank(),text:english,identifier:key}:blank(),has_local_draft:!developer&&edited,
   synced_to:{project:!edited,game:connected?!edited:null},game_value:connected?{Text:english,Gender:"",Plurality:""}:null};
 });
}
function metadata(){return {ready:true,project:{version:"v35.4.003"},release:"v35.4",included_source:false,
 game:connected?{path:"game",state:"installed",selected_mod:"LEKMOD_v35.4",mods:[{name:"LEKMOD_v35.4",version:"v35.4.003",release:"v35.4"}],error:""}:{path:"",state:"missing_game",mods:[],error:""},
 preferences:{...prefs},locales:{RU_RU:["menus","buildings"]},vanilla_counts:{},version_history:{upgrade_versions:[],synced:[],available:[]},
 config:{checks:{},build:{shipped:true}},editor_version:"0.25",server_instance:"ui-test",apply_state:{state:"idle"},...status()};}
async function response(url,options={}){
 const u=new URL(url,"http://localhost/"), body=options.body?JSON.parse(options.body):{};
 requests.push({path:u.pathname,body});
 let result={};
 if(u.pathname==="/api/meta")result=metadata();
 else if(u.pathname==="/api/preferences"){Object.assign(prefs,body);result={preferences:{...prefs}};}
 else if(u.pathname==="/api/rows"||u.pathname==="/api/primary"){
  const all=rows(u.pathname==="/api/primary"),q=u.searchParams.get("q")||"";
  const category=u.searchParams.get("category")||"all";
  const found=all.filter(x=>(!q||x.key.includes(q))&&(category==="all"||x.category===category));result={total:found.length,rows:found};
 } else if(u.pathname==="/api/draft"){
  const before={...(values[body.key]||blank())};values[body.key]={...body.edit};
  history.splice(cursor);history.push({key:body.key,before,after:{...body.edit}});cursor=history.length;revision++;
  result={entry:{slot:body.slot,revision,payload:{...body}},...status()};
 } else if(u.pathname==="/api/draft-checkpoint"){
  checkpoint=JSON.parse(JSON.stringify(values));savedAt="2026-10-06T18:00:00Z";result=status();
 } else if(u.pathname==="/api/draft-restore"){
  for(const key of Object.keys(values))delete values[key];Object.assign(values,JSON.parse(JSON.stringify(checkpoint)));
  history.length=0;cursor=0;revision++;result=status();
 } else if(u.pathname==="/api/draft-undo"||u.pathname==="/api/draft-redo"){
  const undo=u.pathname.endsWith("undo"),action=history[undo?cursor-1:cursor];
  assert(action,"No history action");values[action.key]={...(undo?action.before:action.after)};cursor+=undo?-1:1;revision++;
  result={...status(),slot:"T:RU_RU:"+action.key,version:{mode:"translator",locale:"RU_RU",key:action.key,edit:values[action.key]}};
 } else if(u.pathname==="/api/apply-project"){
  if(gameRunning&&["game","all"].includes(body.target))return {ok:false,json:async()=>({error:"Civilization V is running",error_code:"game_running",processes:["CivilizationV_DX11.exe"]})};
  applying=body.target;result={state:"running",target:applying,count:status().draft_count,...status()};
 } else if(u.pathname==="/api/apply-status"){result={state:"complete",target:applying,count:0,applied_count:0,phase:"Complete",...status()};}
 else if(u.pathname==="/api/versions")result={versions:[{version:"v35.4",supported:true}],warning:""};
 else if(u.pathname==="/api/editor-latest")result={current:"0.25",latest:"0.25",available:false};
 else if(u.pathname==="/api/game-process")result={processes:gameRunning?["CivilizationV_DX11.exe"]:[]};
 else if(u.pathname==="/api/game-status")result=metadata().game;
 else if(u.pathname==="/api/download-status")result={state:"idle"};
 return {ok:true,json:async()=>result};
}
async function wait(fn,label){
 for(let i=0;i<600;i++){if(fn())return;await new Promise(r=>setTimeout(r,15));}
 throw Error("Timed out: "+label+" "+JSON.stringify({cursor,history:history.length,requests:requests.slice(-8),details:debug()}));
}
(async()=>{
 const vc=new VirtualConsole();vc.on("jsdomError",e=>{if(!/Not implemented: window|navigation/.test(e.message))errors.push(e.message);});
 const dom=new JSDOM(fs.readFileSync(path.join(root,"localization/editor/index.html"),"utf8"),{
  url:"http://localhost/",runScripts:"outside-only",pretendToBeVisual:true,virtualConsole:vc});
 const w=dom.window,d=w.document,e=id=>d.getElementById(id);
 debug=()=>({undoDisabled:e("undo").disabled,workspaceHidden:e("workspace").hidden,dialogs:[...d.querySelectorAll("dialog[open]")].map(x=>x.id),state:w.editorDebug(),message:e("message").textContent});
 w.fetch=response;
 w.HTMLDialogElement.prototype.showModal=function(){this.open=true;};
 w.HTMLDialogElement.prototype.close=function(){this.open=false;};
 w.HTMLElement.prototype.setPointerCapture=function(){};
 w.eval(fs.readFileSync(path.join(root,"localization/editor/app.js"),"utf8") + "\nwindow.editorRefresh=refresh; window.editorReflectDraft=reflectDraft; window.editorDebug=()=>({historyPending,applyPending,chosen,savedDraft,current:captureDraft(),dirty:hasUnsaved(),context:selectionContext});");
 function select(i){d.querySelectorAll("tbody tr")[i].click();}
 function input(text){e("translation").value=text;e("translation").dispatchEvent(new w.Event("input",{bubbles:true}));}
 function hotkey(key){d.dispatchEvent(new w.KeyboardEvent("keydown",{key,ctrlKey:true,bubbles:true}));}
 await wait(()=>w.currentRows?.length===4&&e("table-loading").hidden,"startup");
 assert.equal(e("apply-primary").dataset.target,"all");
 assert(d.querySelector('thead th[data-field="synced_to"]'));
 assert(d.querySelector(".sync-chip").title.includes("Source project"));
 assert(!e("save").disabled,"Save records a global checkpoint even without a row selection");
 select(0);input("Первый");
 await wait(()=>values[keys[0]]?.text==="Первый"&&e("draft-state").textContent.includes("Saved locally"),"first autosave");
 e("save").click();await wait(()=>savedAt&&!e("save").disabled,"explicit Save");
 select(1);input("Второй");await wait(()=>values[keys[1]]?.text==="Второй"&&e("draft-state").textContent.includes("Saved locally"),"second autosave");
 select(2);input("Третий");await wait(()=>values[keys[2]]?.text==="Третий"&&e("draft-state").textContent.includes("Saved locally"),"third autosave");
 e("category").value="menus";e("category").dispatchEvent(new w.Event("change",{bubbles:true}));
 await wait(()=>w.currentRows?.length===2&&e("table-loading").hidden,"switch category before Undo");
 hotkey("z");hotkey("z");
 await wait(()=>cursor===1&&e("table-loading").hidden&&!e("undo").disabled,"two rapid Ctrl+Z actions");
 assert(!values[keys[1]].text&&!values[keys[2]].text);
 assert.equal(e("category").value,"all","Undo reveals a row outside the current category");
 assert.equal(e("selected").textContent,keys[1]);
 hotkey("y");await wait(()=>cursor===2&&e("table-loading").hidden,"Ctrl+Y");
 hotkey("z");await wait(()=>cursor===1&&e("table-loading").hidden,"undo before branching");
 e("search-input").value="";e("search-input").dispatchEvent(new w.Event("input",{bubbles:true}));
 await wait(()=>w.currentRows?.length===4&&e("table-loading").hidden,"return to all rows");
 select(3);input("Новая ветка");await wait(()=>values[keys[3]]?.text==="Новая ветка"&&e("draft-state").textContent.includes("Saved locally"),"branch");
 assert.equal(cursor,history.length);assert(e("redo").disabled);
 e("discard").click();assert(e("discard-description").textContent.includes("both modes"));
 const historyCalls=requests.filter(x=>x.path==="/api/draft-undo").length;
 hotkey("z");assert.equal(requests.filter(x=>x.path==="/api/draft-undo").length,historyCalls,"Dialog typing keeps native shortcuts");
 e("discard-confirm").click();await wait(()=>!e("discard-dialog").open&&e("table-loading").hidden,"restore checkpoint");
 assert.equal(values[keys[0]].text,"Первый");assert(!values[keys[3]]);
 gameRunning=true;
 e("apply-toggle").click();assert(!e("apply-menu").hidden);e("game-apply").click();
 await wait(()=>e("game-running-dialog").open,"running game modal");
 e("game-running-retry").click();await wait(()=>e("game-running-status").textContent.includes("still running"),"blocked Retry");
 assert(e("game-running-dialog").open);
 gameRunning=false;e("game-running-retry").click();await wait(()=>!e("game-running-dialog").open,"Retry after game closes");
 await wait(()=>e("apply-primary").dataset.target==="game"&&!e("apply-primary").disabled,"select game action");
 assert(requests.some(x=>x.path==="/api/apply-project"&&x.body.target==="game"));
 assert(!e("all-apply").hidden&&e("game-apply").hidden);
 connected=false;await w.editorRefresh();await wait(()=>e("table-loading").hidden,"project only");
 assert.equal(e("apply-primary").dataset.target,"project");
 assert(e("game-apply").disabled&&e("all-apply").disabled);
 assert(!d.querySelector('thead th[data-field="synced_to"]'),"Synced to defaults off with one connected destination");
 e("mode").click();await wait(()=>e("mode-name").textContent==="Developer"&&e("table-loading").hidden,"Developer");
 assert(d.querySelector('thead th[data-field="entity_status"]'),"Developer Status is visible by default");
 const renamed={key:"TXT_KEY_OLD",game_value:{Text:"English",Gender:"",Plurality:""},synced_to:{project:true,game:true}};
 w.editorReflectDraft(renamed,{...blank(),identifier:"TXT_KEY_NEW",text:"English"},
  {entry:{slot:"P:0:TXT_KEY_OLD",revision:1,payload:{mode:"developer",edit:{...blank(),identifier:"TXT_KEY_NEW",text:"English"}}}}, {mode:"developer"});
 assert.equal(renamed.synced_to.game,false,"A renamed entity cannot match the old game's key");
 assert.deepEqual(errors,[]);
 dom.window.close();
 console.log("Editor UI: autosave, explicit Save/restore, Ctrl+Z/Ctrl+Y, branching, split actions, destination defaults and Developer Status passed.");
})().catch(error=>{console.error(error);process.exit(1);});
