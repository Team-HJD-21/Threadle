(() => {
  'use strict';
  const widgets = new Set();
  let opened = null, serial = 0;
  function close(focus = false) {
    if (!opened) return;
    const item = opened;
    item.menu.hidden = true;
    item.button.setAttribute('aria-expanded', 'false');
    item.button.removeAttribute('aria-activedescendant');
    opened = null;
    if (focus) item.button.focus({preventScroll:true});
  }
  function position(item) {
    const box = item.button.getBoundingClientRect();
    const view = window.visualViewport;
    const top = view?.offsetTop || 0, height = view?.height || innerHeight;
    const below = top + height - box.bottom - 12, above = box.top - top - 12;
    const upward = below < 180 && above > below;
    const room = Math.max(42, Math.min(320, upward ? above : below));
    Object.assign(item.menu.style, {width:Math.min(box.width,innerWidth-16)+'px',left:Math.max(8,Math.min(box.left,innerWidth-box.width-8))+'px',maxHeight:room+'px',top:'0px'});
    item.menu.style.top = (upward ? box.top-item.menu.offsetHeight-6 : box.bottom+6)+'px';
  }
  function active(item,index) {
    item.index = index;
    item.rows.forEach((row,i)=>row.classList.toggle('is-active',i===index));
    const row = item.rows[index];
    if (row) {
      item.button.setAttribute('aria-activedescendant',row.id);
      const rowBox=row.getBoundingClientRect(), menuBox=item.menu.getBoundingClientRect();
      if(rowBox.top<menuBox.top+5) item.menu.scrollTop-=menuBox.top+5-rowBox.top;
      else if(rowBox.bottom>menuBox.bottom-5) item.menu.scrollTop+=rowBox.bottom-menuBox.bottom+5;
    }
  }
  function choose(item,index) {
    const option = item.select.options[index];
    if (!option || option.disabled || option.parentElement.disabled) return;
    item.select.selectedIndex = index;
    refresh(item);
    close(true);
    item.select.dispatchEvent(new Event('input',{bubbles:true}));
    item.select.dispatchEvent(new Event('change',{bubbles:true}));
  }
  function refresh(item) {
    item.value.textContent = item.select.selectedOptions[0]?.textContent || '—';
    const disabled = item.select.matches(':disabled');
    if(item.button.disabled !== disabled) item.button.disabled = disabled;
    item.button.setAttribute('aria-required',String(item.select.required));
  }
  function show(item) {
    close();
    if (item.button.disabled) return;
    item.menu.replaceChildren();
    item.rows = [];
    let group = null;
    [...item.select.options].forEach((option,index)=>{
      if (option.parentElement.tagName==='OPTGROUP' && group!==option.parentElement) {
        group=option.parentElement;
        const heading=document.createElement('li');heading.className='threadle-select-group';heading.setAttribute('role','presentation');heading.textContent=group.label;item.menu.append(heading);
      }
      const row=document.createElement('li');row.className='threadle-select-option';row.id=item.menu.id+'-'+index;
      row.setAttribute('role','option');row.setAttribute('aria-selected',String(option.selected));
      row.setAttribute('aria-disabled',String(option.disabled || !!option.parentElement.disabled));
      row.textContent=option.textContent;
      row.hidden=option.hidden;
      row.addEventListener('pointermove',()=>{if(row.getAttribute('aria-disabled')==='false') active(item,index)});
      row.addEventListener('pointerdown',event=>event.preventDefault());
      row.addEventListener('click',()=>choose(item,index));
      item.menu.append(row);item.rows.push(row);
    });
    opened=item;item.menu.hidden=false;item.button.setAttribute('aria-expanded','true');position(item);
    active(item,item.select.selectedIndex);
  }
  function enhance(select) {
    if(select.dataset.threadleSelect || select.multiple || select.size>1) return;
    const wrapper=document.createElement('div');wrapper.className='threadle-select';
    const button=document.createElement('button');button.type='button';button.className='threadle-select-button';button.id='threadle-select-'+(++serial);button.setAttribute('role','combobox');button.setAttribute('aria-haspopup','listbox');button.setAttribute('aria-expanded','false');
    const value=document.createElement('span');value.className='threadle-select-value';value.id=button.id+'-value';button.append(value);
    const menu=document.createElement('ul');menu.className='threadle-select-menu';menu.id=button.id+'-menu';menu.hidden=true;menu.setAttribute('role','listbox');
    button.setAttribute('aria-controls',menu.id);
    const labels=[...select.labels];
    labels.forEach((label,i)=>{if(!label.id)label.id=button.id+'-label-'+i;label.htmlFor=button.id});
    if(labels.length)button.setAttribute('aria-labelledby',labels.map(label=>label.id).concat(value.id).join(' '));
    else button.setAttribute('aria-label',select.getAttribute('aria-label') || select.name || 'Select');
    if(select.hasAttribute('aria-describedby'))button.setAttribute('aria-describedby',select.getAttribute('aria-describedby'));
    select.before(wrapper);wrapper.append(select,button);document.body.append(menu);
    select.dataset.threadleSelect='true';select.classList.add('threadle-select-native');select.tabIndex=-1;select.setAttribute('aria-hidden','true');
    const item={select,wrapper,button,value,menu,index:-1,rows:[],typed:'',typedAt:0};widgets.add(item);refresh(item);
    button.addEventListener('click',()=>opened===item?close():show(item));
    button.addEventListener('keydown',event=>{
      const key=event.key;
      if(key==='Tab'){close();return}
      if(key==='Escape'){if(opened===item){event.preventDefault();close(true)}return}
      if(key==='Enter'||key===' '){event.preventDefault();if(opened===item)choose(item,item.index);else show(item);return}
      if(['ArrowDown','ArrowUp','Home','End'].includes(key)){
        event.preventDefault();if(opened!==item)show(item);
        const enabled=item.rows.map((row,i)=>!row.hidden&&row.getAttribute('aria-disabled')==='false'?i:-1).filter(i=>i>=0);
        const offset=enabled.indexOf(item.index);
        const next=key==='Home'?enabled[0]:key==='End'?enabled.at(-1):enabled[Math.max(0,Math.min(enabled.length-1,offset+(key==='ArrowDown'?1:-1)))];
        if(next!==undefined)active(item,next);return;
      }
      if(key.length===1&&!event.ctrlKey&&!event.metaKey&&!event.altKey){
        event.preventDefault();if(opened!==item)show(item);
        const now=Date.now();item.typed=now-item.typedAt>700?key:item.typed+key;item.typedAt=now;
        const index=[...select.options].findIndex((option,i)=>!option.hidden&&item.rows[i].getAttribute('aria-disabled')==='false'&&option.textContent.trim().toLocaleLowerCase().startsWith(item.typed.toLocaleLowerCase()));
        if(index>=0)active(item,index);
      }
    });
    select.addEventListener('change',()=>{refresh(item);if(opened===item)close()});
    select.addEventListener('invalid',event=>{event.preventDefault();button.focus();button.setAttribute('aria-invalid','true')});
    select.addEventListener('change',()=>button.removeAttribute('aria-invalid'));
  }
  function scan(root=document) {
    if(root instanceof HTMLSelectElement)enhance(root);
    root.querySelectorAll?.('select').forEach(enhance);
  }
  scan();
  document.addEventListener('pointerdown',event=>{if(opened&&!opened.menu.contains(event.target)&&!opened.wrapper.contains(event.target))close()});
  document.addEventListener('focusin',event=>{if(opened&&!opened.wrapper.contains(event.target)&&!opened.menu.contains(event.target))close()});
  document.addEventListener('reset',()=>setTimeout(()=>{close();widgets.forEach(refresh)},0));
  addEventListener('resize',()=>{if(opened)position(opened)});
  addEventListener('scroll',event=>{if(opened&&!opened.menu.contains(event.target))position(opened)},true);
  window.visualViewport?.addEventListener('resize',()=>{if(opened)position(opened)});
  new MutationObserver(records=>{
    for(const record of records){
      if(record.type==='childList')record.addedNodes.forEach(scan);
      if(record.type==='attributes' || record.target.closest?.('select')) {
        widgets.forEach(item=>{refresh(item);if(opened===item)close()});
      }
    }
    widgets.forEach(item=>{if(!item.select.isConnected){if(opened===item)close();item.menu.remove();widgets.delete(item)}});
  }).observe(document.body,{childList:true,subtree:true,attributes:true,attributeFilter:['disabled','selected','required']});
})();
