const form = document.getElementById('key-request-form');
form.addEventListener('submit', async event => {
  event.preventDefault();
  const button = form.querySelector('button');
  const status = document.getElementById('request-status');
  button.disabled = true; status.textContent = 'Submitting your request…';
  try {
    const body = Object.fromEntries(new FormData(form));
    const response = await fetch('/api/key-requests', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
    const result = await response.json();
    if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : 'Please check the form and try again.');
    status.textContent = result.message; form.reset();
  } catch (error) { status.textContent = error.message; }
  finally { button.disabled = false; }
});
