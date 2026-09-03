/* contrast-audit — the check the ship gate structurally cannot make.
 *
 * estate_check reads HTML. Contrast is not in the HTML: it is the colour a
 * browser resolves after inheritance, alpha compositing, gradient stops and
 * specificity. So the gate passed pages carrying text at 1.00:1 — present in
 * the accessibility tree, invisible on screen — and passed one hub with 306
 * failing pairs across 41 pages.
 *
 * Run this IN a browser against the live page:
 *   navigate to the URL, then execute this file's contents.
 * It returns one row per visible text node, worst-first:
 *   {sel, text, fg, fgEff, bg, ratio, px, weight, large, pass, passAA}
 * fgEff is the foreground after compositing a translucent colour over its
 * ground, which is where alpha-set white footer text hides at 3.22:1.
 *
 * Two things it gets right that a naive walker does not, both of which found
 * real failures in this estate:
 *   - it takes the WORST gradient stop as the background, not the average, so
 *     a hero that is legible at one end and not the other is reported;
 *   - it resolves the background by walking ancestors, so a colour is never
 *     scored against white when it actually sits on a tint. #767676 — the
 *     value usually quoted as "AA on white" — measured 4.09:1 that way.
 *
 * What it CANNOT see, and you must handle yourself: anything behind
 * display:none. Steppers and tabbed panels hide every panel but the first,
 * and that is exactly where status badges and disposition stamps live.
 * Forcing panels open took one build from 75 to 220 nodes and only then
 * surfaced a "hold" stamp at 3.91:1. Drive the UI first, then audit.
 */

(function(){
  function parse(c){
    if(!c) return null;
    var m=c.match(/rgba?\(([^)]+)\)/); if(!m) return null;
    var p=m[1].split(/[,\s\/]+/).filter(function(x){return x.length}).map(Number);
    return {r:p[0],g:p[1],b:p[2],a:p.length>3?p[3]:1};
  }
  function hexstops(bi){
    // pull rgb()/rgba()/#hex colour stops out of a background-image gradient
    var out=[]; if(!bi||bi==='none') return out;
    var re=/rgba?\([^)]*\)|#[0-9a-fA-F]{3,8}/g, m;
    while((m=re.exec(bi))){
      var s=m[0], c;
      if(s[0]==='#'){
        var h=s.slice(1), al=1;
        if(h.length===3||h.length===4){ if(h.length===4) al=parseInt(h[3]+h[3],16)/255; h=h[0]+h[0]+h[1]+h[1]+h[2]+h[2]; }
        else if(h.length===8){ al=parseInt(h.slice(6,8),16)/255; }
        c={r:parseInt(h.slice(0,2),16),g:parseInt(h.slice(2,4),16),b:parseInt(h.slice(4,6),16),a:al};
      } else c=parse(s);
      if(c&&c.a>0.05) out.push(c);
    }
    return out;
  }
  function over(fg,bg){ // composite fg (with alpha) over opaque bg
    var a=fg.a;
    return {r:fg.r*a+bg.r*(1-a),g:fg.g*a+bg.g*(1-a),b:fg.b*a+bg.b*(1-a),a:1};
  }
  function lum(c){
    function f(v){v/=255;return v<=0.03928?v/12.92:Math.pow((v+0.055)/1.055,2.4);}
    return 0.2126*f(c.r)+0.7152*f(c.g)+0.0722*f(c.b);
  }
  function ratio(a,b){var l1=lum(a),l2=lum(b);if(l1<l2){var t=l1;l1=l2;l2=t;}return (l1+0.05)/(l2+0.05);}
  function hex(c){function h(v){v=Math.round(v);return (v<16?'0':'')+v.toString(16);}return '#'+h(c.r)+h(c.g)+h(c.b);}

  // resolve backgrounds: returns list of candidate opaque backgrounds behind el
  function backgrounds(el){
    var stack=[];
    var n=el;
    while(n && n.nodeType===1){
      var s=getComputedStyle(n);
      var bc=parse(s.backgroundColor);
      var stops=hexstops(s.backgroundImage);
      var opaqueStops = stops.length>0;
      for(var q=0;q<stops.length;q++){ if(stops[q].a<0.999) opaqueStops=false; }
      // a gradient whose stops are all opaque fully covers what is behind it;
      // 'transparent' stops (alpha 0) are dropped by hexstops, so a gradient that
      // fades out is detected by re-scanning the raw value for the keyword.
      if(opaqueStops && /transparent|rgba\([^)]*,\s*0(\.0+)?\s*\)/.test(s.backgroundImage)) opaqueStops=false;
      if(stops.length) stack.push({layers:stops,node:n});
      if(bc && bc.a>0) stack.push({layers:[bc],node:n});
      if(opaqueStops) break;
      if(bc && bc.a>=0.999 && !stops.length) break;
      n=n.parentElement;
    }
    stack.push({layers:[{r:255,g:255,b:255,a:1}],node:null}); // canvas
    // composite from bottom up
    var base={r:255,g:255,b:255,a:1};
    var results=[base];
    for(var i=stack.length-1;i>=0;i--){
      var next=[];
      for(var j=0;j<results.length;j++){
        for(var k=0;k<stack[i].layers.length;k++){
          next.push(over(stack[i].layers[k],results[j]));
        }
      }
      results=next;
      if(results.length>24) results=results.slice(0,24);
    }
    return results;
  }

  function visible(el){
    var s=getComputedStyle(el);
    if(s.display==='none'||s.visibility==='hidden'||parseFloat(s.opacity)===0) return false;
    var r=el.getBoundingClientRect();
    if(r.width<1||r.height<1) return false;
    // sr-only style clipping is intentional; skip those
    if(s.clip==='rect(0px, 0px, 0px, 0px)'||s.clipPath==='inset(50%)') return false;
    if(r.width<=2&&r.height<=2) return false;
    return true;
  }

  var out=[];
  var all=document.querySelectorAll('*');
  for(var i=0;i<all.length;i++){
    var el=all[i];
    var tag=el.tagName;
    if(tag==='SCRIPT'||tag==='STYLE'||tag==='HEAD'||tag==='META'||tag==='LINK'||tag==='TITLE'||tag==='NOSCRIPT'||tag==='SVG'||tag==='PATH') continue;
    var text='';
    for(var j=0;j<el.childNodes.length;j++){
      var cn=el.childNodes[j];
      if(cn.nodeType===3) text+=cn.nodeValue;
    }
    text=text.replace(/\s+/g,' ').trim();
    if(!text) continue;
    if(!visible(el)) continue;
    var s=getComputedStyle(el);
    var fg=parse(s.color); if(!fg) continue;
    var bgs=backgrounds(el);
    var worst=null, worstbg=null, worstfg=null;
    for(var k=0;k<bgs.length;k++){
      var f=fg.a<1?over(fg,bgs[k]):fg;
      var r=ratio(f,bgs[k]);
      if(worst===null||r<worst){worst=r;worstbg=bgs[k];worstfg=f;}
    }
    var fpx=parseFloat(s.fontSize);
    var w=parseInt(s.fontWeight,10)||400;
    var large=(fpx>=24)||(fpx>=18.66&&w>=700);
    out.push({sel:tag+(el.id?'#'+el.id:'')+(el.className&&typeof el.className==='string'?'.'+el.className.trim().split(/\s+/).join('.'):''),
      text:text.slice(0,60), fg:hex(fg), fgEff:hex(worstfg), bg:hex(worstbg), ratio:Math.round(worst*100)/100,
      px:Math.round(fpx*10)/10, weight:w, large:large,
      pass: worst >= (large?3:4.5), passAA: worst>=4.5});
  }
  out.sort(function(a,b){return a.ratio-b.ratio;});
  var pre=document.createElement('pre');
  pre.id='__audit__';
  pre.textContent=JSON.stringify(out);
  document.body.appendChild(pre);
  return out.length;
})();
