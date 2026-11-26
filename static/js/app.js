(function(){
const $=(s,r=document)=>r.querySelector(s),$$=(s,r=document)=>[...r.querySelectorAll(s)];
// loader
window.addEventListener('load',()=>setTimeout(()=>{const l=$('#loader');l&&l.classList.add('hide')},250));
// theme toggle
const tb=$('#themeBtn');
function setTheme(t){document.documentElement.setAttribute('data-bs-theme',t);try{localStorage.setItem('evs-theme',t)}catch(e){}if(tb)tb.textContent=t==='dark'?'☀️':'🌙'}
setTheme(document.documentElement.getAttribute('data-bs-theme')||'light');
tb&&tb.addEventListener('click',()=>setTheme(document.documentElement.getAttribute('data-bs-theme')==='dark'?'light':'dark'));
// scroll progress + cursor glow
const pg=$('#progress'),gl=$('#glow');
addEventListener('scroll',()=>{const h=document.documentElement;pg.style.width=(h.scrollTop/(h.scrollHeight-h.clientHeight||1)*100)+'%'});
addEventListener('mousemove',e=>{gl.style.left=e.clientX+'px';gl.style.top=e.clientY+'px'});
// reveal
const io=new IntersectionObserver(es=>es.forEach(e=>{if(e.isIntersecting){e.target.classList.add('show');io.unobserve(e.target)}}),{threshold:.1});
$$('.reveal').forEach((el,i)=>{el.style.transitionDelay=Math.min(i%6,5)*70+'ms';io.observe(el)});
// counters (.stat text or [data-count])
function count(el,end,pre){const t0=performance.now(),dur=1300;(function tick(n){const p=Math.min((n-t0)/dur,1),v=Math.round(end*(1-Math.pow(1-p,3)));el.textContent=pre+v.toLocaleString('en-IN');if(p<1)requestAnimationFrame(tick)})(t0)}
$$('.stat').forEach(el=>{const m=el.textContent.trim().match(/^(₹?)(\d+)$/);if(m)count(el,+m[2],m[1])});
const co=new IntersectionObserver(es=>es.forEach(e=>{if(e.isIntersecting){count(e.target,+e.target.dataset.count,'');co.unobserve(e.target)}}));
$$('[data-count]').forEach(el=>co.observe(el));
// tilt
$$('.tilt').forEach(c=>{c.addEventListener('mousemove',e=>{const r=c.getBoundingClientRect(),x=(e.clientX-r.left)/r.width-.5,y=(e.clientY-r.top)/r.height-.5;c.style.transform=`perspective(800px) rotateY(${x*9}deg) rotateX(${-y*9}deg) translateY(-6px)`});c.addEventListener('mouseleave',()=>c.style.transform='')});
// ripple
document.addEventListener('click',e=>{const b=e.target.closest('.btn');if(!b)return;const r=b.getBoundingClientRect(),s=document.createElement('span'),d=Math.max(r.width,r.height);s.className='ripple';s.style.cssText=`width:${d}px;height:${d}px;left:${e.clientX-r.left-d/2}px;top:${e.clientY-r.top-d/2}px`;b.appendChild(s);setTimeout(()=>s.remove(),600)});
// typewriter
const ty=$('#typing');if(ty){const words=JSON.parse(ty.dataset.words);let w=0,c=0,del=false;(function loop(){const word=words[w];ty.textContent=word.slice(0,c);if(!del&&c===word.length){del=true;return setTimeout(loop,1400)}if(del&&c===0){del=false;w=(w+1)%words.length}c+=del?-1:1;setTimeout(loop,del?40:80)})()}
// particles in hero
const cv=$('#particles');if(cv){const x=cv.getContext('2d');let W,H,P=[];function rs(){W=cv.width=cv.offsetWidth;H=cv.height=cv.offsetHeight}rs();addEventListener('resize',rs);
 for(let i=0;i<55;i++)P.push({x:Math.random()*W,y:Math.random()*H,vx:(Math.random()-.5)*.6,vy:(Math.random()-.5)*.6});
 (function draw(){x.clearRect(0,0,W,H);P.forEach((p,i)=>{p.x+=p.vx;p.y+=p.vy;if(p.x<0||p.x>W)p.vx*=-1;if(p.y<0||p.y>H)p.vy*=-1;x.fillStyle='rgba(255,255,255,.8)';x.beginPath();x.arc(p.x,p.y,2,0,7);x.fill();
  for(let j=i+1;j<P.length;j++){const q=P[j],d=Math.hypot(p.x-q.x,p.y-q.y);if(d<110){x.strokeStyle=`rgba(255,255,255,${.25*(1-d/110)})`;x.beginPath();x.moveTo(p.x,p.y);x.lineTo(q.x,q.y);x.stroke()}}});requestAnimationFrame(draw)})()}
// countdown
$$('[data-countdown]').forEach(el=>{const end=new Date(el.dataset.countdown).getTime();(function t(){let d=end-Date.now();if(d<0){el.innerHTML='<b>🎉 Event started!</b>';return}
 const D=Math.floor(d/864e5),H=Math.floor(d%864e5/36e5),M=Math.floor(d%36e5/6e4),S=Math.floor(d%6e4/1e3);
 el.innerHTML=[[D,'days'],[H,'hrs'],[M,'min'],[S,'sec']].map(([v,l])=>`<div>${String(v).padStart(2,'0')}<small>${l}</small></div>`).join('');setTimeout(t,1000)})()});
// toast + beep
window.toast=function(msg,danger,url){const t=document.createElement('div');t.className='toast-x'+(danger?' danger':'');t.textContent=msg;if(url)t.onclick=()=>location.href=url;$('#toasts').appendChild(t);setTimeout(()=>t.remove(),9000)};
window.beep=function(){try{const a=new (window.AudioContext||window.webkitAudioContext)();[880,660,880,660].forEach((f,i)=>{const o=a.createOscillator(),g=a.createGain();o.frequency.value=f;o.type='square';g.gain.value=.08;o.connect(g);g.connect(a.destination);o.start(a.currentTime+i*.22);o.stop(a.currentTime+i*.22+.18)});navigator.vibrate&&navigator.vibrate([300,100,300])}catch(e){}};
// SOS live polling for organiser/admin (badge + toast + siren)
const feed=document.body.dataset.feed;
if(feed){let seen=+(sessionStorage.getItem('sosSeen')||0);
 async function poll(){try{const r=await fetch(feed,{credentials:'same-origin'});if(!r.ok)return;const j=await r.json();
  $$('.sos-badge').forEach(b=>{b.textContent=j.alerts.length;b.style.display=j.alerts.length?'inline':'none'});
  const open=j.alerts.filter(a=>a.status==='OPEN'),mx=Math.max(0,...open.map(a=>a.id));
  if(mx>seen){const first=seen===0&&!sessionStorage.getItem('sosInit');open.filter(a=>a.id>seen).forEach(a=>toast(`🚨 SOS: ${a.category_label} - ${a.attendee} @ ${a.event}`,true,document.body.dataset.control));
   if(!first||open.length)beep();seen=mx;sessionStorage.setItem('sosSeen',seen)}sessionStorage.setItem('sosInit','1')}catch(e){}}
 poll();setInterval(poll,5000)}
// star-rating hint text
document.addEventListener('change',e=>{if(!e.target.matches('.stars input'))return;const h=e.target.closest('.mb-2').querySelector('[data-hint]');if(h)h.textContent=['','Poor 😕','Fair 😐','Good 🙂','Very good 😄','Excellent! 🤩'][+e.target.value]||''});
})();

/* ===== EventSphere v3.1: eye icon on every password box + numbers-only phone/OTP ===== */
(function () {
  var EYE = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
            '<path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8S1 12 1 12z"/><circle cx="12" cy="12" r="3"/><line class="slash" x1="3" y1="3" x2="21" y2="21"/></svg>';

  function wirePassword(inp) {
    if (inp.dataset.eye) return;
    inp.dataset.eye = '1';
    var wrap = document.createElement('div');
    wrap.className = 'pw-wrap';
    inp.parentNode.insertBefore(wrap, inp);
    wrap.appendChild(inp);
    var b = document.createElement('button');
    b.type = 'button';
    b.className = 'pw-eye';
    b.title = 'Show / hide password';
    b.setAttribute('aria-label', 'Show password');
    b.innerHTML = EYE;
    b.addEventListener('click', function () {
      var show = inp.type === 'password';
      inp.type = show ? 'text' : 'password';
      b.classList.toggle('on', show);
      b.setAttribute('aria-label', show ? 'Hide password' : 'Show password');
      inp.focus();
    });
    wrap.appendChild(b);
  }
  document.querySelectorAll('input[type=password]').forEach(wirePassword);

  // phone: digits only, max 10  |  otp: digits only, max 6
  function limit(sel, max) {
    document.querySelectorAll(sel).forEach(function (i) {
      i.maxLength = max; i.inputMode = 'numeric'; i.setAttribute('pattern', '[0-9]{' + max + '}');
    });
  }
  limit('input[name=phone]', 10);
  limit('input[name=otp]', 6);
  document.addEventListener('input', function (e) {
    var t = e.target, max = t.matches('input[name=phone]') ? 10 : t.matches('input[name=otp]') ? 6 : 0;
    if (!max) return;
    var v = t.value.replace(/\D/g, '').slice(0, max);
    if (v !== t.value) t.value = v;
  });
  document.addEventListener('keypress', function (e) {     // block letters/symbols while typing
    if (e.target.matches('input[name=phone], input[name=otp]') && e.key.length === 1 && !/\d/.test(e.key)) e.preventDefault();
  });
})();
