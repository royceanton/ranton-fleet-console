"use strict";
const $ = id => document.getElementById(id);
let state = null, selected = null, renderedDetail = "", projectSignature = "", polling = false;
let submissionKey = crypto.randomUUID();
const labels = {queued:"Queued", waiting:"Waiting for capacity", running:"Running", review:"Ready for review", needs_input:"Needs attention", paused:"Paused", completed:"Completed"};
function node(tag, text, className) { const element = document.createElement(tag); if (text != null) element.textContent = text; if (className) element.className = className; return element; }
function feedback(message, error = false) { $("feedback").textContent = message; $("feedback").className = error ? "error" : ""; }
async function api(path, data) {
  const options = {credentials:"same-origin"};
  if (data !== undefined) Object.assign(options,{method:"POST",headers:{"Content-Type":"application/json","X-Fleet-Intent":"dashboard"},body:JSON.stringify(data)});
  const response = await fetch(path,options); const result = await response.json();
  if (!response.ok) { const error = new Error(result.error || "The operation did not complete"); error.status = response.status; throw error; }
  return result;
}
const stamp = seconds => new Date(seconds*1000).toLocaleString(undefined,{month:"short",day:"numeric",hour:"2-digit",minute:"2-digit"});
function until(seconds) { const minutes=Math.max(0,Math.round((seconds-Date.now()/1000)/60)); return minutes>=1440?`${Math.floor(minutes/1440)}d ${Math.floor(minutes%1440/60)}h`:minutes>=60?`${Math.floor(minutes/60)}h ${minutes%60}m`:`${minutes}m`; }
function age(seconds) { return seconds==null?"not observed":seconds<60?"just now":`${Math.floor(seconds/60)}m ago`; }
function duration(minutes) { return minutes==null?"Unknown window":minutes>=1440?`${Math.round(minutes/1440)}-day window`:`${minutes/60}-hour window`; }
function accountsView() {
  const host=$("accounts"); host.replaceChildren();
  for(const account of state.accounts) {
    const row=node("div",null,"account"), heading=node("div",null,"account-heading");
    heading.append(node("span",account.alias[0].toUpperCase()+account.alias.slice(1),"account-name"),node("span",account.eligible?"Ready":account.reason,"account-status"+(account.eligible?"":" blocked"))); row.append(heading);
    const windows=(account.quotaWindows||[]).flatMap(bucket=>[bucket.primary,bucket.secondary].filter(Boolean).map(window=>({...window,bucket:bucket.limitId})));
    if(!windows.length) row.append(node("p","Quota is unknown. Waiting for a fresh reading.","muted"));
    for(const window of windows) {
      const quota=node("div",null,"quota-row"), value=node("strong",window.remainingPercent==null?"Unknown":`${window.remainingPercent}%`,"quota-value");value.append(node("span","remaining"));quota.append(value,node("span",duration(window.windowDurationMins),"quota-window"));row.append(quota);
      row.append(node("p",window.resetsAt?`Resets in ${until(window.resetsAt)} · ${stamp(window.resetsAt)}`:"Reset time is unknown","quota-meta"));
    }
    row.append(node("p",`Observed ${age(account.ageSeconds)}${account.maskedEmail?" · "+account.maskedEmail:""}`,"quota-meta")); host.append(row);
  }
  $("quota-note").textContent=`${state.reservePercent}% headroom is reserved on every reported window. Missing windows are not invented.`;
}
function taskTile(task) {
  const button=node("button",null,"task-tile"+(task.id===selected?" selected":""));button.type="button";
  button.append(node("span",labels[task.state],"task-state "+task.state),node("span",task.title,"task-tile-title"));
  const meta=node("span",null,"task-tile-meta"), project=state.projects.find(p=>p.id===task.project);
  meta.append(node("span",project?.name||"Project"),node("span",task.account?`${task.account} · ${task.mode==="code"?"worktree":"analysis"}`:"Automatic account"));button.append(meta);
  button.addEventListener("click",()=>{selected=task.id;renderedDetail="";renderBoard();renderDetail();if(innerWidth<800)$("task-detail").scrollIntoView({behavior:"instant",block:"start"});});return button;
}
function listTasks(id,tasks,emptyText) {const element=$(id);element.replaceChildren();if(!tasks.length)element.append(node("p",emptyText,"empty"));else for(const task of tasks)element.append(taskTile(task));}
function renderBoard() {
  const attention=state.tasks.filter(t=>["review","needs_input"].includes(t.state)), queued=state.tasks.filter(t=>["queued","waiting"].includes(t.state)), running=state.tasks.filter(t=>t.state==="running"), history=state.tasks.filter(t=>["completed","paused"].includes(t.state));
  listTasks("attention-tasks",attention,"Results and questions will appear here. You stay in charge of what gets merged.");listTasks("queued-tasks",queued,"Give the fleet a useful task. It will choose an eligible account.");listTasks("running-tasks",running,"No active workers. The fleet waits when there is no work to do.");listTasks("history-tasks",history,"Completed and paused tasks stay available for inspection.");
  $("attention-total").textContent=attention.length;$("queued-total").textContent=queued.length;$("running-total").textContent=running.length;$("queue-count").textContent=queued.length;$("attention-count").textContent=attention.length;$("history-title").textContent=`Completed & paused (${history.length})`;
}
async function action(task, operation, data={}) {try{await api(`/api/tasks/${task.id}/${operation}`,data);renderedDetail="";feedback(operation==="followup"?"Follow-up queued on the same account.":"Task updated.");await load();}catch(e){feedback(e.message,true);}}
function renderDetail() {
  const task=state.tasks.find(t=>t.id===selected);if(!task)return;
  const signature=task.id+":"+task.updated;if(signature===renderedDetail)return;renderedDetail=signature;
  const detail=$("task-detail");detail.replaceChildren(node("p",labels[task.state],"eyebrow"),node("h2",task.title));
  const meta=node("dl",null,"detail-meta");for(const [label,value] of [["Account",task.account||"Chosen at dispatch"],["Model",task.model||"Automatic"],["Mode",task.mode==="code"?"Isolated worktree":"Read-only"],["Branch",task.branch],["Workspace",task.workspace],["Session",task.session]])if(value)meta.append(node("dt",label),node("dd",value));detail.append(meta);
  if(task.route)detail.append(node("p",task.route,"muted"));if(task.error)detail.append(node("p",task.error,"detail-error"));
  if(task.usage){try{const usage=JSON.parse(task.usage);if(usage.cached_input_tokens!=null)detail.append(node("p",`${usage.cached_input_tokens.toLocaleString()} cached input tokens reported in the last turn.`,"fineprint"));}catch{}}
  if(task.result)detail.append(node("div",task.result,"result"));
  const controls=node("div",null,"task-actions");
  if(["running","queued","waiting"].includes(task.state)){const b=node("button","Pause task");b.onclick=()=>action(task,"pause");controls.append(b);}
  if(["paused","needs_input"].includes(task.state)){const b=node("button","Resume same account");b.onclick=()=>action(task,"resume");controls.append(b);}
  if(["review","needs_input","paused"].includes(task.state)){const b=node("button","Mark reviewed","primary");b.onclick=()=>action(task,"complete");controls.append(b);}detail.append(controls);
  if(task.state!=="running"&&!['queued','waiting'].includes(task.state)){const form=node("form"),label=node("label","Continue this task"),input=node("textarea");input.rows=3;input.maxLength=16000;input.required=true;input.placeholder="A follow-up or answer for the same worker";label.append(input);form.append(label,node("button","Queue follow-up"));form.onsubmit=async event=>{event.preventDefault();await action(task,"followup",{message:input.value});};detail.append(form);}
}
function projectsView() {
  const signature=JSON.stringify(state.projects);if(signature===projectSignature)return;projectSignature=signature;const selectedValue=$("task-project").value;$("task-project").replaceChildren();$("project-list").replaceChildren();
  for(const project of state.projects){const option=node("option",project.name);option.value=project.id;$("task-project").append(option);const row=node("div",null,"project-row");row.append(node("strong",project.name),node("span",project.writable?"Isolated edits enabled":"Analysis only"));$("project-list").append(row);}if(state.projects.some(p=>p.id===selectedValue))$("task-project").value=selectedValue;projectMode();
}
function projectMode(){const project=state?.projects.find(p=>p.id===$("task-project").value);$("task-mode").options[0].disabled=!project?.writable;if(!project?.writable)$("task-mode").value="read-only";}
function activityView() {$("activity-list").replaceChildren();for(const event of state.events.slice(0,7)){const row=node("li"),clock=node("time",new Date(event.at*1000).toLocaleTimeString(undefined,{hour:"2-digit",minute:"2-digit"})), text=node("span",event.message);row.append(clock,text);$("activity-list").append(row);}}
function render() {
  accountsView();projectsView();renderBoard();renderDetail();activityView();
  $("slots").textContent=`${state.activeSlots} / ${state.maxSlots} slots in use`;$("route-account").textContent=state.nextAccount?state.nextAccount[0].toUpperCase()+state.nextAccount.slice(1):"Waiting";$("route-state").textContent=state.nextAccount?"Native Codex · 24 GB Mac":"Capacity needs a fresh reading";$("route-reason").textContent=state.nextReason;
  $("dispatch-label").textContent=state.dispatchEnabled?"Automatic dispatch is on":"New work is paused";$("dispatch-toggle").textContent=state.dispatchEnabled?"Pause new work":"Enable new work";
  $("overview-line").textContent=`Two independent accounts. ${state.activeSlots} active ${state.activeSlots===1?"worker":"workers"}. Tasks stay on their account for continuity.`;
}
async function load() {if(polling)return;polling=true;try{state=await api("/api/status");$("login").hidden=true;$("dashboard").hidden=false;$("connection").textContent="Connected to coordinator";$("login-feedback").textContent="";render();}catch(e){if(e.status===401){$("dashboard").hidden=true;$("login").hidden=false;$("connection").textContent="Private access";}else{$("connection").textContent="Connection interrupted";feedback("The coordinator is unreachable. Task state stays on the Mac.",true);}}finally{polling=false;}}
$("login-form").onsubmit=async event=>{event.preventDefault();const button=event.target.querySelector("button");button.disabled=true;try{await api("/api/login",{key:$("access-key").value});$("access-key").value="";await load();}catch(e){$("login-feedback").textContent=e.message;}finally{button.disabled=false;}};
$("new-task").onclick=()=>{$("task-composer").hidden=false;$("task-title").focus();};$("close-composer").onclick=()=>{$("task-composer").hidden=true;};$("task-project").onchange=projectMode;
$("task-form").addEventListener("input",()=>{submissionKey=crypto.randomUUID();});
$("task-form").onsubmit=async event=>{event.preventDefault();const button=event.target.querySelector('button');button.disabled=true;try{const task=await api("/api/tasks",{project:$("task-project").value,title:$("task-title").value,goal:$("task-goal").value,mode:$("task-mode").value,priority:Number($("task-priority").value),timeout:Number($("task-timeout").value),idempotency:submissionKey});submissionKey=crypto.randomUUID();selected=task.id;renderedDetail="";$("task-title").value="";$("task-goal").value="";$("task-composer").hidden=true;feedback("Task queued. The fleet will choose its account.");await load();}catch(e){feedback(e.message,true);}finally{button.disabled=false;}};
$("project-form").onsubmit=async event=>{event.preventDefault();try{await api("/api/projects",{path:$("project-path").value,writable:$("project-write").checked});$("project-path").value="";feedback("Project registered on this Mac.");await load();}catch(e){feedback(e.message,true);}};
$("refresh").onclick=async()=>{try{await api("/api/refresh",{});feedback("A fresh quota reading is requested. Running workers keep their account lock.");}catch(e){feedback(e.message,true);}};
$("dispatch-toggle").onclick=async()=>{try{await api("/api/dispatch",{enabled:!state.dispatchEnabled});feedback("Dispatch updated. Existing workers keep running.");await load();}catch(e){feedback(e.message,true);}};
$("pair-device").onclick=async()=>{try{const pair=await api("/api/pair",{}),panel=$("pairing");panel.hidden=false;panel.className="pair-box";panel.replaceChildren(node("p","Open this private address on the other device with Tailscale connected:"),node("p",state.privateUrl),node("strong",pair.code),node("p",`Enter this one-use device code on the login page before ${stamp(pair.expires)}.`));}catch(e){feedback(e.message,true);}};
load();setInterval(load,5000);
