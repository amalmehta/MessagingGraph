/* Redeem a code for this phone's own token. The server sets it as a cookie, so
   nothing secret is ever written down here. */
const f = document.getElementById('f');
const code = document.getElementById('code');
const say = document.getElementById('say');
const go = document.getElementById('go');

const tell = (text, kind) => { say.textContent = text; say.className = 'say ' + (kind || ''); };

f.onsubmit = async (e) => {
  e.preventDefault();
  const digits = code.value.replace(/\D/g, '');
  if (digits.length !== 6) return tell('Six digits, from the Mac.', 'bad');
  go.disabled = true;
  tell('Pairing…');
  let res;
  try {
    res = await fetch('/api/pair/redeem', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ code: digits, name: 'iPhone' }),
    });
  } catch {
    go.disabled = false;
    return tell("Couldn't reach the Mac. Same Wi-Fi?", 'bad');
  }
  const body = await res.json().catch(() => ({}));
  if (body.ok) {
    tell('Paired. Opening…', 'good');
    location.href = '/';
    return;
  }
  go.disabled = false;
  code.value = '';
  // The server says nothing about *why*, so neither does this: a wrong code and
  // an expired one look the same from here, which is the point.
  tell(body.offering === false
    ? 'That code is no longer good. Show a new one on the Mac.'
    : "That didn't work. Check the digits on the Mac.", 'bad');
};

code.oninput = () => { code.value = code.value.replace(/\D/g, ''); tell(''); };
