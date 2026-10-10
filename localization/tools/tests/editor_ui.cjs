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
let connected=true, gameRunning=false, revision=0, cursor=0, checkpoint={}, savedAt="", applying=null, simulatePartial=false;
let applyIssues=[];
const mergeFixture={handoff_id:"ui-merge",locales:["en_US","RU_RU"],identical_count:0,items:[
 {id:"en_US:TXT_KEY_UI_1",locale:"en_US",key:"TXT_KEY_UI_1",status:"conflict",choice:"review",team:"Team English",incoming:"Incoming English",resets:[]},
 {id:"RU_RU:TXT_KEY_UI_1",locale:"RU_RU",key:"TXT_KEY_UI_1",status:"conflict",choice:"review",team:"Team translation",incoming:"Incoming translation",resets:[]},
 {id:"RU_RU:TXT_KEY_UI_2",locale:"RU_RU",key:"TXT_KEY_UI_2",status:"new",choice:"incoming",team:"",incoming:"New translation",resets:[]}]};
const requests=[], history=[], saves=[], errors=[], values={};
let debug=()=>({});
const keys=Array.from({length:4},(_,i)=>"TXT_KEY_UI_"+(i+1));
const blank=()=>({text:"",gender:"",plurality:"",note:"",identifier:""});
function status(){return {draft_count:Object.values(values).filter(v=>v.text||v.note).length,
  draft_undo_available:cursor>0,draft_redo_available:cursor<history.length,
  apply_issue_count:applyIssues.length,saved_version_count:saves.length,checkpoint_saved_at:savedAt,checkpoint_dirty:JSON.stringify(values)!==JSON.stringify(checkpoint)};}
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
 config:{checks:{},build:{shipped:true}},editor_version:"0.26",server_instance:"ui-test",apply_state:{state:"idle"},...status()};}
async function response(url,options={}){
 const u=new URL(url,"http://localhost/"), body=options.body?JSON.parse(options.body):{};
 requests.push({path:u.pathname,body,query:u.searchParams.get("q")});
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
  checkpoint=JSON.parse(JSON.stringify(values));savedAt="2026-10-07T04:00:"+String(saves.length).padStart(2,"0")+"Z";
  saves.unshift({id:saves.length+1,saved_at:savedAt,row_count:Object.keys(values).length,values:JSON.parse(JSON.stringify(values))});result=status();
 } else if(u.pathname==="/api/draft-saves"){
  result={saves:saves.map(({values,...meta})=>meta),...status()};
 } else if(u.pathname==="/api/apply-issues"){
  result={issues:applyIssues.map(issue=>({...issue,current_revision:revision,draft_text:values[issue.key]?.text||"",needs_recheck:issue.revision!==revision})),...status()};
 } else if(u.pathname==="/api/draft-accept-formatting"){
  const issue=applyIssues.find(item=>item.slot===body.slot);assert(issue);
  assert.equal(body.revision,revision);assert.equal(body.source_fingerprint,issue.source_fingerprint);
  applyIssues=applyIssues.filter(item=>item!==issue);result={accepted:true,...status()};
 } else if(u.pathname==="/api/draft-load"){
  const save=saves.find(item=>item.id===body.save_id);assert(save);
  for(const key of Object.keys(values))delete values[key];Object.assign(values,JSON.parse(JSON.stringify(save.values)));
  history.length=0;cursor=0;revision++;result={loaded_save_id:save.id,loaded_saved_at:save.saved_at,...status()};
 } else if(u.pathname==="/api/draft-restore"){
  for(const key of Object.keys(values))delete values[key];Object.assign(values,JSON.parse(JSON.stringify(checkpoint)));
  history.length=0;cursor=0;revision++;result=status();
 } else if(u.pathname==="/api/draft-undo"||u.pathname==="/api/draft-redo"){
  const undo=u.pathname.endsWith("undo"),action=history[undo?cursor-1:cursor];
  assert(action,"No history action");values[action.key]={...(undo?action.before:action.after)};cursor+=undo?-1:1;revision++;
  result={...status(),slot:"T:RU_RU:"+action.key,version:{mode:"translator",locale:"RU_RU",key:action.key,edit:values[action.key]}};
 } else if(u.pathname==="/api/apply-project"){
  if(gameRunning&&["game","all"].includes(body.target))return {ok:false,json:async()=>({error:"Civilization V is running",error_code:"game_running",processes:["CivilizationV_DX11.exe"]})};
  applyIssues=simulatePartial?[{slot:"T:RU_RU:"+keys[0],revision,mode:"translator",locale:"RU_RU",key:keys[0],
    english_text:"English {1_Name}",draft_text:values[keys[0]].text,reason:"Missing: {1_Name} × 1",code:"formatting",source_fingerprint:"a".repeat(64)}]:[];
  applying=body.target;result={state:"running",target:applying,count:status().draft_count,...status()};
 } else if(u.pathname==="/api/apply-status"){result={state:"complete",target:applying,count:0,applied_count:1,phase:"Complete",...status()};}
 else if(u.pathname==="/api/versions")result={versions:[{version:"v35.4",supported:true}],warning:""};
 else if(u.pathname==="/api/editor-latest")result={current:"0.26",latest:"0.26",available:false};
 else if(u.pathname==="/api/game-process")result={processes:gameRunning?["CivilizationV_DX11.exe"]:[]};
 else if(u.pathname==="/api/game-status")result=metadata().game;
 else if(u.pathname==="/api/download-status")result={state:"idle"};
 else if(u.pathname==="/api/handoff-review")result=mergeFixture;
 else if(u.pathname==="/api/handoff-choices")result={saved:true};
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
 w.eval(fs.readFileSync(path.join(root,"localization/editor/app.js"),"utf8") + "\nwindow.editorRefresh=refresh; window.editorReflectDraft=reflectDraft; window.editorOpenMerge=review=>{mergeReview=review;mergeChoices={};meta.draft_count=0;openMerge();}; window.editorDebug=()=>({historyPending,applyPending,chosen,savedDraft,current:captureDraft(),dirty:hasUnsaved(),context:selectionContext});");
 function select(i){d.querySelectorAll("tbody tr")[i].click();}
 function input(text){e("translation").value=text;e("translation").dispatchEvent(new w.Event("input",{bubbles:true}));}
 function hotkey(key){d.dispatchEvent(new w.KeyboardEvent("keydown",{key,ctrlKey:true,bubbles:true}));}
 await wait(()=>w.currentRows?.length===4&&e("table-loading").hidden,"startup");
 assert.equal(e("apply-primary").dataset.target,"all");
 assert(d.querySelector('thead th[data-field="synced_to"]'));
 assert(d.querySelector(".sync-chip").title.includes("Source project"));
 assert(!e("save").disabled,"Save records a global checkpoint even without a row selection");
 const rowCalls=requests.filter(x=>x.path==="/api/rows").length;
 e("search-input").value="TXT_KEY_UI_1";e("search-input").dispatchEvent(new w.Event("input",{bubbles:true}));
 e("search-input").focus();e("search-input").setSelectionRange(3,3);
 await new Promise(r=>setTimeout(r,400));
 assert.equal(requests.filter(x=>x.path==="/api/rows").length,rowCalls,"Typing does not submit Search");
 assert.equal(e("search-input").selectionStart,3,"Editing in the middle keeps the caret");
 e("search-input").dispatchEvent(new w.KeyboardEvent("keydown",{key:"Enter",bubbles:true}));
 await wait(()=>w.currentRows?.length===1&&e("table-loading").hidden,"Search by Enter");
 assert.equal(w.currentRows[0].key,keys[0]);
 e("search-clear").click();await wait(()=>w.currentRows?.length===4&&e("table-loading").hidden,"Search clear");
 assert.equal(e("search-input").value,"");
 assert.equal(w.getComputedStyle(e("search-clear")).minHeight,"0");
 assert.equal(w.getComputedStyle(e("search-clear")).height,"27px");
 assert.equal(w.getComputedStyle(d.querySelector(".search-box")).overflow,"hidden");
 e("search-input").value=keys[1];e("search-input").dispatchEvent(new w.Event("input",{bubbles:true}));
 e("search-submit").click();await wait(()=>w.currentRows?.length===1&&e("table-loading").hidden,"Search button");
 assert.equal(w.currentRows[0].key,keys[1]);
 e("search-clear").click();await wait(()=>w.currentRows?.length===4&&e("table-loading").hidden,"Clear button resets applied query");
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
 assert.equal(e("selected").textContent,keys[1]+" · RU_RU");
 hotkey("y");await wait(()=>cursor===2&&e("table-loading").hidden,"Ctrl+Y");
 hotkey("z");await wait(()=>cursor===1&&e("table-loading").hidden,"undo before branching");
 e("search-clear").click();
 await wait(()=>w.currentRows?.length===4&&e("table-loading").hidden,"return to all rows");
 select(3);input("Новая ветка");await wait(()=>values[keys[3]]?.text==="Новая ветка"&&e("draft-state").textContent.includes("Saved locally"),"branch");
 assert.equal(cursor,history.length);assert(e("redo").disabled);
 e("discard").click();assert(e("discard-description").textContent.includes("both modes"));
 const historyCalls=requests.filter(x=>x.path==="/api/draft-undo").length;
 hotkey("z");assert.equal(requests.filter(x=>x.path==="/api/draft-undo").length,historyCalls,"Dialog typing keeps native shortcuts");
 e("discard-confirm").click();await wait(()=>!e("discard-dialog").open&&e("table-loading").hidden,"restore checkpoint");
 assert.equal(values[keys[0]].text,"Первый");assert(!values[keys[3]]);
 select(1);input("Вторая сохранённая версия");
 await wait(()=>values[keys[1]]?.text==="Вторая сохранённая версия"&&e("draft-state").textContent.includes("Saved locally"),"second Save content");
 e("save").click();await wait(()=>saves.length===2&&!e("save").disabled,"second explicit Save");
 const latestSaveTime=savedAt;
 e("saved-versions").click();await wait(()=>e("saved-versions-dialog").open,"Saved versions dialog");
 const savedChoices=d.querySelectorAll('input[name="saved-version"]');assert.equal(savedChoices.length,2);
 savedChoices[1].click();e("saved-versions-load").click();
 await wait(()=>!e("saved-versions-dialog").open&&e("table-loading").hidden&&!e("save").disabled,"load older Save");
 assert.equal(values[keys[0]].text,"Первый");assert(!values[keys[1]]);
 assert.equal(savedAt,latestSaveTime,"Loading an older Save keeps the latest restore point");
 assert.equal(saves.length,2);assert.equal(history.length,0);
 e("discard").click();e("discard-confirm").click();
 await wait(()=>!e("discard-dialog").open&&e("table-loading").hidden,"Trash restores latest Save after loading an older Save");
 assert.equal(values[keys[1]].text,"Вторая сохранённая версия");
 simulatePartial=true;e("apply-primary").click();
 await wait(()=>!e("apply-primary").disabled&&!e("troubleshoot-button").hidden,"partial Apply report");
 assert(e("save-state").textContent.includes("Applied 1 drafts"));
 assert(e("save-state").textContent.includes("1 draft errors found"));
 e("troubleshoot-button").click();await wait(()=>!e("troubleshoot-view").hidden,"Troubleshoot page");
 assert(e("workspace").hidden);assert.equal(e("troubleshoot-table").querySelectorAll("tbody tr").length,1);
 assert(e("troubleshoot-table").textContent.includes("Missing: {1_Name}"));
 e("troubleshoot-table").querySelector("tbody button").click();
 await wait(()=>e("troubleshoot-view").hidden&&e("selected").textContent===keys[0]+" · RU_RU"&&e("table-loading").hidden,"Edit issue reveals its row");
 input("Исправленный {1_Name}");await wait(()=>values[keys[0]]?.text==="Исправленный {1_Name}"&&e("draft-state").textContent.includes("Saved locally"),"fix issue");
 e("troubleshoot-button").click();await wait(()=>!e("troubleshoot-view").hidden,"review edited issue");
 assert(e("troubleshoot-table").textContent.includes("Edited since Apply"));
 simulatePartial=false;e("troubleshoot-retry").click();
 await wait(()=>e("troubleshoot-view").hidden&&!e("apply-primary").disabled&&e("troubleshoot-button").hidden,"Retry applies corrected row");
 simulatePartial=true;e("apply-primary").click();
 await wait(()=>!e("apply-primary").disabled&&!e("troubleshoot-button").hidden,"formatting review report");
 e("troubleshoot-button").click();await wait(()=>!e("troubleshoot-view").hidden,"formatting review page");
 const accept=[...e("troubleshoot-table").querySelectorAll("button")].find(button=>button.textContent==="Accept formatting");assert(accept);
 const textBefore=values[keys[0]].text,applyCalls=requests.filter(x=>x.path==="/api/apply-project").length;
 accept.click();assert(e("formatting-dialog").open);
 e("formatting-cancel").click();assert(!requests.some(x=>x.path==="/api/draft-accept-formatting"),"Cancel does not accept formatting");
 accept.click();e("formatting-confirm").click();
 await wait(()=>!e("formatting-dialog").open&&e("troubleshoot-table").querySelectorAll("tbody tr").length===0,"explicit formatting accepted");
 assert.equal(values[keys[0]].text,textBefore,"Formatting acceptance keeps the exact translation");
 assert.equal(requests.filter(x=>x.path==="/api/apply-project").length,applyCalls,"Acceptance leaves destination writes for Apply");
 simulatePartial=false;e("message").textContent="";e("troubleshoot-retry").click();
 await wait(()=>requests.filter(x=>x.path==="/api/apply-project").length===applyCalls+1&&
  e("troubleshoot-view").hidden&&!e("apply-primary").disabled&&e("table-loading").hidden&&
  e("message").textContent.startsWith("Applied"),"Apply after acceptance");
 gameRunning=true;
 e("apply-toggle").click();assert(!e("apply-menu").hidden);e("game-apply").click();
 await wait(()=>e("game-running-dialog").open,"running game modal");
 e("game-running-retry").click();await wait(()=>e("game-running-status").textContent.includes("still running"),"blocked Retry");
 assert(e("game-running-dialog").open);
 gameRunning=false;e("game-running-retry").click();await wait(()=>!e("game-running-dialog").open,"Retry after game closes");
 await wait(()=>e("apply-primary").dataset.target==="game"&&!e("apply-primary").disabled,"select game action");
 assert(requests.some(x=>x.path==="/api/apply-project"&&x.body.target==="game"));
 assert(!e("all-apply").hidden&&e("game-apply").hidden);
 w.editorOpenMerge(mergeFixture);
 assert(e("merge-apply").disabled,"Conflicting replacements require explicit choices");
 const englishChoice=e("merge-table").querySelector('select[aria-label="Decision for en_US TXT_KEY_UI_1"]');
 const translationChoice=()=>e("merge-table").querySelector('select[aria-label="Decision for RU_RU TXT_KEY_UI_1"]');
 assert.equal(englishChoice.value,"review");assert.equal(translationChoice().value,"review");
 assert(e("merge-table").querySelector('input[aria-label="Include RU_RU TXT_KEY_UI_2"]').checked,"Independent additions remain included");
 englishChoice.value="keep";englishChoice.dispatchEvent(new w.Event("change",{bubbles:true}));
 await wait(()=>!translationChoice().disabled&&e("merge-summary").textContent.includes("1 unresolved"),"English decision rechecks dependencies");
 assert(e("merge-apply").disabled);
 translationChoice().value="incoming";translationChoice().dispatchEvent(new w.Event("change",{bubbles:true}));
 await wait(()=>!e("merge-apply").disabled&&requests.some(x=>x.path==="/api/handoff-choices"),"Explicit translation decision enables Merge");
 const decisions=requests.filter(x=>x.path==="/api/handoff-choices").at(-1).body.choices;
 assert.equal(decisions["en_US:TXT_KEY_UI_1"],"keep");assert.equal(decisions["RU_RU:TXT_KEY_UI_1"],"incoming");
 assert(!requests.some(x=>x.path==="/api/handoff-apply"),"Review does not write destinations");
 e("merge-back").click();
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
 console.log("Editor UI: Search/Enter/clear, Save/history, partial Apply/Troubleshoot, formatting acceptance, autosave, undo/redo, explicit Merge decisions and split actions passed.");
})().catch(error=>{console.error(error);process.exit(1);});
