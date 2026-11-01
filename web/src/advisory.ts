export type Recommendation = {
  engine:string;label:string;summary:string;
  findings:{heading:string;detail:string;priority:'high'|'medium'|'low';evidence_ids:string[]}[];
  watch_items:string[];constraints_acknowledged:string[];verification_needed:string[];known_missing_data?:string[];
  evidence:{id:string;label:string;kind:string;value?:unknown}[];
  sources:{id:string;url:string}[];
};

function node<K extends keyof HTMLElementTagNameMap>(tag:K,className='',text=''):HTMLElementTagNameMap[K]{const element=document.createElement(tag);element.className=className;element.textContent=text;return element;}

/** Source text and model prose always enter the DOM as text, never HTML. */
export function renderAdvisory(host:HTMLElement,value:Recommendation):void{
  host.replaceChildren(node('p','area-evidence-label',value.label),node('p','advisory-summary',value.summary));
  const evidence=node('details','advisory-evidence');evidence.append(node('summary','','Evidence and citations'));
  const entries=new Map<string,HTMLElement>();const list=node('ul');
  for(const item of value.evidence){
    const row=node('li','advisory-evidence-entry');row.tabIndex=-1;
    row.append(node('strong','',item.label),node('small','',` ${item.id} · ${item.kind.replaceAll('_',' ')}`));
    if(item.value!==undefined)row.append(node('pre','advisory-evidence-data',JSON.stringify(item.value,null,2)));
    entries.set(item.id,row);list.append(row);
  }
  evidence.append(list);
  function citation(id:string):HTMLButtonElement{
    const button=node('button','advisory-citation',id);button.type='button';button.setAttribute('aria-label',`Show evidence ${id}`);
    button.onclick=()=>{const row=entries.get(id);if(!row)return;evidence.open=true;for(const entry of entries.values())entry.classList.remove('advisory-highlight');row.classList.add('advisory-highlight');row.scrollIntoView({block:'nearest',behavior:'smooth'});row.focus({preventScroll:true});};
    return button;
  }
  const findings=node('div','area-priorities');
  for(const item of value.findings){
    const card=node('article','area-priority');const heading=node('div','advisory-heading');
    heading.append(node('strong','',item.heading),node('span',`advisory-priority ${item.priority}`,item.priority));
    const detail=node('p');
    for(const part of item.detail.split(/(\[[a-zA-Z][a-zA-Z0-9_-]*\])/g)){
      const id=part.slice(1,-1);detail.append(part.startsWith('[')&&item.evidence_ids.includes(id)?citation(id):document.createTextNode(part));
    }
    const chips=node('div','advisory-citations');for(const id of item.evidence_ids)if(entries.has(id))chips.append(citation(id));
    card.append(heading,detail,chips);findings.append(card);
  }
  host.append(findings);
  for(const [label,items] of [['Watch items',[...new Set([...(value.known_missing_data??[]),...value.watch_items])]],['Constraints acknowledged',value.constraints_acknowledged],['Verification needed',value.verification_needed]] as const){
    const details=node('details');details.append(node('summary','',label));const list=node('ul');for(const item of items)list.append(node('li','',item));details.append(list);host.append(details);
  }
  for(const source of value.sources){
    let url:URL;try{url=new URL(source.url,location.origin);}catch{continue;}
    if(url.username||url.password||!((url.protocol==='https:'&&!!url.hostname)||(url.origin===location.origin&&/^\/data\/[A-Za-z0-9_./-]+\.json$/.test(source.url))))continue;
    const link=node('a','advisory-source',source.id);link.href=url.href;link.target='_blank';link.rel='noopener noreferrer';evidence.append(link);
  }
  host.append(evidence,node('p','area-note','Human review required.'));
}
