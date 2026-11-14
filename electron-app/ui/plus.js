/* Lead Studio - the newer pages: Unibox, channel queues, Video maker, Agile Assets, SEO, Ads connectors.
   Loaded after index.html's own script; it shares its helpers ($, esc, get, post, toast, go ...). */
(function(){
const css=document.createElement("style");css.textContent=`
.nav .cat{all:unset;box-sizing:border-box;display:flex;align-items:center;gap:6px;width:100%;cursor:pointer;color:var(--side-mute);
  font-size:10.5px;font-weight:650;letter-spacing:.06em;text-transform:uppercase;padding:12px 9px 4px;border-radius:6px}
.nav .cat:hover{color:var(--side-ink)}.nav .cat .chev{margin-left:auto;transition:transform .15s}
.nav .cat.shut .chev{transform:rotate(-90deg)}
.nav .sub{color:var(--side-mute);font-size:10.5px;padding:8px 9px 2px 30px;opacity:.85}
.nav button.ind{padding-left:20px}
.qrow{display:grid;grid-template-columns:34px minmax(150px,220px) minmax(0,1fr) auto;gap:10px;align-items:start;padding:10px 12px;border-bottom:1px solid var(--line)}
.qrow.sent{opacity:.55}.qrow.skipped{opacity:.4}
.qrow textarea{width:100%;min-height:62px;font-size:12.5px;border:1px solid var(--line);border-radius:7px;padding:6px 8px;background:var(--panel);resize:vertical}
.qrow .who b{display:block}.qrow .who a{font-size:11.5px;word-break:break-all}
.qrow .acts{display:flex;flex-direction:column;gap:4px}
.drop{border:2px dashed var(--line);border-radius:10px;padding:18px;text-align:center;color:var(--mute);cursor:pointer}
.drop.over{border-color:var(--accent);color:var(--accent);background:var(--accent-soft)}
.pick{max-height:220px;overflow:auto;border:1px solid var(--line);border-radius:7px;padding:4px}
.pick label{display:flex;gap:8px;align-items:center;padding:4px 6px;border-radius:5px;cursor:pointer}
.pick label:hover{background:var(--soft)}.pick small{margin-left:auto;color:var(--mute)}
.md{white-space:pre-wrap;line-height:1.55;font-size:13.5px}
.hit{padding:10px 0;border-bottom:1px solid var(--line)}.hit b{display:block}.hit small{color:var(--mute);word-break:break-all}
.hit p{margin:4px 0 0;color:var(--ink)} .hit mark{background:var(--accent-soft);color:inherit;padding:0 2px;border-radius:3px}
.kv{display:grid;grid-template-columns:150px 1fr;gap:6px 10px}`;
document.head.append(css);

const ICP={
 camp:'<svg width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M2.5 6.5v3l7 3.5v-10z" stroke-linejoin="round"/><path d="M9.5 5.5h2.5a1.5 1.5 0 010 3H9.5M4.5 10l1 3.5" stroke-linecap="round"/></svg>',
 send:'<svg width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M2 8l11.5-5.5L9 14l-1.6-4.6z" stroke-linejoin="round"/></svg>',
 inbox:'<svg width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M2 9.5l2-6h8l2 6v3a1 1 0 01-1 1H3a1 1 0 01-1-1z" stroke-linejoin="round"/><path d="M2 9.5h3.5l1 1.5h3l1-1.5H14"/></svg>',
 chat:'<svg width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M2.5 12.8l.9-2.6A5.2 5.2 0 118 13.2a5.4 5.4 0 01-2.6-.7z" stroke-linejoin="round"/></svg>',
 li:'<svg width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="2" y="2" width="12" height="12" rx="2"/><path d="M5 7v4M5 5v.01M8 11V7m0 1.8c0-1 .8-1.8 1.7-1.8S11 7.8 11 8.8V11" stroke-linecap="round"/></svg>',
 ig:'<svg width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="2" y="2" width="12" height="12" rx="3.4"/><circle cx="8" cy="8" r="2.7"/><path d="M11.4 4.6v.01" stroke-linecap="round"/></svg>',
 yt:'<svg width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="1.8" y="3.5" width="12.4" height="9" rx="2.4"/><path d="M6.8 6.2v3.6L9.8 8z" stroke-linejoin="round"/></svg>',
 x:'<svg width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M3 3l10 10M13 3L3 13" stroke-linecap="round"/></svg>',
 form:'<svg width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="2.5" y="2" width="11" height="12" rx="1.5"/><path d="M5 5.5h6M5 8h6M5 10.5h3.5" stroke-linecap="round"/></svg>',
 video:'<svg width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="1.8" y="3.5" width="9" height="9" rx="1.6"/><path d="M10.8 7l3.4-2v6l-3.4-2" stroke-linejoin="round"/></svg>',
 asset:'<svg width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="2" y="2.5" width="12" height="11" rx="1.6"/><circle cx="5.8" cy="6.2" r="1.2"/><path d="M2.5 12l3.6-3.4 2.6 2.4 2-1.8 2.8 2.6" stroke-linejoin="round"/></svg>',
 post:'<svg width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M3 13l1-3.5 7-7 2.5 2.5-7 7z" stroke-linejoin="round"/></svg>',
 book:'<svg width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M2.5 3.2c2-.8 3.8-.6 5.5.6 1.7-1.2 3.5-1.4 5.5-.6v9.6c-2-.8-3.8-.6-5.5.6-1.7-1.2-3.5-1.4-5.5-.6z" stroke-linejoin="round"/><path d="M8 3.8v9.6"/></svg>',
 bulb:'<svg width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M5.6 10.6A4.4 4.4 0 1110.4 10.6c-.5.4-.8 1-.8 1.6H6.4c0-.6-.3-1.2-.8-1.6zM6.4 14h3.2" stroke-linecap="round"/></svg>',
 cpu:'<svg width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="4" y="4" width="8" height="8" rx="1.4"/><path d="M6.5 1.8v2.2M9.5 1.8v2.2M6.5 12v2.2M9.5 12v2.2M1.8 6.5H4M1.8 9.5H4M12 6.5h2.2M12 9.5h2.2" stroke-linecap="round"/></svg>',
 ads:'<svg width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M2.5 13.5V9M6.2 13.5V6M9.8 13.5V8M13.5 13.5V3" stroke-linecap="round"/></svg>',
 chev:'<svg class="chev" width="10" height="10" viewBox="0 0 10 10" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M2.5 3.8L5 6.3l2.5-2.5" stroke-linecap="round" stroke-linejoin="round"/></svg>'};
Object.assign(IC,ICP);
Object.assign(SECT,{campaigns:["Outreach","Campaigns"],contacts:["Outreach","Contacts"],senders:["Outreach","Senders"],
 unibox:["Outreach","Unibox"],whatsapp:["Outreach","WhatsApp"],"q:linkedin":["Outreach","LinkedIn"],"q:instagram":["Outreach","Instagram"],
 "q:youtube":["Outreach","YouTube"],"q:twitter":["Outreach","X / Twitter"],"q:forms":["Outreach","Contact forms"],
 gaps:["Outreach","No contacts"],web:["Outreach","Crawler"],leads:["Outreach","Files"],enrich:["Outreach","AI enrich"],
 video:["Social Media","Video maker"],agile:["Social Media","Agile Assets"],posts:["Social Media","Social posts"],
 seo:["SEO","Article library"],seoideas:["SEO","Idea generator"],seomodels:["SEO","Ollama models"],ads:["Ads Management","Ads connectors"]});

/* ---------------- sidebar: four categories ---------------- */
let shut={};try{shut=JSON.parse(localStorage.getItem("ls_shut")||"{}")}catch(e){}
window.sideNav=function(item,groups,gname,run){
  const cat=(k,n,body)=>`<button class="cat ${shut[k]?"shut":""}" data-cat="${k}">${esc(n)}${IC.chev}</button>`+(shut[k]?"":body);
  const pipe=groups.map(g=>item("g:"+g,gname(g),"tool",S.tools.filter(t=>t.group===g).length,"ind")).join("");
  return item("today","Today","today")+item("jobs","Jobs","jobs",run||"")
   +cat("out","Outreach",item("campaigns","Campaigns","camp")+item("contacts","Contacts","contacts")+item("senders","Senders","send")
     +item("unibox","Unibox","inbox")+item("whatsapp","WhatsApp messages","chat")+item("q:linkedin","LinkedIn text","li")
     +item("q:instagram","Instagram","ig")+item("q:youtube","YouTube","yt")+item("q:twitter","X comments & messages","x")
     +item("q:forms","Contact forms","form")
     +`<div class="sub">Lead tools</div>`+item("gaps","No contacts","gap",0,"ind")+item("web","Crawler","web",0,"ind")
     +item("leads","Files","leads",0,"ind")+item("enrich","AI enrich","enrich",0,"ind")+pipe)
   +cat("soc","Social Media",item("video","Video maker","video")+item("agile","Agile Assets","asset")+item("posts","Social posts","post"))
   +cat("seo","SEO",item("seo","Article library","book")+item("seoideas","Idea generator","bulb")+item("seomodels","Ollama models","cpu"))
   +cat("ads","Ads Management",item("ads","Ads connectors","ads"))
   +`<div class="grow"></div><div class="rule"></div>`+item("conn","Connections","conn");
};
document.addEventListener("click",e=>{const v=e.target.closest("#main [data-v]");if(v&&!v.onclick){go(v.dataset.v);return}
  const c=e.target.closest("[data-cat]");if(!c)return;
  shut[c.dataset.cat]=!shut[c.dataset.cat];try{localStorage.setItem("ls_shut",JSON.stringify(shut))}catch(x){}chrome()});

/* ---------------- shared bits ---------------- */
let P=null;                                        // studio_plus state
async function pstate(force){if(!P||force){const r=await get("/api/plus/state");P=r}return P}
const bar=(t,extra="")=>`<div class="bar"><h1>${esc(t)}</h1>${extra}</div>`;
const jobGo=async r=>{if(r.error)return toast(r.error,true);curJob=r.job;JT="log";await refresh();go("log")};
const fmtMd=t=>esc(t).replace(/^### (.*)$/gm,"<b>$1</b>").replace(/^## (.*)$/gm,"<b style='font-size:15px'>$1</b>").replace(/^# (.*)$/gm,"<b style='font-size:17px'>$1</b>").replace(/\*\*(.+?)\*\*/g,"<b>$1</b>");
let MODELS=null;
async function models(){if(MODELS)return MODELS;const r=await get("/api/ollama/models");MODELS=r.models||[];MODELS.err=r.error;return MODELS}
const modelSel=(id,cur)=>`<select id="${id}">${(MODELS||[]).length?MODELS.map(m=>`<option ${m===cur?"selected":""}>${esc(m)}</option>`).join(""):`<option value="">${esc(MODELS&&MODELS.err||"No models - add the Ollama key under Connections")}</option>`}</select>`;

/* ---------------- Unibox ---------------- */
let U={days:7,res:null,busy:false,q:""};
async function unibox(){
  const m=$("#main"),r=U.res;
  const rows=r&&r.replies?r.replies.filter(x=>!U.q||(x.from+" "+x.subject+" "+x.mailbox).toLowerCase().includes(U.q.toLowerCase())):[];
  m.innerHTML=bar("Unibox",`<span class="note">Replies from every mailbox with IMAP, in one list</span><span class="sp"></span>
    <select id="ud">${[1,3,7,14,30].map(d=>`<option value="${d}" ${d==U.days?"selected":""}>last ${d} day${d>1?"s":""}</option>`).join("")}</select>
    <input type="text" id="uq" placeholder="Filter" value="${esc(U.q)}" style="width:180px"><button class="btn go" id="ugo">${U.busy?"Checking...":"Check all mailboxes"}</button>`)
   +`<div class="body flush">${U.busy?`<div class="empty"><b>Reading the inboxes</b>Up to 60 mailboxes, 8 at a time. A minute on a big list.</div>`
    :!r?`<div class="empty"><b>Nothing loaded yet</b>Press "Check all mailboxes". It reads the headers only (read-only, nothing is marked as read) and keeps the real replies - out-of-office notes and bounces are counted separately. Add or fix IMAP under Senders.</div>`
    :r.error?`<div class="empty"><b>${esc(r.error)}</b></div>`
    :`<div class="bar"><span class="tag ok">${nfmt(r.replies.length)} replies</span><span class="tag">${nfmt(r.bounces)} bounces / auto-replies</span><span class="tag">${r.checked} of ${r.mailboxes} mailboxes</span>${r.errors.length?`<span class="tag bad" title="${esc(r.errors.map(e=>e.mailbox+": "+e.error).join("\n"))}">${r.errors.length} could not be read</span>`:""}</div>
     <div class="gridwrap">${rows.length?`<table class="grid"><thead><tr><th>Date</th><th>Mailbox</th><th>From</th><th>Subject</th><th></th></tr></thead><tbody>${rows.map(x=>`<tr><td>${esc((x.date||"").replace(/ [+-]\d{4}.*$/,""))}</td><td>${esc(x.mailbox)}</td><td>${esc(x.from)}</td><td>${esc(x.subject)}</td>
      <td><a class="btn" href="mailto:${encodeURIComponent(x.address)}?subject=${encodeURIComponent(/^re:/i.test(x.subject)?x.subject:"Re: "+x.subject)}">Reply</a></td></tr>`).join("")}</tbody></table>`:`<div class="empty"><b>No replies in this period</b></div>`}</div>
     ${r.errors.length?`<div class="body"><details><summary>Mailboxes that could not be read</summary><pre class="code">${esc(r.errors.map(e=>e.mailbox+": "+e.error).join("\n"))}</pre></details></div>`:""}`}</div>`;
  $("#ud").onchange=e=>U.days=+e.target.value;$("#uq").onchange=e=>{U.q=e.target.value;unibox()};
  $("#ugo").onclick=async()=>{U.busy=true;unibox();U.res=await post("/api/plus/unibox",{days:U.days});U.busy=false;unibox()};
}

/* ---------------- channel queues ---------------- */
const QN={linkedin:"LinkedIn text",instagram:"Instagram",youtube:"YouTube",twitter:"X comments & messages",forms:"Contact forms"};
const QHELP={
 linkedin:"Connection notes and messages. A connection note is cut at 300 characters.",
 instagram:"Direct messages to business profiles.",
 youtube:"Comments under a creator's video, or a note through the channel's About page. Use a video link to comment.",
 twitter:"Replies under a post or direct messages. Use a post link to reply, a profile link to message.",
 forms:"The message for each company's contact form. Find the contact pages first with the Crawler (Find contact pages), then build the queue from its result."};
let Q={};
async function queue(ch){
  const q=Q[ch]||(Q[ch]={tab:"list",list:null,open:null,cols:null,filter:"todo"}),m=$("#main");
  const tabs=`<div class="tabs"><button class="${q.tab==="list"?"on":""}" data-qt="list">Queues</button><button class="${q.tab==="new"?"on":""}" data-qt="new">New queue</button></div>`;
  const note=`<p class="lead">${esc(QHELP[ch])} Lead Studio does not send on ${esc(QN[ch])} by itself - these platforms ban accounts that automate messages. It writes each message, opens the profile in your own browser and copies the text; you paste it and press send, then mark the row.</p>`;
  if(q.tab==="new"){
    const g=q.cols&&q.cols.guess||{},cols=q.cols&&q.cols.cols||[];
    const sel=(id,v)=>`<select id="${id}"><option value="">-</option>${cols.map(c=>`<option ${c===v?"selected":""}>${esc(c)}</option>`).join("")}</select>`;
    m.innerHTML=bar(QN[ch],`<span class="sp"></span>${tabs}`)+`<div class="body">${note}<section class="box"><h2>Build a queue from a lead file</h2><div class="form">
     <label for="qf">Lead file</label><div><input type="text" id="qf" value="${esc(q.file||"")}" placeholder="a CSV inside the workspace, e.g. AGENTSDR_IMPORT/search.csv" style="width:100%">
      <small>Any CSV in the workspace: an export from Contacts, a Crawler result, a scraped list. ${ch==="forms"?`<a href="#" id="qweb">Find contact pages with the Crawler</a>`:""}</small>
      <div style="margin-top:6px"><button class="btn" id="qc">Read its columns</button></div></div>
     ${cols.length?`<label>Link column</label><div>${sel("qu",g.url)}<small>The ${ch==="forms"?"contact page (or website)":"profile or post"} link.</small></div>
      <label>Name column</label><div>${sel("qn",g.name)}</div><label>Company column</label><div>${sel("qco",g.company)}</div>
      <label>Columns</label><div><small>${cols.map(c=>`<code>{{${esc(c)}}}</code>`).join(" ")}</small></div>`:""}
     <label for="qtpl">Message</label><div><textarea id="qtpl" style="min-height:120px" placeholder="Hi {{first_name|there}}, ...">${esc(q.tpl||"")}</textarea><small>Use any column as {{column}}, with a fallback as {{first_name|there}}. Leave it short.</small></div>
     <label for="qai">For AI drafts</label><div><textarea id="qai" placeholder="Optional: what you offer and what you want, for the 'AI draft' button on each row.">${esc(q.ai||"")}</textarea></div>
     <label for="ql">How many rows</label><div><input type="number" id="ql" value="${q.limit||200}" min="1" max="1000" style="width:110px"></div>
     <label for="qnm">Queue name</label><div><input type="text" id="qnm" placeholder="optional" style="width:280px"></div>
     <div class="full"><button class="btn go" id="qb" ${cols.length?"":"disabled"}>Build the queue</button></div></div></section></div>`;
    const keep=()=>{q.file=$("#qf").value.trim();q.tpl=$("#qtpl").value;q.ai=$("#qai").value;q.limit=$("#ql").value};
    $("#qc").onclick=async()=>{keep();const r=await post("/api/plus/queue/cols",{file:q.file,channel:ch});if(!r.ok)return toast(r.error,true);q.cols=r;queue(ch)};
    if($("#qweb"))$("#qweb").onclick=e=>{e.preventDefault();go("web")};
    $("#qb").onclick=async()=>{keep();const r=await post("/api/plus/queue/build",{channel:ch,file:q.file,url_col:$("#qu").value,name_col:$("#qn").value,
      company_col:$("#qco").value,template:q.tpl,instruction:q.ai,limit:q.limit,name:$("#qnm").value});
      if(!r.ok)return toast(r.error,true);toast(r.rows+" rows in the queue");q.tab="list";q.list=null;q.open=r.name;queue(ch)};
  }else{
    if(!q.list){const r=await post("/api/plus/queue/list",{channel:ch});q.list=r.queues||[]}
    let qd=null;if(q.open){const r=await post("/api/plus/queue/get",{channel:ch,name:q.open});qd=r.ok?r.queue:null;if(!qd)q.open=null}
    const rows=qd?qd.rows.map((r,i)=>[r,i]).filter(([r])=>q.filter==="all"||r.status===q.filter):[];
    m.innerHTML=bar(QN[ch],`${qd?`<select id="qsel">${q.list.map(x=>`<option ${x.name===q.open?"selected":""}>${esc(x.name)}</option>`).join("")}</select>
       <select id="qflt">${[["todo","To do"],["sent","Sent"],["skipped","Skipped"],["all","All"]].map(([k,n])=>`<option value="${k}" ${q.filter===k?"selected":""}>${n}</option>`).join("")}</select>
       <span class="tag">${qd.rows.filter(r=>r.status==="sent").length} of ${qd.rows.length} sent</span><button class="btn go" id="qnext">Open next and copy</button>`:""}<span class="sp"></span>${tabs}`)
     +`<div class="body ${qd?"flush":""}">${qd?(rows.length?rows.map(([r,i])=>`<div class="qrow ${r.status}" data-i="${i}"><span class="note">${i+1}</span>
        <div class="who"><b>${esc(r.name||r.company||"-")}</b>${r.company&&r.name?`<span class="note">${esc(r.company)}</span>`:""}<a href="#" data-open="${i}">${esc(r.url)}</a>${r.at?`<small class="note">${esc(r.status)} ${esc(r.at)}</small>`:""}</div>
        <textarea data-msg="${i}">${esc(r.message)}</textarea>
        <div class="acts"><button class="btn" data-go="${i}">Open + copy</button><button class="btn" data-ai="${i}">AI draft</button>
         ${["youtube","twitter"].includes(ch)?`<button class="btn" data-read="${i}">Read thread</button>`:""}
         <button class="btn" data-st="sent" data-si="${i}">Sent</button><button class="btn" data-st="${r.status==="skipped"?"todo":"skipped"}" data-si="${i}">${r.status==="skipped"?"Undo":"Skip"}</button></div></div>`).join("")
        :`<div class="empty"><b>Nothing here</b>${q.filter==="todo"?"Every row is sent or skipped.":"Pick another filter."}</div>`)
      :`${note}${q.list.length?`<div class="tools">${q.list.map(x=>`<div class="tool"><div><h3>${esc(x.name)}</h3><p>${esc(x.source)}</p><div class="file">${esc(x.created)}</div></div>
          <div><span class="tag ok">${x.sent} sent</span> <span class="tag">${x.rows-x.sent-x.skipped} to do</span></div><div class="acts"><button class="btn go" data-qo="${esc(x.name)}">Open</button></div></div>`).join("")}</div>`
        :`<div class="empty"><b>No queue yet</b>Press "New queue": pick a lead file, the column with the ${ch==="forms"?"contact page":"profile link"}, and write the message.</div>`}`}</div>`;
    $$("[data-qo]").forEach(b=>b.onclick=()=>{q.open=b.dataset.qo;queue(ch)});
    if($("#qsel"))$("#qsel").onchange=e=>{q.open=e.target.value;queue(ch)};
    if($("#qflt"))$("#qflt").onchange=e=>{q.filter=e.target.value;queue(ch)};
    const upd=async(i,body)=>{const r=await post("/api/plus/queue/update",{channel:ch,name:q.open,idx:i,...body});if(!r.ok)toast(r.error,true);return r};
    const openCopy=async i=>{const msg=$(`[data-msg="${i}"]`).value;try{await navigator.clipboard.writeText(msg)}catch(e){}
      await upd(i,{message:msg});const r=await post("/api/plus/openurl",{url:qd.rows[i].url});
      toast(r.ok?"Opened in your browser - the message is copied, paste it there":(r.error||"Could not open it"),!r.ok)};
    $$("[data-msg]").forEach(t=>t.onchange=()=>upd(+t.dataset.msg,{message:t.value}));
    $$("[data-open]").forEach(a=>a.onclick=e=>{e.preventDefault();openCopy(+a.dataset.open)});
    $$("[data-go]").forEach(b=>b.onclick=()=>openCopy(+b.dataset.go));
    $$("[data-si]").forEach(b=>b.onclick=async()=>{await upd(+b.dataset.si,{status:b.dataset.st});q.list=null;queue(ch)});
    $$("[data-ai]").forEach(b=>b.onclick=async()=>{const i=+b.dataset.ai;b.textContent="Writing...";b.disabled=true;
      const r=await post("/api/plus/ai/draft",{channel:ch,fields:qd.rows[i].fields,instruction:qd.instruction||"",template:qd.template||""});
      if(!r.ok){b.textContent="AI draft";b.disabled=false;return toast(r.error,true)}
      $(`[data-msg="${i}"]`).value=r.message;await upd(i,{message:r.message});b.textContent="AI draft";b.disabled=false;toast("Drafted - read it before you send")});
    $$("[data-read]").forEach(b=>b.onclick=async()=>{b.textContent="Reading...";const r=await post("/api/channel",{cmd:"reach-read",args:{url:qd.rows[+b.dataset.read].url}});
      b.textContent="Read thread";const w=document.createElement("div");w.className="qrow";w.style.display="block";
      w.innerHTML=`<pre class="code" style="max-height:320px;overflow:auto">${esc(r.ok?(r.text||JSON.stringify(r.result||r,null,1)):(r.error||"Agent Reach did not answer - set it up under Connections"))}</pre>`;
      b.closest(".qrow").after(w)});
    if($("#qnext"))$("#qnext").onclick=()=>{const n=qd.rows.findIndex(r=>r.status==="todo");if(n<0)return toast("Nothing left to do");openCopy(n)};
  }
  $$("[data-qt]").forEach(b=>b.onclick=()=>{q.tab=b.dataset.qt;queue(ch)});
}

/* ---------------- Video maker ---------------- */
let V={spec:{orientation:"16:9",language:"",model:"small",combine:false,chorus_label:"CHORUS"},media:null,picked:[]};
const LANGS=[["","detect"],["de","German"],["pt","Portuguese"],["sk","Slovak"],["cs","Czech"],["en","English"],["es","Spanish"],["fr","French"],["it","Italian"],["pl","Polish"],["hu","Hungarian"],["uk","Ukrainian"],["ru","Russian"],["nl","Dutch"]];
async function video(){
  const m=$("#main"),sp=V.spec;
  if(!V.media)V.media=await post("/api/plus/media");
  await pstate();
  const md=V.media,agileOn=P&&P.plus&&P.plus.agile_email;
  const list=(items,type,name)=>items.length?`<div class="pick">${items.map(x=>`<label><input type="${type}" name="${name}" value="${esc(x.path)}" ${type==="checkbox"?(V.picked.includes(x.path)?"checked":""):(sp[name]===x.path?"checked":"")}>${esc(x.name)}<small>${x.mb} MB</small></label>`).join("")}</div>`:`<small>None yet - drop them in the box above.</small>`;
  m.innerHTML=bar("Video maker",`<span class="note">Songs + lyrics in two languages + one picture = lyric videos</span><span class="sp"></span><button class="btn" id="vfold">Open the folder</button>`)
  +`<div class="body">
   <section class="box"><h2>1. Your files</h2>
    <div class="drop" id="vdrop">Drop songs, a background picture and lyrics (.txt) here, or click to choose them.<input type="file" id="vfile" multiple hidden accept="audio/*,image/*,.txt,.md"></div>
    <p class="note" style="margin-top:6px">They are copied into ${esc(md.inputs)}.</p>
    <div class="form" style="margin-top:10px">
     <label>Songs</label><div>${list(md.audio,"checkbox","audio")}<small>Tick one song, or several for an album.</small></div>
     <label>Picture</label><div>${list(md.images,"radio","image")}</div>
     <label>Lyrics</label><div>${list(md.lyrics,"radio","lyrics")}
      <details style="margin-top:6px"><summary>Paste the lyrics instead</summary><textarea id="vlt" style="min-height:160px;margin-top:6px" placeholder="# Song title&#10;Wie geht es Ihnen? || Ako sa máte?&#10;&#10;REFRAIN&#10;Sehr gut, danke || Veľmi dobre, ďakujem"></textarea>
       <div style="display:flex;gap:6px;margin-top:6px"><input type="text" id="vln" placeholder="file name" style="width:220px"><button class="btn" id="vls">Save as a lyrics file</button></div></details>
      <details style="margin-top:6px"><summary>How to write the lyrics file</summary><div class="note" style="margin-top:6px">
       Three ways, each works:<br>1. One line per sung line, original and translation separated by <code>||</code> (or a TAB).<br>
       2. The whole original, then a line <code>---</code>, then the translation line for line.<br>3. The original only - then fill "Translate into" below and Ollama Cloud writes the translation.<br>
       A line <code>[Chorus]</code>, <code>REFRAIN</code>, <code>REFRÃO</code> or <code>REFRÉN</code> marks the next stanza as the chorus. Several songs in one file: start each with <code># Title</code> - each recording is matched to its song by what is sung.</div></details></div>
    </div></section>
   <section class="box"><h2>2. How it looks</h2><div class="form">
     <label>Format</label><div class="checks"><label><input type="radio" name="vor" value="16:9" ${sp.orientation!=="9:16"?"checked":""}>16:9 YouTube</label><label><input type="radio" name="vor" value="9:16" ${sp.orientation==="9:16"?"checked":""}>9:16 Shorts, Reels, TikTok</label></div>
     <label for="vlang">Sung in</label><div><select id="vlang">${LANGS.map(([k,n])=>`<option value="${k}" ${sp.language===k?"selected":""}>${n}</option>`).join("")}</select></div>
     <label for="vtl">Language names</label><div style="display:flex;gap:6px"><input type="text" id="vtl" value="${esc(sp.top_label||"")}" placeholder="Deutsch" style="flex:1"><input type="text" id="vbl" value="${esc(sp.bottom_label||"")}" placeholder="Slovenčina" style="flex:1"></div>
     <label for="vti">Title</label><div><input type="text" id="vti" value="${esc(sp.title||"")}" placeholder="for one song; empty uses the # Title line or the file name" style="width:100%"></div>
     <label for="vsub">Subtitle</label><div><input type="text" id="vsub" value="${esc(sp.subtitle||"")}" placeholder="optional, e.g. the title in the second language" style="width:100%"></div>
     <label for="val">Album</label><div><input type="text" id="val" value="${esc(sp.album||"")}" placeholder="shown in the corner, e.g. Lieder vom Mandelbaum" style="width:100%"></div>
     <label for="vch">Chorus label</label><div><input type="text" id="vch" value="${esc(sp.chorus_label||"CHORUS")}" style="width:280px"></div>
     <label for="vtr">Translate into</label><div><input type="text" id="vtr" value="${esc(sp.translate_to||"")}" placeholder="only when the lyrics have no translation, e.g. Slovak" style="width:280px"></div>
     <label for="vmo">Listening model</label><div><select id="vmo">${["base","small","medium"].map(x=>`<option ${sp.model===x?"selected":""}>${x}</option>`).join("")}</select><small>small is the best balance; medium is slower but hears fast singing better.</small></div>
     <label>Album</label><div class="checks"><label><input type="checkbox" id="vcomb" ${sp.combine?"checked":""}>also join all songs into one album video with YouTube chapters</label></div>
     <label for="vtim">Fixed timing</label><div><select id="vtim"><option value="">- none: listen to the song -</option>${md.timings.map(t=>`<option value="${esc(t.path)}" ${sp.timing===t.path?"selected":""}>${esc(t.name)}</option>`).join("")}</select><small>Each video gets a .timing.csv. Fix a line's start time in it (Excel works), pick it here with its one song, and render again.</small></div>
     <div class="full"><button class="btn go" id="vgo">Make the video${V.picked.length>1?"s":""}</button> <span class="note">The first run installs what it needs and downloads the listening model once (a few hundred MB). A 4-minute song then takes about 3-6 minutes.</span></div>
   </div></section>
   <section class="box"><h2>3. Finished videos</h2>${md.outputs.length?`<div class="tools">${md.outputs.map(o=>`<div class="tool"><div><h3>${esc(o.name)}</h3><div class="file">${esc(o.when)} &middot; ${o.mb} MB</div></div><div></div>
     <div class="acts"><button class="btn" data-vo="${esc(o.path)}">Show</button>${agileOn?`<button class="btn" data-va="${esc(o.path)}" ${o.mb>24?`title="Too big for email - over 24 MB"`:""}>Send to Agile Assets</button>`:""}</div></div>`).join("")}</div>`
     :`<div class="empty"><b>No video yet</b></div>`}${agileOn?"":`<p class="note">Connect Agile Assets on its page to send finished videos there as drafts.</p>`}</section></div>`;
  const keep=()=>{Object.assign(sp,{orientation:($("input[name=vor]:checked")||{}).value||"16:9",language:$("#vlang").value,top_label:$("#vtl").value,
    bottom_label:$("#vbl").value,title:$("#vti").value,subtitle:$("#vsub").value,album:$("#val").value,chorus_label:$("#vch").value,translate_to:$("#vtr").value,
    model:$("#vmo").value,combine:$("#vcomb").checked,timing:$("#vtim").value,image:($("input[name=image]:checked")||{}).value||"",
    lyrics:($("input[name=lyrics]:checked")||{}).value||""});V.picked=$$("input[name=audio]:checked").map(x=>x.value)};
  $$("#main input,#main select").forEach(el=>el.addEventListener("change",keep));
  const drop=$("#vdrop"),inp=$("#vfile");
  const upload=async files=>{keep();for(const f of files){toast("Uploading "+f.name);
      const r=await fetch("/api/plus/upload?"+new URLSearchParams({name:f.name,t:T}),{method:"POST",body:f}).then(x=>x.json());
      if(!r.ok){toast(r.error||"Upload failed",true);continue}
      if(/\.(mp3|wav|m4a|flac|ogg|aac)$/i.test(f.name)&&!V.picked.includes(r.path))V.picked.push(r.path);
      if(/\.(jpe?g|png|webp|avif|bmp)$/i.test(f.name))sp.image=r.path;if(/\.(txt|md|lrc)$/i.test(f.name))sp.lyrics=r.path}
    V.media=null;video()};
  drop.onclick=()=>inp.click();inp.onchange=()=>upload([...inp.files]);
  drop.ondragover=e=>{e.preventDefault();drop.classList.add("over")};drop.ondragleave=()=>drop.classList.remove("over");
  drop.ondrop=e=>{e.preventDefault();drop.classList.remove("over");upload([...e.dataTransfer.files])};
  $("#vls").onclick=async()=>{const r=await post("/api/plus/video/lyrics",{text:$("#vlt").value,name:$("#vln").value});if(!r.ok)return toast(r.error,true);keep();sp.lyrics=r.path;V.media=null;toast("Saved");video()};
  $("#vfold").onclick=()=>post("/api/open",{path:"VIDEO_STUDIO/output"});
  $$("[data-vo]").forEach(b=>b.onclick=()=>post("/api/open",{path:b.dataset.vo}));
  $$("[data-va]").forEach(b=>b.onclick=()=>{V.agileFile=b.dataset.va;go("agile")});
  $("#vgo").onclick=async()=>{keep();if(!V.picked.length)return toast("Tick at least one song",true);
    const spec={...sp,audio:V.picked};if(!spec.timing)delete spec.timing;else if(V.picked.length>1)return toast("A fixed timing works with one song at a time",true);
    jobGo(await post("/api/plus/video/run",{spec}))};
}

/* ---------------- Agile Assets ---------------- */
async function agile(){
  await pstate(true);const st=P.plus,bs=await boxes(),m=$("#main");
  if(!V.media)V.media=await post("/api/plus/media");
  m.innerHTML=bar("Agile Assets",`<span class="sp"></span><button class="btn" id="aga">Open Agile Assets</button>`)+`<div class="body">
   <section class="box"><h2>Connection</h2>
    <p>Agile Assets has no public API, webhooks or Zapier app. What it has is an email inbox per account: anything you email there arrives as a draft, ready to schedule.
    So Lead Studio connects by email - it sends the video or picture as an attachment from one of your mailboxes, with your caption as the text.
    Email stops at about 25 MB, so this suits Shorts and Reels (9:16, under a minute). For a longer video, drag it into Agile Assets in the browser.</p>
    <div class="form"><label for="ae">Agile Assets inbox</label><div><input type="text" id="ae" value="${esc(st.agile_email)}" placeholder="the address shown in your Agile Assets account" style="width:100%"><small>Find it in your Agile Assets account (the email-to-draft address).</small></div>
     <label for="ab">Send from</label><div><select id="ab"><option value="">- pick a mailbox -</option>${bs.map(b=>`<option ${b.name===st.agile_mailbox?"selected":""}>${esc(b.name)}</option>`).join("")}</select>${bs.length?"":`<small>Add a mailbox under Senders first.</small>`}</div>
     <div class="full"><button class="btn go" id="as">Save</button></div></div></section>
   <section class="box"><h2>Send a file as a draft</h2><div class="form">
     <label for="af">File</label><div><select id="af">${V.media.outputs.map(o=>`<option value="${esc(o.path)}" ${o.path===V.agileFile?"selected":""}>${esc(o.name)} (${o.mb} MB)</option>`).join("")}</select>${V.media.outputs.length?"":`<small>Make a video first, or type a path below.</small>`}
      <input type="text" id="afp" placeholder="or any picture or video in the workspace" style="width:100%;margin-top:6px"></div>
     <label for="asu">Title</label><div><input type="text" id="asu" placeholder="the draft's title (email subject)" style="width:100%"></div>
     <label for="aca">Caption</label><div><textarea id="aca" placeholder="The post text, hashtags included."></textarea></div>
     <div class="full"><button class="btn go" id="asend" ${st.agile_email&&st.agile_mailbox?"":"disabled"}>Send to Agile Assets</button></div></div></section></div>`;
  $("#aga").onclick=()=>post("/api/plus/openurl",{url:"https://agile-assets.ai"});
  $("#as").onclick=async()=>{const r=await post("/api/plus/settings",{agile_email:$("#ae").value,agile_mailbox:$("#ab").value});if(!r.ok)return toast(r.error,true);P.plus=r.plus;toast("Saved");agile()};
  $("#asend").onclick=async()=>{const f=$("#afp").value.trim()||$("#af").value;if(!f)return toast("Pick a file",true);
    if(!confirm("Email "+f.split("/").pop()+" to "+st.agile_email+" from "+st.agile_mailbox+"?"))return;
    const r=await post("/api/plus/agile/send",{file:f,subject:$("#asu").value,caption:$("#aca").value});
    toast(r.ok?"Sent - it shows up as a draft in Agile Assets in a minute":r.error,!r.ok)};
}

/* ---------------- SEO ---------------- */
let SE={q:"",res:null,stat:null};
async function seo(){
  await pstate();const m=$("#main");
  if(!SE.stat)SE.stat=await post("/api/plus/seo/status");
  const st=SE.stat||{};
  m.innerHTML=bar("Article library",`<span class="note">${st.documents?nfmt(st.documents)+" documents, "+nfmt(st.words)+" words":"not indexed yet"}</span>`)+`<div class="body">
   <section class="box"><h2>Your collection</h2><p>Point Lead Studio at the folders that hold your texts and articles (.txt, .md, .html, .docx; .pdf once <code>pypdf</code> is installed). It builds a full-text index on this computer; running it again only reads new or changed files.</p>
    <div class="form"><label for="sd">Folders</label><div><textarea id="sd" placeholder="D:\\Articles&#10;C:\\Users\\admin\\Documents\\Texts">${esc((P.plus.seo_dirs||"").split(";").join("\n"))}</textarea><small>One folder per line - full paths, anywhere on this computer.</small></div>
    <div class="full"><button class="btn go" id="si">Index them</button> <button class="btn" id="spdf">Install PDF reading</button></div></div></section>
   <section class="box"><h2>Search the library</h2><div style="display:flex;gap:6px"><input type="text" id="sq" value="${esc(SE.q)}" placeholder="a topic or keyword" style="flex:1"><button class="btn go" id="ss">Search</button></div>
    ${SE.res?(SE.res.length?SE.res.map(h=>`<div class="hit"><b>${esc(h.title)}</b><small>${esc(h.path)}</small><p>${esc(h.snippet).replace(/\[(.+?)\]/g,"<mark>$1</mark>")}</p></div>`).join("")
      +`<div style="margin-top:10px"><button class="btn go" id="sg">Generate ideas from these</button></div>`:`<p class="note">Nothing found.</p>`):""}</section></div>`;
  $("#si").onclick=async()=>jobGo(await post("/api/plus/seo/index",{dirs:$("#sd").value.split(/\n+/).map(x=>x.trim()).filter(Boolean).join(";")}));
  $("#spdf").onclick=async()=>jobGo(await post("/api/pip",{mods:["pypdf"]}));
  const s=async()=>{SE.q=$("#sq").value.trim();if(!SE.q)return;const r=await post("/api/plus/seo/search",{q:SE.q,limit:15});if(!r.ok)return toast(r.error,true);SE.res=r.results;seo()};
  $("#ss").onclick=s;$("#sq").onkeydown=e=>{if(e.key==="Enter")s()};
  if($("#sg"))$("#sg").onclick=()=>{SI.topic=SE.q;go("seoideas")};
}
let SI={topic:"",mode:"ideas",lang:"",extra:"",lib:true,res:null,busy:false};
const MODE_N={ideas:"Article ideas",titles:"Titles and meta descriptions",outline:"Article outline",keywords:"Keyword map",article:"Full article draft",rewrite:"Rewrite my notes into a new article"};
async function seoideas(){
  await pstate();await models();const m=$("#main"),cur=P.plus.seo_model||(S.settings||{}).ollama_model||"";
  m.innerHTML=bar("Idea generator",`<span class="note">Ollama Cloud, fed with passages from your own library</span>`)+`<div class="body">
   <section class="box"><div class="form">
    <label for="it">Topic</label><div><input type="text" id="it" value="${esc(SI.topic)}" placeholder="e.g. vejárové brány cena, ERP pre výrobu, ad attribution" style="width:100%"></div>
    <label for="im">What to write</label><div><select id="im">${Object.entries(MODE_N).map(([k,n])=>`<option value="${k}" ${SI.mode===k?"selected":""}>${n}</option>`).join("")}</select></div>
    <label for="il">Language</label><div><input type="text" id="il" value="${esc(SI.lang)}" placeholder="e.g. Slovak - empty answers in the topic's language" style="width:280px"></div>
    <label for="ix">Direction</label><div><textarea id="ix" placeholder="Optional: audience, product to mention, tone, competitors to beat.">${esc(SI.extra)}</textarea></div>
    <label for="imd">Model</label><div>${modelSel("imd",cur)}</div>
    <label>Library</label><div class="checks"><label><input type="checkbox" id="ilib" ${SI.lib?"checked":""}>use the 6 best passages from my article library</label></div>
    <div class="full"><button class="btn go" id="ig">${SI.busy?"Writing...":"Generate"}</button> <span class="note">Each result is also saved as a Markdown file in SEO_IDEAS.</span></div></div></section>
   ${SI.res?`<section class="box"><div style="display:flex;gap:8px;align-items:center;margin-bottom:8px"><h2 style="margin:0">Result</h2><span class="sp"></span><button class="btn" id="icp">Copy</button><button class="btn" id="iop">Show the file</button></div>
     <div class="md">${fmtMd(SI.res.text)}</div>${SI.res.sources&&SI.res.sources.length?`<p class="note" style="margin-top:12px">From your library: ${SI.res.sources.map(s=>esc(s.title)).join(" &middot; ")}</p>`:""}</section>`:""}</div>`;
  $("#ig").onclick=async()=>{Object.assign(SI,{topic:$("#it").value,mode:$("#im").value,lang:$("#il").value,extra:$("#ix").value,lib:$("#ilib").checked});
    if(!SI.topic.trim())return toast("Write a topic",true);SI.busy=true;seoideas();
    const r=await post("/api/plus/seo/ideas",{topic:SI.topic,mode:SI.mode,language:SI.lang,extra:SI.extra,use_library:SI.lib,model:$("#imd").value});
    SI.busy=false;if(!r.ok){toast(r.error,true);return seoideas()}SI.res=r;seoideas()};
  if($("#icp"))$("#icp").onclick=()=>navigator.clipboard.writeText(SI.res.text).then(()=>toast("Copied"));
  if($("#iop"))$("#iop").onclick=()=>post("/api/open",{path:SI.res.file});
}
async function seomodels(){
  await pstate();MODELS=null;const ms=await models(),st=S.settings||{},cur=P.plus.seo_model||st.ollama_model||"",m=$("#main");
  m.innerHTML=bar("Ollama models",`<span class="sp"></span><button class="btn" data-v="conn">Key and address: Connections</button>`)+`<div class="body">
   <section class="box"><h2>Ollama Cloud</h2><p>Ollama runs large open models (gpt-oss, DeepSeek, Qwen, Kimi and others) on its own servers. A free account and API key get you these models with an hourly and daily usage allowance; when it is used up, Ollama answers "limit reached" until it resets. The list below is what your key can use right now, straight from Ollama.
    ${st.ollama_has_key?`<b>Key saved</b> (${esc(st.ollama_key_hint||"")}).`:`<b>No key yet</b> - create one at ollama.com/settings/keys and paste it under Connections.`}</p>
    ${ms.length?`<div class="tools">${ms.map(x=>`<div class="tool"><div><h3>${esc(x)} ${x===cur?`<span class="tag ok">SEO default</span>`:""}${x===st.ollama_model?` <span class="tag">AI enrich default</span>`:""}</h3></div><div></div>
      <div class="acts"><button class="btn" data-mt="${esc(x)}">Test</button><button class="btn ${x===cur?"":"go"}" data-mu="${esc(x)}" ${x===cur?"disabled":""}>Use for SEO</button></div></div>`).join("")}</div>`
     :`<div class="empty"><b>${esc(ms.err||"No models listed")}</b></div>`}</section></div>`;
  $$("[data-v]").forEach(b=>b.onclick=()=>go(b.dataset.v));
  $$("[data-mu]").forEach(b=>b.onclick=async()=>{const r=await post("/api/plus/settings",{seo_model:b.dataset.mu});P.plus=r.plus;toast("SEO now uses "+b.dataset.mu);seomodels()});
  $$("[data-mt]").forEach(b=>b.onclick=async()=>{b.textContent="Testing...";const r=await post("/api/plus/ollama/test",{model:b.dataset.mt});b.textContent="Test";toast(r.ok?b.dataset.mt+" answered: "+r.text:r.error,!r.ok)});
}

/* ---------------- Ads connectors (MCP) ---------------- */
let A={edit:null,tools:{},open:null,tool:null,out:null};
async function ads(){
  await pstate(true);const st=P.plus,pre=P.presets,m=$("#main"),e=A.edit;
  const kv=o=>Object.entries(o||{}).map(([k,v])=>k+"="+v).join("\n");
  const t=A.open&&A.tools[A.open];
  m.innerHTML=bar("Ads connectors",`<span class="note">HYROS and other ad platforms through MCP</span><span class="sp"></span>
    <select id="apre"><option value="">+ Add a connector</option><option value="hyros">HYROS</option><option value="http">Any remote MCP server (URL)</option><option value="stdio">Any MCP server started by a command</option></select>`)+`<div class="body">
   <p class="lead">MCP is the standard way AI tools talk to apps. Each connector here is one MCP server; Lead Studio connects, lists what it can do, and runs a tool when you ask. Tools that create, change or delete data ask before they run.</p>
   ${e?`<section class="box"><h2>${e.name?"Edit "+esc(e.name):"New connector"}</h2><p class="note">${esc((pre[e.preset]||{}).help||"")}</p><div class="form">
     <label for="xn">Name</label><div><input type="text" id="xn" value="${esc(e.name||"")}" style="width:280px"></div>
     ${e.kind==="http"?`<label for="xu">Server URL</label><div><input type="text" id="xu" value="${esc(e.url||"")}" placeholder="https://.../mcp" style="width:100%"></div>
       <label for="xh">Headers</label><div><textarea id="xh" placeholder="Authorization=Bearer your-token">${esc(kv(e.headers))}</textarea><small>One per line, Name=value. A saved value shows as ...last4 - leave it to keep it.</small></div>`
     :`<label for="xc">Command</label><div><input type="text" id="xc" value="${esc(e.command||"")}" placeholder="npx" style="width:200px"><input type="text" id="xa" value="${esc((e.args||[]).join(" "))}" placeholder="-y hyros-mcp" style="width:300px;margin-left:6px"><small>${e.preset==="hyros"?"Needs Node.js (nodejs.org). The first connect downloads the package, so give it a minute.":""}</small></div>
       <label for="xe">Settings</label><div><textarea id="xe" placeholder="HYROS_API_KEY=...">${esc(kv(e.env))}</textarea><small>Environment values, one per line, Name=value. A saved value shows as ...last4 - leave it to keep it.</small></div>`}
     <div class="full"><button class="btn go" id="xs">Save</button><button class="btn" id="xx">Cancel</button></div></div></section>`:""}
   ${st.ads_servers.length?`<div class="tools">${st.ads_servers.map(x=>`<div class="tool"><div><h3>${esc(x.name)} <span class="tag">${x.kind==="http"?"remote":"on this PC"}</span></h3><div class="file">${esc(x.kind==="http"?x.url:x.command+" "+(x.args||[]).join(" "))}</div></div><div>${A.tools[x.name]?`<span class="tag ok">${A.tools[x.name].length} tools</span>`:""}</div>
     <div class="acts"><button class="btn go" data-xt="${esc(x.name)}">Connect</button><button class="btn" data-xe="${esc(x.name)}">Edit</button><button class="btn" data-xd="${esc(x.name)}">Remove</button></div></div>`).join("")}</div>`
    :e?"":`<div class="empty"><b>No connector yet</b>Pick "HYROS" in the menu above. It runs the open-source hyros-mcp server on this computer with your HYROS API key, so no data goes through anyone else.</div>`}
   ${t?`<section class="box" style="margin-top:14px"><h2>${esc(A.open)}: ${t.length} tools</h2><div class="form">
     <label for="xtool">Tool</label><div><select id="xtool">${t.map(x=>`<option ${x.name===A.tool?"selected":""}>${esc(x.name)}</option>`).join("")}</select><small id="xdesc"></small></div>
     <label for="xargs">Arguments</label><div><textarea id="xargs" style="min-height:120px;font:12px var(--mono)"></textarea><small>JSON. Filled with the tool's fields - remove what you do not need.</small></div>
     <div class="full"><button class="btn go" id="xrun">Run it</button></div></div>
     ${A.out?`<pre class="code" style="margin-top:12px;max-height:480px;overflow:auto">${esc(A.out)}</pre>`:""}</section>`:""}</div>`;
  $("#apre").onchange=ev=>{const p=pre[ev.target.value];if(!p)return;A.edit=JSON.parse(JSON.stringify(p));ads()};
  if($("#xx"))$("#xx").onclick=()=>{A.edit=null;ads()};
  const parse=s=>Object.fromEntries((s||"").split(/\n+/).map(l=>l.trim()).filter(l=>l.includes("=")).map(l=>[l.slice(0,l.indexOf("=")).trim(),l.slice(l.indexOf("=")+1).trim()]));
  if($("#xs"))$("#xs").onclick=async()=>{const x={name:$("#xn").value,kind:e.kind,preset:e.preset};
    if(e.kind==="http"){x.url=$("#xu").value;x.headers=parse($("#xh").value)}else{x.command=$("#xc").value;x.args=$("#xa").value.split(/\s+/).filter(Boolean);x.env=parse($("#xe").value)}
    const r=await post("/api/plus/ads/save",{server:x});if(!r.ok)return toast(r.error,true);P.plus=r.plus;A.edit=null;toast("Saved - press Connect");ads()};
  $$("[data-xe]").forEach(b=>b.onclick=()=>{A.edit=JSON.parse(JSON.stringify(st.ads_servers.find(x=>x.name===b.dataset.xe)));ads()});
  $$("[data-xd]").forEach(b=>b.onclick=async()=>{if(!confirm("Remove "+b.dataset.xd+"?"))return;const r=await post("/api/plus/ads/delete",{name:b.dataset.xd});P.plus=r.plus;if(A.open===b.dataset.xd)A.open=null;ads()});
  $$("[data-xt]").forEach(b=>b.onclick=async()=>{b.textContent="Connecting...";b.disabled=true;const r=await post("/api/plus/ads/tools",{name:b.dataset.xt});
    if(!r.ok){toast(r.error,true);b.textContent="Connect";b.disabled=false;return}A.tools[b.dataset.xt]=r.tools;A.open=b.dataset.xt;A.tool=(r.tools[0]||{}).name;A.out=null;ads()});
  if(t){const fill=()=>{const tl=t.find(x=>x.name===$("#xtool").value)||{};A.tool=tl.name;$("#xdesc").textContent=tl.description||"";
      const props=(tl.schema||{}).properties||{},req=(tl.schema||{}).required||[];
      const ex=Object.fromEntries(Object.entries(props).map(([k,v])=>[k,v.default!==undefined?v.default:v.type==="integer"||v.type==="number"?0:v.type==="boolean"?false:v.type==="array"?[]:v.type==="object"?{}:""]));
      $("#xargs").value=JSON.stringify(ex,null,1);$("#xargs").title=req.length?"Required: "+req.join(", "):""};
    fill();$("#xtool").onchange=fill;
    $("#xrun").onclick=async()=>{let args;try{args=JSON.parse($("#xargs").value||"{}")}catch(x){return toast("The arguments are not valid JSON",true)}
      if(/create|update|delete|refund|remove|set_|send|publish|pause|enable|disable/i.test(A.tool)&&!confirm(A.tool+" changes data in "+A.open+". Run it?"))return;
      $("#xrun").textContent="Running...";const r=await post("/api/plus/ads/call",{name:A.open,tool:A.tool,args});
      A.out=r.ok?(r.text||JSON.stringify(r.structured||{},null,1))+(r.is_error?"\n\n(the tool reported an error)":""):"Error: "+r.error;ads()}}
}

window.PLUS={unibox,video,agile,seo,seoideas,seomodels,ads,queue};
})();
