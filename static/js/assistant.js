/* Booking-help chatbot widget (attendees). Talks to /api/assistant/. */
(function () {
  const panel = document.getElementById('botPanel'); if (!panel) return;
  const fab = document.getElementById('botFab'), msgs = document.getElementById('botMsgs'), chips = document.getElementById('botChips'),
    form = document.getElementById('botForm'), input = document.getElementById('botInput');
  const KEY = 'evs-bot-history', url = panel.dataset.url, eventId = panel.dataset.event || '';
  const token = () => { const t = document.querySelector('[name=csrfmiddlewaretoken]'); return t ? t.value : '' };
  const DEFAULT_CHIPS = ['How do I book a ticket?', 'How does seat selection work?', 'Show my bookings', 'Find events', 'Payment help', 'Feedback & rating'];
  let history = []; try { history = JSON.parse(sessionStorage.getItem(KEY) || '[]') } catch (e) { }
  function save() { try { sessionStorage.setItem(KEY, JSON.stringify(history.slice(-30))) } catch (e) { } }
  function rich(el, text) { // **bold** support, everything else stays plain text (XSS-safe)
    text.split(/(\*\*[^*]+\*\*)/g).forEach(p => { if (/^\*\*[^*]+\*\*$/.test(p)) { const b = document.createElement('b'); b.textContent = p.slice(2, -2); el.appendChild(b) } else el.appendChild(document.createTextNode(p)) })
  }
  function bubble(who, text, links) {
    const d = document.createElement('div'); d.className = 'bot-m ' + who; rich(d, text);
    if (links && links.length) { const w = document.createElement('div'); w.className = 'bot-links'; links.forEach(l => { const a = document.createElement('a'); a.href = l.url; a.textContent = l.label; w.appendChild(a) }); d.appendChild(w) }
    msgs.appendChild(d); msgs.scrollTop = msgs.scrollHeight; return d
  }
  function setChips(list) { chips.innerHTML = ''; (list || DEFAULT_CHIPS).slice(0, 4).forEach(c => { const b = document.createElement('button'); b.type = 'button'; b.textContent = c; b.onclick = () => ask(c); chips.appendChild(b) }) }
  function welcome() { bubble('b', 'Hi! 👋 I\'m your booking assistant. Ask me how to book, choose seats, pay, or find your tickets.'); setChips() }
  async function ask(text) {
    text = (text || '').trim(); if (!text) return;
    bubble('u', text); history.push({ w: 'u', t: text }); input.value = ''; chips.innerHTML = '';
    const typing = document.createElement('div'); typing.className = 'bot-typing'; typing.textContent = 'Assistant is typing…'; msgs.appendChild(typing); msgs.scrollTop = msgs.scrollHeight;
    try {
      const r = await fetch(url, { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json', 'X-CSRFToken': token() }, body: JSON.stringify({ message: text, event_id: eventId }) });
      const j = await r.json(); typing.remove();
      if (!r.ok) throw new Error('bad');
      bubble('b', j.answer, j.links); history.push({ w: 'b', t: j.answer, l: j.links }); setChips(j.chips)
    }
    catch (e) { typing.remove(); bubble('b', 'Sorry, I could not answer that right now. Please try again.'); setChips() }
    save()
  }
  function open_() { panel.classList.remove('d-none'); fab.classList.add('d-none'); input.focus() }
  function close_() { panel.classList.add('d-none'); fab.classList.remove('d-none') }
  fab.onclick = open_; document.getElementById('botClose').onclick = close_;
  form.addEventListener('submit', e => { e.preventDefault(); ask(input.value) });
  if (history.length) { history.forEach(h => bubble(h.w, h.t, h.l)); setChips() } else welcome();
})();

/* ===== Show reminder: gentle message 1 hour before a booked show (attendee only) ===== */
(function () {
  if (!document.getElementById('botPanel')) return;
  var box = document.getElementById('toasts'); if (!box) return;
  var KEY = 'evs-reminder-closed', closed = {};
  try { closed = JSON.parse(localStorage.getItem(KEY) || '{}') } catch (e) { }
  function show(r) {
    var id = 'rem-' + r.id;
    if (closed[r.id] || document.getElementById(id)) return;
    var d = document.createElement('div'); d.id = id; d.className = 'toast-x reminder';
    var t = document.createElement('b'); t.textContent = '\u23F0 Gentle reminder';
    var p = document.createElement('div');
    var when = r.minutes >= 60 ? 'in about 1 hour' : 'in ' + r.minutes + ' minute' + (r.minutes === 1 ? '' : 's');
    p.textContent = 'Your show "' + r.title + '" starts ' + when + ' at ' + r.venue + '. Carry your QR e-ticket. See you there!';
    var x = document.createElement('button'); x.type = 'button'; x.className = 'rem-x'; x.setAttribute('aria-label', 'Close'); x.textContent = '\u2715';
    x.onclick = function () { closed[r.id] = 1; try { localStorage.setItem(KEY, JSON.stringify(closed)) } catch (e) { } d.remove() };
    d.appendChild(t); d.appendChild(p); d.appendChild(x); box.appendChild(d);
  }
  function check() {
    fetch('/api/reminders/', { credentials: 'same-origin' }).then(function (r) { return r.ok ? r.json() : null }).then(function (j) {
      if (j && j.reminders) j.reminders.forEach(show);
    }).catch(function () { });
  }
  check(); setInterval(check, 60000);
})();