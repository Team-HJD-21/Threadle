// Exercise the shipped browser clock with deterministic time, without a browser dependency.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');

function playback(reduced = false) {
  let now = 0, serial = 0, observer;
  const timers = new Map();
  const element = () => ({
    hidden: false, attrs: {}, events: {}, dataset: {play:'Play', pause:'Pause'},
    classList: {toggle() {}},
    setAttribute(key, value) {this.attrs[key] = value;},
    addEventListener(name, fn) {this.events[name] = fn;},
    focus() {},
  });
  const tabs = Array.from({length:4}, element);
  const panels = tabs.map(() => ({...element(), matches:() => false,
    querySelector: selector => selector === '.preview-case' ? element() : null,
    querySelectorAll: () => [],
  }));
  const pause = element(), replay = element();
  const root = {...element(),
    querySelectorAll: selector => selector === '[role="tab"]' ? tabs : selector === '[role="tabpanel"]' ? panels : [],
    querySelector: selector => selector === '[data-feature-pause]' ? pause : selector === '[data-feature-replay]' ? replay : element(),
  };
  const document = {...element(), hidden:false,
    querySelectorAll: selector => selector === '[data-feature-showcase]' ? [root] : [],
  };
  const motion = {...element(), matches:reduced};
  class Observer {
    constructor(fn) {observer = fn;}
    observe() {}
  }
  const context = {document, matchMedia:() => motion, performance:{now:() => now},
    window:{IntersectionObserver:Observer}, IntersectionObserver:Observer,
    setTimeout(fn, delay) {const id=++serial;timers.set(id,{at:now+delay,fn});return id;},
    clearTimeout(id) {timers.delete(id);},
  };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../web_assets/preview.js'),'utf8'),context);
  function advance(ms) {
    const end = now+ms;
    while (true) {
      const entry = [...timers].sort((a,b) => a[1].at-b[1].at)[0];
      if (!entry || entry[1].at>end) break;
      now=entry[1].at;timers.delete(entry[0]);entry[1].fn();
    }
    now=end;
  }
  return {advance, tabs, panels, pause, replay,
    selected:() => tabs.findIndex(tab => tab.attrs['aria-selected']==='true'),
    visible(value) {observer([{intersectionRatio:value?1:0}]);},
    hidden(value) {document.hidden=value;document.events.visibilitychange();},
  };
}

test('all four scenes finish, cycle in order, and wrap without moving focus', () => {
  const p=playback();p.visible(true);
  for (const next of [1,2,3,0,1]) {
    p.advance(20900);assert.equal(p.selected(),(next+3)%4);
    p.advance(100);assert.equal(p.selected(),next);
    assert.equal(p.panels.filter(panel=>!panel.hidden).length,1);
  }
});

test('pause and replay retain control of the cycle', () => {
  const p=playback();p.visible(true);p.advance(10000);
  p.pause.events.click();p.advance(60000);assert.equal(p.selected(),0);
  p.pause.events.click();p.advance(11000);assert.equal(p.selected(),1);
  p.advance(10000);p.replay.events.click();p.advance(11000);assert.equal(p.selected(),1);
  p.advance(10000);assert.equal(p.selected(),2);
});

test('manual selection starts that scene and continues from it', () => {
  const p=playback();p.visible(true);p.advance(19000);
  p.tabs[2].events.click();p.advance(20900);assert.equal(p.selected(),2);
  p.advance(100);assert.equal(p.selected(),3);
});

test('offscreen and hidden documents do not consume the scene time', () => {
  const p=playback();p.advance(60000);assert.equal(p.selected(),0);
  p.visible(true);p.advance(10000);p.visible(false);p.advance(60000);
  p.visible(true);p.advance(10900);assert.equal(p.selected(),0);
  p.advance(100);assert.equal(p.selected(),1);
  p.hidden(true);p.advance(60000);assert.equal(p.selected(),1);
  p.hidden(false);p.advance(21000);assert.equal(p.selected(),2);
});

test('reduced motion keeps manual examples without automatic rotation', () => {
  const p=playback(true);p.visible(true);p.advance(120000);assert.equal(p.selected(),0);
  p.tabs[3].events.click();p.advance(120000);assert.equal(p.selected(),3);
});
