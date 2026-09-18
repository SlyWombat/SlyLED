// pixel_renderer.js — SlyLED Effect Spec v1 (#938)
//
// The canonical per-pixel effect renderer. This file is the SPEC: the Python
// twin (desktop/shared/pixel_renderer.py) must produce byte-identical output
// for every input, gated by tests/test_pixel_renderer_parity.py running this
// file under Node against tests/fixtures/pixel_corpus/.
//
// Extracted verbatim from emulation.js under #938 so both the browser and
// Node can load it. Keep it free of DOM / THREE references.
//
// The renderer is deliberately STATELESS and seekable — colour is a pure
// function of (type, params, pixel index, pixel count, elapsed ms). The
// firmware (main/ChildLED.cpp) is stateful for fire/comet/scanner and uses
// random8(); this approximates those deterministically so that baked .hseq
// output is reproducible and live streams can resume from go_epoch.
//
// If you change pixel maths here you MUST regenerate the corpus and update
// EFFECT_SPEC_VERSION in BOTH this file and pixel_renderer.py.

var EFFECT_SPEC_VERSION = 1;

// -- Per-pixel colour helpers (mirror firmware ChildLED.cpp) ----------------
function _hsvToRgb(h,s,v){
  // FastLED-style hsv2rgb_rainbow approximation (h,s,v: 0-255)
  h=h&0xFF;s=s&0xFF;v=v&0xFF;
  var inv=255-s, r,g,b;
  var sext=Math.floor(h/43), frac=(h-sext*43)*6;
  switch(sext){
    case 0: r=v;g=(v*((255-(s*(255-frac)>>8)))>>8);b=(v*inv>>8);break;
    case 1: r=(v*((255-(s*frac>>8)))>>8);g=v;b=(v*inv>>8);break;
    case 2: r=(v*inv>>8);g=v;b=(v*((255-(s*(255-frac)>>8)))>>8);break;
    case 3: r=(v*inv>>8);g=(v*((255-(s*frac>>8)))>>8);b=v;break;
    case 4: r=(v*((255-(s*(255-frac)>>8)))>>8);g=(v*inv>>8);b=v;break;
    default:r=v;g=(v*inv>>8);b=(v*((255-(s*frac>>8)))>>8);break;
  }
  return [Math.round(r),Math.round(g),Math.round(b)];
}
function _palColor(palId,idx){
  idx=idx&0xFF;
  switch(palId){
    default:
    case 0: return _hsvToRgb(idx,255,255);
    case 1: return _hsvToRgb(((idx>>1)+120)&0xFF,200,Math.min(255,160+(idx/3|0)));
    case 2: return _hsvToRgb((idx>>2)&0xFF,255,Math.min(255,200+Math.round(Math.sin(idx*Math.PI/128)*51)));
    case 3: return _hsvToRgb(((idx/3|0)+60)&0xFF,220,Math.min(255,100+Math.round(Math.sin(idx*Math.PI/128)*128)));
    case 4: return _hsvToRgb((idx*3)&0xFF,255,255);
    case 5:{var t=idx;if(t<85)return[t*3,0,0];if(t<170)return[255,(t-85)*3,0];return[255,255,(t-170)*3];}
    case 6: return _hsvToRgb(((idx>>1)+140)&0xFF,180,Math.min(255,180+(idx>>2)));
    case 7: return _hsvToRgb(idx,100,255);
  }
}
// Compute per-pixel RGB for a procedural action at pixel position i/N
// Returns [r,g,b] for the given dot, or null if not handled
function _emuPixel(pc,di,dotCount,elapsedMs){
  var t=pc.t,p=pc.p||{};
  var e=elapsedMs;
  if(t===5){// RAINBOW
    var spd=p.speedMs||50;if(spd<1)spd=1;
    var dir=p.direction||0;
    var palId=p.paletteId||0;
    var timeOff=Math.floor(e/spd)&0xFF;
    var idx=(dir===2||dir===3)?(dotCount-1-di):di;
    var hue=((idx*255/dotCount)|0)+timeOff;
    return _palColor(palId,hue&0xFF);
  }
  if(t===4){// CHASE
    var spd=p.speedMs||100;if(spd<1)spd=1;
    var spc=p.spacing||3;if(spc<2)spc=3;
    var dir=p.direction||0;
    var off=Math.floor(e/spd)%spc;
    var idx=(dir===2||dir===3)?(dotCount-1-di):di;
    return((idx+off)%spc===0)?[p.r||100,p.g||200,p.b||255]:[0,0,0];
  }
  if(t===7){// COMET
    var spd=p.speedMs||40;if(spd<1)spd=1;
    var tail=p.tailLen||10;if(tail<1)tail=10;
    var dir=p.direction||0;
    var head=Math.floor(e/spd)%(dotCount+tail);
    var pos=(dir===2||dir===3)?(dotCount-1-head%dotCount):(head%dotCount);
    var dist=Math.abs(di-pos);
    if(head>=dotCount)return[0,0,0];
    if(dist===0)return[p.r||255,p.g||255,p.b||255];
    if(dist<=tail){var f=1-dist/tail;return[Math.round((p.r||255)*f),Math.round((p.g||255)*f),Math.round((p.b||255)*f)];}
    return[0,0,0];
  }
  if(t===10){// WIPE
    var spd=p.speedMs||30;if(spd<1)spd=1;
    var dir=p.direction||0;
    var filled=Math.floor(e/spd)%(dotCount*2);
    var filling=filled<dotCount;
    var cnt=filling?filled:(dotCount*2-filled);
    var idx=(dir===2||dir===3)?(dotCount-1-di):di;
    return(idx<cnt)?(filling?[p.r||255,p.g||128,p.b||0]:[0,0,0]):(filling?[0,0,0]:[p.r||255,p.g||128,p.b||0]);
  }
  if(t===11){// SCANNER
    var spd=p.speedMs||30;if(spd<1)spd=1;
    var bar=p.barWidth||3;if(bar<1)bar=3;
    var travel=Math.max(dotCount-bar,1);
    var cyc=travel*2;
    var pos=Math.floor(e/spd)%cyc;if(pos>=travel)pos=cyc-pos;
    if(di>=pos&&di<pos+bar)return[p.r||255,p.g||0,p.b||0];
    return[0,0,0];
  }
  if(t===2){// FADE (ping-pong)
    var spd=p.speedMs||1000;if(spd<1)spd=1;
    var cyc=spd*2;var tt=e%cyc;
    var frac=tt<spd?(tt/spd):((cyc-tt)/spd);
    return[Math.round((p.r||0)*(1-frac)+(p.r2||0)*frac),
           Math.round((p.g||0)*(1-frac)+(p.g2||0)*frac),
           Math.round((p.b||0)*(1-frac)+(p.b2||0)*frac)];
  }
  if(t===3){// BREATHE
    var per=p.periodMs||3000;if(per<1)per=3000;
    var minB=(p.minBri||0)/100;
    var phase=(e%per)/per*2*Math.PI;
    var bri=minB+(1-minB)*(0.5+0.5*Math.sin(phase));
    return[Math.round((p.r||200)*bri),Math.round((p.g||100)*bri),Math.round((p.b||255)*bri)];
  }
  if(t===9){// STROBE
    var per=p.periodMs||100;var duty=p.dutyPct||50;
    return(e%per<per*duty/100)?[p.r||255,p.g||255,p.b||255]:[0,0,0];
  }
  if(t===6){// FIRE (deterministic pseudo-random from position)
    var heat=Math.max(0,Math.min(255,128+Math.round(80*Math.sin(di*0.7+e*0.003))+Math.round(40*Math.sin(di*1.3+e*0.007))));
    if(heat<85)return[heat*3,0,0];
    if(heat<170)return[255,(heat-85)*3,0];
    return[255,255,Math.min(255,(heat-170)*3)];
  }
  if(t===8){// TWINKLE
    var seed=(di*2654435761+Math.floor(e/80))>>>0;
    var bri=((seed>>8)&0xFF);
    if(bri>180)return[Math.round((p.r||200)*bri/255),Math.round((p.g||200)*bri/255),Math.round((p.b||255)*bri/255)];
    return[0,0,0];
  }
  if(t===12){// SPARKLE
    var seed=(di*2654435761+Math.floor(e/50))>>>0;
    if(((seed>>16)&0xFF)>230)return[255,255,255];
    return[p.r||180,p.g||180,p.b||220];
  }
  if(t===13){// GRADIENT
    var frac=dotCount>1?di/(dotCount-1):0;
    return[Math.round((p.r||0)*(1-frac)+(p.r2||0)*frac),
           Math.round((p.g||0)*(1-frac)+(p.g2||0)*frac),
           Math.round((p.b||0)*(1-frac)+(p.b2||0)*frac)];
  }
  return null;
}

// Node (parity harness) — browsers ignore this.
if (typeof module !== 'undefined' && module.exports) {
  module.exports = { EFFECT_SPEC_VERSION: EFFECT_SPEC_VERSION,
                     _hsvToRgb: _hsvToRgb, _palColor: _palColor, _emuPixel: _emuPixel };
}
